from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from liquidity_bot.entry_expiry import EntryExpiryPolicy, load_entry_expiry_policy
from liquidity_bot.errors import ApiAdmissionDeferred
from liquidity_bot.execution_ledger import Execution, ExecutionLedger, OrderState, PositionState
from liquidity_bot.host_recovery import (
    arm, control_lock, finish, read_state, tick, write_state)
from liquidity_bot.live_orders import (
    AsyncTradingGateway, OrderRequest, OrderValidationError, build_order_payload)
from liquidity_bot.live_session import ControlledActionDriver
from liquidity_bot.reconciler import ActionKind, PlanAction
from test_live_session import Executor, market, NOW
from test_live_commissioning import NOW as RUNNER_NOW


class EntryExpiryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = ExecutionLedger(Path(self.tmp.name) / "ledger.db")
        self.addCleanup(self.ledger.close)
        self.executor = Executor()
        self.driver = ControlledActionDriver(
            self.executor, self.ledger, entry_expiry_policy=EntryExpiryPolicy())

    async def test_real_driver_entries_expire_but_reducers_keep_gtc(self):
        action = PlanAction(ActionKind.PLACE_POST_ONLY, "market-one", "test",
                            side="bid", price=.5, qty=Decimal("1"))
        await self.driver.apply_one([action], market=market(),
                                    position=Decimal(0), now=NOW)
        request = self.executor.places[-1][0]
        payload = build_order_payload(request, market())
        self.assertEqual(payload["tif"], "TIME_IN_FORCE_GOOD_TILL_DATE")
        self.assertEqual(request.good_till_time, NOW + timedelta(seconds=300))
        self.assertIn("goodTillTime", payload)
        await self.driver.apply_one([action], market=market(),
                                    position=Decimal(-1), now=NOW)
        reducer = build_order_payload(self.executor.places[-1][0], market())
        self.assertEqual(reducer["tif"], "TIME_IN_FORCE_GOOD_TILL_CANCEL")
        self.assertNotIn("goodTillTime", reducer)

    async def test_deadline_shortens_to_pregame_and_blocks_at_boundary(self):
        action = PlanAction(ActionKind.PLACE_POST_ONLY, "market-one", "test",
                            side="bid", price=.5, qty=Decimal("1"))
        m = replace(market(), game_start=NOW + timedelta(seconds=340))
        await self.driver.apply_one([action], market=m, position=Decimal(0), now=NOW)
        self.assertEqual(self.executor.places[-1][0].good_till_time,
                         NOW + timedelta(seconds=40))
        result = await self.driver.apply_one(
            [action], market=m, position=Decimal(0), now=NOW + timedelta(seconds=40))
        self.assertEqual(result.action.kind, ActionKind.HALT_NEW_RISK)
        self.assertEqual(len(self.executor.places), 1)

    async def test_expiry_after_admission_wait_is_never_sent(self):
        now = datetime.now(timezone.utc)
        payload = build_order_payload(OrderRequest(
            "market-one", "bid", Decimal(".5"), Decimal(1),
            time_in_force="TIME_IN_FORCE_GOOD_TILL_DATE",
            good_till_time=now - timedelta(seconds=1)), market())
        gateway = AsyncTradingGateway(Mock())
        sent = False
        async def mutation(operation, call, **kwargs):
            nonlocal sent
            kwargs["admission_check"]()
            sent = True
        gateway._mutation = mutation
        with self.assertRaises(ApiAdmissionDeferred):
            await gateway.create(payload)
        self.assertFalse(sent)

    def test_invalid_and_reducing_expiry_refused(self):
        request = OrderRequest("market-one", "bid", Decimal(".5"), Decimal(1),
            time_in_force="TIME_IN_FORCE_GOOD_TILL_DATE", good_till_time=NOW)
        for change in ({"reducing": True}, {"good_till_time": None},
                       {"good_till_time": NOW.replace(tzinfo=None)},
                       {"time_in_force": "TIME_IN_FORCE_GOOD_TILL_CANCEL"}):
            with self.subTest(change=change), self.assertRaises(OrderValidationError):
                build_order_payload(replace(request, **change), market())
        for value in (0, -1, 59, 601, True, 120.5):
            with self.assertRaises(ValueError):
                EntryExpiryPolicy(lifetime_seconds=value)

    def test_production_policy_is_ten_minutes_within_the_ceiling(self):
        policy = load_entry_expiry_policy()
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.lifetime_seconds, 600)
        self.assertEqual(EntryExpiryPolicy(lifetime_seconds=600).deadline(
            NOW, NOW + timedelta(hours=2), 300), NOW + timedelta(seconds=600))
        # Still shortened to the pregame flatten boundary.
        self.assertEqual(EntryExpiryPolicy(lifetime_seconds=600).deadline(
            NOW, NOW + timedelta(seconds=420), 300), NOW + timedelta(seconds=120))

    def test_policy_can_disable_without_changing_reducers(self):
        self.assertIsNone(EntryExpiryPolicy(False).deadline(NOW, None, 300))
        path = Path(self.tmp.name) / "policy.json"
        path.write_text(json.dumps({"enabled": False, "lifetime_seconds": 120}))
        self.assertFalse(load_entry_expiry_policy(path).enabled)
        path.write_text('{"enabled":true,"lifetime_seconds":120,"extra":1}')
        with self.assertRaises(ValueError):
            load_entry_expiry_policy(path)

    def test_expired_order_needs_positive_proof_and_keeps_partial_inventory(self):
        expiry = NOW + timedelta(seconds=300)
        payload = build_order_payload(OrderRequest(
            "market-one", "bid", Decimal(".5"), Decimal(1),
            time_in_force="TIME_IN_FORCE_GOOD_TILL_DATE",
            good_till_time=expiry), market())
        self.ledger.prepare_intent("place", "PLACE", "market-one", "client",
                                   payload, created_at=NOW)
        self.ledger.transition_intent("place", "SUBMITTING", updated_at=NOW)
        self.ledger.transition_intent("place", "ACKNOWLEDGED", updated_at=NOW,
                                       exchange_order_id="order")
        order = OrderState(client_order_id="client", exchange_order_id="order",
            market_slug="market-one", side="ORDER_SIDE_BUY", price=Decimal(".5"),
            original_qty=Decimal(1), filled_qty=Decimal(0), status="OPEN", event_at=NOW)
        self.ledger.apply_order_event("test", "open", order, received_at=NOW)
        self.ledger.apply_execution(Execution(
            "partial", "market-one", "BUY", Decimal(".5"), Decimal(".25"),
            Decimal(0), expiry, client_order_id="client", exchange_order_id="order",
            intent="ORDER_INTENT_BUY_LONG"), received_at=expiry)
        self.ledger.apply_order_snapshot("missing", [], as_of=expiry,
            observed_at=expiry, complete=True)
        self.assertFalse(self.ledger.risk_state_known())
        self.assertEqual(len(self.ledger.absent_orders()), 1)
        self.ledger.apply_order_event("test", "expired", replace(order,
            filled_qty=Decimal(".25"), status="EXPIRED", event_at=expiry),
            received_at=expiry)
        self.assertFalse(self.ledger.working_orders())
        self.assertFalse(self.ledger.absent_orders())
        self.assertEqual(self.ledger.position("market-one"), Decimal(".25"))


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.launch = Mock()

    def armed(self):
        return arm(self.root, "auto", "10", "32", "rest")

    def run_tick(self, present=False, now=1000):
        return tick(self.root, present=lambda: present, popen=self.launch,
                    clock=lambda: now)

    def test_missing_or_manual_stop_cannot_restart(self):
        self.assertEqual(self.run_tick(), "disarmed")
        state = self.armed()
        finish(self.root, "disarmed")
        self.assertEqual(self.run_tick(), "disarmed")
        self.launch.assert_not_called()
        finish(self.root, "recovered", state["token"])
        self.assertEqual(read_state(self.root)["status"], "disarmed")

    def test_no_second_executor_and_recovery_only_after_disappearance(self):
        self.armed()
        self.assertEqual(self.run_tick(True), "process_present")
        self.launch.assert_not_called()
        self.assertEqual(self.run_tick(), "recovery_started")
        args = self.launch.call_args.args[0]
        self.assertEqual(args[2:7], ["auto", "10", "32", "rest", "recovery-only"])
        self.assertTrue(self.launch.call_args.kwargs["close_fds"])
        self.assertEqual(self.run_tick(), "backoff")
        self.assertEqual(self.launch.call_count, 1)

    def test_recovery_crashes_are_bounded_across_reboot(self):
        self.armed()
        for now in (1000, 1200, 1400):
            self.assertEqual(self.run_tick(now=now), "recovery_started")
        self.assertEqual(self.run_tick(now=1600), "attention")
        self.assertEqual(self.launch.call_count, 3)
        self.assertEqual(self.run_tick(now=9999), "attention")

    def test_old_child_cannot_change_new_launch_and_complete_never_trades(self):
        old = self.armed()
        new = self.armed()
        finish(self.root, "attention", old["token"])
        self.assertEqual(read_state(self.root)["status"], "armed")
        finish(self.root, "recovered", new["token"])
        self.assertEqual(self.run_tick(), "recovered")
        self.launch.assert_not_called()

    def test_launch_failure_preserves_backoff_and_corrupt_state_refuses(self):
        self.armed()
        self.launch.side_effect = OSError("unavailable")
        with self.assertRaises(OSError):
            self.run_tick()
        self.assertEqual(self.run_tick(), "backoff")
        write_state(self.root, {"version": 1, "status": "armed", "attempts": 0})
        with self.assertRaises((KeyError, ValueError)):
            self.run_tick()

    def test_operator_stop_overrides_corrupt_state_and_busy_tick_does_not_queue(self):
        self.armed()
        (self.root / "data/host_recovery.json").write_text("broken")
        with self.assertRaises(ValueError):
            self.run_tick()
        finish(self.root, "disarmed")
        self.assertEqual(self.run_tick(), "disarmed")
        with control_lock(self.root):
            with self.assertRaises(BlockingIOError):
                with control_lock(self.root, blocking=False):
                    self.fail("acquired a held control lock")
        self.launch.assert_not_called()

    def test_recovery_preserves_verified_source_switch(self):
        self.armed()
        path = self.root / "data/account-source-selection.json"
        path.write_text('{"mode":"websocket"}')
        self.assertEqual(self.run_tick(), "recovery_started")
        self.assertEqual(self.launch.call_args.args[0][5], "saved")
        self.armed()
        path.write_text('{"mode":"bogus"}')
        with self.assertRaises(ValueError):
            self.run_tick()
        self.assertEqual(self.launch.call_count, 1)


