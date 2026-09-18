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
    python3 scripts/usable_markets.py --rejects  # also list why each dropped

**It costs venue calls** -- bounded event pages, exact-symbol incentives, and
one book per detailed candidate (~50 total), plus a second event/incentive read
with `--rejects`. That shares the 4 req/s governor with the live runner, so do
not put it in a loop.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway  # noqa: E402
from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, shared_api_limiter)
from liquidity_bot.commissioning import CommissioningConfig  # noqa: E402
from liquidity_bot.market_selector import (  # noqa: E402
    EVENT_DISCOVERY_CLOCK_SKEW_SECONDS,
    EVENT_DISCOVERY_PAGE_SIZE,
    SUPPORTED_EVENT_PERIODS,
    RewardMarketSelector,
    RewardSelectionPolicy,
)
from liquidity_bot.probe import client_from_environment  # noqa: E402

CONFIG = CommissioningConfig.diagnostic_defaults()
MAX_MARKETS = CONFIG.max_markets


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rejects", action="store_true",
                        help="list every in-horizon market and why it dropped")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(),
        default_traffic_class="report")
    policy = CONFIG.reward_selection_policy()
    selection = await RewardMarketSelector(
        gateway, policy=policy, clock=lambda: datetime.now(timezone.utc)).select(now=now)

    horizon_h = (policy.discovery_window_end(now) - now).total_seconds() / 3600.0
    viable = [o for o in selection.opportunities if o.viable_sides]

    # Derived, not reported by the selector. `_applicable_period` returning
    # None hits a bare `continue` with no counter, so a third of the funnel can
    # drain with nothing to show for it -- measured 2026-08-19T23:31Z, 15 of 43
    # markets, every one live-period-only. The verdict then blamed the spread
    # filter, because that is the counter that does exist. Subtraction is exact:
    # these four are the only paths out of that loop.
    period_dropped = max(0, (
        selection.rewarded_executable_markets
        - selection.unsupported_programs
        - selection.catalog_spread_filtered_markets
        - selection.eligible_pregame_markets))

    print(f"at {now.isoformat()[:19]}Z   horizon {horizon_h:.1f}h   "
          f"min entry spread {policy.minimum_entry_spread}")
    print("")
    print("SELECTION FUNNEL")
    print(f"  buffered event markets     {selection.catalog_markets:6d}")
    print(f"  in the {horizon_h:.0f}h horizon         "
          f"{selection.executable_catalog_markets:6d}")
    print(f"  reward-authorized          "
          f"{selection.rewarded_executable_markets:6d}")
    print(f"  - unsupported program      "
          f"{selection.unsupported_programs:6d}")
    print(f"  - no applicable period     {period_dropped:6d}"
          "    <- live-only; derived, see --rejects")
    print(f"  - book too tight to enter  "
          f"{selection.catalog_spread_filtered_markets:6d}")
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

    rewarded = max(1, selection.rewarded_executable_markets)
    if selection.executable_catalog_markets == 0:
        print("  Nothing is inside the horizon at all: a schedule trough.")
        print("  Normal at quiet hours. Compare against a busy hour before")
        print("  concluding anything.")
    elif period_dropped > selection.catalog_spread_filtered_markets:
        # These two have different levers and conflating them sends the work to
        # the wrong place: a tight book is a market we do not want, a live-only
        # period is a market we cannot address at all. The live pool is ~8x
        # day_of (FINDINGS section 4), so this is the more interesting of them.
        share = 100.0 * period_dropped / rewarded
        print(f"  {share:.0f}% of rewarded markets have NO APPLICABLE PREGAME "
              "PERIOD -- their")
        print("  day-of/daily-event reward clock is inactive, unproven or outside")
        print("  the configured pregame horizon; live-only rewards cannot qualify.")
        print("  This is the dominant drain and it is NOT the spread filter.")
        print("  The lever is the lifecycle, not minimum_entry_spread.")
    elif selection.catalog_spread_filtered_markets > 0.5 * rewarded:
        share = 100.0 * selection.catalog_spread_filtered_markets / rewarded
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
        await _print_rejects(gateway, policy, now)
    return 0


