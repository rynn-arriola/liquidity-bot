#!/usr/bin/env python3
"""One daily table: trading P&L, venue reward, and resting side-hours.

Read-only. The command makes bounded account-wide earnings, cash-activity,
balance and position reads and opens the commissioning ledger in SQLite
read-only mode. It never places, cancels, modifies, or reprices an order.

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
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.daily_pnl import CLEAN_FROM, reward_by_day, trading_by_day  # noqa: E402
from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway  # noqa: E402
from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway  # noqa: E402
from liquidity_bot.cash_activity import (  # noqa: E402
    CashActivityUnavailable,
    corrected_opening_cash,
    fetch_nontrading_cash,
)
from liquidity_bot.errors import AdapterError  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402
from liquidity_bot.reward_calendar import NEW_YORK, reward_day  # noqa: E402
from liquidity_bot.resting_time import resting_side_hours_by_day  # noqa: E402


ACCOUNT_ACTIVITY_MAX_PAGES = 4
ACCOUNT_REPORT_RATE_PER_SECOND = 1.0
BASELINE_START_TOLERANCE = timedelta(minutes=2)


@dataclass(frozen=True)
class AccountRealizedToday:
    value: Decimal | None
    reason: str
    as_of: datetime | None = None
    opening_cash: Decimal | None = None
    current_cash: Decimal | None = None
    position_basis: Decimal | None = None
    nontrading_cash: Decimal | None = None


class AccountMetricUnavailable(ValueError):
    """The current-day account number cannot be proved from bounded reads."""


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


def _opening_cash_for_day(db: str, day_value: date,
                          boundary: datetime) -> Decimal:
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT opening_cash,created_at,source "
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
    source = str(row["source"] or "")
    try:
        corrected = corrected_opening_cash(
            day_value.isoformat(), stored, source)
    except CashActivityUnavailable as exc:
        raise AccountMetricUnavailable(str(exc)) from exc
    # The audited correction itself establishes the ET boundary. Its stored
    # timestamp belongs to the copied UTC row and must not be judged against
    # the fresh-ET two-minute tolerance.
    if source == "legacy_utc_cutover":
        return corrected
    created_at = _timestamp(row["created_at"], "opening baseline")
    delta = created_at - boundary.astimezone(timezone.utc)
    if delta < timedelta(0) or delta > BASELINE_START_TOLERANCE:
        raise AccountMetricUnavailable(
            "ET baseline was not captured at the day boundary")
    return corrected


async def _nontrading_cash_today(
    gateway: AsyncReadOnlyGateway, *, boundary: datetime, through: datetime,
) -> Decimal:
    try:
        return await fetch_nontrading_cash(
            gateway, boundary=boundary, through=through,
            max_pages=ACCOUNT_ACTIVITY_MAX_PAGES)
    except CashActivityUnavailable as exc:
        raise AccountMetricUnavailable(str(exc)) from exc


async def account_realized_today(
    db: str, gateway: AsyncReadOnlyGateway, *, now: datetime,
) -> AccountRealizedToday:
    """Return account-wide realized trading since ET midnight, or no number.

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
        opening_cash = _opening_cash_for_day(db, today, boundary)
        account = AsyncAccountSnapshotGateway(gateway)
        first = await account.fetch_balance(observed_at=datetime.now(timezone.utc))
        positions = await account.fetch_positions(
            observed_at=datetime.now(timezone.utc))
        second = await account.fetch_balance(observed_at=datetime.now(timezone.utc))
        as_of = datetime.now(timezone.utc)
        if as_of.astimezone(NEW_YORK).date() != today:
            raise AccountMetricUnavailable(
                "ET day changed during the account snapshot")
        if first.current_balance != second.current_balance:
            raise AccountMetricUnavailable(
                "settled cash changed during the account snapshot")
        basis = Decimal(0)
        for position in positions:
            if position.net_qty == 0:
                continue
            if position.cost is None:
                raise AccountMetricUnavailable(
                    f"open position has no venue basis: {position.market_slug}")
            cost = _decimal(position.cost, "position basis")
            if cost < 0:
                raise AccountMetricUnavailable(
                    f"open position basis is negative: {position.market_slug}")
            basis += cost
        nontrading = await _nontrading_cash_today(
            gateway, boundary=boundary, through=as_of)
        value = second.current_balance - opening_cash - nontrading + basis
        return AccountRealizedToday(
            value, "ok", as_of=as_of, opening_cash=opening_cash,
            current_cash=second.current_balance, position_basis=basis,
            nontrading_cash=nontrading)
    except (AccountMetricUnavailable, AdapterError) as exc:
        return AccountRealizedToday(None, str(exc))