class RecoveryRunnerTests(unittest.IsolatedAsyncioTestCase):
    use_rest = False

    def build(self, **kwargs):
        from test_live_commissioning import LiveCommissioningTests
        from liquidity_bot.live_commissioning import CommissioningRunner
        from liquidity_bot.rest_fallback import RestFallbackRunner
        harness = LiveCommissioningTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        with patch("test_live_commissioning.CommissioningRunner",
                   RestFallbackRunner if self.use_rest else CommissioningRunner):
            runner, client, _ = harness.build_runner(auto=True, **kwargs)
        runner.config = replace(runner.config, recovery_only=True)
        return runner, client, harness

    async def test_flat_recovery_returns_without_selection_or_entry(self):
        runner, client, _ = self.build()
        runner._select_reward_market = AsyncMock(side_effect=AssertionError("scan"))
        result = await runner.run()
        self.assertTrue(result.flat)
        self.assertEqual(result.stop_reason, "host_recovery_complete")
        self.assertEqual(result.orders_sent_to_polymarket, 0)
        runner._select_reward_market.assert_not_called()

    async def test_unknown_account_cannot_claim_flat_recovery(self):
        runner, client, _ = self.build()
        runner._rest_reconcile = AsyncMock(side_effect=RuntimeError("unknown"))
        from liquidity_bot.commissioning import CommissioningRefused
        with self.assertRaises(CommissioningRefused):
            await runner.preflight()

    def seed_order(self, runner, client, harness, slug, index, state="ORDER_STATE_NEW"):
        client.orders.rows[f"exchange-{index}"] = {
            "id": f"exchange-{index}", "marketSlug": slug,
            "side": "ORDER_SIDE_BUY", "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": ".49", "currency": "USD"},
            "quantity": "10", "cumQuantity": "0", "state": state,
            "createTime": RUNNER_NOW.isoformat(),
            "lastTransactTime": RUNNER_NOW.isoformat(),
        }
        runner.ledger.prepare_intent(f"intent-{index}", "PLACE", slug,
            f"client-{index}", client.orders.rows[f"exchange-{index}"],
            created_at=RUNNER_NOW)
        runner.ledger.transition_intent(f"intent-{index}", "SUBMITTING",
                                       updated_at=RUNNER_NOW)
        runner.ledger.transition_intent(f"intent-{index}", "ACKNOWLEDGED",
            exchange_order_id=f"exchange-{index}", updated_at=RUNNER_NOW)

    async def test_manual_closes_and_cancels_are_reconciled_before_recovery(self):
        runner, client, harness = self.build()
        self.seed_order(runner, client, harness, "market-one", 1,
                        "ORDER_STATE_CANCELED")
        runner.ledger.apply_position_snapshot(
            "old-position", [PositionState(
                "market-one", Decimal(10), NOW, cost=Decimal("4.9"))],
            as_of=NOW, observed_at=NOW, complete_account=True)
        runner._select_reward_market = AsyncMock(side_effect=AssertionError("scan"))
        result = await runner.run()
        self.assertTrue(result.flat)
        self.assertEqual(runner.ledger.position("market-one"), 0)
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(client.orders.cancel_calls)

    async def test_thirty_resting_entries_recover_without_any_new_entry(self):
        from test_live_commissioning import TickingClock
        class FastClock(TickingClock):
            def __call__(self):
                result = self.value
                self.value += timedelta(milliseconds=50)
                return result
        runner, client, harness = self.build(clock=FastClock(), max_markets=32)
        for index in range(30):
            self.seed_order(runner, client, harness, f"market-{index:02d}", index)
        runner._select_reward_market = AsyncMock(side_effect=AssertionError("scan"))
        result = await runner.run()
        self.assertTrue(result.flat)
        self.assertEqual(len(client.orders.cancel_calls), 30)
        self.assertFalse(client.orders.create_calls)
        runner._select_reward_market.assert_not_called()

    async def test_host_recovery_cannot_walk_away_from_a_parked_exit(self):
        _, _, harness = self.build()
        runner, client, slug = await harness._parking_runner("ORDER_SIDE_BUY")
        runner.config = replace(runner.config, recovery_only=True)
        runner.ledger.prepare_intent("old-exit", "PLACE", slug, "old-exit-client",
                                    client.orders.rows["resting-exit"], created_at=RUNNER_NOW)
        runner.ledger.transition_intent("old-exit", "SUBMITTING", updated_at=RUNNER_NOW)
        runner.ledger.transition_intent("old-exit", "ACKNOWLEDGED",
            exchange_order_id="resting-exit", updated_at=RUNNER_NOW)
        # This fixture represents a -5 position with a resting BUY exit, far
        # before kickoff. Ordinary operator shutdown is allowed to park it.
        await runner._shutdown("startup_recovery_only")
        self.assertTrue(client.orders.cancel_calls)

    async def test_recovery_stream_allows_flat_cleanup_during_rest_book_throttle(self):
        from test_live_commissioning import TickingClock
        from test_selection_books import Stream, book
        from liquidity_bot.selection_books import SelectionBookSource
        from liquidity_bot.errors import AdapterError
        runner, client, harness = self.build(clock=TickingClock())
        for index, slug in enumerate(('market-one', 'market-two')):
            self.seed_order(runner, client, harness, slug, index)
        class FreshStream(Stream):
            async def fresh_book(self, slug, *, clock, checkpoint=None):
                if checkpoint:
                    await checkpoint()
                return replace(book(slug), received_at=clock())
        stream = FreshStream()
        runner.gateway.selection_books = SelectionBookSource(
            client, stream_factory=lambda _: stream, clock=runner._now)
        runner._select_reward_market = AsyncMock(side_effect=AssertionError('new selection'))
        with patch.object(client.markets, 'book', new=AsyncMock(
                side_effect=AdapterError('limited', status_code=429))) as rest:
            result = await runner.run()
        self.assertTrue(result.flat)
        self.assertEqual(result.stop_reason, 'startup_recovery_only')
        self.assertEqual(len(client.orders.cancel_calls), 2)
        self.assertFalse(client.orders.create_calls)
        self.assertTrue(stream.closed)
        rest.assert_not_awaited()
        runner._select_reward_market.assert_not_awaited()

    async def test_managed_position_exception_never_enables_host_entries(self):
        from test_live_commissioning import TickingClock
        runner, client, _ = self.build(clock=TickingClock())
        client.markets.started.add("market-one")
        client.portfolio.rows["market-one"] = {
            "netPositionDecimal": "1.0000",
            "cost": {"value": ".49", "currency": "USD"},
            "updateTime": NOW.isoformat(),
        }
        runner._add_startup_markets_around_reserved_position = AsyncMock(
            side_effect=AssertionError("top-up"))
        mode = await runner.preflight()
        self.assertEqual(mode, "RECOVERY_ONLY")
        self.assertFalse(runner.driver.new_entry_allowed())
        runner._add_startup_markets_around_reserved_position.assert_not_called()
        await runner.private_stream.close()
        await runner.market_stream.close()


class RestRecoveryRunnerTests(RecoveryRunnerTests):
    use_rest = True
