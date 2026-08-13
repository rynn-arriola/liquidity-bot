from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from liquidity_bot.errors import AdapterError
from liquidity_bot.private_stream import PrivateEventKind
from liquidity_bot.models import BookSnapshot, TradePrint
from liquidity_bot.streams import (
    MarketReadOnlyStream,
    PrivateReadOnlyStream,
    ShadowMarketStream,
)


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeSocket:
    def __init__(self):
        self.handlers = {}
        self.calls = []
        self.is_connected = False

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, payload):
        for handler in self.handlers.get(event, []):
            handler(payload)

    async def connect(self):
        self.calls.append(("connect",))
        self.is_connected = True

    async def close(self):
        self.calls.append(("close",))
        self.is_connected = False

    async def subscribe_market_data(self, request_id, slugs):
        self.calls.append(("market_data", request_id, slugs))

    async def subscribe_trades(self, request_id, slugs):
        self.calls.append(("trades", request_id, slugs))

    async def subscribe_orders(self, request_id, slugs=None):
        self.calls.append(("orders", request_id, slugs))

    async def subscribe_positions(self, request_id, slugs=None):
        self.calls.append(("positions", request_id, slugs))

    async def subscribe_account_balance(self, request_id):
        self.calls.append(("balance", request_id))


class FakeFactory:
    def __init__(self, market_socket, private_socket):
        self.market_socket = market_socket
        self.private_socket = private_socket

    def markets(self):
        return self.market_socket

    def private(self):
        return self.private_socket


class FakeClient:
    def __init__(self):
        self.market_socket = FakeSocket()
        self.private_socket = FakeSocket()
        self.ws = FakeFactory(self.market_socket, self.private_socket)


class StreamTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_market_stream_subscribes_and_normalizes_full_book(self):
        async def scenario():
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            await stream.start(["market-one"])
            client.market_socket.emit("market_data", fixture("book.json"))
            event = await stream.next_book(timeout=0.1)
            await stream.close()
            return client, event

        client, event = self.run_async(scenario())
        self.assertEqual(event.slug, "market-one")
        self.assertEqual(event.bid_depth, 5100)
        self.assertEqual(client.market_socket.calls[0], ("connect",))
        self.assertEqual(client.market_socket.calls[1][0], "market_data")
        self.assertEqual(client.market_socket.calls[1][2], ["market-one"])
        self.assertEqual(client.market_socket.calls[-1], ("close",))

    def test_market_stream_rejects_unsubscribed_or_malformed_books(self):
        async def scenario(payload):
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            await stream.start(["market-two"])
            client.market_socket.emit("market_data", payload)
            try:
                await stream.next_book(timeout=0.1)
            finally:
                await stream.close()

        with self.assertRaises(AdapterError):
            self.run_async(scenario(fixture("book.json")))
        with self.assertRaises(AdapterError):
            self.run_async(scenario({"marketData": {}}))

    def test_private_stream_subscribes_to_all_read_only_state(self):
        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start(["market-one"])
            messages = fixture("private_messages.json")
            client.private_socket.emit("message", messages["orders_snapshot"])
            event = await stream.next_event(timeout=0.1)
            await stream.close()
            return client, event

        client, event = self.run_async(scenario())
        kinds = [call[0] for call in client.private_socket.calls]
        self.assertEqual(kinds, ["connect", "orders", "positions", "balance", "close"])
        self.assertEqual(event.kind, PrivateEventKind.ORDER_SNAPSHOT)

    def test_private_event_carries_single_market_subscription_scope(self):
        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start(["market-one"])
            position_call = next(
                call for call in client.private_socket.calls
                if call[0] == "positions")
            client.private_socket.emit("message", {
                "requestId": position_call[1],
                "subscriptionType": "SUBSCRIPTION_TYPE_POSITION",
                "positionSubscription": {
                    "beforePosition": {"netPositionDecimal": "0"},
                    "afterPosition": {"netPositionDecimal": "-1"},
                    "updateTime": "2026-08-13T05:39:26Z",
                    "tradeId": "trade-one",
                },
            })
            try:
                return await stream.next_event(timeout=0.1)
            finally:
                await stream.close()

        event = self.run_async(scenario())
        self.assertEqual(event.kind, PrivateEventKind.POSITION_UPDATE)
        self.assertEqual(event.market_scope, ("market-one",))

    def test_private_stream_surfaces_unknown_schema_as_error(self):
        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start()
            client.private_socket.emit("message", {"unknown": True})
            try:
                await stream.next_event(timeout=0.1)
            finally:
                await stream.close()

        with self.assertRaises(AdapterError):
            self.run_async(scenario())

    def test_disconnect_is_a_fail_closed_stream_error(self):
        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start()
            client.private_socket.emit("close", None)
            try:
                await stream.next_event(timeout=0.1)
            finally:
                await stream.close()

        with self.assertRaisesRegex(AdapterError, "disconnected"):
            self.run_async(scenario())

    def test_shadow_stream_subscribes_to_books_and_trades_in_one_queue(self):
        async def scenario():
            client = FakeClient()
            stream = ShadowMarketStream(client)
            await stream.start(["market-one"])
            client.market_socket.emit("market_data", fixture("book.json"))
            client.market_socket.emit("trade", fixture("trade.json"))
            first = await stream.next_event(timeout=0.1)
            second = await stream.next_event(timeout=0.1)
            await stream.close()
            return client, first, second

        client, first, second = self.run_async(scenario())
        self.assertIsInstance(first, BookSnapshot)
        self.assertIsInstance(second, TradePrint)
        self.assertEqual(
            [call[0] for call in client.market_socket.calls],
            ["connect", "market_data", "trades", "close"])

    def test_empty_subscription_is_rejected_before_connect(self):
        async def scenario():
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            with self.assertRaises(ValueError):
                await stream.start([])
            return client

        client = self.run_async(scenario())
        self.assertEqual(client.market_socket.calls, [])


if __name__ == "__main__":
    unittest.main()
