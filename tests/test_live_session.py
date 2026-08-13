from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.execution_ledger import ExecutionLedger, OrderState, PositionState
from liquidity_bot.live_orders import SubmittedOrder
from liquidity_bot.live_session import (
    ControlledActionDriver,
    ControlledTrialStartup,
    MarketKillSwitch,
    PrivateStateGate,
)
from liquidity_bot.models import MarketMetadata
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.readiness import ReadinessVerdict
from liquidity_bot.reconciler import ActionKind, PlanAction
from liquidity_bot.runtime_safety import SessionRiskGuard


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


def market() -> MarketMetadata:
    return MarketMetadata(
        slug="market-one",
        game_start=NOW + timedelta(days=1),
        tick_size=.01,
        minimum_trade_qty=1,
        active=True,
        closed=False,
        status="MARKET_STATE_OPEN")


def order(status="OPEN") -> OrderState:
    return OrderState(
        client_order_id="client-one", exchange_order_id="exchange-one",
        market_slug="market-one", side="ORDER_SIDE_BUY",
        price=Decimal("0.50"), original_qty=Decimal("1"),
        filled_qty=Decimal("0"), status=status, event_at=NOW)


class Account:
    def __init__(self, orders=(), positions=()):
        self.orders = tuple(orders)
        self.positions = tuple(positions)
        self.calls = []

    async def fetch_open_orders(self, **kwargs):
        self.calls.append("orders")
        return self.orders

    async def fetch_positions(self, **kwargs):
        self.calls.append("positions")
        return self.positions


class Executor:
    def __init__(self):
        self.places = []
        self.cancels = []

    async def place(self, request, metadata, *, now):
        self.places.append((request, metadata, now))
        return SubmittedOrder("intent", "client-new", "exchange-new")

    async def cancel(self, **kwargs):
        self.cancels.append(kwargs)
        return f"cancel-{len(self.cancels)}"