def _money(value: Decimal | None, *, zero_if_missing: bool = False) -> str:
    if value is None:
        return "$0.00" if zero_if_missing else "—"
    return f"${float(value):,.2f}"


def _displayed_trading(
    day_value: date,
    *,
    today: date,
    pnl: dict,
    account_realized: AccountRealizedToday | None,
) -> Decimal | None:
    """Use account truth today and the balanced-pair estimate historically."""

    if day_value == today:
        return account_realized.value if account_realized is not None else None
    return pnl.get(day_value.isoformat())


def render_table(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date, account_realized: AccountRealizedToday | None = None,
) -> str:
    lines = [
        "| Date (ET) | Trading P&L | Paid reward | Pending reward | "
        "Finalized net | Resting side-hours | Avg. sides resting |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    complete_net = Decimal(0)
    for day_value in days:
        key = day_value.isoformat()
        trade = _displayed_trading(
            day_value, today=today, pnl=pnl,
            account_realized=account_realized)
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
        trade_text = (
            f"${float(trade):+,.2f}" if trade is not None
            else "Unavailable" if day_value == today else "n/a")
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
        (f"- Account realized today (ET): "
         f"**{_money(account_realized.value)}**"
         if account_realized is not None and account_realized.value is not None
         else "- Account realized today (ET): **Unavailable**"),
        f"- Balanced-pair estimate (7d): **${float(trade_total):+,.2f}**",
        f"- Paid rewards: **${float(paid_total):,.2f}**",
        f"- Known pending rewards: **${float(pending_total):,.2f}**",
        f"- Finalized net (complete reward days only): "
        f"**${float(complete_net):+,.2f}**",
        f"- Total resting side-hours: **{resting_total:,.2f}**",
        "",
        "`*` Current ET day is partial. `Not posted` is not zero. Pending "
        "reward is never counted in finalized net.",
        "Today's Trading P&L is account-wide settled-cash change, less "
        "completed non-trading cash changes, plus current open-position basis. "
        "Historical Trading P&L rows remain balanced-pair estimates.",
    ])
    if account_realized is not None and account_realized.value is None:
        lines.append(f"Account realized unavailable: {account_realized.reason}.")
    return "\n".join(lines)


