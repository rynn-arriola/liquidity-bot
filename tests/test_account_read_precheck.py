"""Known endpoint outages must not start compound proofs that cannot finish."""
import json
import tempfile
from datetime import timedelta
from functools import partial
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.adapters import AsyncIntervalLimiter, fcntl
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot import read_backoff
from liquidity_bot.errors import ApiAdmissionDeferred
from liquidity_bot.rest_fallback import RestFallbackRunner


class CompoundReadPrecheckTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def build(self):
        factory = getattr(self, "factory", RestFallbackRunner)
        with patch.object(fixtures, "CommissioningRunner", factory):
            runner, _, _ = self.fixture.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        runner.gateway.limiter = AsyncIntervalLimiter()
        return runner

    async def throttle(self, runner, operation):
        await runner.gateway.limiter.record_result(operation=operation,
            duration_seconds=.1, status_code=429, failed=True, retry_after_seconds=10)

    async def test_position_cooldown_prevents_exact_order_walk(self):
        runner = await self.build()
        await self.throttle(runner, "GET /v1/portfolio/positions")
        with patch.object(runner, "_read_owned_order_states", AsyncMock()) as exact, \
                patch.object(runner, "_fetch_rest_order_snapshot", AsyncMock()) as orders:
            with self.assertRaises(ApiAdmissionDeferred):
                await runner._rest_reconcile(fixtures.NOW)
        exact.assert_not_awaited()
        orders.assert_not_awaited()

    async def test_activity_cooldown_prevents_starting_financial_bracket(self):
        runner = await self.build()
        await self.throttle(runner, "GET /v1/portfolio/activities")
        with patch.object(runner.account, "fetch_equity_snapshot", AsyncMock()) as equity:
            with self.assertRaises(ApiAdmissionDeferred):
                await runner._refresh_balance(fixtures.NOW)
        equity.assert_not_awaited()

    async def test_due_exit_defers_optional_earnings_without_moving_due_time(self):
        runner = await self.build()
        due = fixtures.NOW - timedelta(seconds=1)
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=True), \
                patch.object(runner, "_refresh_earnings", AsyncMock()) as earnings:
            self.assertEqual(await runner._refresh_due_earnings(fixtures.NOW, due), due)
        earnings.assert_not_awaited()

    async def test_deferred_earnings_resume_after_exit_priority_clears(self):
        runner = await self.build()
        due = fixtures.NOW - timedelta(seconds=1)
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=False), \
                patch.object(runner, "_refresh_earnings", AsyncMock()) as earnings:
            next_due = await runner._refresh_due_earnings(fixtures.NOW, due)
        earnings.assert_awaited_once()
        self.assertEqual(next_due, fixtures.NOW + timedelta(seconds=runner.config.earnings_refresh_seconds))

    async def test_balance_only_cooldown_does_not_block_execution_proof(self):
        runner = await self.build()
        await self.throttle(runner, "GET /v1/account/balances")
        await runner._rest_reconcile(fixtures.NOW)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=fixtures.NOW))

    async def test_ambiguous_order_repair_not_hidden_by_position_cooldown(self):
        runner = await self.build()
        await self.throttle(runner, "GET /v1/portfolio/positions")
        with patch.object(runner.ledger, "absent_orders", return_value=[{}]), \
                patch.object(runner, "_read_owned_order_states",
                             AsyncMock(side_effect=RuntimeError("repair reached"))) as exact:
            with self.assertRaisesRegex(RuntimeError, "repair reached"):
                await runner._rest_reconcile(fixtures.NOW)
        exact.assert_awaited_once()

    async def test_reporting_stop_future_due_and_failure_are_preserved(self):
        runner = await self.build()
        with patch.object(runner, "_refresh_earnings", AsyncMock()) as earnings:
            due = fixtures.NOW + timedelta(seconds=1)
            self.assertEqual(await runner._refresh_due_earnings(fixtures.NOW, due), due)
            runner._stop_requested = "operator_stop"
            self.assertEqual(await runner._refresh_due_earnings(fixtures.NOW, fixtures.NOW), fixtures.NOW)
            earnings.assert_not_awaited()
        runner._stop_requested = ""
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=False), \
                patch.object(runner, "_refresh_earnings", AsyncMock(side_effect=TimeoutError())), \
                patch.object(runner, "_report_runtime_error", AsyncMock()) as report:
            await runner._refresh_due_earnings(fixtures.NOW, fixtures.NOW)
        self.assertIsNone(runner._earnings_observed_at)
        self.assertTrue(runner._reward_refresh_failed)
        report.assert_awaited_once()


class HybridCompoundReadPrecheckTests(CompoundReadPrecheckTests):
    factory = partial(AccountSourceRunner, account_state_mode="hybrid")


class AdmissionPrecheckTests(IsolatedAsyncioTestCase):
    async def test_peek_does_not_claim_probe_or_grant_later_admission(self):
        limiter = AsyncIntervalLimiter()
        state = {"limited": True, "until": 0}
        limiter._account_read_backoff["account_read_positions"] = state
        for _ in range(5):
            await limiter.ensure_account_reads_available("positions", "balance")
        self.assertEqual(state, {"limited": True, "until": 0})
        self.assertEqual(limiter.telemetry().attempts, 0)
        await limiter.record_result(operation="GET /v1/portfolio/positions",
            duration_seconds=.1, status_code=429, failed=True)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation="GET /v1/portfolio/positions")
        await limiter.wait(operation="cancel", request_kind="mutation")
        await limiter.ensure_account_reads_available("balance")

    async def test_invalid_endpoint_refused(self):
        with self.assertRaises(ValueError):
            await AsyncIntervalLimiter().ensure_account_reads_available("typo")

    async def test_shared_peek_sees_other_lanes_and_does_not_rewrite_state(self):
        if fcntl is None:
            self.skipTest("POSIX shared coordinator")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            first = AsyncIntervalLimiter(shared_state_path=path)
            other = AsyncIntervalLimiter(shared_state_path=path)
            await first.record_result(operation="GET /v1/portfolio/positions",
                duration_seconds=.1, status_code=429, failed=True)
            before = path.read_bytes()
            with self.assertRaises(ApiAdmissionDeferred):
                await other.ensure_account_reads_available("open_orders", "positions", "activities")
            self.assertEqual(path.read_bytes(), before)
            await other.ensure_account_reads_available("balance")
            self.assertEqual(path.read_bytes(), before)
            state = json.loads(before)
            self.assertTrue(state["account_read_positions"]["limited"])
