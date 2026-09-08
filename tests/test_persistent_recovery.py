"""Offline recovery, durable HARD pause and exact evacuation regressions."""
import asyncio
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from tests import test_live_commissioning as live
from tests import test_reconciler as plans
from liquidity_bot.adapters import AdapterError
from liquidity_bot.commissioning import CommissioningUnavailable, CommissioningRefused
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.live_commissioning import RetiredMarket, MAX_RETIREMENT_ROUNDS
from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler, WorkingOrder


class PersistentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = live.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def runner(self, **kwargs):
        runner, client, notifier = self.fixture.build_runner(**kwargs)
        async def close():
            await runner.private_stream.close()
            await runner.market_stream.close()
        self.addAsyncCleanup(close)
        await runner.preflight()
        return runner, client, notifier

    async def trip(self, runner, client):
        client.account.current_balance -= runner.config.hard_stop_loss_usd
        await runner._refresh_balance(runner._now())
        self.assertIsNone(runner._stop_reason(runner._now()))
        self.assertIsNotNone(runner._hard_stop_hold_day)

    async def test_hard_hold_survives_recovered_equity_and_same_day_restart(self):
        runner, client, _ = await self.runner()
        await self.trip(runner, client)
        client.account.current_balance = Decimal("100")
        await runner._refresh_balance(live.NOW)
        self.assertIsNone(runner._stop_reason(live.NOW))
        self.assertFalse(runner._frame(live.NOW).new_risk_allowed)
        restarted, _, _ = await self.runner(ledger=runner.ledger, client=client)
        self.assertFalse(restarted._frame(live.NOW).new_risk_allowed)
        self.assertEqual(len(runner.ledger.query(
            "SELECT * FROM execution_hard_stop_holds")), 1)
        self.assertEqual(client.orders.create_calls, [])

    async def test_failed_startup_cash_proof_cannot_latch_raw_withdrawal(self):
        runner, client, _ = await self.runner()
        client.account.current_balance = Decimal("0")
        restarted, _, _ = self.fixture.build_runner(ledger=runner.ledger, client=client)
        self.addAsyncCleanup(restarted.private_stream.close)
        self.addAsyncCleanup(restarted.market_stream.close)
        with patch("liquidity_bot.live_commissioning.fetch_nontrading_cash",
                   AsyncMock(side_effect=AdapterError("unavailable", status_code=503))):
            with self.assertRaises(AdapterError):
                await restarted.preflight()
        self.assertIsNone(restarted.risk_guard.current_equity)
        self.assertIsNone(restarted._stop_reason(live.NOW))
        self.assertIsNone(restarted._hard_stop_hold_day)
        self.assertEqual(client.orders.create_calls, [])

    async def test_cascade_release_needs_post_cooldown_proof_and_keeps_retirements(self):
        runner, client, _ = await self.runner()
        runner._market_fault_pause_until = live.NOW + timedelta(minutes=30)
        runner._retired["bad"] = RetiredMarket("bad", "unrecoverable_book_stale", live.NOW)
        later = runner._market_fault_pause_until
        runner.clock = lambda: later
        self.assertIsNone(runner._stop_reason(later))
        self.assertIsNotNone(runner._market_fault_pause_until)
        await runner._rest_reconcile(later)
        await runner._refresh_balance(later)
        self.assertIsNone(runner._stop_reason(later))
        self.assertIsNone(runner._market_fault_pause_until)
        self.assertIn("bad", runner._retired)
        self.assertEqual(client.orders.create_calls, [])

    async def test_restart_with_hard_hold_and_owned_order_stays_in_recovery(self):
        runner, client, _ = self.fixture.build_runner()
        runner.ledger.record_hard_stop_hold("2026-08-13", at=live.NOW)
        runner.ledger.prepare_intent("restart", "PLACE", "market-one", "client-restart",
            {"marketSlug": "market-one", "intent": "ORDER_INTENT_BUY_LONG",
             "price": {"value": "0.49", "currency": "USD"}, "quantity": "10"},
            created_at=live.NOW)
        runner.ledger.transition_intent("restart", "SUBMITTING", updated_at=live.NOW)
        runner.ledger.transition_intent("restart", "ACKNOWLEDGED", updated_at=live.NOW,
                                        exchange_order_id="exchange-restart")
        client.orders.rows["exchange-restart"] = {
            "id": "exchange-restart", "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY", "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "10", "cumQuantity": "0", "state": "ORDER_STATE_NEW",
            "createTime": live.NOW.isoformat(),
        }
        restarted, _, _ = self.fixture.build_runner(ledger=runner.ledger, client=client)
        self.addAsyncCleanup(restarted.private_stream.close)
        self.addAsyncCleanup(restarted.market_stream.close)
        self.assertEqual(await restarted.preflight(), "HARD_RECOVERY")
        self.assertFalse(restarted._frame(live.NOW).new_risk_allowed)
        self.assertIsNone(restarted._stop_reason(live.NOW))
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any("Persisted HARD recovery" in message
                            for message, _ in restarted.notifier.messages))
        refused, _, _ = self.fixture.build_runner(
            ledger=runner.ledger, client=client,
            notifier=live.FakeNotifier(delivery_succeeds=False))
        self.addAsyncCleanup(refused.private_stream.close)
        self.addAsyncCleanup(refused.market_stream.close)
        with self.assertRaisesRegex(CommissioningRefused, "Discord startup"):
            await refused.preflight()
        self.assertEqual(client.orders.create_calls, [])

    async def test_next_day_requires_fresh_flat_proof_then_preserves_manual_pause(self):
        runner, client, _ = await self.runner()
        await self.trip(runner, client)
        tomorrow = live.NOW + timedelta(days=1)
        runner.clock = lambda: tomorrow
        self.assertIsNone(runner._stop_reason(tomorrow))
        self.assertIsNotNone(runner._hard_stop_hold_day)
        await runner._refresh_balance(tomorrow)
        self.assertIsNone(runner._stop_reason(tomorrow))
        self.assertIsNotNone(runner._hard_stop_hold_day)  # stale account gate
        await runner._rest_reconcile(tomorrow)
        await runner._refresh_balance(tomorrow)
        runner._manual_entry_paused = True
        self.assertIsNone(runner._stop_reason(tomorrow))
        self.assertIsNone(runner._hard_stop_hold_day)
        self.assertTrue(runner._entry_pause_active())
        runner._manual_entry_paused = False
        await runner.gateway.refresh_event_states(runner.active_slugs, force=True)
        self.assertTrue(runner._frame(tomorrow).new_risk_allowed)
        self.assertIsNotNone(runner.ledger.query(
            "SELECT released_at FROM execution_hard_stop_holds")[0][0])

    async def test_next_day_does_not_release_with_inventory_or_unknown_order(self):
        runner, client, _ = await self.runner()
        await self.trip(runner, client)
        tomorrow = live.NOW + timedelta(days=1)
        runner.clock = lambda: tomorrow
        await runner._refresh_balance(tomorrow)
        await runner._rest_reconcile(tomorrow)
        await runner._refresh_balance(tomorrow)
        slug = runner.market_slug
        runner.ledger.apply_position_event("test", "remaining", PositionState(
            slug, Decimal("0.05"), tomorrow), received_at=tomorrow)
        self.assertIsNone(runner._stop_reason(tomorrow))
        self.assertIsNotNone(runner._hard_stop_hold_day)
        runner.ledger.apply_position_event("test", "closed", PositionState(
            slug, Decimal("0"), tomorrow), received_at=tomorrow)
        # A newer bracketed equity read may see a late fill that the older
        # reconciliation has not projected yet. The newer truth also must be flat.
        flat_mark = runner._equity_snapshot
        runner._equity_snapshot = replace(flat_mark, positions=(
            PositionState(slug, Decimal("0.05"), tomorrow),))
        self.assertIsNone(runner._stop_reason(tomorrow))
        self.assertIsNotNone(runner._hard_stop_hold_day)
        runner._equity_snapshot = flat_mark
        runner.ledger.prepare_intent("unknown", "PLACE", slug, "unknown-client",
                                     {}, created_at=tomorrow)
        self.assertIsNone(runner._stop_reason(tomorrow))
        self.assertIsNotNone(runner._hard_stop_hold_day)

    async def test_hard_pause_cancels_flat_quotes_even_on_stale_book(self):
        runner, client, _ = await self.runner()
        await self.trip(runner, client)
        self.fixture.seed_working_order(runner, slug=runner.market_slug,
                                        side="bid", suffix="hard-bid")
        self.fixture.seed_working_order(runner, slug=runner.market_slug,
                                        side="offer", suffix="hard-offer")
        runner.book_observed_at = live.NOW - timedelta(minutes=1)
        frame = runner._frame(live.NOW)
        actions = runner.reconciler.plan(frame)
        self.assertEqual({a.kind for a in actions}, {ActionKind.CANCEL})
        self.assertEqual({a.order_id for a in actions},
                         {"exchange-hard-bid", "exchange-hard-offer"})
        self.assertTrue(frame.hard_stop_flatten_required)

    async def test_retired_held_owner_still_plans_after_ten_rounds(self):
        runner, client, _ = await self.runner()
        slug = runner.market_slug
        runner.ledger.apply_position_event("test", "held", PositionState(
            slug, Decimal("10"), live.NOW), received_at=live.NOW)
        runner._retired[slug] = RetiredMarket(
            slug, "hard_exit_stuck", live.NOW, rounds=MAX_RETIREMENT_ROUNDS)
        with patch.object(runner.reconciler, "plan", return_value=[]) as planner:
            self.assertEqual(await runner._decide_for_market(slug, live.NOW),
                             (None, 0.0))
        planner.assert_called_once()
        self.assertEqual(client.orders.create_calls, [])

    async def test_temporary_startup_waits_past_sixty_attempts(self):
        runner, client, _ = self.fixture.build_runner()
        runner.preflight = AsyncMock(side_effect=[
            *[CommissioningUnavailable("temporary no viable market") for _ in range(75)],
            "LIVE"])
        self.assertEqual(await runner.preflight_until_ready(), "LIVE")
        self.assertEqual(runner.preflight.await_count, 76)
        self.assertEqual(client.orders.create_calls, [])
        runner.preflight = AsyncMock(side_effect=CommissioningRefused("ownership"))
        with self.assertRaises(CommissioningRefused):
            await runner.preflight_until_ready()
        runner.preflight.assert_awaited_once()

    async def test_mixed_503_429_reconnect_recovers_past_short_budget(self):
        for statuses in ([429] * 12, [503, 429] * 6):
            with self.subTest(statuses=statuses):
                runner, client, _ = await self.runner()
                errors = [AdapterError("temporary", status_code=s) for s in statuses]
                with patch.object(runner, "_open_streams_for_active_market",
                                  AsyncMock(side_effect=[*errors, None])) as bind, \
                     patch.object(runner, "_wait_for_venue_recovery",
                                  AsyncMock(return_value=None)) as health:
                    self.assertIsNone(await runner._recover_bound_streams(
                        AdapterError("WebSocket disconnected")))
                self.assertEqual(bind.await_count, 13)
                health.assert_awaited_once()
                self.assertEqual(client.orders.create_calls, [])

    async def test_long_outage_keeps_hard_hold_and_honours_manual_stop(self):
        runner, client, _ = await self.runner()
        await self.trip(runner, client)
        seconds = 0
        async def advance(delay):
            nonlocal seconds
            seconds += delay
            runner.clock = lambda: live.NOW + timedelta(seconds=seconds)
            if seconds >= 3 * 3600:
                runner.request_stop("operator")
            await asyncio.sleep(0)
        runner.sleep = advance
        with patch.object(runner.account, "fetch_open_orders", AsyncMock(
                side_effect=AdapterError("unavailable", status_code=503))):
            self.assertEqual(await runner._wait_for_venue_recovery(
                "test", AdapterError("unavailable", status_code=503)), "operator")
        self.assertGreaterEqual(seconds, 3 * 3600)
        self.assertIsNotNone(runner._hard_stop_hold_day)
        self.assertEqual(client.orders.create_calls, [])

    async def test_recoverable_cascade_pauses_but_unknown_fault_still_stops(self):
        runner, client, _ = await self.runner(max_markets=32)
        for n in range(33):
            self.assertIsNone(await runner._retire_market(
                f"bad-{n}", "unrecoverable_book_stale"))
        self.assertEqual(runner._current_entry_pause_reason(),
                         "market_fault_cascade_recovery")
        stop = await runner._retire_market("bad-unknown", "code_defect")
        self.assertEqual(stop, "critical:market_retirement_limit_exceeded")
        self.assertEqual(client.orders.create_calls, [])


