from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import ExecutionLedger
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.venue_state import (
    PrivateLedgerProjector,
    normalize_execution,
    normalize_order,
    normalize_position,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)


def raw_order(**changes):
    row = {
        "id": "order-one",
        "marketSlug": "market-one",
        "side": "ORDER_SIDE_BUY",
        "intent": "ORDER_INTENT_BUY_SHORT",
        "price": {"value": "0.5000", "currency": "USD"},
        "quantity": "2.000000",
        "cumQuantity": "0.750000",
        "state": "ORDER_STATE_PARTIALLY_FILLED",
        "createTime": "2026-08-13T03:00:00Z",
    }
    row.update(changes)
    return row


def raw_execution(**changes):
    row = {
        "id": "execution-one",
        "order": raw_order(),
        "lastShares": "0.750000",
        "lastPx": {"value": "0.5000", "currency": "USD"},
        "type": "EXECUTION_TYPE_PARTIAL_FILL",
        "transactTime": "2026-08-13T03:00:01Z",
        "aggressor": False,
        "commissionNotionalCollected": {"value": "-0.009375", "currency": "USD"},
    }
    row.update(changes)
    return row


class NormalizationTests(unittest.TestCase):
    def test_a_rejected_order_keeps_the_venue_reason(self):
        """203 rejections were unattributable because this was dropped.

        A rejected order never rests, and reward pays only for resting time, so
        telling an ordinary post-only collision from a real defect matters.
        """
        item = normalize_order(raw_order(
            state="ORDER_STATE_REJECTED",
            orderRejectReason="ORD_REJECT_REASON_POST_ONLY"), observed_at=NOW)

        self.assertEqual(item.status, "REJECTED")
        self.assertEqual(item.reject_reason, "ORD_REJECT_REASON_POST_ONLY")

    def test_a_healthy_order_carries_no_rejection_reason(self):
        self.assertIsNone(normalize_order(
            raw_order(), observed_at=NOW).reject_reason)

    def test_order_amounts_and_cumulative_quantity_stay_decimal(self):
        item = normalize_order(raw_order(), observed_at=NOW)
        self.assertEqual(item.client_order_id, "venue:order-one")
        self.assertEqual(item.price, Decimal("0.5000"))
        self.assertEqual(item.filled_qty, Decimal("0.750000"))
        self.assertEqual(item.status, "PARTIAL")

    def test_execution_preserves_intent_and_signed_maker_commission(self):
        item = normalize_execution(raw_execution(), observed_at=NOW)
        self.assertEqual(item.intent, "ORDER_INTENT_BUY_SHORT")
        self.assertEqual(item.fee, Decimal("-0.009375"))
        self.assertEqual(item.liquidity, "MAKER")

    def test_position_prefers_exact_decimal_field(self):
        item = normalize_position("market-one", {
            "netPosition": "2", "netPositionDecimal": "1.750000",
            "updateTime": "2026-08-13T03:00:00Z"}, observed_at=NOW)
        self.assertEqual(item.net_qty, Decimal("1.750000"))

    def test_rounded_legacy_position_without_decimal_fails_closed(self):
        with self.assertRaises(AdapterError):
            normalize_position(
                "market-one", {"netPosition": "2"}, observed_at=NOW)

    def test_unknown_order_state_fails_closed(self):
        with self.assertRaises(AdapterError):
            normalize_order(raw_order(state="ORDER_STATE_FUTURE"), observed_at=NOW)

    def test_terminal_order_prefers_last_transact_time_when_update_is_absent(self):
        item = normalize_order(raw_order(
            state="ORDER_STATE_CANCELED",
            createTime="2026-08-13T03:00:00Z",
            lastTransactTime="2026-08-13T03:00:02Z"), observed_at=NOW)
        self.assertEqual(item.status, "CANCELED")
        self.assertEqual(
            item.event_at, datetime(2026, 8, 13, 3, 0, 2, tzinfo=timezone.utc))


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp(prefix="liquidity-projector-")) / "ledger.db"
        self.ledger = ExecutionLedger(path)
        self.projector = PrivateLedgerProjector(self.ledger)

    def tearDown(self):
        self.ledger.close()

    def test_order_snapshot_waits_for_eof_before_complete_reconcile(self):
        first = PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [raw_order()], "eof": False}, "orders-request")
        self.projector.apply(first, received_at=NOW)
        self.assertEqual(self.ledger.query(
            "SELECT COUNT(*) n FROM execution_orders")[0]["n"], 0)
        last = PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [], "eof": True}, "orders-request")
        self.projector.apply(last, received_at=NOW)
        self.assertEqual(self.ledger.query(
            "SELECT COUNT(*) n FROM execution_orders")[0]["n"], 1)

    def test_fill_update_books_one_fill_and_short_position(self):
        event = PrivateEvent(
            PrivateEventKind.ORDER_UPDATE,
            {"execution": raw_execution()}, "order-subscription")
        self.projector.apply(event, received_at=NOW)
        self.projector.apply(event, received_at=NOW)
        fill_count = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_fills")[0]["n"]
        net = self.ledger.query("SELECT net_qty FROM execution_positions")[0]["net_qty"]
        self.assertEqual(fill_count, 1)
        self.assertEqual(net, "-0.750000")

    def test_position_snapshot_requires_explicit_eof(self):
        event = PrivateEvent(
            PrivateEventKind.POSITION_SNAPSHOT,
            {"positions": {}}, "positions-request")
        with self.assertRaises(AdapterError):
            self.projector.apply(event, received_at=NOW)

    def test_documented_before_after_position_update_uses_exact_after_value(self):
        event = PrivateEvent(
            PrivateEventKind.POSITION_UPDATE,
            {"marketSlug": "market-one",
             "beforePosition": {"netPositionDecimal": "1.00"},
             "afterPosition": {"netPositionDecimal": "1.50"},
             "updateTime": "2026-08-13T03:00:01Z"},
            "positions-request")
        self.projector.apply(event, received_at=NOW)
        row = self.ledger.query(
            "SELECT net_qty FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "1.50")

    def test_documented_position_update_uses_single_market_scope_when_slug_omitted(self):
        event = PrivateEvent(
            PrivateEventKind.POSITION_UPDATE,
            {"beforePosition": {"netPositionDecimal": "0.00"},
             "afterPosition": {"netPositionDecimal": "-1.00"},
             "updateTime": "2026-08-13T05:39:26Z",
             "tradeId": "trade-live-fill"},
            "positions-request",
            "SUBSCRIPTION_TYPE_POSITION",
            ("market-one",))
        self.projector.apply(event, received_at=NOW)
        row = self.ledger.query(
            "SELECT net_qty FROM execution_positions "
            "WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "-1.00")

    def test_slugless_position_update_without_single_market_scope_fails_closed(self):
        event = PrivateEvent(
            PrivateEventKind.POSITION_UPDATE,
            {"beforePosition": {"netPositionDecimal": "0.00"},
             "afterPosition": {"netPositionDecimal": "-1.00"}},
            "positions-request")
        with self.assertRaisesRegex(AdapterError, "market slug is missing"):
            self.projector.apply(event, received_at=NOW)


if __name__ == "__main__":
    unittest.main()