async def _print_rejects(
    gateway: AsyncReadOnlyGateway,
    policy: RewardSelectionPolicy,
    now: datetime,
) -> None:
    """List every in-horizon market and the gate that dropped it.

    This used to walk the WHOLE catalog and print anything whose catalog BBO
    was tighter than the minimum -- no horizon, moneyline, active or open
    filter, and no connection to `catalog_spread_filtered_markets`. The header
    claimed "markets dropped for a tight book" while the rows were markets the
    horizon gate had discarded long before the spread gate saw them, and
    truncating at 30 in catalog order filled the screen with month-out
    `tec-mlb-*champ-*` futures. It produced two wrong diagnoses on 2026-08-19,
    and the FINDINGS entry from 03:25Z characterising the rejects as long-dated
    futures rests on this output. See BUGFIXES.

    It now reuses the selector's own gates, in the selector's own order, so the
    listing cannot disagree with the funnel printed above it.
    """

    selector = RewardMarketSelector(gateway, policy=policy)
    window_end = policy.discovery_window_end(now)
    clock_skew = timedelta(seconds=EVENT_DISCOVERY_CLOCK_SKEW_SECONDS)
    catalog = await gateway.list_upcoming_sports_markets(
        start=now - clock_skew,
        end=window_end + clock_skew,
        limit=EVENT_DISCOVERY_PAGE_SIZE,
        require_complete=True,
    )
    in_horizon = {
        market.slug: market
        for market in catalog
        if selector._catalog_market_is_executable(
            market, now=now, window_end=window_end)
    }
    incentives = await gateway.fetch_incentives(sorted(in_horizon))
    now = datetime.now(timezone.utc)

    unsupported: list[str] = []
    live_only: list[tuple[str, str]] = []
    tight: list[tuple[float, str]] = []
    candidates: list[tuple[float, str]] = []
    for slug in sorted(incentives):
        incentive = incentives[slug]
        market = in_horizon[slug]
        if not any(str(period.period).strip().lower() in SUPPORTED_EVENT_PERIODS
                   for period in incentive.periods):
            unsupported.append(slug)
            continue
        period = selector._applicable_period(
            market, incentive, now, require_event_freshness=False)
        if period is None or str(period.period) == "live":
            names = ",".join(sorted({str(p.period) for p in incentive.periods}))
            live_only.append((slug, names))
            continue
        bid, offer = market.catalog_best_bid, market.catalog_best_offer
        spread = -1.0 if bid is None or offer is None else offer - bid
        if selector._catalog_spread_can_enter(market):
            candidates.append((spread, slug))
        else:
            tight.append((spread, slug))

    print("")
    print("IN-HORIZON MARKETS AND WHY EACH DROPPED   "
          f"({len(in_horizon)} markets)")

    def _show(title: str, rows: list, render) -> None:
        print("")
        print(f"  {title}  {len(rows)}")
        for row in rows[:15]:
            print(f"    {render(row)}")
        if len(rows) > 15:
            print(f"    ... ({len(rows) - 15} more)")

    def _spread(value: float) -> str:
        return "n/a" if value < 0 else format(value, ".4f")

    if unsupported:
        _show("reward program not implemented", unsupported,
              lambda slug: f"{slug[:46]:<46}")
    if live_only:
        _show("no applicable pregame period", live_only,
              lambda row: f"{row[0][:46]:<46} periods: {row[1]}")
    if tight:
        # Widest first: near-misses are the only rows that speak to whether the
        # 2c minimum is holding back anything real. FINDINGS measured the
        # rejects at 3-20x tighter than the threshold, i.e. no headroom.
        tight.sort(reverse=True)
        _show(f"book too tight to enter (< {policy.minimum_entry_spread})",
              tight, lambda row: f"{row[1][:46]:<46} spread {_spread(row[0])}")
    if candidates:
        candidates.sort(reverse=True)
        _show("candidate for a book read", candidates,
              lambda row: f"{row[1][:46]:<46} spread {_spread(row[0])}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