class HardEvacuationTests(unittest.TestCase):
    def test_long_and_short_hard_exit_are_immediate_and_exact(self):
        for position in ("2", "-2", "0.05", "-0.05"):
            with self.subTest(position=position):
                reconciler = DesiredStateReconciler(plans.policy(age=900))
                frame = replace(plans.frame(position=position, opened=plans.T0,
                                            entry=.49, new_risk_allowed=False),
                                hard_stop_flatten_required=True)
                action, = reconciler.plan(frame)
                self.assertEqual(action.kind, ActionKind.PLACE_IOC)
                self.assertEqual(action.reason, "daily_hard_stop_evacuation")
                self.assertEqual(action.qty, abs(Decimal(position)))
                self.assertEqual(action.side, "offer" if Decimal(position) > 0 else "bid")
                reconciler.restart_hard_exit_budget("market-one")
                blocked, = reconciler.plan(frame)
                self.assertEqual(blocked.kind, ActionKind.CRITICAL)
                self.assertEqual(blocked.reason, "hard_exit_sent_but_inventory_remains")
                reconciler.confirm_hard_exit_terminal("market-one")
                partial = replace(frame, position=Decimal(position) / 2)
                retry, = reconciler.plan(partial)
                self.assertEqual(retry.qty, abs(Decimal(position)) / 2)

    def test_hard_exit_cancels_builder_before_reducer_and_waits_for_proof(self):
        reconciler = DesiredStateReconciler(plans.policy(age=900))
        frame = replace(plans.frame(position="2", opened=plans.T0, entry=.49,
            orders=(WorkingOrder("reducer", "offer", .51, Decimal("2")),
                    WorkingOrder("builder", "bid", .49, Decimal("2")))),
            new_risk_allowed=False, hard_stop_flatten_required=True)
        cancel, = reconciler.plan(frame)
        self.assertEqual(cancel.order_id, "builder")
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "builder", submitted_at=plans.T0)
        self.assertEqual(reconciler.plan(frame), [])
        cancel, = reconciler.plan(replace(frame, working_orders=frame.working_orders[:1]))
        self.assertEqual(cancel.order_id, "reducer")
        unknown = replace(frame, working_orders=(), state_known=False)
        action, = reconciler.plan(unknown)
        self.assertEqual(action.reason, "execution_state_unknown")
