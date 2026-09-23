"""Observability cannot change the risk gate, loss meter or API cadence."""
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.errors import (
    AccountSnapshotConsistencyError, AdapterError, ApiAdmissionDeferred)
from liquidity_bot.cash_activity import CashActivityUnavailable
from liquidity_bot.risk_diagnostics import RiskDiagnostics
from liquidity_bot.risk_windows import RiskWindowPolicy


class RiskDiagnosticsTests(TestCase):
    def test_redaction_dedup_and_bounded_storage(self):
        diag = RiskDiagnostics()
        output = StringIO()
        error = AdapterError("secret server body", status_code=429,
                             error_code="secret-code", trace_id="private-trace")
        with redirect_stdout(output):
            for _ in range(13):
                diag.refresh_failed(fixtures.NOW, stage="cash_history", error=error,
                                    previous_observed_at=fixtures.NOW - timedelta(seconds=9))
            diag.refresh_failed(fixtures.NOW + timedelta(seconds=60), stage="cash_history",
                                error=error, previous_observed_at=None)
        value = output.getvalue()
        self.assertEqual(value.count("[risk-diagnostic]"), 2)
        self.assertIn("status=429", value)
        self.assertIn("previous_age_s=9.000", value)
        for private in ("secret", "private-trace"):
            self.assertNotIn(private, value)
        with redirect_stdout(StringIO()):
            for i in range(40):
                diag.emit(fixtures.NOW, "test", key=i, fields="bounded")
        self.assertEqual(len(diag.last), 32)

    def test_cash_failure_and_local_admission_are_not_claimed_as_http(self):
        for error, reason in (
                (AdapterError("settled cash changed during account equity snapshot"), "cash_changed_positions"),
                (AdapterError("settled cash changed during non-trading activity snapshot"), "cash_changed_history"),
                (ApiAdmissionDeferred("secret"), "local_deferred")):
            with self.subTest(reason=reason), redirect_stdout(StringIO()) as output:
                RiskDiagnostics().refresh_failed(fixtures.NOW, stage="equity_snapshot",
                                                 error=error, previous_observed_at=None)
                self.assertIn(reason, output.getvalue())
                self.assertIn("status=unknown", output.getvalue())

    def test_unknown_transfer_and_transport_are_identified_without_raw_text(self):
        transport = AdapterError("sensitive URL")
        transport.__cause__ = TimeoutError("sensitive URL")
        for error, reason in (
                (CashActivityUnavailable("unclassified completed cash transfer: sensitive memo"), "cash_transfer_unclassified"),
                (CashActivityUnavailable("cash-activity pagination cursor repeated"), "cash_cursor_repeated"),
                (transport, "category=transport")):
            with self.subTest(reason=reason), redirect_stdout(StringIO()) as output:
                RiskDiagnostics().refresh_failed(fixtures.NOW, stage="cash_history",
                                                 error=error, previous_observed_at=None)
                self.assertIn(reason, output.getvalue())
                self.assertNotIn("sensitive", output.getvalue())


class RiskDiagnosticsRunnerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def build(self):
        runner, _, _ = self.fixture.build_runner(risk_window_policy=RiskWindowPolicy())
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        return runner

    async def test_failed_refresh_retains_loss_but_logs_stage_and_blocks_risk(self):
        runner = await self.build()
        before = runner._window_risk.current
        with patch.object(runner.account, "fetch_equity_snapshot", AsyncMock(
                side_effect=AdapterError("secret", status_code=429))), redirect_stdout(StringIO()) as output:
            with self.assertRaises(AdapterError):
                await runner._refresh_balance(fixtures.NOW)
            self.assertFalse(runner._entry_risk_allowed(fixtures.NOW, runner.market))
        self.assertEqual(runner._window_risk.current, before)
        self.assertIsNone(runner.risk_guard.equity_observed_at)
        self.assertIn("stage=equity_snapshot", output.getvalue())
        self.assertIn("equity_unknown", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    async def test_one_cash_consistency_race_retries_whole_snapshot(self):
        runner = await self.build()
        previous = runner.risk_guard.equity_observed_at
        candidate = AsyncMock(side_effect=[
            AccountSnapshotConsistencyError(
                "settled cash changed during account equity snapshot"),
            None,
        ])

        with patch.object(runner, "_refresh_balance_candidate", candidate), \
                redirect_stdout(StringIO()) as output:
            await runner._refresh_balance(fixtures.NOW)

        self.assertEqual(candidate.await_count, 2)
        self.assertEqual(runner.risk_guard.equity_observed_at, previous)
        self.assertNotIn("[risk-diagnostic]", output.getvalue())

    async def test_second_cash_consistency_race_still_blocks_entries(self):
        runner = await self.build()
        candidate = AsyncMock(side_effect=[
            AccountSnapshotConsistencyError(
                "settled cash changed during account equity snapshot"),
            AccountSnapshotConsistencyError(
                "settled cash changed during non-trading activity snapshot"),
        ])

        with patch.object(runner, "_refresh_balance_candidate", candidate), \
                redirect_stdout(StringIO()) as output:
            with self.assertRaises(AccountSnapshotConsistencyError):
                await runner._refresh_balance(fixtures.NOW)

        self.assertEqual(candidate.await_count, 2)
        self.assertIsNone(runner.risk_guard.equity_observed_at)
        self.assertIn("stage=equity_snapshot_retry", output.getvalue())

    async def test_diagnostic_failure_cannot_mask_exception_or_change_boolean(self):
        runner = await self.build()
        error = AdapterError("original")
        with patch.object(runner._risk_diagnostics, "refresh_failed", side_effect=OSError("log failure")), \
                patch.object(runner, "_refresh_balance_candidate", AsyncMock(side_effect=error)):
            with self.assertRaises(AdapterError) as raised:
                await runner._refresh_balance(fixtures.NOW)
        self.assertIs(raised.exception, error)
        with patch.object(runner._risk_diagnostics, "entry_blocked", side_effect=OSError("log failure")):
            self.assertFalse(runner._entry_risk_allowed(fixtures.NOW, runner.market))

    async def test_blockers_distinguish_manual_pause_stale_and_cash_day(self):
        runner = await self.build()
        for change, label in (("manual", "manual_pause"), ("stale", "equity_stale"), ("cash", "cash_day")):
            with self.subTest(change=change):
                runner._manual_entry_paused = change == "manual"
                runner.risk_guard.equity_observed_at = fixtures.NOW - timedelta(seconds=100 if change == "stale" else 0)
                runner._nontrading_cash_day = None if change == "cash" else runner._baseline_day
                with redirect_stdout(StringIO()) as output:
                    self.assertFalse(runner._entry_risk_allowed(fixtures.NOW, runner.market))
                self.assertIn(label, output.getvalue())
