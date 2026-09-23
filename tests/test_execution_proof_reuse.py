"""Fake-only bounded proof reuse; never contacts the venue."""
import asyncio
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import test_hybrid_rest_comparison as comparison
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_proof import ExecutionProofCycle, ExecutionProofReceipt
from liquidity_bot.hybrid_stream import HybridGap
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class ReceiptTests(TestCase):
    def test_age_scope_revision_and_one_use(self):
        now = comparison.fixtures.NOW
        for age, completed_age, version, market, valid in (
                (0, 0, (1,), 'held', True),
                (30, .999, (1,), 'held', True),
                (30.001, 0, (1,), 'held', False),
                (1, 1, (1,), 'held', False),
                (-1, 0, (1,), 'held', False),
                (1, -.001, (1,), 'held', False),
                (.1, .2, (1,), 'held', False),
                (0, 0, (2,), 'held', False),
                (0, 0, (1,), 'peer', False)):
            with self.subTest(age=age, completed=completed_age, version=version, market=market):
                cycle = ExecutionProofCycle()
                cycle.receipt = ExecutionProofReceipt(
                    (1,), frozenset({'held'}), now - timedelta(seconds=age),
                    now - timedelta(seconds=completed_age))
                receipt, _ = cycle.take(market, version=version, now=now)
                self.assertEqual(receipt is not None, valid)
                self.assertEqual(cycle.take('held', version=(1,), now=now), (None, 'missing'))


