#!/usr/bin/env python3
"""How much of the time is each side actually holding a live order?

Reward is paid for RESTING, not for placing, so the count of markets quoting at
an instant understates or overstates coverage depending on when you look. This
measures the fraction of time each (market, side) held a live order.

Read-only: opens the ledger with `mode=ro` so it is safe against a live runner.

    python3 scripts/resting_fraction.py [--db PATH] [--since ISO] [--top N]
                                        [--coverage 0.8]

**Two denominators, and the difference matters.** Markets rotate as games kick
off -- a 3.7h window saw 27 markets pass through 20 slots -- so a market bound
for ten minutes can never rest for more than 4% of the window however well it
quoted. Dividing by the window therefore measures rotation, not performance:
the first version of this script reported a 30% mean that was almost entirely
that artifact.

So each side is reported against its own market's active span (first placement
to last terminal, or now), and the headline mean covers only sides whose market
was present for most of the window -- those are the ones whose number reflects
how well it quoted rather than how long it was bound.

An order's resting window is the PLACE intent's `created_at` through the order's
terminal `event_at`; orders still working count through now.
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
    parser.add_argument("--coverage", type=float, default=0.8,
                        help="a market must be active this fraction of the "
                             "window to count toward the headline mean")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)

    placed: dict[str, datetime] = {}
    for row in conn.execute(
            "SELECT client_order_id, created_at FROM execution_intents "
            "WHERE operation='PLACE' AND client_order_id IS NOT NULL"):
        at = _parse(row["created_at"])
        if at is None:
            continue
        key = str(row["client_order_id"])
        if key not in placed or at < placed[key]:
            placed[key] = at

    since = _parse(args.since) or (min(placed.values()) if placed else now)
    window = max((now - since).total_seconds(), 1.0)

    working = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
    resting: dict[tuple[str, str], float] = defaultdict(float)
    orders: dict[tuple[str, str], int] = defaultdict(int)
    # A market's active span: first placement to last terminal (or now while it
    # still holds something). This is the denominator that isolates how well a
    # market quoted from how long it happened to be bound.
    first_at: dict[str, datetime] = {}
    last_at: dict[str, datetime] = {}

    for row in conn.execute(
            "SELECT client_order_id, market_slug, side, status, event_at "
            "FROM execution_orders"):
        start = placed.get(str(row["client_order_id"]))
        if start is None:
            continue
        live = str(row["status"]) in working
        end = now if live else _parse(row["event_at"])
        if end is None:
            continue
        start = max(start, since)
        end = min(end, now)
        if end <= start:
            continue
        slug = str(row["market_slug"])
        side = "bid" if "BUY" in str(row["side"]).upper() else "offer"
        resting[(slug, side)] += (end - start).total_seconds()
        orders[(slug, side)] += 1
        if slug not in first_at or start < first_at[slug]:
            first_at[slug] = start
        if slug not in last_at or end > last_at[slug]:
            last_at[slug] = end

    if not resting:
        print("no resting intervals in window -- is the ledger populated?")
        return 1

    def span_of(slug: str) -> float:
        return max((last_at[slug] - first_at[slug]).total_seconds(), 1.0)

    print(f"window: {since.isoformat()} -> {now.isoformat()}  "
          f"({window / 60:.1f} min)")
    print(f"markets seen: {len(first_at)}   sides tracked: {len(resting)}\n")
    print(f"{'of span':>8} {'of window':>10} {'span':>7} {'orders':>7}  market / side")

    # Only sides whose market was around long enough to judge. A market that
    # placed once and ended three minutes later scores 100% of its span, which
    # is true and useless -- it would otherwise sort to the top and bury the
    # markets the number is actually about.
    rows = sorted((kv for kv in resting.items()
                   if span_of(kv[0][0]) >= args.coverage * window),
                  key=lambda kv: -kv[1] / span_of(kv[0][0]))
    for (slug, side), seconds in rows[:args.top]:
        span = span_of(slug)
        print(f"{100.0 * seconds / span:7.1f}% {100.0 * seconds / window:9.1f}% "
              f"{span / 60:6.0f}m {orders[(slug, side)]:7d}  {slug[:40]:40s} {side}")

    full = [(k, v) for k, v in resting.items()
            if span_of(k[0]) >= args.coverage * window]
    short = len(resting) - len(full)
    if full:
        mean = sum(v / span_of(k[0]) for k, v in full) / len(full)
        print(f"\nmean resting fraction, markets present >={args.coverage:.0%} "
              f"of the window: {100.0 * mean:.1f}%  ({len(full)} sides)")
    print(f"excluded {short} side(s) whose market was bound too briefly to "
          f"judge -- rotation, not performance")
    print("Reward accrues only while an order rests. 'of span' is the honest "
          "number; 'of window' is what a naive count would report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
