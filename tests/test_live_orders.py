from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.adapters import AsyncReadOnlyGateway
from liquidity_bot.execution_ledger import ExecutionLedger, OrderState
from liquidity_bot.live_orders import (
    AsyncTradingGateway,
    LiveOrderExecutor,
    OrderOutcomeUncertain,
    OrderRequest,
    OrderValidationError,
    build_order_payload,
)
from liquidity_bot.models import MarketMetadata
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.venue_state import PrivateLedgerProjector


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


def market(**changes) -> MarketMetadata:
    values = {
        "slug": "market-one",
        "game_start": datetime(2026, 8, 14, tzinfo=timezone.utc),
        "tick_size": 0.005,
        "minimum_trade_qty": 0.25,
        "active": True,
        "closed": False,
        "status": "MARKET_STATE_OPEN",
    }
    values.update(changes)
    return MarketMetadata(**values)


def order_state(status="OPEN", *, exchange_id="exchange-one") -> OrderState:
    return OrderState(
        client_order_id="client-one",
        exchange_order_id=exchange_id,
        market_slug="market-one",
        side="ORDER_SIDE_BUY",
        price=Decimal("0.500"),
        original_qty=Decimal("1.00"),
        filled_qty=Decimal("0"),
        status=status,
        event_at=NOW,
    )


class CountingLimiter:
    def __init__(self):
        self.calls = 0

    async def wait(self):
        self.calls += 1


class Orders:
    def __init__(self):
        self.create_results = []
        self.retrieve_result = None
        self.create_calls = []
        self.cancel_calls = []
        self.retrieve_calls = []

    async def create(self, payload):
        self.create_calls.append(payload)
        result = self.create_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def cancel(self, order_id, payload):
        self.cancel_calls.append((order_id, payload))
        result = self.create_results.pop(0) if self.create_results else None
        if isinstance(result, Exception):
            raise result
        return result

    async def retrieve(self, order_id):
        self.retrieve_calls.append(order_id)
        return self.retrieve_result


class Client:
    def __init__(self):
        self.orders = Orders()


