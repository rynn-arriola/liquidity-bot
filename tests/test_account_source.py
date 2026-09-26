import asyncio
import tempfile
import uuid
import os
import sys
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.account_source import (
    AccountSourceControl, AccountSourcePolicy, SwitchRequest, atomic_write, read_object, startup_mode)
from liquidity_bot.account_source_runner import AccountSourceRunner, PrivateCandidate
from liquidity_bot.account_observations import AccountSnapshotReader
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind as Kind
from liquidity_bot.rest_fallback import RestStateGate
from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import Execution, ExecutionLedger


def request(control, now, target):
    raw = dict(request_id=str(uuid.uuid4()), run_id=control.run_id,
               generation=control.generation, source=control.selected, target=target,
               created_at=now.isoformat(),
               expires_at=(now + timedelta(seconds=300)).isoformat())
    atomic_write(control.request_path, raw)
    return raw


class ControlTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.control = AccountSourceControl("rest-fallback", self.path)

    def test_no_automatic_source_or_unfinished_request_replay(self):
        self.assertEqual(startup_mode(None, self.path), "websocket")
        self.control.persist_selected("rest")
        raw = request(self.control, fixtures.NOW, "websocket")
        next_run = AccountSourceControl(startup_mode(None, self.path), self.path)
        with self.assertRaises(ValueError):
            next_run.take_request(fixtures.NOW)
        self.assertEqual(next_run.selected, "rest")
        self.assertEqual(startup_mode("websocket", self.path), "websocket")
        self.assertFalse(self.control.request_path.exists())

    def test_request_once_generation_source_expiry_and_clock_rollback(self):
        raw = request(self.control, fixtures.NOW, "websocket")
        parsed = self.control.take_request(fixtures.NOW)
        self.assertIsNone(self.control.take_request(fixtures.NOW))
        for seconds, valid in ((-1, False), (0, True), (299, True), (300, False)):
            self.assertEqual(parsed.valid_for(self.control, fixtures.NOW + timedelta(seconds=seconds)), valid)
        self.control.generation += 1
        self.assertFalse(parsed.valid_for(self.control, fixtures.NOW))
        self.control.generation -= 1
        self.control.request = None
        atomic_write(self.control.request_path, raw)
        with self.assertRaises(ValueError):
            self.control.take_request(fixtures.NOW)

    def test_invalid_mode_and_duplicate_saved_keys_fail_closed(self):
        with self.assertRaises(ValueError):
            startup_mode("auto", self.path)
        atomic_write(self.path / "account-source-selection.json", {"mode": "automatic"})
        with self.assertRaises(ValueError):
            startup_mode(None, self.path)

    def test_working_intent_lookup_index_preserves_latest_place(self):
        with closing(ExecutionLedger(self.path / "ledger.db")) as ledger:
            for i, operation in enumerate(("PLACE", "CANCEL", "PLACE")):
                ledger.prepare_intent(str(i), operation, "market", "client", {"n": i},
                    created_at=fixtures.NOW + timedelta(seconds=i))
            query = ("SELECT payload_json FROM execution_intents WHERE operation='PLACE' "
                     "AND client_order_id=? ORDER BY created_at DESC LIMIT 1")
            indexed = [tuple(row) for row in ledger.query(query, ("client",))]
            plan = str([tuple(row) for row in ledger.query("EXPLAIN QUERY PLAN " + query, ("client",))])
            self.assertIn("idx_execution_intents_client_operation_created", plan)
            ledger.connection.execute("DROP INDEX idx_execution_intents_client_operation_created")
            self.assertEqual(indexed, [tuple(row) for row in ledger.query(query, ("client",))])

    def test_atomic_request_never_replaces_another_request(self):
        path = self.control.request_path
        atomic_write(path, {"first": True}, exclusive=True)
        with self.assertRaises(FileExistsError):
            atomic_write(path, {"second": True}, exclusive=True)
        self.assertEqual(read_object(path), {"first": True})
        self.assertEqual(list(self.path.glob("*.tmp")), [])

    def test_source_policy_bounds(self):
        for kwargs in ({"default_mode": "auto"}, {"read_concurrency": 5},
                       {"read_concurrency": True}, {"verification_seconds": 0},
                       {"rest_pending_entry_ceiling": 41}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AccountSourcePolicy(**kwargs)

    def test_supervisor_cannot_reuse_previous_run_selection_proof(self):
        from liquidity_bot.account_source import main
        self.control.persist_selected("rest")
        self.control.publish(fixtures.NOW)
        for identity, expected in ((str(uuid.uuid4()), 1), (self.control.run_id, 0)):
            with patch.dict(os.environ, LIQUIDITY_ACCOUNT_SOURCE_RUN_ID=identity), \
                    patch.object(sys, "argv", ["account_source", "relaunch-ready",
                        "--directory", str(self.path)]), self.assertRaises(SystemExit) as result:
                main()
            self.assertEqual(result.exception.code, expected)


class CandidateStream:
    def __init__(self, client):
        self.client = client
        self.events = asyncio.Queue()
        self.closed = False
        self.initial_state_ready = False

    async def start(self, scope):
        self.scope = tuple(scope)
        orders = await self.client.orders.list({})
        self.events.put_nowait(PrivateEvent(
            Kind.ORDER_SNAPSHOT, {"orders": orders["orders"], "eof": True},
            request_id="candidate-orders", market_scope=self.scope,
            received_at=fixtures.NOW))
        positions = await self.client.portfolio.positions({})
        self.events.put_nowait(PrivateEvent(
            Kind.POSITION_SNAPSHOT, {"positions": positions["positions"], "eof": True},
            request_id="candidate-positions", market_scope=self.scope,
            received_at=fixtures.NOW))
        self.events.put_nowait(PrivateEvent(
            Kind.ACCOUNT_BALANCE_SNAPSHOT, {"balances": []},
            request_id="candidate-balance", received_at=fixtures.NOW))

    async def next_event(self, timeout=None):
        event = await self.events.get()
        if event.kind == Kind.ACCOUNT_BALANCE_SNAPSHOT:
            self.initial_state_ready = True
        return event

    def drain_events(self, *, limit=None):
        result = []
        while not self.events.empty() and (limit is None or len(result) < limit):
            result.append(self.events.get_nowait())
        return tuple(result)

    def assert_live(self, **kwargs):
        if self.closed:
            raise AdapterError("closed candidate")

    async def close(self):
        self.closed = True


class SourceRunnerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self, mode="rest", **kwargs):
        with patch.object(fixtures, "CommissioningRunner", partial(
                AccountSourceRunner, account_state_mode=mode)):
            runner, client, notifier = self.fixture.build_runner(clock=lambda: self.now, **kwargs)
        runner._websocket_factory = CandidateStream
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(self.close_candidate, runner)
        return runner, client, notifier

    async def close_candidate(self, runner):
        if runner._source_candidate:
            await runner._source_candidate.close()
        await runner.private_stream.close()

    async def cancel_task(self, task):
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def switch(self, runner, target):
        request(runner.source_control, self.now, target)
        task = asyncio.create_task(runner.private_stream.next_event())
        for _ in range(12):
            task = await runner._source_checkpoint(task)
            await asyncio.sleep(0)
            if runner.source_control.request is None:
                break
        await self.cancel_task(task)

    async def test_switch_both_directions_same_executor_and_ledger(self):
        runner, client, _ = await self.build()
        executor, ledger, reconciler = runner.executor, runner.ledger, runner.reconciler
        await self.switch(runner, "websocket")
        self.assertEqual(runner.account_state_mode, "websocket")
        self.assertEqual(runner.source_control.generation, 1)
        self.assertFalse(isinstance(runner.gate, RestStateGate))
        self.assertIsNone(runner.PENDING_ENTRY_ORDER_CEILING)
        await self.switch(runner, "rest")
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertEqual(runner.source_control.generation, 2)
        self.assertIs(runner.executor, executor)
        self.assertIs(runner.ledger, ledger)
        self.assertIs(runner.reconciler, reconciler)
        self.assertEqual(runner.PENDING_ENTRY_ORDER_CEILING, 30)
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        self.assertEqual(startup_mode(None, runner.ledger.path.parent), "rest")

    async def test_window_budget_survives_rest_websocket_handoff(self):
        from liquidity_bot.risk_windows import RiskWindowPolicy, load_risk_window_policy
        runner, client, _ = await self.build(risk_window_policy=RiskWindowPolicy())
        meter = runner._window_risk
        # A complete matched pair realizes a $6 loss, reaching the first
        # scaled reducer tier. Cash by itself is not a
        # window-loss input, regardless of the selected account transport.
        for name, intent, price in (
                ("open", "ORDER_INTENT_BUY_LONG", Decimal("0.5")),
                ("close", "ORDER_INTENT_BUY_SHORT", Decimal("0.44"))):
            runner.ledger.apply_execution(Execution(
                execution_id=f"handoff-{name}", market_slug="handoff-market",
                side="BUY", price=price, qty=Decimal("100"), fee=Decimal("0"),
                intent=intent, event_at=self.now), received_at=self.now)
        await runner._refresh_balance(self.now)
        self.assertEqual(runner._loss_entry_order_cap, 25)
        for target in ("websocket", "rest"):
            await self.switch(runner, target)
            self.assertEqual(runner.account_state_mode, target)
            self.assertIs(runner._window_risk, meter)
            self.assertEqual(runner._loss_entry_order_cap, 25)
            self.assertEqual(runner.risk_guard.starting_equity, Decimal("0"))
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_silent_candidate_cannot_switch_and_old_source_survives(self):
        runner, client, _ = await self.build()
        class Silent(CandidateStream):
            async def start(self, scope):
                self.scope = tuple(scope)
        runner._websocket_factory = Silent
        await self.switch(runner, "websocket")
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertIsNotNone(runner._source_candidate)
        self.now += timedelta(seconds=301)
        await runner._source_checkpoint()
        self.assertIsNone(runner._source_candidate)
        self.assertEqual(runner.source_control.state, "failed")
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_stop_cancels_request_without_changing_source(self):
        runner, _, _ = await self.build()
        request(runner.source_control, self.now, "websocket")
        await runner._source_checkpoint(allow_commit=False)
        runner.request_stop("operator_test")
        await runner._source_checkpoint()
        self.assertIsNone(runner._source_candidate)
        self.assertEqual(runner.account_state_mode, "rest")

    async def test_bad_target_account_proof_rolls_back_without_order_mutation(self):
        runner, client, _ = await self.build()
        old_stream = runner.private_stream
        with patch.object(runner, "_refresh_balance", AsyncMock(side_effect=AdapterError("503"))):
            await self.switch(runner, "websocket")
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertIs(runner.private_stream, old_stream)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertEqual(startup_mode(None, runner.ledger.path.parent), "rest")
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        await runner._rest_reconcile(self.now + timedelta(seconds=1))
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_urgent_exit_aborts_handoff_not_position_work(self):
        runner, _, _ = await self.build()
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=True):
            await self.switch(runner, "websocket")
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertEqual(runner.source_control.reason, "deferred_for_position_exit")

    async def test_candidate_partial_snapshot_and_overflow_never_ready(self):
        runner, _, _ = await self.build()
        stream = CandidateStream(None)
        stream.initial_state_ready = True
        candidate = PrivateCandidate(stream, ("market-one",))
        candidate.started = True
        candidate.collect(PrivateEvent(Kind.ORDER_SNAPSHOT,
            {"orders": [], "eof": False}, "partial", market_scope=("market-one",)))
        self.assertFalse(candidate.ready(runner.ledger))
        candidate.MAX_EVENTS = 1
        with self.assertRaises(AdapterError):
            candidate.collect(PrivateEvent(Kind.HEARTBEAT, {}))

    async def test_rest_collector_is_single_writer_and_failure_revokes_proof(self):
        runner, client, _ = await self.build()
        with patch.object(client.portfolio, "activities", AsyncMock(side_effect=AdapterError("503"))):
            with self.assertRaises(AdapterError):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertIsNone(runner._snapshot_reader.task)

    async def test_candidate_worker_does_not_adopt_orphan_order(self):
        from test_venue_state import raw_order
        runner, client, _ = await self.build()
        raw = raw_order(id="orphan-order", marketSlug=runner.market_slug)
        client.orders.rows["orphan-order"] = raw
        runner.ledger.prepare_intent("orphan", "PLACE", runner.market_slug,
            "owned-client", raw, created_at=self.now)
        runner.ledger.transition_intent("orphan", "SUBMITTING", updated_at=self.now)
        request(runner.source_control, self.now, "websocket")
        await runner._source_checkpoint(None)
        candidate = runner._source_candidate
        self.assertIsNotNone(candidate)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await candidate.freeze()
        self.assertTrue(candidate.orders)
        self.assertIsNone(runner.ledger.resolve_client_order_id("orphan-order"))
        await runner._project_candidate_events(candidate)
        self.assertEqual(runner.ledger.resolve_client_order_id("orphan-order"), "owned-client")
        self.assertTrue(candidate.agrees(runner.ledger))

    async def test_generation_change_during_admission_prevents_submission(self):
        runner, client, _ = await self.build()
        class Admission:
            async def wait(inner):
                runner.source_control.generation += 1
        runner.gateway.limiter = Admission()
        with self.assertRaises(AdapterError):
            await runner.executor.gateway.create({"marketSlug": "market-one"})
        self.assertFalse(client.orders.create_calls)
        self.assertEqual(runner._source_mutations_in_flight, 0)

    async def test_inflight_mutation_defers_source_commit(self):
        runner, _, _ = await self.build("websocket")
        request(runner.source_control, self.now, "rest")
        runner._source_mutations_in_flight = 1
        task = asyncio.create_task(runner.private_stream.next_event())
        returned = await runner._source_checkpoint(task)
        self.assertIs(returned, task)
        self.assertEqual(runner.account_state_mode, "websocket")
        runner._source_mutations_in_flight = 0
        returned = await runner._source_checkpoint(task)
        await self.cancel_task(returned)
        self.assertEqual(runner.account_state_mode, "rest")

    async def test_cancelled_handoff_restores_mode_and_never_persists_target(self):
        runner, _, _ = await self.build()
        with patch.object(runner, "_refresh_balance", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.switch(runner, "websocket")
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertEqual(startup_mode(None, runner.ledger.path.parent), "rest")
        self.assertFalse(runner._source_switching)

    async def test_open_position_reducer_and_deadlines_survive_both_switches(self):
        from test_venue_state import raw_order
        runner, client, _ = await self.build()
        slug = runner.market_slug
        self.fixture.seed_working_order(runner, slug=slug, side="offer", suffix="reducer")
        client.orders.rows["exchange-reducer"] = raw_order(
            id="exchange-reducer", marketSlug=slug, side="SELL", quantity="10", cumQuantity="0",
            state="ORDER_STATE_NEW", createTime=self.now.isoformat(),
            price={"value": "0.49", "currency": "USD"})
        client.portfolio.rows[slug] = {
            "netPositionDecimal": "10", "cost": {"value": "4.90", "currency": "USD"}}
        await runner._rest_reconcile(self.now)
        basis = runner.ledger.inventory_basis(slug)
        deadline = runner._position_exit_deadline(slug)
        state = runner.state_for(slug)
        risk_guard = runner.risk_guard
        for mode in ("websocket", "rest"):
            await self.switch(runner, mode)
            self.assertEqual(runner.account_state_mode, mode)
            self.assertEqual(runner.ledger.position(slug), Decimal(10))
            self.assertEqual(runner.ledger.inventory_basis(slug).opened_at, basis.opened_at)
            self.assertEqual(runner._position_exit_deadline(slug), deadline)
            self.assertIs(runner.state_for(slug), state)
            self.assertIs(runner.risk_guard, risk_guard)
            self.assertEqual(len(runner.ledger.working_orders()), 1)
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_private_position_disagreement_cannot_promote(self):
        from liquidity_bot.execution_ledger import PositionState
        runner, _, _ = await self.build()
        stream = CandidateStream(None)
        stream.initial_state_ready = True
        candidate = PrivateCandidate(stream, (runner.market_slug,))
        candidate.started = True
        candidate.collect(PrivateEvent(Kind.POSITION_SNAPSHOT,
            {"positions": {}, "eof": True}, "empty-position",
            market_scope=(runner.market_slug,), received_at=self.now))
        runner.ledger.apply_position_snapshot("held", (
            PositionState(runner.market_slug, Decimal(10), self.now),),
            as_of=self.now, observed_at=self.now, complete_account=True)
        self.assertTrue(candidate.ready(runner.ledger))
        self.assertFalse(candidate.agrees(runner.ledger))

    async def test_explicit_rest_switch_works_in_dead_private_recovery(self):
        runner, client, _ = await self.build("websocket")
        runner.gate.on_disconnect()
        errors = [AdapterError("dead private stream")]
        def drain(*, limit=None):
            if errors:
                raise errors.pop()
            return ()
        runner.private_stream.drain_events = drain
        request(runner.source_control, self.now, "rest")
        self.assertTrue(await runner._account_source_recovery_checkpoint())
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_unknown_order_identity_during_exact_read_cannot_write_ledger(self):
        from test_venue_state import raw_order
        runner, client, _ = await self.build()
        client.orders.retrieve = AsyncMock(return_value={"order": raw_order(id="wrong-id")})
        before = runner.ledger.working_orders()
        failures = await runner._read_exact_order_states(("expected-id",), self.now)
        self.assertEqual(len(failures), 1)
        self.assertEqual(runner.ledger.working_orders(), before)

    async def test_late_event_during_balance_is_checked_before_promotion(self):
        runner, client, _ = await self.build()
        original = runner._refresh_balance
        async def balance(now):
            result = await original(now)
            if runner._source_switching:
                runner.private_stream.events.put_nowait(PrivateEvent(
                    Kind.ORDER_SNAPSHOT, {"orders": [], "eof": False},
                    "late-partial", market_scope=tuple(runner.active_slugs),
                    received_at=self.now))
            return result
        with patch.object(runner, "_refresh_balance", balance):
            await self.switch(runner, "websocket")
        self.assertEqual(runner.account_state_mode, "rest")
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        self.assertNotIn("late-partial", runner.projector._order_pages)

    async def test_selected_held_event_precedes_later_queued_event(self):
        runner, _, _ = await self.build("websocket")
        first = PrivateEvent(Kind.HEARTBEAT, {"n": 1}, received_at=self.now)
        second = PrivateEvent(Kind.HEARTBEAT, {"n": 2}, received_at=self.now)
        queued = [second]
        runner.private_stream.drain_events = lambda **kw: (queued.pop(0),) if queued else ()
        held = asyncio.create_task(asyncio.sleep(0, result=first))
        await held
        seen = []
        original = runner._observe_private
        async def observe(event, now):
            seen.append(event.payload.get("n"))
            await original(event, now)
        request(runner.source_control, self.now, "rest")
        with patch.object(runner, "_observe_private", observe):
            result = await runner._source_checkpoint(held)
        await self.cancel_task(result)
        self.assertEqual(seen[:2], [1, 2])
        self.assertEqual(runner.account_state_mode, "rest")

    async def test_switch_does_not_clear_hard_or_operator_maintenance_hold(self):
        runner, _, _ = await self.build()
        runner._manual_entry_paused = True
        runner._hard_stop_hold_day = self.now.date()
        maintenance = runner._maintenance_decision
        for mode in ("websocket", "rest"):
            await self.switch(runner, mode)
            self.assertEqual(runner.account_state_mode, mode)
            self.assertTrue(runner._manual_entry_paused)
            self.assertEqual(runner._hard_stop_hold_day, self.now.date())
            self.assertIs(runner._maintenance_decision, maintenance)
            self.assertTrue(runner._entry_pause_active())


class BoundedPrivateQueueTests(IsolatedAsyncioTestCase):
    async def test_every_callback_is_bounded_and_overflow_loses_readiness(self):
        import test_streams
        from liquidity_bot.streams import PrivateReadOnlyStream
        for kind in ("message", "error", "close"):
            with self.subTest(kind=kind):
                client = test_streams.FakeClient()
                stream = PrivateReadOnlyStream(client)
                stream.MAX_PENDING_EVENTS = 2
                stream._balance_snapshot_seen = True
                for _ in range(12):
                    client.private_socket.emit(kind, {"heartbeat": 1})
                self.assertEqual(stream._queue.qsize(), 3)
                self.assertTrue(stream.overflowed)
                self.assertFalse(stream.initial_state_ready)

    async def test_retirement_keeps_pool_then_unpumped_shard_tail(self):
        from liquidity_bot.streams import PrivateStreamPool
        from types import SimpleNamespace
        pool = PrivateStreamPool(None)
        first = PrivateEvent(Kind.HEARTBEAT, {"n": 1})
        second = PrivateEvent(Kind.HEARTBEAT, {"n": 2})
        tail = asyncio.Queue()
        tail.put_nowait(second)
        tail.put_nowait(AdapterError("closed"))
        shard = SimpleNamespace(_queue=tail, close=AsyncMock())
        pool._streams.append(shard)
        pool._queue.put_nowait((0, first))
        pool._queue.put_nowait((0, AdapterError("error")))
        result = await pool.retire_with_events()
        self.assertEqual(result, (first, second))
        self.assertEqual(pool.connection_count, 0)


# Replay the real reducer/IOC and 32-owner scheduler regressions against the
# permanent entry point, not only its compatibility REST predecessor.
import test_rest_reducer_continuity as reducer_replay
import test_rest_position_priority as priority_replay
import test_rest_bulk_polling as bulk_replay


class PermanentReducerTests(reducer_replay.RestReducerContinuityTests):
    async def build(self):
        with patch.object(reducer_replay, "RestFallbackRunner", partial(
                AccountSourceRunner, account_state_mode="rest")):
            return await super().build()


class PermanentPriorityTests(priority_replay.RestPositionPriorityTests):
    async def build(self, **kwargs):
        with patch.object(priority_replay, "RestFallbackRunner", partial(
                AccountSourceRunner, account_state_mode="rest")):
            return await super().build(**kwargs)


class PermanentBulkTests(bulk_replay.RestBulkPollingTests):
    async def build(self, count=30):
        with patch.object(bulk_replay, "RestFallbackRunner", partial(
                AccountSourceRunner, account_state_mode="rest")):
            return await super().build(count=count)


class PermanentWebSocketTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)


