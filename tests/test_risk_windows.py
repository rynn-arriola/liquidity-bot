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
from liquidity_bot.execution_ledger import Execution, ExecutionLedger, PositionState
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

    def observe(self, pnl, *, at=START):
        return self.meter.observe(
            settled_pnl=D(pnl), observed_at=at, now=at, full_cap=64,
        )

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
        for loss, cap in ((-20, 30), (0, 30), (5.99, 30), (6, 25),
                          (11.99, 25), (12, 20), (18, 15), (24, 10),
                          (29.99, 10), (30, 0), (36, 0)):
            with self.subTest(loss=loss):
                self.assertEqual(self.policy.cap(D(str(loss)), full_cap=64), cap)
        self.assertEqual(self.policy.cap(D(0), full_cap=8), 8)

    def test_production_config_fixed_dollars_and_weekly_four_window_reserve(self):
        for size, slots in ((5, 20), (10, 32), (30, 24)):
            config = CommissioningConfig("market", True, True, True,
                                         quote_size=D(size), max_markets=slots)
            self.assertEqual(config.hard_stop_loss_usd, D(30))
            self.assertEqual(config.weekly_operating_reserve_usd, D(840))
            self.assertEqual(config.entry_order_cap_for_loss(D(12)), 20)
            for hour in range(24):
                self.assertEqual(config.entry_order_cap_for_time(START + timedelta(hours=hour)), 30)

    def test_authorized_size_fifteen_scales_exposure_and_window_tiers(self):
        config = CommissioningConfig(
            "market", True, True, True, quote_size=D(15), max_markets=32)
        self.assertEqual(config.hard_stop_loss_usd, D(30))
        self.assertEqual(config.max_early_exit_loss_usd, D(12))
        self.assertEqual(config.max_stressed_exit_loss_usd, D(60))
        self.assertEqual(config.max_market_exposure_usd, D(135))
        self.assertEqual(config.max_portfolio_exposure_usd, D(630))
        self.assertEqual(config.execution_fee_observation_usd, D(20))
        self.assertEqual(config.weekly_operating_reserve_usd, D(840))
        self.assertEqual(
            [config.entry_order_cap_for_loss(D(loss))
             for loss in (0, 6, 12, 18, 24, 30)],
            [30, 25, 20, 15, 10, 0])

    def test_cap_recovers_only_in_new_window_and_restart_does_not_rebase(self):
        self.observe(0)
        self.assertEqual(self.observe(-18).cap, 15)
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        self.assertFalse(self.meter.ready(START))
        self.assertEqual(self.observe(-1).cap, 15)
        next_window = START + timedelta(hours=6)
        self.assertFalse(self.meter.ready(next_window))
        result = self.observe(0, at=next_window)
        self.assertEqual(result.opening_equity, D(0))
        self.assertEqual(result.cap, 30)

    def test_close_in_boundary_gap_counts_in_new_window(self):
        self.observe(0)
        self.observe(0, at=START + timedelta(hours=6, seconds=-3))
        result = self.observe(-12, at=START + timedelta(hours=6, seconds=3))
        self.assertEqual(result.opening_equity, D(0))
        self.assertEqual(result.cap, 20)

    def test_new_window_uses_its_own_settled_pnl(self):
        self.observe(-24, at=START + timedelta(hours=23, minutes=59))
        next_day = START + timedelta(days=1)
        result = self.observe(-12, at=next_day)
        self.assertEqual(result.opening_equity, D(0))
        self.assertEqual(self.meter.realized_equity, D(-12))
        self.assertEqual(result.cap, 20)
        result = self.observe(-12, at=next_day + timedelta(seconds=5))
        self.assertEqual(self.meter.realized_equity, D(-12))
        self.assertEqual(result.cap, 20)

    def test_only_settled_pnl_can_change_the_budget(self):
        mark = self.observe(0)
        self.assertFalse(self.meter.provisional)
        self.assertFalse(self.meter.held)
        self.assertEqual(mark.cap, 30)
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        mark = self.observe(-6)
        self.assertFalse(self.meter.provisional)
        self.assertFalse(self.meter.held)
        self.assertEqual(mark.cap, 25)

    def test_false_legacy_cash_cap_is_not_imported(self):
        with self.ledger.connection:
            self.ledger.connection.execute(
                "INSERT INTO execution_risk_windows "
                "(window_start,window_end,opening_equity,opening_marked_equity,"
                "observed_at,day_baseline,policy,entry_cap) VALUES (?,?,?,?,?,?,?,?)",
                (START.isoformat(), (START + timedelta(hours=6)).isoformat(),
                 "100", "100", START.isoformat(), START.isoformat(),
                 self.policy.fingerprint(), 15))
        mark = self.observe("-2.84")
        self.assertEqual(mark.cap, 30)
        old = self.ledger.query(
            "SELECT entry_cap FROM execution_risk_windows")[0]
        self.assertEqual(old["entry_cap"], 15)

    def test_active_legacy_cash_hold_requires_explicit_reconciliation(self):
        with self.ledger.connection:
            self.ledger.connection.execute(
                "INSERT INTO execution_risk_windows "
                "(window_start,window_end,opening_equity,opening_marked_equity,"
                "observed_at,day_baseline,policy,entry_cap,tripped_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (START.isoformat(), (START + timedelta(hours=6)).isoformat(),
                 "100", "100", START.isoformat(), START.isoformat(),
                 self.policy.fingerprint(), 0, START.isoformat()))
        with self.assertRaisesRegex(ValueError, "explicit reconciliation"):
            WindowRiskMeter(self.ledger.connection, self.policy)

    def test_confirmed_stop_survives_restart_and_needs_later_flat_proof(self):
        self.observe(0)
        self.observe(-30)
        self.assertTrue(self.meter.held)
        path = self.ledger.path
        self.ledger.close()
        self.ledger = ExecutionLedger(path)
        self.addCleanup(self.ledger.close)
        self.meter = WindowRiskMeter(self.ledger.connection, self.policy)
        self.observe(10)
        self.assertTrue(self.meter.held)
        self.assertFalse(self.meter.release(now=START, flat_proven=True))
        later = START + timedelta(hours=6)
        self.observe(0, at=later)
        self.assertFalse(self.meter.release(now=later, flat_proven=False))
        self.assertTrue(self.meter.release(now=later, flat_proven=True))
        self.assertTrue(self.meter.ready(later))

    def test_invalid_policy_clock_and_changed_policy_fail_closed(self):
        self.observe(0)
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.meter.observe(settled_pnl=D(0), observed_at=START,
                               now=START + timedelta(hours=6), full_cap=64)
        self.meter = WindowRiskMeter(self.ledger.connection, RiskWindowPolicy(stop_loss_usd=D(25)))
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.observe(0)
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

    def settle_loss(self, runner, loss, *, suffix="loss", at=live.NOW):
        """Record a complete matched pair with exactly ``loss`` gross P&L."""
        quantity = D(100)
        short_frame_price = D("0.5") - D(loss) / quantity
        for name, intent, price in (
                ("open", "ORDER_INTENT_BUY_LONG", D("0.5")),
                ("close", "ORDER_INTENT_BUY_SHORT", short_frame_price)):
            runner.ledger.apply_execution(Execution(
                execution_id=f"{suffix}-{name}", market_slug=f"settled-{suffix}",
                side="BUY", price=price, qty=quantity, fee=D(0),
                intent=intent, event_at=at), received_at=at)

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
        self.assertEqual(runner.risk_guard.current_equity, D(0))
        self.assertEqual(runner._loss_entry_order_cap, 30)
        self.assertFalse(runner._window_risk.provisional)
        self.assertFalse(runner._hard_stop_active())
        self.assertFalse(runner._frame(now).hard_stop_flatten_required)
        self.assertTrue(runner._frame(now).new_risk_allowed)
        self.assertEqual(runner._equity_snapshot.marked_trading_pnl(
            opening_equity=runner._day_opening_equity, nontrading_cash=D(0)), D(-30))
        # Only a completed matched pair confirms the $31 loss.
        self.settle_loss(runner, D(31), suffix="hard")
        client.portfolio.rows = {}
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
        self.assertEqual(restarted.risk_guard.starting_equity, D(0))

    async def test_new_window_needs_new_account_proof_and_preserves_other_pauses(self):
        runner, client, _ = await self.runner()
        self.settle_loss(runner, D(30), suffix="prior-hard")
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
        with self.assertRaisesRegex(AdapterError, "basis is unavailable"):
            await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, previous)
        self.assertFalse(runner._hard_stop_active())
        self.assertIsNone(runner.risk_guard.equity_observed_at)
        self.assertFalse(runner._entry_risk_allowed(live.NOW, runner.state_for(runner.market_slug).market))

    async def test_failed_cash_scan_keeps_window_budget_and_sends_no_order(self):
        runner, client, _ = await self.runner()
        previous = runner._window_risk.current
        client.account.current_balance = D(0)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
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
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=(change,))) as history:
            for _ in range(2):
                await runner._refresh_balance(now)
                self.assertEqual(runner.risk_guard.current_equity, D(0))
                self.assertEqual(runner._loss_entry_order_cap, 30)
                self.assertFalse(runner._hard_stop_active())
            self.assertEqual(history.call_args.kwargs["boundary"], runner._cash_activity_boundary)
        # A similarly backdated deposit is not a trading gain or counted twice.
        deposit = NontradingCashChange(live.NOW + timedelta(seconds=3), D(30))
        client.account.current_balance = D(100)
        now += timedelta(seconds=5)
        with patch("liquidity_bot.runner_account.fetch_nontrading_cash_changes",
                   AsyncMock(return_value=(change, deposit))):
            for _ in range(2):
                await runner._refresh_balance(now)
                self.assertEqual(runner.risk_guard.current_equity, D(0))

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
        self.settle_loss(runner, D(6), suffix="cap")
        await runner._refresh_balance(live.NOW)
        overflow = runner._loss_cap_overflow_order_ids(live.NOW)
        self.assertEqual(len(overflow), 5)
        self.assertNotIn("exchange-reducer", overflow)

    async def test_all_tiers_connect_settled_fills_entry_reducer_and_summary_meter(self):
        from liquidity_bot.summary_report import prepare_summary

        runner, client, notifier = await self.runner(max_markets=32)
        runner._bind_markets(("market-one", "market-two"))
        for number in range(30):
            self.fixture.seed_working_order(
                runner, slug="market-one", side="bid", suffix=f"tier-{number}")
        self.fixture.seed_working_order(
            runner, slug="market-two", side="offer", suffix="tier-reducer")
        runner.ledger.apply_position_snapshot("positioned", (
            PositionState("market-two", D(10), live.NOW),),
            as_of=live.NOW, observed_at=live.NOW, complete_account=True)
        previous = D(0)
        for index, (loss, cap) in enumerate((
                ("0", 30), ("5.999", 30), ("6", 25), ("11.999", 25),
                ("12", 20), ("17.999", 20), ("18", 15),
                ("23.999", 15), ("24", 10), ("29.999", 10), ("30", 0))):
            with self.subTest(loss=loss):
                loss = D(loss)
                if loss != previous:
                    self.settle_loss(runner, loss - previous, suffix=f"tier-{index}")
                previous = loss
                await runner._refresh_balance(live.NOW)
                self.assertEqual(runner.risk_guard.current_equity, -loss)
                self.assertEqual(runner._effective_entry_order_cap(live.NOW), cap)
                overflow = runner._loss_cap_overflow_order_ids(live.NOW)
                self.assertEqual(len(overflow), 30 - cap)
                self.assertNotIn("exchange-tier-reducer", overflow)
                self.assertTrue(runner._loss_cap_blocks_new_entry(live.NOW))
                await runner._send_summary(live.NOW)
                _, model = prepare_summary(notifier.payloads[-1][0])
                self.assertEqual(model["hard_stop_meter"], float(loss / D(30) * 100))
                self.assertIn(("Entries / cap", f"30 / {cap}"), model["risk"])
                self.assertEqual(runner._hard_stop_active(), loss >= 30)
        # A later gain changes the displayed net loss, not a latched HARD/cap.
        self.settle_loss(runner, D("-28.8"), suffix="recovery-gain")
        await runner._refresh_balance(live.NOW)
        await runner._send_summary(live.NOW)
        _, model = prepare_summary(notifier.payloads[-1][0])
        self.assertEqual(model["hard_stop_meter"], 4)
        self.assertEqual(runner._effective_entry_order_cap(live.NOW), 0)
        self.assertTrue(runner._hard_stop_active())

    async def test_partial_close_counts_only_realized_part_not_remaining_paper_loss(self):
        runner, client, _ = await self.runner()
        slug = runner.market_slug
        runner.ledger.apply_execution(Execution(
            execution_id="partial-open", market_slug=slug, side="BUY",
            price=D("0.5"), qty=D(100), fee=D(0),
            intent="ORDER_INTENT_BUY_LONG", event_at=live.NOW),
            received_at=live.NOW)
        client.account.current_balance = D(50)
        client.portfolio.rows = {slug: {
            "netPositionDecimal": "100", "cost": {"value": "50", "currency": "USD"},
            "cashValue": {"value": "45", "currency": "USD"}}}
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D(0))
        # Half closes for $20 against $25 cost. Remaining $25 basis is still
        # open and its paper loss is not charged again.
        client.account.current_balance = D(70)
        client.portfolio.rows[slug].update({
            "netPositionDecimal": "50", "cost": {"value": "25", "currency": "USD"},
            "cashValue": {"value": "20", "currency": "USD"}})
        runner.ledger.apply_execution(Execution(
            execution_id="partial-close", market_slug=slug, side="BUY",
            price=D("0.4"), qty=D(50), fee=D(0),
            intent="ORDER_INTENT_BUY_SHORT", event_at=live.NOW),
            received_at=live.NOW)
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D(-5))
        self.assertEqual(runner._loss_entry_order_cap, 30)
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D(-5))
        self.assertEqual(runner._loss_entry_order_cap, 30)

    async def test_net_flat_cash_retention_cannot_create_false_window_loss(self):
        runner, client, _ = await self.runner()
        # The incident shape: opposite fills economically close the position,
        # REST reports no position, but the matched cash remains unavailable.
        runner.ledger.apply_execution(Execution(
            execution_id="retained-open", market_slug="retained-cash", side="BUY",
            price=D("0.5"), qty=D(100), fee=D(0),
            intent="ORDER_INTENT_BUY_LONG", event_at=live.NOW),
            received_at=live.NOW)
        runner.ledger.apply_execution(Execution(
            execution_id="retained-close", market_slug="retained-cash", side="BUY",
            price=D("0.475"), qty=D(100), fee=D(0),
            intent="ORDER_INTENT_BUY_SHORT", event_at=live.NOW),
            received_at=live.NOW)
        client.account.current_balance = D(80)
        client.portfolio.rows = {}
        await runner._refresh_balance(live.NOW)
        self.assertEqual(runner.risk_guard.current_equity, D("-2.5"))
        self.assertEqual(runner._loss_entry_order_cap, 30)
        self.assertFalse(runner._hard_stop_active())

    async def test_live_summary_separates_daily_and_window_loss_and_pause(self):
        runner, client, notifier = await self.runner()
        self.settle_loss(runner, D(6), suffix="summary")
        await runner._refresh_balance(live.NOW)
        runner._day_opening_equity = D(199)
        await runner._send_summary(live.NOW)
        rendered = json.dumps(notifier.payloads[-1][0])
        self.assertIn("$6.00 / $30.00", rendered)
        self.assertIn("$24.00 (80%)", rendered)
        self.assertIn("0 / 25", rendered)
        self.assertIn("confirmed", rendered)
        runner.clock = lambda: live.NOW + timedelta(seconds=16)
        await runner._send_summary(runner.clock())
        stale = json.dumps(notifier.payloads[-1][0])
        self.assertIn("HARD     UNAVAILABLE", stale)
        self.assertIn("Awaiting fresh window proof", stale)
        self.assertNotIn("$104.00 / $30.00", stale)

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
