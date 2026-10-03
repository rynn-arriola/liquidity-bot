#!/usr/bin/env python3
"""One daily table: trading P&L, venue reward, and resting side-hours.

Read-only. The command makes bounded account-wide earnings, cash-activity
and settlement-activity reads, reuses the running bot's fresh account mark
when available (otherwise bounded balance and position reads), and opens the
commissioning ledger in SQLite read-only mode. It never places, cancels, modifies, or reprices an order.

    .venv/bin/python -B scripts/reward_table.py --days 7

Reward dates and table days use America/New_York, matching the venue. Trading
uses account marked-equity changes, excluding proven non-trading cash.
Incomplete matched-pair ledger results are diagnostics, never daily totals.
Resting time merges overlapping orders per (market, side), splits intervals at
New York midnight, and sums market-side hours. It can therefore exceed 24
hours per day when several market sides rest simultaneously.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.daily_pnl import CLEAN_FROM, reward_by_day, trading_by_day  # noqa: E402
from scripts.reward_liquidity import resting_period_hours_by_day  # noqa: E402
from liquidity_bot.account_snapshots import (  # noqa: E402
    AsyncAccountSnapshotGateway, account_equity_snapshot)
from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, OPPORTUNISTIC_REPORT_RATE_PER_SECOND,
    shared_api_limiter)
from liquidity_bot.cash_activity import (  # noqa: E402
    CashActivityUnavailable,
    NontradingCashChange,
    corrected_opening_cash,
    fetch_nontrading_cash,
    fetch_nontrading_cash_changes,
)
from liquidity_bot.errors import AdapterError  # noqa: E402
from liquidity_bot.bot_era import BOT_ERA_START_DATE  # noqa: E402
from liquidity_bot.execution_ledger import ExecutionLedger  # noqa: E402
from liquidity_bot.pnl import MatchedInventory  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402
from liquidity_bot.reward_calendar import NEW_YORK, reward_day  # noqa: E402
from liquidity_bot.resting_time import resting_side_hours_by_day  # noqa: E402
from liquidity_bot.settlement_activity import (  # noqa: E402
    CACHE_FILENAME as SETTLEMENT_CACHE_FILENAME, Resolution,
    refresh_resolutions)


ACCOUNT_ACTIVITY_MAX_PAGES = 4
ACCOUNT_REPORT_RATE_PER_SECOND = OPPORTUNISTIC_REPORT_RATE_PER_SECOND
LIVE_PNL_CACHE_FILENAME = "tv-live-pnl.json"
# Reporting only: use the first saved opening, at most 15 minutes late.
# Never relabel a much later restart as midnight or change the risk baseline.
NEAR_MIDNIGHT_MAX_DELAY = timedelta(minutes=15)
# A daily row whose recorded boundaries are both this close to midnight is
# treated as the exact calendar day and may finalize its net.
CALENDAR_DAY_TOLERANCE = timedelta(seconds=60)
# Payouts for the newest paid days keep arriving over about two cycles and
# are returned as PAID, not PENDING. These days are shown as still posting
# and never finalize their net.
REWARD_POSTING_DAYS = 2
# The running bot's own account mark is reused when at most this old, which
# avoids the report's separate balance/position reads.
LIVE_MARK_MAX_AGE = timedelta(minutes=5)
LIVE_MARK_MAX_FUTURE = timedelta(seconds=30)

# This baseline landed four minutes after midnight because the hard-stop
# shutdown had already proved the account flat at 03:50:49Z and the next
# process did not place its first order until 04:04:28Z. The production ledger
# independently contains no fill in that gap. Pin every field so this narrow
# incident audit can never bless a different delayed row by accident.
AUDITED_DELAYED_ET_BASELINES = {
    "2026-08-27": {
        "opening_equity": Decimal("708.3008"),
        "opening_cash": Decimal("708.3008"),
        "created_at": "2026-08-27T04:04:10.544588+00:00",
        "source": "fresh_et_day",
    },
    # Flat authenticated reads at 03:50Z and 04:05Z straddle midnight;
    # no ledger fill or completed cash activity before this startup mark.
    # See REPORTING_ACCURACY_2026-09-06.md for the retained evidence.
    "2026-09-04": {
        "opening_equity": Decimal("792.7224"),
        "opening_cash": Decimal("792.7224"),
        "created_at": "2026-09-04T04:07:34.628633+00:00",
        "source": "fresh_et_day",
    },
}


@dataclass(frozen=True)
class AccountRealizedToday:
    value: Decimal | None
    reason: str
    marked_value: Decimal | None = None
    open_unrealized: Decimal | None = None
    as_of: datetime | None = None
    opening_equity: Decimal | None = None
    opening_cash: Decimal | None = None
    current_cash: Decimal | None = None
    position_basis: Decimal | None = None
    position_value: Decimal | None = None
    account_equity: Decimal | None = None
    position_count: int | None = None
    nontrading_cash: Decimal | None = None
    opening_as_of: datetime | None = None


class AccountTradingHistory(dict[str, Decimal]):
    """Snapshot-interval values; delayed boundaries are not exact calendar days."""

    def __init__(self) -> None:
        super().__init__()
        self.intervals: dict[str, tuple[datetime, datetime]] = {}


class AccountMetricUnavailable(ValueError):
    """The current-day account number cannot be proved from bounded reads."""


def _report_boundary_time(row: Any, day_value: date,
                          boundary: datetime) -> datetime:
    """Accept an audited midnight or a bounded, explicitly dated estimate."""

    created_at = _timestamp(row["created_at"], "opening baseline")
    audited = AUDITED_DELAYED_ET_BASELINES.get(day_value.isoformat())
    if audited is not None:
        if any(str(row[field]) != str(audited[field]) for field in (
                "opening_equity", "opening_cash", "created_at", "source")):
            raise AccountMetricUnavailable("audited ET boundary fields changed")
        return boundary
    if not timedelta(0) <= created_at - boundary <= NEAR_MIDNIGHT_MAX_DELAY:
        raise AccountMetricUnavailable(
            "ET opening snapshot is outside the 15-minute reporting window")
    return created_at


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountMetricUnavailable(f"{field} is invalid") from exc
    if not result.is_finite():
        raise AccountMetricUnavailable(f"{field} is not finite")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AccountMetricUnavailable(f"{field} timestamp is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AccountMetricUnavailable(f"{field} timestamp is timezone-naive")
    return result.astimezone(timezone.utc)


def _opening_snapshot_for_day(
    db: str, day_value: date, boundary: datetime,
) -> tuple[Decimal, Decimal, datetime]:
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT opening_equity,opening_cash,created_at,source "
                "FROM execution_risk_day_baselines WHERE risk_date=?",
                (day_value.isoformat(),)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise AccountMetricUnavailable(
            "ET opening-cash baseline is unreadable") from exc
    if row is None or row["opening_cash"] is None:
        raise AccountMetricUnavailable("ET opening-cash baseline is missing")
    stored = _decimal(row["opening_cash"], "opening cash")
    stored_equity = _decimal(row["opening_equity"], "opening equity")
    if stored < 0 or stored_equity < 0:
        raise AccountMetricUnavailable("ET opening snapshot is negative")
    source = str(row["source"] or "")
    try:
        corrected = corrected_opening_cash(
            day_value.isoformat(), stored, source)
    except CashActivityUnavailable as exc:
        raise AccountMetricUnavailable(str(exc)) from exc
    # The audited correction itself establishes the ET boundary. Its stored
    # timestamp belongs to the copied UTC row and must not be judged against
    # the native ET reporting window.
    if source == "legacy_utc_cutover":
        return corrected, stored_equity + corrected - stored, boundary
    opening_at = _report_boundary_time(row, day_value, boundary)
    return (corrected, stored_equity + corrected - stored,
            opening_at)


def _opening_marks_for_day(
    db: str, day_value: date, boundary: datetime,
) -> tuple[Decimal, Decimal]:
    cash, equity, _ = _opening_snapshot_for_day(db, day_value, boundary)
    return cash, equity


def _opening_cash_for_day(db: str, day_value: date,
                          boundary: datetime) -> Decimal:
    return _opening_marks_for_day(db, day_value, boundary)[0]


def historical_account_trading_by_day(
    db: str,
    days: list[date],
    changes: tuple[NontradingCashChange, ...],
    *, through: datetime | None = None,
) -> dict[str, Decimal]:
    """Use adjacent recorded ET snapshots, subtracting same-interval cash.

    This includes fills, manual closes, settlements and unbalanced inventory
    that the matched-pair ledger estimator misses. A day is omitted unless
    both boundaries are native ET snapshots within 15 minutes of midnight
    (or an exact field-pinned audited exception). Carried inventory is included in
    equity: this is marked trading, consistently with today's row, not a
    claim of closed-only realized P&L. Delayed snapshots carry their actual
    intervals and display as calendar-day estimates. Missing boundaries remain
    unavailable. The caller's cash scan must cover the closing snapshot.
    """

    if not days:
        return {}
    wanted = {day.isoformat() for day in days}
    wanted.update((day + timedelta(days=1)).isoformat() for day in days)
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" for _ in wanted)
            rows = connection.execute(
                "SELECT risk_date,opening_equity,opening_cash,created_at,source "
                f"FROM execution_risk_day_baselines WHERE risk_date IN "
                f"({placeholders})",
                tuple(sorted(wanted)),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    by_day = {str(row["risk_date"]): row for row in rows}

    def equity_at(day_value: date) -> tuple[Decimal, datetime] | None:
        key = day_value.isoformat()
        row = by_day.get(key)
        if row is None or str(row["source"] or "") != "fresh_et_day":
            return None
        boundary = datetime.combine(
            day_value, time.min, tzinfo=NEW_YORK).astimezone(timezone.utc)
        try:
            opening_at = _report_boundary_time(row, day_value, boundary)
            equity = _decimal(row["opening_equity"], "opening equity")
            cash = _decimal(row["opening_cash"], "opening cash")
        except AccountMetricUnavailable:
            return None
        if equity < 0 or cash < 0:
            return None
        return equity, opening_at

    result = AccountTradingHistory()
    for day_value in days:
        opening = equity_at(day_value)
        closing = equity_at(day_value + timedelta(days=1))
        if opening is None or closing is None:
            continue
        if through is not None and closing[1] > through:
            continue
        key = day_value.isoformat()
        nontrading = sum(
            (change.amount for change in changes
             if opening[1] < change.completed_at <= closing[1]),
            Decimal(0),
        )
        result[key] = closing[0] - opening[0] - nontrading
        result.intervals[key] = (opening[1], closing[1])
    return result


def _settlement_value(inventory: MatchedInventory, long_price: Decimal) -> Decimal:
    """Close the ledger's residual at the venue's resolution price.

    Uses the ledger's own cost basis; fill fees were already charged when the
    fills occurred, so the settlement itself carries no fee.
    """

    if inventory.position == 0 or inventory.average_cost is None:
        return Decimal(0)
    payout = long_price if inventory.position > 0 else Decimal(1) - long_price
    return abs(inventory.position) * (payout - inventory.average_cost)


def settled_replay(
    db: str, resolutions: tuple[Resolution, ...] = (),
) -> list[tuple[str, Decimal, bool]]:
    """Return ``(ET day, P&L, is_settlement)`` events in time order.

    Fills realize matched pairs and fees as before. A venue resolution then
    closes whatever inventory the ledger still carries in that market at the
    resolution price. Resolutions in markets the ledger never traded, or where
    its inventory is already flat, add nothing.
    """

    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT execution_id,market_slug,price,qty,fee,intent,"
                "event_at,received_at FROM execution_fills WHERE reversed=0 "
                "ORDER BY event_at,received_at,execution_id"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise AccountMetricUnavailable(
            "settled-fill ledger is unreadable") from exc

    # Fills keep the ledger's own order exactly; each resolution is applied
    # just before the first fill that happened after it.
    pending = sorted(resolutions, key=lambda item: item.resolved_at)
    inventories: dict[str, MatchedInventory] = {}
    result: list[tuple[str, Decimal, bool]] = []
    position = 0

    def settle(item: Resolution) -> None:
        inventory = inventories.get(item.market_slug)
        if inventory is None or inventory.position == 0:
            return
        result.append((reward_day(item.resolved_at),
                       _settlement_value(inventory, item.long_price), True))
        inventories[item.market_slug] = MatchedInventory()

    for row in rows:
        at = _timestamp(row["event_at"], "fill event")
        while position < len(pending) and pending[position].resolved_at < at:
            settle(pending[position])
            position += 1
        delta = inventories.setdefault(
            str(row["market_slug"]), MatchedInventory()).apply(
                intent=str(row["intent"] or ""),
                price=str(row["price"]),
                quantity=str(row["qty"]),
                fee=str(row["fee"] or 0),
            )
        result.append((reward_day(at), delta, False))
    for item in pending[position:]:
        settle(item)
    return result


def settlement_total(
    db: str, resolutions: tuple[Resolution, ...],
) -> Decimal:
    """Lifetime settlement P&L the fill ledger cannot see on its own."""

    return sum((delta for _, delta, settlement in settled_replay(db, resolutions)
                if settlement), Decimal(0))


def settled_trading_by_day(
    db: str, days: list[date], *,
    resolutions: tuple[Resolution, ...] = (),
    complete_through: datetime | None = None,
) -> dict[str, Decimal | None]:
    """Return settled trading P&L by ET day from one chronological replay.

    The replay includes older fills to establish the cost basis, then assigns
    each realized match and observed fill fee to the venue event's ET day, and
    each market settlement (``resolutions``) to its resolution ET day. Open
    inventory contributes no gross P&L. Reversed fills are excluded. This is a
    separate, narrower measurement than account-marked P&L: it can remain
    available when a midnight account snapshot is missing, but it never fills
    that evidence gap or finalizes account net.

    With ``complete_through``, a day ending after it is ``None``: the
    settlement history does not cover it, so its total is not proven.
    """

    result: dict[str, Decimal | None] = {
        day.isoformat(): Decimal(0) for day in days}
    if not result:
        return result
    for key, delta, _ in settled_replay(db, resolutions):
        if key in result:
            result[key] += delta
    if complete_through is not None:
        for day in days:
            end = datetime.combine(
                day + timedelta(days=1), time.min, tzinfo=NEW_YORK)
            if end > complete_through:
                result[day.isoformat()] = None
    return result


async def _nontrading_cash_today(
    gateway: AsyncReadOnlyGateway, *, boundary: datetime, through: datetime,
) -> Decimal:
    try:
        return await fetch_nontrading_cash(
            gateway, boundary=boundary, through=through,
            max_pages=ACCOUNT_ACTIVITY_MAX_PAGES)
    except CashActivityUnavailable as exc:
        raise AccountMetricUnavailable(str(exc)) from exc


async def _historical_nontrading_cash_changes(
    gateway: AsyncReadOnlyGateway, *, boundary: datetime, through: datetime,
) -> tuple[NontradingCashChange, ...]:
    """Read one immutable historical window without weakening live snapshots."""

    return await fetch_nontrading_cash_changes(
        gateway,
        boundary=boundary,
        through=through,
        max_pages=ACCOUNT_ACTIVITY_MAX_PAGES,
        fixed_historical_window=True,
    )


async def account_realized_today(
    db: str, gateway: AsyncReadOnlyGateway, *, now: datetime,
) -> AccountRealizedToday:
    """Return account-wide trading since the recorded ET opening, or no number.

    Two equal balance reads bracket the position snapshot. If settled cash
    moves while positions are being read, the report declines to mix states.
    Completed non-trading cash changes are subtracted through the second balance
    observation. Every venue operation is read-only and bounded.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        return AccountRealizedToday(None, "report time is timezone-naive")
    today = now.astimezone(NEW_YORK).date()
    boundary = datetime.combine(today, time.min, tzinfo=NEW_YORK).astimezone(
        timezone.utc)
    try:
        opening_cash, opening_equity, opening_at = _opening_snapshot_for_day(
            db, today, boundary)
        account = AsyncAccountSnapshotGateway(gateway)
        first = await account.fetch_balance(observed_at=datetime.now(timezone.utc))
        positions = await account.fetch_positions(
            observed_at=datetime.now(timezone.utc))
        # The activity window ends after the position page but before the
        # final cash read. If any trade/non-trading cash then lands, the final
        # read differs and the mixed snapshot is rejected.
        as_of = datetime.now(timezone.utc)
        nontrading = await _nontrading_cash_today(
            gateway, boundary=opening_at, through=as_of)
        second = await account.fetch_balance(observed_at=datetime.now(timezone.utc))
        if as_of.astimezone(NEW_YORK).date() != today:
            raise AccountMetricUnavailable(
                "ET day changed during the account snapshot")
        if first.current_balance != second.current_balance:
            raise AccountMetricUnavailable(
                "settled cash changed during the account snapshot")
        snapshot = account_equity_snapshot(
            second, positions, observed_at=as_of)
        marked = snapshot.marked_trading_pnl(
            opening_equity=opening_equity, nontrading_cash=nontrading)
        # Without the opening position basis, a position carried across ET
        # midnight cannot be split into today's closed and open components.
        # The total marked change remains exact from opening equity.
        realized = unrealized = None
        if opening_equity == opening_cash:
            realized = snapshot.realized_trading_pnl(
                opening_cash=opening_cash, nontrading_cash=nontrading)
            unrealized = snapshot.open_unrealized_pnl()
        return AccountRealizedToday(
            realized, "ok", marked_value=marked,
            open_unrealized=unrealized, as_of=as_of,
            opening_equity=opening_equity, opening_cash=opening_cash,
            current_cash=second.current_balance,
            position_basis=snapshot.position_basis,
            position_value=snapshot.position_value,
            account_equity=snapshot.marked_equity,
            position_count=sum(1 for item in positions if item.net_qty != 0),
            nontrading_cash=nontrading, opening_as_of=opening_at)
    except (AccountMetricUnavailable, AdapterError) as exc:
        return AccountRealizedToday(None, str(exc))