class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp(prefix="liquidity-session-")) / "ledger.db"
        self.ledger = ExecutionLedger(path)

    def tearDown(self):
        self.ledger.close()

    def run_async(self, value):
        return asyncio.run(value)

    def verdict(self, ready=True):
        return ReadinessVerdict(
            "TRIAL_ELIGIBLE_NOT_SCALE" if ready else "DO_NOT_START_TRIAL",
            ready, False, () if ready else ("shadow evidence missing",), ())

    def test_not_ready_verdict_refuses_before_account_calls(self):
        account = Account()
        report = self.run_async(ControlledTrialStartup(
            account, self.ledger).reconcile(self.verdict(False), observed_at=NOW))
        self.assertFalse(report.ready)
        self.assertEqual(account.calls, [])

    def test_flat_shared_account_snapshot_can_pass_startup(self):
        account = Account()
        report = self.run_async(ControlledTrialStartup(
            account, self.ledger).reconcile(self.verdict(), observed_at=NOW))
        self.assertTrue(report.ready)
        self.assertEqual(account.calls, ["orders", "positions"])

    def test_any_existing_order_or_position_blocks_startup(self):
        account = Account(
            [OrderState(
                client_order_id="venue:external", exchange_order_id="external",
                market_slug="market-one", side="ORDER_SIDE_BUY",
                price=Decimal("0.50"), original_qty=Decimal("1"),
                filled_qty=Decimal("0"), status="OPEN", event_at=NOW)],
            [PositionState("market-one", Decimal("1"), NOW)])
        report = self.run_async(ControlledTrialStartup(
            account, self.ledger).reconcile(self.verdict(), observed_at=NOW))
        self.assertFalse(report.ready)
        self.assertIn("shared account has open orders", report.blockers)
        self.assertIn("shared account has nonzero positions", report.blockers)

    def test_private_gate_requires_reconcile_snapshots_and_fresh_messages(self):
        gate = PrivateStateGate(max_silence_seconds=10)
        gate.on_connect()
        gate.on_rest_reconciled(NOW)
        gate.observe(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT, {"orders": [], "eof": True}, "o"),
            received_at=NOW)
        self.assertFalse(gate.ready(self.ledger, now=NOW))
        gate.observe(PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT, {"balances": []}, "b"),
            received_at=NOW)
        self.assertTrue(gate.ready(self.ledger, now=NOW + timedelta(seconds=9)))
        self.assertFalse(gate.ready(self.ledger, now=NOW + timedelta(seconds=11)))
        gate.on_disconnect()
        self.assertFalse(gate.ready(self.ledger, now=NOW))

    def test_new_incomplete_order_snapshot_revokes_private_readiness(self):
        gate = PrivateStateGate(max_silence_seconds=10)
        gate.on_connect()
        gate.on_rest_reconciled(NOW)
        gate.observe(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [], "eof": True}, "complete"), received_at=NOW)
        gate.observe(PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
            {"balances": []}, "balance"), received_at=NOW)
        self.assertTrue(gate.ready(self.ledger, now=NOW))
        gate.observe(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [], "eof": False}, "new-page"), received_at=NOW)
        self.assertFalse(gate.ready(self.ledger, now=NOW))

    def test_empty_account_can_use_complete_rest_orders_when_ws_omits_snapshot(self):
        gate = PrivateStateGate(max_silence_seconds=10)
        gate.on_connect()
        gate.on_rest_reconciled(NOW)
        gate.observe(PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
            {"balances": []}, "balance"), received_at=NOW)
        self.assertFalse(gate.order_snapshot_seen)
        self.assertTrue(gate.ready(self.ledger, now=NOW))

    def test_quiet_connected_private_stream_can_be_backstopped_by_rest(self):
        gate = PrivateStateGate(max_silence_seconds=10)
        gate.on_connect()
        gate.on_rest_reconciled(NOW)
        gate.observe(PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
            {"balances": []}, "balance"), received_at=NOW)
        later = NOW + timedelta(seconds=11)
        self.assertFalse(gate.ready(self.ledger, now=later))
        gate.on_rest_reconciled(later)
        self.assertTrue(gate.ready(self.ledger, now=later))
        self.assertEqual(gate.last_message_at, NOW)

    def test_driver_translates_full_position_ioc_and_only_one_action(self):
        executor = Executor()
        driver = ControlledActionDriver(executor, self.ledger)
        actions = [
            PlanAction(ActionKind.PLACE_IOC, "market-one", "timeout",
                       side="offer", price=.48, qty=Decimal("1")),
            PlanAction(ActionKind.PLACE_IOC, "market-one", "duplicate",
                       side="offer", price=.47, qty=Decimal("1")),
        ]
        result = self.run_async(driver.apply_one(
            actions, market=market(), position=Decimal("1"), now=NOW))
        self.assertIsNotNone(result.submitted)
        self.assertEqual(len(executor.places), 1)
        request = executor.places[0][0]
        self.assertTrue(request.reducing)
        self.assertFalse(request.post_only)

    def test_driver_refuses_partial_ioc(self):
        driver = ControlledActionDriver(Executor(), self.ledger)
        action = PlanAction(
            ActionKind.PLACE_IOC, "market-one", "timeout",
            side="offer", price=.48, qty=Decimal("1"))
        with self.assertRaisesRegex(RuntimeError, "entire exact position"):
            self.run_async(driver.apply_one(
                [action], market=market(), position=Decimal("2"), now=NOW))

    def test_tripped_runtime_guard_blocks_building_but_allows_reducing(self):
        guard = SessionRiskGuard(
            started_at=NOW, starting_equity=Decimal("100"),
            max_session_loss=Decimal("5"), max_market_loss=Decimal("2"),
            max_duration_seconds=1800)
        guard.observe_equity("95", observed_at=NOW)
        executor = Executor()
        driver = ControlledActionDriver(executor, self.ledger, guard)
        building = PlanAction(
            ActionKind.PLACE_POST_ONLY, "market-one", "reward",
            side="bid", price=.50, qty=Decimal("1"))
        result = self.run_async(driver.apply_one(
            [building], market=market(), position=Decimal("0"), now=NOW))
        self.assertEqual(result.action.kind, ActionKind.HALT_NEW_RISK)
        self.assertEqual(executor.places, [])

        reducing = PlanAction(
            ActionKind.PLACE_POST_ONLY, "market-one", "reduce",
            side="offer", price=.51, qty=Decimal("1"))
        result = self.run_async(driver.apply_one(
            [reducing], market=market(), position=Decimal("1"), now=NOW))
        self.assertIsNotNone(result.submitted)
        self.assertTrue(executor.places[0][0].reducing)

    def test_kill_switch_waits_for_terminal_cancel_and_flat_position(self):
        self.ledger.apply_order_event("ws", "new", order(), received_at=NOW)
        executor = Executor()
        kill = MarketKillSwitch(executor, self.ledger)
        report = self.run_async(kill.initiate("market-one", now=NOW))
        self.assertFalse(report.safe_to_stop)
        self.assertEqual(report.cancel_requests, 1)
        self.assertEqual(len(executor.cancels), 1)
        self.ledger.apply_order_event(
            "ws", "canceled", order("CANCELED"), received_at=NOW)
        self.assertTrue(kill.status("market-one").safe_to_stop)

    def test_kill_switch_refuses_unowned_order(self):
        unowned = OrderState(
            client_order_id="venue:external", exchange_order_id="external",
            market_slug="market-one", side="ORDER_SIDE_BUY",
            price=Decimal("0.50"), original_qty=Decimal("1"),
            filled_qty=Decimal("0"), status="OPEN", event_at=NOW)
        self.ledger.apply_order_event("ws", "external", unowned, received_at=NOW)
        executor = Executor()
        report = self.run_async(MarketKillSwitch(
            executor, self.ledger).initiate("market-one", now=NOW))
        self.assertFalse(report.safe_to_stop)
        self.assertEqual(report.cancel_requests, 0)
        self.assertEqual(executor.cancels, [])
        self.assertIn("unowned working order", report.blockers[0])


if __name__ == "__main__":
    unittest.main()