def build_discord_payload(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date, generated_at: datetime, unbalanced_markets: int,
    account_realized: AccountRealizedToday | None = None,
) -> dict:
    """Build two narrow, aligned tables that Discord renders consistently."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    if len(days) > 7:
        raise ValueError("the Discord table layout supports at most 7 days")

    pnl_lines = [
        f"{'Date ET':<11}  {'Trading':>9}  {'Paid':>8}  "
        f"{'Pending':>8}  {'Net':>9}",
        "-----------  ---------  --------  --------  ---------",
    ]
    resting_lines = [
        "Date ET      Side-hours  Avg sides",
        "-----------  ----------  ---------",
    ]
    complete_net = Decimal(0)
    for day_value in days:
        key = day_value.isoformat()
        trade = _displayed_trading(
            day_value, today=today, pnl=pnl,
            account_realized=account_realized)
        paid_reward = paid.get(key)
        pending_reward = pending.get(key)
        has_reward = paid_reward is not None or pending_reward is not None
        complete = (
            trade is not None and paid_reward is not None
            and not pending_reward)
        if complete:
            net = trade + paid_reward
            complete_net += net
            net_text = f"${float(net):+,.2f}"
        else:
            net_text = "Pending"

        trade_text = (
            f"${float(trade):+,.2f}" if trade is not None
            else "unavail" if day_value == today else "n/a")
        if has_reward:
            paid_text = _money(paid_reward, zero_if_missing=True)
            pending_text = _money(pending_reward, zero_if_missing=True)
        else:
            paid_text = "--"
            pending_text = "--"
        resting = float(resting_hours.get(key, 0.0))
        elapsed = float(elapsed_hours.get(key, 0.0))
        average = resting / elapsed if elapsed > 0 else 0.0
        label = f"{key}{'*' if day_value == today else ''}"
        pnl_lines.append(
            f"{label:<11}  {trade_text:>9}  {paid_text:>8}  "
            f"{pending_text:>8}  {net_text:>9}")
        resting_lines.append(
            f"{label:<11}  {resting:>10.2f}  {average:>9.2f}")

    trade_total = sum(
        (pnl.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    paid_total = sum(
        (paid.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    pending_total = sum(
        (pending.get(day.isoformat(), Decimal(0)) for day in days), Decimal(0))
    resting_total = sum(resting_hours.get(day.isoformat(), 0.0) for day in days)
    stamp = generated_at.astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M %Z")
    fields = [
        {
            "name": "Daily P&L and rewards",
            "value": "```text\n" + "\n".join(pnl_lines) + "\n```",
            "inline": False,
        },
        {
            "name": "Liquidity supplied",
            "value": "```text\n" + "\n".join(resting_lines) + "\n```",
            "inline": False,
        },
    ]
    embed = {
        "title": "Liquidity Bot · Last 7 ET Days",
        "description": (
            f"**Account realized today (ET):** "
            f"{_money(account_realized.value) if account_realized is not None else '—'}\n"
            f"**Balanced-pair estimate (7d):** ${float(trade_total):+,.2f}\n"
            f"**Paid rewards:** ${float(paid_total):,.2f}\n"
            f"**Known pending:** ${float(pending_total):,.2f}\n"
            f"**Finalized net:** ${float(complete_net):+,.2f}\n"
            f"**Resting side-hours:** {resting_total:,.2f}"),
        "fields": fields,
        "footer": {"text": (
            f"Generated {stamp} · * current ET day is partial · "
            "-- reward not posted · Pending excluded from net · "
            "Today uses account-wide cash change - transfers + open basis · "
            "Historical rows are balanced-pair estimates; "
            f"{unbalanced_markets} unbalanced market(s) and settled exits may "
            "be missing")},
    }
    if account_realized is not None and account_realized.value is None:
        embed["footer"]["text"] += (
            f" · Account figure unavailable: {account_realized.reason}")
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


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--post-discord", action="store_true",
        help="post to the dedicated rewards webhook")
    parser.add_argument(
        "--quiet", action="store_true",
        help="suppress the table on stdout; requires --post-discord")
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
    gateway = AsyncReadOnlyGateway(
        client_from_environment(),
        limiter=AsyncIntervalLimiter.from_rate(ACCOUNT_REPORT_RATE_PER_SECOND),
        max_pages=ACCOUNT_ACTIVITY_MAX_PAGES)
    paid, pending = await reward_by_day(gateway)
    account_realized = await account_realized_today(
        args.db, gateway, now=now)
    resting, elapsed = resting_side_hours_by_day(
        args.db, days, now=now)
    table = render_table(
        days, pnl=pnl, paid=paid, pending=pending,
        resting_hours=resting, elapsed_hours=elapsed, today=today,
        account_realized=account_realized)
    if not args.quiet:
        print(table)
        print()
        print("Trading is balanced-leg ledger P&L and excludes "
              f"{unbalanced} unbalanced market(s); settled exits can be missing.")
        if account_realized.value is None:
            print(f"Account realized today is unavailable: "
                  f"{account_realized.reason}.")
        print("Resting side-hours merge overlap per market/side and may exceed 24 "
              "when several sides rest simultaneously.")
    if args.post_discord:
        from liquidity_bot.notifications import DiscordNotifier
        from liquidity_bot.runtime_secrets import load_runtime_secrets

        notifier = DiscordNotifier(load_runtime_secrets(
            require_rewards_webhook=True))
        result = await notifier.send_payload_async(
            build_discord_payload(
                days, pnl=pnl, paid=paid, pending=pending,
                resting_hours=resting, elapsed_hours=elapsed, today=today,
                generated_at=now, unbalanced_markets=unbalanced,
                account_realized=account_realized),
            channel="rewards")
        if not result.sent:
            print(f"reward table Discord post failed: {result.reason}",
                  file=sys.stderr)
            return 1
        print("reward table posted to dedicated Discord webhook")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