class LiveOrderPayloadTests(unittest.TestCase):
    def test_post_only_payload_is_metadata_aligned_and_automatic(self):
        payload = build_order_payload(OrderRequest(
            "market-one", "bid", Decimal("0.505"), Decimal("1.25")), market())
        self.assertEqual(payload["intent"], "ORDER_INTENT_BUY_LONG")
        self.assertEqual(payload["price"]["value"], "0.505")
        self.assertEqual(payload["quantity"], 1.25)
        self.assertTrue(payload["participateDontInitiate"])
        self.assertEqual(
            payload["manualOrderIndicator"],
            "MANUAL_ORDER_INDICATOR_AUTOMATIC")
        self.assertFalse(payload["synchronousExecution"])

    def test_observed_market_status_open_family_is_accepted(self):
        payload = build_order_payload(OrderRequest(
            "market-one", "bid", Decimal("0.505"), Decimal("1")),
            market(status="MARKET_STATUS_OPEN"))
        self.assertEqual(payload["quantity"], 1)
        self.assertTrue(payload["participateDontInitiate"])

    def test_reducing_ioc_uses_sell_intent_and_never_post_only(self):
        payload = build_order_payload(OrderRequest(
            "market-one", "offer", Decimal("0.495"), Decimal("1.00"),
            reducing=True,
            time_in_force="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            post_only=False), market())
        self.assertEqual(payload["intent"], "ORDER_INTENT_SELL_LONG")
        self.assertEqual(
            payload["tif"], "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
        self.assertFalse(payload["participateDontInitiate"])

    def test_missing_or_misaligned_metadata_fails_before_network(self):
        bad = (
            (OrderRequest("market-one", "bid", Decimal("0.503"), Decimal("1")),
             market()),
            (OrderRequest("market-one", "bid", Decimal("0.505"), Decimal("0.30")),
             market()),
            (OrderRequest("market-one", "bid", Decimal("0.505"), Decimal("1")),
             market(minimum_trade_qty=None)),
        )
        for request, metadata in bad:
            with self.subTest(request=request, metadata=metadata):
                with self.assertRaises(OrderValidationError):
                    build_order_payload(request, metadata)


class LiveOrderExecutorTests(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp(prefix="liquidity-live-orders-")) / "ledger.db"
        self.ledger = ExecutionLedger(path)
        self.client = Client()
        self.limiter = CountingLimiter()
        read = AsyncReadOnlyGateway(self.client, limiter=self.limiter)
        self.gateway = AsyncTradingGateway(read)
        self.executor = LiveOrderExecutor(self.gateway, self.ledger)

    def tearDown(self):
        self.ledger.close()

    def run_async(self, value):
        return asyncio.run(value)

    def request(self):
        return OrderRequest(
            "market-one", "bid", Decimal("0.500"), Decimal("1.00"))

    def test_acknowledged_create_uses_one_gate_and_waits_for_exchange_state(self):
        self.client.orders.create_results = [{"id": "exchange-one", "executions": []}]
        submitted = self.run_async(self.executor.place(
            self.request(), market(), now=NOW,
            intent_id="intent-one", client_order_id="client-one"))
        self.assertEqual(submitted.exchange_order_id, "exchange-one")
        self.assertEqual(self.limiter.calls, 1)
        self.assertEqual(len(self.client.orders.create_calls), 1)
        self.assertEqual(
            self.ledger.unresolved_intents()[0]["state"], "ACKNOWLEDGED")
        self.assertEqual(
            self.ledger.resolve_client_order_id("exchange-one"), "client-one")
        self.ledger.apply_order_event(
            "private_ws", "exchange-new", order_state(), received_at=NOW)
        self.assertEqual(self.ledger.unresolved_intents(), [])

    def test_create_timeout_is_not_retried_and_quarantines_market(self):
        self.client.orders.create_results = [TimeoutError("lost response")]
        with self.assertRaises(OrderOutcomeUncertain):
            self.run_async(self.executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-timeout", client_order_id="client-timeout"))
        self.assertEqual(self.limiter.calls, 1)
        self.assertEqual(len(self.client.orders.create_calls), 1)
        row = self.ledger.unresolved_intents()[0]
        self.assertEqual(row["state"], "UNCERTAIN")
        self.assertTrue(self.ledger.market_has_unresolved_intent("market-one"))
        self.assertFalse(self.ledger.risk_state_known())

    def test_missing_create_id_is_uncertain(self):
        self.client.orders.create_results = [{"executions": []}]
        with self.assertRaises(OrderOutcomeUncertain):
            self.run_async(self.executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-no-id", client_order_id="client-no-id"))
        self.assertEqual(self.ledger.unresolved_intents()[0]["state"], "UNCERTAIN")

    def test_private_stream_adopts_exactly_one_timed_out_create(self):
        self.client.orders.create_results = [TimeoutError("lost response")]
        with self.assertRaises(OrderOutcomeUncertain):
            self.run_async(self.executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-timeout", client_order_id="client-timeout"))
        raw = {
            "id": "recovered-exchange",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.500", "currency": "USD"},
            "quantity": "1.00",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }
        PrivateLedgerProjector(self.ledger).apply(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [raw], "eof": True}, "recovery-snapshot"),
            received_at=NOW)
        self.assertEqual(self.ledger.unresolved_intents(), [])
        row = self.ledger.query("SELECT * FROM execution_orders")[0]
        self.assertEqual(row["client_order_id"], "client-timeout")
        self.assertEqual(row["exchange_order_id"], "recovered-exchange")
        self.assertTrue(self.ledger.risk_state_known())

    def test_crash_after_remote_create_is_recovered_without_duplicate_order(self):
        self.client.orders.create_results = [
            {"id": "exchange-after-crash", "executions": []}]
        transition = self.ledger.transition_intent

        def crash_before_ack(intent_id, state, **kwargs):
            if state == "ACKNOWLEDGED":
                raise RuntimeError("simulated process crash after create response")
            return transition(intent_id, state, **kwargs)

        with patch.object(
                self.ledger, "transition_intent", side_effect=crash_before_ack):
            with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
                self.run_async(self.executor.place(
                    self.request(), market(), now=NOW,
                    intent_id="intent-crash", client_order_id="client-crash"))
        self.assertEqual(len(self.client.orders.create_calls), 1)
        self.assertEqual(
            self.ledger.unresolved_intents()[0]["state"], "SUBMITTING")

        raw = {
            "id": "exchange-after-crash",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.500", "currency": "USD"},
            "quantity": "1.00",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }
        PrivateLedgerProjector(self.ledger).apply(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [raw], "eof": True}, "crash-recovery"),
            received_at=NOW)
        self.assertEqual(self.ledger.unresolved_intents(), [])
        self.assertEqual(len(self.client.orders.create_calls), 1)
        row = self.ledger.query("SELECT * FROM execution_orders")[0]
        self.assertEqual(row["client_order_id"], "client-crash")
        self.assertEqual(row["exchange_order_id"], "exchange-after-crash")

    def test_prepared_but_never_submitted_intent_cannot_adopt_venue_order(self):
        payload = build_order_payload(self.request(), market())
        self.ledger.prepare_intent(
            "prepared-only", "PLACE", "market-one", "client-prepared",
            payload, created_at=NOW)
        raw = {
            "id": "unrelated-exchange", "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY", "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.500", "currency": "USD"},
            "quantity": "1.00", "cumQuantity": "0",
            "state": "ORDER_STATE_NEW", "createTime": NOW.isoformat(),
        }
        PrivateLedgerProjector(self.ledger).apply(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [raw], "eof": True}, "prepared-snapshot"),
            received_at=NOW)
        row = self.ledger.query("SELECT * FROM execution_orders")[0]
        self.assertEqual(row["client_order_id"], "venue:unrelated-exchange")
        self.assertEqual(
            self.ledger.unresolved_intents()[0]["state"], "PREPARED")
        self.assertFalse(self.ledger.risk_state_known())

    def test_mismatched_order_does_not_resolve_timed_out_create(self):
        self.client.orders.create_results = [TimeoutError("lost response")]
        with self.assertRaises(OrderOutcomeUncertain):
            self.run_async(self.executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-timeout", client_order_id="client-timeout"))
        raw = {
            "id": "other-exchange", "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY", "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.510", "currency": "USD"},
            "quantity": "1.00", "cumQuantity": "0",
            "state": "ORDER_STATE_NEW", "createTime": NOW.isoformat(),
        }
        PrivateLedgerProjector(self.ledger).apply(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [raw], "eof": True}, "mismatch-snapshot"),
            received_at=NOW)
        self.assertEqual(self.ledger.unresolved_intents()[0]["state"], "UNCERTAIN")
        self.assertEqual(
            self.ledger.query("SELECT client_order_id FROM execution_orders")[0][0],
            "venue:other-exchange")
        self.assertFalse(self.ledger.risk_state_known())

    def test_cancel_ack_is_not_terminal_until_confirmed(self):
        self.ledger.prepare_intent(
            "place", "PLACE", "market-one", "client-one", {}, created_at=NOW)
        self.ledger.transition_intent("place", "SUBMITTING", updated_at=NOW)
        self.ledger.transition_intent(
            "place", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="exchange-one")
        self.ledger.apply_order_event(
            "private_ws", "new", order_state(), received_at=NOW)
        cancel_id = self.run_async(self.executor.cancel(
            client_order_id="client-one", exchange_order_id="exchange-one",
            market_slug="market-one", now=NOW, intent_id="cancel-one"))
        self.assertEqual(cancel_id, "cancel-one")
        self.assertEqual(self.ledger.unresolved_intents()[0]["state"], "ACKNOWLEDGED")
        self.ledger.apply_order_event(
            "private_ws", "canceled", order_state("CANCELED"), received_at=NOW)
        self.assertEqual(self.ledger.unresolved_intents(), [])

    def test_fill_winning_cancel_race_is_terminal_and_preserves_inventory(self):
        self.ledger.prepare_intent(
            "place", "PLACE", "market-one", "client-one", {}, created_at=NOW)
        self.ledger.transition_intent("place", "SUBMITTING", updated_at=NOW)
        self.ledger.transition_intent(
            "place", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="exchange-one")
        self.ledger.apply_order_event(
            "private_ws", "new", order_state(), received_at=NOW)
        self.run_async(self.executor.cancel(
            client_order_id="client-one", exchange_order_id="exchange-one",
            market_slug="market-one", now=NOW, intent_id="cancel-race"))

        filled = OrderState(
            client_order_id="client-one", exchange_order_id="exchange-one",
            market_slug="market-one", side="ORDER_SIDE_BUY",
            price=Decimal("0.500"), original_qty=Decimal("1.00"),
            filled_qty=Decimal("1.00"), status="FILLED", event_at=NOW)
        self.ledger.apply_order_event(
            "private_ws", "filled-before-cancel", filled, received_at=NOW)
        self.assertEqual(self.ledger.unresolved_intents(), [])
        self.assertEqual(self.ledger.working_orders("market-one"), [])

    def test_unowned_working_order_blocks_new_risk(self):
        unknown = OrderState(
            client_order_id="venue:someone-else",
            exchange_order_id="someone-else",
            market_slug="market-one", side="ORDER_SIDE_BUY",
            price=Decimal("0.50"), original_qty=Decimal("1"),
            filled_qty=Decimal("0"), status="OPEN", event_at=NOW)
        self.ledger.apply_order_event("snapshot", "unknown", unknown, received_at=NOW)
        self.assertFalse(self.ledger.risk_state_known())
        self.client.orders.create_results = [{"id": "should-not-send"}]
        with self.assertRaises(OrderOutcomeUncertain):
            self.run_async(self.executor.place(self.request(), market(), now=NOW))
        self.assertEqual(self.client.orders.create_calls, [])


if __name__ == "__main__":
    unittest.main()
