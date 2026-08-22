#!/usr/bin/env python3
"""Trading, reward and net for each of the last N UTC days. One table.

Defaults to 14 days rather than 7 so the settled rows stay visible even
when the whole most-recent week is still pending -- reward can lag two
days, and a seven-day window can end up almost entirely `pending`.

Answers "is the bot making money" without stitching together three commands.

**The trap this script exists to avoid: reward posts one to two days late.**
A day whose reward has not settled shows its trading loss against nothing, so
it reads as a disaster and is not one. Those rows are marked `pending` and
their net is deliberately left blank rather than printed as a number someone
will quote later. Only days whose reward has posted get a net.

Sources, and why each:

* **Trading** -- matched-pair cost from this bot's own ledger. Every fill it
  sends is a PURCHASE (FINDINGS 6i), so a YES+NO pair settles at exactly
  1.0000 and realises `1.00 - paid` with no direction or outcome data needed.
  Restricted to markets whose legs BALANCE: allocating all fill cost to the
  matched pairs turned accounting.py's -47.24 into -135.92 on the same period.
* **Reward** -- venue `PAID` rows by earning date, filtered to this bot's era.
  PAID only: invariant 22 keeps pending out of accounted net. Era-filtered
  because the account carries $86.51 of maker-lab reward from before this bot
  (see bot_era.py); counting it roughly doubles the apparent result.

**What this does NOT capture:** positions that settled rather than closing.
Their entry is recorded and their exit is invisible, so the trading column
understates losses by an unknown amount -- $44.45 across 20 markets as of
2026-08-21. `accounting.py` is the authority on the account-level total.

One venue call (account-wide earnings); everything else is local.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.adapters import AsyncReadOnlyGateway  # noqa: E402
from liquidity_bot.bot_era import is_bot_era  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402

# Fills before this are unusable: the isAggressor fix reached production on
# 2026-08-17T13:20Z and roughly 60% of earlier fills were never recorded,
# overwhelmingly the passive closing leg.
CLEAN_FROM = "2026-08-18"


def trading_by_day(db: str, since: str) -> tuple[dict, dict, int]:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    markets = defaultdict(
        lambda: {"lq": Decimal(0), "sq": Decimal(0), "paid": Decimal(0),
                 "day": None})
    for row in connection.execute(
            "SELECT market_slug, intent, qty, price, received_at "
            "FROM execution_fills WHERE intent IS NOT NULL AND received_at>=? "
            "ORDER BY received_at", (since,)):
        qty, price = Decimal(str(row["qty"])), Decimal(str(row["price"]))
        cell = markets[row["market_slug"]]
        if "LONG" in row["intent"]:
            cell["lq"] += qty
            cell["paid"] += qty * price
        else:
            cell["sq"] += qty
            cell["paid"] += qty * (Decimal(1) - price)
        if cell["day"] is None:
            cell["day"] = row["received_at"][:10]

    pnl, pairs, unbalanced = defaultdict(Decimal), defaultdict(Decimal), 0
    for cell in markets.values():
        matched = min(cell["lq"], cell["sq"])
        if matched <= 0 or cell["lq"] != cell["sq"]:
            unbalanced += 1
            continue
        pnl[cell["day"]] += matched - cell["paid"]
        pairs[cell["day"]] += matched
    return pnl, pairs, unbalanced


async def reward_by_day() -> dict:
    gateway = AsyncReadOnlyGateway(client_from_environment())
    rows = await gateway.fetch_earnings(program_type="liquidityProgram")
    paid = defaultdict(Decimal)
    for row in rows:
        if str(row.status).upper() != "PAID" or not is_bot_era(row.date):
            continue
        paid[str(row.date)[:10]] += Decimal(str(row.reward))
    return paid


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    pnl, pairs, unbalanced = trading_by_day(args.db, CLEAN_FROM)
    paid = await reward_by_day()

    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=n)).isoformat()
            for n in range(args.days - 1, -1, -1)]

    print(f"daily P&L, last {args.days} UTC days      "
          f"(trading from {CLEAN_FROM}, balanced-leg markets only)")
    print()
    print(f"{'day':12} {'pairs':>7} {'trading':>9} {'reward':>9} {'NET':>9}  note")
    t_sum = r_sum = n_sum = Decimal(0)
    for day in days:
        trade = pnl.get(day)
        reward = paid.get(day)
        pair_n = pairs.get(day, Decimal(0))
        # Nothing at all happened -- before the bot existed, or a dark day.
        if trade is None and reward is None and day < CLEAN_FROM:
            continue
        if trade is None:
            # Distinguish "no trading data" from "traded nothing". Before the
            # clean-data floor the fills exist but are unusable, and a dash
            # would read as a flat day rather than a blind one.
            trade_s = f"{'n/a':>9}"
            note = "no usable fill data"
        else:
            trade_s = f"{float(trade):9.2f}"
            note = ""
        if reward is None:
            # Not posted yet. Printing a net here invites quoting a number
            # that is missing its income side.
            print(f"{day:12} {float(pair_n):>7.0f} {trade_s} "
                  f"{'pending':>9} {'--':>9}  reward not posted yet")
            continue
        if trade is None:
            print(f"{day:12} {float(pair_n):>7.0f} {trade_s} "
                  f"{float(reward):9.2f} {'--':>9}  {note}")
            continue
        net = trade + reward
        t_sum += trade
        r_sum += reward
        n_sum += net
        print(f"{day:12} {float(pair_n):>7.0f} {trade_s} "
              f"{float(reward):9.2f} {float(net):9.2f}")
    print(f"{'-' * 49}")
    print(f"{'complete':12} {'':>7} {float(t_sum):9.2f} "
          f"{float(r_sum):9.2f} {float(n_sum):9.2f}")
    print()
    print("  Only rows with BOTH sides are totalled. `pending` means reward has")
    print("  not posted (1-2 day lag) and its net is withheld on purpose -- do")
    print("  not read those as losses. `n/a` means the fills predate the")
    print(f"  isAggressor fix ({CLEAN_FROM}) and cannot be priced at all.")
    print(f"  trading excludes {unbalanced} market(s) with unbalanced legs, and")
    print("  cannot see positions that settled rather than closing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
