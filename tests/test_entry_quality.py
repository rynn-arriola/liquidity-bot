from argparse import Namespace
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from liquidity_bot.entry_quality import EntryQualityPolicy, load_entry_quality_policy
from liquidity_bot.models import BookLevel, BookSnapshot
from liquidity_bot.shadow_math import cross_exit
from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler, PlanAction, WorkingOrder
from liquidity_bot.market_selector import RewardSelectionPolicy, evaluate_market_opportunity
from liquidity_bot.live_commissioning import _run
from liquidity_bot.commissioning import CommissioningRefused
import test_reconciler as recon
import test_market_selector as selector
import test_live_commissioning as live


def cliff(slug='market-one'):
    return BookSnapshot(slug,
        (BookLevel(.49, 100), BookLevel(.05, 100)),
        (BookLevel(.51, 100), BookLevel(.52, 100)))


class EntryQualityTests(unittest.TestCase):
    def test_config_enabled_for_operator_authorized_trial(self):
        p = load_entry_quality_policy()
        self.assertTrue(p.enabled)
        self.assertEqual(p.minimum_retained_value, .7)

    def test_strict_restart_scoped_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quality.json'
            for bad in ('{}', '[]', '{',
                '{"enabled":false,"enabled":true,"minimum_retained_value":0.7}',
                *[json.dumps(dict(enabled=e, minimum_retained_value=v)) for e,v in
                  [('false',.7),(1,.7),(True,0),(True,1.1),(True,-.2),
                   (False,float('nan')),(True,float('inf')),(True,True),(True,'0.7')]]):
                path.write_text(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_entry_quality_policy(path)
            path.write_text('{"enabled":true,"minimum_retained_value":0.8}')
            loaded = load_entry_quality_policy(path)
            path.write_text('{"enabled":false,"minimum_retained_value":0.7}')
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.minimum_retained_value,.8)
            self.assertFalse(load_entry_quality_policy(path).enabled)
            with self.assertRaises(ValueError):
                load_entry_quality_policy(Path(directory)/'missing.json')

    def test_boundary_unknown_and_quantity_scaling(self):
        p = EntryQualityPolicy(True,.7)
        for size in (5,10,13,30):
            result = cross_exit(cliff(),entry_side='bid',entry_price=.49,qty=size,
                depth_haircut=.5,exclude_entry_level=True)
            self.assertEqual(p.blocker(result),'entry_quality_below_minimum')
            self.assertIsNone(EntryQualityPolicy(False,.7).blocker(result))
            exact = replace(result,pnl=-.3*result.entry_cost)
            self.assertIsNone(p.blocker(exact))
            self.assertEqual(p.blocker(replace(exact,pnl=exact.pnl-0.000001)),
                'entry_quality_below_minimum')
            for invalid in (replace(result,complete=False),replace(result,entry_cost=0),
                replace(result,pnl=float('nan')),replace(result,pnl=float('inf'))):
                self.assertEqual(p.blocker(invalid),'entry_quality_unknown')

    def test_reconciler_filters_one_side_and_cancels_only_owned_bad_entry(self):
        r = DesiredStateReconciler(recon.policy(entry_quality_policy=EntryQualityPolicy(True)))
        f = recon.frame(snapshot=cliff())
        self.assertEqual([a.side for a in r.plan(f)],['offer'])
        orders = (WorkingOrder('bad','bid',.49,Decimal('2')),
                  WorkingOrder('good','offer',.51,Decimal('2')))
        actions = r.plan(replace(f,working_orders=orders))
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].order_id,'bad')
        self.assertEqual(actions[0].reason,'side_no_longer_desired:entry_quality_below_minimum')
        disabled = DesiredStateReconciler(recon.policy())
        self.assertEqual({a.side for a in disabled.plan(f)},{'bid','offer'})

    def test_quality_cancel_requires_stable_recovery_before_reentry(self):
        r = DesiredStateReconciler(recon.policy(
            entry_quality_policy=EntryQualityPolicy(True),
            entry_quality_recovery_seconds=60))
        bad_order = WorkingOrder('bad','bid',.49,Decimal('2'))
        bad = recon.frame(snapshot=cliff(),orders=(bad_order,))

        cancelled = r.plan(bad)
        self.assertEqual(len(cancelled),1)
        self.assertEqual(
            cancelled[0].reason,
            'side_no_longer_desired:entry_quality_below_minimum')

        first_good_at = recon.T0 + timedelta(seconds=1)
        first_good = recon.frame(
            now=first_good_at, snapshot=recon.book(first_good_at))
        self.assertNotIn('bid',{a.side for a in r.plan(first_good)})

        almost_ready_at = first_good_at + timedelta(seconds=59)
        almost_ready = recon.frame(
            now=almost_ready_at, snapshot=recon.book(almost_ready_at))
        self.assertNotIn('bid',{a.side for a in r.plan(almost_ready)})

        ready_at = first_good_at + timedelta(seconds=60)
        ready = recon.frame(now=ready_at,snapshot=recon.book(ready_at))
        self.assertIn('bid',{a.side for a in r.plan(ready)})

    def test_quality_recovery_must_remain_continuously_healthy(self):
        r = DesiredStateReconciler(recon.policy(
            entry_quality_policy=EntryQualityPolicy(True),
            entry_quality_recovery_seconds=60))
        r.plan(recon.frame(
            snapshot=cliff(),
            orders=(WorkingOrder('bad','bid',.49,Decimal('2')),)))

        first_good_at = recon.T0 + timedelta(seconds=1)
        r.plan(recon.frame(
            now=first_good_at, snapshot=recon.book(first_good_at)))
        failed_again_at = recon.T0 + timedelta(seconds=30)
        r.plan(recon.frame(now=failed_again_at,snapshot=cliff()))
        recovered_again_at = recon.T0 + timedelta(seconds=31)
        r.plan(recon.frame(
            now=recovered_again_at,
            snapshot=recon.book(recovered_again_at)))

        old_deadline_at = recon.T0 + timedelta(seconds=61)
        old_deadline = recon.frame(
            now=old_deadline_at,snapshot=recon.book(old_deadline_at))
        self.assertNotIn('bid',{a.side for a in r.plan(old_deadline)})
        new_deadline_at = recon.T0 + timedelta(seconds=91)
        new_deadline = recon.frame(
            now=new_deadline_at,snapshot=recon.book(new_deadline_at))
        self.assertIn('bid',{a.side for a in r.plan(new_deadline)})

    def test_unknown_state_resets_quality_recovery_clock(self):
        r = DesiredStateReconciler(recon.policy(
            entry_quality_policy=EntryQualityPolicy(True),
            entry_quality_recovery_seconds=60))
        r.plan(recon.frame(
            snapshot=cliff(),
            orders=(WorkingOrder('bad','bid',.49,Decimal('2')),)))
        first_good_at = recon.T0 + timedelta(seconds=1)
        r.plan(recon.frame(
            now=first_good_at,snapshot=recon.book(first_good_at)))
        unknown_at = recon.T0 + timedelta(seconds=30)
        r.plan(recon.frame(
            now=unknown_at,snapshot=recon.book(unknown_at),state_known=False))

        recovered_at = recon.T0 + timedelta(seconds=61)
        recovered = recon.frame(
            now=recovered_at,snapshot=recon.book(recovered_at))
        self.assertNotIn('bid',{a.side for a in r.plan(recovered)})

    def test_position_lifecycle_clears_flat_quality_recovery(self):
        r = DesiredStateReconciler(recon.policy(
            entry_quality_policy=EntryQualityPolicy(True),
            entry_quality_recovery_seconds=60))
        r.plan(recon.frame(
            snapshot=cliff(),
            orders=(WorkingOrder('bad','bid',.49,Decimal('2')),)))
        position_at = recon.T0 + timedelta(seconds=1)
        r.plan(recon.frame(
            now=position_at,position='2',entry=.49,opened=position_at,
            snapshot=recon.book(position_at)))
        flat_at = recon.T0 + timedelta(seconds=2)

        actions = r.plan(recon.frame(
            now=flat_at,snapshot=recon.book(flat_at)))

        self.assertIn('bid',{a.side for a in actions})

    def test_quality_reason_wins_over_its_expected_net_zero_marker(self):
        r = DesiredStateReconciler(recon.policy(
            entry_quality_policy=EntryQualityPolicy(True)))
        f = recon.frame(
            snapshot=cliff(),
            orders=(WorkingOrder('bad','bid',.49,Decimal('2')),))
        f.expected_net_by_side['bid'] = 0.0

        actions = r.plan(f)

        self.assertEqual(actions[0].reason,
            'side_no_longer_desired:entry_quality_below_minimum')

    def test_never_resting_quality_candidate_enters_on_first_healthy_frame(self):
        r = DesiredStateReconciler(recon.policy(
            entry_quality_policy=EntryQualityPolicy(True),
            entry_quality_recovery_seconds=60))
        r.plan(recon.frame(snapshot=cliff()))
        good_at = recon.T0 + timedelta(seconds=1)

        actions = r.plan(recon.frame(
            now=good_at,snapshot=recon.book(good_at)))

        self.assertEqual({a.side for a in actions},{'bid','offer'})

    def test_quality_refused_side_does_not_consume_candidate_risk_budget(self):
        r = DesiredStateReconciler(recon.policy(
            max_market_worst_loss=1,
            max_portfolio_worst_loss=15,
            entry_quality_policy=EntryQualityPolicy(True)))

        actions = r.plan(recon.frame(snapshot=cliff()))

        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].kind,ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side,'offer')

    def test_reducers_ioc_shutdown_and_existing_gates_are_unchanged(self):
        enabled = recon.policy(age=900,entry_quality_policy=EntryQualityPolicy(True,1))
        disabled = replace(enabled,entry_quality_policy=EntryQualityPolicy())
        cases = [
            recon.frame(position='2',entry=.49,opened=recon.T0,snapshot=cliff()),
            recon.frame(position='-2',entry=.51,opened=recon.T0,snapshot=cliff()),
            recon.frame(position='2',entry=.49,opened=recon.T0-timedelta(seconds=901),snapshot=cliff()),
            recon.frame(position='2',entry=.49,opened=recon.T0,snapshot=cliff(),maintenance_flatten_required=True),
            recon.frame(state_known=False),
            recon.frame(new_risk_allowed=False),
            recon.frame(now=recon.T0+timedelta(hours=5)),
        ]
        for f in cases:
            self.assertEqual(DesiredStateReconciler(enabled).plan(f),
                             DesiredStateReconciler(disabled).plan(f))

    def test_selector_excludes_bad_side_even_with_large_reward(self):
        reward = selector.incentive('market-one',pool=1000000)
        args = dict(market=selector.market('market-one'),incentive=reward,
            book=cliff(),period=reward.periods[0],now=selector.NOW)
        enabled = evaluate_market_opportunity(**args, policy=RewardSelectionPolicy(
            quote_size=10,entry_quality_policy=EntryQualityPolicy(True)))
        self.assertEqual(enabled.viable_sides,('offer',))
        self.assertEqual(enabled.sides[0].blocker,'entry_quality_below_minimum')
        disabled = evaluate_market_opportunity(**args,policy=RewardSelectionPolicy(quote_size=10))
        self.assertEqual(disabled.viable_sides,('bid','offer'))


