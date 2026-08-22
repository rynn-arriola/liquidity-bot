#!/usr/bin/env python3
"""Where trading loss actually lands, bucketed by how long a pair took.

Ledger only -- no venue calls, safe to run beside the live runner as often as
you like. Answers "is the loss in fast pairs or slow ones?", which decides
whether the lever is the inventory clock, the exit cost cap, or selection.

Two corrections are baked in, both of which produced wrong answers first:

**1. Clean data only.** The `isAggressor` fix reached production at
2026-08-17T13:20Z. Before it roughly 60% of fills were never recorded and the
missing ones were overwhelmingly the passive CLOSING leg, so pairs appeared to
take hours because their completion was simply absent from the ledger. Reading
across that boundary made a ledger defect look like a trading problem: the
>1h bucket read 66.7% on 08-16 and 1.9% on 08-18.

**2. Balanced legs only.** Allocating every fill's cost to the matched pairs
inflates any market whose legs do not balance -- it turned `accounting.py`'s
-47.24 into -135.92 for the same period. A market with 10 long and 5 short has
5 pairs but 15 contracts of cost, and charging all of it to the pairs is simply
wrong. Markets where `long_qty != short_qty` are excluded and counted.

Every fill this bot sends is a PURCHASE (FINDINGS 6i): BUY_LONG pays `price`,
BUY_SHORT pays `1 - price` for the opposite outcome. A matched YES+NO pair
settles at exactly 1.0000, so a completed pair realises `1.00 - paid` with no
direction, outcome or settlement data needed.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

# Fills before this are unusable: see the module docstring.
CLEAN_FROM = "2026-08-18"

BUCKETS = ((0, 300), (300, 900), (900, 1800), (1800, 3600), (3600, 10 ** 9))


def label(lo: int, hi: int) -> str:
    return f"{lo}-{hi}s" if hi < 10 ** 9 else f"{lo}s+"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--since", default=CLEAN_FROM,
                        help=f"ISO date floor; defaults to {CLEAN_FROM}")
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    markets: dict[str, dict] = defaultdict(
        lambda: {"lq": Decimal(0), "sq": Decimal(0), "paid": Decimal(0),
                 "first": None, "last": None, "day": None})
    for row in connection.execute(
            "SELECT market_slug, intent, qty, price, received_at "
            "FROM execution_fills WHERE intent IS NOT NULL AND received_at>=? "
            "ORDER BY received_at", (args.since,)):
        qty, price = Decimal(str(row["qty"])), Decimal(str(row["price"]))
        cell = markets[row["market_slug"]]
        if "LONG" in row["intent"]:
            cell["lq"] += qty
            cell["paid"] += qty * price
        else:
            cell["sq"] += qty
            cell["paid"] += qty * (Decimal(1) - price)
        if cell["first"] is None:
            cell["first"] = row["received_at"]
            cell["day"] = row["received_at"][:10]
        cell["last"] = row["received_at"]

    agg = {b: {"n": 0, "pnl": Decimal(0), "pairs": Decimal(0)} for b in BUCKETS}
    by_day: dict[str, dict] = defaultdict(
        lambda: {"long": 0, "total": 0, "pnl": Decimal(0), "pairs": Decimal(0)})
    unbalanced = 0

    for cell in markets.values():
        pairs = min(cell["lq"], cell["sq"])
        if pairs <= 0 or cell["lq"] != cell["sq"]:
            unbalanced += 1
            continue
        seconds = (datetime.fromisoformat(cell["last"])
                   - datetime.fromisoformat(cell["first"])).total_seconds()
        pnl = pairs - cell["paid"]
        day = by_day[cell["day"]]
        day["total"] += 1
        day["pnl"] += pnl
        day["pairs"] += pairs
        if seconds >= 3600:
            day["long"] += 1
        for lo, hi in BUCKETS:
            if lo <= seconds < hi:
                bucket = agg[(lo, hi)]
                bucket["n"] += 1
                bucket["pnl"] += pnl
                bucket["pairs"] += pairs
                break

    print(f"pair P&L by completion time   (fills from {args.since}, "
          "balanced-leg markets only)")
    print()
    print(f"{'bucket':>12} {'markets':>8} {'pairs':>8} {'total':>10} "
          f"{'per pair':>10} {'share':>7}")
    total = sum((b["pnl"] for b in agg.values()), Decimal(0))
    for lo, hi in BUCKETS:
        bucket = agg[(lo, hi)]
        if not bucket["n"]:
            continue
        per = bucket["pnl"] / bucket["pairs"] if bucket["pairs"] else Decimal(0)
        share = (100.0 * float(bucket["pnl"]) / float(total)) if total else 0.0
        print(f"  {label(lo, hi):>10} {bucket['n']:>8} "
              f"{float(bucket['pairs']):>8.0f} {float(bucket['pnl']):>10.2f} "
              f"{float(per):>10.4f} {share:6.1f}%")
    print(f"\n  total {float(total):.2f}"
          f"   (excluded {unbalanced} market(s) with unbalanced legs)")

    print()
    print("per UTC day")
    print(f"{'day':12} {'markets':>8} {'pairs':>7} {'P&L':>9} {'per pair':>10} "
          f"{'>1h share':>10}")
    for day in sorted(by_day):
        cell = by_day[day]
        per = cell["pnl"] / cell["pairs"] if cell["pairs"] else Decimal(0)
        share = (100.0 * cell["long"] / cell["total"]) if cell["total"] else 0.0
        print(f"{day:12} {cell['total']:>8} {float(cell['pairs']):>7.0f} "
              f"{float(cell['pnl']):>9.2f} {float(per):>10.4f} {share:9.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
