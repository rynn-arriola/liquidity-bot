"""Offline regressions for the September 6 end-to-end review."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from liquidity_bot.adapters import AdapterError
from liquidity_bot.execution_ledger import LedgerConflict
import test_live_commissioning as fixtures
from test_live_commissioning import NOW, TickingClock


class ReviewRegressions(IsolatedAsyncioTestCase):
    setUp = fixtures.LiveCommissioningTests.setUp
    build_runner = fixtures.LiveCommissioningTests.build_runner

    async def test_fresh_mark_never_reuses_cached_transfer_adjustment(self):
        # Withdrawal, credit masking loss, and offsetting credit/trading with
        # unchanged cash must all scan, even inside the old 15-second cache.
        for cash, adjustment, expected in ((0, -100, 0), (100, 100, -100),
                                           (150, 100, -50)):
            with self.subTest(cash=cash, adjustment=adjustment):
                runner, client, _ = self.build_runner(max_markets=32)
                runner.config = replace(runner.config, quote_size=Decimal(10))
                await runner.preflight()
                moment = NOW + timedelta(seconds=5)
                runner.clock = lambda: moment
                client.account.current_balance = Decimal(cash)
                scan = AsyncMock(return_value=Decimal(adjustment))
                with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash", scan):
                    await runner._refresh_balance(moment)
                scan.assert_awaited_once()
                self.assertEqual(runner.risk_guard.current_equity -
                                 runner.risk_guard.starting_equity, Decimal(expected))
                self.assertEqual(runner.risk_guard.hard_stop_reached(now=moment),
                                 expected <= -90)
                if expected == 0:
                    self.assertEqual(runner._loss_entry_order_cap, 64)
                await runner.private_stream.close()
                await runner.market_stream.close()

    async def test_failed_fresh_transfer_scan_keeps_previous_mark(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        previous = runner.risk_guard.current_equity
        snapshot = runner._equity_snapshot
        client.account.current_balance = Decimal(0)
        moment = NOW + timedelta(seconds=5)
        runner.clock = lambda: moment
        with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash",
                   AsyncMock(side_effect=AdapterError("cash history unavailable"))):
            with self.assertRaises(AdapterError):
                await runner._refresh_balance(moment)
        self.assertIs(runner._equity_snapshot, snapshot)
        self.assertEqual(runner.risk_guard.current_equity, previous)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def _midnight_close(self, *, confirmed):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        original_day = runner._baseline_day
        slug = runner.market_slug
        client.portfolio.rows = {slug: {
            "netPosition": "100", "netPositionDecimal": "100.0000",
            "cost": {"value": "90", "currency": "USD"},
            "cashValue": {"value": "9", "currency": "USD"},
            "updateTime": NOW.isoformat(),
        }}
        await runner._rest_reconcile(NOW)
        client.account.current_balance = Decimal(10)
        await runner._refresh_balance(NOW)
        self.assertIsNone(runner._stop_reason(NOW))
        self.assertIsNotNone(runner._provisional_hard_stop_at)
        tomorrow = NOW + timedelta(days=1)
        runner.clock = lambda: tomorrow
        client.portfolio.rows = {}
        await runner._rest_reconcile(tomorrow)
        moment = tomorrow + timedelta(seconds=1)
        runner.clock = lambda: moment
        client.account.current_balance = (Decimal(100) -
            runner.config.hard_stop_loss_usd + (Decimal(-1) if confirmed else Decimal(4)))
        await runner._refresh_balance(moment)
        self.assertEqual(runner._baseline_day, original_day)
        self.assertEqual(runner.risk_guard.equity_observed_at, moment)
        self.assertFalse(runner._frame(moment).new_risk_allowed)
        reason = runner._stop_reason(moment)
        if confirmed:
            self.assertIsNone(reason)
            self.assertEqual(runner._hard_stop_hold_day, original_day)
            self.assertIsNone(runner._provisional_hard_stop_at)
            self.assertEqual(runner._baseline_day, original_day)
        else:
            self.assertIsNone(reason)
            self.assertIsNone(runner._provisional_hard_stop_at)
            await runner._refresh_balance(moment)
            self.assertNotEqual(runner._baseline_day, original_day)
            self.assertEqual(runner.risk_guard.starting_equity,
                             client.account.current_balance)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_provisional_close_can_recover_after_et_midnight(self):
        await self._midnight_close(confirmed=False)

    async def test_provisional_loss_is_not_forgiven_at_et_midnight(self):
        await self._midnight_close(confirmed=True)

    async def _shutdown_fixture(self, owners):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_shutdown_seconds=100)
        runner._shutdown_slugs = lambda: owners
        runner._rest_reconcile = AsyncMock()
        runner._venue_proves_flat = lambda: False
        runner._whole_account_safe_to_stop = lambda: False
        runner._isolated_unexecutable_slugs = lambda: set()
        runner.ledger.position = lambda slug: Decimal(1)
        runner.ledger.working_orders = lambda slug: []
        runner.ledger.market_has_unresolved_intent = lambda slug: False
        runner.ledger.inventory_basis = lambda slug: None
        state = runner.state_for(runner.market_slug)
        runner.state_for = lambda slug: state
        runner._refresh_book = AsyncMock()
        runner.driver.apply_one = AsyncMock()
        return runner

    async def test_shutdown_budget_belongs_to_each_owner(self):
        runner = await self._shutdown_fixture(["a", "b", "c", "d"])
        await runner._shutdown("review")
        attempts = [call.args[0][0].market_slug
                    for call in runner.driver.apply_one.await_args_list]
        for owner in ("a", "b", "c", "d"):
            self.assertEqual(attempts.count(owner), 3)
        self.assertEqual(attempts[:4], ["a", "b", "c", "d"])
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_bad_book_cannot_starve_other_cancel_or_exit(self):
        runner = await self._shutdown_fixture(["a-bad", "b-good", "z-cancel"])
        runner.ledger.position = lambda slug: Decimal(0 if slug == "z-cancel" else 1)
        runner.ledger.working_orders = lambda slug: [object()] if slug == "z-cancel" else []
        runner.kill_switch.initiate = AsyncMock()

        async def read(now, slug):
            if slug == "a-bad":
                runner.kill_switch.initiate.assert_awaited()
                raise AdapterError("book unavailable")
        runner._refresh_book = AsyncMock(side_effect=read)
        await runner._shutdown("review")
        runner.kill_switch.initiate.assert_awaited()
        self.assertEqual(runner.driver.apply_one.await_count, 3)
        self.assertTrue(all(call.args[0][0].market_slug == "b-good"
                            for call in runner.driver.apply_one.await_args_list))
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_ownership_fault_still_blocks_account_mutations(self):
        runner = await self._shutdown_fixture(["a", "b"])
        runner._refresh_book = AsyncMock(side_effect=LedgerConflict("ownership"))
        await runner._shutdown("review")
        runner.driver.apply_one.assert_not_awaited()
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()
