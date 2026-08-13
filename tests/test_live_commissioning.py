from __future__ import annotations

import asyncio
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from liquidity_bot.commissioning import CommissioningConfig, CommissioningRefused
from liquidity_bot.errors import AdapterError, RuntimeInvariantViolation
from liquidity_bot.execution_ledger import ExecutionLedger, OrderState
from liquidity_bot.live_commissioning import (
    CommissioningResult,
    CommissioningRunner,
    _run,
    main,
)
from liquidity_bot.live_orders import OrderOutcomeUncertain, OrderRequest
from liquidity_bot.notifications import NotificationResult
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.reconciler import ActionKind, PlanAction


NOW = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
GAME_START = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


class ImmediateLimiter:
    def __init__(self):
        self.calls = 0

    async def wait(self):
        self.calls += 1


class FakeMarkets:
    def __init__(self):
        self.book_calls = 0

    async def retrieve_by_slug(self, slug):
        return {"market": {
            "slug": slug,
            "active": True,
            "closed": False,
            "status": "MARKET_STATE_OPEN",
            "gameStartTime": GAME_START.isoformat(),
            "orderPriceMinTickSize": {"value": "0.01", "currency": "USD"},
            "minimumTradeQty": "1",
            "sportsMarketType": "moneyline",
            "question": "Fake commissioning market?",
        }}

    async def book(self, slug):
        self.book_calls += 1
        return {"marketData": {
            "marketSlug": slug,
            "state": "MARKET_STATE_OPEN",
            "bids": [
                {"px": "0.49", "qty": "100"},
                {"px": "0.48", "qty": "100"},
            ],
            "offers": [
                {"px": "0.51", "qty": "100"},
                {"px": "0.52", "qty": "100"},
            ],
            "transactTime": NOW.isoformat(),
        }}


class FakeOrders:
    def __init__(self):
        self.create_calls = []
        self.cancel_calls = []
        self.rows = {}

    async def list(self, params):
        active = {
            "ORDER_STATE_OPEN", "ORDER_STATE_NEW",
            "ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_PENDING",
        }
        return {"orders": [row for row in self.rows.values()
                           if row["state"] in active]}

    async def create(self, payload):
        self.create_calls.append(payload)
        return {"id": f"fake-order-{len(self.create_calls)}", "executions": []}

    async def cancel(self, order_id, payload):
        self.cancel_calls.append((order_id, payload))
        self.rows[order_id]["state"] = "ORDER_STATE_CANCELED"
        self.rows[order_id]["lastTransactTime"] = (
            datetime(2026, 8, 13, 5, 0, 1, tzinfo=timezone.utc).isoformat())

    async def retrieve(self, order_id):
        return {"order": self.rows[order_id]}


class FakePortfolio:
    def __init__(self):
        self.rows = {}

    async def positions(self, params):
        return {"positions": self.rows, "eof": True}


class FakeAccount:
    def __init__(self):
        self.current_balance = Decimal("100")
        self.asset_notional = Decimal("0")

    async def balances(self):
        return {"balances": [{
            "currency": "USD",
            "currentBalance": str(self.current_balance),
            "assetNotional": str(self.asset_notional),
            "buyingPower": str(self.current_balance),
        }]}


class FakeClient:
    def __init__(self):
        self.markets = FakeMarkets()
        self.orders = FakeOrders()
        self.portfolio = FakePortfolio()
        self.account = FakeAccount()

    async def get(self, path, *, query=None, authenticated=False):
        if path != "/v1/incentives":
            raise AssertionError(f"unexpected GET {path}")
        return {"programs": [{
            "marketSlug": "market-one",
            "eventStartTime": GAME_START.isoformat(),
            "timePeriods": [{
                "programId": "fake-early-program",
                "programType": "liquidityProgram",
                "period": "early",
                "status": "active",
                "rewardPool": 25,
                "discountFactor": 0.5,
                "targetSize": 10,
                "start": None,
                "end": None,
            }],
        }]}


class FakePrivateStream:
    def __init__(self, _client):
        self.events = [PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
            {"balances": []}, "fake-balance")]
        self.started = False
        self.started_with = None
        self.closed = False

    async def start(self, slugs=None):
        self.started = True
        self.started_with = slugs

    async def next_event(self, *, timeout=None):
        if self.events:
            return self.events.pop(0)
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FakeMarketStream:
    def __init__(self, _client):
        self.started = False
        self.closed = False

    async def start(self, slugs):
        self.started = True

    async def next_book(self, *, timeout=None):
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FailingPrivateStream(FakePrivateStream):
    async def start(self, slugs=None):
        self.started_with = slugs
        raise AdapterError("simulated private reconnect failure")


