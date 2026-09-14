"""Maintenance cannot turn empty owners into book-fault retirements."""
from datetime import timedelta
from functools import partial
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.errors import AdapterError, EmptyBookError
from liquidity_bot.maintenance_schedule import MaintenanceDecision, MaintenancePhase
from liquidity_bot.execution_ledger import Execution
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class MaintenanceReadRecoveryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def build(self, *, rest=False):
        kind = partial(AccountSourceRunner, account_state_mode="rest",
                       rest_polling_policy=RestPollingPolicy(enabled=True)) if rest else fixtures.CommissioningRunner
        with patch.object(fixtures, "CommissioningRunner", kind):
            runner, client, _ = self.fixture.build_runner(max_markets=32)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        return runner, client

    async def test_32_empty_owners_do_not_refresh_or_retire_during_hold(self):
        runner, _ = await self.build()
        slugs = ("market-one", *(f"market-{i}" for i in range(31)))
        runner._bind_markets(slugs)
        due = fixtures.NOW - timedelta(seconds=60)
        for phase in (MaintenancePhase.EVACUATE, MaintenancePhase.MAINTENANCE,
                      MaintenancePhase.RESUME_HOLD):
            runner._maintenance_decision = MaintenanceDecision(phase)
            with patch.object(runner, "_refresh_book", AsyncMock(side_effect=EmptyBookError("empty"))) as book, \
                    patch.object(runner.reconciler, "plan", side_effect=AssertionError("idle price plan")):
                for slug in slugs:
                    self.assertEqual(await runner._refresh_quiet_book_if_due(due, slug), due)
                    self.assertEqual(await runner._decide_for_market(slug, fixtures.NOW), (None, 0))
                book.assert_not_awaited()
        self.assertFalse(runner._retired)
        self.assertFalse(runner._runtime_error_counts)

    async def test_resume_fetches_fresh_book_without_inherited_backoff(self):
        runner, client = await self.build(rest=True)
        slug = "market-one"
        runner.state_for(slug).book_observed_at = fixtures.NOW - timedelta(seconds=60)
        due = fixtures.NOW - timedelta(seconds=60)
        runner._maintenance_decision = MaintenanceDecision(MaintenancePhase.RESUME_HOLD)
        self.assertEqual(await runner._refresh_quiet_book_if_due(due, slug), due)
        runner._maintenance_decision = MaintenanceDecision(MaintenancePhase.NORMAL)
        runner._maintenance_health_required_for = "pending-health"
        self.assertTrue(runner._maintenance_owner_is_idle(slug, fixtures.NOW))
        runner._maintenance_health_required_for = None
        with patch.object(runner, "_refresh_book", wraps=runner._refresh_book) as refresh:
            await runner._refresh_quiet_book_if_due(due, slug)
            refresh.assert_awaited_once()
        self.assertTrue(runner._book_is_fresh_enough_to_plan(slug))
        self.assertFalse(runner._retired)

    async def test_unknown_state_and_stop_never_use_idle_exception(self):
        runner, _ = await self.build()
        runner._maintenance_decision = MaintenanceDecision(MaintenancePhase.MAINTENANCE)
        self.assertTrue(runner._maintenance_owner_is_idle("market-one", fixtures.NOW))
        for method in ("working_orders", "absent_orders", "market_has_unresolved_intent"):
            with patch.object(runner.ledger, method, return_value=["unknown"]):
                self.assertFalse(runner._maintenance_owner_is_idle("market-one", fixtures.NOW))
        runner._stop_requested = "operator_sigterm"
        self.assertFalse(runner._maintenance_owner_is_idle("market-one", fixtures.NOW))
        runner._stop_requested = None
        runner.gate.on_disconnect()
        self.assertFalse(runner._maintenance_owner_is_idle("market-one", fixtures.NOW))

    async def test_idle_owner_keeps_due_book_without_request_or_delay(self):
        runner, _ = await self.build(rest=True)
        runner._maintenance_decision = MaintenanceDecision(MaintenancePhase.MAINTENANCE)
        runner.state_for("market-one").book_observed_at = fixtures.NOW - timedelta(seconds=60)
        due = fixtures.NOW - timedelta(seconds=60)
        with patch.object(runner, "_refresh_book", AsyncMock()) as book:
            returned = await runner._refresh_quiet_book_if_due(due, "market-one")
            book.assert_not_awaited()
            self.assertEqual(returned, due)

    async def test_late_position_reactivates_book_service_and_existing_exit(self):
        runner, _ = await self.build()
        runner._maintenance_decision = MaintenanceDecision(MaintenancePhase.MAINTENANCE)
        runner.ledger.apply_execution(Execution(
            execution_id="late", client_order_id="liquidity-late", exchange_order_id="late",
            market_slug="market-one", side="BUY", price=Decimal(".5"), qty=Decimal("10"),
            fee=Decimal("0"), liquidity="MAKER", intent="ORDER_INTENT_BUY_LONG",
            event_at=fixtures.NOW), received_at=fixtures.NOW)
        self.assertFalse(runner._maintenance_owner_is_idle("market-one", fixtures.NOW))
        runner.state_for("market-one").book_observed_at = fixtures.NOW - timedelta(seconds=60)
        with patch.object(runner, "_refresh_book", wraps=runner._refresh_book) as book:
            await runner._refresh_quiet_book_if_due(fixtures.NOW, "market-one")
            book.assert_awaited_once()
        frame = runner._frame(fixtures.NOW)
        self.assertTrue(frame.maintenance_flatten_required)
        self.assertEqual(runner.reconciler.plan(frame)[0].reason, "scheduled_maintenance_evacuation")

    async def test_account_503_yields_once_without_32_owner_recovery_fanout(self):
        runner, _ = await self.build()
        slugs = ("market-one", *(f"market-{i}" for i in range(31)))
        runner._bind_markets(slugs)
        with patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock(
                side_effect=AdapterError("service unavailable", status_code=503))) as proof, \
                patch.object(runner, "_decide_for_market", AsyncMock(return_value=(None, 0))) as decide, \
                patch.object(runner, "_retire_market", AsyncMock()) as retire:
            self.assertIsNone(await runner._decision_sweep(slugs))
        proof.assert_awaited_once()
        decide.assert_not_awaited()
        retire.assert_not_awaited()
