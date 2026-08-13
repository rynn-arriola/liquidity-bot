from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.execution_ledger import (
    Execution,
    ExecutionLedger,
    LedgerConflict,
    OrderState,
    PositionState,
)
from liquidity_bot.models import RewardEarning


UTC = timezone.utc
T0 = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)


def order(*, filled="0", status="OPEN", at=T0) -> OrderState:
    return OrderState(
        client_order_id="client-one",
        exchange_order_id="exchange-one",
        market_slug="market-one",
        side="BUY",
        price=Decimal("0.50"),
        original_qty=Decimal("2.00"),
        filled_qty=Decimal(filled),
        status=status,
        event_at=at,
    )


def execution(execution_id: str, qty: str, *, side="BUY",
              intent="ORDER_INTENT_BUY_LONG", at=T0) -> Execution:
    return Execution(
        execution_id=execution_id,
        client_order_id="client-one",
        exchange_order_id="exchange-one",
        market_slug="market-one",
        side=side,
        price=Decimal("0.50"),
        qty=Decimal(qty),
        fee=Decimal("0.001250"),
        liquidity="MAKER",
        intent=intent,
        event_at=at,
    )


class ExecutionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="liquidity-ledger-")) / "ledger.db"
        self.ledger = ExecutionLedger(self.path)

    def tearDown(self):
        self.ledger.close()

    def test_pre_submit_intent_survives_crash_and_is_idempotent(self):
        created = self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one",
            {"price": "0.50", "qty": "2.00"}, created_at=T0)
        self.assertTrue(created)
        self.ledger.close()
        self.ledger = ExecutionLedger(self.path)
        self.assertEqual(len(self.ledger.unresolved_intents()), 1)
        repeated = self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one",
            {"price": "0.50", "qty": "2.00"}, created_at=T0)
        self.assertFalse(repeated)
        with self.assertRaises(LedgerConflict):
            self.ledger.prepare_intent(
                "intent-one", "PLACE", "market-one", "client-one",
                {"price": "0.51", "qty": "2.00"}, created_at=T0)

    def test_timeout_requires_reconciliation_and_blocks_risk(self):
        self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one", {}, created_at=T0)
        self.ledger.transition_intent(
            "intent-one", "SUBMITTING", updated_at=T0 + timedelta(seconds=1))
        self.ledger.transition_intent(
            "intent-one", "UNCERTAIN", updated_at=T0 + timedelta(seconds=2),
            error="request timeout")
        self.assertFalse(self.ledger.risk_state_known())
        self.ledger.transition_intent(
            "intent-one", "RECONCILED", updated_at=T0 + timedelta(seconds=3))
        self.assertTrue(self.ledger.risk_state_known())

    def test_acknowledged_intent_blocks_risk_until_state_is_reconciled(self):
        self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one", {}, created_at=T0)
        self.ledger.transition_intent("intent-one", "SUBMITTING", updated_at=T0)
        self.ledger.transition_intent(
            "intent-one", "ACKNOWLEDGED", updated_at=T0,
            exchange_order_id="exchange-one")
        self.assertFalse(self.ledger.risk_state_known())
        self.ledger.transition_intent("intent-one", "RECONCILED", updated_at=T0)
        self.assertTrue(self.ledger.risk_state_known())

    def test_duplicate_fill_is_idempotent_and_position_is_exact_decimal(self):
        self.ledger.apply_order_event("ws", "order-1", order(), received_at=T0)
        item = execution("fill-one", "0.125")
        self.assertTrue(self.ledger.apply_execution(item, received_at=T0))
        self.assertFalse(self.ledger.apply_execution(item, received_at=T0))
        position = self.ledger.query(
            "SELECT net_qty FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(position["net_qty"], "0.125")
        self.assertEqual(self.ledger.query(
            "SELECT COUNT(*) n FROM execution_fills")[0]["n"], 1)

    def test_duplicate_event_id_with_different_content_fails_closed(self):
        self.ledger.apply_execution(
            execution("fill-one", "0.125"), received_at=T0)
        with self.assertRaises(LedgerConflict):
            self.ledger.apply_execution(
                execution("fill-one", "0.250"), received_at=T0)

    def test_buy_short_intent_reduces_position_even_with_buy_side(self):
        self.ledger.apply_execution(
            execution("short-fill", "0.25", side="BUY",
                      intent="ORDER_INTENT_BUY_SHORT"), received_at=T0)
        row = self.ledger.query(
            "SELECT net_qty FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "-0.25")

    def test_partial_fills_accumulate_once_and_complete_order(self):
        self.ledger.apply_order_event("ws", "order-1", order(), received_at=T0)
        self.ledger.apply_execution(execution("fill-1", "0.75"), received_at=T0)
        self.ledger.apply_execution(
            execution("fill-2", "1.25", at=T0 + timedelta(seconds=1)),
            received_at=T0 + timedelta(seconds=1))
        row = self.ledger.query(
            "SELECT filled_qty,status FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual(row["filled_qty"], "2.00")
        self.assertEqual(row["status"], "FILLED")

    def test_cumulative_order_update_and_execution_do_not_double_count(self):
        cumulative = order(filled="0.75", status="PARTIAL")
        self.ledger.apply_order_event("ws", "order-partial", cumulative,
                                      received_at=T0)
        self.ledger.apply_execution(execution("fill-1", "0.75"), received_at=T0)
        row = self.ledger.query(
            "SELECT filled_qty,status FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual(row["filled_qty"], "0.75")
        self.assertEqual(row["status"], "PARTIAL")

    def test_execution_before_order_snapshot_is_reflected_in_cumulative_fill(self):
        self.ledger.apply_execution(execution("fill-before-order", "0.75"),
                                    received_at=T0)
        self.ledger.apply_order_event("ws", "order-after-fill", order(),
                                      received_at=T0 + timedelta(seconds=1))
        row = self.ledger.query(
            "SELECT filled_qty,status FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual(row["filled_qty"], "0.75")
        self.assertEqual(row["status"], "PARTIAL")

    def test_stale_order_snapshot_cannot_reopen_newer_terminal_order(self):
        newer = order(filled="2.00", status="FILLED", at=T0 + timedelta(seconds=10))
        self.ledger.apply_order_event(
            "ws", "terminal", newer, received_at=T0 + timedelta(seconds=10))
        stale = order(filled="0", status="OPEN", at=T0)
        self.ledger.apply_order_snapshot(
            "snapshot-old", [stale], as_of=T0,
            observed_at=T0 + timedelta(seconds=20), complete=True)
        row = self.ledger.query(
            "SELECT status,filled_qty FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual((row["status"], row["filled_qty"]), ("FILLED", "2.00"))

    def test_complete_reconnect_snapshot_marks_missing_order_unknown_not_canceled(self):
        self.ledger.apply_order_event("ws", "open", order(), received_at=T0)
        self.ledger.apply_order_snapshot(
            "snapshot-new", [], as_of=T0 + timedelta(seconds=5),
            observed_at=T0 + timedelta(seconds=6), complete=True)
        row = self.ledger.query(
            "SELECT status,unknown_reason FROM execution_orders")[0]
        self.assertEqual(row["status"], "UNKNOWN_ABSENT")
        self.assertFalse(self.ledger.risk_state_known())

    def test_fresh_position_snapshot_reconciles_but_stale_snapshot_does_not(self):
        update = PositionState("market-one", Decimal("1.750000"),
                               T0 + timedelta(seconds=10))
        self.ledger.apply_position_event(
            "private_ws", "position-new", update,
            received_at=T0 + timedelta(seconds=10))
        self.ledger.apply_position_snapshot(
            "stale", [PositionState("market-one", Decimal("0"), T0)],
            as_of=T0, observed_at=T0 + timedelta(seconds=20),
            complete_account=True)
        row = self.ledger.query("SELECT net_qty FROM execution_positions")[0]
        self.assertEqual(row["net_qty"], "1.750000")
        self.ledger.apply_position_snapshot(
            "fresh", [PositionState("market-one", Decimal("1.50"),
                                    T0 + timedelta(seconds=30))],
            as_of=T0 + timedelta(seconds=30),
            observed_at=T0 + timedelta(seconds=31), complete_account=True)
        row = self.ledger.query("SELECT net_qty FROM execution_positions")[0]
        self.assertEqual(row["net_qty"], "1.50")
        reconciliation = self.ledger.query(
            "SELECT difference FROM execution_position_reconciliations")
        self.assertEqual(reconciliation[-1]["difference"], "-0.250000")

    def test_late_execution_already_in_position_snapshot_is_not_added_twice(self):
        self.ledger.apply_position_snapshot(
            "snapshot", [PositionState("market-one", Decimal("0.75"),
                                       T0 + timedelta(seconds=10))],
            as_of=T0 + timedelta(seconds=10),
            observed_at=T0 + timedelta(seconds=11), complete_account=True)
        self.ledger.apply_execution(
            execution("late-fill", "0.75", at=T0 + timedelta(seconds=5)),
            received_at=T0 + timedelta(seconds=12))
        row = self.ledger.query(
            "SELECT net_qty,source FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "0.75")
        self.assertEqual(row["source"], "position_snapshot")

    def test_snapshot_watermark_survives_a_newer_execution(self):
        self.ledger.apply_position_snapshot(
            "snapshot", [PositionState("market-one", Decimal("0.75"),
                                       T0 + timedelta(seconds=10))],
            as_of=T0 + timedelta(seconds=10),
            observed_at=T0 + timedelta(seconds=11), complete_account=True)
        self.ledger.apply_execution(
            execution("new-fill", "0.25", at=T0 + timedelta(seconds=12)),
            received_at=T0 + timedelta(seconds=12))
        self.ledger.apply_execution(
            execution("late-old-fill", "0.75", at=T0 + timedelta(seconds=5)),
            received_at=T0 + timedelta(seconds=13))
        row = self.ledger.query(
            "SELECT net_qty,authoritative_through FROM execution_positions "
            "WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "1.00")
        self.assertEqual(row["authoritative_through"],
                         (T0 + timedelta(seconds=10)).isoformat())

    def test_inventory_basis_rebuilds_weighted_entry_and_flat_reset(self):
        def fill(fill_id, qty, price, intent, seconds):
            return Execution(
                execution_id=fill_id, client_order_id="client-one",
                exchange_order_id="exchange-one", market_slug="market-one",
                side="BUY", price=Decimal(price), qty=Decimal(qty),
                fee=Decimal("0"), intent=intent,
                event_at=T0 + timedelta(seconds=seconds))

        self.ledger.apply_execution(fill(
            "open-a", ".4", ".40", "ORDER_INTENT_BUY_LONG", 1),
            received_at=T0 + timedelta(seconds=1))
        self.ledger.apply_execution(fill(
            "open-b", ".6", ".60", "ORDER_INTENT_BUY_LONG", 2),
            received_at=T0 + timedelta(seconds=2))
        basis = self.ledger.inventory_basis("market-one")
        self.assertEqual(basis.net_qty, Decimal("1.0"))
        self.assertEqual(basis.average_entry_price, Decimal("0.52"))
        self.assertEqual(basis.opened_at, T0 + timedelta(seconds=1))

        self.ledger.apply_execution(fill(
            "close", "1", ".55", "ORDER_INTENT_SELL_LONG", 3),
            received_at=T0 + timedelta(seconds=3))
        self.assertIsNone(self.ledger.inventory_basis("market-one"))

    def test_inventory_basis_fails_closed_when_venue_position_disagrees(self):
        self.ledger.apply_execution(
            execution("one-fill", "1"), received_at=T0)
        self.ledger.apply_position_event(
            "private_ws", "disagree",
            PositionState("market-one", Decimal("2"), T0 + timedelta(seconds=1)),
            received_at=T0 + timedelta(seconds=1))
        self.assertIsNone(self.ledger.inventory_basis("market-one"))

    def test_reward_observations_are_separate_from_execution_pnl(self):
        earning = RewardEarning(
            "2026-08-13", "market-one", "liquidityProgram", 1.25, "PENDING")
        self.ledger.record_reward_earnings([earning], observed_at=T0)
        self.ledger.record_reward_earnings([earning], observed_at=T0)
        rows = self.ledger.query(
            "SELECT date,market_slug,status,reward FROM execution_reward_earnings")
        self.assertEqual(len(rows), 1)
        self.assertEqual(dict(rows[0]), {
            "date": "2026-08-13",
            "market_slug": "market-one",
            "status": "PENDING",
            "reward": "1.25",
        })


if __name__ == "__main__":
    unittest.main()
