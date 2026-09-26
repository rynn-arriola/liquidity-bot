from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from liquidity_bot.adapters import REST_REQUEST_TIMEOUT_SECONDS
from liquidity_bot.probe import client_from_environment, inspect_markets


class FakeMarkets:
    def __init__(self, market, book):
        self._market = market
        self._book = book

    async def retrieve_by_slug(self, _slug):
        return {"market": self._market}

    async def book(self, _slug):
        return {"marketData": self._book}


class FakeClient:
    def __init__(self, responses, market, book):
        self._responses = list(responses)
        self.markets = FakeMarkets(market, book)

    async def get(self, _path, *, query=None, authenticated=False):
        self.last_query = query
        self.last_authenticated = authenticated
        return self._responses.pop(0)


class ProbeTests(unittest.TestCase):
    def test_project_clients_use_official_rest_timeout(self):
        with patch(
                "liquidity_bot.probe.load_runtime_secrets",
                return_value=SimpleNamespace(key_id="key", secret_key="secret")), \
                patch("liquidity_bot.sdk_client.AsyncPolymarketUS") as client:
            client_from_environment()

        client.assert_called_once_with(
            key_id="key", secret_key="secret",
            timeout=REST_REQUEST_TIMEOUT_SECONDS)

    def test_probe_composes_clock_program_book_score_and_paid_totals(self):
        start = datetime.now(timezone.utc) + timedelta(hours=2)
        program = {
            "programs": [{
                "marketSlug": "market-one",
                "eventStartTime": "",
                "timePeriods": [{
                    "programId": "day-one", "programType": "liquidityProgram",
                    "period": "day_of",
                    "status": "active", "rewardPool": 35,
                    "discountFactor": 0.5, "targetSize": 5000,
                    "start": None, "end": None,
                }],
            }],
            "nextPageToken": None,
        }
        earnings = {"rewards": [
            {"date": "2026-08-10", "marketSlug": "old-market",
             "programType": "liquidityProgram", "reward": "0.30",
             "status": "PAID"},
            {"date": "2026-08-11", "marketSlug": "old-market",
             "programType": "liquidityProgram", "reward": "0.10",
             "status": "PENDING"},
        ]}
        market = {
            "slug": "market-one", "active": True, "closed": False,
            "status": "MARKET_STATE_OPEN", "gameStartTime": start.isoformat(),
            "orderPriceMinTickSize": "0.01", "sportsMarketType": "moneyline",
        }
        book = {
            "marketSlug": "market-one", "state": "MARKET_STATE_OPEN",
            "bids": [{"px": "0.50", "qty": "5000"}],
            "offers": [{"px": "0.51", "qty": "5000"}],
        }
        client = FakeClient([program, earnings], market, book)
        result = asyncio.run(inspect_markets(
            client, ["market-one"], size=2, include_earnings=True))

        row = result["markets"][0]
        self.assertEqual(row["phase"], "day_of")
        self.assertTrue(row["bid"]["eligible"])
        self.assertEqual(row["bid"]["score"], 2)
        self.assertTrue(row["offer"]["eligible"])
        self.assertEqual(result["account_earnings"]["PAID"]["reward"], 0.30)
        self.assertEqual(result["account_earnings"]["PENDING"]["reward"], 0.10)

    def test_missing_program_is_an_explicit_non_candidate(self):
        start = datetime.now(timezone.utc) + timedelta(hours=2)
        market = {
            "slug": "market-one", "active": True, "closed": False,
            "gameStartTime": start.isoformat(), "orderPriceMinTickSize": "0.01",
        }
        book = {
            "marketSlug": "market-one",
            "bids": [{"px": "0.50", "qty": "1"}],
            "offers": [{"px": "0.51", "qty": "1"}],
        }
        client = FakeClient([{"programs": [], "nextPageToken": None}], market, book)
        result = asyncio.run(inspect_markets(client, ["market-one"]))
        self.assertEqual(result["markets"][0]["reason"], "no_incentive_program")


if __name__ == "__main__":
    unittest.main()
