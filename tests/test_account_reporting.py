"""Fast settled-window proof and independently complete cash reporting."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from tests import test_live_commissioning as fixtures
from tests import test_risk_windows
from liquidity_bot.cash_activity import NontradingCashChange
from liquidity_bot.errors import AdapterError
from liquidity_bot.risk_windows import RiskWindowPolicy, load_risk_window_policy


class AccountReportingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self, *, legacy=False, mode=None):
        cls = fixtures.CommissioningRunner
        if mode is not None:
            from liquidity_bot.account_source_runner import AccountSourceRunner
            cls = partial(AccountSourceRunner, account_state_mode=mode)
        with patch.object(fixtures, "CommissioningRunner", cls):
            runner, client, notifier = self.fixture.build_runner(
                clock=lambda: self.now,
                risk_window_policy=None if legacy else RiskWindowPolicy())
        if mode == "hybrid":
            from test_hybrid_account import SilentStream
            runner._websocket_factory = SilentStream
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        await runner.preflight()
        return runner, client, notifier

    async def test_routine_refresh_saves_cash_and_final_balance_but_keeps_safety(self):
        runner, client, _ = await self.build()
        initial_report = runner._account_report
        self.now += timedelta(seconds=5)
        with patch.object(runner.gateway, "_call", wraps=runner.gateway._call) as read:
            await runner._refresh_trading_balance(self.now)
        labels = [call.args[0] for call in read.call_args_list]
        self.assertEqual(labels, ["GET /v1/account/balances",
                                 "GET /v1/portfolio/positions", "GET /v1/account/balances"])
        self.assertEqual(runner.risk_guard.equity_observed_at, self.now)
        self.assertIs(runner._account_report, initial_report)
        self.assertEqual(client.orders.create_calls, [])

    async def test_30_second_due_report_is_complete_and_reuses_current_bracket(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=30)
        with patch.object(runner.gateway, "_call", wraps=runner.gateway._call) as read:
            await runner._refresh_trading_balance(self.now)
        labels = [call.args[0] for call in read.call_args_list]
        self.assertEqual(labels.count("GET /v1/portfolio/positions"), 1)
        self.assertEqual(labels.count("GET /v1/account/balances"), 3)
        self.assertEqual(labels.count("GET /v1/portfolio/activities (cash only)"), 1)
        self.assertEqual(runner._reporting_equity(self.now).observed_at, self.now)

    async def test_six_fast_refreshes_save_ten_requests_against_forced_proof(self):
        runner, _, _ = await self.build()
        with patch.object(runner.gateway, "_call", wraps=runner.gateway._call) as read:
            for _ in range(6):
                self.now += timedelta(seconds=5)
                await runner._refresh_trading_balance(self.now)
            optimized = read.call_count
            read.reset_mock()
            for _ in range(6):
                self.now += timedelta(seconds=5)
                await runner._refresh_balance(self.now)
            forced = read.call_count
        self.assertEqual((optimized, forced), (20, 30))

    async def test_current_balance_never_uses_old_cash_adjustment_for_report(self):
        runner, client, _ = await self.build()
        old_report = runner._reporting_equity(self.now)
        client.account.current_balance = D(150)
        self.now += timedelta(seconds=5)
        await runner._refresh_trading_balance(self.now)
        self.assertEqual(runner.balance.current_balance, D(150))
        self.assertIs(runner._reporting_equity(self.now), old_report)
        self.assertEqual(runner.risk_guard.current_equity, D(0))
        self.assertNotEqual(old_report.balance.current_balance, D(150))

    async def test_due_deposit_is_classified_without_changing_settled_risk(self):
        runner, client, _ = await self.build()
        client.account.current_balance = D(150)
        self.now += timedelta(seconds=30)
        change = NontradingCashChange(self.now - timedelta(seconds=1), D(100))
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=(change,))):
            await runner._refresh_trading_balance(self.now)
        report = runner._account_report
        self.assertEqual(report.equity.marked_trading_pnl(
            opening_equity=report.opening_equity, nontrading_cash=report.nontrading_cash), D(-50))
        self.assertEqual(runner.risk_guard.current_equity, D(0))
        self.assertEqual(runner._loss_entry_order_cap, 30)

    async def test_cash_429_preserves_independent_safety_and_does_not_retry_every_tick(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=30)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(side_effect=AdapterError("limited", status_code=429))) as history:
            await runner._refresh_trading_balance(self.now)
            self.assertEqual(runner.risk_guard.equity_observed_at, self.now)
            self.assertIsNone(runner._reporting_equity(self.now))
            self.assertFalse(runner._cash_report_blocked)
            self.now += timedelta(seconds=5)
            await runner._refresh_trading_balance(self.now)
            self.assertEqual(history.await_count, 1)

    async def test_unknown_cash_still_blocks_and_requires_full_success_to_recover(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=30)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(side_effect=AdapterError("unclassified transfer"))):
            with self.assertRaises(AdapterError):
                await runner._refresh_trading_balance(self.now)
        self.assertTrue(runner._cash_report_blocked)
        self.assertIsNone(runner.risk_guard.equity_observed_at)
        self.now += timedelta(seconds=5)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=())) as history:
            await runner._refresh_trading_balance(self.now)
        history.assert_awaited_once()
        self.assertFalse(runner._cash_report_blocked)

    async def test_required_balance_failure_never_becomes_reporting_only(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=5)
        with patch.object(runner.account, "fetch_balance",
                          AsyncMock(side_effect=AdapterError("limited", status_code=429))):
            with self.assertRaises(AdapterError):
                await runner._refresh_trading_balance(self.now)
        self.assertIsNone(runner.risk_guard.equity_observed_at)

    async def test_urgent_position_defers_report_without_postponing_due_time(self):
        runner, _, _ = await self.build()
        attempted = runner._cash_report_attempted_at
        self.now += timedelta(seconds=30)
        with patch.object(runner, "_position_near_its_flatten_deadline", return_value=True), \
                patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", AsyncMock()) as history:
            await runner._refresh_trading_balance(self.now)
        history.assert_not_awaited()
        self.assertEqual(runner._cash_report_attempted_at, attempted)
        self.now += timedelta(seconds=1)
        await runner._refresh_trading_balance(self.now)
        self.assertEqual(runner._account_report.equity.observed_at, self.now)

    async def test_window_boundary_and_midnight_force_complete_cash_proof(self):
        for boundary in (fixtures.NOW.replace(hour=10), fixtures.NOW.replace(hour=4) + timedelta(days=1)):
            self.now = fixtures.NOW
            runner, _, _ = await self.build()
            self.now = boundary
            with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                       AsyncMock(return_value=())) as history:
                await runner._refresh_trading_balance(self.now)
            history.assert_awaited_once()

    async def test_full_refresh_and_disabled_optimization_remain_synchronous(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=5)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=())) as history:
            await runner._refresh_balance(self.now)
            runner.config = replace(runner.config, cash_history_refresh_seconds=0)
            await runner._refresh_trading_balance(self.now)
        self.assertEqual(history.await_count, 2)

    async def test_legacy_risk_never_uses_deferred_cash(self):
        runner, _, _ = await self.build(legacy=True)
        self.now += timedelta(seconds=5)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash",
                   AsyncMock(return_value=D(0))) as history:
            await runner._refresh_trading_balance(self.now)
        history.assert_awaited_once()

    async def test_report_age_and_baseline_mismatch_do_not_renew_account_values(self):
        runner, _, _ = await self.build()
        self.assertIsNone(runner._reporting_equity(self.now - timedelta(seconds=1)))
        self.assertIsNone(runner._reporting_equity(self.now + timedelta(seconds=46)))
        runner._cash_baseline += D(1)
        self.assertIsNone(runner._reporting_equity(self.now))

    async def test_cancelled_refresh_restores_forced_proof_context(self):
        runner, _, _ = await self.build()
        with patch.object(runner, "_refresh_balance", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await runner._refresh_trading_balance(self.now)
        self.assertFalse(runner._defer_cash_reporting)

    async def test_fast_proof_keeps_all_loss_tiers_and_hard_latch(self):
        runner, _, _ = await self.build()
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", AsyncMock()) as history:
            for index, cap in enumerate((25, 20, 15, 10, 0)):
                self.now += timedelta(seconds=1)
                test_risk_windows.WindowRunnerTests.settle_loss(
                    self, runner, D(6), suffix=f"fast-{index}", at=self.now)
                await runner._refresh_trading_balance(self.now)
                self.assertEqual(runner._effective_entry_order_cap(self.now), cap)
                self.assertEqual(runner.risk_guard.current_equity, -D(6 * (index + 1)))
        history.assert_not_awaited()
        self.assertTrue(runner._hard_stop_active())
        self.assertTrue(runner._frame(self.now).hard_stop_flatten_required)

    async def test_pending_hint_or_uncovered_position_yields_optional_work(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=30)
        with patch.object(runner, "_entry_observation_pending", return_value=True), \
                patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", AsyncMock()) as history:
            await runner._refresh_trading_balance(self.now)
        history.assert_not_awaited()
        with patch.object(runner, "_position_priority_tokens", return_value={"held": ()}), \
                patch.object(runner, "_position_priority_rank", return_value=1), \
                patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", AsyncMock()) as history:
            await runner._refresh_trading_balance(self.now)
        history.assert_not_awaited()

    async def test_cash_race_keeps_original_report_and_safety_age(self):
        runner, client, _ = await self.build()
        old_report = runner._account_report
        self.now += timedelta(seconds=30)
        async def moved_cash(*args, **kwargs):
            client.account.current_balance = D(200)
            return ()
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", moved_cash):
            await runner._refresh_trading_balance(self.now)
        self.assertIs(runner._account_report, old_report)
        self.assertIsNone(runner._reporting_equity(self.now))
        self.assertEqual(runner.balance.current_balance, D(100))
        self.assertEqual(runner.risk_guard.equity_observed_at, self.now)

    async def test_slow_cash_cannot_renew_safety_or_report_timestamp(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=30)
        started = self.now
        async def slow_cash(*args, **kwargs):
            self.now += timedelta(seconds=16)
            return ()
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", slow_cash):
            await runner._refresh_trading_balance(started)
        self.assertEqual(runner.risk_guard.equity_observed_at, started)
        self.assertEqual(runner._account_report.equity.observed_at, started)
        self.assertFalse(runner.risk_guard.can_add_new_risk(now=self.now))

    async def test_no_report_or_recovery_always_forces_complete_proof(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=5)
        runner._account_report = None
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes", AsyncMock(return_value=())) as history:
            await runner._refresh_trading_balance(self.now)
            self.assertIsNotNone(runner._account_report)
            runner._transport_recovery_component = "private_stream"
            await runner._refresh_trading_balance(self.now)
        self.assertEqual(history.await_count, 2)

    async def test_optional_history_failure_leaves_shared_execution_circuit_intact(self):
        runner, _, _ = await self.build()
        self.now += timedelta(seconds=30)
        from liquidity_bot.errors import ApiAdmissionDeferred
        error = ApiAdmissionDeferred(
            "activities backoff", operation="account-read:activities",
            error_code="LOCAL_ACCOUNT_READ_COOLDOWN", retry_after_seconds=10)
        original = runner.gateway.ensure_account_reads_available
        async def availability(*endpoints):
            if "activities" in endpoints:
                raise error
            await original(*endpoints)
        with patch.object(runner.gateway, "ensure_account_reads_available", availability):
            await runner._refresh_trading_balance(self.now)
            self.assertIsNone(runner._reporting_equity(self.now))
            with self.assertRaises(ApiAdmissionDeferred):
                await runner.gateway.ensure_account_reads_available("activities")

    async def test_rest_and_hybrid_use_same_fast_financial_proof(self):
        for mode in ("rest", "hybrid"):
            runner, _, _ = await self.build(mode=mode)
            self.now += timedelta(seconds=11)
            with patch.object(runner.gateway, "_call", wraps=runner.gateway._call) as read:
                await runner._refresh_equity_for_decision(self.now)
            labels = [call.args[0] for call in read.call_args_list]
            self.assertEqual(labels, ["GET /v1/account/balances",
                                     "GET /v1/portfolio/positions", "GET /v1/account/balances"])
            self.assertEqual(runner.account_state_mode, mode)

    async def test_cash_refresh_configuration_bounds(self):
        runner, _, _ = await self.build()
        for value, valid in ((0, True), (5, True), (30, True), (60, True),
                             (-1, False), (4, False), (61, False), (True, False), (30.0, False)):
            problems = replace(runner.config, cash_history_refresh_seconds=value).blockers(now=self.now)
            self.assertEqual("cash history refresh must be 0 or 5-60 seconds" not in problems, valid)
