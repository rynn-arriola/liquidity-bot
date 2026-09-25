import asyncio
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.account_observations import PositionWatch
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.adapters import AsyncReadOnlyGateway
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.live_commissioning import CommissioningRunner


class CatalogWatchTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self, mode="rest"):
        with patch.object(fixtures, "CommissioningRunner", partial(
                AccountSourceRunner, account_state_mode=mode)):
            runner, client, _ = self.fixture.build_runner(clock=lambda: self.now)
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        return runner, client

    async def scan(self, runner, *, on_read=None, count=30):
        self.reads = 0
        async def scan(_runner, *args, **kwargs):
            async def read():
                self.reads += 1
                self.now += timedelta(seconds=1)
                if on_read:
                    on_read(self.reads)
                await asyncio.sleep(0)
                return {}
            for _ in range(count):
                await runner.gateway._call("markets.book(fake)", read)
            return ((), "fake", None)
        with patch.object(CommissioningRunner, "_replacement_candidates_for_top_up", scan):
            return await runner._replacement_candidates_for_top_up(self.now)

    async def test_new_fill_interrupts_scan_without_projecting_or_mutating(self):
        runner, client = await self.build()
        async def fetch(**kwargs):
            self.assertIsNone(runner.gateway._read_checkpoint.get())
            self.assertEqual(runner.gateway._traffic_class_override.get(), "safety")
            return (PositionState(runner.market_slug, Decimal("6.03"), self.now),)
        runner.account.fetch_positions = AsyncMock(side_effect=fetch)
        with self.assertRaises(ApiAdmissionDeferred):
            await self.scan(runner)
        self.assertLess(self.reads, 3)
        self.assertEqual(runner.ledger.position(runner.market_slug), 0)
        self.assertIsNone(runner._rest_poll_completed_at)
        self.assertFalse(runner.gate.rest_reconciled)
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(client.orders.cancel_calls)

    async def test_unchanged_snapshot_finishes_scan_with_bounded_polling(self):
        runner, _ = await self.build()
        runner.account.fetch_positions = AsyncMock(return_value=())
        await self.scan(runner)
        self.assertEqual(self.reads, 30)
        self.assertLessEqual(runner.account.fetch_positions.await_count, 6)
        self.assertIsNone(runner.gateway._read_checkpoint.get())
        self.assertIsNone(runner.gateway._traffic_class_override.get())

    async def test_fill_interrupts_inflight_catalog_read_and_joins_it(self):
        runner, client = await self.build()
        entered, finished = asyncio.Event(), asyncio.Event()

        async def positions(**kwargs):
            await entered.wait()
            return (PositionState(runner.market_slug, Decimal("6"), self.now),)

        async def read():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        async def scan(*args, **kwargs):
            await runner.gateway._call("events.list", read)
            self.fail("interrupted discovery must not publish a selection")

        runner.account.fetch_positions = positions
        with patch.object(CommissioningRunner, "_replacement_candidates_for_top_up", scan):
            try:
                async with asyncio.timeout(2):
                    with self.assertRaises(ApiAdmissionDeferred) as caught:
                        await runner._replacement_candidates_for_top_up(self.now)
            except TimeoutError:
                self.fail("new fill stayed blocked behind an in-flight catalog read")
        self.assertEqual(caught.exception.error_code, "LOCAL_CATALOG_ACCOUNT_CHANGED")
        self.assertTrue(finished.is_set())
        self.assertEqual(runner.ledger.position(runner.market_slug), 0)
        self.assertFalse(runner.gate.rest_reconciled)
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_disabled_optimization_preserves_five_second_watch_cadence(self):
        from liquidity_bot.rest_polling_policy import RestPollingPolicy
        runner, _ = await self.build()
        runner.rest_polling_policy = RestPollingPolicy(enabled=False)
        position = PositionState(runner.market_slug, Decimal("2"), self.now)
        runner.ledger.apply_position_event("test", "held-cadence", position, received_at=self.now)
        runner.account.fetch_positions = AsyncMock(return_value=(position,))
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=False):
            await self.scan(runner)
        self.assertLessEqual(runner.account.fetch_positions.await_count, 6)

    async def test_covered_deadline_interrupts_inflight_read_for_both_signs(self):
        for quantity, side in (("10", "offer"), ("-10", "bid")):
            runner, client = await self.build()
            position = PositionState(runner.market_slug, Decimal(quantity), self.now)
            runner.ledger.apply_position_event("test", "held", position, received_at=self.now)
            self.fixture.seed_working_order(runner, slug=runner.market_slug,
                                            side=side, suffix="covered")
            runner.account.fetch_positions = AsyncMock(return_value=(position,))
            deadline = self.now + timedelta(seconds=61)
            finished = asyncio.Event()
            async def read():
                self.now += timedelta(seconds=2)
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()
            async def scan(*args, **kwargs):
                await runner.gateway._call("events.list", read)
            with patch.object(runner, "_position_exit_deadline", return_value=deadline), \
                    patch.object(CommissioningRunner, "_replacement_candidates_for_top_up", scan):
                self.assertFalse(runner._position_near_its_flatten_deadline(self.now))
                async with asyncio.timeout(2):
                    with self.assertRaises(ApiAdmissionDeferred) as caught:
                        await runner._replacement_candidates_for_top_up(self.now)
            self.assertEqual(caught.exception.error_code, "LOCAL_CATALOG_POSITION_PRIORITY")
            self.assertTrue(finished.is_set())
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_owner_changes_interrupt_inflight_read_in_both_modes(self):
        for mode in ("rest", "websocket"):
            for change in ("stop", "generation", "scope"):
                with self.subTest(mode=mode, change=change):
                    runner, client = await self.build(mode)
                    runner.account.fetch_positions = AsyncMock(return_value=())
                    finished = asyncio.Event()
                    async def read():
                        if change == "stop":
                            runner.request_stop("operator_stop")
                        elif change == "generation":
                            runner.source_control.generation += 1
                        else:
                            runner._active_market_slugs = ()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            finished.set()
                    async def scan(*args, **kwargs):
                        await runner.gateway._call("events.list", read)
                    with patch.object(CommissioningRunner, "_replacement_candidates_for_top_up", scan):
                        async with asyncio.timeout(2):
                            with self.assertRaises(ApiAdmissionDeferred) as caught:
                                await runner._replacement_candidates_for_top_up(self.now)
                    self.assertEqual(caught.exception.error_code, "LOCAL_CATALOG_INTERRUPTED")
                    self.assertTrue(finished.is_set())
                    self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
                    if mode == "websocket":
                        runner.account.fetch_positions.assert_not_awaited()

    async def test_missing_position_is_only_wakeup_not_flat_proof(self):
        runner, _ = await self.build()
        runner.ledger.apply_position_event("test", "held",
            PositionState(runner.market_slug, Decimal("1"), self.now), received_at=self.now)
        runner.account.fetch_positions = AsyncMock(return_value=())
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=False):
            with self.assertRaises(ApiAdmissionDeferred):
                await self.scan(runner)
        self.assertEqual(runner.ledger.position(runner.market_slug), 1)

    async def test_failed_watch_aborts_not_one_failure_per_candidate(self):
        runner, _ = await self.build()
        runner.account.fetch_positions = AsyncMock(side_effect=AdapterError("bad pagination"))
        with self.assertRaises(ApiAdmissionDeferred):
            await self.scan(runner)
        self.assertLess(self.reads, 3)
        self.assertFalse(runner.gate.rest_reconciled)

    async def test_stop_and_generation_change_interrupt_before_next_read(self):
        for mode in ("rest", "websocket"):
            for trigger in ("stop", "generation", "scope"):
                with self.subTest(mode=mode, trigger=trigger):
                    runner, _ = await self.build(mode)
                    def change(count):
                        if trigger == "stop":
                            runner.request_stop("operator_stop")
                        elif trigger == "generation":
                            runner.source_control.generation += 1
                        else:
                            runner._active_market_slugs = ()
                    with self.assertRaises(ApiAdmissionDeferred):
                        await self.scan(runner, on_read=change)
                    self.assertEqual(self.reads, 1)

    async def test_websocket_drains_private_and_does_not_poll_positions(self):
        runner, _ = await self.build("websocket")
        runner.account.fetch_positions = AsyncMock(side_effect=AssertionError("REST watcher in WS"))
        runner._drain_private_events = AsyncMock(return_value=0)
        with patch.object(runner, "_position_near_its_flatten_deadline",
                          side_effect=lambda now: runner._drain_private_events.await_count >= 4):
            with self.assertRaises(ApiAdmissionDeferred):
                await self.scan(runner)
        self.assertLess(self.reads, 4)
        runner.account.fetch_positions.assert_not_awaited()

    async def test_malformed_private_event_revokes_proof_and_requests_rebind(self):
        runner, _ = await self.build("websocket")
        self.assertTrue(runner.gate.rest_reconciled)
        runner._drain_private_events = AsyncMock(side_effect=AdapterError("malformed event"))
        with self.assertRaises(ApiAdmissionDeferred):
            await self.scan(runner)
        self.assertFalse(runner.gate.rest_reconciled)
        self.assertFalse(runner.gate.connected)
        self.assertTrue(runner._streams_need_rebind)

    async def test_cancel_scan_joins_pending_watch(self):
        runner, _ = await self.build()
        entered, finished = asyncio.Event(), asyncio.Event()
        async def fetch(**kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        runner.account.fetch_positions = fetch
        async def blocked_scan(*args, **kwargs):
            await entered.wait()
            await asyncio.Event().wait()
        with patch.object(CommissioningRunner, "_replacement_candidates_for_top_up", blocked_scan):
            task = asyncio.create_task(runner._replacement_candidates_for_top_up(self.now))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())

    async def test_single_flight_and_interval_from_completion(self):
        release = asyncio.Event()
        async def fetch(**kwargs):
            await release.wait()
            return ()
        mock = AsyncMock(side_effect=fetch)
        watch = PositionWatch(mock, lambda: self.now)
        self.addAsyncCleanup(watch.close)
        def start():
            watch.start_if_due(source="rest", generation=1, scope=("a",), interval=5)
        start(); start()
        await asyncio.sleep(0)
        self.assertEqual(mock.await_count, 1)
        self.now += timedelta(seconds=10)
        start()
        self.assertEqual(mock.await_count, 1)
        release.set(); await asyncio.sleep(0)
        result = watch.take_ready()
        self.assertEqual(result.scope, ("a",))
        self.assertEqual(result.started_at, fixtures.NOW)
        start(); await asyncio.sleep(0)
        self.assertEqual(mock.await_count, 1)
        self.now += timedelta(seconds=5)
        start(); await asyncio.sleep(0)
        self.assertEqual(mock.await_count, 2)

    async def test_nested_safety_read_has_no_checkpoint_recursion(self):
        gateway = AsyncReadOnlyGateway(object(), limiter=fixtures.ImmediateLimiter())
        read = AsyncMock(return_value={})
        async def checkpoint():
            self.assertEqual(gateway._traffic_class_override.get(), "safety")
            await gateway._call("GET /v1/portfolio/positions", read)
        check = AsyncMock(side_effect=checkpoint)
        with gateway.read_checkpoints(check), gateway.traffic_class("background"):
            await gateway._call("markets.book(fake)", read)
        self.assertEqual(check.await_count, 1)
        self.assertEqual(read.await_count, 2)
        self.assertIsNone(gateway._read_checkpoint.get())

    async def test_websocket_fill_interrupts_before_inventory_deadline(self):
        runner, client = await self.build("websocket")
        from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
        event = PrivateEvent(PrivateEventKind.POSITION_UPDATE, {
            "marketSlug": runner.market_slug, "netPositionDecimal": "2",
            "updateTime": (self.now + timedelta(seconds=1)).isoformat()}, request_id="position-watch")
        runner.private_stream.drain_events = lambda limit=None: (
            (runner.private_stream.events.pop(0),) if runner.private_stream.events else ())
        def fill(count):
            runner.private_stream.events.append(event)
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=False):
            with self.assertRaises(ApiAdmissionDeferred) as caught:
                await self.scan(runner, on_read=fill)
        self.assertEqual(caught.exception.error_code, "LOCAL_CATALOG_ACCOUNT_CHANGED",
                         str(caught.exception.__cause__))
        self.assertEqual(self.reads, 1)
        self.assertEqual(runner.ledger.position(runner.market_slug), 2)
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_slow_or_future_watch_never_authorizes_entry(self):
        for seconds in (31, -3):
            runner, _ = await self.build()
            async def fetch(**kwargs):
                self.now += timedelta(seconds=seconds)
                return ()
            runner.account.fetch_positions = fetch
            with self.assertRaises(ApiAdmissionDeferred):
                await self.scan(runner)
            self.assertFalse(runner.gate.rest_reconciled)

    async def test_history_identity_adoption_waits_for_owner_and_complete_batch(self):
        from test_venue_state import raw_order, raw_execution
        for outcome in ("success", "failure", "cancel", "obsolete"):
            with self.subTest(outcome=outcome):
                runner, client = await self.build()
                raw = raw_order(id="adopt-me", marketSlug=runner.market_slug)
                runner.ledger.prepare_intent("orphan", "PLACE", runner.market_slug,
                    "owned-client", raw, created_at=self.now)
                runner.ledger.transition_intent("orphan", "SUBMITTING", updated_at=self.now)
                entered, release = asyncio.Event(), asyncio.Event()
                async def positions(**kwargs):
                    await release.wait()
                    if outcome == "failure":
                        raise AdapterError("position page failed")
                    return ()
                async def history(params):
                    entered.set()
                    return {"activities": [{"type": "ACTIVITY_TYPE_TRADE", "trade": {
                        "id": "trade-one", "isAggressor": False,
                        "passiveExecution": raw_execution(order=raw)}}], "eof": True}
                runner.account.fetch_positions = positions
                client.portfolio.activities = history
                original = runner.ledger.resolve_client_order_id
                task = None
                def resolve(*args):
                    self.assertIs(asyncio.current_task(), task)
                    return original(*args)
                # Order collection has no rows in this fixture, hence only
                # the history identity reaches this resolver.
                with patch.object(runner.ledger, "resolve_client_order_id", side_effect=resolve) as resolver:
                    task = asyncio.create_task(runner._collect_rest_snapshots(self.now))
                    await entered.wait()
                    await asyncio.sleep(0)
                    resolver.assert_not_called()
                    self.assertIsNone(original("adopt-me"))
                    if outcome == "cancel":
                        task.cancel()
                    elif outcome == "obsolete":
                        runner.source_control.generation += 1
                    release.set()
                    if outcome == "success":
                        await task
                        self.assertEqual(original("adopt-me"), "owned-client")
                        self.assertEqual(runner._collected_history.executions[0].client_order_id,
                                         "owned-client")
                    else:
                        with self.assertRaises(asyncio.CancelledError if outcome == "cancel" else AdapterError):
                            await task
                        resolver.assert_not_called()
                        self.assertIsNone(original("adopt-me"))

    async def test_exact_order_workers_never_resolve_ownership(self):
        from test_venue_state import raw_order
        runner, client = await self.build()
        for name in ("first", "second"):
            client.orders.rows[name] = raw_order(id=name)
        owner = asyncio.current_task()
        def resolve(exchange_id, raw):
            self.assertIs(asyncio.current_task(), owner)
            return "owned-" + exchange_id
        with patch.object(runner.ledger, "resolve_client_order_id", side_effect=resolve):
            self.assertEqual(await runner._read_exact_order_states(("first", "second"), self.now), [])
        self.assertEqual({r["client_order_id"] for r in runner.ledger.working_orders()},
                         {"owned-first", "owned-second"})

    async def test_cache_revalidation_never_swallows_integrity_failure(self):
        from liquidity_bot.execution_ledger import LedgerConflict
        runner, _ = await self.build()
        selection = fixtures.fake_selection("candidate")
        runner._market_selection = selection
        runner._market_selection_observed_at = self.now
        runner.reward_selector = fixtures.FakeRewardSelector(selection)
        runner.reward_selector.revalidate_viable = AsyncMock(side_effect=LedgerConflict("fill changed"))
        with self.assertRaises(LedgerConflict):
            await runner._replacement_candidates_for_top_up(self.now,
                exclude=set(), wanted=1, allow_cached_selection=True)
        self.assertEqual(runner.reward_selector.calls, [])
        self.assertFalse(runner.gate.rest_reconciled)

    async def test_top_up_integrity_failure_is_terminal_not_retry(self):
        from liquidity_bot.execution_ledger import LedgerConflict
        runner, client, _ = self.fixture.build_runner()
        calls = 0
        async def top_up(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise LedgerConflict("fill changed")
            runner.request_stop("unexpected_retry")
            return False
        # Ordinary owners now run before discovery. Keep this test's account
        # flat so it isolates terminal scan handling, not fake shutdown fills.
        with patch.object(runner, "_incremental_top_up_due", return_value=True), \
                patch.object(runner, "_decision_sweep", new=AsyncMock(return_value=None)) as decide, \
                patch.object(runner, "_top_up_active_markets", side_effect=top_up):
            async with asyncio.timeout(5):
                result = await runner.run()
        self.assertEqual(calls, 1)
        decide.assert_awaited_once()
        self.assertEqual(result.stop_reason, "critical:market_top_up_LedgerConflict")
        self.assertFalse(client.orders.create_calls)

    async def test_real_32_candidate_selector_yields_to_fill_without_partial_commit(self):
        import test_market_selector as selection_fixtures
        from liquidity_bot.market_selector import RewardMarketSelector, RewardSelectionPolicy
        counts = []
        for watched in (False, True):
            runner, client = await self.build()
            slugs = [f"candidate-{i}" for i in range(32)]
            runner.gateway.list_upcoming_sports_markets = AsyncMock(return_value=tuple(
                selection_fixtures.market(slug) for slug in slugs))
            runner.gateway.fetch_incentives = AsyncMock(return_value={slug:
                selection_fixtures.incentive(slug, pool=500) for slug in slugs})
            read_count = 0
            async def fetch_book(slug):
                async def read():
                    nonlocal read_count
                    read_count += 1
                    self.now += timedelta(seconds=1)
                    await asyncio.sleep(0)
                    return selection_fixtures.book(slug)
                return await runner.gateway._call("markets.book(test)", read)
            runner.gateway.fetch_book = fetch_book
            runner.reward_selector = RewardMarketSelector(runner.gateway,
                RewardSelectionPolicy(max_book_candidates=32), clock=lambda: self.now)
            runner.account.fetch_positions = AsyncMock(return_value=(
                PositionState(runner.market_slug, Decimal("6.03"), self.now),))
            original = runner._market_selection
            kwargs = dict(exclude=set(runner.active_slugs), wanted=1, allow_cached_selection=False)
            if watched:
                with self.assertRaises(ApiAdmissionDeferred):
                    await runner._replacement_candidates_for_top_up(self.now, **kwargs)
                self.assertIs(runner._market_selection, original)
                self.assertFalse(runner.gate.rest_reconciled)
            else:
                await CommissioningRunner._replacement_candidates_for_top_up(runner, self.now, **kwargs)
            counts.append(read_count)
            self.assertEqual(runner.ledger.position(runner.market_slug), 0)
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        self.assertEqual(counts, [32, 1])
