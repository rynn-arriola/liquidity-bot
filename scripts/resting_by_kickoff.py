#!/usr/bin/env python3
"""Where does our resting time actually go, relative to kickoff?

FINDINGS ("Reward is concentrated in the hours before kickoff") measured
projected reward by time-to-start across 48 candidates:

    mins to start     n    median reward    share paying > 0
    15-30            11       0.000313            100%
    30-60             5       0.000327            100%
    60-120           10       0.000204             90%
    120+             21       0.000022             67%

That is where reward IS. This measures where we WERE -- resting seconds bucketed
by how far from kickoff they occurred. The two together answer whether slots are
being spent in the window that pays.

Reward accrues per second of resting, so a bucket's share of resting time is
directly comparable to its share of reward density.

    python3 scripts/resting_by_kickoff.py [--db PATH] [--since ISO]

Read-only on the ledger; kickoff times come from the venue, one metadata read
per market, through the shared rate governor. The venue exposes no per-period
earnings breakdown, so realised reward cannot be attributed to a bucket -- this
is resting time, not money.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, shared_api_limiter)
from liquidity_bot.probe import client_from_environment  # noqa: E402

BUCKETS = [(0, 15), (15, 30), (30, 60), (60, 120), (120, 240), (240, 10**6)]


def _parse(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _bucket(minutes: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= minutes < hi:
            return f"{lo}-{hi}" if hi < 10**6 else f"{lo}+"
    return "post-kickoff"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--since", default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)

    placed = {}
    for row in conn.execute(
            "SELECT client_order_id, created_at FROM execution_intents "
            "WHERE operation='PLACE' AND client_order_id IS NOT NULL"):
        at = _parse(row["created_at"])
        key = str(row["client_order_id"])
        if at and (key not in placed or at < placed[key]):
            placed[key] = at
    since = _parse(args.since) or (min(placed.values()) if placed else now)

    working = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
    spans = []          # (slug, start, end)
    for row in conn.execute(
            "SELECT client_order_id, market_slug, status, event_at "
            "FROM execution_orders"):
        start = placed.get(str(row["client_order_id"]))
        if start is None:
            continue
        end = now if str(row["status"]) in working else _parse(row["event_at"])
        if end is None:
            continue
        start, end = max(start, since), min(end, now)
        if end > start:
            spans.append((str(row["market_slug"]), start, end))
    if not spans:
        print("no resting intervals -- is the ledger populated?")
        return 1

    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(),
        default_traffic_class="background")
    kickoff: dict[str, datetime | None] = {}
    for slug in sorted({s for s, _, _ in spans}):
        try:
            kickoff[slug] = (await gateway.fetch_market(slug)).game_start
        except Exception:
            kickoff[slug] = None

    # Split each resting span minute by minute, so a span that straddles
    # buckets is credited to each rather than to whichever end it started in.
    by_bucket: dict[str, float] = defaultdict(float)
    unknown = 0.0
    for slug, start, end in spans:
        start_time = kickoff.get(slug)
        if start_time is None:
            unknown += (end - start).total_seconds()
            continue
        cursor = start
        while cursor < end:
            step = min(cursor + timedelta(minutes=1), end)
            mins = (start_time - cursor).total_seconds() / 60.0
            key = _bucket(mins) if mins >= 0 else "post-kickoff"
            by_bucket[key] += (step - cursor).total_seconds()
            cursor = step

    total = sum(by_bucket.values()) + unknown
    print(f"window from {since.isoformat()}   markets {len(kickoff)}   "
          f"resting {total / 3600:.1f}h\n")
    print(f"{'mins to kickoff':>16} {'resting':>10} {'share':>7}   "
          f"FINDINGS median projected reward")
    reference = {"0-15": "(n=1, not read)", "15-30": "0.000313  100% pay",
                 "30-60": "0.000327  100% pay", "60-120": "0.000204   90% pay",
                 "120-240": "0.000022   67% pay  <- 120+ bucket",
                 "240+": "0.000022   67% pay  <- 120+ bucket",
                 "post-kickoff": "n/a -- should be ~0, we are pregame only"}
    order = ["0-15", "15-30", "30-60", "60-120", "120-240", "240+",
             "post-kickoff"]
    for key in order:
        secs = by_bucket.get(key, 0.0)
        if secs <= 0:
            continue
        print(f"{key:>16} {secs / 3600:9.2f}h {100 * secs / total:6.1f}%   "
              f"{reference.get(key, '')}")
    if unknown:
        print(f"{'unknown kickoff':>16} {unknown / 3600:9.2f}h "
              f"{100 * unknown / total:6.1f}%")
    paying = sum(by_bucket.get(k, 0.0) for k in ("0-15", "15-30", "30-60", "60-120"))
    print(f"\nresting inside the 0-120 min window that pays: "
          f"{100 * paying / total:.1f}% of all resting time")
    print("Reward accrues per second of resting, so this share is directly "
          "comparable to the reward densities above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
