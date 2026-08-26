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
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.daily_pnl import CLEAN_FROM, reward_by_day, trading_by_day  # noqa: E402
from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway  # noqa: E402
from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway  # noqa: E402
from liquidity_bot.errors import AdapterError  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402
from liquidity_bot.reward_calendar import NEW_YORK, reward_day  # noqa: E402
from liquidity_bot.resting_time import resting_side_hours_by_day  # noqa: E402


ACCOUNT_ACTIVITY_MAX_PAGES = 4
ACCOUNT_REPORT_RATE_PER_SECOND = 1.0
BASELINE_START_TOLERANCE = timedelta(minutes=2)
CASH_ACTIVITY_TYPES = (
    "ACTIVITY_TYPE_ACCOUNT_DEPOSIT",
    "ACTIVITY_TYPE_ACCOUNT_ADVANCED_DEPOSIT",
    "ACTIVITY_TYPE_ACCOUNT_WITHDRAWAL",
    "ACTIVITY_TYPE_REFERRAL_BONUS",
    "ACTIVITY_TYPE_TRANSFER",
)
# The first ET-keyed row copied the old UTC baseline. This one boundary was
# positively reconstructed while the account was flat at 04:00Z and a bounded
# activity walk proved no transfer crossed it. Never apply the correction to a
# database whose stored legacy value differs from the value that was audited.
LEGACY_ET_OPENING_CASH = {
    "2026-08-25": (Decimal("478.6998"), Decimal("466.0899")),
}


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


