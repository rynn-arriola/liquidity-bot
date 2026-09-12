"""Read coalescing and early exit scheduling; no real venue access."""
import asyncio
from datetime import timedelta
from decimal import Decimal
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

    async def build(self, *, rest=True):
        cls = RestFallbackRunner if rest else fixtures.CommissioningRunner
        with patch.object(fixtures, "CommissioningRunner", cls):
            runner, client, _ = self.fixture.build_runner(
                clock=lambda: self.now, max_markets=32)
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

        async def service(slugs):
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
        for where in ("service", "proof", "equity"):
            runner, client = await self.build()
            self.position(runner)

            async def service(slugs):
                if where == "service":
                    self.position(runner, "0")
                return ""

            async def proof(now):
                if where == "proof":
                    self.position(runner, "0")

            async def equity(now):
                if where == "equity":
                    self.position(runner, "0")

            with patch.object(runner, "_service_bound_markets", service), \
                    patch.object(runner, "_refresh_execution_state_for_decision", proof), \
                    patch.object(runner, "_refresh_equity_for_decision", equity), \
                    patch.object(runner, "_decide_for_market", AsyncMock()) as decide:
                await runner._service_urgent_positions()
                decide.assert_not_awaited()
            self.assertFalse(client.orders.create_calls)

    async def test_stop_during_service_prevents_decision(self):
        runner, _ = await self.build()
        self.position(runner)

        async def service(slugs):
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
            service.assert_awaited_once_with(("market-one",))
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
