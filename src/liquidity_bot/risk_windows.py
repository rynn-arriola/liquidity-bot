"""Restart-persistent ET trading windows, independent of daily reporting.

The owner supplies matched-inventory realised P&L reconstructed from durable
fills. Open orders, open-position basis, paper marks and cash movements never
change the window budget. This module does no network IO or order mutation.
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
    stop_loss_usd: Decimal = Decimal("30")
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
        legacy_active = self.connection.execute(
            "SELECT 1 FROM execution_risk_windows WHERE "
            "(tripped_at IS NOT NULL AND released_at IS NULL) "
            "OR provisional_markets IS NOT NULL LIMIT 1").fetchone()
        if legacy_active is not None:
            raise ValueError(
                "legacy cash-based risk hold requires explicit reconciliation")
        self.held = bool(self._holds())
        self.provisional = False

    def _holds(self):
        return self.connection.execute(
            "SELECT window_start,window_end,tripped_at "
            "FROM execution_settled_risk_windows "
            "WHERE tripped_at IS NOT NULL AND released_at IS NULL").fetchall()

    def checkpoint(self):
        return self.connection.execute(
            "SELECT observed_at,settled_pnl "
            "FROM execution_settled_risk_windows "
            "ORDER BY observed_at DESC LIMIT 1").fetchone()

    def ready(self, now: datetime) -> bool:
        return (self.current is not None and not self.held and not self.provisional
                and self.current.start <= _aware(now) < self.current.end)

    def observe(self, *, settled_pnl: Decimal, observed_at: datetime,
                now: datetime, full_cap: int) -> WindowMark:
        now, observed_at = _aware(now), _aware(observed_at)
        start, end = self.policy.bounds(now)
        pnl = _amount(settled_pnl)
        if not start <= observed_at <= now < end:
            raise ValueError("account snapshot crossed a risk-window boundary")
        if self.current is not None and start < self.current.start:
            raise ValueError("risk-window clock moved backwards")
        key = start.isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO execution_settled_risk_windows "
                "(window_start,window_end,settled_pnl,observed_at,policy,"
                "entry_cap) VALUES (?,?,?,?,?,?)",
                (key, end.isoformat(), str(pnl), observed_at.isoformat(),
                 self.policy.fingerprint(),
                 min(full_cap, self.policy.max_pending_entry_orders)))
            row = self.connection.execute(
                "SELECT entry_cap,tripped_at,policy "
                "FROM execution_settled_risk_windows WHERE window_start=?",
                (key,)).fetchone()
            if row[2] != self.policy.fingerprint():
                raise ValueError("risk-window policy changed during window")
            loss = max(Decimal("0"), -pnl)
            cap = min(int(row[0]), self.policy.cap(loss, full_cap=full_cap))
            trip = row[1]
            if loss >= self.policy.stop_loss_usd and trip is None:
                trip = now.isoformat()
            self.connection.execute(
                "UPDATE execution_settled_risk_windows SET settled_pnl=?,"
                "entry_cap=?,tripped_at=?,observed_at=? WHERE window_start=?",
                (str(pnl), cap, trip, observed_at.isoformat(), key))
        self.current = WindowMark(start, end, Decimal("0"), cap,
                                  datetime.fromisoformat(trip) if trip else None)
        self.held = bool(self._holds())
        self.provisional = False
        return self.current

    @property
    def realized_equity(self) -> Decimal | None:
        row = self.checkpoint()
        return _amount(row[1]) if row else None

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
                    "UPDATE execution_settled_risk_windows SET released_at=? "
                    "WHERE window_start=? AND released_at IS NULL",
                    (now.isoformat(), key))
        self.held = bool(self._holds())
        return bool(expired)