def _websocket_replay(name):
    async def test(self):
        with patch.object(fixtures, "CommissioningRunner", partial(
                AccountSourceRunner, account_state_mode="websocket")):
            await getattr(self.fixture, name)()
    return test


for _name in (
    "test_shutdown_local_not_sent_does_not_spend_ioc_attempts",
    "test_event_live_cancels_flat_quote_despite_future_clock",
    "test_event_live_forces_exit_without_waiting_inventory_timeout",
    "test_event_unknown_blocks_entry_but_not_passive_reduction",
    "test_unowned_exchange_order_refuses_and_never_mutates",
    "test_a_fill_only_the_venue_saw_still_reaches_the_stop",
    "test_a_cancel_is_not_re_issued_while_the_order_is_unknown",
    "test_only_hard_stop_has_money_loss_authority",
    "test_reward_cash_cannot_mask_the_hard_stop",
    "test_hard_equity_excludes_proven_nontrading_cash",
    "test_shutdown_does_not_submit_a_fourth_ioc",
    "test_unresolved_create_prevents_shutdown_flat_proof",
    "test_position_orders_and_reducers_do_not_consume_entry_cap",
    "test_loss_entry_cap_restart_inherits_tightest_same_day_tier",
    "test_daytime_cap_cancels_only_order_above_forty",
):
    setattr(PermanentWebSocketTests, _name, _websocket_replay(_name))


class ReaderTests(IsolatedAsyncioTestCase):
    async def test_singleflight_cancellation_joins_all_workers(self):
        reader = AccountSnapshotReader()
        started = asyncio.Event()
        active = 0
        async def wait():
            nonlocal active
            active += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
        kwargs = dict(source="rest", generation=1, scope=("one",),
                      clock=lambda: fixtures.NOW, orders=wait, positions=wait, history=wait)
        task = asyncio.create_task(reader.collect(**kwargs))
        await started.wait()
        with self.assertRaises(RuntimeError):
            await reader.collect(**kwargs)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(active, 0)
        self.assertIsNone(reader.task)