def account_realized_from_live_cache(
    db: str, path: Path, *, now: datetime,
) -> AccountRealizedToday | None:
    """Reuse the running bot's own proven mark instead of new venue reads.

    The runner publishes ``tv-live-pnl.json`` after each consistent account
    report, measured from the same recorded ET opening. Returns None (so the
    caller makes its own bounded reads) unless the file is valid, for today's
    ET day, and at most ``LIVE_MARK_MAX_AGE`` old.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        return None
    today = now.astimezone(NEW_YORK).date()
    boundary = datetime.combine(today, time.min, tzinfo=NEW_YORK).astimezone(
        timezone.utc)
    try:
        if path.stat().st_size > 8_192:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(payload, dict)
                or payload.get("schema_version") != 1
                or payload.get("scope") != "ACCOUNT_WIDE_TRADING"
                or payload.get("risk_date_et") != today.isoformat()):
            return None
        observed_at = _timestamp(payload.get("observed_at"), "live mark")
        age = now - observed_at
        if age > LIVE_MARK_MAX_AGE or age < -LIVE_MARK_MAX_FUTURE:
            return None
        if observed_at.astimezone(NEW_YORK).date() != today:
            return None
        marked = _decimal(payload.get("marked_pnl"), "live marked pnl")
        nontrading = _decimal(payload.get("nontrading_cash"), "live nontrading cash")
        realized = unrealized = None
        if payload.get("realized_pnl") is not None:
            realized = _decimal(payload["realized_pnl"], "live realized pnl")
            unrealized = _decimal(payload.get("unrealized_pnl"), "live unrealized pnl")
            if realized + unrealized != marked:
                return None
        elif payload.get("unrealized_pnl") is not None:
            return None
        opening_cash, opening_equity, opening_at = _opening_snapshot_for_day(
            db, today, boundary)
    except (OSError, ValueError):  # includes AccountMetricUnavailable
        return None
    return AccountRealizedToday(
        realized, "ok (running bot's latest mark)", marked_value=marked,
        open_unrealized=unrealized, as_of=observed_at,
        opening_equity=opening_equity, opening_cash=opening_cash,
        nontrading_cash=nontrading, opening_as_of=opening_at)


def _money(value: Decimal | None, *, zero_if_missing: bool = False) -> str:
    if value is None:
        return "$0.00" if zero_if_missing else "—"
    return f"${float(value):,.2f}"


def _trading_money(value: Decimal | None, *, missing: str = "—") -> str:
    """Format signed P&L conventionally while keeping missing data explicit."""

    if value is None:
        return missing
    sign = "-" if value < 0 else "+" if value > 0 else ""
    return f"{sign}${abs(float(value)):,.2f}"


def _displayed_trading(
    day_value: date,
    *,
    today: date,
    pnl: dict,
    account_realized: AccountRealizedToday | None,
    account_history: dict[str, Decimal] | None = None,
) -> Decimal | None:
    """Only account evidence can populate the daily trading column."""

    if day_value == today:
        return _current_mark(account_realized)
    if account_history is not None and day_value.isoformat() in account_history:
        return account_history[day_value.isoformat()]
    return None


def _current_mark(
    account_realized: AccountRealizedToday | None,
) -> Decimal | None:
    """Return the current account mark, including legacy flat-only callers."""

    if account_realized is None:
        return None
    return (account_realized.marked_value
            if account_realized.marked_value is not None
            else account_realized.value)


def _trading_is_estimate(
    day_value: date, *, today: date,
    account_history: dict[str, Decimal] | None,
) -> bool:
    interval = getattr(account_history, "intervals", {}).get(day_value.isoformat())
    if interval is None:
        return False
    start = datetime.combine(day_value, time.min, tzinfo=NEW_YORK)
    end = datetime.combine(day_value + timedelta(days=1), time.min, tzinfo=NEW_YORK)
    # The runner records each ET opening a few seconds after midnight. A
    # boundary that close is the calendar day; anything later is an estimate.
    return any(abs(actual - exact) > CALENDAR_DAY_TOLERANCE
               for actual, exact in zip(interval, (start, end)))


def reward_posting_days(paid: dict, *, today: date) -> frozenset[str]:
    """The newest closed ET days with paid rows; their payouts may still grow."""

    posted = sorted(key for key in paid if key < today.isoformat())
    return frozenset(posted[-REWARD_POSTING_DAYS:])


def _covered_total(values: list[Decimal | None]) -> str:
    known = [value for value in values if value is not None]
    if len(known) != len(values) or not values:
        return f"Unavailable ({len(known)}/{len(values)} days proven)"
    return f"${sum(known, Decimal(0)):+,.2f}"


def _coverage_summary(values: list[Decimal | None]) -> str:
    """Summarize account-mark coverage without implying a partial total."""

    known = [value for value in values if value is not None]
    if len(known) == len(values) and values:
        return f"${sum(known, Decimal(0)):+,.2f}"
    return f"{len(known)}/{len(values)} days proven"


def _snapshot_interval_notes(
    days: list[date], *, today: date,
    account_history: dict[str, Decimal] | None,
    account_realized: AccountRealizedToday | None,
) -> list[str]:
    """Expose actual ET endpoints; never present a late mark as midnight."""

    lines = []
    intervals = getattr(account_history, "intervals", {})
    for day in days:
        if _trading_is_estimate(day, today=today, account_history=account_history):
            opening, closing = intervals[day.isoformat()]
            lines.append(
                f"{day.isoformat()}: "
                f"{opening.astimezone(NEW_YORK):%m-%d %H:%M:%S %Z} to "
                f"{closing.astimezone(NEW_YORK):%m-%d %H:%M:%S %Z}")
        elif (day == today and account_realized is not None
              and account_realized.opening_as_of is not None):
            lines.append(
                f"Today starts at "
                f"{account_realized.opening_as_of.astimezone(NEW_YORK):%m-%d %H:%M:%S %Z}")
    return lines


def _net_subtotal(values: list[Decimal]) -> str:
    return (f"${sum(values, Decimal(0)):+,.2f}" if values
            else "Unavailable (no finalized days)")


def _settled_total(
    days: list[date], settled_history: dict[str, Decimal | None] | None,
) -> Decimal | None:
    """Sum settled P&L only when every day is proven."""

    if settled_history is None or not days:
        return None
    values = [settled_history.get(day.isoformat()) for day in days]
    if any(value is None for value in values):
        return None
    return sum(values, Decimal(0))


def _dashboard_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def build_dashboard_snapshot(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], today: date, generated_at: datetime,
    unbalanced_markets: int,
    overall_trading: Decimal,
    account_realized: AccountRealizedToday | None = None,
    account_history: dict[str, Decimal] | None = None,
    settled_history: dict[str, Decimal | None] | None = None,
    overall_includes_settlements: bool = False,
) -> dict[str, Any]:
    """Build the bounded local file consumed by the zero-API TV dashboard."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    if len(days) > 7:
        raise ValueError("the TV dashboard supports at most 7 days")

    rows: list[dict[str, Any]] = []
    posting = reward_posting_days(paid, today=today)
    for day_value in days:
        key = day_value.isoformat()
        trading = _displayed_trading(
            day_value, today=today, pnl=pnl,
            account_realized=account_realized,
            account_history=account_history)
        paid_value = paid.get(key)
        pending_value = pending.get(key)
        has_gain = paid_value is not None or pending_value is not None
        gain = paid_value if paid_value is not None else (
            Decimal(0) if has_gain else None)
        pending_gain = pending_value if pending_value is not None else (
            Decimal(0) if has_gain else None)
        finalized = (
            day_value < today and trading is not None and paid_value is not None
            and not pending_value and key not in posting
            and not _trading_is_estimate(
                day_value, today=today, account_history=account_history))
        net = trading + paid_value if finalized else None
        rows.append({
            "date_et": key,
            "trading_interval": [stamp.isoformat() for stamp in
                getattr(account_history, "intervals", {}).get(key, ())],
            "current_day": day_value == today,
            "trading": _dashboard_money(trading),
            "settled_trading": _dashboard_money(
                settled_history.get(key)
                if settled_history is not None else None),
            "trading_estimate": _trading_is_estimate(
                day_value, today=today, account_history=account_history),
            "gain": _dashboard_money(gain),
            "pending": _dashboard_money(pending_gain),
            "gain_posting": key in posting,
            "side_hours": round(float(resting_hours.get(key, 0.0)), 6),
            "net": _dashboard_money(net),
            "net_status": "finalized" if finalized else "pending",
        })
    paid_total = sum((Decimal(str(value)) for value in paid.values()), Decimal(0))
    overall_pnl = overall_trading + paid_total
    today_row = next((row for row in rows if row["current_day"]), None)
    return {
        "schema_version": 1,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat()
        .replace("+00:00", "Z"),
        "time_zone": "America/New_York",
        "unbalanced_markets": int(unbalanced_markets),
        "headline": {
            "today_pnl": today_row["trading"] if today_row else None,
            "today_realized": _dashboard_money(
                account_realized.value if account_realized is not None
                and account_realized.open_unrealized is not None
                else None),
            "today_unrealized": _dashboard_money(
                account_realized.open_unrealized
                if account_realized is not None else None),
            "today_as_of": (
                account_realized.as_of.astimezone(timezone.utc).isoformat()
                .replace("+00:00", "Z")
                if account_realized is not None
                and account_realized.as_of is not None else None),
            "overall_estimate": True,
            "overall_pnl": _dashboard_money(overall_pnl),
            "overall_trading": _dashboard_money(overall_trading),
            "overall_gain": _dashboard_money(paid_total),
            "overall_since_et": BOT_ERA_START_DATE,
            "overall_includes_settlements": overall_includes_settlements,
            "settled_total": _dashboard_money(_settled_total(
                days, settled_history)),
        },
        "rows": rows,
    }


