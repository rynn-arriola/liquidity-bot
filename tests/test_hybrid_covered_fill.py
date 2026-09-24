"""Covered REST/private fill overlap; fake venue only, no live API calls."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_hybrid_rest_comparison as fixtures
from test_venue_state import raw_execution
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind as Kind


class CoveredFillTests(IsolatedAsyncioTestCase):
    setUp = fixtures.HybridRestComparisonTests.setUp
    build = fixtures.HybridRestComparisonTests.build
    reads = fixtures.HybridRestComparisonTests.reads

    def fill(self, client, quantity='10'):
        self.now += timedelta(seconds=1)
        order = dict(client.orders.rows['exchange-0'])
        partial = quantity != '10'
        order.update(cumQuantity=quantity, state=(
                     'ORDER_STATE_PARTIALLY_FILLED' if partial else 'ORDER_STATE_FILLED'),
                     lastTransactTime=self.now.isoformat())
        client.orders.rows['exchange-0'] = order
        execution = raw_execution(id='overlap-fill', tradeId='overlap-trade',
            order=order, type=('EXECUTION_TYPE_PARTIAL_FILL' if partial else 'EXECUTION_TYPE_FILL'),
            lastShares=quantity,
            lastPx={'value': '0.49', 'currency': 'USD'},
            commissionNotionalCollected={'value': '0', 'currency': 'USD'},
            transactTime=self.now.isoformat())
        client.portfolio.trades = [{'id': 'overlap-trade', 'isAggressor': True,
                                    'aggressorExecution': execution}]
        client.portfolio.rows['market-0'] = {'netPositionDecimal': quantity,
            'cost': {'value': str(Decimal(quantity) * Decimal('0.49')), 'currency': 'USD'}}
        return PrivateEvent(Kind.ORDER_UPDATE, {'execution': execution},
                            'orders', received_at=self.now)

    async def covered(self, during=False, quantity='10'):
        runner, client = await self.build('hybrid', count=1)
        event = self.fill(client, quantity)
        self.now += timedelta(seconds=1)
        original = runner.account.fetch_positions
        async def positions(**kwargs):
            result = await original(**kwargs)
            if during:
                await runner.private_stream._put(event)
            return result
        with patch.object(runner.account, 'fetch_positions', positions):
            await runner._rest_reconcile(self.now, tag='rest-source-decision')
        return runner, client, event

    async def check_overlap(self, during):
        runner, client, event = await self.covered(during)
        proof_time = runner.gate.last_reconcile_at
        poll_time = runner._rest_poll_completed_at
        if not during:
            await runner.private_stream._put(event)
        with self.reads(client) as reads:
            await runner._refresh_execution_state_for_decision(self.now)
        self.assertEqual(dict(reads), {})
        self.assertEqual(runner.ledger.position('market-0'), 10)
        self.assertEqual(len(runner.ledger.query('SELECT * FROM execution_fills')), 1)
        self.assertEqual(runner.gate.last_reconcile_at, proof_time)
        self.assertEqual(runner._rest_poll_completed_at, poll_time)
        self.assertFalse(runner._hybrid_requires_reconcile)
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_duplicate_after_rest_proof_adds_no_reads(self):
        await self.check_overlap(False)

    async def test_duplicate_queued_during_rest_proof_adds_no_reads(self):
        await self.check_overlap(True)

    async def test_new_fill_still_wakes_proof(self):
        runner, client = await self.build('hybrid', count=1)
        await runner.private_stream._put(self.fill(client))
        with self.reads(client) as reads:
            await runner._refresh_execution_state_for_decision(self.now)
        self.assertEqual(dict(reads), {'orders': 1, 'positions': 1, 'history': 1})
        self.assertEqual(runner.ledger.position('market-0'), 10)

    async def test_duplicate_does_not_extend_normal_active_poll(self):
        runner, client, event = await self.covered()
        self.now += timedelta(seconds=1)
        await runner.private_stream._put(event)
        with self.reads(client) as reads:
            await runner._refresh_execution_state_for_decision(self.now)
        self.assertGreater(sum(reads.values()), 0)

    async def test_prior_hint_cannot_be_cleared_by_duplicate(self):
        runner, client, event = await self.covered()
        runner._hybrid_requires_reconcile = True
        await runner.private_stream._put(event)
        await runner._drain_private_events()
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertIsNone(runner._rest_poll_completed_at)

    async def test_changed_fill_content_remains_conflict(self):
        from liquidity_bot.execution_ledger import LedgerConflict
        runner, client, event = await self.covered()
        event = deepcopy(event)
        event.payload['execution']['lastShares'] = '9'
        await runner.private_stream._put(event)
        with self.assertRaises(LedgerConflict):
            await runner._drain_private_events()
        self.assertFalse(runner.gate.rest_reconciled)

    async def test_authority_guards_refuse_shortcut(self):
        runner, client, event = await self.covered()
        self.assertTrue(runner._hybrid_fill_already_covered(event, self.now))
        for name, value in (
                ('_stop_requested', True), ('_streams_need_rebind', True),
                ('_source_switching', True), ('_source_mutations_in_flight', 1),
                ('_hybrid_requires_reconcile', True),
                ('_rest_poll_completed_at', None), ('account_state_mode', 'rest'),
                ('_hybrid_fill_proof', (('market-1',), *runner._hybrid_fill_proof[1:]))):
            with self.subTest(guard=name), patch.object(runner, name, value):
                self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))
        with patch.object(runner.source_control, 'generation', 999):
            self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))
        with patch.object(runner.ledger, 'unresolved_intents', return_value=[{}]):
            self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))
        with patch.object(runner.ledger, 'absent_orders', return_value=[{}]):
            self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))
        self.assertFalse(runner._hybrid_fill_already_covered(event, self.now - timedelta(seconds=1)))
        self.assertFalse(runner._hybrid_fill_already_covered(event, self.now + timedelta(seconds=31)))
        runner.gate.on_reconciliation_conflict()
        self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))

    async def test_identity_economics_and_order_changes_refuse_shortcut(self):
        runner, client, event = await self.covered()
        for field, value in (
                ('id', 'new-fill'), ('tradeId', 'different-trade'),
                ('lastShares', '9'), ('lastPx', {'value': '0.48'}),
                ('commissionNotionalCollected', {'value': '0.01'}),
                ('transactTime', (self.now + timedelta(seconds=1)).isoformat()),
                ('type', 'EXECUTION_TYPE_CANCELED')):
            changed = deepcopy(event)
            changed.payload['execution'][field] = value
            with self.subTest(field=field):
                self.assertFalse(runner._hybrid_fill_already_covered(changed, self.now))
        for field, value in (
                ('cumQuantity', '9'), ('state', 'ORDER_STATE_PARTIALLY_FILLED'),
                ('lastTransactTime', self.now.isoformat())):
            changed = deepcopy(event)
            changed.payload['execution']['order'][field] = value
            with self.subTest(order_field=field):
                self.assertFalse(runner._hybrid_fill_already_covered(changed, self.now))
        for field in ('transactTime', 'commissionNotionalCollected'):
            changed = deepcopy(event)
            del changed.payload['execution'][field]
            self.assertFalse(runner._hybrid_fill_already_covered(changed, self.now))

    async def test_repeated_duplicates_preserve_cadence_and_exactly_once_fill(self):
        runner, client, event = await self.covered()
        revision = runner._execution_proof_event_revision
        for _ in range(3):
            await runner.private_stream._put(deepcopy(event))
        with self.reads(client) as reads:
            await runner._refresh_execution_state_for_decision(self.now)
        self.assertEqual(dict(reads), {})
        self.assertEqual(runner._hybrid_counts['covered_fill'], 3)
        self.assertEqual(runner._execution_proof_event_revision, revision + 3)
        self.assertEqual(runner.ledger.position('market-0'), 10)

    async def test_failed_full_proof_discards_coverage(self):
        from liquidity_bot.errors import AdapterError
        runner, client, event = await self.covered()
        with patch.object(runner.account, 'fetch_positions',
                          AsyncMock(side_effect=AdapterError('offline'))):
            with self.assertRaises(AdapterError):
                await runner._rest_reconcile(self.now, tag='rest-source-decision')
        self.assertIsNone(runner._hybrid_fill_proof)
        self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))

    async def test_scoped_proof_does_not_certify_global_fill_replay(self):
        runner, client, event = await self.covered()
        await runner._reconcile_position_state('market-0', tag='hybrid-preplan')
        self.assertIsNone(runner._hybrid_fill_proof)

    async def test_cadence_noop_cannot_renew_coverage(self):
        runner, client, event = await self.covered()
        proof = runner._hybrid_fill_proof
        await runner._rest_reconcile(self.now, tag='periodic')
        self.assertEqual(runner._hybrid_fill_proof, proof)
        self.assertTrue(runner._hybrid_fill_already_covered(event, self.now))

    async def test_private_only_known_fill_not_enough_without_history_overlap(self):
        runner, client = await self.build('hybrid', count=1)
        event = self.fill(client)
        await runner.private_stream._put(event)
        await runner._drain_private_events()
        client.portfolio.trades = []
        await runner._rest_reconcile(self.now, tag='rest-source-decision')
        self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))

    async def test_later_position_hint_still_forces_proof(self):
        runner, client, event = await self.covered()
        await runner.private_stream._put(event)
        await runner.private_stream._put(PrivateEvent(
            Kind.POSITION_UPDATE, {}, 'positions', received_at=self.now))
        await runner._drain_private_events()
        self.assertEqual(runner._hybrid_counts['covered_fill'], 1)
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertIsNone(runner._rest_poll_completed_at)

    async def test_busted_fill_cannot_use_coverage(self):
        from liquidity_bot.execution_ledger import TradeStateObservation
        runner, client, event = await self.covered()
        runner.ledger.apply_trade_state(TradeStateObservation(
            trade_id='overlap-trade', market_slug='market-0',
            state='TRADE_STATE_BUSTED', update_at=self.now), received_at=self.now)
        self.assertFalse(runner._hybrid_fill_already_covered(event, self.now))

    async def test_partial_fill_duplicate_does_not_wake_another_proof(self):
        runner, client, event = await self.covered(quantity='5')
        await runner.private_stream._put(event)
        with self.reads(client) as reads:
            await runner._refresh_execution_state_for_decision(self.now)
        self.assertEqual(dict(reads), {})
        self.assertEqual(runner.ledger.position('market-0'), 5)
        self.assertEqual(runner._hybrid_counts['covered_fill'], 1)

    async def test_stale_order_envelope_with_new_execution_stays_urgent(self):
        runner, client, event = await self.covered(quantity='5')
        event = deepcopy(event)
        event.payload['execution'].update(id='new-fill', tradeId='new-trade',
            transactTime=(self.now + timedelta(seconds=1)).isoformat(), lastShares='1')
        await runner.private_stream._put(event)
        await runner._drain_private_events()
        self.assertEqual(runner._hybrid_counts['covered_fill'], 0)
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertIsNone(runner._rest_poll_completed_at)
        self.assertEqual(len(runner.ledger.query('SELECT * FROM execution_fills')), 2)

    async def test_canceled_collection_discards_coverage(self):
        import asyncio
        runner, client, event = await self.covered()
        with patch.object(runner.account, 'fetch_positions',
                          AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await runner._rest_reconcile(self.now, tag='rest-source-decision')
        self.assertIsNone(runner._hybrid_fill_proof)
        self.assertFalse(runner._hybrid_collecting_fill_coverage)

    async def test_missing_or_null_wire_values_cannot_authorize_shortcut(self):
        runner, client, event = await self.covered()
        for field in ('transactTime', 'commissionNotionalCollected'):
            changed = deepcopy(event)
            changed.payload['execution'][field] = None
            self.assertFalse(runner._hybrid_fill_already_covered(changed, self.now))
        malformed = PrivateEvent(Kind.ORDER_UPDATE, None, 'orders', received_at=self.now)
        self.assertFalse(runner._hybrid_fill_already_covered(malformed, self.now))
        await runner.private_stream._put(malformed)
        await runner._drain_private_events()
        self.assertFalse(runner.gate.rest_reconciled)
        self.assertTrue(runner._hybrid_requires_reconcile)
