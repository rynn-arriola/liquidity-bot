"""BC-2026-10-03-A: live (in-game) reward farming behind a strategy switch."""
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from liquidity_bot.market_selector import (
    RewardMarketSelector, RewardSelectionPolicy, evaluate_market_opportunity)
from liquidity_bot.models import BookLevel, BookSnapshot
from liquidity_bot.event_guard import event_ended
from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler
from liquidity_bot.rewards import IncentivePeriod, project_order_reward
from liquidity_bot.strategy import (
    LIVE_JUMPY_BLOCKER, LIVE_PRICE_BLOCKER, LivePolicy, StrategyPolicy, load_strategy_policy)
from test_market_selector import NOW, FakeGateway, book, incentive, market
import test_reconciler as rec
import test_live_commissioning as live

LIVE_ONLY = StrategyPolicy(pregame_enabled=False, live_enabled=True)
BOTH = StrategyPolicy(pregame_enabled=True, live_enabled=True)
SLUG = "aec-setkameua-live-game-2026-08-13"


def started(slug=SLUG, minutes_ago=20, *, in_play=True, checked_ago=5, **kw):
    m = market(slug, **kw)
    return replace(m, game_start=NOW - timedelta(minutes=minutes_ago),
                   event_guard_required=True, event_slug=slug.removeprefix("aec-"),
                   event_checked_at=NOW - timedelta(seconds=checked_ago),
                   event_started_at=NOW - timedelta(minutes=minutes_ago),
                   event_in_play=in_play)


def live_period(pool=300.0):
    return IncentivePeriod("reward-live", "live", "active", pool, 0.5, 10.0)


class StrategyPolicyTests(unittest.TestCase):
    def test_missing_file_is_the_pregame_only_bot(self):
        policy = load_strategy_policy(Path("/nonexistent/strategy.json"))
        self.assertEqual((policy.pregame_enabled, policy.live_enabled), (True, False))
        self.assertEqual(policy.label, "pregame")

    def test_checked_in_switch_is_the_operator_decision(self):
        # Operator 2026-10-03: "Turn off everything and focus on the new
        # version ... live", "dont remove pre-game we should be able to switch it".
        shipped = load_strategy_policy()
        self.assertEqual((shipped.pregame_enabled, shipped.live_enabled), (False, True))
        self.assertEqual(shipped.live.entry_lifetime_seconds, 60)
        self.assertEqual(shipped.live.max_inventory_age_seconds, 120)
        self.assertEqual(shipped.live.min_price, 0.15)
        self.assertEqual(shipped.live.min_entry_spread, 0.01)            # "Ok we can do 1c"
        self.assertEqual(shipped.live.jumpy_touch_changes_30s, 3)

    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            for bad in ({"live_enabled": "yes"}, {"surprise": 1},
                        {"live": {"min_price": 0.6}}, {"live": {"entry_lifetime_seconds": 5}},
                        {"live": {"jumpy_touch_changes_30s": 2.5}}, {"live": {"other": 1}}):
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                    load_strategy_policy(path)

    def test_entry_phase_and_evidence(self):
        pregame = replace(market(SLUG), event_guard_required=True,
                          event_checked_at=NOW - timedelta(seconds=5), event_not_started=True)
        self.assertEqual(LIVE_ONLY.entry_phase(pregame, NOW), None)
        self.assertFalse(LIVE_ONLY.allows_entry(pregame, NOW))
        self.assertTrue(BOTH.allows_entry(pregame, NOW))
        self.assertTrue(StrategyPolicy().allows_entry(pregame, NOW))
        self.assertTrue(LIVE_ONLY.allows_entry(started(), NOW))
        self.assertFalse(LIVE_ONLY.allows_entry(started(in_play=False), NOW))   # ended
        self.assertFalse(LIVE_ONLY.allows_entry(started(checked_ago=31), NOW))  # stale
        self.assertFalse(StrategyPolicy().allows_entry(started(), NOW))         # pregame bot


