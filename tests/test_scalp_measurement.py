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
from liquidity_bot.scalp_gtc import (
    GtcReplayConfig,
    _build_replays,
    build_gtc_scalp_report,
)
from liquidity_bot.scalp_report import build_scalp_report
from liquidity_bot.scalp_run import (
    ScalpRunConfig,
    ScalpRunner,
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

    def test_active_run_coverage_health_is_visible_before_finish(self):
        self.store.update_run_health("run", reconnects=2, coverage_gaps=3)
        self.store.flush()
        connection = sqlite3.connect(
            f"file:{self.path.resolve()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT reconnects,coverage_gaps FROM scalp_runs WHERE run_id='run'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, (2, 3))


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

    def test_gtc_trial_waits_for_warmup_then_remains_after_rank_rotation(self):
        root = Path(tempfile.mkdtemp(prefix="scalp-gtc-select-"))
        market = MarketMetadata(
            slug="market-one", game_start=T0 + timedelta(hours=2), tick_size=0.01,
            active=True, closed=False, sports_market_type="moneyline")
        runner = ScalpRunner(object(), ScalpRunConfig(
            database=root / "scalp.db", live_database=root / "live.db",
            minutes=1, pure_markets=40, horizon_minutes=1440,
            refresh_minutes=15, rest_requests_per_second=1,
            policy=ScalpPolicy(), gtc_pure_markets=1, gtc_warmup_seconds=300))
        try:
            runner.catalog = {market.slug: market}
            runner.candidates = [market]
            runner.scope = (market.slug,)
            runner.latest_books = {market.slug: book()}
            runner.latest_book_at = {market.slug: T0}
            runner.first_book_at = {market.slug: T0 - timedelta(seconds=299)}
            runner._rank_pure(T0)
            self.assertEqual(runner.gtc_pure_slugs, set())

            runner.first_book_at[market.slug] = T0 - timedelta(seconds=300)
            runner._rank_pure(T0)
            self.assertEqual(runner.gtc_pure_slugs, {market.slug})

            runner.candidates = []
            runner._rank_pure(T0 + timedelta(seconds=30))
            self.assertEqual(runner.gtc_pure_slugs, {market.slug})
            rows = runner.store.query(
                "SELECT COUNT(*) n FROM scalp_selections "
                "WHERE cohort='PURE_VOLUME_GTC' AND market_slug='market-one'")
            self.assertEqual(rows[0]["n"], 2)
        finally:
            runner.close()

    def test_retained_gtc_overlap_does_not_shrink_the_forty_market_cohort(self):
        root = Path(tempfile.mkdtemp(prefix="scalp-gtc-capacity-"))
        markets = [MarketMetadata(
            slug=f"pure-{index:02d}", game_start=T0 + timedelta(hours=2),
            tick_size=0.01, active=True, closed=False,
            sports_market_type="moneyline") for index in range(40)]
        runner = ScalpRunner(object(), ScalpRunConfig(
            database=root / "scalp.db", live_database=root / "live.db",
            minutes=1, pure_markets=40, horizon_minutes=1440,
            refresh_minutes=15, rest_requests_per_second=1,
            policy=ScalpPolicy(), gtc_pure_markets=20))
        try:
            runner.catalog = {market.slug: market for market in markets}
            runner.candidates = markets
            runner.scope = tuple(market.slug for market in markets)
            runner.latest_books = {
                market.slug: replace(book(), slug=market.slug,
                                     shares_traded=1000 - index)
                for index, market in enumerate(markets)}
            runner.latest_book_at = {market.slug: T0 for market in markets}
            runner.gtc_pure_slugs = {market.slug for market in markets[:20]}
            runner.reward_slugs = tuple(f"reward-{index:02d}" for index in range(20))
            runner.engine.set_cohorts(
                {"RETAINED": {f"exposure-{index:02d}" for index in range(30)}},
                at=T0)
            runner._rank_pure(T0)
            self.assertEqual(len(runner.pure_slugs), 40)
        finally:
            runner.close()


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


class FixedGtcReplayTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="scalp-gtc-")) / "scalp.db"
        self.store = ScalpStore(self.path)
        self.store.start_run("run", T0 - timedelta(minutes=10), {})
        self.market = MarketMetadata(
            slug="market-one", game_start=T0 + timedelta(hours=2), tick_size=0.01,
            active=True, closed=False, sports_market_type="moneyline")

    def tearDown(self):
        self.store.close()

    def select(self, at: datetime = T0) -> None:
        self.store.record_selection(
            "run", "PURE_VOLUME_GTC", (self.market,), at, source="TEST",
            rank_metric="shares_traded", rank_values={"market-one": 100.0})

    def raw_book(self, at: datetime, *, bid: float = 0.45,
                 offer: float = 0.50, bid_qty: float = 5,
                 offer_qty: float = 5) -> None:
        self.store.record_book(
            "run", book(at=at, bid=bid, offer=offer, bid_qty=bid_qty,
                        offer_qty=offer_qty), at)

    def raw_trade(self, side: str, at: datetime, *, price: float,
                  qty: float) -> None:
        self.store.record_trade("run", trade(side, price=price, qty=qty, at=at), at)

    def finish(self, at: datetime) -> None:
        self.store.finish_run("run", at, reconnects=0, coverage_gaps=0)
        self.store.flush()

    def test_gtc_keeps_original_pair_past_five_minutes_until_late_full_fills(self):
        self.select()
        self.raw_book(T0)
        # Both trades arrive after the rolling engine's five-minute renewal.
        self.raw_trade("bid", T0 + timedelta(minutes=6), price=0.44, qty=2)
        self.raw_trade("offer", T0 + timedelta(minutes=7), price=0.51, qty=2)
        self.finish(T0 + timedelta(minutes=8))

        report = build_gtc_scalp_report(
            self.path, config=GtcReplayConfig(quantity=2))
        row = report["fixed_gtc_to_settlement"][0]
        self.assertEqual(row["orders_started"], 1)
        self.assertEqual(row["both_filled"], 1)
        self.assertAlmostEqual(row["net_pnl_usd"], 0.12)
        self.assertEqual(row["median_second_leg_seconds"], 60)

    def test_preplacement_features_never_read_future_trade_flow(self):
        self.raw_book(T0 - timedelta(minutes=2))
        self.raw_trade("bid", T0 - timedelta(seconds=30), price=0.44, qty=10)
        self.select()
        self.raw_book(T0)
        self.raw_trade("offer", T0 + timedelta(seconds=1), price=0.51, qty=100)
        self.finish(T0 + timedelta(minutes=1))

        connection = sqlite3.connect(
            f"file:{self.path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            replay = _build_replays(
                connection, GtcReplayConfig(quantity=2))[0]
        finally:
            connection.close()
        self.assertEqual(replay.features["bid_consuming_qty"], 10)
        self.assertEqual(replay.features["offer_consuming_qty"], 0)
        self.assertEqual(replay.features["trade_qty_total"], 10)
        self.assertEqual(replay.features["two_side_queue_clearance_score"], 0)

    def test_one_sided_fill_is_charged_at_settlement_not_called_a_pair(self):
        self.select()
        self.raw_book(T0)
        self.raw_trade("bid", T0 + timedelta(seconds=1), price=0.44, qty=2)
        settled = T0 + timedelta(hours=2)
        self.store.record_settlement("run", "market-one", settled, 0.0)
        self.finish(settled)

        report = build_gtc_scalp_report(
            self.path, config=GtcReplayConfig(quantity=2))
        row = report["fixed_gtc_to_settlement"][0]
        self.assertEqual(row["both_filled"], 0)
        self.assertEqual(row["one_side_or_partial"], 1)
        self.assertAlmostEqual(row["net_pnl_usd"], -0.89)

    def test_selection_refresh_does_not_create_a_second_gtc_pair(self):
        self.select()
        self.raw_book(T0)
        self.select(T0 + timedelta(minutes=15))
        self.raw_book(T0 + timedelta(minutes=15))
        self.finish(T0 + timedelta(minutes=16))

        connection = sqlite3.connect(
            f"file:{self.path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = _build_replays(connection, GtcReplayConfig(quantity=2))
        finally:
            connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].started_at, T0)

    def test_one_hour_variant_uses_fresh_book_after_first_fill(self):
        self.select()
        self.raw_book(T0)
        self.raw_trade("bid", T0 + timedelta(seconds=1), price=0.44, qty=2)
        due = T0 + timedelta(seconds=3601)
        self.raw_book(due, bid=0.40, offer=0.50, bid_qty=20, offer_qty=20)
        self.finish(due + timedelta(seconds=1))

        report = build_gtc_scalp_report(
            self.path, config=GtcReplayConfig(quantity=2))
        row = report["fixed_gtc_with_early_exit"][0]
        self.assertEqual(row["resolved"], 1)
        self.assertEqual(row["one_side_or_partial"], 1)
        self.assertLess(row["net_pnl_usd"], 0)

    def test_later_scope_loss_excludes_an_unresolved_path_from_strict_results(self):
        self.select()
        first_stream = self.store.start_stream(
            "run", T0 - timedelta(seconds=1), ("market-one",))
        self.raw_book(T0)
        self.raw_trade("bid", T0 + timedelta(seconds=1), price=0.44, qty=2)
        self.store.finish_stream(
            first_stream, T0 + timedelta(seconds=2), "REBOUND")
        second_stream = self.store.start_stream(
            "run", T0 + timedelta(seconds=2), ("different-market",))
        settled = T0 + timedelta(minutes=10)
        self.store.finish_stream(second_stream, settled, "STOPPED")
        self.store.record_settlement("run", "market-one", settled, 0.0)
        self.finish(settled)

        report = build_gtc_scalp_report(
            self.path, config=GtcReplayConfig(quantity=2))
        row = report["fixed_gtc_to_settlement"][0]
        self.assertEqual(row["orders_started"], 1)
        self.assertEqual(row["coverage_clean_orders"], 0)
        self.assertEqual(row["coverage_gaps_excluded"], 1)

    def test_old_gap_does_not_censor_pair_started_after_clean_reconnect(self):
        old = self.store.start_stream(
            "run", T0 - timedelta(minutes=5), ("market-one",))
        self.store.finish_stream(
            old, T0 - timedelta(seconds=2), "INTERRUPTED", "old process ended")
        clean = self.store.start_stream(
            "run", T0 - timedelta(seconds=1), ("market-one",))
        self.select()
        self.raw_book(T0)
        ended = T0 + timedelta(minutes=1)
        self.store.finish_stream(clean, ended, "STOPPED")
        self.store.finish_run("run", ended, reconnects=1, coverage_gaps=1)
        self.store.flush()

        report = build_gtc_scalp_report(
            self.path, config=GtcReplayConfig(quantity=2))
        row = report["fixed_gtc_to_settlement"][0]
        self.assertEqual(row["orders_started"], 1)
        self.assertEqual(row["coverage_clean_orders"], 1)
        self.assertEqual(row["coverage_gaps_excluded"], 0)

    def test_legacy_run_gap_without_stream_intervals_stays_conservative(self):
        self.select()
        self.raw_book(T0)
        ended = T0 + timedelta(minutes=1)
        self.store.finish_run("run", ended, reconnects=1, coverage_gaps=1)
        self.store.flush()

        report = build_gtc_scalp_report(
            self.path, config=GtcReplayConfig(quantity=2))
        row = report["fixed_gtc_to_settlement"][0]
        self.assertEqual(row["coverage_clean_orders"], 0)
        self.assertEqual(row["coverage_gaps_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
