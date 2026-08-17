#!/usr/bin/env python3
"""What the venue says we have earned: by day, by market, by league.

Read-only. Safe to run against a live runner -- it touches the venue's earnings
endpoint and never the ledger or any order path.

    .venv/bin/python scripts/rewards_report.py            # everything
    .venv/bin/python scripts/rewards_report.py --days 7   # recent days only
    .venv/bin/python scripts/rewards_report.py --top 25   # more markets

**Query account-wide.** Passing a market slug to `/v1/incentives/earnings`
returns $0 even for markets that plainly earned; only the unfiltered query is
truthful. Each row carries `date`, `market_slug`, `program_type`, `reward` and
`status`, so daily and per-market attribution work -- but there is no
time-to-kickoff field, so reward cannot be attributed to a point in a market's
life.

**A missing day means one of two things**, and they are not distinguishable from
a single reading: the venue has not posted it yet, or nothing was earned.
2026-08-10 settled to PAID while 08-11 onward sat PENDING, so posting lags by
days -- while 2026-08-13 is a clean gap between two populated days and is
genuinely zero. Re-run a day later to tell them apart.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.adapters import AsyncReadOnlyGateway  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402


def _league(slug: str) -> str:
    """`aec-setkamemd-a-b-2026-08-17` -> `setkamemd`."""
    parts = slug.split("-")
    return parts[1] if len(parts) > 1 else slug


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=0,
                        help="only days within this many days of today")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--gap-days", type=int, default=10,
                        help="how far back to report missing days")
    args = parser.parse_args()

    gateway = AsyncReadOnlyGateway(client_from_environment())
    rows = await gateway.fetch_earnings()
    if not rows:
        print("no earnings rows returned -- check credentials")
        return 1

    cutoff = None
    if args.days > 0:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=args.days)).strftime("%Y-%m-%d")

    by_date: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_market: dict[str, float] = defaultdict(float)
    market_status: dict[str, set[str]] = defaultdict(set)
    by_league: dict[str, float] = defaultdict(float)
    per_day_rows: dict[str, int] = defaultdict(int)
    status_total: dict[str, float] = defaultdict(float)

    for row in rows:
        date = str(getattr(row, "date", "?"))[:10]
        if cutoff and date < cutoff:
            continue
        status = str(getattr(row, "status", "?"))
        reward = float(getattr(row, "reward", 0) or 0)
        slug = str(getattr(row, "market_slug", "?"))
        by_date[date][status] += reward
        per_day_rows[date] += 1
        status_total[status] += reward
        if reward:
            by_market[slug] += reward
            market_status[slug].add(status)
            by_league[_league(slug)] += reward

    print(f"{'date':<12} {'PAID':>9} {'PENDING':>9} {'SKIPPED':>9} "
          f"{'total':>9} {'rows':>5}")
    for date in sorted(by_date):
        day = by_date[date]
        print(f"{date:<12} {day.get('PAID', 0):9.2f} {day.get('PENDING', 0):9.2f} "
              f"{day.get('SKIPPED', 0):9.2f} {sum(day.values()):9.2f} "
              f"{per_day_rows[date]:5d}")
    grand = sum(status_total.values())
    print(f"{'TOTAL':<12} {status_total.get('PAID', 0):9.2f} "
          f"{status_total.get('PENDING', 0):9.2f} "
          f"{status_total.get('SKIPPED', 0):9.2f} {grand:9.2f} "
          f"{sum(per_day_rows.values()):5d}")

    # Gaps are the point of the daily view, so name them rather than leaving a
    # hole in the table for the reader to notice. Bounded to a recent window:
    # this account has occasional single rows going back months, and walking
    # from the first of those produced a 46-date list of days the bot was not
    # even running, which buries the two or three that matter.
    if by_date:
        now = datetime.now(timezone.utc)
        horizon = now - timedelta(days=args.gap_days)
        cursor = max(datetime.strptime(sorted(by_date)[0], "%Y-%m-%d")
                     .replace(tzinfo=timezone.utc), horizon)
        missing = []
        while cursor.date() <= now.date():
            key = cursor.strftime("%Y-%m-%d")
            if key not in by_date:
                missing.append(
                    f"{key} (today)" if cursor.date() == now.date() else key)
            cursor += timedelta(days=1)
        if missing:
            print(f"\nno rows in the last {args.gap_days} days for: "
                  f"{', '.join(missing)}")
            print("  either not posted yet or nothing earned -- re-run "
                  "tomorrow to tell them apart")

    print(f"\ntop {args.top} markets by reward:")
    for slug, amount in sorted(by_market.items(), key=lambda kv: -kv[1])[:args.top]:
        status = "/".join(sorted(market_status[slug]))
        print(f"   ${amount:8.4f}  {status:<14} {slug[:48]}")

    print("\nby league:")
    for league, amount in sorted(by_league.items(), key=lambda kv: -kv[1]):
        share = 100.0 * amount / grand if grand else 0.0
        print(f"   ${amount:8.2f}  {share:5.1f}%  {league}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