def write_dashboard_snapshot(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish one private local dashboard snapshot."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def render_table(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date, account_realized: AccountRealizedToday | None = None,
    account_history: dict[str, Decimal] | None = None,
    settled_history: dict[str, Decimal | None] | None = None,
) -> str:
    posting = reward_posting_days(paid, today=today)
    lines = [
        "| Date (ET) | Settled P&L | Account P&L | Paid reward | Pending reward | "
        "Finalized net | Resting side-hours | Avg. sides resting |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    finalized_nets: list[Decimal] = []
    for day_value in days:
        key = day_value.isoformat()
        trade = _displayed_trading(
            day_value, today=today, pnl=pnl,
            account_realized=account_realized,
            account_history=account_history)
        paid_reward = paid.get(key)
        pending_reward = pending.get(key)
        has_reward_row = paid_reward is not None or pending_reward is not None
        if (day_value < today and trade is not None
                and paid_reward is not None and not pending_reward
                and key not in posting
                and not _trading_is_estimate(
                    day_value, today=today, account_history=account_history)):
            net = trade + paid_reward
            finalized_nets.append(net)
            net_text = f"**{float(net):+,.2f}**"
        else:
            net_text = "Pending"
        if not has_reward_row:
            paid_text = "Not posted"
            pending_text = "Not posted"
        else:
            paid_text = _money(paid_reward, zero_if_missing=True)
            pending_text = (
                "Still posting" if key in posting and not pending_reward
                else _money(pending_reward, zero_if_missing=True))
        resting = float(resting_hours.get(key, 0.0))
        elapsed = float(elapsed_hours.get(key, 0.0))
        average = resting / elapsed if elapsed > 0 else 0.0
        label = f"{key}{'*' if day_value == today else ''}"
        estimate = _trading_is_estimate(
            day_value, today=today, account_history=account_history)
        trade_text = (
            f"{'~' if estimate else ''}${float(trade):+,.2f}"
            if trade is not None
            else "Unavailable")
        settled = (settled_history.get(key)
                   if settled_history is not None else None)
        settled_text = (_money(settled) if settled is not None
                        else "Unavailable")
        lines.append(
            f"| {label} | {settled_text} | {trade_text} | {paid_text} | {pending_text} | "
            f"{net_text} | {resting:.2f} | {average:.2f} |")

    displayed_trades = [
        _displayed_trading(
            day, today=today, pnl=pnl, account_realized=account_realized,
            account_history=account_history)
        for day in days
    ]
    pair_total = sum(
        (pnl.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    paid_total = sum((paid.get(day.isoformat(), Decimal(0)) for day in days),
                     Decimal(0))
    pending_total = sum(
        (pending.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    resting_total = sum(resting_hours.get(day.isoformat(), 0.0) for day in days)
    settled_total = _settled_total(days, settled_history)
    lines.extend([
        "",
        (f"- Account marked trading today (ET): "
         f"**{_money(_current_mark(account_realized))}**"
         if _current_mark(account_realized) is not None
         else "- Account marked trading today (ET): **Unavailable**"),
        (f"- Closed / open mark today: "
         f"**{_money(account_realized.value)}**"
         f" / **{_money(account_realized.open_unrealized)}**"
         if account_realized is not None
         and account_realized.value is not None
         and account_realized.open_unrealized is not None
         else "- Closed / open mark today: **Unavailable**"),
        f"- Account marked trading total ({len(days)}d): **{_covered_total(displayed_trades)}**",
        f"- Settled trading total, closed fills + settlements ({len(days)}d): "
        f"**{_money(settled_total)}**",
        f"- Old balanced-pair estimate ({len(days)}d; diagnostic only, "
        f"misses settlements and unpaired fills): "
        f"**${float(pair_total):+,.2f}**",
        f"- Paid rewards: **${float(paid_total):,.2f}**",
        f"- Known pending rewards: **${float(pending_total):,.2f}**",
        f"- Finalized net subtotal (proven closed days only): "
        f"**{_net_subtotal(finalized_nets)}**",
        f"- Total resting side-hours: **{resting_total:,.2f}**",
        "",
        "`*` Current ET day is partial. `Not posted` is not zero. Pending "
        "reward is never counted in finalized net. `Still posting` marks the "
        f"newest {REWARD_POSTING_DAYS} paid days: payouts keep arriving as "
        "PAID for about two cycles, so their paid amount may still grow and "
        "their net is not finalized.",
        "Trading P&L uses recorded account snapshots, with cash changes matched "
        "to the same interval. `~` means delayed midnight snapshots, not an "
        "exact calendar day; its calendar-day net is not finalized. Boundaries "
        "within one minute of midnight count as the calendar day. "
        "Missing boundaries remain unavailable, not a matched-pair fallback. Today's row "
        "is marked trading: closed P&L plus current open-position cashValue, "
        "with proven non-trading cash excluded, since the recorded ET opening.",
        "Settled P&L is the separate ledger result: closed fills plus venue "
        "settlements of inventory held at resolution; open positions and "
        "non-trading cash are excluded and it never finalizes a missing "
        "account-marked day.",
        "Resting side-hours count every resting order, including orders that "
        "reduce a position; the market-quality tracker counts entry orders "
        "only, so it reads somewhat lower.",
    ])
    if account_realized is not None and _current_mark(account_realized) is None:
        lines.append(f"Account mark unavailable: {account_realized.reason}.")
    intervals = _snapshot_interval_notes(
        days, today=today, account_history=account_history,
        account_realized=account_realized)
    if intervals:
        lines.extend(["", "Snapshot intervals (ET; estimated daily boundaries):",
                      *intervals])
    return "\n".join(lines)


def _discord_amount(value: str, width: int) -> str:
    """The table heading supplies USD; bound large values without wrapping."""
    text = value.replace("$", "").replace(",", "")
    if len(text) <= width:
        return text
    amount = Decimal(text)
    # Scientific notation remains explicitly approximate display, never a
    # change to the source amounts or the unabridged headline totals.
    for precision in (2, 1, 0):
        compact = f"{amount:.{precision}E}"
        if len(compact) <= width:
            return compact
    return "--"


PULSE_SHORT = {"Hot": "Hot", "Good": "Good", "Average": "Avg", "Low": "Low",
               "Dead": "Dead", "Unrated": "--"}


def load_pulse(root: Path, days: list[date]) -> dict[str, dict]:
    """Reward Pulse grades already written by scripts/reward_pulse.py (local, read-only)."""
    out: dict[str, dict] = {}
    for day_value in days:
        path = Path(root) / f"{day_value.isoformat()}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out[day_value.isoformat()] = dict(
                level=str(data["level"]), vs_normal=data.get("vs_normal"),
                per_hour=data.get("per_hour"))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


# Reward Pool Tracker: our-markets pool score (1-10) as a column of the Reward
# Pulse section (operator, 2026-10-01), with the all-markets score in the note.
POOL_MAX_AGE_SECONDS = 12 * 3600


def load_pools(root: Path, *, now: datetime) -> dict | None:
    """Pool scores from scripts/reward_pool_tracker.py output (local, read-only)."""
    root = Path(root)
    try:
        score_path = root / "latest-score.json"
        if now.timestamp() - score_path.stat().st_mtime > POOL_MAX_AGE_SECONDS:
            return None
        score = json.loads(score_path.read_text(encoding="utf-8"))
        rows = json.loads((root / "latest.json").read_text(encoding="utf-8"))
        allp, ours = score["all"], score["ours"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    new = [r["family"].replace("_moneyline", "").replace("_day_of", "").replace("_dayof", "")
           .replace("_", " ").upper() for r in rows
           if r.get("alert") == "NEW" and r.get("period") == "day_of"]
    changed = sum(1 for r in rows if r.get("alert") == "CHANGED")
    return dict(all=allp, ours=ours, new=new, changed=changed,
                by_day=dict(score.get("ours_by_day") or {}),
                profitable=str(score.get("profitable", "MAYBE")))


def pool_note(pools: dict) -> str:
    """One short line for the footer / image note."""
    parts = [f"all markets {pools['all']['score']:.1f}/10",
             f"ours {pools['ours']['score']:.1f}/10 profitable {pools['profitable']}"]
    if pools.get("new"):
        parts.append("new: " + ", ".join(pools["new"][:3]))
    if pools.get("changed"):
        parts.append(f"{pools['changed']} changed")
    return "pools " + "; ".join(parts)


def _pulse_lines(days: list[date], pulse: dict[str, dict], today: date,
                 pools: dict | None = None) -> list[str]:
    last = "Pool" if pools else "$/h"
    lines = [f"{'Date':<6} {'Level':>7} {'vsNorm':>7} {last:>7}",
             "------ ------- ------- -------"]
    for day_value in days:
        key = day_value.isoformat()
        row = pulse.get(key) or {}
        label = f"{day_value.month}/{day_value.day}{'*' if day_value == today else ''}"
        ratio = row.get("vs_normal")
        if pools:
            v = pools["by_day"].get(key)
            fourth = "--" if v is None else f"{v:.1f}"
        else:
            rate = row.get("per_hour")
            fourth = "--" if rate is None else f"{rate:.2f}"
        lines.append(
            f"{label:<6} {PULSE_SHORT.get(row.get('level'), '--'):>7} "
            f"{('--' if ratio is None else f'{ratio:.2f}x'):>7} {fourth:>7}")
    return lines


def build_discord_payload(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date, generated_at: datetime, unbalanced_markets: int,
    account_realized: AccountRealizedToday | None = None,
    account_history: dict[str, Decimal] | None = None,
    settled_history: dict[str, Decimal | None] | None = None,
    period_hours: dict[str, dict[str, float]] | None = None,
    pulse: dict[str, dict] | None = None,
    pools: dict | None = None,
) -> dict:
    """Build a compact settled-trading and reward report for Discord.

    The webhook deliberately uses only the ledger (closed fills plus venue
    settlements) for trading P&L.
    Account-marked equity remains available to the dashboard and CLI, but it
    is not mixed into this operator-facing reward report.
    """

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    if len(days) > 7:
        raise ValueError("the Discord table layout supports at most 7 days")

    pnl_lines = [
        f"{'Date':<6} {'P&L':>8} {'Paid':>7} {'Pend':>7}",
        "------ -------- ------- -------",
    ]
    resting_lines = [
        f"{'Date':<6} {'Day h':>7} {'Daily h':>7} {'Avg':>5}",
        "------ ------- ------- -----",
    ]
    partial_split = False
    posting = reward_posting_days(paid, today=today)
    posting_shown = False
    for day_value in days:
        key = day_value.isoformat()
        paid_reward = paid.get(key)
        pending_reward = pending.get(key)
        has_reward = paid_reward is not None or pending_reward is not None
        settled = (settled_history.get(key)
                   if settled_history is not None else None)
        trading_text = _discord_amount(_trading_money(settled, missing="--"), 8)
        if has_reward:
            paid_text = _discord_amount(_money(paid_reward, zero_if_missing=True), 7)
            if key in posting and not pending_reward:
                pending_text = "posting"
                posting_shown = True
            else:
                pending_text = _discord_amount(
                    _money(pending_reward, zero_if_missing=True), 7)
        else:
            paid_text = "--"
            pending_text = "--"
        resting = float(resting_hours.get(key, 0.0))
        elapsed = float(elapsed_hours.get(key, 0.0))
        average = resting / elapsed if elapsed > 0 else 0.0
        label = f"{day_value.month}/{day_value.day}{'*' if day_value == today else ''}"
        pnl_lines.append(
            f"{label:<6} {trading_text:>8} {paid_text:>7} {pending_text:>7}")
        split = period_hours.get(key) if period_hours is not None else None
        if split is None:
            day_text = daily_text = "--"
        else:
            incomplete = split.get("unallocated", 0.0) > 1e-9
            partial_split |= incomplete
            suffix = "+" if incomplete else ""
            day_text = _discord_amount(f"{split['day_of']:.1f}", 7-len(suffix)) + suffix
            daily_text = _discord_amount(f"{split['daily_event']:.1f}", 7-len(suffix)) + suffix
        resting_lines.append(
            f"{label:<6} {day_text:>7} {daily_text:>7} "
            f"{_discord_amount(f'{average:.1f}', 5):>5}")
    paid_total = sum(
        (paid.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    pending_total = sum(
        (pending.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    settled_total = _settled_total(days, settled_history)
    reward_observed = any(
        paid.get(day.isoformat()) is not None
        or pending.get(day.isoformat()) is not None for day in days)
    fields = [
        {
            "name": "Results ($)",
            "value": "```text\n" + "\n".join(pnl_lines) + "\n```",
            "inline": False,
        },
        {
            "name": "Liquidity · Avg sides",
            "value": "```text\n" + "\n".join(resting_lines) + "\n```",
            "inline": False,
        },
    ]
    if pulse or pools:
        # Operator 2026-09-27: Reward Pulse grade per day (vs our 3-week normal);
        # 2026-10-01: the our-markets pool score (1-10) is its last column.
        fields.append({
            "name": "Reward Pulse",
            "value": "```text\n" + "\n".join(_pulse_lines(days, pulse or {}, today, pools)) + "\n```",
            "inline": False,
        })
    embed = {
        "title": f"Rewards · {len(days)}d · ET",
        "description": (
            f"**P&L:** {_trading_money(settled_total)}\n"
            f"**Paid:** {_money(paid_total) if reward_observed else '--'}\n"
            f"**Pend:** {_money(pending_total) if reward_observed else '--'}"),
        "fields": fields,
        "color": 0x3498DB,
        "timestamp": generated_at.isoformat(),
        "footer": {"text": "P&L: closed fills + settlements · * today" + (
            " · posting: payouts still arriving" if posting_shown else "") + (
            " · + partial split; Avg includes untyped hours" if partial_split else "") + (
            f" · {pool_note(pools)}" if pools else "")},
    }
    total_chars = (
        len(embed["title"]) + len(embed["description"])
        + len(embed["footer"]["text"])
        + sum(len(field["name"]) + len(field["value"])
              for field in fields))
    if total_chars > 6000:
        raise ValueError("Discord reward embed exceeds the 6,000-character cap")
    if any(len(field["value"]) > 1024 for field in fields):
        raise ValueError("Discord reward table exceeds a field's 1,024-character cap")
    return {"embeds": [embed]}


# --- Trading summary (operator 2026-10-03: "change summary section and add end game
# trader info ... remove unnecessary data and just provide useful information") ---

ENDGAME_DB = Path("data/late-favorite/trader.db")


def endgame_by_day(days: list[date], path: Path = ENDGAME_DB) -> dict:
    """Endgame Trader real trades per ET day (by buy day) plus what is open now."""
    out = {"days": {d.isoformat(): {"trades": 0, "won": 0, "lost": 0, "result": None} for d in days},
           "open": 0, "at_risk": Decimal(0), "available": path.exists()}
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT created_at, cost, avg_price, qty, filled_qty, settlement, result FROM trades "
                "WHERE mode='live' AND status='FILLED'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        out["available"] = False
        return out
    for created, cost, avg, qty, filled, settlement, result in rows:
        day = reward_day(datetime.fromisoformat(created.replace("Z", "+00:00")))
        if settlement is None:
            out["open"] += 1
            out["at_risk"] += Decimal(str(avg if avg is not None else cost)) * Decimal(str(filled or qty))
        cell = out["days"].get(day)
        if cell is None:
            continue
        cell["trades"] += 1
        if result is not None:
            cell["won" if result > 0 else "lost"] += 1
            cell["result"] = (cell["result"] or Decimal(0)) + Decimal(str(result))
    return out


def bots_running() -> dict[str, bool]:
    """Which bots are up right now (report-only; never affects trading)."""
    try:
        import subprocess
        args = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    return {"Liquidity": "liquidity_bot.live_commissioning" in args,
            "Endgame": "late_favorite_trader.py run" in args}


def _signed(value: Decimal | None, missing: str = "--") -> str:
    if value is None:
        return missing
    return f"{'+' if value > 0 else '-' if value < 0 else ''}{abs(value):.2f}"


def build_summary_payload(
    days: list[date], *, paid: dict, pending: dict, today: date, generated_at: datetime,
    settled_history: dict[str, Decimal | None], endgame: dict, pools: dict | None = None,
    running: dict[str, bool] | None = None,
) -> dict:
    """One clean card: Liquidity Trader (trading, rewards, net) and Endgame Trader per day."""
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    if len(days) > 7:
        raise ValueError("the summary layout supports at most 7 days")
    liq = ["Date Trading Rewards Net", "---- ------- ------- ---"]
    end = ["Date Trades W-L Result", "---- ------ --- ------"]
    liq_total = Decimal(0)
    end_total = Decimal(0)
    for day_value in days:
        key = day_value.isoformat()
        label = f"{day_value.month}/{day_value.day}{'*' if day_value == today else ''}"
        trading = settled_history.get(key)
        has_reward = paid.get(key) is not None or pending.get(key) is not None
        reward = (paid.get(key) or Decimal(0)) + (pending.get(key) or Decimal(0)) if has_reward else None
        net = None if trading is None and reward is None else (trading or Decimal(0)) + (reward or Decimal(0))
        liq_total += net or Decimal(0)
        liq.append(f"{label} {_signed(trading)} {'--' if reward is None else f'{reward:.2f}'} {_signed(net)}")
        cell = endgame["days"].get(key, {"trades": 0, "won": 0, "lost": 0, "result": None})
        end_total += cell["result"] or Decimal(0)
        if cell["trades"] or day_value == today:
            # Only days it traded (and today): no rows of dashes before it existed.
            wl = f"{cell['won']}-{cell['lost']}" if cell["trades"] else "--"
            end.append(f"{label} {cell['trades'] or '--'} {wl} {_signed(cell['result'])}")
    notes = ["Net = trading (closed fills + settlements) + rewards (paid + pending) · * today"]
    if running:
        status = " · ".join(f"{name} {'ON' if on else 'OFF'}" for name, on in running.items())
        if endgame.get("available"):
            status += f" · Endgame open {endgame['open']} (${endgame['at_risk']:.2f} at risk)"
        notes.append("Now: " + status)
    if pools and pools.get("ours"):
        score = pools["ours"].get("score")
        notes.append(f"Reward pools (our pre-game markets): {score}/10 - "
                     f"{'worth farming' if (score or 0) > 5 else 'not profitable to farm'}")
    fields = [
        {"name": "Liquidity Trader", "value": "```text\n" + "\n".join(liq) + "\n```", "inline": False},
        {"name": "Endgame Trader", "value": "```text\n" + "\n".join(end) + "\n```", "inline": False},
    ]
    embed = {
        "title": f"Trading summary · {len(days)}d · ET",
        "description": (f"**Liquidity:** {_signed(liq_total)}\n**Endgame:** {_signed(end_total)}\n"
                        f"**Total:** {_signed(liq_total + end_total)}"),
        "fields": fields, "color": 0x3498DB, "timestamp": generated_at.isoformat(),
        "footer": {"text": "\n".join(notes)},
    }
    if any(len(f["value"]) > 1024 for f in fields):
        raise ValueError("summary table exceeds a field's 1,024-character cap")
    return {"embeds": [embed]}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--post-discord", action="store_true",
        help="post to the dedicated rewards webhook")
    parser.add_argument(
        "--pool-root", default="data/reward-pool-tracker",
        help="Reward Pool Tracker output to show in the Discord table")
    parser.add_argument(
        "--pulse-root", default="data/reward-pulse",
        help="Reward Pulse grades to show in the Discord table (scripts/reward_pulse.py)")
    parser.add_argument(
        "--quiet", action="store_true",
        help="suppress the table on stdout; requires --post-discord")
    parser.add_argument(
        "--dashboard-cache", type=Path,
        help="atomically publish the seven-day local TV dashboard snapshot")
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be positive")
    if args.quiet and not args.post_discord:
        parser.error("--quiet requires --post-discord")

    now = datetime.now(timezone.utc)
    today = date.fromisoformat(reward_day(now))
    days = [today - timedelta(days=n)
            for n in range(args.days - 1, -1, -1)]
    pnl, _, unbalanced = trading_by_day(args.db, CLEAN_FROM)
    ledger = ExecutionLedger.open_read_only(args.db)
    try:
        overall_trading = ledger.realized_pnl()
    finally:
        ledger.close()
    gateway = AsyncReadOnlyGateway(
        client_from_environment(),
        limiter=shared_api_limiter(ACCOUNT_REPORT_RATE_PER_SECOND),
        default_traffic_class="report",
        max_pages=ACCOUNT_ACTIVITY_MAX_PAGES)
    settlements = await refresh_resolutions(
        gateway, Path(args.db).parent / SETTLEMENT_CACHE_FILENAME, now=now)
    if settlements.error is not None:
        print(f"Settlement history: {settlements.error}", file=sys.stderr)
    if settlements.complete_through is None:
        # No settlement history at all: settled P&L is not proven for any day.
        settled_history = {day.isoformat(): None for day in days}
        overall_includes_settlements = False
    else:
        # A fresh read covers everything up to now, including today so far.
        # After a failed read only the days the cached history covers count.
        settled_history = settled_trading_by_day(
            args.db, days, resolutions=settlements.resolutions,
            complete_through=(None if settlements.complete_through >= now
                              else settlements.complete_through))
        overall_trading += settlement_total(args.db, settlements.resolutions)
        overall_includes_settlements = True
    paid, pending = await reward_by_day(gateway)
    history: dict[str, Decimal] = {}
    try:
        history_boundary = datetime.combine(
            days[0], time.min, tzinfo=NEW_YORK).astimezone(timezone.utc)
        changes = await _historical_nontrading_cash_changes(
            gateway,
            boundary=history_boundary,
            through=now,
        )
        history = historical_account_trading_by_day(
            args.db, days, changes, through=now)
    except CashActivityUnavailable:
        # Historical account rows remain unavailable. Today's independently
        # bounded snapshot still runs; no incomplete ledger fallback.
        history = {}
    # Judge the mark's age at read time; the reads above can take minutes.
    read_at = datetime.now(timezone.utc)
    account_realized = (account_realized_from_live_cache(
        args.db, Path(args.db).parent / LIVE_PNL_CACHE_FILENAME, now=read_at)
        if reward_day(read_at) == today.isoformat() else None)
    if account_realized is None:
        account_realized = await account_realized_today(
            args.db, gateway, now=now)
    resting, elapsed = resting_side_hours_by_day(
        args.db, days, now=now)
    table = render_table(
        days, pnl=pnl, paid=paid, pending=pending,
        resting_hours=resting, elapsed_hours=elapsed, today=today,
        account_realized=account_realized, account_history=history,
        settled_history=settled_history)
    if args.dashboard_cache is not None:
        write_dashboard_snapshot(
            args.dashboard_cache,
            build_dashboard_snapshot(
                days, pnl=pnl, paid=paid, pending=pending,
                resting_hours=resting, today=today, generated_at=now,
                unbalanced_markets=unbalanced,
                overall_trading=overall_trading,
                account_realized=account_realized,
                account_history=history,
                settled_history=settled_history,
                overall_includes_settlements=overall_includes_settlements))
    if not args.quiet:
        print(table)
        print()
        print("Daily trading is account marked P&L excluding non-trading cash. "
              "The old pair estimate is diagnostic only: it pairs buy fills "
              f"alone, cannot pair {unbalanced} market(s) and excludes "
              "settlements.")
        if _displayed_trading(
                today, today=today, pnl={},
                account_realized=account_realized) is None:
            print(f"Account marked trading today is unavailable: "
                  f"{account_realized.reason}.")
        print("Resting side-hours merge overlap per market/side and may exceed 24 "
              "when several sides rest simultaneously.")
    if args.post_discord:
        from liquidity_bot.notifications import DiscordNotifier
        from liquidity_bot.runtime_secrets import load_runtime_secrets
        from scripts.reward_image import send_reward_report

        secrets = load_runtime_secrets(require_rewards_webhook=True)
        notifier = DiscordNotifier(secrets)
        try:
            period_hours = resting_period_hours_by_day(args.db, days, now=now)
        except (sqlite3.Error, OSError):
            # Optional classification must not suppress the existing financial
            # report or fabricate zero hours if the evidence cannot be read.
            period_hours = None
            print("Reward-period hours unavailable; totals retained.", file=sys.stderr)
        from scripts.reward_image import send_summary_report
        result = await send_summary_report(
            build_summary_payload(
                days, paid=paid, pending=pending, today=today, generated_at=now,
                settled_history=settled_history, endgame=endgame_by_day(days),
                pools=load_pools(Path(args.pool_root), now=now), running=bots_running()),
            secrets, notifier)
        if not result.sent:
            print(f"reward table Discord post failed: {result.reason}",
                  file=sys.stderr)
            return 1
        print("reward table posted to dedicated Discord webhook")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
