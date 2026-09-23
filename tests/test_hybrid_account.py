"""Hybrid notifications with the real ledger/runner and a fake venue only."""
import asyncio
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
import test_rest_reducer_continuity as continuity
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

    async def test_no_fill_new_ack_preserves_rest_cadence_but_not_prior_hint(self):
        runner, _ = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        event = self.order_event(status="ORDER_STATE_NEW", execution_id="new")
        event.payload["execution"]["type"] = "EXECUTION_TYPE_NEW"
        completed = runner._rest_poll_completed_at
        await self.deliver(runner, event)
        self.assertEqual(runner._rest_poll_completed_at, completed)
        self.assertFalse(runner._hybrid_requires_reconcile)
        self.assertEqual(runner._hybrid_counts["ack_only"], 1)
        await self.deliver(runner, PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        await self.deliver(runner, event)
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertIsNone(runner._rest_poll_completed_at)

    async def test_terminal_cancel_always_requires_rest_proof(self):
        runner, _ = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        await self.deliver(runner, self.order_event())
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertIsNone(runner._rest_poll_completed_at)
        self.assertEqual(runner._hybrid_counts["ack_only"], 0)

    async def test_new_no_fill_ack_of_exact_pending_create_avoids_extra_poll(self):
        runner, _ = await self.build()
        runner.ledger.prepare_intent("new", "PLACE", "market-one", "client-one", {
            "marketSlug": "market-one", "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"}, "quantity": "10",
        }, created_at=self.now)
        runner.ledger.transition_intent("new", "SUBMITTING", updated_at=self.now)
        runner.ledger.transition_intent("new", "ACKNOWLEDGED", updated_at=self.now,
                                        exchange_order_id="exchange-one")
        event = self.order_event(status="ORDER_STATE_NEW", execution_id="new")
        event.payload["execution"]["type"] = "EXECUTION_TYPE_NEW"
        event.payload["execution"]["order"]["side"] = "ORDER_SIDE_BUY"
        completed = runner._rest_poll_completed_at
        await self.deliver(runner, event)
        self.assertFalse(runner.ledger.unresolved_intents())
        self.assertFalse(runner._hybrid_requires_reconcile)
        self.assertEqual(runner._rest_poll_completed_at, completed)

    async def test_changed_cumulative_unknown_type_or_missing_cumulative_requires_proof(self):
        for change in ("fill", "type", "missing", "foreign"):
            with self.subTest(change=change):
                runner, _ = await self.build()
                self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
                event = self.order_event(status="ORDER_STATE_NEW", execution_id="new")
                raw = event.payload["execution"]
                raw["type"] = "EXECUTION_TYPE_NEW"
                if change == "fill":
                    raw["order"]["cumQuantity"] = "0.75"
                elif change == "type":
                    raw["type"] = "EXECUTION_TYPE_FUTURE"
                elif change == "missing":
                    del raw["order"]["cumQuantity"]
                else:
                    raw["order"]["id"] = "unowned-order"
                await self.deliver(runner, event)
                self.assertTrue(runner._hybrid_requires_reconcile)
                self.assertIsNone(runner._rest_poll_completed_at)


