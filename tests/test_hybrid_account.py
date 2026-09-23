"""Hybrid notifications with the real ledger/runner and a fake venue only."""
import asyncio
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from test_venue_state import raw_order, raw_execution
from liquidity_bot.account_source import startup_mode
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import LedgerConflict
from liquidity_bot.hybrid_stream import HybridGap, HybridPrivateStream
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind as Kind
from liquidity_bot.rest_fallback import RestStateGate
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class SilentStream:
    def __init__(self, client=None):
        self.events = asyncio.Queue()
        self.closed = False
        self.health_checks = 0

    async def start(self, scope):
        self.scope = scope

    async def next_event(self, timeout=None):
        async with asyncio.timeout(timeout):
            result = await self.events.get()
        if isinstance(result, Exception):
            raise result
        return result

    def assert_live(self, **kwargs):
        self.health_checks += 1

    async def close(self):
        self.closed = True


class HybridStreamTests(IsolatedAsyncioTestCase):
    async def test_reconnect_delivers_again_and_shutdown_closes_new_connection(self):
        first, second = SilentStream(), SilentStream()
        first.events.put_nowait(AdapterError("disconnect"))
        second.events.put_nowait(PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        wires = iter((first, second))
        stream = HybridPrivateStream(None, factory=lambda _: next(wires))
        stream.RETRY_INITIAL_SECONDS = .001
        self.addAsyncCleanup(stream.close)
        await stream.start(("market-one",))
        gap = await stream.next_event(timeout=1)
        stream.applied(gap)
        resumed = await stream.next_event(timeout=1)
        self.assertEqual(resumed.event.kind, Kind.POSITION_UPDATE)
        stream.applied(resumed)
        self.assertEqual(stream.state, "receiving")
        await stream.close()
        self.assertTrue(first.closed and second.closed)

    async def test_failure_is_fallback_notice_not_runner_exception(self):
        wire = SilentStream()
        wire.events.put_nowait(AdapterError("disconnected"))
        stream = HybridPrivateStream(None, factory=lambda _: wire)
        self.addAsyncCleanup(stream.close)
        await stream.start(("market-one",))
        notice = await stream.next_event(timeout=1)
        self.assertIsInstance(notice.event, HybridGap)
        self.assertEqual(stream.state, "rest_fallback")
        self.assertTrue(wire.closed)
        stream.applied(notice)
        self.assertEqual(stream.pending, 0)

    async def test_quiet_healthy_transport_does_not_claim_account_delivery(self):
        wire = SilentStream()
        stream = HybridPrivateStream(None, factory=lambda _: wire)
        stream.HEALTH_SECONDS = .001
        self.addAsyncCleanup(stream.close)
        await stream.start(("market-one",))
        for _ in range(100):
            await asyncio.sleep(.001)
            if wire.health_checks:
                break
        self.assertGreater(wire.health_checks, 0)
        self.assertEqual(stream.state, "listening")
        self.assertEqual(stream.pending, 0)
        self.assertFalse(hasattr(stream, "initial_state_ready"))
        self.assertIsNone(wire.scope)

    async def test_held_prefix_is_drained_before_later_event_exactly_once(self):
        stream = HybridPrivateStream(None)
        await stream._put(HybridGap("first"))
        first = await stream.next_event()
        await stream._put(HybridGap("second"))
        self.assertEqual(stream.pending, 2)
        self.assertIs(stream.drain_events(limit=1)[0], first)
        stream.applied(first)
        second = stream.drain_events(limit=1)[0]
        self.assertEqual(second.event.reason, "second")
        stream.applied(second)
        self.assertTrue(stream.already_applied(first))
        self.assertEqual(stream.pending, 0)

    async def test_full_queue_backpressures_without_dropping_known_events(self):
        stream = HybridPrivateStream(None)
        stream._queue = asyncio.Queue(maxsize=1)
        await stream._put(HybridGap("first"))
        blocked = asyncio.create_task(stream._put(HybridGap("second")))
        await asyncio.sleep(0)
        self.assertFalse(blocked.done())
        first = await stream.next_event()
        stream.applied(first)
        await blocked
        second = await stream.next_event()
        self.assertEqual(second.event.reason, "second")
        stream.applied(second)

    async def test_close_joins_reader_during_connect(self):
        wire = SilentStream()
        async def slow(_):
            await asyncio.sleep(100)
        wire.start = slow
        stream = HybridPrivateStream(None, factory=lambda _: wire)
        await stream.start(("market-one",))
        await asyncio.sleep(0)
        await asyncio.wait_for(stream.close(), 1)
        self.assertTrue(wire.closed)
        self.assertIsNone(stream._task)
        self.assertEqual(stream.state, "closed")


class HybridRunnerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self):
        with patch.object(fixtures, "CommissioningRunner", partial(
                AccountSourceRunner, account_state_mode="hybrid")):
            runner, client, notifier = self.fixture.build_runner(clock=lambda: self.now)
        runner._websocket_factory = SilentStream
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        return runner, client

    async def deliver(self, runner, event):
        await runner.private_stream._put(event)
        await runner._drain_private_events()

    def order_event(self, *, status="ORDER_STATE_CANCELED", filled="0", execution_id="cancel"):
        order = raw_order(id="exchange-one", marketSlug="market-one", side="BUY",
            intent="ORDER_INTENT_BUY_LONG", quantity="10", cumQuantity=filled,
            state=status, price={"value": "0.49", "currency": "USD"},
            createTime=fixtures.NOW.isoformat(), lastTransactTime=self.now.isoformat())
        execution = raw_execution(id=execution_id, order=order,
            type="EXECUTION_TYPE_CANCELED", transactTime=self.now.isoformat())
        return PrivateEvent(Kind.ORDER_UPDATE, {"execution": execution},
                            request_id="orders", received_at=self.now)

    async def test_silent_socket_starts_with_rest_proof_and_preserves_cap(self):
        runner, _ = await self.build()
        self.assertIsInstance(runner.gate, RestStateGate)
        self.assertIsInstance(runner.private_stream, HybridPrivateStream)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertFalse(runner.gate.balance_snapshot_seen)
        self.assertEqual(runner.PENDING_ENTRY_ORDER_CEILING, 30)
        self.assertEqual(startup_mode(None, runner.ledger.path.parent), "hybrid")

    async def test_position_notification_preserves_bulk_optimization_for_flat_slate(self):
        runner, _ = await self.build()
        runner.rest_polling_policy = RestPollingPolicy(enabled=True)
        await self.deliver(runner, PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        states = []
        from liquidity_bot.live_commissioning import CommissioningRunner
        async def proof(*args, **kwargs):
            states.append(runner._bulk_order_proof)
        with patch.object(CommissioningRunner, "_rest_reconcile", proof):
            await runner._rest_reconcile(self.now, tag="rest-source-decision")
        self.assertEqual(states, [True])
        self.assertFalse(runner._hybrid_requires_reconcile)

    async def test_applied_hint_still_blocks_post_until_complete_reconciliation(self):
        runner, client = await self.build()
        await self.deliver(runner, PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        self.assertEqual(runner.private_stream.pending, 0)
        self.assertTrue(runner._hybrid_requires_reconcile)
        call = AsyncMock(return_value={})
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway._mutation("POST /v1/orders", call)
        call.assert_not_awaited()
        await runner._rest_reconcile(self.now, tag="hint-proof")
        await runner.executor.gateway._mutation("POST /v1/orders", call)
        call.assert_awaited_once()

    async def test_notification_arriving_during_rest_proof_still_blocks_post(self):
        runner, _ = await self.build()
        original = runner.account.fetch_positions
        async def positions(**kwargs):
            await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "racing"))
            return await original(**kwargs)
        with patch.object(runner.account, "fetch_positions", positions):
            await runner._rest_reconcile(self.now, tag="race")
        call = AsyncMock(return_value={})
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway._mutation("POST /v1/orders", call)
        call.assert_not_awaited()

    async def test_stream_terminal_proof_removes_exact_rest_retrieve(self):
        runner, client = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        self.now += timedelta(seconds=1)
        await runner.private_stream._put(self.order_event())
        with patch.object(client.orders, "retrieve", AsyncMock(side_effect=AssertionError("redundant read"))) as read:
            await runner._rest_reconcile(self.now, tag="hybrid-test")
        self.assertEqual(read.await_count, 0)
        self.assertFalse(runner.ledger.working_orders())
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_missing_terminal_frame_uses_exact_rest_read(self):
        runner, client = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        self.now += timedelta(seconds=1)
        client.orders.rows["exchange-one"] = self.order_event().payload["execution"]["order"]
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as read:
            await runner._rest_reconcile(self.now, tag="hybrid-fallback")
        self.assertEqual(read.await_count, 1)
        self.assertFalse(runner.ledger.working_orders())

    async def test_position_hint_cannot_flatten_or_overwrite_ledger(self):
        runner, _ = await self.build()
        from liquidity_bot.execution_ledger import PositionState
        runner.ledger.apply_position_event("test", "held", PositionState(
            "market-one", Decimal("15"), self.now), received_at=self.now)
        await self.deliver(runner, PrivateEvent(Kind.POSITION_UPDATE,
            {"afterPosition": {"netPositionDecimal": "0"}}, request_id="positions"))
        self.assertEqual(runner.ledger.position("market-one"), 15)
        self.assertTrue(runner._hybrid_requires_reconcile)

    async def test_empty_or_partial_snapshot_does_not_erase_working_order(self):
        runner, _ = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        for eof in (False, True):
            await self.deliver(runner, PrivateEvent(Kind.ORDER_SNAPSHOT,
                {"orders": [], "eof": eof}, request_id="snapshot"))
            self.assertEqual(len(runner.ledger.working_orders()), 1)
            self.assertFalse(runner.ledger.absent_orders())

    async def test_task_held_fill_applies_before_cancel_and_replay_is_idempotent(self):
        runner, _ = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        self.now += timedelta(seconds=1)
        fill = self.order_event(status="ORDER_STATE_PARTIALLY_FILLED", filled="0.75", execution_id="fill")
        fill.payload["execution"]["type"] = "EXECUTION_TYPE_PARTIAL_FILL"
        await runner.private_stream._put(fill)
        held = await runner.private_stream.next_event()
        await runner.private_stream._put(self.order_event(filled="0.75"))
        await runner._drain_private_events()
        await runner._observe_private(held, self.now)
        await self.deliver(runner, fill)
        self.assertEqual(runner.ledger.position("market-one"), Decimal("0.75"))
        self.assertEqual(len(runner.ledger.query("SELECT * FROM execution_fills")), 1)
        self.assertFalse(runner.ledger.working_orders())
        self.assertEqual(runner.private_stream.pending, 0)

    async def test_pending_event_refuses_create_at_admission_but_allows_cancel(self):
        runner, client = await self.build()
        runner._evaluate_rewards(self.now)
        actions = runner.reconciler.plan(runner._frame(self.now))
        await runner.private_stream._put(HybridGap("gap"))
        with self.assertRaisesRegex(ApiAdmissionDeferred, "private observation pending"):
            await runner.driver.apply_one(actions, market=runner.market, position=Decimal(0), now=self.now)
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(runner.ledger.unresolved_intents())
        self.assertEqual(runner.ledger.query("SELECT state FROM execution_intents")[-1]["state"], "FAILED")
        # The gateway admission check applies to POST only; cancellation must
        # remain available while the optional notification queue is catching up.
        callback = AsyncMock(return_value={})
        await runner.executor.gateway._mutation("POST /v1/orders/id/cancel", callback)
        callback.assert_awaited_once()

    async def test_gap_keeps_rest_recovery_and_does_not_mutate_orders(self):
        runner, client = await self.build()
        await self.deliver(runner, HybridGap("disconnected"))
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.now += timedelta(seconds=1)
        await runner._refresh_execution_state_for_decision(self.now)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertEqual(runner.account_state_mode, "hybrid")
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_malformed_event_falls_back_but_integrity_conflict_propagates(self):
        runner, _ = await self.build()
        await self.deliver(runner, PrivateEvent(Kind.ORDER_UPDATE, {}, request_id="broken"))
        self.assertEqual(runner.private_stream.state, "rest_fallback")
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        with patch.object(runner.projector, "apply", side_effect=LedgerConflict("conflict")):
            with self.assertRaises(LedgerConflict):
                await self.deliver(runner, self.order_event())

    async def test_failed_rest_remains_closed_despite_private_delivery(self):
        runner, _ = await self.build()
        await self.deliver(runner, PrivateEvent(Kind.POSITION_UPDATE, {}, request_id="hint"))
        with patch.object(runner.account, "fetch_positions", AsyncMock(side_effect=AdapterError("503"))):
            with self.assertRaises(AdapterError):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
