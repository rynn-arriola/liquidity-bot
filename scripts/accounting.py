#!/usr/bin/env python3
"""Legacy ledger/account diagnostics, not the daily trading-P&L authority.

Use reward_table.py for account-marked daily trading with non-trading cash
removed. This command does not classify deposits/withdrawals or reconcile the
change in open-position marks. Its unexplained remainder is neither trading
profit/loss nor proven settlement proceeds.

The ledger's `realized_pnl` reported -$143.59 on 2026-08-17 when the account had
actually moved -$48.69 -- wrong by 3x, in the number the loss stops read.

Three sources, made to agree:

  1. **Closed positions**, from the local ledger, priced with no ambiguity.
     One YES plus one NO settles at exactly 1.0000, so a matched pair realises
     `1.00 - paid` with no direction, outcome or settlement data needed.

     The bot itself only ever sends the two BUY intents (FINDINGS 6i -- two
     intents, never four), but an operator closing a position by hand produces
     a SELL_* fill, so quantities and costs here are NET. A market that was
     bought and then sold back out lands at a residual of zero and is realised
     by the same expression as a matched pair.

  2. **Settled positions** (`--settlements`). The venue publishes no settlement
     rows, but it does publish the answer: a resolved market carries `outcomes`
     and `outcomePrices`, and the winner prices at exactly 1. Our fills record
     `outcome` -- the side we hold -- so payout is `qty` if ours won, else 0.
     One venue call per market, hence opt-in.

  3. **The account**, from bracketed marked equity. Changes can include
     deposits, withdrawals, reward credits, fees and open-position marks.

Account snapshots use several bounded reads; other calculations are local. An
earlier version paged the venue's trade history and triggered rate limiting --
analysis shares one 4 req/s governor with the live runner, so a report that
walks history competes with the bot for the budget it needs to quote.

    python3 scripts/accounting.py                    # report and snapshot
    python3 scripts/accounting.py --settlements      # + price settled positions
    python3 scripts/accounting.py --no-snapshot      # report only
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
from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, shared_api_limiter)
from liquidity_bot.pnl import (  # noqa: E402
    economic_contract_cost,
    economic_sale_proceeds,
)
from liquidity_bot.probe import client_from_environment  # noqa: E402
from liquidity_bot.sqlite_compat import active_fills_predicate  # noqa: E402

BUY_LONG = "ORDER_INTENT_BUY_LONG"
BUY_SHORT = "ORDER_INTENT_BUY_SHORT"
SELL_LONG = "ORDER_INTENT_SELL_LONG"
SELL_SHORT = "ORDER_INTENT_SELL_SHORT"


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
        lambda: {"L_q": Decimal(0), "L_c": Decimal(0), "L_out": None,
                 "S_q": Decimal(0), "S_c": Decimal(0), "S_out": None})
    spent = fees = Decimal(0)
    unknown_intent = 0
    active_fills = active_fills_predicate(conn)
    for row in conn.execute(
            "SELECT market_slug,intent,price,qty,fee,outcome "
            f"FROM execution_fills WHERE {active_fills}"):
        qty, price = Decimal(str(row["qty"])), Decimal(str(row["price"]))
        fees += Decimal(str(row["fee"] or 0))
        book = per_market[row["market_slug"]]
        # Quantities and costs are NET. Stored prices use the LONG/YES frame:
        # BUY_LONG costs `price`, while BUY_SHORT costs `1 - price` for the
        # opposite outcome.
        #
        # An operator closing a position by hand breaks that: it lands as a
        # SELL_* fill, and 2026-08-17 produced the first one this script ever
        # saw. Treating it as a purchase was wrong twice over -- it added the
        # sale proceeds to "paid", and it left the position looking open when
        # the account was flat. Verified against the balance: 5 contracts sold
        # at 0.50 with an 0.08 fee moved the account +2.42, cash IN.
        if row["intent"] == BUY_LONG:
            cost = qty * economic_contract_cost(row["intent"], price)
            book["L_q"] += qty
            book["L_c"] += cost
            book["L_out"] = book["L_out"] or row["outcome"]
            spent += cost
        elif row["intent"] == BUY_SHORT:
            cost = qty * economic_contract_cost(row["intent"], price)
            book["S_q"] += qty
            book["S_c"] += cost
            book["S_out"] = book["S_out"] or row["outcome"]
            spent += cost
        elif row["intent"] == SELL_LONG:
            proceeds = qty * economic_sale_proceeds(row["intent"], price)
            book["L_q"] -= qty
            book["L_c"] -= proceeds
            spent -= proceeds
        elif row["intent"] == SELL_SHORT:
            proceeds = qty * economic_sale_proceeds(row["intent"], price)
            book["S_q"] -= qty
            book["S_c"] -= proceeds
            spent -= proceeds
        else:
            unknown_intent += 1
    conn.close()
    return per_market, spent, fees, unknown_intent


def _residual(book):
    """The unmatched leg: what we still held after pairing off."""
    residual = book["L_q"] - book["S_q"]
    if residual == 0:
        return None
    long_side = residual > 0
    qty = abs(residual)
    whole = book["L_q"] if long_side else book["S_q"]
    cost = book["L_c"] if long_side else book["S_c"]
    held = book["L_out"] if long_side else book["S_out"]
    paid = cost / whole * qty if whole else Decimal(0)
    return qty, paid, held


async def _price_settlements(gateway, per_market, cache_path: Path):
    """Price each unmatched position against its market's published resolution.

    A resolution is final, so it is cached on disk: only markets we have never
    seen resolved cost a venue call. That matters because this shares the one
    4 req/s governor with the live runner -- an uncached rerun over 60 markets
    spends budget the bot needs to quote, and an earlier version of this
    analysis triggered venue rate limiting exactly that way.
    """

    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}

    priced, unknown = [], []
    fetched = 0
    for slug, book in sorted(per_market.items()):
        found = _residual(book)
        if not found:
            continue
        qty, paid, held = found
        if not held:
            unknown.append((slug, "no outcome recorded on the fill"))
            continue
        lookup = cache.get(slug)
        if lookup is None:
            try:
                raw = await gateway._call(
                    f"markets.retrieve_by_slug({slug})",
                    lambda: gateway.client.markets.retrieve_by_slug(slug),
                )
                market = raw.get("market", raw) if isinstance(raw, dict) else raw
                fetched += 1
                if str(market.get("status")) != "MARKET_STATUS_RESOLVED":
                    unknown.append((slug, "not resolved yet"))
                    continue
                names = json.loads(market.get("outcomes") or "[]")
                prices = json.loads(market.get("outcomePrices") or "[]")
                lookup = {str(n): str(p) for n, p in zip(names, prices)}
                cache[slug] = lookup           # cached only once RESOLVED
            except Exception as exc:
                unknown.append((slug, type(exc).__name__))
                continue
        if str(held) not in lookup:
            unknown.append((slug, f"outcome {held!r} not among {list(lookup)}"))
            continue
        won = Decimal(lookup[str(held)]) >= 1
        payout = qty if won else Decimal(0)
        priced.append((slug, held, qty, paid, payout, payout - paid))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return priced, unknown, fetched


def snapshot_change_lines(last, *, equity: Decimal, spent: Decimal,
                          paid_reward: Decimal,
                          settled_pnl: Decimal | None = None) -> list[str]:
    """Describe incomplete attribution without turning cash into trading P&L."""
    d_equity = equity - Decimal(last["equity"])
    d_spent = spent - Decimal(last["spent"])
    d_paid = paid_reward - Decimal(last["reward_paid"])
    remainder = d_equity + d_spent - d_paid
    lines = [
        f"--- since {last['at']} ---",
        f"  equity moved           : {d_equity:+12.2f}",
        f"  paid for fills         : {d_spent:12.2f}",
        f"  paid-status reward change: {d_paid:+12.2f}",
        f"  unattributed remainder : {remainder:+12.2f}",
        "  NOT trading P&L or settlement proof: cash flows, fees, open marks",
        "  and incomplete history can contribute. Use reward_table.py for daily trading.",
    ]
    if settled_pnl is not None and "settled_pnl" in last:
        measured = settled_pnl - Decimal(last["settled_pnl"])
        lines.append(f"  resolution-priced P&L change: {measured:+12.2f}")
        lines.append("  Ledger resolution estimate; not comparable to the unattributed remainder.")
    return lines


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--snapshots", default="data/accounting_snapshots.json")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--resolutions", default="data/resolutions.json",
                        help="cache of settled market resolutions (final, so "
                             "cached forever once seen)")
    parser.add_argument("--settlements", action="store_true",
                        help="price settled positions against each market's "
                             "resolution (one venue call per market)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    per_market, spent, fees, unknown_intent = _from_ledger(args.db)

    closed_pnl = Decimal(0)
    closed = open_ = 0
    open_cost = Decimal(0)
    for book in per_market.values():
        pairs = max(min(book["L_q"], book["S_q"]), Decimal(0))
        if book["L_q"] - book["S_q"] == 0:
            # Nothing is held any more. Either the two legs matched into pairs
            # that settle at exactly 1.00 each, or the position was sold back
            # out -- and both are fully realised, with the same expression.
            # Only the pair case existed until an operator closed a position by
            # hand; before this, a bought-then-sold market showed a residual of
            # zero, was priced by neither branch, and its P&L silently vanished.
            closed_pnl += pairs - (book["L_c"] + book["S_c"])
            closed += 1
            continue
        if pairs > 0:
            lc = book["L_c"] / book["L_q"] * pairs if book["L_q"] else Decimal(0)
            sc = book["S_c"] / book["S_q"] * pairs if book["S_q"] else Decimal(0)
            closed_pnl += pairs - lc - sc
            closed += 1
        found = _residual(book)
        if found:
            open_ += 1
            open_cost += found[1]

    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(),
        default_traffic_class="report")
    account = AsyncAccountSnapshotGateway(gateway)
    account_snapshot = await account.fetch_equity_snapshot(observed_at=now)
    equity = _money(account_snapshot.marked_equity)
    earnings = await gateway.fetch_earnings()
    paid_reward = sum(Decimal(str(getattr(r, "reward", 0) or 0))
                      for r in earnings
                      if str(getattr(r, "status", "")) == "PAID")
    pending = sum(Decimal(str(getattr(r, "reward", 0) or 0))
                  for r in earnings
                  if str(getattr(r, "status", "")) == "PENDING")

    print(f"ledger fills priced      : {closed + open_:5d} markets")
    if unknown_intent:
        print(f"  fills with an unrecognised intent: {unknown_intent}")
    print(f"net cash paid for fills  : {float(spent):12.2f}   "
          "(buys less sells)")
    print(f"fees                     : {float(fees):+12.2f}   (negative = rebate)")
    print(f"closed pairs             : {closed:5d} markets  "
          f"P&L {float(closed_pnl):+.2f}")
    print(f"unmatched (settled/open) : {open_:5d} markets  "
          f"cost {float(open_cost):.2f}")
    print("")
    print(f"reward   PAID {float(paid_reward):8.2f}   PENDING {float(pending):8.2f}")
    print(f"account marked equity    : {float(equity):12.2f}")

    settled_pnl = None
    if args.settlements:
        print("")
        print("pricing settled positions against each market's resolution...")
        priced, unknown, fetched = await _price_settlements(
            gateway, per_market, Path(args.resolutions))
        print(f"  ({fetched} venue calls; the rest came from the cache)")
        if priced:
            won = [r for r in priced if r[4] > 0]
            staked = sum(r[3] for r in priced)
            returned = sum(r[4] for r in priced)
            units = sum(r[2] for r in priced)
            settled_pnl = returned - staked
            print(f"  priced                   : {len(priced):5d} markets "
                  f"({len(won)} won / {len(priced) - len(won)} lost)")
            print(f"  staked                   : {float(staked):12.2f}")
            print(f"  returned                 : {float(returned):12.2f}")
            print(f"  settlement P&L           : {float(settled_pnl):+12.2f}")
            print(f"  win rate                 : "
                  f"{100.0 * len(won) / len(priced):11.1f}%   "
                  f"(break-even {float(staked / units) * 100:.1f}%)")
            print("")
            print("  worst settled positions:")
            for slug, held, qty, paid, payout, pnl in sorted(
                    priced, key=lambda r: r[5])[:5]:
                print(f"    {float(pnl):+7.2f}  held {str(held)[:18]:<18} "
                      f"qty {float(qty):6.2f}  paid {float(paid):6.2f} -> "
                      f"{float(payout):6.2f}   {slug[:30]}")
        if unknown:
            print(f"  could not price          : {len(unknown):5d}")
            for slug, why in unknown[:5]:
                print(f"      {slug[:40]:<40} {why}")

    path = Path(args.snapshots)
    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text())
        except Exception:
            history = []
    if history:
        last = history[-1]
        print("")
        for line in snapshot_change_lines(
                last, equity=equity, spent=spent, paid_reward=paid_reward,
                settled_pnl=settled_pnl):
            print(line)
    else:
        print("")
        print("no previous snapshot -- this run sets the baseline.")

    if not args.no_snapshot:
        history.append({"at": now.isoformat(), "equity": str(equity),
                        "spent": str(spent), "fees": str(fees),
                        "reward_paid": str(paid_reward),
                        "reward_pending": str(pending),
                        "closed_pnl": str(closed_pnl),
                        **({"settled_pnl": str(settled_pnl)}
                           if settled_pnl is not None else {})})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=1))
        print("")
        print(f"snapshot recorded ({len(history)} total) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
