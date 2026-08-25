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
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.daily_pnl import CLEAN_FROM, reward_by_day, trading_by_day  # noqa: E402
from liquidity_bot.reward_calendar import NEW_YORK, reward_day  # noqa: E402
from liquidity_bot.resting_time import resting_side_hours_by_day  # noqa: E402


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


def build_discord_payload(
    days: list[date], *, pnl: dict, paid: dict, pending: dict,
    resting_hours: dict[str, float], elapsed_hours: dict[str, float],
    today: date, generated_at: datetime, unbalanced_markets: int,
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
            f"**Trading P&L:** ${float(trade_total):+,.2f}\n"
            f"**Paid rewards:** ${float(paid_total):,.2f}\n"
            f"**Known pending:** ${float(pending_total):,.2f}\n"
            f"**Finalized net:** ${float(complete_net):+,.2f}\n"
            f"**Resting side-hours:** {resting_total:,.2f}"),
        "fields": fields,
        "footer": {"text": (
            f"Generated {stamp} · * current ET day is partial · "
            "-- reward not posted · Pending excluded from net · "
            f"Balanced-leg only; {unbalanced_markets} unbalanced market(s) "
            "and settled exits may be missing")},
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
    paid, pending = await reward_by_day()
    resting, elapsed = resting_side_hours_by_day(
        args.db, days, now=now)
    table = render_table(
        days, pnl=pnl, paid=paid, pending=pending,
        resting_hours=resting, elapsed_hours=elapsed, today=today)
    if not args.quiet:
        print(table)
        print()
        print("Trading is balanced-leg ledger P&L and excludes "
              f"{unbalanced} unbalanced market(s); settled exits can be missing.")
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
                generated_at=now, unbalanced_markets=unbalanced),
            channel="rewards")
        if not result.sent:
            print(f"reward table Discord post failed: {result.reason}",
                  file=sys.stderr)
            return 1
        print("reward table posted to dedicated Discord webhook")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
