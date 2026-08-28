from __future__ import annotations

import asyncio
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liquidity_bot.errors import AdapterError
from liquidity_bot.private_stream import PrivateEventKind
from liquidity_bot.models import BookSnapshot, TradePrint
from liquidity_bot.streams import (
    MarketReadOnlyStream,
    PrivateReadOnlyStream,
    ShadowMarketStream,
    TradeReadOnlyStream,
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
        self.assertIsNotNone(event.received_at)
        self.assertEqual(client.market_socket.calls[0], ("connect",))
        self.assertEqual(client.market_socket.calls[1][0], "market_data")
        self.assertEqual(client.market_socket.calls[1][2], ["market-one"])
        self.assertEqual(client.market_socket.calls[-1], ("close",))

    def test_market_stream_coalesces_pending_full_books_by_slug(self):
        async def scenario():
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            await stream.start(["market-one"])
            first = fixture("book.json")
            latest = copy.deepcopy(first)
            latest["marketData"]["bids"][0]["px"]["value"] = "0.5100"
            client.market_socket.emit("market_data", first)
            client.market_socket.emit("market_data", latest)
            queued = stream._queue.qsize()
            try:
                event = await stream.next_book(timeout=0.1)
            finally:
                await stream.close()
            return queued, event

        queued, event = self.run_async(scenario())
        self.assertEqual(queued, 1)
        self.assertEqual(event.best_bid.price, .51)

    def test_market_heartbeat_prevents_a_false_silence_alarm(self):
        async def scenario():
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            await stream.start(["market-one"])
            now = datetime.now(timezone.utc)
            stream._last_message_at = now - timedelta(seconds=181)
            with self.assertRaisesRegex(AdapterError, "silent"):
                stream.assert_live(now=now)
            client.market_socket.emit("message", {"heartbeat": {}})
            stream.assert_live(now=datetime.now(timezone.utc))
            await stream.close()

        self.run_async(scenario())

    def test_market_stream_drains_latest_books_for_all_pending_slugs(self):
        async def scenario():
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            await stream.start(["market-one", "market-two"])
            first = fixture("book.json")
            second = copy.deepcopy(first)
            second["marketData"]["marketSlug"] = "market-two"
            client.market_socket.emit("market_data", first)
            client.market_socket.emit("market_data", second)
            try:
                return stream.drain_books()
            finally:
                await stream.close()

        books = self.run_async(scenario())
        self.assertEqual([book.slug for book in books],
                         ["market-one", "market-two"])

    def test_market_stream_rejects_a_book_outside_its_subscription(self):
        """A scope violation is our bug, not the venue's, so it stays fatal."""
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

    def test_one_unusable_book_does_not_kill_the_feed(self):
        """Measured 2026-08-15: this killed book delivery for every market.

        A parse failure was queued and raised out of next_book, which the
        runner treats as the socket failing. Subscribing 28 slugs delivered 20
        messages and then silence after one `book is not two-sided` arrived,
        while a later run of 29 stayed healthy at 415 -- so it was never a
        width cap, just whether a bad book happened to arrive. About one market
        in four publishes one.
        """
        async def scenario():
            client = FakeClient()
            stream = MarketReadOnlyStream(client)
            await stream.start(["market-one"])
            # An unusable book, then a good one for a subscribed market.
            client.market_socket.emit("market_data", {"marketData": {}})
            client.market_socket.emit("market_data", fixture("book.json"))
            try:
                book = await stream.next_book(timeout=0.1)
            finally:
                await stream.close()
            return book, stream

        book, stream = self.run_async(scenario())
        # The good book still arrives; the bad one is counted, not raised.
        self.assertEqual(book.slug, "market-one")
        self.assertEqual(stream._dropped_books, 1)
        self.assertIn("slug", str(stream._last_drop))

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
        self.assertIsNotNone(event.received_at)

    def test_private_stream_drains_the_current_backlog_in_wire_order(self):
        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start(["market-one"])
            for value in (1, 2, 3):
                client.private_socket.emit("message", {"heartbeat": value})
            try:
                return stream.drain_events()
            finally:
                await stream.close()

        events = self.run_async(scenario())
        self.assertEqual([event.payload for event in events], [1, 2, 3])
        self.assertTrue(all(event.received_at is not None for event in events))

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

    def test_many_markets_keep_every_subscription_singleton_scoped(self):
        slugs = ["market-one", "market-two", "market-three"]

        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start(slugs)
            position_calls = [call for call in client.private_socket.calls
                              if call[0] == "positions"]
            events = []
            for call in position_calls:
                client.private_socket.emit("message", {
                    "requestId": call[1],
                    "subscriptionType": "SUBSCRIPTION_TYPE_POSITION",
                    "positionSubscription": {
                        "beforePosition": {"netPositionDecimal": "0"},
                        "afterPosition": {"netPositionDecimal": "-1"},
                        "updateTime": "2026-08-13T05:39:26Z",
                        "tradeId": "trade-" + call[1],
                    },
                })
                events.append(await stream.next_event(timeout=0.1))
            try:
                return position_calls, events
            finally:
                await stream.close()

        position_calls, events = self.run_async(scenario())

        # One subscription per market, each filtered to exactly that market.
        self.assertEqual(len(position_calls), len(slugs))
        self.assertEqual(
            sorted(call[2] for call in position_calls),
            sorted([slug] for slug in slugs))
        # Every scope stays a singleton, so a slugless position update remains
        # attributable no matter how many markets are subscribed. A single
        # multi-slug subscription would make all of these ambiguous.
        scopes = [event.market_scope for event in events]
        self.assertTrue(all(len(scope) == 1 for scope in scopes))
        self.assertEqual(
            sorted(scope[0] for scope in scopes), sorted(slugs))

    def test_eight_markets_fit_the_ten_subscription_connection_budget(self):
        async def scenario(count):
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            await stream.start([f"market-{i}" for i in range(count)])
            try:
                return [call for call in client.private_socket.calls
                        if call[0] in ("orders", "positions", "balance")]
            finally:
                await stream.close()

        calls = self.run_async(scenario(8))
        kinds = [call[0] for call in calls]
        # Orders carry marketSlug on every event, so one subscription covering
        # all eight is unambiguous. Positions do not, so they cost one each.
        # 1 + 8 + 1 is the entire connection budget: splitting orders per
        # market too would need 17 and the venue would drop seven silently.
        self.assertEqual(kinds.count("orders"), 1)
        self.assertEqual(kinds.count("positions"), 8)
        self.assertEqual(kinds.count("balance"), 1)
        self.assertEqual(len(calls), 10)
        order_call = next(c for c in calls if c[0] == "orders")
        self.assertEqual(len(order_call[2]), 8)

    def test_more_markets_than_the_connection_allows_is_refused(self):
        async def scenario():
            client = FakeClient()
            stream = PrivateReadOnlyStream(client)
            with self.assertRaisesRegex(ValueError, "exceed the 10"):
                await stream.start([f"market-{i}" for i in range(9)])
            return client

        # It must fail before connecting: the venue reports the overflow
        # asynchronously, so a half-subscribed connection looks healthy.
        client = self.run_async(scenario())
        self.assertEqual(client.private_socket.calls, [])

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

    def test_shadow_stream_counts_an_empty_book_without_reconnecting(self):
        async def scenario():
            client = FakeClient()
            stream = ShadowMarketStream(client)
            await stream.start(["market-one"])
            client.market_socket.emit("market_data", {"marketData": {
                "marketSlug": "market-one", "bids": [], "offers": []}})
            client.market_socket.emit("market_data", fixture("book.json"))
            try:
                event = await stream.next_event(timeout=0.1)
                return stream, event
            finally:
                await stream.close()

        stream, event = self.run_async(scenario())
        self.assertIsInstance(event, BookSnapshot)
        self.assertEqual(stream.decode_failures, 1)

    def test_shadow_stream_keeps_out_of_scope_book_fatal(self):
        async def scenario():
            client = FakeClient()
            stream = ShadowMarketStream(client)
            await stream.start(["market-two"])
            client.market_socket.emit("market_data", fixture("book.json"))
            try:
                await stream.next_event(timeout=0.1)
            finally:
                await stream.close()

        with self.assertRaisesRegex(AdapterError, "unsubscribed slug"):
            self.run_async(scenario())

    def test_shadow_stream_bounds_measurement_backlog(self):
        async def scenario():
            client = FakeClient()
            stream = ShadowMarketStream(client, max_pending=1)
            await stream.start(["market-one"])
            client.market_socket.emit("trade", fixture("trade.json"))
            client.market_socket.emit("trade", fixture("trade.json"))
            try:
                event = await stream.next_event(timeout=0.1)
                return stream, event
            finally:
                await stream.close()

        stream, event = self.run_async(scenario())
        self.assertIsInstance(event, TradePrint)
        self.assertEqual(stream.dropped_events, 1)

    def test_trade_stream_is_separate_bounded_and_trade_only(self):
        async def scenario():
            client = FakeClient()
            stream = TradeReadOnlyStream(client, max_pending=1)
            await stream.start(["market-one"])
            client.market_socket.emit("trade", fixture("trade.json"))
            client.market_socket.emit("trade", fixture("trade.json"))
            try:
                event = await stream.next_trade(timeout=.1)
                return client, stream, event
            finally:
                await stream.close()

        client, stream, event = self.run_async(scenario())
        self.assertIsInstance(event, TradePrint)
        self.assertEqual(stream.dropped_trades, 1)
        self.assertEqual(
            [call[0] for call in client.market_socket.calls],
            ["connect", "trades", "close"])

    def test_bad_trade_is_a_coverage_gap_not_a_stream_failure(self):
        async def scenario():
            client = FakeClient()
            stream = TradeReadOnlyStream(client)
            await stream.start(["market-one"])
            client.market_socket.emit("trade", {"trade": {}})
            client.market_socket.emit("trade", fixture("trade.json"))
            try:
                return stream, await stream.next_trade(timeout=.1)
            finally:
                await stream.close()

        stream, event = self.run_async(scenario())
        self.assertIsInstance(event, TradePrint)
        self.assertEqual(stream.decode_failures, 1)

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
