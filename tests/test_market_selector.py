from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.fill_hazard import FillHazardEstimate
from liquidity_bot.market_selector import (
    RewardMarketSelector,
    RewardSelectionPolicy,
    evaluate_market_opportunity,
    reward_market_slugs,
)
from liquidity_bot.models import (
    BookLevel,
    BookSnapshot,
    MarketIncentive,
    MarketMetadata,
)
from liquidity_bot.rewards import IncentivePeriod


NOW = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)


def market(
    slug: str,
    *,
    starts_in_hours: int = 1,
    market_type_v2: str = "SPORTS_MARKET_TYPE_MONEYLINE",
    catalog_bid: float | None = .49,
    catalog_offer: float | None = .51,
) -> MarketMetadata:
    return MarketMetadata(
        slug=slug,
        game_start=NOW + timedelta(hours=starts_in_hours),
        tick_size=0.01,
        active=True,
        closed=False,
        status="MARKET_STATUS_OPEN",
        minimum_trade_qty=1,
        sports_market_type_v2=market_type_v2,
        catalog_best_bid=catalog_bid,
        catalog_best_offer=catalog_offer,
    )


def incentive(
    slug: str,
    *,
    pool: float,
    target: float = 10,
    phase: str = "day_of",
) -> MarketIncentive:
    return MarketIncentive(slug=slug, periods=(IncentivePeriod(
        program_id=f"reward-{slug}",
        period=phase,
        status="active",
        reward_pool=pool,
        discount_factor=0.5,
        target_size=target,
    ),))


def book(
    slug: str,
    *,
    touch_size: float = 5,
    bid: float = .49,
    offer: float = .51,
) -> BookSnapshot:
    return BookSnapshot(
        slug=slug,
        bids=(BookLevel(bid, touch_size), BookLevel(bid - .01, touch_size)),
        offers=(BookLevel(offer, touch_size), BookLevel(offer + .01, touch_size)),
        state="MARKET_STATE_OPEN",
    )


class FakeGateway:
    def __init__(self, markets, incentives, books):
        self.markets = markets
        self.incentives = incentives
        self.books = books
        self.metadata_calls = []
        self.catalog_calls = 0
        self.book_calls = []
        self.incentive_symbol_calls = []

    async def list_sports_markets(self):
        self.catalog_calls += 1
        return tuple(self.markets.values())

    async def fetch_incentives(self, symbols):
        symbols = tuple(symbols)
        self.incentive_symbol_calls.append(symbols)
        return {
            slug: self.incentives[slug]
            for slug in symbols
            if slug in self.incentives
        }

    async def fetch_market(self, slug):
        self.metadata_calls.append(slug)
        raise AssertionError("selector must not fetch metadata by slug")

    async def fetch_book(self, slug):
        self.book_calls.append(slug)
        return self.books[slug]


class FixedFillHazard:
    def __init__(self, probability: float):
        self.probability = probability

    def estimate(self, **inputs):
        return FillHazardEstimate(
            probability=self.probability,
            global_probability=self.probability,
            driver="test:fixed",
            horizon_seconds=inputs["horizon_seconds"],
            calibration_id="test",
        )


class OpportunityTests(unittest.TestCase):
    def test_exact_live_economics_are_applied_to_both_sides(self):
        item = evaluate_market_opportunity(
            market("high"),
            incentive("high", pool=20_000),
            book("high"),
            period=incentive("high", pool=20_000).periods[0],
            now=NOW,
            policy=RewardSelectionPolicy(enforce_reward_cost_gate=True, ),
        )

        self.assertEqual(item.viable_sides, ("bid", "offer"))
        self.assertGreater(item.best_expected_net_value, 0)
        self.assertTrue(all(side.projection.eligible for side in item.sides))

    def test_fill_probability_changes_the_exit_cost_without_changing_reward(self):
        args = dict(
            market=market("aec-itfme-a-b-2026-08-10"),
            incentive=incentive("risk", pool=100),
            book=book("risk"),
            period=incentive("risk", pool=100).periods[0],
            now=NOW,
            policy=RewardSelectionPolicy(enforce_reward_cost_gate=True, ),
        )
        conservative = evaluate_market_opportunity(
            **args, fill_hazard_model=FixedFillHazard(1.0))
        calibrated = evaluate_market_opportunity(
            **args, fill_hazard_model=FixedFillHazard(.01))

        self.assertEqual(conservative.viable_sides, ())
        self.assertEqual(calibrated.viable_sides, ("bid", "offer"))
        self.assertEqual(
            {side.projection.projected_reward for side in conservative.sides},
            {side.projection.projected_reward for side in calibrated.sides},
        )

    def test_tight_full_book_is_never_an_entry_candidate(self):
        item = evaluate_market_opportunity(
            market("tight", catalog_bid=None, catalog_offer=None),
            incentive("tight", pool=20_000),
            book("tight", bid=.49, offer=.50),
            period=incentive("tight", pool=20_000).periods[0],
            now=NOW,
            policy=RewardSelectionPolicy(enforce_reward_cost_gate=True, ),
        )

        self.assertFalse(item.entry_spread_eligible)
        self.assertEqual(item.viable_sides, ())
        self.assertEqual(
            {side.blocker for side in item.sides},
            {"spread_below_entry_minimum"},
        )


