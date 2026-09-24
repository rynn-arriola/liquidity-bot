"""Read coalescing and early exit scheduling; no real venue access."""
import asyncio
from contextlib import ExitStack
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.rest_fallback import RestFallbackRunner


class RestThrottleExitSchedulingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW
        self.sequence = 0

    async def build(self, *, rest=True, mode=None):
        cls = RestFallbackRunner if rest else fixtures.CommissioningRunner
        if mode is not None:
            from liquidity_bot.account_source_runner import AccountSourceRunner
            cls = partial(AccountSourceRunner, account_state_mode=mode)
        with patch.object(fixtures, "CommissioningRunner", cls):
            runner, client, _ = self.fixture.build_runner(
                clock=lambda: self.now, max_markets=32)
        if mode == 'hybrid':
            from test_hybrid_account import SilentStream
            runner._websocket_factory = SilentStream
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        return runner, client

    def position(self, runner, qty="10", slug="market-one"):
        self.sequence += 1
        runner.ledger.apply_position_event(
            "rest", f"scheduling-position-{self.sequence}",
            PositionState(slug, Decimal(qty), self.now), received_at=self.now)

    async def test_failed_equity_bracket_is_not_retried_for_all_32_markets(self):
        runner, _ = await self.build()
        slugs = tuple(f"flat-{n}" for n in range(32))
        runner._bind_markets(slugs)
        self.now += timedelta(seconds=20)
        original_observed_at = runner.risk_guard.equity_observed_at
        visited = []
        market = runner.state_for("market-one").market

        async def decide(slug, now):
            visited.append(slug)
            self.assertFalse(runner._entry_risk_allowed(now, market))
            return None, 0

        with patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_balance", AsyncMock(
                    side_effect=AdapterError("HTTP 429"))) as refresh, \
                patch.object(runner, "_decide_for_market", decide):
            await runner._decision_sweep(slugs)
            self.assertEqual(refresh.await_count, 1)
            self.assertEqual(len(visited), 32)
            self.assertEqual(runner.risk_guard.equity_observed_at, original_observed_at)
            await runner._decision_sweep(slugs)
            self.assertEqual(refresh.await_count, 2)  # Next sweep can recover.

    async def test_fresh_bracket_coalesces_but_age_and_clock_reversal_refresh(self):
        runner, _ = await self.build()
        with patch.object(runner, "_refresh_balance", AsyncMock()) as refresh:
            next_due = await runner._refresh_periodic_balance(self.now + timedelta(seconds=4))
            self.assertEqual(next_due, self.now + timedelta(seconds=5))
            refresh.assert_not_awaited()
            await runner._refresh_periodic_balance(self.now + timedelta(seconds=5))
            await runner._refresh_periodic_balance(self.now - timedelta(seconds=1))
            self.assertEqual(refresh.await_count, 2)

    async def test_periodic_coalescing_does_not_delay_midnight_rollover(self):
        runner, _ = await self.build()
        midnight = self.now.replace(hour=4)
        runner.risk_guard.observe_equity(Decimal(1000), observed_at=midnight - timedelta(seconds=1))
        with patch.object(runner, "_refresh_balance", AsyncMock()) as refresh:
            self.assertEqual(await runner._refresh_periodic_balance(midnight),
                             midnight + timedelta(seconds=5))
            refresh.assert_awaited_once_with(midnight)

    async def test_urgent_phase_services_only_position_then_keeps_normal_proofs(self):
        runner, _ = await self.build()
        runner._bind_markets((*(f"flat-{n}" for n in range(31)), "market-one"))
        self.position(runner)
        events = []

        async def service(slugs, *, refresh_books):
            self.assertFalse(refresh_books)
            events.append(("service", slugs))
            return ""

        async def proof(now):
            events.append(("proof", now))

        async def equity(now):
            events.append(("equity", now))

        async def decide(slug, now):
            events.append(("decide", slug))
            return None, 0

        with patch.object(runner, "_service_bound_markets", service), \
                patch.object(runner, "_refresh_execution_state_for_decision", proof), \
                patch.object(runner, "_refresh_equity_for_decision", equity), \
                patch.object(runner, "_decide_for_market", decide):
            self.assertIsNone(await runner._service_urgent_positions())
        self.assertEqual([event[0] for event in events], ["service", "proof", "equity", "decide"])
        self.assertEqual(events[0][1], ("market-one",))
        self.assertEqual(events[-1][1], "market-one")

    async def test_flat_during_service_or_proof_cannot_become_extra_entry(self):
        for where in ("service", "proof", "equity", "book"):
            runner, client = await self.build()
            self.position(runner)

            async def service(slugs, *, refresh_books):
                self.assertFalse(refresh_books)
                if where == "service":
                    self.position(runner, "0")
                return ""

            async def proof(now):
                if where == "proof":
                    self.position(runner, "0")

            async def equity(now):
                if where == "equity":
                    self.position(runner, "0")

            async def book(slug, now):
                if where == "book":
                    self.position(runner, "0")

            with patch.object(runner, "_service_bound_markets", service), \
                    patch.object(runner, "_refresh_execution_state_for_decision", proof), \
                    patch.object(runner, "_refresh_equity_for_decision", equity), \
                    patch.object(runner, "_service_market_book", book), \
                    patch.object(runner, "_decide_for_market", AsyncMock()) as decide:
                await runner._service_urgent_positions()
                decide.assert_not_awaited()
            self.assertFalse(client.orders.create_calls)

    async def test_stop_during_service_prevents_decision(self):
        runner, _ = await self.build()
        self.position(runner)

        async def service(slugs, *, refresh_books):
            self.assertFalse(refresh_books)
            runner.request_stop("operator_stop")
            return ""

        with patch.object(runner, "_service_bound_markets", service), \
                patch.object(runner, "_decision_sweep", AsyncMock()) as sweep:
            self.assertEqual(await runner._service_urgent_positions(), "operator_stop")
            sweep.assert_not_awaited()

    async def test_covered_position_wakes_at_deadline_not_before(self):
        runner, _ = await self.build()
        self.position(runner)
        self.fixture.seed_working_order(runner, slug="market-one", side="offer", suffix="exit")
        deadline = self.now + timedelta(seconds=1)
        with patch.object(runner, "_position_exit_deadline", return_value=deadline), \
                patch.object(runner, "_service_bound_markets", AsyncMock(return_value="")) as service, \
                patch.object(runner, "_decision_sweep", AsyncMock()) as sweep:
            await runner._service_urgent_positions()
            service.assert_not_awaited()
            self.now = deadline
            await runner._service_urgent_positions()
            service.assert_awaited_once_with(("market-one",), refresh_books=False)
            sweep.assert_awaited_once_with(("market-one",), position_only=True)

    async def test_unknown_backoff_and_isolated_owners_do_not_add_service_reads(self):
        runner, _ = await self.build()
        self.position(runner)
        with patch.object(runner, "_service_bound_markets", AsyncMock()) as service:
            runner._next_decision_at["market-one"] = self.now + timedelta(seconds=1)
            await runner._service_urgent_positions()
            service.assert_not_awaited()
            runner._next_decision_at.clear()
            with patch.object(runner, "_isolated_unexecutable_slugs", return_value={"market-one"}):
                await runner._service_urgent_positions()
                service.assert_not_awaited()
            runner.ledger.prepare_intent("unknown", "PLACE", "market-one", "client-unknown", {},
                                         created_at=self.now)
            await runner._service_urgent_positions()
            service.assert_not_awaited()

    async def test_multiple_positions_and_partial_change_keep_exact_quantity(self):
        runner, _ = await self.build()
        runner._bind_markets(("market-one", "market-two"))
        self.position(runner)
        self.position(runner, "-8", "market-two")
        seen = {}

        async def proof(now):
            self.position(runner, "6")

        async def decide(slug, now):
            seen[slug] = runner.ledger.position(slug)
            return None, 0

        with patch.object(runner, "_service_bound_markets", AsyncMock(return_value="")), \
                patch.object(runner, "_refresh_execution_state_for_decision", proof), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", decide):
            await runner._service_urgent_positions()
        self.assertEqual(seen, {"market-one": Decimal(6), "market-two": Decimal(-8)})

    async def test_websocket_and_flat_sources_have_no_extra_urgent_pass(self):
        for rest in (False, True):
            runner, _ = await self.build(rest=rest)
            if not rest:
                self.position(runner)
            with patch.object(runner, "_service_bound_markets", AsyncMock()) as service:
                self.assertIsNone(await runner._service_urgent_positions())
                service.assert_not_awaited()

    async def test_main_loop_urgent_phase_precedes_periodic_work(self):
        runner, _ = await self.build()
        runner.preflight_until_ready = AsyncMock(return_value="TRADE")
        runner._shutdown = AsyncMock()
        events = []

        async def urgent():
            events.append("urgent")
            return None

        async def equity(now):
            events.append("equity")
            runner.request_stop("test_done")
            return now + timedelta(seconds=5)

        with patch.object(runner, "_service_urgent_positions", urgent), \
                patch.object(runner, "_refresh_periodic_balance", equity), \
                patch.object(runner, "_refresh_balance", AsyncMock()) as raw:
            await asyncio.wait_for(runner.run(), timeout=3)
            raw.assert_not_awaited()
        self.assertEqual(events[:2], ["urgent", "equity"])

    async def _periodic_gap_scenario(self, *, stage, kind, mode='rest', failure=False):
        runner, client = await self.build(mode=mode)
        runner.preflight_until_ready = AsyncMock(return_value='TRADE')
        runner._shutdown = AsyncMock()
        start = self.now
        deadline = start + timedelta(seconds=12)
        events = []
        if kind == 'deadline':
            self.position(runner)

        async def consume(market_task, private_task):
            self.now = max(self.now, start + timedelta(seconds=11))
            events.append('streams')
            return market_task, private_task, None

        def become_urgent():
            self.now += timedelta(seconds=2)
            if kind == 'fill':
                self.position(runner)
            events.append('urgent_now')

        async def equity(now):
            events.append('financial')
            if stage == 'financial':
                become_urgent()
                if failure:
                    raise AdapterError('failed financial proof')
            return now + timedelta(seconds=5)

        async def reconcile(now, **kwargs):
            self.assertEqual(kwargs['tag'], 'periodic')
            self.assertEqual(kwargs['strict_order_markets'], frozenset())
            events.append('execution')
            if stage == 'execution':
                become_urgent()
                if failure:
                    raise AdapterError('failed execution proof')

        async def service(*args, **kwargs):
            events.append('position_service')
            return None

        async def sweep(slugs, *, position_only):
            self.assertTrue(position_only)
            self.assertEqual(slugs, ('market-one',))
            # A successful reducer makes further checkpoints a local no-op.
            self.position(runner, '0')

        async def reporting(now, due):
            events.append('reporting')
            runner.request_stop('test_done')
            return due

        with ExitStack() as stack:
            for name, fn in (
                ('_consume_ready_stream_tasks', consume),
                ('_refresh_periodic_balance', equity),
                ('_rest_reconcile', reconcile),
                ('_service_bound_markets', service),
                ('_decision_sweep', sweep),
                ('_refresh_due_earnings', reporting),
            ):
                stack.enter_context(patch.object(runner, name, fn))
            stack.enter_context(patch.object(runner, '_position_exit_deadline', return_value=deadline))
            stack.enter_context(patch.object(runner, '_position_has_exact_reducer', return_value=kind == 'deadline'))
            await asyncio.wait_for(runner.run(), timeout=3)
        urgent_at = events.index('urgent_now')
        service_at = events.index('position_service', urgent_at)
        next_stage = 'execution' if stage == 'financial' else 'reporting'
        self.assertLess(service_at, events.index(next_stage))
        self.assertIn('streams', events[urgent_at + 1:service_at])
        self.assertEqual(events.count('financial'), 1)
        self.assertEqual(events.count('execution'), 1)
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_new_urgent_position_preempts_next_periodic_stage(self):
        for mode in ('rest', 'hybrid'):
            for stage in ('financial', 'execution'):
                for kind in ('fill', 'deadline'):
                    with self.subTest(mode=mode, stage=stage, kind=kind):
                        await self._periodic_gap_scenario(stage=stage, kind=kind, mode=mode)

    async def test_failed_periodic_stage_still_rechecks_position(self):
        for stage in ('financial', 'execution'):
            with self.subTest(stage=stage):
                await self._periodic_gap_scenario(stage=stage, kind='fill', failure=True)

    async def test_periodic_checkpoint_respects_stop_rebind_and_stream_failure(self):
        for when in ('before', 'during'):
            for condition in ('stop', 'rebind', 'stream_stop', 'error'):
                if when == 'before' and condition in ('stream_stop', 'error'):
                    continue
                with self.subTest(when=when, condition=condition):
                    runner, _ = await self.build()
                    pair = (object(), object())
                    def change():
                        if condition == 'stop':
                            runner.request_stop('operator_stop')
                        elif condition == 'rebind':
                            runner._streams_need_rebind = True
                    async def consume(*args):
                        change()
                        if condition == 'error':
                            raise AdapterError('stream checkpoint failed')
                        return *pair, 'stream_stop' if condition == 'stream_stop' else None
                    if when == 'before':
                        change()
                    with patch.object(runner, '_consume_ready_stream_tasks', AsyncMock(side_effect=consume)) as drain, \
                            patch.object(runner, '_service_urgent_positions', AsyncMock()) as urgent:
                        if condition == 'error':
                            with self.assertRaisesRegex(AdapterError, 'stream checkpoint failed'):
                                await runner._periodic_position_checkpoint(*pair)
                        else:
                            result = await runner._periodic_position_checkpoint(*pair)
                            expected = {'stop':'operator_stop', 'stream_stop':'stream_stop'}.get(condition)
                            self.assertEqual(result, (*pair, expected))
                        urgent.assert_not_awaited()
                        self.assertEqual(drain.await_count, int(when == 'during'))

    async def test_periodic_checkpoint_preserves_replacement_tasks_and_stop_from_service(self):
        runner, _ = await self.build()
        old, new = (object(), object()), (object(), object())
        with patch.object(runner, '_consume_ready_stream_tasks', AsyncMock(return_value=(*new, None))) as drain, \
                patch.object(runner, '_service_urgent_positions', AsyncMock(return_value='exit_stop')) as urgent:
            self.assertEqual(await runner._periodic_position_checkpoint(*old), (*new, 'exit_stop'))
            drain.assert_awaited_once_with(*old)
            urgent.assert_awaited_once()

    async def test_periodic_checkpoint_websocket_mode_remains_noop(self):
        runner, _ = await self.build(mode='websocket')
        pair = (object(), object())
        with patch.object(runner, '_consume_ready_stream_tasks', AsyncMock()) as drain, \
                patch.object(runner, '_service_urgent_positions', AsyncMock()) as urgent:
            self.assertEqual(await runner._periodic_position_checkpoint(*pair), (*pair, None))
            drain.assert_not_awaited()
            urgent.assert_not_awaited()

    async def test_periodic_checkpoint_applies_held_private_event_before_queue_and_service(self):
        for mode in ('rest', 'hybrid'):
            runner, _ = await self.build(mode=mode)
            events = []
            market_task = asyncio.get_running_loop().create_future()
            private_task = asyncio.get_running_loop().create_future()
            private_task.set_result('held_event')
            async def observe(event, now):
                self.assertEqual(event, 'held_event')
                events.append('held')
            async def drain():
                events.append('queued')
            async def service():
                events.append('service')
            with patch.object(runner, '_observe_private', observe), \
                    patch.object(runner, '_drain_private_events', drain), \
                    patch.object(runner, '_service_urgent_positions', service):
                new_market, new_private, stop = await runner._periodic_position_checkpoint(market_task, private_task)
            self.assertIsNone(stop)
            self.assertEqual(events, ['held', 'queued', 'service'])
            self.assertIs(new_market, market_task)
            self.assertIsNot(new_private, private_task)
            new_market.cancel()
            new_private.cancel()
            await asyncio.gather(new_market, new_private, return_exceptions=True)

    async def test_flat_checkpoint_does_not_start_account_or_market_service(self):
        runner, _ = await self.build()
        pair = (object(), object())
        with patch.object(runner, '_consume_ready_stream_tasks', AsyncMock(return_value=(*pair, None))), \
                patch.object(runner, '_service_bound_markets', AsyncMock()) as service, \
                patch.object(runner, '_rest_reconcile', AsyncMock()) as proof:
            self.assertEqual(await runner._periodic_position_checkpoint(*pair), (*pair, None))
            service.assert_not_awaited()
            proof.assert_not_awaited()
