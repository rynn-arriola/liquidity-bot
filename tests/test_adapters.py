from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from liquidity_bot.adapters import (
    AdapterError,
    AsyncIntervalLimiter,
    AsyncReadOnlyGateway,
    parse_trade_response,
)
from liquidity_bot.private_stream import PrivateEventKind, decode_private_message


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeMarkets:
    def __init__(self):
        self.list_responses = []
        self.market_response = None
        self.book_response = None
        self.list_calls = []

    async def list(self, params):
        self.list_calls.append(params)
        return self.list_responses.pop(0)

    async def retrieve_by_slug(self, slug):
        self.market_slug = slug
        return self.market_response

    async def book(self, slug):
        self.book_slug = slug
        return self.book_response


class FakeClient:
    def __init__(self):
        self.markets = FakeMarkets()
        self.get_responses = []
        self.get_calls = []

    async def get(self, path, *, query=None, authenticated=False):
        self.get_calls.append((path, query, authenticated))
        return self.get_responses.pop(0)


class CountingLimiter:
    def __init__(self):
        self.calls = 0

    async def wait(self):
        self.calls += 1


class RateLimited(Exception):
    status_code = 429


class GatewayTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_incentives_follow_next_page_token_and_preserve_all_periods(self):
        client = FakeClient()
        client.get_responses = [fixture("incentives_page_1.json"),
                                fixture("incentives_page_2.json")]
        gateway = AsyncReadOnlyGateway(client)
        found = self.run_async(gateway.fetch_incentives(["market-one", "market-two"]))

        self.assertEqual(set(found), {"market-one", "market-two"})
        self.assertEqual([p.period for p in found["market-one"].periods],
                         ["day_of", "live"])
        self.assertIsNone(found["market-one"].reported_event_start)
        self.assertEqual(client.get_calls[0][1]["symbols"],
                         ["market-one", "market-two"])
        self.assertEqual(client.get_calls[0][1]["page_size"], 100)
        self.assertEqual(
            client.get_calls[0][1]["program_type"], "liquidityProgram")
        self.assertEqual(client.get_calls[1][1]["page_token"], "page-two")
        self.assertNotIn("pageSize", client.get_calls[0][1])
        self.assertTrue(all(call[2] for call in client.get_calls))

    def test_repeated_pagination_token_fails_closed(self):
        page = fixture("incentives_page_1.json")
        client = FakeClient()
        client.get_responses = [page, page]
        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(client).fetch_incentives(["market-one"]))

    def test_each_attempt_passes_one_and_only_one_governor_gate(self):
        client = FakeClient()
        client.get_responses = [fixture("earnings.json")]
        limiter = CountingLimiter()
        self.run_async(AsyncReadOnlyGateway(client, limiter=limiter).fetch_earnings())
        self.assertEqual(limiter.calls, 1)

    def test_confirmed_429_retries_with_bounded_backoff(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                self.get_calls.append((path, query, authenticated))
                response = self.get_responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        client = Client()
        client.get_responses = [RateLimited("slow down"), fixture("earnings.json")]
        limiter = CountingLimiter()
        rows = self.run_async(AsyncReadOnlyGateway(
            client, limiter=limiter, retry_attempts=2,
            retry_base_seconds=0.25, sleep=fake_sleep).fetch_earnings())
        self.assertEqual(len(rows), 3)
        self.assertEqual(limiter.calls, 2)
        self.assertEqual(sleeps, [0.25])

    def test_unconfirmed_global_limit_text_is_not_treated_as_429(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                raise RuntimeError("Global Rate Limit Exceeded")

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(
                Client(), retry_attempts=3, sleep=fake_sleep).fetch_earnings())
        self.assertEqual(sleeps, [])

    def test_rate_constructor_stays_below_official_limit(self):
        limiter = AsyncIntervalLimiter.from_rate(4)
        self.assertAlmostEqual(limiter.minimum_interval_seconds, 0.25)
        with self.assertRaises(ValueError):
            AsyncIntervalLimiter.from_rate(21)

    def test_malformed_incentive_collection_is_not_empty_success(self):
        client = FakeClient()
        client.get_responses = [{"programs": {}}]
        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(client).fetch_incentives(["market-one"]))

    def test_non_liquidity_period_is_not_treated_as_book_scoring(self):
        client = FakeClient()
        page = fixture("incentives_page_1.json")
        page["programs"][0]["timePeriods"].append({
            "programId": "volume-only",
            "programType": "volumeProgram",
            "period": "day_of",
            "status": "active",
            "rewardPool": 100,
            "minTakerNotional": 10,
        })
        page["nextPageToken"] = None
        client.get_responses = [page]
        found = self.run_async(
            AsyncReadOnlyGateway(client).fetch_incentives(["market-one"]))
        self.assertEqual(
            [item.program_id for item in found["market-one"].periods],
            ["day-one", "live-one"])

    def test_earnings_preserve_paid_pending_and_skipped(self):
        client = FakeClient()
        client.get_responses = [fixture("earnings.json")]
        rows = self.run_async(AsyncReadOnlyGateway(client).fetch_earnings())
        self.assertEqual([row.status for row in rows], ["PAID", "PENDING", "SKIPPED"])
        self.assertEqual(rows[0].reward, 0.30)
        self.assertTrue(rows[0].is_paid)
        self.assertFalse(rows[1].is_paid)

    def test_market_uses_nested_wrapper_clock_and_tick(self):
        client = FakeClient()
        client.markets.market_response = fixture("market.json")
        market = self.run_async(AsyncReadOnlyGateway(client).fetch_market("market-one"))
        self.assertEqual(market.slug, "market-one")
        self.assertEqual(market.game_start.isoformat(), "2026-08-13T03:25:00+00:00")
        self.assertEqual(market.tick_size, 0.005)
        self.assertEqual(market.minimum_trade_qty, 0.25)

    def test_missing_market_clock_fails_closed(self):
        client = FakeClient()
        response = fixture("market.json")
        response["market"]["gameStartTime"] = ""
        client.markets.market_response = response
        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(client).fetch_market("market-one"))

    def test_book_uses_market_data_wrapper_and_full_depth(self):
        client = FakeClient()
        client.markets.book_response = fixture("book.json")
        book = self.run_async(AsyncReadOnlyGateway(client).fetch_book("market-one"))
        self.assertEqual(book.slug, "market-one")
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(len(book.offers), 2)
        self.assertEqual(book.best_bid.price, 0.50)
        self.assertEqual(book.best_offer.price, 0.505)
        self.assertEqual(book.bid_depth, 5100)
        self.assertEqual(book.offer_depth, 5100)

    def test_empty_or_unwrapped_book_fails_closed(self):
        client = FakeClient()
        for response in ({}, {"bids": [], "offers": []},
                         {"marketData": {"marketSlug": "market-one", "bids": [], "offers": []}}):
            client.markets.book_response = response
            with self.assertRaises(AdapterError):
                self.run_async(AsyncReadOnlyGateway(client).fetch_book("market-one"))

    def test_catalog_paginates_by_offset_without_requiring_every_clock(self):
        client = FakeClient()
        one = fixture("market.json")["market"]
        two = dict(one, slug="market-two", gameStartTime="")
        three = dict(one, slug="market-three")
        client.markets.list_responses = [
            {"markets": [one, two]},
            {"markets": [three]},
        ]
        gateway = AsyncReadOnlyGateway(client, catalog_page_size=2)
        rows = self.run_async(gateway.list_sports_markets())
        self.assertEqual([row.slug for row in rows],
                         ["market-one", "market-two", "market-three"])
        self.assertIsNone(rows[1].game_start)
        self.assertEqual([call["offset"] for call in client.markets.list_calls], [0, 2])

    def test_trade_wrapper_preserves_contract_quantity_and_maker_side(self):
        row = parse_trade_response(fixture("trade.json"), expected_slug="market-one")
        self.assertEqual(row.slug, "market-one")
        self.assertEqual(row.price, 0.50)
        self.assertEqual(row.quantity, 4)
        self.assertEqual(row.maker_side, "ORDER_SIDE_BUY")
        self.assertEqual(row.trade_time.isoformat(), "2026-08-13T02:30:01+00:00")

    def test_malformed_trade_fails_closed(self):
        with self.assertRaises(AdapterError):
            parse_trade_response({"trade": {"marketSlug": "market-one"}})


class PrivateMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.messages = fixture("private_messages.json")

    def test_empty_order_snapshot_is_still_a_snapshot(self):
        event = decode_private_message(self.messages["orders_snapshot"])
        self.assertEqual(event.kind, PrivateEventKind.ORDER_SNAPSHOT)
        self.assertEqual(event.payload["orders"], [])

    def test_balance_snapshot_decodes(self):
        event = decode_private_message(self.messages["balance_snapshot"])
        self.assertEqual(event.kind, PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT)

    def test_decimal_position_update_is_preserved(self):
        event = decode_private_message(self.messages["position_update"])
        self.assertEqual(event.kind, PrivateEventKind.POSITION_UPDATE)
        self.assertEqual(event.payload["netPositionDecimal"], "1.750000")

    def test_documented_snake_case_order_snapshot_decodes(self):
        event = decode_private_message({
            "request_id": "orders-snake",
            "subscription_type": 1,
            "order_subscription_snapshot": {"orders": [], "eof": True},
        })
        self.assertEqual(event.kind, PrivateEventKind.ORDER_SNAPSHOT)
        self.assertEqual(event.request_id, "orders-snake")
        self.assertEqual(event.subscription_type, "1")

    def test_documented_position_and_balance_update_envelopes_decode(self):
        position = decode_private_message({
            "requestId": "position-doc",
            "positionSubscription": {"afterPosition": {
                "marketSlug": "market-one",
                "netPositionDecimal": "1.25"}},
        })
        balance = decode_private_message({
            "requestId": "balance-doc",
            "accountBalancesUpdate": {"balanceChange": {}},
        })
        self.assertEqual(position.kind, PrivateEventKind.POSITION_UPDATE)
        self.assertEqual(balance.kind, PrivateEventKind.ACCOUNT_BALANCE_UPDATE)

    def test_heartbeat_and_error_are_explicit(self):
        self.assertEqual(decode_private_message(self.messages["heartbeat"]).kind,
                         PrivateEventKind.HEARTBEAT)
        self.assertEqual(decode_private_message(self.messages["error"]).kind,
                         PrivateEventKind.ERROR)

    def test_unknown_message_fails_closed(self):
        with self.assertRaises(AdapterError):
            decode_private_message({"requestId": "unknown", "newSchema": {}})


if __name__ == "__main__":
    unittest.main()
