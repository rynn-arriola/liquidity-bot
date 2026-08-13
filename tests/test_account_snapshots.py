from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
from liquidity_bot.adapters import AsyncReadOnlyGateway
from liquidity_bot.errors import AdapterError

from tests.test_adapters import CountingLimiter
from tests.test_venue_state import raw_order


NOW = datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc)


class Orders:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def list(self, params):
        self.calls.append(params)
        return self.response


class Portfolio:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def positions(self, params):
        self.calls.append(params)
        return self.responses.pop(0)


class Account:
    def __init__(self, response=None):
        self.response = response or {"balances": []}

    async def balances(self):
        return self.response


class Client:
    def __init__(self, order_response, position_responses, balance_response=None):
        self.orders = Orders(order_response)
        self.portfolio = Portfolio(position_responses)
        self.account = Account(balance_response)


class SnapshotGatewayTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_order_and_paginated_positions_share_one_limiter(self):
        client = Client(
            {"orders": [raw_order()]},
            [
                {"positions": {"market-one": {
                    "netPosition": "1", "netPositionDecimal": "1.25",
                    "updateTime": NOW.isoformat()}},
                 "eof": False, "nextCursor": "page-two"},
                {"positions": {"market-two": {
                    "netPosition": "-1", "netPositionDecimal": "-0.50",
                    "updateTime": NOW.isoformat()}},
                 "eof": True},
            ])
        limiter = CountingLimiter()
        gateway = AsyncReadOnlyGateway(client, limiter=limiter)
        account = AsyncAccountSnapshotGateway(gateway)
        orders = self.run_async(account.fetch_open_orders(
            slugs=["market-one"], observed_at=NOW))
        positions = self.run_async(account.fetch_positions(observed_at=NOW))
        self.assertEqual(len(orders), 1)
        self.assertEqual({item.market_slug for item in positions},
                         {"market-one", "market-two"})
        self.assertEqual(limiter.calls, 3)
        self.assertEqual(client.portfolio.calls[1]["cursor"], "page-two")

    def test_position_page_without_eof_or_cursor_fails_closed(self):
        client = Client({"orders": []}, [{"positions": {}, "eof": False}])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))
        with self.assertRaises(AdapterError):
            self.run_async(account.fetch_positions(observed_at=NOW))

    def test_position_page_missing_eof_fails_closed(self):
        client = Client({"orders": []}, [{"positions": {}}])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))
        with self.assertRaises(AdapterError):
            self.run_async(account.fetch_positions(observed_at=NOW))

    def test_usd_balance_produces_marked_equity_through_shared_limiter(self):
        client = Client({"orders": []}, [], {"balances": [{
            "currency": "USD", "currentBalance": "90.25",
            "assetNotional": "9.75", "buyingPower": "80.00"}]})
        limiter = CountingLimiter()
        account = AsyncAccountSnapshotGateway(
            AsyncReadOnlyGateway(client, limiter=limiter))
        balance = self.run_async(account.fetch_balance(observed_at=NOW))
        self.assertEqual(balance.marked_equity, Decimal("100.00"))
        self.assertEqual(limiter.calls, 1)

    def test_balance_missing_asset_notional_fails_closed(self):
        client = Client({"orders": []}, [], {"balances": [{
            "currency": "USD", "currentBalance": "90",
            "buyingPower": "80"}]})
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))
        with self.assertRaisesRegex(AdapterError, "assetNotional"):
            self.run_async(account.fetch_balance(observed_at=NOW))


if __name__ == "__main__":
    unittest.main()