class HybridReducerContinuityTests(continuity.RestReducerContinuityTests):
    """Run the existing REST cancel/replace contract unchanged in hybrid too."""

    async def build(self):
        original = self.fixture.build_runner
        def build_runner(**kwargs):
            runner, client, notifier = original(**kwargs)
            runner._websocket_factory = SilentStream
            return runner, client, notifier
        with patch.object(continuity, "RestFallbackRunner", partial(
                AccountSourceRunner, account_state_mode="hybrid")), \
                patch.object(self.fixture, "build_runner", build_runner):
            return await super().build()

    async def hint(self, runner):
        await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        await runner._drain_private_events()

    async def test_terminal_cancel_event_uses_scoped_proof_before_next_flat_owner(self):
        runner, client, held = await self.build()
        runner._bind_markets((held, "flat"))
        old_gate = runner.gate.last_reconcile_at
        cancel = client.orders.cancel
        async def cancel_with_hint(*args):
            result = await cancel(*args)
            order = dict(client.orders.rows[args[0]])
            execution = raw_execution(id="cancel-race", order=order,
                type="EXECUTION_TYPE_CANCELED", transactTime=self.now.isoformat())
            await runner.private_stream._put(PrivateEvent(
                Kind.ORDER_UPDATE, {"execution": execution}, "cancel-race"))
            return result
        original = runner._decide_for_market
        visited = []
        async def decide(slug, now):
            visited.append(slug)
            if slug == "flat":
                self.assertEqual(len(client.orders.create_calls), 1)
                return None, 0
            runner.state_for(held).book_observed_at = self.now
            return await original(slug, now)
        with patch.object(client.orders, "cancel", cancel_with_hint), \
                patch.object(runner, "_decide_for_market", decide), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_rest_reconcile", AsyncMock(
                    side_effect=AssertionError("unnecessary global proof"))):
            await runner._decision_sweep((held, "flat"))
        self.assertEqual(visited[:2], [held, held])
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(Decimal(client.orders.create_calls[0]["quantity"]), 10)
        self.assertTrue(runner._hybrid_requires_reconcile)  # Entries still need global proof.
        self.assertEqual(runner.gate.last_reconcile_at, old_gate)

    async def proof_then_intent(self, runner, client, held, *, quantity="10", direction=None):
        client.orders.rows["exchange-reducer"]["state"] = "ORDER_STATE_CANCELED"
        self.now += timedelta(seconds=.1)
        await self.hint(runner)
        await runner._reconcile_position_state(held, tag="position-transition")
        payload = {"marketSlug": held, "quantity": quantity,
                   "intent": direction or "ORDER_INTENT_BUY_SHORT",
                   "price": {"value": "0.50", "currency": "USD"}}
        runner.ledger.prepare_intent("replacement", "PLACE", held, "replacement-client",
                                     payload, created_at=self.now)
        runner.ledger.transition_intent("replacement", "SUBMITTING", updated_at=self.now)
        return payload

    async def test_scoped_certificate_refuses_bad_quantity_direction_age_scope_and_entry(self):
        for change in ("quantity", "direction", "age", "future", "scope", "generation", "entry", "position", "gate"):
            with self.subTest(change=change):
                runner, client, held = await self.build()
                payload = await self.proof_then_intent(runner, client, held,
                    quantity="11" if change == "quantity" else "10",
                    direction="ORDER_INTENT_BUY_LONG" if change == "direction" else None)
                if change == "age":
                    self.now += timedelta(seconds=31)
                elif change == "future":
                    self.now -= timedelta(seconds=1)
                elif change == "scope":
                    runner._bind_markets((held, "other"))
                elif change == "generation":
                    runner.source_control.generation += 1
                elif change == "position":
                    from liquidity_bot.execution_ledger import PositionState
                    runner.ledger.apply_position_event("test", "changed", PositionState(
                        held, Decimal(9), self.now + timedelta(seconds=1)), received_at=self.now)
                elif change == "gate":
                    runner.gate.on_reconciliation_conflict()
                with self.assertRaises(ApiAdmissionDeferred):
                    await runner.executor.gateway.create(payload, reducing=change != "entry")
                self.assertFalse(client.orders.create_calls)

    async def test_new_message_after_scoped_proof_still_blocks_reducer(self):
        runner, client, held = await self.build()
        payload = await self.proof_then_intent(runner, client, held)
        await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "late"))
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create(payload, reducing=True)
        await runner._drain_private_events()
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create(payload, reducing=True)
        self.assertFalse(client.orders.create_calls)

    async def test_notification_during_scoped_proof_cannot_certify_reducer(self):
        runner, client, held = await self.build()
        original = runner.account.fetch_positions
        async def positions(**kwargs):
            await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "during"))
            return await original(**kwargs)
        with patch.object(runner.account, "fetch_positions", positions):
            payload = await self.proof_then_intent(runner, client, held)
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create(payload, reducing=True)
        self.assertFalse(client.orders.create_calls)
        self.assertNotIn(held, runner._position_confirmation_proofs)

    async def test_failed_scoped_proof_revokes_existing_certificate(self):
        runner, client, held = await self.build()
        payload = await self.proof_then_intent(runner, client, held)
        with patch.object(runner.account, "fetch_positions", AsyncMock(side_effect=AdapterError("503"))):
            with self.assertRaises(AdapterError):
                await runner._reconcile_position_state(held, tag="failed")
        self.assertFalse(runner._hybrid_reducer_proofs)
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create(payload, reducing=True)

    async def test_fresh_scoped_reducer_proof_is_one_use_and_does_not_release_entries(self):
        runner, client, held = await self.build()
        payload = await self.proof_then_intent(runner, client, held)
        before = runner.gate.last_reconcile_at
        await runner.executor.gateway.create(payload, reducing=True)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertEqual(runner._hybrid_counts["scoped_admissions"], 1)
        self.assertEqual(runner.gate.last_reconcile_at, before)
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create(payload, reducing=True)

    async def test_failed_final_admission_does_not_consume_scoped_certificate(self):
        runner, client, held = await self.build()
        await self.proof_then_intent(runner, client, held)
        gateway = runner.executor.gateway
        token = gateway._reducing_market.set(held)
        callback = AsyncMock(return_value={})
        try:
            def refuse():
                raise ApiAdmissionDeferred("later gate refused")
            with self.assertRaisesRegex(ApiAdmissionDeferred, "later gate refused"):
                await gateway._mutation("POST /v1/orders", callback,
                                        admission_check=refuse)
            self.assertIn(held, runner._hybrid_reducer_proofs)
            await gateway._mutation("POST /v1/orders", callback)
        finally:
            gateway._reducing_market.reset(token)
        callback.assert_awaited_once()
        self.assertNotIn(held, runner._hybrid_reducer_proofs)

    async def test_sell_intents_cannot_use_scoped_reducer_certificate(self):
        for quantity, direction in (("10", "ORDER_INTENT_SELL_LONG"),
                                    ("-10", "ORDER_INTENT_SELL_SHORT")):
            with self.subTest(quantity=quantity):
                runner, client, held = await self.build()
                exact = Decimal(quantity)
                client.portfolio.rows[held] = {
                    "netPositionDecimal": quantity,
                    "cost": {"value": str(abs(exact) * Decimal("0.49")),
                             "currency": "USD"}}
                payload = await self.proof_then_intent(
                    runner, client, held, quantity=str(abs(exact)), direction=direction)
                with self.assertRaises(ApiAdmissionDeferred):
                    await runner.executor.gateway.create(payload, reducing=True)
                self.assertFalse(client.orders.create_calls)

    async def test_fill_racing_cancel_uses_residual_not_previous_quantity(self):
        runner, client, held = await self.build()
        cancel = client.orders.cancel
        async def cancel_with_fill(*args):
            result = await cancel(*args)
            client.orders.rows[args[0]]["cumQuantity"] = "8"
            client.portfolio.rows[held]["netPositionDecimal"] = "2"
            client.portfolio.rows[held]["cost"]["value"] = "0.98"
            await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "partial"))
            return result
        original = runner._decide_for_market
        async def decide(slug, now):
            runner.state_for(held).book_observed_at = self.now
            return await original(slug, now)
        with patch.object(client.orders, "cancel", cancel_with_fill), \
                patch.object(runner, "_decide_for_market", decide), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()):
            await runner._decision_sweep((held,))
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(Decimal(client.orders.create_calls[0]["quantity"]), 2)

    async def test_pending_message_during_admission_wait_vetoes_prepared_reducer(self):
        runner, client, held = await self.build()
        payload = await self.proof_then_intent(runner, client, held)
        original = runner.gateway.limiter.wait
        async def delayed(*args, **kwargs):
            await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "admission-race"))
            return await original(*args, **kwargs)
        with patch.object(runner.gateway.limiter, "wait", delayed):
            with self.assertRaises(ApiAdmissionDeferred):
                await runner.executor.gateway.create(payload, reducing=True)
        self.assertFalse(client.orders.create_calls)

    async def test_scoped_reducers_preserve_size15_and_fractional_long_short_positions(self):
        for quantity in ("15", "-15", "0.04", "-7.54"):
            with self.subTest(quantity=quantity):
                runner, client, held = await self.build()
                exact = Decimal(quantity)
                client.portfolio.rows[held] = {
                    "netPositionDecimal": quantity,
                    "cost": {"value": str(abs(exact) * Decimal("0.49")), "currency": "USD"}}
                payload = await self.proof_then_intent(runner, client, held,
                    quantity=str(abs(exact)), direction=("ORDER_INTENT_BUY_SHORT"
                        if exact > 0 else "ORDER_INTENT_BUY_LONG"))
                await runner.executor.gateway.create(payload, reducing=True)
                self.assertEqual(Decimal(client.orders.create_calls[0]["quantity"]), abs(exact))

    async def test_other_unresolved_intent_prevents_scoped_admission(self):
        runner, client, held = await self.build()
        payload = await self.proof_then_intent(runner, client, held)
        runner.ledger.prepare_intent("other", "PLACE", "other-market", "other-client",
                                     {**payload, "marketSlug": "other-market"}, created_at=self.now)
        runner.ledger.transition_intent("other", "SUBMITTING", updated_at=self.now)
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create(payload, reducing=True)
        self.assertFalse(client.orders.create_calls)
