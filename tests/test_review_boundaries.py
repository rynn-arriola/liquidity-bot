"""September 12 review regressions; fake venue only, shared engine in both modes."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import Execution, LedgerConflict, PositionState
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind as Kind
from liquidity_bot.streams import PrivateReadOnlyStream
from scripts.accounting import snapshot_change_lines


class ReviewBoundaryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self, mode="websocket"):
        with patch.object(fixtures, "CommissioningRunner", partial(
                AccountSourceRunner, account_state_mode=mode)):
            runner, client, _ = self.fixture.build_runner(clock=lambda: self.now)
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        runner._evaluate_rewards(self.now)
        return runner, client

    async def test_conflicting_fill_revokes_both_sources(self):
        for mode in ("websocket", "rest"):
            with self.subTest(mode=mode):
                runner, _ = await self.build(mode)
                fill = Execution(
                    execution_id="review-conflict", market_slug=runner.market_slug,
                    side="ORDER_SIDE_BUY", price=Decimal("0.4"), qty=Decimal(1),
                    fee=Decimal(0), event_at=self.now, intent="ORDER_INTENT_BUY_LONG")
                runner.ledger.apply_execution(fill, received_at=self.now)
                runner._fetch_execution_history = AsyncMock(return_value=(
                    replace(fill, price=Decimal("0.8")),))
                with self.assertRaises(LedgerConflict):
                    await runner._rest_reconcile(self.now)
                self.assertFalse(runner.gate.rest_reconciled)

    async def test_slow_cash_bracket_cannot_renew_risk_freshness(self):
        for mode in ("websocket", "rest"):
            with self.subTest(mode=mode):
                self.now = fixtures.NOW
                runner, _ = await self.build(mode)
                started = self.now
                async def delayed_cash(*args, **kwargs):
                    self.now += timedelta(seconds=25)
                    return Decimal(0)
                with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash", delayed_cash):
                    await runner._refresh_balance(started)
                self.assertEqual(runner.risk_guard.equity_observed_at, started)
                self.assertFalse(runner.risk_guard.can_add_new_risk(now=self.now))
                # Publication succeeding is different from the mark being fresh.
                self.assertEqual(runner._equity_snapshot.observed_at, started)

    async def test_failed_cash_bracket_keeps_previous_mark(self):
        runner, _ = await self.build()
        before = runner._equity_snapshot
        stamp = runner.risk_guard.equity_observed_at
        with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash",
                   AsyncMock(side_effect=AdapterError("cash history unavailable"))):
            with self.assertRaises(AdapterError):
                await runner._refresh_balance(self.now)
        self.assertIs(runner._equity_snapshot, before)
        self.assertEqual(runner.risk_guard.equity_observed_at, stamp)

    async def send_with_wait(self, runner, wait):
        actions = runner.reconciler.plan(runner._frame(self.now))
        self.assertTrue(actions)
        class Admission:
            async def wait(_self):
                wait()
        runner.gateway.limiter = Admission()
        return await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal(0), now=self.now)

    async def test_entry_admission_rechecks_equity_in_both_modes(self):
        for mode in ("websocket", "rest"):
            with self.subTest(mode=mode):
                self.now = fixtures.NOW
                runner, client = await self.build(mode)
                def delay():
                    self.now += timedelta(seconds=16)
                    runner.state_for(runner.market_slug).book_observed_at = self.now
                with self.assertRaises(ApiAdmissionDeferred):
                    await self.send_with_wait(runner, delay)
                self.assertFalse(client.orders.create_calls)
                self.assertFalse(runner.ledger.unresolved_intents())
                self.assertEqual(runner.ledger.query(
                    "SELECT state FROM execution_intents WHERE operation='PLACE'")[-1]["state"], "FAILED")

    async def test_entry_admission_rechecks_book_age(self):
        runner, client = await self.build()
        def delay():
            self.now += timedelta(seconds=6)
        with self.assertRaises(ApiAdmissionDeferred):
            await self.send_with_wait(runner, delay)
        self.assertTrue(runner.risk_guard.can_add_new_risk(now=self.now))
        self.assertFalse(client.orders.create_calls)

    async def test_entry_admission_rechecks_pause_and_tightening_cap(self):
        for reason in ("pause", "cap", "stop"):
            with self.subTest(reason=reason):
                runner, client = await self.build()
                def change():
                    if reason == "pause":
                        runner._hard_stop_hold_day = runner._baseline_day
                    elif reason == "cap":
                        runner._loss_entry_order_cap = 0
                    else:
                        runner.request_stop("operator_stop")
                with self.assertRaises(ApiAdmissionDeferred):
                    await self.send_with_wait(runner, change)
                self.assertFalse(client.orders.create_calls)
                self.assertFalse(runner.ledger.unresolved_intents())

    async def test_current_create_reservation_is_not_counted_twice(self):
        for mode in ("websocket", "rest"):
            with self.subTest(mode=mode):
                self.now = fixtures.NOW
                runner, client = await self.build(mode)
                runner._loss_entry_order_cap = 1
                await self.send_with_wait(runner, lambda: None)
                self.assertEqual(len(client.orders.create_calls), 1)
                self.assertTrue(runner._loss_cap_blocks_new_entry(self.now))

    async def test_local_validator_error_never_creates_unknown_intent(self):
        runner, client = await self.build()
        def broken(_payload):
            raise ValueError("invalid local state")
        runner.gateway.entry_admission_check = broken
        with self.assertRaises(ApiAdmissionDeferred):
            await self.send_with_wait(runner, lambda: None)
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(runner.ledger.unresolved_intents())

    async def test_changed_position_blocks_the_previously_flat_entry(self):
        runner, client = await self.build()
        def position_changed():
            runner.ledger.apply_position_event("test", "new-position", PositionState(
                runner.market_slug, Decimal(1), self.now, cost=Decimal("0.49")),
                received_at=self.now)
        with self.assertRaises(ApiAdmissionDeferred):
            await self.send_with_wait(runner, position_changed)
        self.assertFalse(client.orders.create_calls)

    async def test_source_generation_change_vetoes_send_without_changing_mode(self):
        runner, client = await self.build("rest")
        def generation_changed():
            runner.source_control.generation += 1
        with self.assertRaises(ApiAdmissionDeferred):
            await self.send_with_wait(runner, generation_changed)
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(runner.ledger.unresolved_intents())

    async def test_reducer_does_not_use_flat_entry_admission(self):
        runner, client = await self.build("rest")
        def refuse(_payload):
            raise AssertionError("entry-only check called for reduction")
        runner.gateway.entry_admission_check = refuse
        # Isolate the gateway boundary; exact quantity/direction tests remain
        # in the unchanged driver/REST reducer suites.
        await runner.executor.gateway.create(
            {"marketSlug": runner.market_slug, "quantity": 1}, reducing=True)
        self.assertEqual(len(client.orders.create_calls), 1)

    async def test_shutdown_projects_task_held_frame_before_shutdown(self):
        runner, _ = await self.build()
        runner.preflight_until_ready = AsyncMock(return_value="RUNNING")
        marker = PrivateEvent(Kind.HEARTBEAT, {}, "review-held", received_at=self.now)
        runner.private_stream.events = [marker]
        original = runner._observe_private
        seen = []
        async def observe(event, now):
            seen.append(event.request_id)
            return await original(event, now)
        runner._observe_private = observe
        async def stop_at_yield(_seconds):
            await asyncio.sleep(0)
            runner.request_stop("review-stop-after-receipt")
        runner.sleep = stop_at_yield
        async def shutdown(_reason):
            self.assertEqual(seen.count("review-held"), 1)
        runner._shutdown = AsyncMock(side_effect=shutdown)
        await runner.run()
        runner._shutdown.assert_awaited_once()

    async def test_private_prefix_survives_a_later_queued_error(self):
        runner, _ = await self.build()
        # Real adapter drain, synthetic queue: the later error must not erase
        # the event already removed from the queue.
        stream = PrivateReadOnlyStream.__new__(PrivateReadOnlyStream)
        stream._queue = asyncio.Queue()
        stream._mark_delivered = lambda event: None
        stream._queue.put_nowait(PrivateEvent(Kind.HEARTBEAT, {}, "prefix"))
        stream._queue.put_nowait(AdapterError("socket failed after prefix"))
        runner.private_stream.drain_events = stream.drain_events
        runner._observe_private = AsyncMock()
        with self.assertRaises(AdapterError):
            await runner._drain_private_events()
        self.assertEqual(runner._observe_private.await_count, 1)
        self.assertEqual(runner._observe_private.await_args.args[0].request_id, "prefix")

    async def test_teardown_keeps_exact_fill_then_position_without_mutation(self):
        runner, client = await self.build()
        await self.send_with_wait(runner, lambda: None)
        qty = runner.config.quote_size
        order = {**client.orders.create_calls[0], "id": "fake-order-1",
                 "side": "ORDER_SIDE_BUY", "state": "ORDER_STATE_FILLED",
                 "cumQuantity": str(qty), "createTime": self.now.isoformat()}
        fill = PrivateEvent(Kind.ORDER_UPDATE, {"execution": {
            "id": "held-fill", "type": "EXECUTION_TYPE_FILL", "order": order,
            "lastPx": {"value": "0.49"}, "lastShares": str(qty),
            "transactTime": self.now.isoformat()}}, "held-fill-event")
        position = PrivateEvent(Kind.POSITION_UPDATE, {
            "marketSlug": runner.market_slug, "netPositionDecimal": str(qty),
            "cost": {"value": str(qty * Decimal("0.49"))},
            "updateTime": self.now.isoformat()}, "following-position")
        queued = [position]
        runner.private_stream.drain_events = lambda **kw: (queued.pop(0),) if queued else ()
        async def returned():
            return fill
        task = asyncio.create_task(returned())
        await asyncio.sleep(0)
        await runner._settle_stream_readers(None, task)
        self.assertEqual(runner.ledger.position(runner.market_slug), qty)
        self.assertEqual(len(runner.ledger.query("SELECT * FROM execution_fills")), 1)
        self.assertFalse(runner.ledger.unresolved_intents())
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertFalse(client.orders.cancel_calls)

    async def test_market_failure_does_not_discard_private_result(self):
        runner, _ = await self.build()
        async def failure():
            raise AdapterError("market socket failed")
        async def private():
            return PrivateEvent(Kind.HEARTBEAT, {}, "held-private")
        market_task = asyncio.create_task(failure())
        private_task = asyncio.create_task(private())
        await asyncio.sleep(0)
        runner._observe_private = AsyncMock()
        await runner._settle_stream_readers(market_task, private_task)
        runner._observe_private.assert_awaited_once()
        self.assertFalse(runner.gate.connected)
        self.assertTrue(runner._streams_need_rebind)

    async def test_teardown_integrity_error_still_attempts_shutdown(self):
        runner, _ = await self.build()
        runner.preflight_until_ready = AsyncMock(return_value="RUNNING")
        runner.request_stop("operator_stop")
        runner._settle_stream_readers = AsyncMock(side_effect=LedgerConflict("conflicting fill"))
        runner._shutdown = AsyncMock()
        await runner.run()
        runner._shutdown.assert_awaited_once()
        self.assertFalse(runner.gate.rest_reconciled)

    async def test_liveness_recovery_does_not_queue_a_duplicate_rebind(self):
        for both in (False, True):
            with self.subTest(both=both):
                runner, _ = await self.build()
                market_task = asyncio.create_task(asyncio.sleep(3600))
                private_task = asyncio.create_task(asyncio.sleep(3600))
                async def checkpoint(task):
                    return task
                runner._source_checkpoint = checkpoint
                def failed_liveness(**_kwargs):
                    raise AdapterError("stream silence")
                runner.private_stream.assert_live = failed_liveness
                if both:
                    runner.market_stream.assert_live = failed_liveness
                runner.private_stream.drain_events = lambda **kwargs: (_ for _ in ()).throw(
                    AdapterError("queued transport failure"))
                runner._recover_private_stream = AsyncMock(return_value=None)
                runner._recover_bound_streams = AsyncMock(return_value=None)
                readers = [market_task, private_task]
                try:
                    market_task, private_task, stop = await runner._consume_ready_stream_tasks(
                        market_task, private_task)
                    readers.extend([market_task, private_task])
                    self.assertIsNone(stop)
                    recovery = (runner._recover_bound_streams if both
                                else runner._recover_private_stream)
                    recovery.assert_awaited_once()
                    self.assertFalse(runner._streams_need_rebind)
                finally:
                    for task in readers:
                        task.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)


class LegacyAccountingLabelTests(TestCase):
    def test_cash_or_mark_change_is_never_called_derived_settlements(self):
        for change in (Decimal(120), Decimal(-120), Decimal("3.45"), Decimal(0)):
            with self.subTest(change=change):
                lines = snapshot_change_lines(
                    {"at": "test", "equity": "1000", "spent": "0", "reward_paid": "0"},
                    equity=Decimal(1000) + change, spent=Decimal(0), paid_reward=Decimal(0))
                report = "\n".join(lines)
                self.assertIn("unattributed remainder", report)
                self.assertIn("NOT trading P&L or settlement proof", report)
                self.assertNotIn("settlements derived", report)
                self.assertNotIn("GAP:", report)

    def test_resolution_delta_is_independent_not_a_false_reconciliation(self):
        report = "\n".join(snapshot_change_lines(
            {"at": "test", "equity": "1000", "spent": "0", "reward_paid": "0", "settled_pnl": "-5"},
            equity=Decimal(1100), spent=Decimal(0), paid_reward=Decimal(0), settled_pnl=Decimal(-6)))
        self.assertIn("resolution-priced P&L change", report)
        self.assertIn("not comparable", report)
        self.assertNotIn("GAP:", report)
