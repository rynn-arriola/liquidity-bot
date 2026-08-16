#!/usr/bin/env python3
"""How much of the time is each side actually holding a live order?

Reward is paid for RESTING, not for placing, so the count of markets quoting at
an instant understates or overstates coverage depending on when you look. This
measures the fraction of wall-clock time each (market, side) held a live order,
which is the quantity that maps to reward.

Read-only: opens the ledger with `mode=ro` so it is safe against a live runner.

    python3 scripts/resting_fraction.py [--db PATH] [--since ISO] [--top N]

An order's resting window is taken as the PLACE intent's `created_at` through
the order's terminal `event_at`. Orders still working are counted through
"now". Windows are clipped to the reporting window, so a session that began
mid-window is not credited for time before it.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--since", default=None,
                        help="ISO start; defaults to the earliest PLACE intent")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)

    # When each order started resting: its PLACE intent.
    placed: dict[str, datetime] = {}
    for row in conn.execute(
            "SELECT client_order_id, created_at FROM execution_intents "
            "WHERE operation='PLACE' AND client_order_id IS NOT NULL"):
        at = _parse(row["created_at"])
        if at is None:
            continue
        key = str(row["client_order_id"])
        # Earliest wins: an intent replayed later must not shorten the window.
        if key not in placed or at < placed[key]:
            placed[key] = at

    since = _parse(args.since) or (min(placed.values()) if placed else now)
    window = max((now - since).total_seconds(), 1.0)

    working = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
    resting: dict[tuple[str, str], float] = defaultdict(float)
    orders: dict[tuple[str, str], int] = defaultdict(int)

    for row in conn.execute(
            "SELECT client_order_id, market_slug, side, status, event_at "
            "FROM execution_orders"):
        start = placed.get(str(row["client_order_id"]))
        if start is None:
            continue  # not ours, or placed before this ledger
        end = now if str(row["status"]) in working else _parse(row["event_at"])
        if end is None:
            continue
        start = max(start, since)
        end = min(end, now)
        if end <= start:
            continue
        side = "bid" if "BUY" in str(row["side"]).upper() else "offer"
        key = (str(row["market_slug"]), side)
        resting[key] += (end - start).total_seconds()
        orders[key] += 1

    if not resting:
        print("no resting intervals in window -- is the ledger populated?")
        return 1

    by_market: dict[str, float] = defaultdict(float)
    for (slug, _side), seconds in resting.items():
        by_market[slug] += seconds

    print(f"window: {since.isoformat()} -> {now.isoformat()}  "
          f"({window / 60:.1f} min)")
    print(f"markets seen: {len(by_market)}   sides tracked: {len(resting)}\n")
    print(f"{'resting%':>9} {'orders':>7}  market / side")
    rows = sorted(resting.items(), key=lambda kv: -kv[1])
    for (slug, side), seconds in rows[:args.top]:
        pct = 100.0 * seconds / window
        print(f"{pct:8.1f}% {orders[(slug, side)]:7d}  {slug[:44]:44s} {side}")

    total = sum(resting.values())
    sides = len(resting)
    print(f"\nmean resting fraction per side: "
          f"{100.0 * total / (window * sides):.1f}%  ({sides} sides)")
    print("A side at 100% held an order for the whole window; reward accrues "
          "only over that time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
