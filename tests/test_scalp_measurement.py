from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liquidity_bot.adapters import parse_book_response
from liquidity_bot.models import (
    BookLevel,
    BookSnapshot,
    MarketMetadata,
    TradePrint,
)
from liquidity_bot.scalp_engine import (
    ScalpMeasurementEngine,
    ScalpPolicy,
    maker_rebate,
)
from liquidity_bot.scalp_report import build_scalp_report
from liquidity_bot.scalp_run import (
    eligible_scalp_markets,
    rank_observed_volume,
    read_reward_scope,
)
from liquidity_bot.scalp_store import ScalpStore


UTC = timezone.utc
T0 = datetime(2026, 8, 28, 4, 0, tzinfo=UTC)


def book(*, at=T0, bid=0.45, offer=0.50, bid_qty=5, offer_qty=5) -> BookSnapshot:
    return BookSnapshot(
        slug="market-one",
        bids=(BookLevel(bid, bid_qty), BookLevel(round(bid - 0.01, 3), 20)),
        offers=(BookLevel(offer, offer_qty), BookLevel(round(offer + 0.01, 3), 20)),
        state="MARKET_STATE_OPEN",
        transact_time=at,
        shares_traded=100,
        open_interest=50,
        notional_traded_usd=45,
    )


def trade(side: str, *, price: float, qty: float, at: datetime) -> TradePrint:
    return TradePrint(
        slug="market-one", price=price, quantity=qty,
        maker_side="ORDER_SIDE_BUY" if side == "bid" else "ORDER_SIDE_SELL",
        trade_time=at, request_id="subscription-request")


class AdapterMeasurementFieldsTests(unittest.TestCase):
    def test_book_stats_are_retained_as_optional_measurement_metadata(self):
        parsed = parse_book_response({"marketData": {
            "marketSlug": "market-one",
            "state": "MARKET_STATE_OPEN",
            "bids": [{"px": "0.45", "qty": "5"}],
            "offers": [{"px": "0.50", "qty": "5"}],
            "stats": {
                "sharesTraded": "123.5",
                "openInterest": "40",
                "notionalTraded": {"value": "55.25", "currency": "USD"},
            },
        }})
        self.assertEqual(parsed.shares_traded, 123.5)
        self.assertEqual(parsed.open_interest, 40)
        self.assertEqual(parsed.notional_traded_usd, 55.25)


class ScalpEngineTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="scalp-measure-")) / "scalp.db"
        self.store = ScalpStore(self.path)
        self.store.start_run("run", T0, {})
        self.engine = ScalpMeasurementEngine(
            "run", self.store,
            ScalpPolicy(quantity=2, no_fill_timeout_seconds=60,
                        minimum_spread=0.005))
        self.engine.set_cohorts({
            "PURE_VOLUME": {"market-one"},
            "REWARD_SLOTS": {"market-one"},
        }, at=T0)

    def tearDown(self):
        self.store.close()

    def test_pair_success_requires_full_quantity_on_both_original_sides(self):
        self.engine.on_book(book(), observed_at=T0)
        # Five contracts were ahead. Six consume the queue and fill only one.
        self.engine.on_trade(trade(
            "bid", price=0.45, qty=6, at=T0 + timedelta(seconds=1)),
            observed_at=T0 + timedelta(seconds=1))
        # Filling the other side fully still leaves the first side partial.
        self.engine.on_trade(trade(
            "offer", price=0.50, qty=7, at=T0 + timedelta(seconds=2)),
            observed_at=T0 + timedelta(seconds=2))
        rows = self.store.query(
            "SELECT cohort,status,bid_filled_qty,offer_filled_qty "
            "FROM scalp_episodes ORDER BY cohort")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "OPEN" for row in rows))
        self.assertTrue(all(row["bid_filled_qty"] == 1 for row in rows))
        self.assertTrue(all(row["offer_filled_qty"] == 2 for row in rows))
        # The last bid contract is now behind no queue and completes the pair.
        self.engine.on_trade(trade(
            "bid", price=0.45, qty=1, at=T0 + timedelta(seconds=3)),
            observed_at=T0 + timedelta(seconds=3))
        completed = self.store.query(
            "SELECT * FROM scalp_episodes WHERE status='BOTH_FILLED'")
        self.assertEqual(len(completed), 2)
        for row in completed:
            self.assertAlmostEqual(row["gross_pair_pnl_usd"], 0.10)

    def test_no_fill_timeout_creates_a_new_denominator_episode(self):
        self.engine.on_book(book(), observed_at=T0)
        self.engine.on_book(
            book(at=T0 + timedelta(seconds=61)),
            observed_at=T0 + timedelta(seconds=61))
        rows = self.store.query(
            "SELECT status,COUNT(*) n FROM scalp_episodes "
            "WHERE cohort='PURE_VOLUME' GROUP BY status ORDER BY status")
        self.assertEqual([(row["status"], row["n"]) for row in rows],
                         [("NO_FILL_TIMEOUT", 1), ("OPEN", 1)])

    def test_selection_removal_retains_first_leg_but_censors_no_fill(self):
        self.engine.on_book(book(), observed_at=T0)
        self.engine.on_trade(trade(
            "bid", price=0.44, qty=2, at=T0 + timedelta(seconds=1)),
            observed_at=T0 + timedelta(seconds=1))
        self.engine.set_cohorts({"PURE_VOLUME": set(), "REWARD_SLOTS": set()},
                                at=T0 + timedelta(seconds=2))
        rows = self.store.query(
            "SELECT cohort,status,first_fill_at FROM scalp_episodes ORDER BY cohort")
        # Trade-through fills both cohort copies on bid; both remain observable.
        self.assertTrue(all(row["status"] == "OPEN" for row in rows))
        self.assertTrue(all(row["first_fill_at"] is not None for row in rows))
        self.assertIn("market-one", self.engine.required_slugs)

    def test_maker_rebate_uses_cent_level_bankers_rounding(self):
        self.assertEqual(maker_rebate(0.50, 1), 0.0)
        self.assertEqual(maker_rebate(0.50, 10), 0.03)

    def test_unclean_restart_restores_first_leg_and_finishes_original_pair(self):
        self.engine.on_book(book(), observed_at=T0)
        self.engine.on_trade(trade(
            "bid", price=0.44, qty=2, at=T0 + timedelta(seconds=1)),
            observed_at=T0 + timedelta(seconds=1))
        self.store.close()

        self.store = ScalpStore(self.path)
        restored = ScalpMeasurementEngine("run", self.store, self.engine.policy)
        self.assertEqual(restored.restore_open(), 2)
        restored.on_trade(trade(
            "offer", price=0.51, qty=2, at=T0 + timedelta(seconds=2)),
            observed_at=T0 + timedelta(seconds=2))

        rows = self.store.query(
            "SELECT status FROM scalp_episodes ORDER BY cohort")
        self.assertEqual([row["status"] for row in rows],
                         ["BOTH_FILLED", "BOTH_FILLED"])