class EntryQualityLiveTests(unittest.IsolatedAsyncioTestCase):
    setUp = live.LiveCommissioningTests.setUp
    build_runner = live.LiveCommissioningTests.build_runner

    async def prepared(self):
        runner,client,_ = self.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        runner._evaluate_rewards(live.NOW,'market-one')
        return runner,client

    async def test_observation_recomputes_actual_price_size_and_book(self):
        runner,client = await self.prepared()
        state = runner.state_for('market-one')
        cached = state.stressed_exit_by_side['bid']
        state.book = cliff()
        state.book_observed_at = live.NOW
        action = PlanAction(ActionKind.PLACE_POST_ONLY,'market-one','test',
            side='bid',price=.48,qty=Decimal('7'))
        reads = client.markets.book_calls
        observation = runner._quote_observation_for_action(state,action,live.NOW,Decimal(0))
        expected = cross_exit(state.book,entry_side='bid',entry_price=.48,qty=7,
            depth_haircut=.5,exclude_entry_level=True)
        self.assertIsNotNone(observation)
        self.assertEqual(observation.stressed_exit_pnl,expected.pnl)
        self.assertAlmostEqual(observation.retained_value_ratio,
            (expected.entry_cost+expected.pnl)/expected.entry_cost)
        self.assertEqual(state.stressed_exit_by_side['bid'],cached)
        self.assertEqual(reads,client.markets.book_calls)

    async def test_live_policy_wiring_and_bad_action_recheck(self):
        runner,client = await self.prepared()
        runner.config = replace(runner.config,entry_quality_policy=EntryQualityPolicy(True))
        self.assertTrue(runner.config.reward_selection_policy().entry_quality_policy.enabled)
        state = runner.state_for('market-one')
        state.book = cliff()
        state.book_observed_at = live.NOW
        action = PlanAction(ActionKind.PLACE_POST_ONLY,'market-one','test',
            side='bid',price=.49,qty=runner.config.quote_size)
        with patch.object(runner.reconciler,'plan',return_value=[action]), patch.object(
                runner.driver,'apply_one') as apply:
            result,delay = await runner._decide_for_market('market-one',live.NOW)
        apply.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(delay,1)
        self.assertEqual(runner.ledger.query("SELECT * FROM execution_intents WHERE operation='PLACE'"),[])

    async def test_enabled_policy_ignores_forged_shadow_and_keeps_normal_entry(self):
        runner,client = await self.prepared()
        runner.config = replace(runner.config,entry_quality_policy=EntryQualityPolicy(True))
        runner.reconciler = DesiredStateReconciler(replace(runner.reconciler.policy,
            entry_quality_policy=runner.config.entry_quality_policy))
        original = runner._quote_observation_for_action
        def forged(*args,**kwargs):
            return replace(original(*args,**kwargs),retained_value_ratio=.01,
                would_refuse_retained_70=True)
        with patch.object(runner,'_quote_observation_for_action',side_effect=forged):
            outcome,delay = await runner._decide_for_market('market-one',live.NOW)
        self.assertIsNone(outcome)
        self.assertEqual(delay,0)
        self.assertEqual(len(client.orders.create_calls),1)

    async def test_runner_constructor_passes_same_restart_policy_to_reconciler(self):
        runner,client,_ = self.build_runner()
        quality = EntryQualityPolicy(True,.8)
        with patch('liquidity_bot.live_commissioning.ReconcilerPolicy',
                   wraps=recon.ReconcilerPolicy) as policy_type:
            live.CommissioningRunner(client,replace(runner.config,entry_quality_policy=quality),
                runner.ledger,live.FakeNotifier(),clock=lambda:live.NOW,
                market_stream_factory=live.FakeMarketStream,
                private_stream_factory=live.FakePrivateStream,
                operator_entry_pause_path=runner.ledger.path.parent/'pause-test.json',
                maintenance_cache_path=runner.ledger.path.parent/'maintenance-test.json')
        self.assertIs(policy_type.call_args.kwargs['entry_quality_policy'],quality)

    async def test_bad_quality_is_replaceable_but_unknown_is_not(self):
        runner,_,_ = self.build_runner()
        self.assertIn('entry_quality_below_minimum',runner.REPLACEABLE_ECONOMIC_BLOCKERS)
        self.assertNotIn('entry_quality_unknown',runner.REPLACEABLE_ECONOMIC_BLOCKERS)

    async def test_runtime_economics_marks_quality_refusal_without_stopping(self):
        runner,client = await self.prepared()
        runner.config = replace(runner.config,entry_quality_policy=EntryQualityPolicy(True))
        state = runner.state_for('market-one')
        state.book = cliff()
        reads = client.markets.book_calls
        runner._evaluate_rewards(live.NOW,'market-one')
        self.assertEqual(state.economic_blocker_by_side['bid'],'entry_quality_below_minimum')
        self.assertEqual(state.expected_net_by_side['bid'],0)
        self.assertGreater(state.expected_net_by_side['offer'],0)
        self.assertEqual(reads,client.markets.book_calls)

    async def test_invalid_config_refuses_before_secrets(self):
        args = Namespace(market_slug='market-one',entry_quality_policy=Path('missing-quality-policy.json'))
        with patch('liquidity_bot.live_commissioning.load_runtime_secrets') as load:
            with self.assertRaisesRegex(CommissioningRefused,'entry quality policy'):
                await _run(args)
        load.assert_not_called()