class EventEvidenceTests(unittest.TestCase):
    def test_ended_detection(self):
        self.assertTrue(event_ended({"ended": True}))
        self.assertTrue(event_ended({"eventState": {"period": "FT"}}))
        self.assertTrue(event_ended({"metadata": {"gameState": {"ended": True}}}))
        self.assertFalse(event_ended({"live": True, "eventState": {"period": "Q3"}}))

    def test_gateway_marks_in_play_and_ended(self):
        from liquidity_bot.event_guard import EventAwareGateway
        gw = object.__new__(EventAwareGateway)
        gw._event_now = lambda: NOW
        gw._event_ledger = None
        gw._started, gw._parent, gw._event_status, gw._event_members = {}, {}, {}, {}
        slug = SLUG
        event = {"slug": "live-game", "live": True, "markets": [{"slug": slug}]}
        gw._consume_events([event], NOW)
        m = gw.apply_event_status(market(slug))
        self.assertTrue(m.event_in_play and m.event_started_at == NOW)
        self.assertTrue(gw.event_live_entry_allowed(slug))
        self.assertFalse(gw.event_entry_allowed(slug))
        gw._consume_events([dict(event, live=False, ended=True)], NOW + timedelta(seconds=1))
        self.assertFalse(gw.apply_event_status(market(slug)).event_in_play)
        self.assertFalse(gw.event_live_entry_allowed(slug))


class LiveRewardTests(unittest.TestCase):
    def test_live_period_projects_only_with_an_assumed_length(self):
        kwargs = dict(side="bid", price=.49, size=10, levels=book(SLUG).levels("bid"),
                      incentive=live_period(), tick_size=.01,
                      event_start=NOW - timedelta(minutes=20), now=NOW,
                      horizon_seconds=30, minimum_payout=0.0)
        self.assertEqual(project_order_reward(**kwargs).reason, "period_duration_unproven")
        projected = project_order_reward(**kwargs, live_period_seconds=14400)
        self.assertTrue(projected.eligible)
        self.assertGreater(projected.projected_reward, 0)
        # The whole remaining assumed period is enterable (no kickoff buffer).
        self.assertAlmostEqual(projected.remaining_entry_reward,
                               projected.projected_reward / 30 * (14400 - 1200), places=6)
        late = project_order_reward(**dict(kwargs, now=NOW + timedelta(hours=4)),
                                    live_period_seconds=14400)
        self.assertFalse(late.eligible)

    def test_selector_picks_in_play_markets_on_their_live_row_only(self):
        m = started()
        inc = replace(incentive(SLUG, pool=300), periods=(live_period(),))
        live_selector = RewardMarketSelector(
            FakeGateway({SLUG: m}, {SLUG: inc}, {SLUG: book(SLUG)}),
            RewardSelectionPolicy(quote_size=10, strategy=LIVE_ONLY))
        pregame_selector = RewardMarketSelector(
            FakeGateway({SLUG: m}, {SLUG: inc}, {SLUG: book(SLUG)}),
            RewardSelectionPolicy(quote_size=10))
        end = NOW + timedelta(hours=6)
        self.assertTrue(live_selector._catalog_market_is_executable(m, now=NOW, window_end=end))
        self.assertFalse(pregame_selector._catalog_market_is_executable(m, now=NOW, window_end=end))
        self.assertEqual(live_selector._applicable_period(m, inc, NOW).period, "live")
        self.assertIsNone(pregame_selector._applicable_period(m, inc, NOW))
        ended = started(in_play=False)
        self.assertFalse(live_selector._catalog_market_is_executable(ended, now=NOW, window_end=end))
        self.assertIsNone(live_selector._applicable_period(ended, inc, NOW))
        # Pregame switched off: an upcoming game is not selected.
        upcoming = replace(market(SLUG), event_guard_required=True,
                           event_checked_at=NOW, event_not_started=True)
        day_of = incentive(SLUG, pool=300)
        self.assertFalse(live_selector._catalog_market_is_executable(upcoming, now=NOW, window_end=end))
        self.assertIsNone(live_selector._applicable_period(upcoming, day_of, NOW))

    def test_live_price_band_in_selection(self):
        m = started()
        policy = RewardSelectionPolicy(quote_size=10, strategy=LIVE_ONLY)
        edge = book(SLUG, bid=.13, offer=.16)
        result = evaluate_market_opportunity(
            m, replace(incentive(SLUG, pool=300), periods=(live_period(),)), edge,
            period=live_period(), now=NOW, policy=policy)
        blockers = {s.side: s.blocker for s in result.sides}
        self.assertEqual(blockers["bid"], "price_too_extreme")       # 0.13 < 0.15


    def test_live_markets_accept_a_one_cent_spread_pregame_keeps_two(self):
        one_cent = book(SLUG, bid=.49, offer=.50)
        live_inc = replace(incentive(SLUG, pool=300), periods=(live_period(),))
        live_policy = RewardSelectionPolicy(
            quote_size=10, strategy=replace(LIVE_ONLY, live=LivePolicy(min_entry_spread=0.01)))
        result = evaluate_market_opportunity(started(), live_inc, one_cent, period=live_period(),
                                             now=NOW, policy=live_policy)
        self.assertNotIn("spread_below_entry_minimum", {s.blocker for s in result.sides})
        pregame = evaluate_market_opportunity(
            market(SLUG), incentive(SLUG, pool=300), one_cent,
            period=incentive(SLUG, pool=300).periods[0], now=NOW,
            policy=RewardSelectionPolicy(quote_size=10, strategy=BOTH))
        self.assertEqual({s.blocker for s in pregame.sides}, {"spread_below_entry_minimum"})
        selector = RewardMarketSelector(FakeGateway({}, {}, {}), live_policy, clock=lambda: NOW)
        tight = replace(started(), catalog_best_bid=.49, catalog_best_offer=.50)
        self.assertTrue(selector._catalog_spread_can_enter(tight))
        self.assertFalse(RewardMarketSelector(FakeGateway({}, {}, {}), RewardSelectionPolicy(
            quote_size=10), clock=lambda: NOW)._catalog_spread_can_enter(tight))