class SelectionTests(unittest.TestCase):
    def test_volume_ranking_uses_observed_book_shares_not_missing_catalog_fields(self):
        def market(slug: str) -> MarketMetadata:
            return MarketMetadata(
                slug=slug, game_start=T0 + timedelta(hours=2), tick_size=0.01,
                active=True, closed=False, sports_market_type="moneyline")
        markets = [market("b"), market("a"), market("c")]
        books = {
            "a": replace(book(), slug="a", shares_traded=20),
            "b": replace(book(), slug="b", shares_traded=100),
            "c": replace(book(), slug="c", shares_traded=None),
        }
        ranked = rank_observed_volume(markets, books, limit=3)
        self.assertEqual([row.slug for row in ranked], ["b", "a"])

    def test_candidate_eligibility_has_no_league_filter(self):
        markets = [MarketMetadata(
            slug=slug, game_start=T0 + timedelta(hours=2), tick_size=0.01,
            active=True, closed=False, sports_market_type="moneyline")
            for slug in ("aec-wta-one-two-2026-08-28",
                         "aec-mlb-three-four-2026-08-28")]
        result = eligible_scalp_markets(
            markets, now=T0, horizon=T0 + timedelta(hours=6))
        self.assertEqual({market.slug for market in result},
                         {market.slug for market in markets})

    def test_reward_scope_requires_one_complete_active_session(self):
        path = Path(tempfile.mkdtemp(prefix="scalp-scope-")) / "live.db"
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE execution_queue_stream_sessions("
            "id INTEGER PRIMARY KEY,scope_json TEXT,scope_size INTEGER,"
            "status TEXT,ended_at TEXT)")
        connection.execute(
            "INSERT INTO execution_queue_stream_sessions(scope_json,scope_size,"
            "status,ended_at) VALUES (?,?,?,NULL)",
            (json.dumps(["m1", "m2"]), 2, "COLLECTING"))
        connection.commit()
        connection.close()
        self.assertEqual(read_reward_scope(path), ("m1", "m2"))


class ScalpReportTests(unittest.TestCase):
    def test_report_groups_pair_fill_and_replays_arbitrary_early_exit(self):
        path = Path(tempfile.mkdtemp(prefix="scalp-report-")) / "scalp.db"
        store = ScalpStore(path)
        store.start_run("run", T0, {})
        engine = ScalpMeasurementEngine(
            "run", store, ScalpPolicy(quantity=2, no_fill_timeout_seconds=60,
                                       minimum_spread=0.005))
        engine.set_cohorts({"PURE_VOLUME": {"market-one"}}, at=T0)
        engine.on_book(book(), observed_at=T0)
        engine.on_trade(trade(
            "bid", price=0.44, qty=2, at=T0 + timedelta(seconds=1)),
            observed_at=T0 + timedelta(seconds=1))
        # A book after the 30-second horizon provides stressed exit depth.
        engine.on_book(
            book(at=T0 + timedelta(seconds=31), bid=0.44, offer=0.49,
                 bid_qty=20, offer_qty=20),
            observed_at=T0 + timedelta(seconds=31))
        store.finish_run("run", T0 + timedelta(seconds=60),
                         reconnects=0, coverage_gaps=0)
        store.flush()
        report = build_scalp_report(path, (30, 90))
        store.close()
        cohort = report["cohorts"][0]
        self.assertEqual(cohort["started_two_sided_episodes"], 1)
        self.assertEqual(cohort["resolved_episodes"], 0)
        self.assertIsNone(cohort["both_side_fill_rate"])
        self.assertEqual(cohort["both_sides_filled"], 0)
        replay = report["early_exit_replays"][0]
        self.assertEqual(replay["samples"], 1)
        self.assertEqual(replay["complete_exits"], 1)
        pending = report["early_exit_replays"][1]
        self.assertEqual(pending["samples"], 0)
        self.assertEqual(pending["pending_horizon"], 1)


if __name__ == "__main__":
    unittest.main()