def _first_timestamp(*sources: Any) -> datetime:
    keys = (
        "updateTime", "createTime", "updatedAt", "createdAt",
        "update_time", "create_time", "updated_at", "created_at",
        "timestamp", "time", "date", "settledAt",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if source.get(key) not in (None, ""):
                return _timestamp(source[key], "activity")
    raise AccountMetricUnavailable("cash activity has no timestamp")


def _amount(raw: Any) -> Decimal:
    if isinstance(raw, dict):
        currency = str(raw.get("currency") or "USD").upper()
        if currency != "USD":
            raise AccountMetricUnavailable(
                f"cash activity currency is {currency}, not USD")
        raw = raw.get("value")
    return _decimal(raw, "cash activity amount")


def _completed(status: Any) -> bool:
    clean = str(status or "").upper()
    return ((clean == "COMPLETED" or clean.endswith("_COMPLETED"))
            and not clean.endswith("NOT_COMPLETED"))


def _cash_change_sign(activity_type: str, description: str,
                      amount: Decimal) -> Decimal:
    if amount < 0:
        return amount
    if activity_type == "ACTIVITY_TYPE_ACCOUNT_WITHDRAWAL":
        return -amount
    if activity_type in {
            "ACTIVITY_TYPE_ACCOUNT_DEPOSIT",
            "ACTIVITY_TYPE_ACCOUNT_ADVANCED_DEPOSIT",
            "ACTIVITY_TYPE_REFERRAL_BONUS"}:
        return amount
    text = description.lower()
    if "withdraw" in text:
        return -amount
    if any(token in text for token in (
            "reward", "deposit", "apple pay", "credit", "bonus", "referral")):
        return amount
    raise AccountMetricUnavailable(
        f"unclassified completed cash transfer: {description or '(blank)'}")


def _cash_rows(activity: dict[str, Any]) -> Iterable[tuple[dict, dict]]:
    change = activity.get("accountBalanceChange")
    if not isinstance(change, dict):
        raise AccountMetricUnavailable(
            "cash activity has no accountBalanceChange")
    # The balance change is the economic event. Its transaction list contains
    # payment attempts and may grow; summing both levels can count one deposit
    # more than once. Current production rows carry the authoritative amount,
    # status and timestamp at this top level. Use transactions only as a
    # compatibility fallback when those economic fields are absent.
    if change.get("amount") is not None and change.get("status") is not None:
        yield change, change
        return
    transactions = change.get("transactions")
    if isinstance(transactions, list) and transactions:
        for transaction in transactions:
            if not isinstance(transaction, dict):
                raise AccountMetricUnavailable(
                    "cash activity transaction is not an object")
            yield change, transaction
    else:
        yield change, change


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
    if source == "legacy_utc_cutover":
        correction = LEGACY_ET_OPENING_CASH.get(day_value.isoformat())
        if correction is None or stored != correction[0]:
            raise AccountMetricUnavailable(
                "legacy UTC baseline has no proven ET correction")
        return correction[1]
    if source != "fresh_et_day":
        raise AccountMetricUnavailable(f"unknown ET baseline source: {source}")
    created_at = _timestamp(row["created_at"], "opening baseline")
    delta = created_at - boundary.astimezone(timezone.utc)
    if delta < timedelta(0) or delta > BASELINE_START_TOLERANCE:
        raise AccountMetricUnavailable(
            "ET baseline was not captured at the day boundary")
    return stored


async def _nontrading_cash_today(
    gateway: AsyncReadOnlyGateway, *, boundary: datetime, through: datetime,
) -> Decimal:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    net = Decimal(0)
    for _ in range(ACCOUNT_ACTIVITY_MAX_PAGES):
        params: dict[str, Any] = {
            "limit": 100,
            "types": list(CASH_ACTIVITY_TYPES),
            "sortOrder": "SORT_ORDER_DESCENDING",
        }
        if cursor:
            params["cursor"] = cursor
        response = await gateway._call(
            "GET /v1/portfolio/activities (cash only)",
            lambda params=params: gateway.client.portfolio.activities(params))
        if not isinstance(response, dict) or not isinstance(
                response.get("activities"), list):
            raise AccountMetricUnavailable(
                "cash-activity response has no activities list")
        for activity in response["activities"]:
            if not isinstance(activity, dict):
                raise AccountMetricUnavailable("cash activity is not an object")
            activity_type = str(activity.get("type") or "").upper()
            if activity_type not in CASH_ACTIVITY_TYPES:
                raise AccountMetricUnavailable(
                    f"unexpected cash activity type: {activity_type or '(blank)'}")
            for parent, row in _cash_rows(activity):
                status = row.get("status", parent.get("status"))
                if status in (None, ""):
                    raise AccountMetricUnavailable(
                        "cash activity status is missing")
                if not _completed(status):
                    continue
                row_at = _first_timestamp(row, parent, activity)
                if row_at > through:
                    raise AccountMetricUnavailable(
                        "completed cash activity changed after the account snapshot")
                if row_at < boundary:
                    continue
                amount = _amount(row.get("amount", parent.get("amount")))
                description = str(
                    row.get("description", parent.get("description", "")))
                net += _cash_change_sign(activity_type, description, amount)
        eof = response.get("eof")
        # The endpoint sorts balance changes by createTime, but settled cash
        # moves at completion/updateTime. An old-created deposit can therefore
        # complete later than a newer row. Only explicit EOF proves the daily
        # completion-time window; never stop merely because createTime crossed
        # the ET boundary.
        if eof is True:
            return net
        if eof is not False:
            raise AccountMetricUnavailable(
                "cash-activity pagination has no explicit eof")
        next_cursor = response.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor.strip():
            raise AccountMetricUnavailable(
                "cash-activity pagination ended without eof")
        if next_cursor in seen_cursors:
            raise AccountMetricUnavailable(
                "cash-activity pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise AccountMetricUnavailable(
        "cash-activity scan did not reach the ET boundary")


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


def render_table(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date, account_realized: AccountRealizedToday | None = None,
) -> str:
    lines = [
        "| Date (ET) | Pair estimate | Paid reward | Pending reward | "
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
        "Account realized is account-wide settled-cash change, less completed "
        "non-trading cash changes, plus current open-position basis.",
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
        f"{'Date ET':<11}  {'Pair est.':>9}  {'Paid':>8}  "
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
        trade = pnl.get(key)
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

        trade_text = (f"${float(trade):+,.2f}"
                      if trade is not None else "n/a")
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
            "Account realized is account-wide cash change - transfers + open "
            "basis · Historical rows are balanced-pair estimates; "
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
