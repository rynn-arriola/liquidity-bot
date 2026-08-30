#!/usr/bin/env python3
"""Trading, reward and net for each of the last N ET reward days. One table.

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

from liquidity_bot.bot_era import is_bot_era  # noqa: E402
from liquidity_bot.pnl import economic_contract_cost  # noqa: E402
from liquidity_bot.reward_calendar import reward_day  # noqa: E402
from liquidity_bot.sqlite_compat import active_fills_predicate  # noqa: E402

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
    active_fills = active_fills_predicate(connection)
    for row in connection.execute(
            "SELECT market_slug, intent, qty, price, received_at "
            f"FROM execution_fills WHERE {active_fills} "
            "AND intent IS NOT NULL AND received_at>=? "
            "ORDER BY received_at", (since,)):
        qty, price = Decimal(str(row["qty"])), Decimal(str(row["price"]))
        cell = markets[row["market_slug"]]
        if row["intent"] == "ORDER_INTENT_BUY_LONG":
            cell["lq"] += qty
            cell["paid"] += qty * economic_contract_cost(row["intent"], price)
        elif row["intent"] == "ORDER_INTENT_BUY_SHORT":
            cell["sq"] += qty
            cell["paid"] += qty * economic_contract_cost(row["intent"], price)
        else:
            # This estimate is deliberately BUY-pair-only. Account-level
            # accounting handles operator/legacy SELL intents exactly.
            continue
        if cell["day"] is None:
            cell["day"] = reward_day(row["received_at"])

    pnl, pairs, unbalanced = defaultdict(Decimal), defaultdict(Decimal), 0
    for cell in markets.values():
        matched = min(cell["lq"], cell["sq"])
        if matched <= 0 or cell["lq"] != cell["sq"]:
            unbalanced += 1
            continue
        pnl[cell["day"]] += matched - cell["paid"]
        pairs[cell["day"]] += matched
    return pnl, pairs, unbalanced


async def reward_by_day(gateway=None) -> tuple[dict, dict]:
    """Returns (paid_by_day, pending_by_day).

    Pending is returned, not discarded, because a day can post PARTIALLY -- and
    a partially posted day is the one case where PAID alone is actively
    misleading. Measured 2026-08-23: 2026-08-21 held $1.11 PAID against $28.43
    PENDING, so netting the paid part against a full day of trading printed
    `NET -34.03` for a day whose real figure was about -5.60. That is a $28
    error in the direction that reads as a disaster, on the exact number
    NEXT.md tells an operator to judge a size rollback by.

    Invariant 22 still holds: pending never enters an accounted net. It only
    decides whether a net may be printed at all.
    """

    # Keep the venue SDK dependency out of module import so the pure ledger
    # bucketing path remains testable without live-client packages installed.
    if gateway is None:
        from liquidity_bot.adapters import (
            AsyncReadOnlyGateway, shared_api_limiter)
        from liquidity_bot.probe import client_from_environment

        gateway = AsyncReadOnlyGateway(
            client_from_environment(), limiter=shared_api_limiter(),
            default_traffic_class="background")
    rows = await gateway.fetch_earnings(program_type="liquidityProgram")
    paid: dict = defaultdict(Decimal)
    pending: dict = defaultdict(Decimal)
    for row in rows:
        if not is_bot_era(row.date):
            continue
        status = str(row.status).upper()
        day = str(row.date)[:10]
        if status == "PAID":
            paid[day] += Decimal(str(row.reward))
        elif status == "PENDING":
            pending[day] += Decimal(str(row.reward))
    return paid, pending


def _unpaid_reward_display(pending: Decimal | None) -> tuple[str, str]:
    """Render an all-pending day without calling it an unposted day."""

    if pending:
        return f"{float(pending):9.2f}", "all PENDING, net withheld"
    return f"{'pending':>9}", "reward not posted yet"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    pnl, pairs, unbalanced = trading_by_day(args.db, CLEAN_FROM)
    paid, pending = await reward_by_day()

    today = date.fromisoformat(reward_day(datetime.now(timezone.utc)))
    days = [(today - timedelta(days=n)).isoformat()
            for n in range(args.days - 1, -1, -1)]

    print(f"daily P&L, last {args.days} ET reward days "
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
            reward_s, pending_note = _unpaid_reward_display(pending.get(day))
            print(f"{day:12} {float(pair_n):>7.0f} {trade_s} "
                  f"{reward_s} {'--':>9}  {pending_note}")
            continue
        due = pending.get(day)
        if due:
            # PARTIALLY posted. The paid part is real but incomplete, so a net
            # built from it understates income by whatever is still pending --
            # which is most of the day when reward is one to two days behind.
            # Show both parts and withhold the net, exactly as a fully
            # unposted day does.
            print(f"{day:12} {float(pair_n):>7.0f} {trade_s} "
                  f"{float(reward):9.2f} {'--':>9}  "
                  f"+{float(due):.2f} still PENDING, net withheld")
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
