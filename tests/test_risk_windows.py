"""Six-hour policy boundaries, settlement, persistence and owner integration."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from liquidity_bot.commissioning import CommissioningConfig, CommissioningRefused
from liquidity_bot.cash_activity import NontradingCashChange
from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import ExecutionLedger, PositionState
from liquidity_bot.risk_windows import (
    DEFAULT_RISK_WINDOW_POLICY_PATH, RiskWindowPolicy, WindowRiskMeter,
    load_risk_window_policy)
from tests import test_live_commissioning as live

UTC = timezone.utc
START = datetime(2026, 9, 15, 4, tzinfo=UTC)
D = Decimal


class RiskWindowTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.ledger = ExecutionLedger(Path(root.name) / "ledger.db")
        self.addCleanup(self.ledger.close)
        self.policy = load_risk_window_policy()
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)

    def observe(self, equity, *, at=START, **kwargs):
        return self.meter.observe(
            equity=D(equity), observed_at=at, now=at, full_cap=64,
            day_baseline=START, **kwargs)

    def test_all_four_windows_and_dst_use_et_wall_clock(self):
        for hour in (0, 6, 12, 18):
            at = START + timedelta(hours=hour)
            self.assertEqual(self.policy.bounds(at), (at, at + timedelta(hours=6)))
            self.assertEqual(self.policy.bounds(at + timedelta(hours=6, microseconds=-1))[0], at)
        for day, utc_hour, elapsed in (("2026-03-08", 5, 5), ("2026-11-01", 4, 7)):
            at = datetime.fromisoformat(day).replace(hour=utc_hour, tzinfo=UTC)
            a, b = self.policy.bounds(at)
            self.assertEqual((b-a).total_seconds(), elapsed * 3600)
        with self.assertRaises(ValueError):
            self.policy.bounds(datetime(2026, 9, 15))

    def test_exact_remaining_budget_tiers_profit_and_full_stop(self):
        for loss, cap in ((-20, 30), (0, 30), (4.99, 30), (5, 25),
                          (9.99, 25), (10, 20), (15, 15), (20, 10), (24.99, 10), (25, 0), (30, 0)):
            with self.subTest(loss=loss):
                self.assertEqual(self.policy.cap(D(str(loss)), full_cap=64), cap)
        self.assertEqual(self.policy.cap(D(0), full_cap=8), 8)

    def test_production_config_fixed_dollars_and_weekly_four_window_reserve(self):
        for size, slots in ((5, 20), (10, 32), (30, 24)):
            config = CommissioningConfig("market", True, True, True,
                                         quote_size=D(size), max_markets=slots)
            self.assertEqual(config.hard_stop_loss_usd, D(25))
            self.assertEqual(config.weekly_operating_reserve_usd, D(700))
            self.assertEqual(config.entry_order_cap_for_loss(D(10)), 20)
            for hour in range(24):
                self.assertEqual(config.entry_order_cap_for_time(START + timedelta(hours=hour)), 30)

    def test_cap_recovers_only_in_new_window_and_restart_does_not_rebase(self):
        self.observe(100)
        self.assertEqual(self.observe(85).cap, 15)
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        self.assertFalse(self.meter.ready(START))
        self.assertEqual(self.observe(99).cap, 15)
        next_window = START + timedelta(hours=6)
        self.assertFalse(self.meter.ready(next_window))
        result = self.observe(99, at=next_window)
        self.assertEqual(result.opening_equity, D(99))
        self.assertEqual(result.cap, 30)

    def test_close_in_boundary_gap_counts_in_new_window(self):
        self.observe(100)
        self.observe(100, at=START + timedelta(hours=6, seconds=-3))
        result = self.observe(90, at=START + timedelta(hours=6, seconds=3))
        self.assertEqual(result.opening_equity, D(100))
        self.assertEqual(result.cap, 20)

    def test_transfers_across_midnight_cannot_hide_or_create_loss(self):
        self.observe(100, at=START + timedelta(hours=23, minutes=59))
        next_day = START + timedelta(days=1)
        # A $50 deposit and $10 closed loss result in raw cash+basis of $140.
        result = self.observe(140, at=next_day, interval_nontrading=D(50))
        self.assertEqual(result.opening_equity, D(100))
        self.assertEqual(self.meter.realized_equity, D(90))
        self.assertEqual(result.cap, 20)
        result = self.observe(110, at=next_day + timedelta(seconds=5), interval_nontrading=D(-30))
        self.assertEqual(self.meter.realized_equity, D(90))
        self.assertEqual(result.cap, 20)

    def test_open_paper_loss_is_provisional_and_does_not_charge_tiers(self):
        self.observe(100)
        mark = self.observe(100, marked_equity=D(60), positions=frozenset({"one"}))
        self.assertTrue(self.meter.provisional)
        self.assertFalse(self.meter.held)
        self.assertEqual(mark.cap, 30)
        self.assertFalse(self.meter.ready(START))
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        self.assertTrue(self.meter.provisional)
        self.observe(100, marked_equity=D(99), positions=frozenset({"one"}))
        self.assertTrue(self.meter.provisional)
        mark = self.observe(95)  # Finally closes for $5 actual loss.
        self.assertFalse(self.meter.provisional)
        self.assertFalse(self.meter.held)
        self.assertEqual(mark.cap, 25)

    def test_confirmed_stop_survives_restart_and_needs_later_flat_proof(self):
        self.observe(100)
        self.observe(75)
        self.assertTrue(self.meter.held)
        path = self.ledger.path
        self.ledger.close()
        self.ledger = ExecutionLedger(path)
        self.addCleanup(self.ledger.close)
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        self.observe(110)
        self.assertTrue(self.meter.held)
        self.assertFalse(self.meter.release(now=START, flat_proven=True))
        later = START + timedelta(hours=6)
        self.observe(110, at=later)
        self.assertFalse(self.meter.release(now=later, flat_proven=False))
        self.assertTrue(self.meter.release(now=later, flat_proven=True))
        self.assertTrue(self.meter.ready(later))

    def test_late_fill_joins_persisted_provisional_cohort(self):
        self.observe(100)
        self.observe(100, marked_equity=D(60), positions=frozenset({"first"}))
        self.observe(100, marked_equity=D(65), positions=frozenset({"first", "late"}))
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        self.observe(95, marked_equity=D(92), positions=frozenset({"late"}))
        self.assertTrue(self.meter.provisional)
        self.assertFalse(self.meter.held)
        self.observe(93)
        self.assertFalse(self.meter.provisional)
        self.assertEqual(self.meter.current.cap, 25)

    def test_provisional_carried_position_closes_next_window_without_forgiveness(self):
        self.observe(100)
        self.observe(100, marked_equity=D(65), positions=frozenset({"one"}))
        later = START + timedelta(hours=6)
        self.observe(100, at=later, marked_equity=D(65), positions=frozenset({"one"}))
        self.assertTrue(self.meter.provisional)
        result = self.observe(70, at=later + timedelta(seconds=5))
        self.assertEqual(result.cap, 0)
        self.assertTrue(self.meter.held)
        self.assertFalse(self.meter.provisional)

    def test_invalid_policy_clock_and_changed_policy_fail_closed(self):
        self.observe(100)
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.meter.observe(equity=D(100), observed_at=START,
                               now=START + timedelta(hours=6), full_cap=64, day_baseline=START)
        self.meter = WindowRiskMeter(self.ledger.connection, RiskWindowPolicy(stop_loss_usd=D(30)))
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.observe(100)
        for amount in ("NaN", "Infinity", "0", "-25"):
            with self.assertRaises(ValueError):
                RiskWindowPolicy(stop_loss_usd=D(amount))
        document = json.loads(DEFAULT_RISK_WINDOW_POLICY_PATH.read_text())
        document["remaining_budget_tiers"][0]["max_pending_entry_orders"] = True
        path = self.ledger.path.parent / "invalid.json"
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            load_risk_window_policy(path)


class WindowRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = live.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def runner(self, **kwargs):
        runner, client, notifier = self.fixture.build_runner(
            risk_window_policy=load_risk_window_policy(), **kwargs)
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        await runner.preflight()
        return runner, client, notifier

    async def test_settlement_only_caps_daily_pnl_and_exact_exit_authority(self):
        runner, client, _ = await self.runner()
        now = live.NOW
        slug = runner.market_slug
        client.account.current_balance = D(60)
        client.portfolio.rows = {slug: {
            "netPositionDecimal": "80", "cost": {"value": "40", "currency": "USD"},
            "cashValue": {"value": "10", "currency": "USD"}, "updateTime": now.isoformat()}}
        await runner._rest_reconcile(now)
        await runner._refresh_balance(now)
        self.assertEqual(runner.risk_guard.current_equity, D(100))
        self.assertEqual(runner._loss_entry_order_cap, 30)
        self.assertTrue(runner._window_risk.provisional)
        self.assertFalse(runner._hard_stop_active())
        self.assertFalse(runner._frame(now).hard_stop_flatten_required)
        self.assertFalse(runner._frame(now).new_risk_allowed)
        self.assertEqual(runner._equity_snapshot.marked_trading_pnl(
            opening_equity=runner._day_opening_equity, nontrading_cash=D(0)), D(-30))
        # Full close finally confirms $26 loss; HARD now requests evacuation.
        client.portfolio.rows = {}
        client.account.current_balance = D(74)
        now += timedelta(seconds=1)
        runner.clock = lambda: now
        await runner._rest_reconcile(now)
        await runner._refresh_balance(now)
        self.assertIsNone(runner._stop_reason(now))
        self.assertTrue(runner._hard_stop_active())
        self.assertEqual(runner._effective_entry_order_cap(now), 0)
        self.assertTrue(runner._frame(now).hard_stop_flatten_required)
        restarted, _, _ = await self.runner(ledger=runner.ledger, client=client, clock=lambda: now)
        self.assertTrue(restarted._hard_stop_active())
        self.assertEqual(restarted.risk_guard.starting_equity, D(100))

    async def test_new_window_needs_new_account_proof_and_preserves_other_pauses(self):
        runner, client, _ = await self.runner()
        client.account.current_balance = D(75)
        await runner._refresh_balance(live.NOW)
        self.assertTrue(runner._hard_stop_active())
        later = runner._window_risk.current.end
        runner.clock = lambda: later
        self.assertFalse(runner._entry_risk_allowed(later, runner.state_for(runner.market_slug).market))
        await runner._refresh_balance(later)
        runner._stop_reason(later)
        self.assertTrue(runner._hard_stop_active())
        await runner._rest_reconcile(later)
        await runner._refresh_balance(later)
        runner._manual_entry_paused = True
        runner._stop_reason(later)
        self.assertFalse(runner._hard_stop_active())
        self.assertTrue(runner._entry_pause_active())
        self.assertEqual(runner._loss_entry_order_cap, 30)

    async def test_missing_open_basis_does_not_charge_entry_spend_as_loss(self):
        runner, client, _ = await self.runner()
        previous = runner.risk_guard.current_equity
        client.account.current_balance = D(50)
        client.portfolio.rows = {runner.market_slug: {
            "netPositionDecimal": "10", "cashValue": {"value": "5", "currency": "USD"}}}
        with self.assertRaisesRegex(AdapterError, "cost is unavailable"):
            await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, previous)
        self.assertFalse(runner._hard_stop_active())
        self.assertIsNone(runner.risk_guard.equity_observed_at)
        self.assertFalse(runner._entry_risk_allowed(live.NOW, runner.state_for(runner.market_slug).market))

    async def test_failed_cash_scan_keeps_window_budget_and_sends_no_order(self):
        runner, client, _ = await self.runner()
        previous = runner._window_risk.current
        client.account.current_balance = D(0)
        with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash_changes",
                   AsyncMock(side_effect=AdapterError("unknown transfer"))):
            with self.assertRaises(AdapterError):
                await runner._refresh_balance(live.NOW)
        self.assertEqual(runner._window_risk.current, previous)
        self.assertFalse(runner._hard_stop_active())
        self.assertEqual(client.orders.create_calls, [])

    async def test_late_posted_transfer_before_last_poll_is_neutral_and_idempotent(self):
        runner, client, _ = await self.runner()
        now = live.NOW + timedelta(seconds=5)
        runner.clock = lambda: now
        await runner._refresh_balance(now)
        # Venue publishes a withdrawal now but dates it before the last poll.
        change = NontradingCashChange(live.NOW + timedelta(seconds=2), D(-30))
        client.account.current_balance = D(70)
        now += timedelta(seconds=5)
        with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=(change,))) as history:
            for _ in range(2):
                await runner._refresh_balance(now)
                self.assertEqual(runner.risk_guard.current_equity, D(100))
                self.assertEqual(runner._loss_entry_order_cap, 30)
                self.assertFalse(runner._hard_stop_active())
            self.assertEqual(history.call_args.kwargs["boundary"], runner._cash_activity_boundary)
        # A similarly backdated deposit is not a trading gain or counted twice.
        deposit = NontradingCashChange(live.NOW + timedelta(seconds=3), D(30))
        client.account.current_balance = D(100)
        now += timedelta(seconds=5)
        with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=(change, deposit))):
            for _ in range(2):
                await runner._refresh_balance(now)
                self.assertEqual(runner.risk_guard.current_equity, D(100))

    async def test_pending_entry_ceiling_excludes_positions_and_reducers(self):
        runner, client, _ = await self.runner(max_markets=32)
        runner._bind_markets(("market-one", "market-two"))
        for number in range(30):
            self.fixture.seed_working_order(runner, slug="market-one", side="bid",
                                            suffix=f"entry-{number}")
        self.fixture.seed_working_order(runner, slug="market-two", side="offer", suffix="reducer")
        runner.ledger.apply_position_snapshot("positioned", (
            PositionState("market-two", D(10), live.NOW),),
            as_of=live.NOW, observed_at=live.NOW, complete_account=True)
        self.assertEqual(runner._loss_cap_overflow_order_ids(live.NOW), frozenset())
        client.account.current_balance = D(95)
        await runner._refresh_balance(live.NOW)
        overflow = runner._loss_cap_overflow_order_ids(live.NOW)
        self.assertEqual(len(overflow), 5)
        self.assertNotIn("exchange-reducer", overflow)

    async def test_partial_close_counts_only_realized_part_not_remaining_paper_loss(self):
        runner, client, _ = await self.runner()
        slug = runner.market_slug
        client.account.current_balance = D(50)
        client.portfolio.rows = {slug: {
            "netPositionDecimal": "100", "cost": {"value": "50", "currency": "USD"},
            "cashValue": {"value": "45", "currency": "USD"}}}
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D(100))
        # Half closes for $20 against $25 cost. Remaining $25 basis is still
        # open and its paper loss is not charged again.
        client.account.current_balance = D(70)
        client.portfolio.rows[slug].update({
            "netPositionDecimal": "50", "cost": {"value": "25", "currency": "USD"},
            "cashValue": {"value": "20", "currency": "USD"}})
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D(95))
        self.assertEqual(runner._loss_entry_order_cap, 25)
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D(95))
        self.assertEqual(runner._loss_entry_order_cap, 25)

    async def test_live_summary_separates_daily_and_window_loss_and_pause(self):
        runner, client, notifier = await self.runner()
        client.account.current_balance = D(95)
        await runner._refresh_balance(live.NOW)
        runner._day_opening_equity = D(199)
        await runner._send_summary(live.NOW)
        rendered = json.dumps(notifier.payloads[-1][0])
        self.assertIn("$5.00 / $25.00", rendered)
        self.assertIn("$20.00 (80%)", rendered)
        self.assertIn("0 / 25", rendered)
        self.assertIn("confirmed", rendered)
        runner.clock = lambda: live.NOW + timedelta(seconds=16)
        await runner._send_summary(runner.clock())
        stale = json.dumps(notifier.payloads[-1][0])
        self.assertIn("HARD     UNAVAILABLE", stale)
        self.assertIn("Awaiting fresh window proof", stale)
        self.assertNotIn("$104.00 / $25.00", stale)

    async def test_cross_boundary_and_stale_samples_cannot_ratchet_budget(self):
        runner, client, _ = await self.runner()
        previous = runner._window_risk.current
        client.account.current_balance = D(40)
        runner.clock = lambda: live.NOW + timedelta(seconds=16)
        with self.assertRaisesRegex(AdapterError, "stale"):
            await runner._refresh_balance(live.NOW)
        self.assertEqual(runner._window_risk.current, previous)
        self.assertFalse(runner._hard_stop_active())
        boundary = previous.end
        runner.clock = lambda: boundary
        with self.assertRaisesRegex(AdapterError, "boundary"):
            await runner._refresh_balance(boundary - timedelta(seconds=1))
        self.assertEqual(runner._window_risk.current, previous)
        self.assertFalse(runner._window_entry_ready(boundary))

    async def test_legacy_active_stop_cannot_be_silently_forgiven_by_migration(self):
        old, _, _ = self.fixture.build_runner()
        old.ledger.record_hard_stop_hold("2026-08-13", at=live.NOW)
        with self.assertRaisesRegex(CommissioningRefused, "existing daily HARD"):
            self.fixture.build_runner(ledger=old.ledger, risk_window_policy=self.policy())

    @staticmethod
    def policy():
        return load_risk_window_policy()