class RankingTests(unittest.IsolatedAsyncioTestCase):
    """Only twenty slots exist, so which twenty is the whole game."""

    async def test_the_richer_pool_wins_the_slot(self):
        """Ranked by projected reward, not by a field that ties everywhere.

        `_opportunity_key` led with -best_expected_net_value, which is floored
        to 1e-09 on every side whenever the reward-cost gate is advisory -- so
        every market TIED on the primary key and ranking fell through to
        tiebreakers that do not predict earnings. Measured live 2026-08-15: the
        chosen twenty overlapped the best twenty on only seven, holding a slot
        worth 0.000014 while one worth 0.010063 went unquoted.
        """
        # IDENTICAL pools and targets, so every old tiebreaker ties. The only
        # difference is depth inside the reward band -- the competition. A thin
        # book means a bigger share of the same pool, which is exactly what
        # projected reward measures and what the old key could not see.
        slugs = ("crowded", "thin")
        gateway = FakeGateway(
            {s: market(s) for s in slugs},
            {s: incentive(s, pool=100.0) for s in slugs},
            {"crowded": book("crowded", touch_size=5000),
             "thin": book("thin", touch_size=5)})
        selector = RewardMarketSelector(
            gateway, RewardSelectionPolicy(quote_size=1.0),
            fill_hazard_model=FixedFillHazard(0.05))

        out = await selector.select(now=NOW)
        ranked = [item.market.slug for item in out.top_viable(2)]

        self.assertEqual(
            ranked[0], "thin",
            "the market where our size is a bigger share must win the slot")

    async def test_the_default_horizon_takes_the_whole_day_of_period(self):
        """Six hours by default, restored 2026-08-23.

        This test asserted the opposite until then. The narrowing to two hours
        was argued from `projected_reward` bucketed by time-to-kickoff, and
        that quantity is `constant x (pool x share)` -- its only time term is
        `min(horizon, remaining)`, which is always exactly 30 because the
        pre-kickoff flatten keeps `remaining` above 300. It cannot vary with
        distance, so the buckets compared different markets, not different
        times. Polymarket's docs give no distance penalty either: day_of runs
        six hours, every second equally weighted. See FINDINGS.

        `later` is deliberately the RICHER book -- thinner touch, bigger share
        -- so this also pins that a distant market is allowed to WIN a slot on
        its economics, which is the whole behaviour the narrowing removed.
        """
        gateway = FakeGateway(
            {"soon": market("soon", starts_in_hours=1),
             "later": market("later", starts_in_hours=5)},
            {s: incentive(s, pool=100.0) for s in ("soon", "later")},
            {"soon": book("soon", touch_size=5000),
             "later": book("later", touch_size=5)})
        selector = RewardMarketSelector(
            gateway, RewardSelectionPolicy(quote_size=1.0),
            fill_hazard_model=FixedFillHazard(0.05))

        out = await selector.select(now=NOW)

        self.assertEqual(
            sorted(i.market.slug for i in out.opportunities),
            ["later", "soon"])
        self.assertIn(
            "later", gateway.book_calls,
            "a market inside the six-hour window must be priced, not skipped")

    async def test_the_horizon_is_configurable_back_down_to_two_hours(self):
        """Narrowing stays available by config, and still skips the book fetch.

        Kept as a live path rather than deleted: if the fill-risk half of the
        original argument turns out to be right -- it has never been measured,
        because clean trading data begins after the change -- this is how it
        goes back.
        """

        gateway = FakeGateway(
            {"soon": market("soon", starts_in_hours=1),
             "later": market("later", starts_in_hours=5)},
            {s: incentive(s, pool=100.0) for s in ("soon", "later")},
            {"soon": book("soon", touch_size=5000),
             "later": book("later", touch_size=5)})
        selector = RewardMarketSelector(
            gateway,
            RewardSelectionPolicy(quote_size=1.0,
                                  selection_horizon_seconds=7200),
            fill_hazard_model=FixedFillHazard(0.05))

        out = await selector.select(now=NOW)

        self.assertEqual([i.market.slug for i in out.opportunities], ["soon"])
        self.assertNotIn(
            "later", gateway.book_calls,
            "a market outside the horizon must not even cost a book fetch")

    def test_the_horizon_cannot_exceed_the_authorized_window(self):
        """Narrowing is a strategy choice; widening past the venue's `day_of`
        period would be selecting markets that cannot be paid at all."""

        with self.assertRaises(ValueError):
            RewardSelectionPolicy(selection_horizon_seconds=21601)
        with self.assertRaises(ValueError):
            RewardSelectionPolicy(selection_horizon_seconds=0)

    async def test_projected_reward_total_sums_the_eligible_sides(self):
        gateway = FakeGateway(
            {"one": market("one")}, {"one": incentive("one", pool=100.0)},
            {"one": book("one")})
        selector = RewardMarketSelector(
            gateway, RewardSelectionPolicy(quote_size=1.0),
            fill_hazard_model=FixedFillHazard(0.05))

        out = await selector.select(now=NOW)
        item = out.opportunities[0]

        self.assertAlmostEqual(
            item.projected_reward_total,
            sum(s.projection.projected_reward for s in item.sides
                if s.projection is not None and s.projection.eligible))


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_reward_programs_are_considered_not_only_high_pool(self):
        markets = {slug: market(slug) for slug in ("high", "low")}
        incentives = {
            "high": incentive("high", pool=20_000),
            "low": incentive("low", pool=200),
        }
        gateway = FakeGateway(
            markets,
            incentives,
            {slug: book(slug) for slug in markets},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.rewarded_executable_markets, 2)
        self.assertEqual(result.unsupported_programs, 0)
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(set(gateway.book_calls), {"high", "low"})
        self.assertEqual(set(reward_market_slugs(result)), {"high", "low"})
        self.assertEqual(result.selected.market.slug, "high")
        self.assertEqual(result.viable_markets, 2)
        self.assertEqual(gateway.catalog_calls, 1)
        self.assertEqual(
            set(gateway.incentive_symbol_calls[0]), {"high", "low"})

    async def test_no_profitable_market_returns_no_execution_selection(self):
        markets = {slug: market(slug) for slug in ("smaller", "larger")}
        incentives = {
            "smaller": incentive("smaller", pool=100),
            "larger": incentive("larger", pool=200),
        }
        gateway = FakeGateway(
            markets,
            incentives,
            {slug: book(slug, touch_size=1000) for slug in markets},
        )

        # Armed explicitly: this test is about the reward-cost gate, which is
        # advisory by default now (maker-lab's entry rule is 2c plus caps).
        result = await RewardMarketSelector(
            gateway,
            RewardSelectionPolicy(enforce_reward_cost_gate=True),
        ).select(now=NOW)

        self.assertEqual(result.viable_markets, 0)
        self.assertIsNone(result.selected)
        larger = next(
            item for item in result.opportunities
            if item.market.slug == "larger")
        self.assertEqual(
            {side.blocker for side in larger.sides},
            {"projected_reward_does_not_cover_probability_weighted_exit_cost"},
        )
        self.assertEqual(
            {side.projection.reason for side in larger.sides},
            {"projected"},
        )

    async def test_live_closed_and_too_large_minimum_markets_are_excluded(self):
        open_item = market("open")
        live_item = market("live", starts_in_hours=0)
        closed_item = MarketMetadata(
            **{**market("closed").__dict__, "closed": True})
        large_minimum = MarketMetadata(
            **{**market("large-minimum").__dict__, "minimum_trade_qty": 2})
        markets = {
            item.slug: item for item in
            (open_item, live_item, closed_item, large_minimum)
        }
        incentives = {
            slug: incentive(slug, pool=1000) for slug in markets
        }
        gateway = FakeGateway(
            markets,
            incentives,
            {slug: book(slug) for slug in markets},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.eligible_pregame_markets, 1)
        self.assertEqual(reward_market_slugs(result), ("open",))
        self.assertEqual(gateway.book_calls, ["open"])

    async def test_undocumented_period_is_counted_without_rest_fanout(self):
        markets = {"tournament": market("tournament")}
        incentives = {
            "tournament": incentive(
                "tournament", pool=5000, phase="tournament_with_cutoff")
        }
        gateway = FakeGateway(
            markets,
            incentives,
            {"tournament": book("tournament")},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.rewarded_executable_markets, 1)
        self.assertEqual(result.unsupported_programs, 1)
        self.assertEqual(result.eligible_pregame_markets, 0)
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(gateway.book_calls, [])

    async def test_book_budget_defers_but_does_not_hide_discovery_count(self):
        markets = {f"market-{i}": market(f"market-{i}") for i in range(3)}
        incentives = {
            slug: incentive(slug, pool=100 + i)
            for i, slug in enumerate(markets)
        }
        gateway = FakeGateway(
            markets,
            incentives,
            {slug: book(slug) for slug in markets},
        )
        selector = RewardMarketSelector(
            gateway,
            RewardSelectionPolicy(enforce_reward_cost_gate=True, max_book_candidates=2),
        )

        result = await selector.select(now=NOW)

        self.assertEqual(result.rewarded_executable_markets, 3)
        self.assertEqual(result.eligible_pregame_markets, 3)
        self.assertEqual(result.books_evaluated, 2)
        self.assertEqual(len(result.deferred_markets), 1)

        second = await selector.select(now=NOW)

        self.assertEqual(second.books_evaluated, 2)
        self.assertNotEqual(
            set(reward_market_slugs(result)),
            set(reward_market_slugs(second)),
        )
        self.assertEqual(set(gateway.book_calls), set(markets))

    async def test_catalog_filter_precedes_incentive_and_book_fanout(self):
        valid = market("valid")
        early = market("early", starts_in_hours=7)
        closed = MarketMetadata(
            **{**market("closed").__dict__, "closed": True})
        missing_tick = MarketMetadata(
            **{**market("missing-tick").__dict__, "tick_size": None})
        markets = {
            item.slug: item for item in (valid, early, closed, missing_tick)
        }
        incentives = {
            slug: incentive(slug, pool=20_000) for slug in markets
        }
        gateway = FakeGateway(
            markets,
            incentives,
            {slug: book(slug) for slug in markets},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.catalog_markets, 4)
        self.assertEqual(result.executable_catalog_markets, 1)
        self.assertEqual(gateway.incentive_symbol_calls, [("valid",)])
        self.assertEqual(gateway.book_calls, ["valid"])
        self.assertEqual(gateway.metadata_calls, [])

    async def test_structure_filter_uses_v2_not_two_way_side_count(self):
        moneyline = market("moneyline")
        future = market(
            "future", market_type_v2="SPORTS_MARKET_TYPE_FUTURE")
        prop = market("prop", market_type_v2="SPORTS_MARKET_TYPE_PROP")
        markets = {item.slug: item for item in (moneyline, future, prop)}
        gateway = FakeGateway(
            markets,
            {slug: incentive(slug, pool=20_000) for slug in markets},
            {slug: book(slug) for slug in markets},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.executable_catalog_markets, 1)
        self.assertEqual(gateway.incentive_symbol_calls, [("moneyline",)])
        self.assertEqual(gateway.book_calls, ["moneyline"])

    async def test_catalog_bbo_skips_known_tight_market_before_book(self):
        markets = {
            "tight": market("tight", catalog_bid=.49, catalog_offer=.50),
            "wide": market("wide", catalog_bid=.49, catalog_offer=.51),
        }
        gateway = FakeGateway(
            markets,
            {slug: incentive(slug, pool=20_000) for slug in markets},
            {slug: book(slug) for slug in markets},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.rewarded_executable_markets, 2)
        self.assertEqual(result.catalog_spread_filtered_markets, 1)
        self.assertEqual(gateway.book_calls, ["wide"])
        self.assertEqual(result.selected.market.slug, "wide")

    async def test_fresh_book_overrides_stale_wide_catalog_quote(self):
        markets = {"tight": market("tight")}
        gateway = FakeGateway(
            markets,
            {"tight": incentive("tight", pool=20_000)},
            {"tight": book("tight", bid=.49, offer=.50)},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertIsNone(result.selected)
        self.assertEqual(result.books_evaluated, 1)
        self.assertEqual(
            {side.blocker for side in result.opportunities[0].sides},
            {"spread_below_entry_minimum"},
        )


if __name__ == "__main__":
    unittest.main()
