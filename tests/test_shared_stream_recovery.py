"""Shared public failure must reach the owner, not amplify across slots."""
import asyncio
import traceback
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.errors import (
    AdapterError, EmptyBookError, MarketStreamUnavailable, RuntimeInvariantViolation)
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.reconciler import WorkingOrder
from liquidity_bot.refreshing_books import RefreshingMarketStream
from liquidity_bot.rest_polling_policy import RestPollingPolicy
from test_refreshing_books import Socket
from tests.test_streams import FakeClient


class SharedStreamRecoveryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def build(self, *, rest=True, capacity=32):
        kind = partial(AccountSourceRunner, account_state_mode="rest",
                       rest_polling_policy=RestPollingPolicy(enabled=True)) if rest else CommissioningRunner
        with patch.object(fixtures, "CommissioningRunner", kind):
            runner, client, _ = self.fixture.build_runner(max_markets=capacity)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        return runner, client

    async def test_drain_failure_yields_full_slate_without_retirement_or_extra_proof(self):
        runner, client = await self.build()
        slugs = ("market-one", *(f"market-{i}" for i in range(31)))
        runner._bind_markets(slugs)
        with patch.object(runner.market_stream, "drain_books", create=True,
                          side_effect=AdapterError("socket lost")) as drain, \
                patch.object(runner, "_refresh_execution_state_for_decision", new=AsyncMock()) as proof, \
                patch.object(runner, "_refresh_equity_for_decision", new=AsyncMock()), \
                patch.object(runner, "_retire_market", new=AsyncMock(return_value=None)) as retire, \
                patch.object(runner, "_decide_for_market", new=AsyncMock(return_value=(None, 0))) as decide:
            self.assertIsNone(await runner._decision_sweep(slugs))
            self.assertIsNone(await runner._decision_sweep(slugs))
        self.assertEqual(drain.call_count, 1)
        self.assertEqual(proof.await_count, 1)
        retire.assert_not_awaited()
        decide.assert_not_awaited()
        self.assertTrue(runner._streams_need_rebind)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=fixtures.NOW))
        self.assertEqual(runner.active_slugs, slugs)
        self.assertFalse(client.orders.create_calls)

    async def test_jit_failure_after_successful_drain_is_shared_in_both_modes(self):
        for rest in (True, False):
            with self.subTest(rest=rest):
                runner, client = await self.build(rest=rest)
                runner.state_for("market-one").next_book_refresh = fixtures.NOW
                with patch.object(runner, "_refresh_execution_state_for_decision", new=AsyncMock()), \
                        patch.object(runner, "_refresh_equity_for_decision", new=AsyncMock()), \
                        patch.object(runner, "_refresh_quiet_book_if_due", new=AsyncMock(
                            side_effect=MarketStreamUnavailable("renewal failed"))), \
                        patch.object(runner, "_retire_market", new=AsyncMock(return_value=None)) as retire, \
                        patch.object(runner, "_decide_for_market", new=AsyncMock(return_value=(None, 0))) as decide:
                    self.assertIsNone(await runner._decision_sweep(("market-one",)))
                self.assertTrue(runner._streams_need_rebind)
                retire.assert_not_awaited()
                decide.assert_not_awaited()
                self.assertFalse(client.orders.create_calls)

    async def test_bound_service_defers_shared_failure_before_later_owners(self):
        for stage in ("guard", "book"):
            with self.subTest(stage=stage):
                runner, _ = await self.build()
                runner._bind_markets(("market-one", "market-two"))
                error = MarketStreamUnavailable("disconnected")
                with patch.object(runner, "_refresh_event_guard", new=AsyncMock(
                        side_effect=error if stage == "guard" else None)) as guard, \
                        patch.object(runner, "_refresh_due_contract_batches", new=AsyncMock()), \
                        patch.object(runner, "_service_market_state", new=AsyncMock(side_effect=error)) as service, \
                        patch.object(runner, "_retire_market", new=AsyncMock()) as retire:
                    self.assertEqual(await runner._service_bound_markets(), "")
                    self.assertEqual(await runner._service_bound_markets(), "")
                self.assertEqual(guard.await_count, 1)
                self.assertEqual(service.await_count, 0 if stage == "guard" else 1)
                retire.assert_not_awaited()
                self.assertTrue(runner._streams_need_rebind)

    async def test_stale_recovery_preserves_shared_scope_and_operator_stop(self):
        runner, _ = await self.build()
        with patch.object(runner, "_refresh_book", new=AsyncMock(
                side_effect=MarketStreamUnavailable("lost"))), \
                patch.object(runner, "_quarantine_working_orders", new=AsyncMock()) as cancel:
            await runner._recover_strategy_state("book_stale", "market-one")
        cancel.assert_not_awaited()
        self.assertTrue(runner._streams_need_rebind)
        runner.request_stop("operator_stop")
        self.assertEqual(await runner._decision_sweep(), "operator_stop")

    async def test_owner_recovery_joins_readers_before_rebind_and_rearms_once(self):
        runner, _ = await self.build()
        error = MarketStreamUnavailable("lost")
        runner._request_market_stream_rebind(error)
        market_task = asyncio.create_task(asyncio.Event().wait())
        private_task = asyncio.create_task(asyncio.Event().wait())
        async def recover(actual):
            self.assertIs(actual, error)
            self.assertTrue(market_task.done())
            self.assertTrue(private_task.done())
            runner._streams_need_rebind = False
            return None
        with patch.object(runner, "_recover_bound_streams", new=AsyncMock(side_effect=recover)) as rebind:
            market, private, stop = await runner._consume_ready_stream_tasks(market_task, private_task)
        self.assertIsNone(stop)
        rebind.assert_awaited_once()
        self.assertIsNone(runner._pending_market_stream_error)
        self.assertFalse(runner._streams_need_rebind)
        await runner._settle_stream_readers(market, private, recovery_pending=True)

    async def test_failed_recovery_retains_hold_and_does_not_start_new_readers(self):
        runner, _ = await self.build()
        error = MarketStreamUnavailable("lost")
        runner._request_market_stream_rebind(error)
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
        with patch.object(runner, "_recover_bound_streams", new=AsyncMock(return_value="operator_stop")):
            market, private, stop = await runner._consume_ready_stream_tasks(*tasks)
        self.assertEqual(stop, "operator_stop")
        self.assertEqual([market, private], tasks)
        self.assertTrue(all(t.done() for t in tasks))
        self.assertTrue(runner._streams_need_rebind)
        self.assertIs(runner._pending_market_stream_error, error)

    async def test_real_bound_recovery_restores_fresh_account_and_stream_without_retiring(self):
        for rest in (True, False):
            with self.subTest(rest=rest):
                runner, client = await self.build(rest=rest)
                old_market, old_private = runner.market_stream, runner.private_stream
                runner._request_market_stream_rebind(MarketStreamUnavailable("market WebSocket disconnected"))
                tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
                market, private, stop = await runner._consume_ready_stream_tasks(*tasks)
                try:
                    self.assertIsNone(stop)
                    self.assertFalse(runner._streams_need_rebind)
                    self.assertTrue(runner.gate.session_ready(runner.ledger, now=fixtures.NOW))
                    self.assertIsNot(runner.market_stream, old_market)
                    self.assertIsNot(runner.private_stream, old_private)
                    self.assertFalse(runner._retired)
                    self.assertFalse(client.orders.create_calls)
                    self.assertEqual(runner.active_slugs, ("market-one",))
                finally:
                    await runner._settle_stream_readers(market, private, recovery_pending=True)
                    await runner.market_stream.close()
                    await runner.private_stream.close()

    async def test_only_rest_bound_recovery_enables_position_preemption(self):
        for rest in (True, False):
            with self.subTest(rest=rest):
                runner, _ = await self.build(rest=rest)
                runner._request_market_stream_rebind(
                    MarketStreamUnavailable("lost"))

                async def open_streams(**_kwargs):
                    runner._streams_need_rebind = False
                    runner._pending_market_stream_error = None

                with patch.object(
                        runner, "_refresh_balance", new=AsyncMock()), patch.object(
                            runner, "_open_streams_for_active_market",
                            new=AsyncMock(side_effect=open_streams)), patch.object(
                                runner, "_quarantine_working_orders",
                                new=AsyncMock(return_value=None)) as quarantine, patch.object(
                                    runner, "_drain_private_events",
                                    new=AsyncMock()):
                    self.assertIsNone(await runner._recover_bound_streams(
                        MarketStreamUnavailable("lost")))

                self.assertEqual(
                    quarantine.await_args.kwargs,
                    {
                        "require_terminal": False,
                        "account_state_already_reconciled": rest,
                        "preempt_positions": rest,
                    },
                )

    async def test_private_recovery_keeps_original_global_quarantine(self):
        runner, _ = await self.build(rest=False)
        with patch.object(
                runner, "_quarantine_working_orders",
                new=AsyncMock(return_value=None)) as quarantine, patch.object(
                    runner, "_wait_for_initial_private_state",
                    new=AsyncMock()), patch.object(
                        runner, "_drain_private_events", new=AsyncMock()):
            self.assertIsNone(await runner._recover_private_stream(
                MarketStreamUnavailable("private lost")))

        quarantine.assert_awaited_once_with(require_terminal=False)

    async def test_rest_recovery_preserves_only_an_exact_reducer(self):
        runner, _ = await self.build(rest=True, capacity=20)
        held = "market-held"
        runner.ledger.apply_position_event(
            "rest", "position-held",
            PositionState(held, Decimal("10"), fixtures.NOW),
            received_at=fixtures.NOW,
        )

        exact = WorkingOrder("reducer", "offer", .51, Decimal("10"))
        wrong_side = WorkingOrder("builder", "bid", .49, Decimal("10"))
        wrong_qty = WorkingOrder("oversized", "offer", .51, Decimal("11"))
        with patch.object(runner, "_working_orders", return_value=(exact,)):
            self.assertTrue(
                runner._preserve_position_orders_during_bound_recovery(held))
        for orders in ((wrong_side,), (wrong_qty,), (exact, wrong_qty), ()):
            with self.subTest(orders=orders), patch.object(
                    runner, "_working_orders", return_value=orders):
                self.assertFalse(
                    runner._preserve_position_orders_during_bound_recovery(held))
        with patch.object(runner, "_working_orders", return_value=(exact,)), \
                patch.object(runner.ledger, "market_has_unresolved_intent",
                             return_value=True):
            self.assertFalse(
                runner._preserve_position_orders_during_bound_recovery(held))
        with patch.object(runner, "_working_orders", return_value=(exact,)), \
                patch.object(runner.ledger, "absent_orders",
                             return_value=[object()]):
            self.assertFalse(
                runner._preserve_position_orders_during_bound_recovery(held))

    async def test_rest_recovery_checks_positions_between_flat_cancels(self):
        runner, _ = await self.build(rest=True, capacity=20)
        held, flat_one, flat_two = "market-held", "market-flat-one", "market-flat-two"
        runner._bind_markets((flat_one, held, flat_two))
        runner.ledger.apply_position_event(
            "rest", "position-held",
            PositionState(held, Decimal("10"), fixtures.NOW),
            received_at=fixtures.NOW,
        )
        self.fixture.seed_working_order(
            runner, slug=held, side="offer", suffix="held-reducer")
        self.fixture.seed_working_order(
            runner, slug=flat_one, side="bid", suffix="flat-one")
        self.fixture.seed_working_order(
            runner, slug=flat_two, side="bid", suffix="flat-two")

        events = []
        original_preserve = runner._preserve_position_orders_during_bound_recovery

        def preserve(market):
            events.append(("inspect", market))
            return original_preserve(market)

        async def cancel(market, *, now):
            events.append(("cancel", market))

        async def service_positions():
            events.append(("position-check", None))
            if any(kind == "cancel" for kind, _ in events):
                return "operator_stop"
            return None

        with patch.object(
                runner, "_rest_reconcile", new=AsyncMock()), patch.object(
                    runner, "_service_bound_recovery_positions",
                    new=AsyncMock(side_effect=service_positions)), patch.object(
                        runner, "_preserve_position_orders_during_bound_recovery",
                        side_effect=preserve), patch.object(
                            runner.kill_switch, "initiate",
                            new=AsyncMock(side_effect=cancel)):
            stop = await runner._quarantine_working_orders(
                require_terminal=False,
                account_state_already_reconciled=True,
                preempt_positions=True,
            )

        self.assertEqual(stop, "operator_stop")
        self.assertEqual(
            next(event for event in events if event[0] == "inspect"),
            ("inspect", held),
        )
        cancelled = [market for kind, market in events if kind == "cancel"]
        self.assertEqual(len(cancelled), 1)
        self.assertNotIn(held, cancelled)

    async def test_rebind_projects_held_private_evidence_before_recovery(self):
        runner, _ = await self.build(rest=False)
        held = object()
        async def value():
            return held
        private = asyncio.create_task(value())
        market = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        runner._request_market_stream_rebind(MarketStreamUnavailable("lost"))
        order = []
        async def observe(event, now):
            self.assertIs(event, held)
            order.append("held")
        async def drain():
            order.append("queued")
        async def recover(error):
            self.assertEqual(order, ["held", "queued"])
            return "operator_stop"
        with patch.object(runner, "_observe_private", new=AsyncMock(side_effect=observe)), \
                patch.object(runner, "_drain_private_events", new=AsyncMock(side_effect=drain)), \
                patch.object(runner, "_recover_bound_streams", new=AsyncMock(side_effect=recover)):
            _, _, stop = await runner._consume_ready_stream_tasks(market, private)
        self.assertEqual(stop, "operator_stop")

    async def test_run_loop_routes_mid_sweep_failure_to_recovery_then_honors_stop(self):
        runner, client = await self.build()
        refresh_guard = runner._refresh_event_guard
        recover = runner._recover_bound_streams
        tripped = False
        async def fail_once():
            nonlocal tripped
            if not tripped:
                tripped = True
                raise MarketStreamUnavailable("market WebSocket disconnected")
            await refresh_guard()
        async def recover_and_stop(error):
            result = await recover(error)
            self.assertIsNone(result)
            runner.request_stop("operator_stop")
            return result
        with patch.object(runner, "preflight_until_ready", new=AsyncMock(return_value=None)), \
                patch.object(runner, "_refresh_event_guard", new=AsyncMock(side_effect=fail_once)), \
                patch.object(runner, "_recover_bound_streams", new=AsyncMock(side_effect=recover_and_stop)) as rebind, \
                patch.object(runner, "_retire_market", new=AsyncMock()) as retire:
            result = await asyncio.wait_for(runner.run(), 5)
        rebind.assert_awaited_once()
        retire.assert_not_awaited()
        self.assertEqual(result.stop_reason, "operator_stop")
        self.assertFalse(client.orders.create_calls)

    async def test_stored_failure_traceback_does_not_grow_across_consumers(self):
        client = FakeClient()
        socket = Socket()
        client.ws.market_socket = socket
        stream = RefreshingMarketStream(client)
        await stream.start(("market-one",))
        self.addAsyncCleanup(stream.close)
        socket.emit("close", None)
        failures = []
        for _ in range(32):
            for method in (stream.drain_books, lambda: stream.fresh_book("market-one"), stream.next_book):
                try:
                    result = method()
                    if asyncio.iscoroutine(result):
                        await result
                except MarketStreamUnavailable as exc:
                    failures.append(exc)
        self.assertEqual(len(failures), 96)
        self.assertEqual(len({id(e) for e in failures}), 96)
        self.assertIsNone(stream._failure.__traceback__)
        self.assertLessEqual(max(len(traceback.extract_tb(e.__traceback__)) for e in failures), 5)
        self.assertTrue(all(e.__cause__ is stream._failure for e in failures))

    async def test_shared_recovery_429_uses_one_coordinated_backoff_not_owner_fanout(self):
        runner, client = await self.build()
        runner._bind_markets(tuple(["market-one", *(f"market-{i}" for i in range(31))]))
        runner._request_market_stream_rebind(MarketStreamUnavailable("lost"))
        bind = runner._open_streams_for_active_market
        attempts = 0
        async def throttled_once(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AdapterError("limited", status_code=429, retry_after_seconds=10)
            await bind(**kwargs)
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
        with patch.object(runner, "_open_streams_for_active_market", new=AsyncMock(side_effect=throttled_once)), \
                patch.object(runner, "_quarantine_working_orders", new=AsyncMock()), \
                patch.object(runner, "sleep", new=AsyncMock()) as backoff, \
                patch.object(runner, "_retire_market", new=AsyncMock()) as retire:
            market, private, stop = await runner._consume_ready_stream_tasks(*tasks)
        try:
            self.assertIsNone(stop)
            self.assertEqual(attempts, 2)
            backoff.assert_awaited_once_with(1)
            retire.assert_not_awaited()
            self.assertEqual(len(runner.active_slugs), 32)
            self.assertFalse(client.orders.create_calls)
        finally:
            await runner._settle_stream_readers(market, private, recovery_pending=True)
            await runner.market_stream.close()
            await runner.private_stream.close()

    async def test_held_private_invariant_prevents_rebind(self):
        runner, client = await self.build(rest=False)
        runner._request_market_stream_rebind(MarketStreamUnavailable("lost"))
        async def fail():
            raise RuntimeInvariantViolation("unowned_order")
        private = asyncio.create_task(fail())
        market = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        with patch.object(runner, "_recover_bound_streams", new=AsyncMock()) as rebind:
            with self.assertRaisesRegex(RuntimeInvariantViolation, "unowned_order"):
                await runner._consume_ready_stream_tasks(market, private)
        rebind.assert_not_awaited()
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=fixtures.NOW))
        self.assertFalse(client.orders.create_calls)

    async def test_market_local_error_still_retires_only_that_owner(self):
        runner, _ = await self.build()
        runner._bind_markets(("market-one", "market-two"))
        with patch.object(runner, "_refresh_execution_state_for_decision", new=AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", new=AsyncMock()), \
                patch.object(runner, "_service_market_book", new=AsyncMock(
                    side_effect=[EmptyBookError("empty market"), None])), \
                patch.object(runner, "_retire_market", new=AsyncMock(return_value=None)) as retire, \
                patch.object(runner, "_decide_for_market", new=AsyncMock(return_value=(None, 0))) as decide:
            await runner._decision_sweep()
        retire.assert_awaited_once_with("market-one", "service_failed_EmptyBookError")
        self.assertEqual(decide.await_args.args[0], "market-two")
        self.assertFalse(runner._streams_need_rebind)