class ExecutionProofReuseTests(IsolatedAsyncioTestCase):
    setUp = comparison.HybridRestComparisonTests.setUp
    build = comparison.HybridRestComparisonTests.build
    reads = comparison.HybridRestComparisonTests.reads

    async def cycle(self, runner, callback):
        # Keep the real source-specific cycle boundary around controlled serial
        # consumers; production plan/apply is not run by these counting cases.
        with patch.object(CommissioningRunner, '_decision_sweep', callback):
            await runner._decision_sweep(())

    async def held(self, mode='hybrid', *, count=1):
        runner, client = await self.build(mode, count=count)
        client.portfolio.rows['market-31'] = {
            'netPositionDecimal': '15',
            'cost': {'value': '7.35', 'currency': 'USD'},
            'cashValue': {'value': '7.35', 'currency': 'USD'},
        }
        await runner._rest_reconcile(self.now, tag='baseline-position')
        self.now += timedelta(seconds=6)
        return runner, client

    async def test_full_then_scoped_saves_three_reads_both_modes_and_preserves_clocks(self):
        for mode in ('rest', 'hybrid'):
            runner, client = await self.held(mode, count=30)
            async def consumers(*args, **kwargs):
                await runner._refresh_execution_state_for_decision(self.now)
                before = runner.gate.last_reconcile_at, runner._rest_poll_completed_at
                original = runner._execution_proof_cycle.receipt
                self.now += timedelta(seconds=.1)
                with self.reads(client) as counts:
                    receipt = await runner._reconcile_position_state('market-31', tag='hybrid-preplan')
                self.assertEqual(sum(counts.values()), 0)
                self.assertEqual(receipt.started_at, original.started_at)
                self.assertEqual(receipt.completed_at, original.completed_at)
                self.assertEqual(before, (runner.gate.last_reconcile_at, runner._rest_poll_completed_at))
                self.assertEqual(runner._hybrid_reducer_proofs.get('market-31', (None,))[-1],
                                 original.started_at if mode == 'hybrid' else None)
                # Neither repeated use nor another cycle can chain the receipt.
                with self.reads(client) as counts:
                    await runner._reconcile_position_state('market-31', tag='hybrid-preplan')
                self.assertEqual(dict(counts), {'orders': 1, 'positions': 1, 'history': 1})
            await self.cycle(runner, consumers)
            self.assertIsNone(runner._execution_proof_cycle)
            self.assertEqual(runner.ledger.position('market-31'), 15)
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_scoped_receipt_cannot_skip_global_proof_or_peer(self):
        runner, client = await self.held()
        async def consumers(*args, **kwargs):
            await runner._reconcile_position_state('market-31', tag='position-transition')
            with self.reads(client) as counts:
                await runner._refresh_execution_state_for_decision(self.now)
            self.assertEqual(dict(counts), {'orders': 2, 'positions': 1, 'history': 1})
            runner._invalidate_execution_proof()
            await runner._reconcile_position_state('market-31', tag='position-transition')
            with self.reads(client) as counts:
                await runner._reconcile_position_state('market-30', tag='position-transition')
            self.assertEqual(sum(counts.values()), 3)
        await self.cycle(runner, consumers)

    async def test_receipts_do_not_cross_cycles_even_at_same_clock(self):
        runner, client = await self.held()
        async def consumer(*args, **kwargs):
            with self.reads(client) as counts:
                await runner._reconcile_position_state('market-31', tag='position-transition')
            self.assertEqual(sum(counts.values()), 3)
        await self.cycle(runner, consumer)
        await self.cycle(runner, consumer)

    async def test_state_changes_cannot_reuse(self):
        for change in ('ledger', 'generation', 'scope', 'age', 'future', 'gate',
                       'stop', 'rebind', 'switch', 'mode', 'mutation', 'disabled', 'queued'):
            with self.subTest(change=change):
                runner, client = await self.held()
                async def consumers(*args, **kwargs):
                    await runner._refresh_execution_state_for_decision(self.now)
                    if change == 'ledger':
                        self.fixture.seed_working_order(runner, slug='market-30', side='bid', suffix='changed')
                    elif change == 'generation':
                        runner.source_control.generation += 1
                    elif change == 'scope':
                        runner._bind_markets(('market-31',))
                    elif change == 'age':
                        self.now += timedelta(seconds=1)
                    elif change == 'future':
                        self.now -= timedelta(seconds=.01)
                    elif change == 'gate':
                        runner.gate.on_reconciliation_conflict()
                    elif change == 'stop':
                        runner._stop_requested = 'operator'
                    elif change == 'rebind':
                        runner._streams_need_rebind = True
                    elif change == 'switch':
                        runner._source_switching = True
                    elif change == 'mode':
                        runner.account_state_mode = 'websocket'
                    elif change == 'mutation':
                        runner._source_mutations_in_flight = 1
                    elif change == 'disabled':
                        runner.rest_polling_policy = RestPollingPolicy(enabled=False)
                    elif change == 'queued':
                        await runner.private_stream._put(PrivateEvent(
                            PrivateEventKind.POSITION_UPDATE, {}, 'position'))
                    self.assertIsNone(runner._take_execution_proof('market-31'))
                await self.cycle(runner, consumers)

    async def test_applied_private_hint_or_gap_ends_reuse(self):
        for event in (HybridGap('test'), PrivateEvent(PrivateEventKind.POSITION_UPDATE, {}, 'hint')):
            runner, client = await self.held()
            async def consumers(*args, **kwargs):
                await runner._refresh_execution_state_for_decision(self.now)
                await runner.private_stream._put(event)
                await runner._drain_private_events()
                with self.reads(client) as counts:
                    await runner._reconcile_position_state('market-31', tag='hybrid-preplan')
                self.assertEqual(sum(counts.values()), 3)
            await self.cycle(runner, consumers)

    async def test_event_during_proof_cannot_publish_reusable_receipt(self):
        runner, client = await self.held()
        original = runner.account.fetch_positions
        async def positions(**kwargs):
            await runner.private_stream._put(PrivateEvent(PrivateEventKind.POSITION_UPDATE, {}, 'during'))
            return await original(**kwargs)
        async def consumers(*args, **kwargs):
            with patch.object(runner.account, 'fetch_positions', positions):
                await runner._reconcile_position_state('market-31', tag='position-transition')
            self.assertIsNone(runner._execution_proof_cycle.receipt)
            self.assertFalse(runner._hybrid_reducer_proofs)
        await self.cycle(runner, consumers)

    async def test_applied_event_during_proof_cannot_publish_receipt(self):
        runner, client = await self.held()
        original = runner.account.fetch_positions
        async def positions(**kwargs):
            await runner.private_stream._put(HybridGap('during'))
            await runner._drain_private_events()
            return await original(**kwargs)
        async def consumers(*args, **kwargs):
            with patch.object(runner.account, 'fetch_positions', positions):
                await runner._reconcile_position_state('market-31', tag='position-transition')
            self.assertIsNone(runner._execution_proof_cycle.receipt)
        await self.cycle(runner, consumers)

    async def test_failed_global_and_cancelled_scoped_drop_old_receipt(self):
        for failure in (AdapterError('503'), asyncio.CancelledError()):
            runner, client = await self.held()
            async def consumers(*args, **kwargs):
                await runner._refresh_execution_state_for_decision(self.now)
                with patch.object(runner.account, 'fetch_positions', AsyncMock(side_effect=failure)):
                    with self.assertRaises(type(failure)):
                        if isinstance(failure, AdapterError):
                            await runner._rest_reconcile(self.now, tag='recovery')
                        else:
                            await runner._reconcile_position_state('market-31', tag='hard-exit')
                self.assertIsNone(runner._execution_proof_cycle.receipt)
                self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
            await self.cycle(runner, consumers)

    async def test_recovery_and_hard_exit_stay_strict(self):
        runner, client = await self.held()
        async def consumers(*args, **kwargs):
            for tag in ('hard-exit', 'strategy-recovery-terminal'):
                await runner._refresh_execution_state_for_decision(self.now)
                with self.reads(client) as counts:
                    await runner._reconcile_position_state('market-31', tag=tag)
                self.assertEqual(sum(counts.values()), 3)
                self.assertIsNone(runner._execution_proof_cycle.receipt)
        await self.cycle(runner, consumers)

    async def test_financial_bracket_unchanged(self):
        runner, client = await self.held()
        async def consumers(*args, **kwargs):
            await runner._refresh_execution_state_for_decision(self.now)
            with self.reads(client) as counts:
                await runner._refresh_balance(self.now)
            self.assertEqual(dict(counts), {'balance': 3, 'positions': 1, 'history': 1})
        await self.cycle(runner, consumers)

    async def test_nested_cycle_and_exception_discard_parent_receipt(self):
        runner, client = await self.held()
        async def inner(*args, **kwargs):
            raise AdapterError('test nested failure')
        async def outer(*args, **kwargs):
            await runner._refresh_execution_state_for_decision(self.now)
            parent = runner._execution_proof_cycle
            with self.assertRaises(AdapterError):
                await self.cycle(runner, inner)
            self.assertIs(runner._execution_proof_cycle, parent)
            self.assertIsNone(parent.receipt)
        await self.cycle(runner, outer)
        self.assertIsNone(runner._execution_proof_cycle)

    async def test_telemetry_is_bounded_and_adds_no_reads(self):
        runner, client = await self.held()
        with self.reads(client) as counts, patch('builtins.print') as output:
            runner._note_execution_proof('scoped', 'secret-order-123')
            runner._report_execution_proofs()
            runner._note_execution_proof('global', 'periodic')
            runner._report_execution_proofs()
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(output.call_count, 1)
        self.assertNotIn('secret-order-123', str(output.call_args))

    async def test_existing_exact_counter_retains_strict_reads(self):
        runner, client = await self.held()
        runner._rest_order_proof_counts.clear()
        with self.reads(client) as counts:
            await runner._read_owned_order_states(['exchange-0'], self.now)
        self.assertEqual(counts['exact'], 1)
        self.assertEqual(runner._rest_order_proof_counts['exact'], 1)

    async def test_scope_or_source_changed_during_proof_does_not_publish(self):
        for change in ('scope', 'generation'):
            runner, client = await self.held()
            original = runner.account.fetch_positions
            async def positions(**kwargs):
                if change == 'scope':
                    runner._bind_markets(('market-31',))
                else:
                    runner.source_control.generation += 1
                return await original(**kwargs)
            async def consumers(*args, **kwargs):
                with patch.object(runner.account, 'fetch_positions', positions):
                    with self.assertRaises(AdapterError):
                        await runner._reconcile_position_state('market-31', tag='position-transition')
                self.assertIsNone(runner._execution_proof_cycle.receipt)
            await self.cycle(runner, consumers)

    async def test_late_event_after_reuse_still_blocks_network_create(self):
        runner, client = await self.held()
        async def consumers(*args, **kwargs):
            await runner._refresh_execution_state_for_decision(self.now)
            with self.reads(client) as counts:
                await runner._reconcile_position_state('market-31', tag='hybrid-preplan')
            self.assertEqual(sum(counts.values()), 0)
            await runner.private_stream._put(PrivateEvent(PrivateEventKind.POSITION_UPDATE, {}, 'late'))
            with self.assertRaises(ApiAdmissionDeferred):
                await runner.executor.gateway.create({'marketSlug': 'market-31'}, reducing=True)
            self.assertFalse(client.orders.create_calls)
        await self.cycle(runner, consumers)

    async def test_ambiguity_and_unknown_ownership_never_reuse(self):
        runner, client = await self.held()
        async def consumers(*args, **kwargs):
            for method, value in (('unresolved_intents', [{}]), ('absent_orders', [{}]),
                                  ('ownership_is_known', False), ('risk_state_known', False)):
                await runner._rest_reconcile(self.now, tag='rest-source-decision')
                with patch.object(runner.ledger, method, return_value=value):
                    self.assertIsNone(runner._take_execution_proof('market-31'), method)
        await self.cycle(runner, consumers)
