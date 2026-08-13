from __future__ import annotations

import asyncio
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from liquidity_bot.commissioning import CommissioningConfig, CommissioningRefused
from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import ExecutionLedger
from liquidity_bot.live_commissioning import CommissioningRunner, _run
from liquidity_bot.notifications import NotificationResult
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind


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
    async def positions(self, params):
        return {"positions": {}, "eof": True}


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
