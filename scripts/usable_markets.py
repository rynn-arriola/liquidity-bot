#!/usr/bin/env python3
"""Why is the bot quoting N markets and not 20? Answers it in one command.

The `[selection]` log line prints `catalog / executable / rewarded /
books_ranked / viable` and stops there. Those five numbers cannot distinguish
"working correctly, nothing is eligible" from "markets are being lost to book
errors" -- the counters that separate them (`catalog_spread_filtered`,
`unsupported_programs`, `failures`, `deferred_markets`) exist on the selection
object and are never logged. On 2026-08-17 answering "is this normal?" needed
the selector run by hand.

This runs the real selector read-only, prints the whole funnel, and says which
of the two it is.

    python3 scripts/usable_markets.py            # funnel + verdict
    python3 scripts/usable_markets.py --rejects  # also list tight-book markets

**It costs venue calls** -- one catalog read, batched incentives, and one book
per detailed candidate (~50 total). That shares the 4 req/s governor with the
live runner, so do not put it in a loop.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway  # noqa: E402
from liquidity_bot.adapters import AsyncReadOnlyGateway  # noqa: E402
from liquidity_bot.market_selector import (  # noqa: E402
    RewardMarketSelector, RewardSelectionPolicy)
from liquidity_bot.probe import client_from_environment  # noqa: E402

MAX_MARKETS = 20          # pinned by assert_authorized; see commissioning.py


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rejects", action="store_true",
                        help="list markets dropped by the spread filter")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    gateway = AsyncReadOnlyGateway(client_from_environment())
    policy = RewardSelectionPolicy()
    selection = await RewardMarketSelector(gateway, policy=policy).select(now=now)

    horizon_h = policy.selection_horizon_seconds / 3600.0
    viable = [o for o in selection.opportunities if o.viable_sides]

    print(f"at {now.isoformat()[:19]}Z   horizon {horizon_h:.1f}h   "
          f"min entry spread {policy.minimum_entry_spread}")
    print("")
    print("SELECTION FUNNEL")
    print(f"  catalog markets            {selection.catalog_markets:6d}")
    print(f"  in the {horizon_h:.0f}h horizon         "
          f"{selection.executable_catalog_markets:6d}")
    print(f"  carrying rewards           "
          f"{selection.rewarded_executable_markets:6d}")
    print(f"  - unsupported program      "
          f"{selection.unsupported_programs:6d}")
    print(f"  - book too tight to enter  "
          f"{selection.catalog_spread_filtered_markets:6d}"
          "    <- usually the big one")
    print(f"  = eligible pregame         "
          f"{selection.eligible_pregame_markets:6d}")
    print(f"  books actually read        {selection.books_evaluated:6d}")
    print(f"  - deferred (book budget)   {len(selection.deferred_markets):6d}")
    print(f"  - book read failures       {len(selection.failures):6d}")
    print(f"  = VIABLE                   {len(viable):6d}"
          f"    of {MAX_MARKETS} slots")

    # Venue is authoritative for what we are actually resting in.
    account = AsyncAccountSnapshotGateway(gateway)
    orders = await account.fetch_open_orders(observed_at=now)
    per_market = collections.Counter(o.market_slug for o in orders)
    print("")
    print(f"AT THE VENUE: {len(orders)} working order(s) across "
          f"{len(per_market)} market(s), {MAX_MARKETS} slots")
    for slug, count in sorted(per_market.items()):
        note = "" if count >= 2 else "   (one side only)"
        print(f"    {slug[:46]:<46} {count}{note}")

    if viable:
        print("")
        print("VIABLE NOW")
        for opportunity in viable:
            sides = ",".join(sorted(opportunity.viable_sides))
            print(f"    {opportunity.market.slug[:46]:<46} {sides}")

    # --- the verdict -------------------------------------------------------
    print("")
    print("VERDICT")
    problems = []
    if selection.failures:
        problems.append(
            f"{len(selection.failures)} book read(s) FAILED -- markets are "
            "being lost to errors, not to economics")
        for failure in selection.failures[:5]:
            problems.append(f"     {failure}")
    if selection.deferred_markets:
        problems.append(
            f"{len(selection.deferred_markets)} market(s) deferred by the book "
            f"budget (max_book_candidates={policy.max_book_candidates}) -- "
            "more candidates than the selector will price in one pass")
    if selection.unsupported_programs:
        problems.append(
            f"{selection.unsupported_programs} market(s) run a reward program "
            "this executor does not implement")

    if problems:
        print("  SOMETHING IS OFF:")
        for line in problems:
            print(f"  - {line}")
    else:
        print("  Healthy. Nothing is being lost to errors, throttling or")
        print("  unsupported programs -- every market that dropped out did so")
        print("  on a deliberate economic rule.")

    if selection.executable_catalog_markets == 0:
        print("  Nothing is inside the horizon at all: a schedule trough.")
        print("  Normal at quiet hours. Compare against a busy hour before")
        print("  concluding anything.")
    elif selection.catalog_spread_filtered_markets > (
            0.5 * max(1, selection.rewarded_executable_markets)):
        share = (100.0 * selection.catalog_spread_filtered_markets
                 / max(1, selection.rewarded_executable_markets))
        print(f"  {share:.0f}% of rewarded markets have books tighter than "
              f"{policy.minimum_entry_spread}, so they are refused before any")
        print("  book is read. This is the dominant filter and it is WORKING")
        print("  AS DESIGNED -- a book that tight leaves no room between entry")
        print("  and exit. It is also the lever if you want more markets, and")
        print("  loosening it buys thinner margins. See FINDINGS.")

    if len(viable) < MAX_MARKETS and not problems:
        print(f"  {MAX_MARKETS - len(viable)} slot(s) idle because nothing")
        print("  qualifies -- not because the bot is stuck.")

    if args.rejects:
        print("")
        print("MARKETS DROPPED FOR A TIGHT BOOK (catalog BBO)")
        catalog = await gateway.list_sports_markets()
        shown = 0
        for market in catalog:
            bid = market.catalog_best_bid
            offer = market.catalog_best_offer
            if bid is None or offer is None:
                continue
            spread = offer - bid
            if spread + 1e-9 < policy.minimum_entry_spread:
                print(f"    {market.slug[:46]:<46} spread {spread:.4f}")
                shown += 1
                if shown >= 30:
                    print("    ... (truncated at 30)")
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