class FakeNotifier:
    def __init__(self, *, delivery_succeeds=True):
        self.messages = []
        self.payloads = []
        self.delivery_succeeds = delivery_succeeds

    async def send_async(self, content, *, critical=False):
        self.messages.append((content, critical))
        return NotificationResult(
            self.delivery_succeeds,
            "sent" if self.delivery_succeeds else "delivery_failed")

    async def send_payload_async(self, payload, *, critical=False):
        self.payloads.append((payload, critical))
        return NotificationResult(
            self.delivery_succeeds,
            "sent" if self.delivery_succeeds else "delivery_failed")


class TickingClock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        result = self.value
        self.value += timedelta(seconds=1)
        return result


class LiveCommissioningTests(unittest.IsolatedAsyncioTestCase):
    def build_runner(self, *, clock=lambda: NOW, notifier=None):
        root = Path(tempfile.mkdtemp(prefix="liquidity-commissioning-"))
        ledger = ExecutionLedger(root / "ledger.db")
        self.addCleanup(ledger.close)
        client = FakeClient()
        notifier = notifier or FakeNotifier()
        config = CommissioningConfig(
            market_slug="market-one",
            economic_risk_accepted=True,
            exclusive_api_key_confirmed=True,
            maker_lab_stopped_confirmed=True,
            arm_live=True,
        )
        runner = CommissioningRunner(
            client, config, ledger, notifier,
            clock=clock,
            sleep=lambda _seconds: asyncio.sleep(0),
            market_stream_factory=FakeMarketStream,
            private_stream_factory=FakePrivateStream,
        )
        runner.gateway.limiter = ImmediateLimiter()
        return runner, client, notifier

    async def test_preflight_then_first_fake_order_is_exactly_one_contract(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(runner.gate.ready(runner.ledger, now=NOW))
        self.assertEqual(runner.private_stream.started_with, ["market-one"])

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))
        self.assertEqual({action.side for action in actions}, {"bid", "offer"})
        self.assertTrue(all(action.qty == Decimal("1") for action in actions))
        await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal("0"), now=NOW)

        self.assertEqual(len(client.orders.create_calls), 1)
        payload = client.orders.create_calls[0]
        self.assertEqual(payload["quantity"], 1)
        self.assertTrue(payload["participateDontInitiate"])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_operator_stop_exits_flat_without_placing_an_order(self):
        runner, client, notifier = self.build_runner()
        runner.request_stop("operator_requested")
        result = await runner.run()

        self.assertEqual(result.stop_reason, "operator_requested")
        self.assertIsNone(result.cutoff_at)
        self.assertTrue(result.flat)
        self.assertEqual(result.orders_sent_to_polymarket, 0)
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(runner.private_stream.closed)
        self.assertTrue(runner.market_stream.closed)
        self.assertGreaterEqual(len(notifier.messages), 3)

    async def test_fifteen_dollar_drawdown_is_named_hard_stop(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        client.account.current_balance = Decimal("85")
        await runner._refresh_balance(NOW)
        self.assertEqual(runner._stop_reason(NOW), "15_USD_HARD_STOP")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_restart_recovers_identified_owned_order_without_new_risk(self):
        runner, client, notifier = self.build_runner(clock=TickingClock())
        payload = {
            "marketSlug": "market-one",
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": 1,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": False,
        }
        runner.ledger.prepare_intent(
            "prior-place", "PLACE", "market-one", "prior-client", payload,
            created_at=NOW)
        runner.ledger.transition_intent(
            "prior-place", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "prior-place", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="prior-order")
        client.orders.rows["prior-order"] = {
            "id": "prior-order",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        result = await runner.run()

        self.assertEqual(result.stop_reason, "startup_recovery_only")
        self.assertTrue(result.flat)
        self.assertEqual(result.orders_sent_to_polymarket, 0)
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(len(client.orders.cancel_calls), 1)
        self.assertTrue(any(critical for _, critical in notifier.messages))

    async def test_unowned_exchange_order_refuses_and_never_mutates(self):
        runner, client, _ = self.build_runner()
        client.orders.rows["external-order"] = {
            "id": "external-order",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        with self.assertRaisesRegex(
                CommissioningRefused, "open orders|ownership"):
            await runner.run()

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        self.assertTrue(runner.private_stream.closed)
        self.assertTrue(runner.market_stream.closed)

    async def test_failed_startup_discord_delivery_blocks_first_order(self):
        notifier = FakeNotifier(delivery_succeeds=False)
        runner, client, _ = self.build_runner(notifier=notifier)

        with self.assertRaisesRegex(
                CommissioningRefused, "Discord startup alert delivery failed"):
            await runner.run()

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])

    async def test_reconciliation_can_clear_last_unresolved_intent_mid_pass(self):
        runner, _, _ = self.build_runner()
        self.assertIsNone(runner._oldest_unresolved_age_seconds(NOW))
        runner.ledger.prepare_intent(
            "temporary", "PLACE", "market-one", "temporary-client",
            {"marketSlug": "market-one"}, created_at=NOW)
        self.assertEqual(runner._oldest_unresolved_age_seconds(NOW), 0)
        runner.ledger.transition_intent(
            "temporary", "FAILED", updated_at=NOW)
        self.assertIsNone(runner._oldest_unresolved_age_seconds(NOW))

    async def test_quiet_change_driven_book_refreshes_before_stale_limit(self):
        runner, _, _ = self.build_runner()
        runner.book_observed_at = NOW
        self.assertFalse(runner._quiet_book_needs_rest_refresh(
            NOW + timedelta(seconds=2)))
        self.assertTrue(runner._quiet_book_needs_rest_refresh(
            NOW + timedelta(seconds=3)))
        self.assertTrue(runner._quiet_book_needs_rest_refresh(
            NOW - timedelta(seconds=1)))

    async def test_rest_book_timestamp_is_response_receipt_not_request_start(self):
        received = NOW + timedelta(seconds=4)
        runner, _, _ = self.build_runner(clock=lambda: received)
        runner.market = await runner.gateway.fetch_market("market-one")
        await runner._refresh_book(NOW)
        self.assertEqual(runner.book_observed_at, received)

    async def test_book_due_check_reads_clock_after_upstream_api_latency(self):
        after_slow_io = NOW + timedelta(seconds=6)
        runner, client, _ = self.build_runner(clock=lambda: after_slow_io)
        runner.market = await runner.gateway.fetch_market("market-one")
        runner.book_observed_at = NOW
        next_refresh = await runner._refresh_quiet_book_if_due(
            NOW + timedelta(seconds=2))
        self.assertEqual(client.markets.book_calls, 1)
        self.assertEqual(runner.book_observed_at, after_slow_io)
        self.assertEqual(next_refresh, after_slow_io + timedelta(seconds=3))

    async def test_recent_ws_book_anchors_next_check_to_its_receipt(self):
        check_at = NOW + timedelta(seconds=4)
        runner, client, _ = self.build_runner(clock=lambda: check_at)
        runner.book_observed_at = NOW + timedelta(seconds=2)
        next_refresh = await runner._refresh_quiet_book_if_due(
            NOW + timedelta(seconds=3))
        self.assertEqual(client.markets.book_calls, 0)
        self.assertEqual(next_refresh, NOW + timedelta(seconds=5))

    async def test_periodic_rest_reconcile_backstops_quiet_private_stream(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        later = NOW + timedelta(seconds=31)
        self.assertFalse(runner.gate.ready(runner.ledger, now=later))
        await runner._rest_reconcile(later)
        self.assertTrue(runner.gate.ready(runner.ledger, now=later))
        self.assertEqual(runner.gate.last_message_at, NOW)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_private_adapter_error_recovers_without_placing_new_orders(self):
        runner, client, notifier = self.build_runner()
        await runner.preflight()
        old_stream = runner.private_stream

        stop = await runner._recover_private_stream(
            AdapterError("position market slug is missing"))

        self.assertIsNone(stop)
        self.assertTrue(old_stream.closed)
        self.assertIsNot(runner.private_stream, old_stream)
        self.assertEqual(runner.private_stream.started_with, ["market-one"])
        self.assertTrue(runner.gate.ready(runner.ledger, now=NOW))
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(critical for _, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_market_adapter_error_recovers_from_fresh_book_without_new_orders(self):
        runner, client, notifier = self.build_runner()
        await runner.preflight()
        old_stream = runner.market_stream
        recovered_book = runner.book

        class RecoveringMarketStream(FakeMarketStream):
            async def next_book(self, *, timeout=None):
                return recovered_book

        runner.market_stream_factory = RecoveringMarketStream
        stop = await runner._recover_market_stream(
            AdapterError("market WebSocket disconnected"))

        self.assertIsNone(stop)
        self.assertTrue(old_stream.closed)
        self.assertIsNot(runner.market_stream, old_stream)
        self.assertIs(runner.book, recovered_book)
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(critical for _, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_repeated_private_reconnect_failure_stays_alive_until_operator_stop(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.private_stream_factory = FailingPrivateStream
        sleeps = 0

        async def stop_after_retry(_seconds):
            nonlocal sleeps
            sleeps += 1
            runner.request_stop("operator_requested")

        runner.sleep = stop_after_retry
        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertEqual(stop, "operator_requested")
        self.assertEqual(sleeps, 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_hard_stop_remains_terminal_during_stream_recovery(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        client.account.current_balance = Decimal("85")

        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertEqual(stop, "15_USD_HARD_STOP")
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_stale_equity_stays_alive_but_cannot_add_new_risk(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._evaluate_rewards(NOW)
        later = NOW + timedelta(seconds=16)
        runner.book_observed_at = later

        self.assertIsNone(runner._stop_reason(later))
        frame = runner._frame(later)
        self.assertEqual(frame.reward_eligible, frozenset())
        self.assertEqual(runner.reconciler.plan(frame), [])
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_pending_hard_exit_confirmation_flat_is_recoverable(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        runner.reconciler._hard_exit_sent[slug] = NOW

        outcome = await runner._reconcile_pending_hard_exit()

        self.assertEqual(outcome, "flat")
        self.assertNotIn(slug, runner.reconciler._hard_exit_sent)
        self.assertIsNone(runner._stop_reason(runner._now()))
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_terminal_unfilled_hard_exit_can_retry_without_process_stop(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        runner.reconciler._hard_exit_sent[slug] = NOW
        client.portfolio.rows = {
            slug: {
                "netPosition": "1",
                "netPositionDecimal": "1.0000",
                "updateTime": NOW.isoformat(),
            }
        }

        outcome = await runner._reconcile_pending_hard_exit()

        self.assertEqual(outcome, "retry")
        self.assertEqual(runner.ledger.position(slug), Decimal("1.0000"))
        self.assertNotIn(slug, runner.reconciler._hard_exit_sent)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_confirmation_errors_escalate_at_the_deadline(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        slug = runner.config.market_slug
        runner.reconciler._hard_exit_sent[slug] = NOW
        runner.reconciler.plan = lambda _frame: [PlanAction(
            ActionKind.CRITICAL, slug,
            "hard_exit_sent_but_inventory_remains")]

        async def fail_confirmation():
            raise AdapterError("confirmation endpoint unavailable")

        runner._reconcile_pending_hard_exit = fail_confirmation

        result = await runner.run()

        self.assertEqual(
            result.stop_reason, "critical:hard_exit_confirmation_timeout")
        self.assertTrue(result.flat)
        self.assertEqual(client.orders.create_calls, [])

    async def test_unknown_strategy_critical_is_terminal_by_default(self):
        runner, client, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = await runner._handle_strategy_critical(
            "future_runtime_condition")

        self.assertEqual(stop, "critical:future_runtime_condition")
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(critical for _, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_only_named_transient_strategy_critical_recovers(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = await runner._handle_strategy_critical(
            "execution_state_unknown")

        self.assertIsNone(stop)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unexitable_inventory_conditions_are_terminal(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        for reason in (
            "hard_exit_insufficient_stressed_depth",
            "hard_exit_price_cap_exceeded",
            "inventory_basis_or_clock_unknown",
            "inventory_clock_in_future",
            "hard_exit_attempt_limit_exceeded",
            "hard_exit_retry_window_exceeded",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    await runner._handle_strategy_critical(reason),
                    f"critical:{reason}")

        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_runtime_unowned_order_is_terminal_without_mutation(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.ledger.apply_order_event(
            "ws", "runtime-unowned",
            OrderState(
                client_order_id="venue:external",
                exchange_order_id="external",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.50"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        with self.assertRaises(RuntimeInvariantViolation) as raised:
            runner._frame(runner._now())
        stop = await runner._handle_strategy_plan_failure(raised.exception)

        self.assertEqual(stop, "critical:unowned_working_order")
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_cross_market_action_is_terminal_without_mutation(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        action = PlanAction(
            ActionKind.PLACE_POST_ONLY, "other-market", "bad-market",
            side="bid", price=.49, qty=Decimal("1"))

        with self.assertRaises(RuntimeInvariantViolation) as raised:
            await runner.driver.apply_one(
                [action], market=runner.market, position=Decimal("0"),
                now=runner._now())
        stop = await runner._handle_action_failure(raised.exception)

        self.assertEqual(stop, "critical:action_market_mismatch")
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_adapter_action_failure_remains_recoverable(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = await runner._handle_action_failure(
            AdapterError("temporary order adapter failure"))

        self.assertIsNone(stop)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_does_not_submit_a_fourth_ioc(self):
        runner, client, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        self._hold_one_contract(client, slug)
        # Every shutdown IOC expires unfilled, so the position never clears and
        # the loop keeps asking for another exit until its budget is spent.
        self._expire_every_created_order(runner, client, slug)
        runner.config = replace(runner.config, max_shutdown_seconds=120)

        await runner._shutdown("critical:hard_exit_attempt_limit_exceeded")

        self.assertEqual(
            len(client.orders.create_calls),
            runner.config.max_shutdown_exit_attempts)
        self.assertTrue(any(
            "shutdown exit budget" in message and critical
            for message, critical in notifier.messages))
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_runtime_recovery_delay_is_exponential_and_capped(self):
        runner, _, _ = self.build_runner()
        runner._runtime_error_counts["action_driver"] = 1
        self.assertEqual(runner._recovery_delay("action_driver"), 1)
        runner._runtime_error_counts["action_driver"] = 4
        self.assertEqual(runner._recovery_delay("action_driver"), 8)
        runner._runtime_error_counts["action_driver"] = 20
        self.assertEqual(runner._recovery_delay("action_driver"), 30)
        await runner.private_stream.close()
        await runner.market_stream.close()

    def _hold_one_contract(self, client, slug):
        client.portfolio.rows = {slug: {
            "netPosition": "1",
            "netPositionDecimal": "1.0000",
            "updateTime": NOW.isoformat(),
        }}

    def _expire_every_created_order(self, runner, client, slug):
        """Accept each order at the venue and let it expire unfilled."""

        original_create = client.orders.create

        async def create_then_expire(payload):
            response = await original_create(payload)
            moment = runner._now().isoformat()
            client.orders.rows[response["id"]] = {
                "id": response["id"],
                "marketSlug": slug,
                "intent": payload["intent"],
                "state": "ORDER_STATE_EXPIRED",
                "side": "ORDER_SIDE_SELL",
                "price": payload["price"],
                "quantity": payload["quantity"],
                "cumQuantity": "0",
                "updateTime": moment,
                "lastTransactTime": moment,
            }
            return response

        client.orders.create = create_then_expire

    def _fill_first_created_order(self, runner, client, slug):
        """Accept the first shutdown IOC and expose terminal flat venue state."""

        original_create = client.orders.create

        async def create_then_fill(payload):
            response = await original_create(payload)
            moment = runner._now().isoformat()
            client.orders.rows[response["id"]] = {
                "id": response["id"],
                "marketSlug": slug,
                "intent": payload["intent"],
                "state": "ORDER_STATE_FILLED",
                "side": "ORDER_SIDE_SELL",
                "price": payload["price"],
                "quantity": payload["quantity"],
                "cumQuantity": payload["quantity"],
                "updateTime": moment,
                "lastTransactTime": moment,
            }
            client.portfolio.rows = {}
            return response

        client.orders.create = create_then_fill

    async def test_shutdown_flattens_on_its_own_budget_after_runtime_ceiling(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        # The quoting loop already spent its ceiling before going terminal.
        runner.reconciler._hard_exit_attempts[slug] = (
            runner.reconciler.policy.max_hard_exit_attempts)
        self._hold_one_contract(client, slug)
        self._fill_first_created_order(runner, client, slug)
        runner.config = replace(runner.config, max_shutdown_seconds=30)

        await runner._shutdown("critical:hard_exit_attempt_limit_exceeded")

        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(
            client.orders.create_calls[0]["tif"],
            "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
        self.assertTrue(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_stuck_shutdown_repeats_its_critical_alert(self):
        notifier = FakeNotifier()
        runner, client, _ = self.build_runner(
            clock=TickingClock(), notifier=notifier)
        await runner.preflight()
        slug = runner.config.market_slug
        self._hold_one_contract(client, slug)
        runner.config = replace(
            runner.config, max_shutdown_exit_attempts=0,
            shutdown_alert_interval_seconds=1, max_shutdown_seconds=30)

        await runner._shutdown("critical:hard_exit_insufficient_stressed_depth")

        stuck = [message for message, critical in notifier.messages
                 if critical and "shutdown exit budget" in message]
        self.assertGreater(len(stuck), 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_nonthrowing_unowned_shutdown_state_repeats_alerts(self):
        notifier = FakeNotifier()
        runner, client, _ = self.build_runner(
            clock=TickingClock(), notifier=notifier)
        await runner.preflight()
        slug = runner.config.market_slug
        client.orders.rows["external"] = {
            "id": "external",
            "marketSlug": slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "state": "ORDER_STATE_NEW",
            "side": "ORDER_SIDE_BUY",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "updateTime": NOW.isoformat(),
        }
        runner.config = replace(
            runner.config, shutdown_alert_interval_seconds=1,
            max_shutdown_seconds=15)

        await runner._shutdown("critical:unowned_working_order")

        repeated = [message for message, critical in notifier.messages
                    if critical and "still cannot prove flat" in message]
        self.assertGreater(len(repeated), 1)
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_expired_shutdown_window_reports_a_non_flat_exit(self):
        notifier = FakeNotifier()
        runner, client, _ = self.build_runner(
            clock=TickingClock(), notifier=notifier)
        await runner.preflight()
        slug = runner.config.market_slug
        self._hold_one_contract(client, slug)
        runner.config = replace(
            runner.config, max_shutdown_exit_attempts=0, max_shutdown_seconds=10)

        await runner._shutdown("critical:hard_exit_insufficient_stressed_depth")

        self.assertFalse(runner._shutdown_proved_flat)
        self.assertTrue(any(
            critical and "shutdown window expired" in message
            for message, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    def test_cli_returns_exit_three_for_unproven_flat_state(self):
        result = CommissioningResult(
            started_at=NOW,
            cutoff_at=None,
            stopped_at=NOW + timedelta(minutes=15),
            stop_reason="critical:hard_exit_attempt_limit_exceeded",
            flat=False,
            orders_sent_to_polymarket=6,
        )
        argv = [
            "live_commissioning",
            "--market-slug", "market-one",
            "--arm-live",
            "--accept-one-contract-risk",
            "--exclusive-api-key",
            "--maker-lab-stopped",
        ]

        with patch("sys.argv", argv), patch(
                "liquidity_bot.live_commissioning._run",
                new=AsyncMock(return_value=result)), patch("builtins.print"):
            self.assertEqual(main(), 3)

    async def test_ambiguous_place_is_adopted_instead_of_unowned(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        async def never_answers(_payload):
            raise AdapterError("create response never arrived")

        client.orders.create = never_answers
        with self.assertRaises(OrderOutcomeUncertain):
            await runner.executor.place(
                OrderRequest(slug, "bid", Decimal("0.49"), Decimal("1")),
                runner.market, now=runner._now())

        # The venue did accept it. A private order update now carries an
        # exchange id the ledger has never seen bound to its intent.
        raw_order = {
            "id": "fake-ambiguous",
            "marketSlug": slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "state": "ORDER_STATE_NEW",
            "side": "ORDER_SIDE_BUY",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "updateTime": NOW.isoformat(),
        }
        await runner._observe_private(PrivateEvent(
            PrivateEventKind.ORDER_UPDATE,
            {"execution": {
                "id": "private-new-ambiguous",
                "type": "EXECUTION_TYPE_NEW",
                "order": raw_order,
            }},
            "private-order-update"), runner._now())

        working = runner.ledger.working_orders(slug)
        self.assertEqual(len(working), 1)
        self.assertFalse(
            str(working[0]["client_order_id"]).startswith("venue:"))
        self.assertEqual(runner.ledger.unresolved_intents(), [])
        # The ambiguous outcome must stay recoverable rather than tripping the
        # unowned-order invariant against the bot's own order.
        runner._assert_runtime_scope()
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_cli_refuses_before_loading_secrets_when_not_armed(self):
        args = Namespace(
            market_slug="market-one",
            accept_one_contract_risk=True,
            exclusive_api_key=True,
            maker_lab_stopped=True,
            arm_live=False,
            lock_file=Path("unused.lock"),
            database=Path("unused.db"),
        )
        with patch("liquidity_bot.live_commissioning.load_runtime_secrets") as load:
            with self.assertRaisesRegex(CommissioningRefused, "arming"):
                await _run(args)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
