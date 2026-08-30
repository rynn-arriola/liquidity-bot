from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from liquidity_bot.account_snapshots import (
    AsyncAccountSnapshotGateway, account_equity_snapshot)
from liquidity_bot.adapters import AsyncReadOnlyGateway
from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.models import AccountBalance

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

    async def activities(self, params):
        self.calls.append(params)
        return self.responses.pop(0)


class Account:
    def __init__(self, response=None):
        self.response = response or {"balances": []}

    async def balances(self):
        if isinstance(self.response, list):
            return self.response.pop(0)
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

    def test_equity_uses_position_cash_value_when_asset_notional_is_zero(self):
        balance = {"balances": [{
            "currency": "USD", "currentBalance": "944.4802",
            "assetNotional": "0", "buyingPower": "900"}]}
        client = Client(
            {"orders": []}, [{"positions": {"market-one": {
                "netPosition": "13", "netPositionDecimal": "13",
                "cost": {"value": "6.72", "currency": "USD"},
                "cashValue": {"value": "6.50", "currency": "USD"},
                "updateTime": NOW.isoformat(),
            }}, "eof": True}], [balance, balance])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))

        snapshot = self.run_async(
            account.fetch_equity_snapshot(observed_at=NOW))

        self.assertEqual(snapshot.position_value, Decimal("6.50"))
        self.assertEqual(snapshot.position_basis, Decimal("6.72"))
        self.assertEqual(snapshot.marked_equity, Decimal("950.9802"))
        self.assertEqual(snapshot.open_unrealized_pnl(), Decimal("-0.22"))

    def test_equity_rejects_cash_movement_between_position_reads(self):
        def balance(value):
            return {"balances": [{
                "currency": "USD", "currentBalance": value,
                "assetNotional": "0", "buyingPower": value}]}

        client = Client(
            {"orders": []}, [{"positions": {}, "eof": True}],
            [balance("100"), balance("99")])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))

        with self.assertRaisesRegex(AdapterError, "settled cash changed"):
            self.run_async(account.fetch_equity_snapshot(observed_at=NOW))

    def test_open_position_without_cash_value_fails_closed(self):
        balance = {"balances": [{
            "currency": "USD", "currentBalance": "90",
            "assetNotional": "10", "buyingPower": "80"}]}
        client = Client(
            {"orders": []}, [{"positions": {"market-one": {
                "netPosition": "1", "netPositionDecimal": "1",
                "cost": {"value": "10", "currency": "USD"},
                "updateTime": NOW.isoformat(),
            }}, "eof": True}], [balance, balance])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))

        with self.assertRaisesRegex(AdapterError, "no venue cashValue"):
            self.run_async(account.fetch_equity_snapshot(observed_at=NOW))

    def test_missing_position_basis_keeps_total_mark_but_hides_breakdown(self):
        balance = {"balances": [{
            "currency": "USD", "currentBalance": "90",
            "assetNotional": "0", "buyingPower": "80"}]}
        client = Client(
            {"orders": []}, [{"positions": {"market-one": {
                "netPosition": "1", "netPositionDecimal": "1",
                "cashValue": {"value": "9", "currency": "USD"},
                "updateTime": NOW.isoformat(),
            }}, "eof": True}], [balance, balance])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))

        snapshot = self.run_async(
            account.fetch_equity_snapshot(observed_at=NOW))

        self.assertEqual(snapshot.marked_equity, Decimal("99"))
        self.assertIsNone(snapshot.realized_trading_pnl(
            opening_cash=Decimal("100"), nontrading_cash=Decimal("0")))
        self.assertIsNone(snapshot.open_unrealized_pnl())

    def test_production_incident_reconciles_marked_closed_and_open_pnl(self):
        snapshot = account_equity_snapshot(
            AccountBalance(
                current_balance=Decimal("937.6602"),
                asset_notional=Decimal("0"),
                buying_power=Decimal("900"), observed_at=NOW),
            (PositionState(
                market_slug="market-one", net_qty=Decimal("13"),
                event_at=NOW, cost=Decimal("9.5900"),
                cash_value=Decimal("9.4900")),),
            observed_at=NOW)

        marked = snapshot.marked_trading_pnl(
            opening_equity=Decimal("968.2870"),
            nontrading_cash=Decimal("0"))
        realized = snapshot.realized_trading_pnl(
            opening_cash=Decimal("968.2870"),
            nontrading_cash=Decimal("0"))
        unrealized = snapshot.open_unrealized_pnl()

        self.assertEqual(marked, Decimal("-21.1368"))
        self.assertEqual(realized, Decimal("-21.0368"))
        self.assertEqual(unrealized, Decimal("-0.1000"))
        self.assertEqual(realized + unrealized, marked)

    def test_reward_is_excluded_without_erasing_trading_loss(self):
        snapshot = account_equity_snapshot(
            AccountBalance(
                current_balance=Decimal("1098"), asset_notional=Decimal("0"),
                buying_power=Decimal("1098"), observed_at=NOW),
            (), observed_at=NOW)

        self.assertEqual(snapshot.marked_trading_pnl(
            opening_equity=Decimal("1048"),
            nontrading_cash=Decimal("200")), Decimal("-150"))

    def test_empty_reset_execution_history_produces_no_synthetic_evidence(self):
        """A venue history reset is an absence of evidence, nothing more."""

        client = Client({"orders": []}, [{"activities": [], "eof": True}])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))

        executions = self.run_async(account.fetch_executions(observed_at=NOW))

        self.assertEqual(tuple(executions), ())
        self.assertEqual(executions.trade_states, ())
        self.assertEqual(client.portfolio.calls, [{"limit": 100}])

    def test_busted_trade_is_observed_even_without_an_execution_leg(self):
        client = Client({"orders": []}, [{"activities": [{
            "type": "ACTIVITY_TYPE_TRADE",
            "trade": {
                "id": "trade-busted",
                "marketSlug": "market-one",
                "state": "TRADE_STATE_BUSTED",
                "updateTime": "2026-08-13T03:01:00Z",
                "isAggressor": True,
            },
        }], "eof": True}])
        account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(client))

        batch = self.run_async(account.fetch_executions(observed_at=NOW))

        self.assertEqual(tuple(batch), ())
        self.assertEqual(len(batch.trade_states), 1)
        state = batch.trade_states[0]
        self.assertEqual(state.trade_id, "trade-busted")
        self.assertEqual(state.state, "TRADE_STATE_BUSTED")
        self.assertEqual(state.market_slug, "market-one")


if __name__ == "__main__":
    unittest.main()
