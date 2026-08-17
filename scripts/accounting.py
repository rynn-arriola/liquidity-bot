#!/usr/bin/env python3
"""Accounting that reconciles against the account, not against itself.

The ledger's `realized_pnl` reported -$143.59 on 2026-08-17 when the account had
actually moved -$48.69 -- wrong by 3x, in the number the loss stops read.

This computes P&L two ways and makes them agree, or says loudly that they do not.

  1. **Closed pairs**, from the local ledger, priced with no ambiguity. Every
     fill the bot sends is a PURCHASE (FINDINGS 6i -- two intents, never four),
     so one YES plus one NO settles at exactly 1.0000 and a matched pair
     realises `1.00 - paid`. No direction, no outcome, no settlement data.

  2. **The account**, from marked equity. Ground truth.

        equity_change = -(paid for trades) + settlements + rewards_paid

     so settlements are derivable exactly, without knowing which side won.

**Deliberately cheap against the venue: two calls, balance and earnings.**
Everything else comes from the ledger. Earlier versions of this walked the
venue's trade history and triggered rate limiting -- the analysis shares one
4 req/s governor with the live runner, so a report that pages history is
competing with the bot for the budget it needs to quote.

That means this inherits the ledger's blind spot: fills the runner could not
tie to one of its own orders are missing. The reconciliation is what EXPOSES
that -- if derived settlements look implausible, unrecorded fills are the
reason. Fixing the ingestion is runner work, not report work.

Per-market settlement outcomes are not recoverable from history: when several
positions settle in one window the balance gives one number and the venue
publishes no settlement rows at all. Going forward they are, which is why each
run records a dated snapshot -- consecutive snapshots bound each window.

    python3 scripts/accounting.py                # report and snapshot
    python3 scripts/accounting.py --no-snapshot  # report only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway  # noqa: E402
from liquidity_bot.adapters import AsyncReadOnlyGateway  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402

BUY_LONG = "ORDER_INTENT_BUY_LONG"
BUY_SHORT = "ORDER_INTENT_BUY_SHORT"


def _money(value) -> Decimal:
    if isinstance(value, dict):
        value = value.get("value", 0)
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _from_ledger(db: str):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    per_market = defaultdict(
        lambda: {"L_q": Decimal(0), "L_c": Decimal(0),
                 "S_q": Decimal(0), "S_c": Decimal(0)})
    spent = fees = Decimal(0)
    inverted = 0
    for row in conn.execute("SELECT market_slug,intent,price,qty,fee "
                            "FROM execution_fills"):
        qty, price = Decimal(str(row["qty"])), Decimal(str(row["price"]))
        spent += qty * price
        fees += Decimal(str(row["fee"] or 0))
        book = per_market[row["market_slug"]]
        if row["intent"] == BUY_LONG:
            book["L_q"] += qty
            book["L_c"] += qty * price
        elif row["intent"] == BUY_SHORT:
            book["S_q"] += qty
            book["S_c"] += qty * price
        else:
            # SELL_* is the frame the venue inverts and that was never
            # validated here. Counted, never priced.
            inverted += 1
    conn.close()
    return per_market, spent, fees, inverted


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--snapshots", default="data/accounting_snapshots.json")
    parser.add_argument("--no-snapshot", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    per_market, spent, fees, inverted = _from_ledger(args.db)

    closed_pnl = Decimal(0)
    closed = open_ = 0
    open_cost = Decimal(0)
    for book in per_market.values():
        pairs = min(book["L_q"], book["S_q"])
        if pairs > 0:
            lc = book["L_c"] / book["L_q"] * pairs if book["L_q"] else Decimal(0)
            sc = book["S_c"] / book["S_q"] * pairs if book["S_q"] else Decimal(0)
            closed_pnl += pairs - lc - sc
            closed += 1
        if book["L_q"] != book["S_q"]:
            open_ += 1
            long_side = book["L_q"] > book["S_q"]
            whole = book["L_q"] if long_side else book["S_q"]
            cost = book["L_c"] if long_side else book["S_c"]
            if whole:
                open_cost += cost / whole * abs(book["L_q"] - book["S_q"])

    gateway = AsyncReadOnlyGateway(client_from_environment())
    account = AsyncAccountSnapshotGateway(gateway)
    balance = await account.fetch_balance(observed_at=now)          # call 1
    equity = _money(getattr(balance, "marked_equity", 0))
    earnings = await gateway.fetch_earnings()                        # call 2
    paid = sum(Decimal(str(getattr(r, "reward", 0) or 0))
               for r in earnings if str(getattr(r, "status", "")) == "PAID")
    pending = sum(Decimal(str(getattr(r, "reward", 0) or 0))
                  for r in earnings if str(getattr(r, "status", "")) == "PENDING")

    print(f"ledger fills priced      : {closed + open_:5d} markets")
    if inverted:
        print(f"  fills in the inverted SELL_* frame, excluded: {inverted}")
    print(f"paid for all fills       : {float(spent):12.2f}")
    print(f"fees                     : {float(fees):+12.2f}   (negative = rebate)")
    print(f"closed pairs             : {closed:5d} markets  "
          f"P&L {float(closed_pnl):+.2f}")
    print(f"unmatched (rode/open)    : {open_:5d} markets  "
          f"cost {float(open_cost):.2f}")
    print(f"\nreward   PAID {float(paid):8.2f}   PENDING {float(pending):8.2f}")
    print(f"account marked equity    : {float(equity):12.2f}")

    path = Path(args.snapshots)
    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text())
        except Exception:
            history = []
    if history:
        last = history[-1]
        d_equity = equity - Decimal(last["equity"])
        d_spent = spent - Decimal(last["spent"])
        d_paid = paid - Decimal(last["reward_paid"])
        settlements = d_equity + d_spent - d_paid
        d_closed = closed_pnl - Decimal(last["closed_pnl"])
        print(f"\n--- since {last['at']} ---")
        print(f"  equity moved           : {float(d_equity):+12.2f}")
        print(f"  paid for fills         : {float(d_spent):12.2f}")
        print(f"  reward actually paid   : {float(d_paid):+12.2f}")
        print(f"  closed-pair P&L        : {float(d_closed):+12.2f}")
        print(f"  => settlements received: {float(settlements):+12.2f}"
              "   (derived; exact at account level)")
        print("\n  If settlements look impossible -- larger than the inventory "
              "that could\n  have settled, or negative -- the ledger is missing "
              "fills. That is the\n  signal this report exists to give.")
    else:
        print("\nno previous snapshot -- this run sets the baseline. "
              "Run it again later and settlements become exactly derivable.")

    if not args.no_snapshot:
        history.append({"at": now.isoformat(), "equity": str(equity),
                        "spent": str(spent), "fees": str(fees),
                        "reward_paid": str(paid), "reward_pending": str(pending),
                        "closed_pnl": str(closed_pnl)})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=1))
        print(f"\nsnapshot recorded ({len(history)} total) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
