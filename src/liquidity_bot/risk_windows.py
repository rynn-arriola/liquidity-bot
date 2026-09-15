"""Restart-persistent ET trading windows, independent of daily reporting.

The owner supplies cash plus open-position cost, less proven non-trading cash.
Only closed losses affect the durable cap and stop; open marks may pause entry
provisionally until the breach-time positions close.
This module does no network IO and has no order authority.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_RISK_WINDOW_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "risk_window_policy.json")


def _amount(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("risk amount must be finite")
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("risk amount must be finite") from exc
    if not result.is_finite():
        raise ValueError("risk amount must be finite")
    return result


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("risk window clock must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RiskWindowPolicy:
    stop_loss_usd: Decimal = Decimal("25")
    max_pending_entry_orders: int = 30
    remaining_budget_tiers: tuple[tuple[Decimal, int], ...] = (
        (Decimal("0.8"), 25), (Decimal("0.6"), 20),
        (Decimal("0.4"), 15), (Decimal("0.2"), 10))

    def __post_init__(self) -> None:
        object.__setattr__(self, "stop_loss_usd", _amount(self.stop_loss_usd))
        object.__setattr__(self, "remaining_budget_tiers", tuple(
            (_amount(fraction), cap) for fraction, cap in self.remaining_budget_tiers))
        if _amount(self.stop_loss_usd) <= 0:
            raise ValueError("window stop loss must be positive")
        previous_fraction = Decimal("1")
        previous_cap = self.max_pending_entry_orders
        if (type(previous_cap) is not int or not 1 <= previous_cap <= 64
                or not self.remaining_budget_tiers):
            raise ValueError("window pending-order cap/tiers are invalid")
        for remaining, cap in self.remaining_budget_tiers:
            remaining = _amount(remaining)
            if (not 0 < remaining < previous_fraction
                    or type(cap) is not int or not 0 <= cap <= previous_cap):
                raise ValueError("remaining-budget tiers must decrease")
            previous_fraction, previous_cap = remaining, cap

    def bounds(self, now: datetime) -> tuple[datetime, datetime]:
        local = _aware(now).astimezone(ZoneInfo("America/New_York"))
        start = datetime.combine(
            local.date(), time(local.hour // 6 * 6), tzinfo=local.tzinfo)
        # Local civil boundaries, including 5/7 elapsed hours at DST changes.
        end = start + timedelta(hours=6)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    def cap(self, loss: Decimal, *, full_cap: int) -> int:
        loss = max(Decimal("0"), _amount(loss))
        if loss >= self.stop_loss_usd:
            return 0
        cap = min(full_cap, self.max_pending_entry_orders)
        for remaining, tier_cap in self.remaining_budget_tiers:
            if loss >= self.stop_loss_usd * (1 - remaining):
                cap = min(cap, tier_cap)
        return cap

    def fingerprint(self) -> str:
        return json.dumps([str(self.stop_loss_usd), self.max_pending_entry_orders,
                           [[str(f), c] for f, c in self.remaining_budget_tiers]])


def load_risk_window_policy(
    path: Path = DEFAULT_RISK_WINDOW_POLICY_PATH,
) -> RiskWindowPolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(raw, dict) or set(raw) != {
                "timezone", "window_start_hours", "stop_loss_usd",
                "max_pending_entry_orders", "remaining_budget_tiers"}
                or raw["timezone"] != "America/New_York"
                or raw["window_start_hours"] != [0, 6, 12, 18]
                or any(type(hour) is not int for hour in raw["window_start_hours"])
                or not isinstance(raw["remaining_budget_tiers"], list)):
            raise ValueError("invalid six-hour risk-window policy")
        tiers = []
        for row in raw["remaining_budget_tiers"]:
            if not isinstance(row, dict) or set(row) != {
                    "remaining_fraction", "max_pending_entry_orders"}:
                raise ValueError("invalid remaining-budget tier")
            tiers.append((_amount(row["remaining_fraction"]),
                          row["max_pending_entry_orders"]))
        return RiskWindowPolicy(_amount(raw["stop_loss_usd"]),
                                raw["max_pending_entry_orders"], tuple(tiers))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError("risk-window policy cannot be read") from exc


@dataclass(frozen=True)
class WindowMark:
    start: datetime
    end: datetime
    opening_equity: Decimal
    cap: int
    tripped_at: datetime | None


class WindowRiskMeter:
    def __init__(self, connection: sqlite3.Connection, policy: RiskWindowPolicy):
        self.connection = connection
        self.policy = policy
        self.current: WindowMark | None = None
        self.held = bool(self._holds())
        self.provisional = bool(self._provisionals())

    def _provisionals(self):
        return self.connection.execute(
            "SELECT window_start,provisional_markets FROM execution_risk_windows "
            "WHERE provisional_markets IS NOT NULL").fetchall()

    def _holds(self):
        return self.connection.execute(
            "SELECT window_start,window_end,tripped_at FROM execution_risk_windows "
            "WHERE tripped_at IS NOT NULL AND released_at IS NULL").fetchall()

    def checkpoint(self):
        return self.connection.execute(
            "SELECT observed_at,raw_cost_equity,realized_equity,marked_equity,"
            "cash_anchor,nontrading_total "
            "FROM execution_risk_window_checkpoint WHERE singleton=1").fetchone()

    @property
    def cash_boundary(self) -> datetime | None:
        row = self.checkpoint()
        return datetime.fromisoformat(row[4]) if row else None

    @property
    def nontrading_total(self) -> Decimal:
        row = self.checkpoint()
        return _amount(row[5]) if row else Decimal("0")

    def ready(self, now: datetime) -> bool:
        return (self.current is not None and not self.held and not self.provisional
                and self.current.start <= _aware(now) < self.current.end)

    def observe(self, *, equity: Decimal, observed_at: datetime,
                now: datetime, full_cap: int, day_baseline: datetime,
                marked_equity: Decimal | None = None,
                positions: frozenset[str] = frozenset(),
                interval_nontrading: Decimal = Decimal("0"),
                cash_observed_at: datetime | None = None,
                nontrading_total: Decimal = Decimal("0")) -> WindowMark:
        now, observed_at = _aware(now), _aware(observed_at)
        start, end = self.policy.bounds(now)
        equity = _amount(equity)
        marked = equity if marked_equity is None else _amount(marked_equity)
        raw_equity = equity
        adjustment = _amount(interval_nontrading)
        nontrading_total = _amount(nontrading_total)
        cash_at = _aware(cash_observed_at or observed_at)
        if not start <= observed_at <= now < end:
            raise ValueError("account snapshot crossed a risk-window boundary")
        if not observed_at <= cash_at <= now:
            raise ValueError("risk-window cash observation clock is invalid")
        if self.current is not None and start < self.current.start:
            raise ValueError("risk-window clock moved backwards")
        checkpoint = self.checkpoint()
        if checkpoint:
            last_at = datetime.fromisoformat(checkpoint[0])
            if cash_at < last_at:
                raise ValueError("risk-window account clock moved backwards")
            # Continuous, transfer-neutral coordinates survive midnight and
            # restart. A close in the observation gap is charged to the new
            # window, never forgiven by its first lower post-boundary balance.
            equity = _amount(checkpoint[2]) + raw_equity - _amount(checkpoint[1]) - adjustment
            marked = equity + marked - raw_equity
        else:
            last_at = None
        opening = (_amount(checkpoint[2]) if last_at is not None and last_at < start
                   else equity)
        opening_marked = (_amount(checkpoint[3]) if last_at is not None and last_at < start
                          else marked)
        day_key = _aware(day_baseline).isoformat()
        key = start.isoformat()
        with self.connection:
            # A policy change must not silently rebase an already-used window.
            self.connection.execute(
                "INSERT OR IGNORE INTO execution_risk_windows "
                "(window_start,window_end,opening_equity,observed_at,day_baseline,"
                "policy,entry_cap,opening_marked_equity) VALUES (?,?,?,?,?,?,?,?)",
                (key, end.isoformat(), str(opening), observed_at.isoformat(),
                 day_key, self.policy.fingerprint(),
                 min(full_cap, self.policy.max_pending_entry_orders), str(opening_marked)))
            row = self.connection.execute(
                "SELECT opening_equity,entry_cap,tripped_at,day_baseline,policy,"
                "opening_marked_equity "
                "FROM execution_risk_windows WHERE window_start=?", (key,)).fetchone()
            if row[3] != day_key or row[4] != self.policy.fingerprint():
                raise ValueError("risk-window baseline or policy changed during window")
            opening = _amount(row[0])
            loss = max(Decimal("0"), opening - equity)
            cap = min(int(row[1]), self.policy.cap(loss, full_cap=full_cap))
            trip = row[2]
            if loss >= self.policy.stop_loss_usd and trip is None:
                trip = now.isoformat()
            self.connection.execute(
                "UPDATE execution_risk_windows SET entry_cap=?,tripped_at=?,observed_at=? "
                "WHERE window_start=?", (cap, trip, observed_at.isoformat(), key))
            # A market's absence here comes from a complete account snapshot,
            # never a missing order row or a local cache. Hold the cohort until
            # every affected position has closed, including across midnight.
            had_current_provisional = False
            for held_key, cohort in self._provisionals():
                if held_key == key:
                    had_current_provisional = True
                names = json.loads(cohort)
                if not isinstance(names, list) or not names:
                    raise ValueError("provisional window cohort is invalid")
                if not set(names) & positions:
                    self.connection.execute(
                        "UPDATE execution_risk_windows SET provisional_markets=NULL "
                        "WHERE window_start=?", (held_key,))
                elif not positions <= set(names):
                    # Already-resting entries can fill while cancellation is
                    # in flight. Their closure must join the pending proof.
                    self.connection.execute(
                        "UPDATE execution_risk_windows SET provisional_markets=? "
                        "WHERE window_start=?",
                        (json.dumps(sorted(set(names) | positions)), held_key))
            if trip is not None:
                self.connection.execute(
                    "UPDATE execution_risk_windows SET provisional_markets=NULL "
                    "WHERE window_start=?", (key,))
            elif (not had_current_provisional and positions
                  and _amount(row[5]) - marked >= self.policy.stop_loss_usd):
                self.connection.execute(
                    "UPDATE execution_risk_windows SET provisional_markets=? "
                    "WHERE window_start=?", (json.dumps(sorted(positions)), key))
            self.connection.execute(
                "INSERT INTO execution_risk_window_checkpoint VALUES (1,?,?,?,?,?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET observed_at=excluded.observed_at,"
                "raw_cost_equity=excluded.raw_cost_equity,"
                "realized_equity=excluded.realized_equity,marked_equity=excluded.marked_equity,"
                "cash_anchor=excluded.cash_anchor,nontrading_total=excluded.nontrading_total",
                (cash_at.isoformat(), str(raw_equity), str(equity), str(marked),
                 day_key, str(nontrading_total)))
        self.current = WindowMark(start, end, opening, cap,
                                  datetime.fromisoformat(trip) if trip else None)
        self.held = bool(self._holds())
        self.provisional = bool(self._provisionals())
        return self.current

    @property
    def realized_equity(self) -> Decimal | None:
        row = self.checkpoint()
        return _amount(row[2]) if row else None

    def release(self, *, now: datetime, flat_proven: bool) -> bool:
        """Release only expired windows after the owner proves fresh flat state."""
        now = _aware(now)
        if not flat_proven or self.current is None or not (
                self.current.start <= now < self.current.end):
            return False
        holds = self._holds()
        expired = [row[0] for row in holds
                   if datetime.fromisoformat(row[1]) <= self.current.start]
        with self.connection:
            for key in expired:
                self.connection.execute(
                    "UPDATE execution_risk_windows SET released_at=? "
                    "WHERE window_start=? AND released_at IS NULL",
                    (now.isoformat(), key))
        self.held = bool(self._holds())
        return bool(expired)
