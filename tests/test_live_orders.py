from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import ExecutionLedger, OrderState
from liquidity_bot.live_orders import (
    AsyncTradingGateway,
    LiveOrderExecutor,
    OrderMutationRejected,
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


class ResponseError(RuntimeError):
    def __init__(self, message, *, status_code, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


class Orders:
    def __init__(self):
        self.create_results = []
        self.retrieve_result = None
        self.create_calls = []
        self.cancel_calls = []
        self.retrieve_calls = []
        self.retrieve_errors = []

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
        if self.retrieve_errors:
            raise self.retrieve_errors.pop(0)
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

    def test_reducing_uses_a_buy_intent_because_sell_inverts_the_price(self):
        """Confirmed live 2026-08-14, and the reason for the two-intent map.

        Holding a short of 5 entered at 0.36, the reducing quote went out as
        SELL_SHORT at 0.36 and the venue rested it at **0.64** -- 1 - 0.36 --
        where it adds to the short instead of closing it. maker-lab recorded
        the same inversion in their `to_intent` docstring and resolved it by
        sending only BUY intents with the price unchanged.

        The venue nets a BUY on the opposite outcome against an open position,
        so reduction needs no separate intent.
        """

        payload = build_order_payload(OrderRequest(
            "market-one", "offer", Decimal("0.495"), Decimal("1.00"),
            reducing=True,
            time_in_force="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            post_only=False), market())
        self.assertEqual(payload["intent"], "ORDER_INTENT_BUY_SHORT")
        # The price is sent unchanged, in the frame we chose it in. The
        # inversion was the venue reinterpreting it, not us rewriting it.
        self.assertEqual(payload["price"]["value"], "0.495")
        self.assertEqual(
            payload["tif"], "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
        self.assertFalse(payload["participateDontInitiate"])

    def test_reducing_and_building_send_the_same_intent_per_side(self):
        """The flag must not change the intent -- that was the whole bug."""

        for side, expected in (("bid", "ORDER_INTENT_BUY_LONG"),
                               ("offer", "ORDER_INTENT_BUY_SHORT")):
            building = build_order_payload(OrderRequest(
                "market-one", side, Decimal("0.50"), Decimal("1.00")), market())
            reducing = build_order_payload(OrderRequest(
                "market-one", side, Decimal("0.50"), Decimal("1.00"),
                reducing=True), market())
            self.assertEqual(building["intent"], expected)
            self.assertEqual(reducing["intent"], expected)

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

    def test_retrieve_waits_out_the_post_cancel_not_found_window(self):
        # Measured 2026-08-14: a cancelled order is briefly unreadable, 246
        # times in 5.7 hours, and every id that 404'd resolved to a proper
        # terminal state later. Escalating on the first 404 left the intent
        # unresolved, which quarantined every working order and cancelled
        # resting quotes -- and resting is the only thing that earns.
        class NotFoundError(Exception):
            pass

        self.client.orders.retrieve_errors = [
            NotFoundError("Order not found"),
            NotFoundError("Order not found"),
        ]
        self.client.orders.retrieve_result = {
            "order": {"id": "exchange-one", "state": "ORDER_STATE_CANCELED"}}

        response = self.run_async(self.gateway.retrieve("exchange-one"))

        self.assertEqual(response["order"]["state"], "ORDER_STATE_CANCELED")
        self.assertEqual(len(self.client.orders.retrieve_calls), 3)

    def test_retrieve_still_fails_when_the_order_never_appears(self):
        class NotFoundError(Exception):
            pass

        # Bounded, not infinite: a genuinely absent order must still surface.
        self.client.orders.retrieve_errors = [
            NotFoundError("Order not found") for _ in range(10)]

        with self.assertRaisesRegex(AdapterError, "NotFound"):
            self.run_async(self.gateway.retrieve("exchange-one"))
        self.assertEqual(
            len(self.client.orders.retrieve_calls),
            self.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS)

    def test_retrieve_retries_a_404_whose_wording_changed(self):
        # Classification follows the structured status, so a renamed SDK
        # exception or reworded message cannot silently end the wait.
        class GoneError(Exception):
            status_code = 404

        self.client.orders.retrieve_errors = [GoneError("resource gone")]
        self.client.orders.retrieve_result = {
            "order": {"id": "exchange-one", "state": "ORDER_STATE_CANCELED"}}

        response = self.run_async(self.gateway.retrieve("exchange-one"))

        self.assertEqual(response["order"]["state"], "ORDER_STATE_CANCELED")
        self.assertEqual(len(self.client.orders.retrieve_calls), 2)

    def test_retrieve_does_not_retry_other_failures(self):
        class ServerError(Exception):
            status_code = 500

        self.client.orders.retrieve_errors = [ServerError("upstream failed")]

        with self.assertRaises(AdapterError):
            self.run_async(self.gateway.retrieve("exchange-one"))
        self.assertEqual(len(self.client.orders.retrieve_calls), 1)

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

    def test_real_limiter_records_mutation_without_retrying_it(self):
        limiter = AsyncIntervalLimiter()
        gateway = AsyncTradingGateway(AsyncReadOnlyGateway(
            self.client, limiter=limiter))
        self.client.orders.create_results = [
            {"id": "exchange-one", "executions": []}]

        self.run_async(gateway.create({"marketSlug": "market-one"}))
        evidence = limiter.telemetry()

        self.assertEqual(evidence.attempts, 1)
        self.assertEqual(evidence.read_attempts, 0)
        self.assertEqual(evidence.mutation_attempts, 1)
        self.assertEqual(
            evidence.attempts_by_category, (("mutation_create", 1),))

    def test_real_limiter_attributes_failed_mutation_category_and_status(self):
        limiter = AsyncIntervalLimiter()
        gateway = AsyncTradingGateway(AsyncReadOnlyGateway(
            self.client, limiter=limiter))
        self.client.orders.create_results = [ResponseError(
            "venue unavailable", status_code=503)]

        with self.assertRaises(AdapterError):
            self.run_async(gateway.create({"marketSlug": "market-one"}))

        evidence = limiter.telemetry()
        self.assertEqual(evidence.failed_responses, 1)
        self.assertEqual(
            evidence.failed_responses_by_category_status,
            (("mutation_create@503", 1),),
        )

    def test_reducers_and_ioc_exits_keep_safety_priority(self):
        class RecordingLimiter(AsyncIntervalLimiter):
            def __init__(self):
                super().__init__()
                self.classes = []

            async def wait(self, **kwargs):
                self.classes.append(kwargs.get("traffic_class"))

        limiter = RecordingLimiter()
        gateway = AsyncTradingGateway(AsyncReadOnlyGateway(
            self.client, limiter=limiter))
        self.client.orders.create_results = [
            {"id": "entry", "executions": []},
            {"id": "reducer", "executions": []},
            {"id": "ioc", "executions": []},
        ]

        self.run_async(gateway.create({
            "marketSlug": "market-one",
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        }))
        self.run_async(gateway.create({
            "marketSlug": "market-one",
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        }, reducing=True))
        self.run_async(gateway.create({
            "marketSlug": "market-one",
            "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
        }))

        self.assertEqual(limiter.classes, ["entry", "safety", "safety"])

    def test_executor_carries_reducing_priority_outside_wire_payload(self):
        class RecordingLimiter(AsyncIntervalLimiter):
            def __init__(self):
                super().__init__()
                self.classes = []

            async def wait(self, **kwargs):
                self.classes.append(kwargs.get("traffic_class"))

        limiter = RecordingLimiter()
        executor = LiveOrderExecutor(AsyncTradingGateway(AsyncReadOnlyGateway(
            self.client, limiter=limiter)), self.ledger)
        self.client.orders.create_results = [
            {"id": "passive-reducer", "executions": []}]
        request = OrderRequest(
            "market-one", "offer", Decimal("0.495"), Decimal("1.00"),
            reducing=True)

        self.run_async(executor.place(
            request, market(), now=NOW,
            intent_id="intent-reducer", client_order_id="client-reducer"))

        self.assertEqual(limiter.classes, ["safety"])
        self.assertNotIn("reducing", self.client.orders.create_calls[0])

    def test_entry_cooldown_fails_intent_without_claiming_remote_uncertainty(self):
        limiter = AsyncIntervalLimiter()
        executor = LiveOrderExecutor(AsyncTradingGateway(AsyncReadOnlyGateway(
            self.client, limiter=limiter)), self.ledger)
        self.run_async(limiter.record_result(
            duration_seconds=0.1, status_code=429, failed=True,
            retry_after_seconds=30.0, traffic_class="entry"))

        with self.assertRaises(ApiAdmissionDeferred):
            self.run_async(executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-deferred", client_order_id="client-deferred"))

        self.assertEqual(self.client.orders.create_calls, [])
        self.assertEqual(self.ledger.unresolved_intents(), [])
        row = self.ledger.query(
            "SELECT state,error FROM execution_intents WHERE intent_id=?",
            ("intent-deferred",))[0]
        self.assertEqual(row["state"], "FAILED")
        self.assertIn("ApiAdmissionDeferred", row["error"])

    def test_stop_interrupt_fails_waiting_cancel_without_network_or_ambiguity(self):
        limiter = AsyncIntervalLimiter()
        executor = LiveOrderExecutor(AsyncTradingGateway(AsyncReadOnlyGateway(
            self.client, limiter=limiter)), self.ledger)
        self.run_async(limiter.record_result(
            duration_seconds=0.1, status_code=429, failed=True,
            retry_after_seconds=60.0, traffic_class="safety"))

        async def exercise():
            cancelling = asyncio.create_task(executor.cancel(
                client_order_id="client-one",
                exchange_order_id="exchange-one",
                market_slug="market-one",
                now=NOW,
                intent_id="cancel-interrupted"))
            await asyncio.sleep(0.01)
            limiter.interrupt_waits()
            with self.assertRaises(ApiAdmissionDeferred) as raised:
                await cancelling
            self.assertEqual(
                raised.exception.error_code, "LOCAL_API_INTERRUPTED")

        self.run_async(exercise())
        self.assertEqual(self.client.orders.cancel_calls, [])
        self.assertEqual(self.ledger.unresolved_intents(), [])
        row = self.ledger.query(
            "SELECT state,error FROM execution_intents WHERE intent_id=?",
            ("cancel-interrupted",))[0]
        self.assertEqual(row["state"], "FAILED")
        self.assertIn("request not sent", row["error"])

    def test_same_market_pending_place_reports_wait_without_network(self):
        """The specific wait path must be reachable before the state gate."""

        self.ledger.prepare_intent(
            "pending-place", "PLACE", "market-one", "client-pending", {},
            created_at=NOW)
        self.ledger.transition_intent(
            "pending-place", "SUBMITTING", updated_at=NOW)
        self.ledger.transition_intent(
            "pending-place", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="exchange-pending")

        with self.assertRaisesRegex(
                OrderOutcomeUncertain,
                "market has an unresolved order intent"):
            self.run_async(self.executor.place(self.request(), market(), now=NOW))

        self.assertEqual(self.client.orders.create_calls, [])

    def test_other_market_pending_place_does_not_block_order_entry(self):
        """One market's confirmation window must not blank its neighbours."""

        self.ledger.prepare_intent(
            "pending-place", "PLACE", "market-one", "client-pending", {},
            created_at=NOW)
        self.ledger.transition_intent(
            "pending-place", "SUBMITTING", updated_at=NOW)
        self.ledger.transition_intent(
            "pending-place", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="exchange-pending")
        self.client.orders.create_results = [
            {"id": "exchange-two", "executions": []}]
        request = OrderRequest(
            "market-two", "bid", Decimal("0.500"), Decimal("1.00"))

        submitted = self.run_async(self.executor.place(
            request, market(slug="market-two"), now=NOW,
            intent_id="intent-two", client_order_id="client-two"))

        self.assertEqual(submitted.exchange_order_id, "exchange-two")
        self.assertEqual(len(self.client.orders.create_calls), 1)

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
        self.assertEqual(row["quote_observation_required"], 1)
        self.assertEqual(self.ledger.query(
            "SELECT * FROM execution_quote_observations"), [])
        self.assertTrue(self.ledger.market_has_unresolved_intent("market-one"))
        self.assertFalse(self.ledger.risk_state_known())

    def test_response_backed_403_fails_create_without_unresolved_intent(self):
        self.client.orders.create_results = [ResponseError(
            "location permission denied",
            status_code=403,
            body={"code": 7},
        )]

        with self.assertRaises(OrderMutationRejected) as raised:
            self.run_async(self.executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-denied", client_order_id="client-denied"))

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.error_code, "7")
        self.assertEqual(len(self.client.orders.create_calls), 1)
        self.assertEqual(self.ledger.unresolved_intents(), [])
        row = self.ledger.query(
            "SELECT state,error FROM execution_intents WHERE intent_id=?",
            ("intent-denied",))[0]
        self.assertEqual(row["state"], "FAILED")
        self.assertIn("OrderMutationRejected", row["error"])

    def test_response_backed_403_fails_cancel_without_inferring_terminal_order(self):
        self.client.orders.create_results = [AdapterError(
            "location permission denied",
            operation="POST /v1/order/exchange-one/cancel",
            status_code=403,
            error_code="7",
        )]

        with self.assertRaises(OrderMutationRejected):
            self.run_async(self.executor.cancel(
                client_order_id="client-one",
                exchange_order_id="exchange-one",
                market_slug="market-one",
                now=NOW,
                intent_id="cancel-denied"))

        self.assertEqual(len(self.client.orders.cancel_calls), 1)
        self.assertEqual(self.ledger.unresolved_intents(), [])
        row = self.ledger.query(
            "SELECT state,exchange_order_id FROM execution_intents "
            "WHERE intent_id=?", ("cancel-denied",))[0]
        self.assertEqual(row["state"], "FAILED")
        self.assertEqual(row["exchange_order_id"], "exchange-one")

    def test_exact_legacy_permission_denial_is_repaired_but_near_match_is_not(self):
        for intent_id, error in (
            ("legacy-denied",
             "AdapterError: POST /v1/orders outcome uncertain: "
             "PermissionDeniedError: VPN (status=403, code=7)"),
            ("legacy-timeout",
             "AdapterError: POST /v1/orders outcome uncertain: "
             "TimeoutError (status=403, code=7)"),
        ):
            self.ledger.prepare_intent(
                intent_id, "PLACE", "market-one", f"client-{intent_id}", {},
                created_at=NOW)
            self.ledger.transition_intent(
                intent_id, "SUBMITTING", updated_at=NOW)
            self.ledger.transition_intent(
                intent_id, "UNCERTAIN", updated_at=NOW, error=error)

        repaired = self.executor.resolve_recorded_permission_denials(now=NOW)

        self.assertEqual(repaired, ("legacy-denied",))
        states = {row["intent_id"]: row["state"] for row in self.ledger.query(
            "SELECT intent_id,state FROM execution_intents")}
        self.assertEqual(states["legacy-denied"], "FAILED")
        self.assertEqual(states["legacy-timeout"], "UNCERTAIN")

    def test_missing_create_id_is_uncertain(self):
        self.client.orders.create_results = [{"executions": []}]
        with self.assertRaises(OrderOutcomeUncertain):
            self.run_async(self.executor.place(
                self.request(), market(), now=NOW,
                intent_id="intent-no-id", client_order_id="client-no-id"))
        self.assertEqual(self.ledger.unresolved_intents()[0]["state"], "UNCERTAIN")

    def test_building_observation_reaches_intent_but_never_the_wire(self):
        observation = object()
        request = replace(self.request(), quote_observation=observation)
        self.client.orders.create_results = [
            {"id": "exchange-observed", "executions": []}]
        with patch.object(self.ledger, "prepare_intent") as prepare, patch.object(
                self.ledger, "transition_intent"):
            self.run_async(self.executor.place(
                request, market(), now=NOW,
                intent_id="intent-observed", client_order_id="client-observed"))

        self.assertIs(prepare.call_args.kwargs["quote_observation"], observation)
        self.assertTrue(
            prepare.call_args.kwargs["quote_observation_required"])
        wire = self.client.orders.create_calls[0]
        self.assertNotIn("quote_observation", wire)
        self.assertNotIn("clientOrderId", wire)

    def test_reducing_request_cannot_persist_building_observation(self):
        request = replace(
            self.request(), reducing=True, quote_observation=object())
        self.client.orders.create_results = [
            {"id": "exchange-reducing", "executions": []}]
        with patch.object(self.ledger, "prepare_intent") as prepare, patch.object(
                self.ledger, "transition_intent"):
            self.run_async(self.executor.place(
                request, market(), now=NOW,
                intent_id="intent-reducing", client_order_id="client-reducing"))

        self.assertIsNone(prepare.call_args.kwargs["quote_observation"])
        self.assertFalse(
            prepare.call_args.kwargs["quote_observation_required"])

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
