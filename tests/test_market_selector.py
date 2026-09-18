from __future__ import annotations

import unittest
from dataclasses import replace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
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
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> MarketIncentive:
    return MarketIncentive(slug=slug, periods=(IncentivePeriod(
        program_id=f"reward-{slug}",
        period=phase,
        status="active",
        reward_pool=pool,
        discount_factor=0.5,
        target_size=target,
        starts_at=starts_at,
        ends_at=ends_at,
    ),), observed_at=NOW)


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
        self.upcoming_calls = []
        self.incentive_universe_calls = []
        self.book_calls = []
        self.incentive_symbol_calls = []
        self.traffic_classes = []
        self.last_upcoming_event_pages = 0

    @contextmanager
    def traffic_class(self, value):
        self.traffic_classes.append(value)
        yield

    async def list_sports_markets(self):
        self.catalog_calls += 1
        return tuple(self.markets.values())

    async def list_upcoming_sports_markets(
        self, *, start, end, limit=500, require_complete=False,
    ):
        self.upcoming_calls.append((start, end, limit, require_complete))
        self.last_upcoming_event_pages = 1
        return tuple(self.markets.values())

    async def fetch_incentives(self, symbols):
        symbols = tuple(symbols)
        self.incentive_symbol_calls.append(symbols)
        return {
            slug: self.incentives[slug]
            for slug in symbols
            if slug in self.incentives
        }

    async def list_incentives(self, *, statuses=("active",)):
        self.incentive_universe_calls.append(tuple(statuses))
        return dict(self.incentives)

    async def fetch_market(self, slug):
        self.metadata_calls.append(slug)
        raise AssertionError("selector must not fetch metadata by slug")

    async def fetch_markets_by_slugs(self, slugs):
        slugs = tuple(slugs)
        self.metadata_calls.append(slugs)
        return {
            slug: self.markets[slug]
            for slug in slugs
            if slug in self.markets
        }

    async def fetch_book(self, slug):
        self.book_calls.append(slug)
        value = self.books[slug]
        if isinstance(value, Exception):
            raise value
        return value


class ExpandedDailySelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_daily_keeps_day_of_priority_and_book_bound(self):
        markets = {"old": market("old"), "daily": market("daily", starts_in_hours=24),
                   "too-far": market("too-far", starts_in_hours=25),
                   "early-only": market("early-only", starts_in_hours=12),
                   "live": market("live", starts_in_hours=0)}
        incentives = {s: incentive(s, pool=1_000_000, phase="daily_event") for s in markets}
        incentives["old"] = incentive("old", pool=35)
        incentives["early-only"] = incentive("early-only", pool=1_000_000, phase="early")
        for cap in (1, 2):
            gateway = FakeGateway(markets, incentives, {s: book(s) for s in markets})
            selector = RewardMarketSelector(gateway, RewardSelectionPolicy(
                max_book_candidates=cap), clock=lambda: NOW)
            selected = await selector.select(now=NOW)
            self.assertEqual(selected.selected.market.slug, "old")
            self.assertEqual(gateway.book_calls, ["old"] if cap == 1 else ["old", "daily"])
            self.assertEqual(gateway.incentive_symbol_calls, [("daily", "early-only", "old")])
            validated = await selector.revalidate_viable(["daily"], now=NOW)
            self.assertEqual([o.market.slug for o in validated], ["daily"])

    async def test_daily_rollback_and_disable_prevent_early_books(self):
        for policy in (RewardSelectionPolicy(daily_event_horizon_seconds=21600),
                       RewardSelectionPolicy(allow_daily_event_rewards=False)):
            gateway = FakeGateway({"daily": market("daily", starts_in_hours=24)},
                {"daily": incentive("daily", pool=1000, phase="daily_event")}, {})
            selector = RewardMarketSelector(gateway, policy, clock=lambda: NOW)
            self.assertIsNone((await selector.select(now=NOW)).selected)
            self.assertEqual(await selector.revalidate_viable(["daily"], now=NOW), ())
            self.assertEqual(gateway.book_calls, [])
            self.assertEqual(gateway.upcoming_calls[0][1], NOW+timedelta(hours=30))

    async def test_midnight_drops_cached_rewards_until_fresh_read(self):
        before = datetime(2026, 9, 19, 3, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=1)
        for revalidate in (False, True):
            moment = [before]
            meta = replace(market("daily"), game_start=after+timedelta(hours=12))
            rewards = replace(incentive("daily", pool=1000, phase="daily_event"),
                              observed_at=before)
            gateway = FakeGateway({"daily": meta}, {"daily": rewards}, {"daily": book("daily")})
            original = gateway.fetch_book
            async def delayed(slug):
                value = await original(slug)
                moment[0] = after
                return value
            gateway.fetch_book = delayed
            selector = RewardMarketSelector(gateway, clock=lambda: moment[0])
            if revalidate:
                self.assertEqual(await selector.revalidate_viable(["daily"], now=before), ())
            else:
                self.assertIsNone((await selector.select(now=before)).selected)
            gateway.incentives["daily"] = replace(rewards, observed_at=after)
            self.assertEqual(len(await selector.revalidate_viable(["daily"], now=after)), 1)

    async def test_daily_reward_rank_does_not_follow_headline_pool(self):
        gateway = FakeGateway({s: market(s, starts_in_hours=12) for s in ("small", "large")},
            {"small": incentive("small", pool=100, phase="daily_event"),
             "large": incentive("large", pool=1000, target=100000, phase="daily_event")},
            {"small": book("small"), "large": book("large", touch_size=100000)})
        result = await RewardMarketSelector(gateway, clock=lambda: NOW).select(now=NOW)
        self.assertEqual(result.selected.market.slug, "small")
        self.assertEqual(sum(bool(o.viable_sides) for o in result.opportunities), 2)

    def test_discovery_elapsed_horizon_and_invalid_policy(self):
        from zoneinfo import ZoneInfo
        now = datetime(2026, 11, 1, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(RewardSelectionPolicy().discovery_window_end(now),
                         now.astimezone(timezone.utc)+timedelta(hours=24))
        for value in (0, 86401, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                RewardSelectionPolicy(daily_event_horizon_seconds=value)


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


class PacedSelectionClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_only_selection_and_revalidation_obey_toggle(self):
        for enabled in (True, False):
            gateway = FakeGateway({"daily": market("daily")},
                {"daily": incentive("daily", pool=1000, phase="daily_event")},
                {"daily": book("daily")})
            selector = RewardMarketSelector(gateway, RewardSelectionPolicy(
                allow_daily_event_rewards=enabled), clock=lambda: NOW)
            selected = await selector.select(now=NOW)
            self.assertEqual(selected.selected is not None, enabled)
            self.assertEqual(bool(await selector.revalidate_viable(
                ["daily"], now=NOW)), enabled)
            self.assertEqual(len(gateway.book_calls), 2 if enabled else 0)

    async def test_day_of_wins_before_and_after_book_ranking(self):
        for cap in (1, 2):
            gateway = FakeGateway(
                {slug: market(slug) for slug in ("daily", "old")},
                {"daily": incentive("daily", pool=1_000_000, phase="daily_event"),
                 "old": incentive("old", pool=35)},
                {slug: book(slug) for slug in ("daily", "old")})
            selector = RewardMarketSelector(gateway, RewardSelectionPolicy(
                max_book_candidates=cap), clock=lambda: NOW)
            result = await selector.select(now=NOW)
            self.assertEqual(result.selected.market.slug, "old")
            self.assertEqual(gateway.book_calls[0], "old")
            self.assertEqual([o.market.slug for o in result.opportunities],
                             ["old"] if cap == 1 else ["old", "daily"])

    async def test_daily_cannot_rotate_ahead_of_day_of_book_capacity(self):
        for old_count in (2, 4):
            old = [f"old{i}" for i in range(old_count)]
            daily = [f"daily{i}" for i in range(4)]
            slugs = old + daily
            gateway = FakeGateway({s: market(s) for s in slugs},
                {s: incentive(s, pool=1000,
                    phase="day_of" if s in old else "daily_event") for s in slugs},
                {s: book(s) for s in slugs})
            selector = RewardMarketSelector(gateway, RewardSelectionPolicy(
                max_book_candidates=3), clock=lambda: NOW)
            seen = set()
            for _ in range(5):
                # Prior daily ownership must not take priority book capacity.
                selector._last_selected_slug = daily[-1]
                gateway.book_calls.clear()
                result = await selector.select(now=NOW)
                scanned = set(gateway.book_calls)
                seen.update(scanned)
                self.assertEqual(len(scanned), 3)
                self.assertEqual(len(scanned.intersection(old)), min(3, old_count))
                self.assertEqual(set(result.deferred_markets), set(slugs) - scanned)
            self.assertTrue(set(old).issubset(seen))

    async def test_daily_scan_cannot_extend_six_hour_window_or_survive_kickoff(self):
        gateway = FakeGateway({"early": market("early", starts_in_hours=7),
                               "live": market("live", starts_in_hours=0)},
            {s: incentive(s, pool=1000, phase="daily_event") for s in ("early", "live")}, {})
        result = await RewardMarketSelector(gateway, RewardSelectionPolicy(
            daily_event_horizon_seconds=21600), clock=lambda: NOW).select(now=NOW)
        self.assertIsNone(result.selected)
        self.assertEqual(gateway.book_calls, [])
        moment = [NOW]
        gateway = FakeGateway({"daily": market("daily")},
            {"daily": incentive("daily", pool=1000, phase="daily_event")},
            {"daily": book("daily")})
        original = gateway.fetch_book
        async def delayed(slug):
            result = await original(slug)
            moment[0] += timedelta(hours=1)
            return result
        gateway.fetch_book = delayed
        result = await RewardMarketSelector(gateway, clock=lambda: moment[0]).select(now=NOW)
        self.assertIsNone(result.selected)

    async def test_scan_and_revalidation_drop_games_started_during_book_wait(self):
        for revalidate in (False, True):
            moment = [NOW]
            gateway = FakeGateway({"one": market("one")},
                {"one": incentive("one", pool=20000)}, {"one": book("one")})
            original = gateway.fetch_book

            async def delayed(slug):
                result = await original(slug)
                moment[0] = NOW + timedelta(hours=1)
                return result

            gateway.fetch_book = delayed
            selector = RewardMarketSelector(gateway, RewardSelectionPolicy(), clock=lambda: moment[0])
            if revalidate:
                self.assertEqual(await selector.revalidate_viable(("one",), now=NOW), ())
            else:
                result = await selector.select(now=NOW)
                self.assertIsNone(result.selected)
                self.assertEqual(result.books_evaluated, 1)
                self.assertEqual(result.opportunities, ())

    async def test_scan_drops_reward_expired_while_reading_and_retains_future_game(self):
        moment = [NOW]
        gateway = FakeGateway({"one": market("one", starts_in_hours=2)},
            {"one": incentive("one", pool=20000, starts_at=NOW-timedelta(minutes=1),
                              ends_at=NOW+timedelta(seconds=10))}, {"one": book("one")})
        original = gateway.fetch_book

        async def delayed(slug):
            result = await original(slug)
            moment[0] = NOW + timedelta(seconds=10)
            return result

        gateway.fetch_book = delayed
        result = await RewardMarketSelector(gateway, RewardSelectionPolicy(),
            clock=lambda: moment[0]).select(now=NOW)
        self.assertIsNone(result.selected)


class OpportunityTests(unittest.TestCase):
    def test_opportunity_projection_is_bounded_by_explicit_program_end(self):
        reward = incentive(
            "short", pool=20_000,
            starts_at=NOW - timedelta(minutes=5),
            ends_at=NOW + timedelta(minutes=5))
        item = evaluate_market_opportunity(
            market("short", starts_in_hours=2),
            reward,
            book("short"),
            period=reward.periods[0],
            now=NOW,
            policy=RewardSelectionPolicy(),
        )

        for side in item.sides:
            self.assertTrue(side.projection.eligible)
            self.assertAlmostEqual(
                side.projection.maximum_remaining_reward,
                side.projection.projected_reward * 10,
            )

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

    def test_extreme_price_is_rejected_during_selection_not_after_binding(self):
        item = evaluate_market_opportunity(
            market("extreme"),
            incentive("extreme", pool=20_000),
            book("extreme", bid=.07, offer=.93),
            period=incentive("extreme", pool=20_000).periods[0],
            now=NOW,
            policy=RewardSelectionPolicy(),
        )

        self.assertEqual(item.viable_sides, ())
        self.assertEqual({side.blocker for side in item.sides},
                         {"price_too_extreme"})


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
    async def test_real_429_stops_selection_and_revalidation_after_one_book(self):
        for revalidate in (False, True):
            with self.subTest(revalidate=revalidate):
                slugs = ("one", "two", "three")
                gateway = FakeGateway(
                    {s: market(s) for s in slugs},
                    {s: incentive(s, pool=1000) for s in slugs},
                    {s: AdapterError("limited", status_code=429) for s in slugs})
                selector = RewardMarketSelector(gateway)
                with self.assertRaises(AdapterError):
                    if revalidate:
                        await selector.revalidate_viable(slugs, now=NOW)
                    else:
                        await selector.select(now=NOW)
                self.assertEqual(len(gateway.book_calls), 1)

    async def test_empty_reward_universe_returns_without_metadata_or_books(self):
        gateway = FakeGateway(
            {"non-rewarded": market("non-rewarded")},
            {},
            {"non-rewarded": book("non-rewarded")},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertIsNone(result.selected)
        self.assertEqual(result.catalog_markets, 1)
        self.assertEqual(result.executable_catalog_markets, 1)
        self.assertEqual(result.rewarded_executable_markets, 0)
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(gateway.incentive_symbol_calls, [("non-rewarded",)])
        self.assertEqual(gateway.book_calls, [])

    async def test_shared_cooldown_stops_scan_without_market_failures(self):
        deferred = ApiAdmissionDeferred(
            "background API cooldown active; request not sent",
            operation="markets.book(one)",
            error_code="LOCAL_API_COOLDOWN",
            retry_after_seconds=30.0,
        )
        gateway = FakeGateway(
            {slug: market(slug) for slug in ("one", "two", "three")},
            {slug: incentive(slug, pool=1000)
             for slug in ("one", "two", "three")},
            {slug: deferred for slug in ("one", "two", "three")},
        )

        with self.assertRaises(ApiAdmissionDeferred):
            await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(len(gateway.book_calls), 1)

    async def test_shared_cooldown_stops_after_bounded_event_discovery(self):
        deferred = ApiAdmissionDeferred(
            "background API cooldown active; request not sent",
            operation="incentives.list",
            error_code="LOCAL_API_COOLDOWN",
            retry_after_seconds=30.0,
        )
        gateway = FakeGateway(
            {"one": market("one")},
            {"one": incentive("one", pool=1000)},
            {"one": book("one")},
        )

        async def defer_incentives(symbols):
            gateway.incentive_symbol_calls.append(tuple(symbols))
            raise deferred

        gateway.fetch_incentives = defer_incentives

        with self.assertRaises(ApiAdmissionDeferred):
            await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(gateway.incentive_symbol_calls, [("one",)])
        self.assertEqual(len(gateway.upcoming_calls), 1)
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(gateway.book_calls, [])

    async def test_real_book_failure_remains_one_market_failure(self):
        gateway = FakeGateway(
            {slug: market(slug) for slug in ("broken", "healthy")},
            {slug: incentive(slug, pool=1000)
             for slug in ("broken", "healthy")},
            {
                "broken": AdapterError("malformed book"),
                "healthy": book("healthy"),
            },
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.failures, ("broken:book:AdapterError",))
        self.assertEqual(reward_market_slugs(result), ("healthy",))

    async def test_shared_cooldown_stops_cached_revalidation(self):
        deferred = ApiAdmissionDeferred(
            "entry API cooldown active; request not sent",
            operation="markets.book(one)",
            error_code="LOCAL_API_COOLDOWN",
            retry_after_seconds=30.0,
        )
        gateway = FakeGateway(
            {slug: market(slug) for slug in ("one", "two")},
            {slug: incentive(slug, pool=1000) for slug in ("one", "two")},
            {slug: deferred for slug in ("one", "two")},
        )

        with self.assertRaises(ApiAdmissionDeferred):
            await RewardMarketSelector(gateway).revalidate_viable(
                ("one", "two"), now=NOW)

        self.assertEqual(gateway.book_calls, ["one"])

    async def test_cached_candidates_are_freshly_revalidated_without_catalog_scan(self):
        fresh = market("fresh")
        closed = MarketMetadata(
            **{**market("closed").__dict__, "closed": True})
        tight = market("tight", catalog_bid=.495, catalog_offer=.505)
        gateway = FakeGateway(
            {item.slug: item for item in (fresh, closed, tight)},
            {slug: incentive(slug, pool=1000)
             for slug in ("fresh", "closed", "tight")},
            {slug: book(slug) for slug in ("fresh", "closed", "tight")},
        )
        selector = RewardMarketSelector(gateway)

        result = await selector.revalidate_viable(
            ("fresh", "closed", "tight"), now=NOW)

        self.assertEqual(
            tuple(item.market.slug for item in result), ("fresh",))
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(
            gateway.metadata_calls, [("fresh", "closed", "tight")])
        self.assertEqual(
            gateway.incentive_symbol_calls,
            [("fresh", "closed", "tight")])
        self.assertEqual(gateway.book_calls, ["fresh"])
        self.assertEqual(gateway.traffic_classes, ["entry"])

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
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(len(gateway.upcoming_calls), 1)
        self.assertEqual(gateway.incentive_universe_calls, [])
        self.assertEqual(gateway.incentive_symbol_calls, [("high", "low")])

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

    async def test_minimum_quantity_equal_to_authorized_quote_size_is_included(self):
        sized = MarketMetadata(
            **{**market("sized").__dict__, "minimum_trade_qty": 10})
        gateway = FakeGateway(
            {"sized": sized},
            {"sized": incentive("sized", pool=20_000)},
            {"sized": book("sized")},
        )

        result = await RewardMarketSelector(
            gateway, RewardSelectionPolicy(quote_size=10)).select(now=NOW)

        self.assertEqual(reward_market_slugs(result), ("sized",))

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
        self.assertEqual(gateway.incentive_symbol_calls, [("tournament",)])
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

    async def test_reward_program_filter_precedes_book_fanout(self):
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
        self.assertEqual(result.executable_catalog_markets, 2)
        self.assertEqual(gateway.incentive_universe_calls, [])
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(gateway.incentive_symbol_calls, [("early", "valid")])
        self.assertEqual(gateway.book_calls, ["valid"])

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
        self.assertEqual(gateway.incentive_universe_calls, [])
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(gateway.incentive_symbol_calls, [("moneyline",)])
        self.assertEqual(gateway.book_calls, ["moneyline"])

    async def test_bounded_event_discovery_filters_before_reward_lookup(self):
        markets = {
            f"market-{index:03d}": market(f"market-{index:03d}")
            for index in range(205)
        }
        gateway = FakeGateway(
            markets,
            {slug: incentive(slug, pool=20_000) for slug in markets},
            {slug: book(slug) for slug in markets},
        )
        selector = RewardMarketSelector(
            gateway, RewardSelectionPolicy(max_book_candidates=1))

        result = await selector.select(now=NOW)

        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(len(gateway.upcoming_calls), 1)
        (discovery_start, discovery_end, discovery_limit,
         require_complete) = gateway.upcoming_calls[0]
        self.assertEqual(discovery_start, NOW - timedelta(days=1))
        self.assertEqual(discovery_end, NOW + timedelta(days=2))
        self.assertEqual(discovery_limit, 500)
        self.assertTrue(require_complete)
        self.assertEqual(gateway.incentive_universe_calls, [])
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(len(gateway.incentive_symbol_calls), 1)
        self.assertEqual(len(gateway.incentive_symbol_calls[0]), 205)
        self.assertEqual(result.catalog_markets, 205)
        self.assertEqual(result.event_pages_used, 1)
        self.assertEqual(result.rewarded_executable_markets, 205)
        self.assertEqual(result.books_evaluated, 1)

    async def test_missing_exact_market_metadata_never_authorizes_a_reward(self):
        gateway = FakeGateway(
            {"present": market("present")},
            {
                "missing": incentive("missing", pool=30_000),
                "present": incentive("present", pool=20_000),
            },
            {"present": book("present")},
        )

        result = await RewardMarketSelector(gateway).select(now=NOW)

        self.assertEqual(result.catalog_markets, 1)
        self.assertEqual(result.rewarded_executable_markets, 1)
        self.assertEqual(reward_market_slugs(result), ("present",))
        self.assertEqual(gateway.book_calls, ["present"])

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
