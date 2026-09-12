"""Offline acceptance regressions for the September 8 safety review fixes."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
import test_live_session as session_fixtures
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.errors import RuntimeInvariantViolation
from liquidity_bot.live_session import ControlledActionDriver
from liquidity_bot.reconciler import ActionKind, PlanAction
from liquidity_bot.notifications import RuntimeNotificationDispatcher, NotificationResult


class ReviewSafetyRegressions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def test_hard_cancel_sweep_does_not_wait_for_discord(self):
        runner, _, notifier = self.fixture.build_runner()
        await runner.preflight()
        entered, release = asyncio.Event(), asyncio.Event()
        original_send = notifier.send_async

        async def delayed_send(message, **kwargs):
            if "HARD loss confirmed" in message:
                entered.set()
                await release.wait()
            return await original_send(message, **kwargs)

        notifier.send_async = delayed_send
        runner.preflight_until_ready = AsyncMock(return_value="TRADE")
        runner._refresh_maintenance_schedule = AsyncMock()
        runner._refresh_entry_pause = AsyncMock()
        runner._evaluate_rewards = lambda now: None
        runner._stop_reason = lambda now: None
        runner._announce_execution_fee_observation = AsyncMock()
        runner._announce_loss_entry_order_cap = AsyncMock()
        runner._announce_overnight_entry_order_cap = AsyncMock()
        runner._hard_stop_hold_notice = "held"
        runner._take_entry_pause_cancel_slugs = lambda: ("market-one",)
        runner._decision_sweep = AsyncMock(return_value="review_complete")
        runner._shutdown = AsyncMock()
        task = asyncio.create_task(runner.run())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.sleep(0.03)
            self.assertEqual(runner._decision_sweep.await_count, 1)
            self.assertFalse(task.done())
            release.set()
            result = await asyncio.wait_for(task, timeout=2)
            self.assertEqual(result.stop_reason, "review_complete")
            runner._decision_sweep.assert_awaited_once_with(
                ("market-one",), service_books=False)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_unknown_basis_reserves_full_loss_for_both_position_signs(self):
        runner, _, _ = self.fixture.build_runner()
        now = fixtures.NOW
        for index, quantity in enumerate(("-10", "10")):
            at = now + timedelta(seconds=index)
            runner.ledger.apply_position_event(
                "review", str(index), PositionState("other", Decimal(quantity), at),
                received_at=at)
            self.assertIsNone(runner.ledger.inventory_basis("other"))
            risk = runner._portfolio_worst_loss(excluding="market-one")
            self.assertEqual(risk, 10.0)

    async def test_unresolved_place_reserves_order_count_and_risk(self):
        runner, _, _ = self.fixture.build_runner()
        runner.ledger.prepare_intent(
            "review-intent", "PLACE", "other", "review-client",
            {"marketSlug": "other", "intent": "ORDER_INTENT_BUY_LONG",
             "price": {"value": "0.80", "currency": "USD"}, "quantity": "10"},
            created_at=fixtures.NOW)
        runner.ledger.transition_intent("review-intent", "SUBMITTING", updated_at=fixtures.NOW)
        runner.ledger.transition_intent("review-intent", "UNCERTAIN", updated_at=fixtures.NOW)
        self.assertEqual(runner._flat_entry_orders_and_reservations()[1], 1)
        self.assertEqual(runner._portfolio_worst_loss(excluding="market-one"), 8.0)
        self.assertTrue(runner.ledger.risk_state_known("market-one"))

    async def test_absent_order_retains_risk_and_count_until_terminal_proof(self):
        runner, _, _ = self.fixture.build_runner()
        self.fixture.seed_working_order(runner, slug="other", side="bid", suffix="review")
        self.assertAlmostEqual(runner._portfolio_worst_loss(excluding="market-one"), 4.9)
        at = fixtures.NOW + timedelta(seconds=1)
        runner.ledger.apply_order_snapshot("review-absence", [], as_of=at, observed_at=at, complete=True)
        rows = runner.ledger.query("SELECT status FROM execution_orders WHERE market_slug='other'")
        self.assertEqual(rows[0]["status"], "UNKNOWN_ABSENT")
        self.assertAlmostEqual(runner._portfolio_worst_loss(excluding="market-one"), 4.9)
        self.assertTrue(runner.ledger.risk_state_known("market-one"))
        self.assertEqual(runner._flat_entry_orders_and_reservations(), ((), 1))
        original = runner.ledger.query(
            "SELECT * FROM execution_orders WHERE client_order_id='client-review'")[0]
        terminal = fixtures.OrderState(
            client_order_id=original["client_order_id"],
            exchange_order_id=original["exchange_order_id"], market_slug="other",
            side="BUY", price=Decimal("0.49"), original_qty=Decimal(10),
            filled_qty=Decimal(0), status="CANCELED", event_at=at)
        runner.ledger.apply_order_event("test", "terminal", terminal, received_at=at)
        self.assertEqual(runner._portfolio_worst_loss(excluding="market-one"), 0)
        self.assertEqual(runner._flat_entry_orders_and_reservations(), ((), 0))

    async def test_absent_and_unresolved_same_order_are_not_double_counted(self):
        runner, _, _ = self.fixture.build_runner()
        self.fixture.seed_working_order(runner, slug="other", side="offer", suffix="dup")
        runner.ledger.prepare_intent(
            "pending-cancel", "CANCEL", "other", "client-dup", {},
            created_at=fixtures.NOW)
        runner.ledger.transition_intent("pending-cancel", "SUBMITTING", updated_at=fixtures.NOW)
        # A cancellation in flight cannot free or duplicate the reservation.
        self.assertEqual(len(runner.ledger.unresolved_intents("other")), 1)
        at = fixtures.NOW + timedelta(seconds=1)
        runner.ledger.apply_order_snapshot("absent", [], as_of=at, observed_at=at, complete=True)
        self.assertAlmostEqual(runner._portfolio_worst_loss(excluding="market-one"), 5.1)
        self.assertEqual(runner._flat_entry_orders_and_reservations(), ((), 1))
        runner.ledger.apply_position_event("test", "held", PositionState(
            "other", Decimal(-2), at), received_at=at)
        # Position-market orders still do not consume entry slots.
        self.assertEqual(runner._flat_entry_orders_and_reservations(), ((), 0))
        self.assertAlmostEqual(runner._portfolio_worst_loss(excluding="market-one"), 7.1)

    async def test_restored_cohort_uses_original_baseline_across_midnight(self):
        runner, _, _ = self.fixture.build_runner()
        await runner.preflight()
        day = runner._baseline_day
        runner.ledger.record_provisional_hard_stop(
            day.isoformat(), at=fixtures.NOW, markets={"market-one"})
        restarted, _, _ = self.fixture.build_runner(
            ledger=runner.ledger, clock=lambda: fixtures.NOW + timedelta(days=1))
        restarted.balance = runner.balance
        restarted._equity_snapshot = runner._equity_snapshot
        restarted._roll_realized_baseline()
        self.assertEqual(restarted._baseline_day, day)
        self.assertEqual(restarted._cash_activity_boundary, runner._cash_activity_boundary)
        self.assertEqual(restarted._day_opening_equity, runner._day_opening_equity)
        self.assertEqual(len(runner.ledger.query(
            "SELECT * FROM execution_risk_day_baselines")), 1)
        # Cohort growth preserves original identity; confirmation clears it
        # in the same transaction as the durable confirmed hold.
        runner.ledger.record_provisional_hard_stop(
            day.isoformat(), at=fixtures.NOW, markets={"market-two"})
        restored, _, _ = self.fixture.build_runner(ledger=runner.ledger)
        self.assertEqual(restored._provisional_hard_stop_markets,
                         {"market-one", "market-two"})
        runner.ledger.record_hard_stop_hold(day.isoformat(), at=fixtures.NOW)
        confirmed, _, _ = self.fixture.build_runner(ledger=runner.ledger)
        self.assertIsNone(confirmed._provisional_hard_stop_at)
        self.assertEqual(confirmed._hard_stop_hold_day, day)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_missing_breached_baseline_fails_closed(self):
        runner, _, _ = self.fixture.build_runner()
        runner.ledger.record_provisional_hard_stop(
            "2026-08-13", at=fixtures.NOW, markets={"market-one"})
        restarted, _, _ = self.fixture.build_runner(ledger=runner.ledger)
        with self.assertRaisesRegex(RuntimeInvariantViolation, "baseline is unavailable"):
            restarted._roll_realized_baseline()

    async def test_restart_preserves_unclosed_provisional_cohort_when_mark_recovers(self):
        runner, client, _ = self.fixture.build_runner(max_markets=32)
        runner.config = replace(runner.config, quote_size=Decimal(10))
        await runner.preflight()
        self.assertEqual(runner.config.hard_stop_loss_usd, Decimal(90))
        client.portfolio.rows = {"market-one": {
            "netPosition": "10", "netPositionDecimal": "10.0000",
            "cost": {"value": "9", "currency": "USD"},
            "cashValue": {"value": "4", "currency": "USD"},
            "updateTime": fixtures.NOW.isoformat(),
        }}
        client.account.current_balance = Decimal(2)
        await runner._rest_reconcile(fixtures.NOW)
        await runner._refresh_balance(fixtures.NOW)
        runner._stop_reason(fixtures.NOW)
        self.assertIsNotNone(runner._provisional_hard_stop_at)
        client.portfolio.rows["market-one"]["cashValue"]["value"] = "9"
        await runner._refresh_balance(fixtures.NOW)
        runner._stop_reason(fixtures.NOW)
        self.assertIsNotNone(runner._provisional_hard_stop_at)
        self.assertFalse(runner._frame(fixtures.NOW).new_risk_allowed)
        await runner.private_stream.close()
        await runner.market_stream.close()
        client.markets.started.add("market-one")
        restarted, _, _ = self.fixture.build_runner(
            ledger=runner.ledger, client=client, auto=True, max_markets=32,
            clock=lambda: fixtures.NOW + timedelta(seconds=1),
            reward_selector=fixtures.FakeRewardSelector(
                fixtures.multi_selection("market-two", "market-three")))
        restarted.config = replace(restarted.config, quote_size=Decimal(10))
        try:
            mode = await restarted.preflight()
            self.assertEqual(mode, "TRADE")
            self.assertEqual(restarted.ledger.position("market-one"), Decimal(10))
            self.assertIsNotNone(restarted._provisional_hard_stop_at)
            restarted._stop_reason(restarted._now())
            self.assertFalse(restarted._frame(restarted._now(), "market-two").new_risk_allowed)
            # A positive later close plus a fresh adjusted mark can clear it.
            moment = fixtures.NOW + timedelta(seconds=2)
            restarted.clock = lambda: moment
            client.portfolio.rows = {}
            client.account.current_balance = Decimal(11)
            await restarted._rest_reconcile(moment)
            await restarted._refresh_balance(moment)
            restarted._stop_reason(moment)
            self.assertIsNone(restarted._provisional_hard_stop_at)
            self.assertEqual(restarted.ledger.query(
                "SELECT * FROM execution_provisional_hard_stop"), [])
        finally:
            await restarted.private_stream.close()
            await restarted.market_stream.close()

    async def test_ioc_mutation_boundary_rejects_wrong_reducing_direction(self):
        runner, _, _ = self.fixture.build_runner()
        executor = session_fixtures.Executor()
        driver = ControlledActionDriver(executor, runner.ledger)
        action = PlanAction(ActionKind.PLACE_IOC, "market-one", "review",
                            side="bid", price=0.51, qty=Decimal(10))
        with self.assertRaisesRegex(RuntimeInvariantViolation, "IOC must oppose"):
            await driver.apply_one((action,), market=session_fixtures.market(),
                                   position=Decimal(10), now=fixtures.NOW)
        self.assertEqual(executor.places, [])
        for position, side in ((Decimal(10), "offer"), (Decimal(-10), "bid")):
            await driver.apply_one((replace(action, side=side),),
                                   market=session_fixtures.market(),
                                   position=position, now=fixtures.NOW)
        self.assertEqual(len(executor.places), 2)
        with self.assertRaisesRegex(RuntimeInvariantViolation, "IOC must oppose"):
            await driver.apply_one((replace(action, side="offer"),),
                                   market=session_fixtures.market(),
                                   position=Decimal(-10), now=fixtures.NOW)

    async def test_notification_queue_is_bounded_prioritized_and_snapshots_reports(self):
        notifier = fixtures.FakeNotifier()
        queue = RuntimeNotificationDispatcher(notifier, capacity=2)
        result = await queue.send_async("ordinary")
        self.assertFalse(result.sent)
        self.assertEqual(result.reason, "queued")
        payload = {"content": "original report"}
        await queue.send_payload_async(payload, channel="summary")
        payload["content"] = "later mutation"
        await queue.send_async("urgent", critical=True)
        refused = await queue.send_async("overflow")
        self.assertEqual(refused.reason, "notification_queue_full")
        await queue.close()
        self.assertEqual(notifier.messages[0][0], "urgent")
        self.assertEqual(notifier.payloads[0][0]["content"], "original report")
        self.assertEqual((await queue.send_async("closed")).reason, "dispatcher_closed")

    async def test_notification_failure_retries_without_crashing_worker(self):
        notifier = fixtures.FakeNotifier()
        notifier.send_async = AsyncMock(side_effect=[
            RuntimeError("private transport details"),
            NotificationResult(False, "delivery_failed"),
            NotificationResult(True, "sent"), NotificationResult(True, "sent")])
        queue = RuntimeNotificationDispatcher(notifier)
        with patch("liquidity_bot.notifications.asyncio.sleep", new=AsyncMock()):
            await queue.send_async("urgent", critical=True)
            await queue.send_async("next")
            await queue.close()
        self.assertEqual(notifier.send_async.await_count, 4)

    async def test_notification_close_does_not_wait_indefinitely(self):
        notifier = fixtures.FakeNotifier()
        entered = asyncio.Event()

        async def stalled(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        notifier.send_async = stalled
        queue = RuntimeNotificationDispatcher(notifier)
        await queue.send_async("urgent", critical=True)
        await entered.wait()
        await asyncio.wait_for(queue.close(timeout=0.01), timeout=0.5)
        self.assertTrue(queue._task.done())


if __name__ == "__main__":
    unittest.main(verbosity=2)
