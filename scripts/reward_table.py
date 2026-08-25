#!/usr/bin/env python3
"""One daily table: trading P&L, venue reward, and resting side-hours.

Read-only. The command makes one account-wide liquidity-earnings request and
reads the commissioning ledger in SQLite read-only mode. It never places,
cancels, modifies, or reprices an order.

    .venv/bin/python -B scripts/reward_table.py --days 7

Reward dates and table days use America/New_York, matching the venue. Trading
uses the same balanced-leg lower-bound calculation as ``daily_pnl.py``.
Resting time merges overlapping orders per (market, side), splits intervals at
New York midnight, and sums market-side hours. It can therefore exceed 24
hours per day when several market sides rest simultaneously.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.daily_pnl import CLEAN_FROM, reward_by_day, trading_by_day  # noqa: E402
from liquidity_bot.reward_calendar import NEW_YORK, reward_day  # noqa: E402


WORKING_ORDER_STATES = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, NEW_YORK)
    end = datetime.combine(day + timedelta(days=1), time.min, NEW_YORK)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def resting_side_hours_by_day(
    db: str, days: list[date], *, now: datetime,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return summed resting side-hours and elapsed hours for ET days.

    Replacement acknowledgements can briefly leave two ledger orders for the
    same market side whose intervals overlap. Merge by (market, side) before
    summing so those seconds are not counted twice.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not days:
        return {}, {}
    ordered_days = sorted(days)
    window_start, _ = _day_bounds(ordered_days[0])
    _, final_end = _day_bounds(ordered_days[-1])
    window_end = min(now.astimezone(timezone.utc), final_end)

    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        placed: dict[str, datetime] = {}
        for row in connection.execute(
                "SELECT client_order_id, created_at FROM execution_intents "
                "WHERE operation='PLACE' AND client_order_id IS NOT NULL"):
            at = _parse(row["created_at"])
            key = str(row["client_order_id"])
            if at is not None and (key not in placed or at < placed[key]):
                placed[key] = at

        intervals: dict[
            tuple[str, str], list[tuple[datetime, datetime]]
        ] = defaultdict(list)
        for row in connection.execute(
                "SELECT client_order_id, market_slug, side, status, event_at "
                "FROM execution_orders"):
            start = placed.get(str(row["client_order_id"]))
            if start is None:
                continue
            end = (now if str(row["status"]) in WORKING_ORDER_STATES
                   else _parse(row["event_at"]))
            if end is None:
                continue
            start = max(start.astimezone(timezone.utc), window_start)
            end = min(end.astimezone(timezone.utc), window_end)
            if end <= start:
                continue
            side = ("bid" if "BUY" in str(row["side"]).upper()
                    else "offer")
            intervals[(str(row["market_slug"]), side)].append((start, end))
    finally:
        connection.close()

    merged: list[tuple[datetime, datetime]] = []
    for spans in intervals.values():
        spans.sort()
        current_start, current_end = spans[0]
        for start, end in spans[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = start, end
        merged.append((current_start, current_end))

    wanted = set(ordered_days)
    hours: dict[str, float] = defaultdict(float)
    for start, end in merged:
        cursor = start
        while cursor < end:
            local_day = cursor.astimezone(NEW_YORK).date()
            _, next_midnight = _day_bounds(local_day)
            stop = min(end, next_midnight)
            if local_day in wanted:
                hours[local_day.isoformat()] += (
                    stop - cursor).total_seconds() / 3600
            cursor = stop

    elapsed: dict[str, float] = {}
    for day in ordered_days:
        start, end = _day_bounds(day)
        elapsed[day.isoformat()] = max(
            0.0, (min(window_end, end) - start).total_seconds() / 3600)
    return dict(hours), elapsed


def _money(value: Decimal | None, *, zero_if_missing: bool = False) -> str:
    if value is None:
        return "$0.00" if zero_if_missing else "—"
    return f"${float(value):,.2f}"


def render_table(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date,
) -> str:
    lines = [
        "| Date (ET) | Trading P&L | Paid reward | Pending reward | "
        "Finalized net | Resting side-hours | Avg. sides resting |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    complete_net = Decimal(0)
    for day_value in days:
        key = day_value.isoformat()
        trade = pnl.get(key)
        paid_reward = paid.get(key)
        pending_reward = pending.get(key)
        has_reward_row = paid_reward is not None or pending_reward is not None
        if trade is not None and paid_reward is not None and not pending_reward:
            net = trade + paid_reward
            complete_net += net
            net_text = f"**{float(net):+,.2f}**"
        else:
            net_text = "Pending"
        if not has_reward_row:
            paid_text = "Not posted"
            pending_text = "Not posted"
        else:
            paid_text = _money(paid_reward, zero_if_missing=True)
            pending_text = _money(pending_reward, zero_if_missing=True)
        resting = float(resting_hours.get(key, 0.0))
        elapsed = float(elapsed_hours.get(key, 0.0))
        average = resting / elapsed if elapsed > 0 else 0.0
        label = f"{key}{'*' if day_value == today else ''}"
        trade_text = (f"${float(trade):+,.2f}" if trade is not None else "n/a")
        lines.append(
            f"| {label} | {trade_text} | {paid_text} | {pending_text} | "
            f"{net_text} | {resting:.2f} | {average:.2f} |")

    trade_total = sum((pnl.get(day.isoformat(), Decimal(0)) for day in days),
                      Decimal(0))
    paid_total = sum((paid.get(day.isoformat(), Decimal(0)) for day in days),
                     Decimal(0))
    pending_total = sum(
        (pending.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    resting_total = sum(resting_hours.get(day.isoformat(), 0.0) for day in days)
    lines.extend([
        "",
        f"- Trading P&L: **${float(trade_total):+,.2f}**",
        f"- Paid rewards: **${float(paid_total):,.2f}**",
        f"- Known pending rewards: **${float(pending_total):,.2f}**",
        f"- Finalized net (complete reward days only): "
        f"**${float(complete_net):+,.2f}**",
        f"- Total resting side-hours: **{resting_total:,.2f}**",
        "",
        "`*` Current ET day is partial. `Not posted` is not zero. Pending "
        "reward is never counted in finalized net.",
    ])
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be positive")

    now = datetime.now(timezone.utc)
    today = date.fromisoformat(reward_day(now))
    days = [today - timedelta(days=n)
            for n in range(args.days - 1, -1, -1)]
    pnl, _, unbalanced = trading_by_day(args.db, CLEAN_FROM)
    paid, pending = await reward_by_day()
    resting, elapsed = resting_side_hours_by_day(
        args.db, days, now=now)
    print(render_table(
        days, pnl=pnl, paid=paid, pending=pending,
        resting_hours=resting, elapsed_hours=elapsed, today=today))
    print()
    print("Trading is balanced-leg ledger P&L and excludes "
          f"{unbalanced} unbalanced market(s); settled exits can be missing.")
    print("Resting side-hours merge overlap per market/side and may exceed 24 "
          "when several sides rest simultaneously.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