class LiveExitTests(unittest.TestCase):
    def reconciler(self):
        return DesiredStateReconciler(rec.policy(age=900, live_max_inventory_age_seconds=120))

    def live_frame(self, *, opened_ago, metadata=None, snapshot=None, **kw):
        now = rec.T0 + timedelta(hours=6)          # an hour after the fixture's kickoff
        f = rec.frame(now=now, position="2", opened=now - timedelta(seconds=opened_ago),
                      entry=.49, metadata=metadata, snapshot=snapshot or rec.book(now), **kw)
        return replace(f, live_market=True)

    def test_passive_exit_until_the_live_clock_then_cross_without_deferral(self):
        early = self.reconciler().plan(self.live_frame(opened_ago=60))
        self.assertTrue(early and all(a.kind != ActionKind.PLACE_IOC for a in early))
        self.assertTrue(any(a.kind == ActionKind.PLACE_POST_ONLY and a.side == "offer"
                            for a in early))
        # A deep loss at 121 s still crosses: no early-exit deferral live.
        cheap = rec.book(rec.T0 + timedelta(hours=6), bid=.30, offer=.32)
        due = self.reconciler().plan(self.live_frame(opened_ago=121, snapshot=cheap))
        self.assertEqual(due[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(due[0].reason, "hard_inventory_timeout")

    def test_game_over_forces_the_exit_at_once(self):
        closed = replace(rec.market(), active=False)
        actions = self.reconciler().plan(self.live_frame(opened_ago=10, metadata=closed))
        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)

    def test_pregame_frame_after_kickoff_is_unchanged(self):
        now = rec.T0 + timedelta(hours=6)
        f = rec.frame(now=now, position="2", opened=now - timedelta(seconds=10), entry=.49,
                      snapshot=rec.book(now))
        self.assertEqual(self.reconciler().plan(f)[0].kind, ActionKind.PLACE_IOC)

    def test_live_market_quotes_after_kickoff_but_pregame_does_not(self):
        now = rec.T0 + timedelta(hours=6)
        flat = rec.frame(now=now, snapshot=rec.book(now))
        self.assertFalse(any(a.kind == ActionKind.PLACE_POST_ONLY
                             for a in self.reconciler().plan(flat)))
        live_actions = self.reconciler().plan(replace(flat, live_market=True))
        self.assertEqual({a.side for a in live_actions
                          if a.kind == ActionKind.PLACE_POST_ONLY}, {"bid", "offer"})


class LiveRunnerTests(unittest.IsolatedAsyncioTestCase):
    setUp = live.LiveCommissioningTests.setUp
    build_runner = live.LiveCommissioningTests.build_runner

    async def live_runner(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        runner.config = replace(runner.config, strategy=LIVE_ONLY)
        state = runner.state_for("market-one")
        state.market = replace(state.market, game_start=live.NOW - timedelta(minutes=30),
                               event_guard_required=False)
        state.incentive = replace(state.incentive, periods=(live_period(),))
        return runner, state

    async def test_in_play_market_is_evaluated_on_its_live_row(self):
        runner, state = await self.live_runner()
        runner._evaluate_rewards(live.NOW, "market-one")
        self.assertNotIn("period_is_live", state.economic_blocker_by_side.values())
        self.assertTrue(any(net > 0 for net in state.expected_net_by_side.values()))
        self.assertIsNone(runner._live_entry_blocker("market-one", live.NOW))
        self.assertTrue(runner._frame(live.NOW).live_market)

    async def test_pregame_bot_still_refuses_live_rows(self):
        runner, state = await self.live_runner()
        runner.config = replace(runner.config, strategy=StrategyPolicy())
        runner._evaluate_rewards(live.NOW, "market-one")
        self.assertEqual(set(state.economic_blocker_by_side.values()), {"period_is_live"})
        self.assertFalse(runner._frame(live.NOW).live_market)

    async def test_jumpy_or_extreme_live_market_blocks_new_entries(self):
        runner, state = await self.live_runner()
        for i, (bid, offer) in enumerate(((.48, .50), (.49, .51), (.47, .49), (.48, .50))):
            state.observe_book_touch(BookSnapshot(
                "market-one", (BookLevel(bid, 50),), (BookLevel(offer, 50),)),
                observed_at=live.NOW + timedelta(seconds=1 + i))
        busy = live.NOW + timedelta(seconds=5)
        self.assertEqual(runner._live_entry_blocker("market-one", busy), LIVE_JUMPY_BLOCKER)
        self.assertFalse(runner._frame(busy).new_risk_allowed)
        calm = busy + timedelta(seconds=31)
        self.assertIsNone(runner._live_entry_blocker("market-one", calm))
        state.book = BookSnapshot("market-one", (BookLevel(.08, 50),), (BookLevel(.10, 50),))
        self.assertEqual(runner._live_entry_blocker("market-one", calm), LIVE_PRICE_BLOCKER)


class LiveOrderLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_entries_get_the_short_lifetime_and_pregame_keeps_its_cutoff(self):
        from liquidity_bot.entry_expiry import EntryExpiryPolicy
        from liquidity_bot.execution_ledger import ExecutionLedger
        from liquidity_bot.live_session import ControlledActionDriver
        from liquidity_bot.reconciler import PlanAction
        from test_live_session import Executor, market as session_market, NOW as T
        with tempfile.TemporaryDirectory() as d:
            ledger = ExecutionLedger(Path(d) / "ledger.db")
            try:
                executor = Executor()
                action = PlanAction(ActionKind.PLACE_POST_ONLY, "market-one", "test",
                                    side="bid", price=.5, qty=Decimal("1"))
                in_play = replace(session_market(), game_start=T - timedelta(minutes=10))
                live_driver = ControlledActionDriver(
                    executor, ledger, entry_expiry_policy=EntryExpiryPolicy(True, 600),
                    strategy=LIVE_ONLY)
                await live_driver.apply_one([action], market=in_play,
                                            position=Decimal(0), now=T)
                self.assertEqual(executor.places[-1][0].good_till_time,
                                 T + timedelta(seconds=60))
                pregame_driver = ControlledActionDriver(
                    executor, ledger, entry_expiry_policy=EntryExpiryPolicy(True, 600))
                result = await pregame_driver.apply_one([action], market=in_play,
                                                        position=Decimal(0), now=T)
                self.assertEqual(result.action.reason, "entry_expiry_pregame_cutoff")
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
