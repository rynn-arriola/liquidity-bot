from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liquidity_bot.models import BookLevel, BookSnapshot, MarketMetadata, TradePrint
from liquidity_bot.rewards import IncentivePeriod
from liquidity_bot.shadow_engine import ShadowEngine, ShadowPolicy
from liquidity_bot.shadow_math import cross_exit, split_reward_day_interval
from liquidity_bot.shadow_store import ShadowStore


UTC = timezone.utc
T0 = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)


def book(*, bid=0.50, offer=0.52, bid_qty=5000, offer_qty=5000,
         at=T0) -> BookSnapshot:
    return BookSnapshot(
        slug="market-one",
        bids=(BookLevel(bid, bid_qty), BookLevel(round(bid - 0.01, 2), 5000)),
        offers=(BookLevel(offer, offer_qty), BookLevel(round(offer + 0.01, 2), 5000)),
        state="MARKET_STATE_OPEN",
        transact_time=at,
    )


def market() -> MarketMetadata:
    return MarketMetadata(
        slug="market-one", game_start=T0 + timedelta(hours=2), tick_size=0.01,
        active=True, closed=False, status="MARKET_STATE_OPEN",
        sports_market_type="table_tennis_match_winner")


def incentive(*, target=5000) -> IncentivePeriod:
    return IncentivePeriod(
        program_id="day-one", period="day_of", status="active",
        reward_pool=35, discount_factor=0.5, target_size=target)


def trade(*, price, qty, maker_side, at) -> TradePrint:
    return TradePrint(
        slug="market-one", price=price, quantity=qty,
        maker_side=maker_side, trade_time=at, request_id="fixture")


class ShadowMathTests(unittest.TestCase):
    def test_reward_interval_splits_at_midnight_new_york(self):
        start = datetime(2026, 8, 13, 3, 59, 50, tzinfo=UTC)
        end = start + timedelta(seconds=20)
        self.assertEqual(
            split_reward_day_interval(start, end),
            [("2026-08-12", 10.0), ("2026-08-13", 10.0)])

    def test_cross_exit_walks_depth_and_prices_long_close(self):
        snapshot = BookSnapshot(
            slug="market-one",
            bids=(BookLevel(0.49, 1), BookLevel(0.48, 2)),
            offers=(BookLevel(0.52, 5),),
        )
        result = cross_exit(snapshot, entry_side="bid", entry_price=0.50, qty=2)
        self.assertTrue(result.complete)
        self.assertAlmostEqual(result.average_price, 0.485)
        self.assertLess(result.pnl, 0)
        self.assertGreater(result.taker_fee, 0)

    def test_depth_haircut_can_make_an_apparent_exit_unsafe(self):
        snapshot = BookSnapshot(
            slug="market-one",
            bids=(BookLevel(0.50, 3),),
            offers=(BookLevel(0.52, 3),),
        )
        result = cross_exit(
            snapshot, entry_side="bid", entry_price=0.50,
            qty=2, depth_haircut=0.5)
        self.assertFalse(result.complete)
        self.assertAlmostEqual(result.closed_qty, 1.5)
        self.assertGreater(result.remaining_qty, 0)


class ShadowEngineTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="liquidity-shadow-")) / "shadow.db"
        self.store = ShadowStore(self.path)
        self.policy = ShadowPolicy(
            quote_size=2, exit_horizons_seconds=(15, 30, 60, 120),
            exit_depth_haircut=0.5, max_exit_loss_per_market=5)
        self.engine = ShadowEngine("run-one", self.store, self.policy, started_at=T0)
        self.engine.register_market(market(), (incentive(),), observed_at=T0)

    def tearDown(self):
        self.store.close()

    def test_eligible_touch_accumulates_score_seconds(self):
        self.engine.on_book(book(), observed_at=T0)
        self.engine.on_book(book(at=T0 + timedelta(seconds=10)),
                            observed_at=T0 + timedelta(seconds=10))
        rows = self.store.query(
            "SELECT side,candidate_seconds,candidate_score_seconds "
            "FROM shadow_score_daily ORDER BY side")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertAlmostEqual(row["candidate_seconds"], 10)
            self.assertAlmostEqual(row["candidate_score_seconds"], 20)

    def test_target_failure_records_zero_candidate_score(self):
        self.engine = ShadowEngine("run-two", self.store, self.policy, started_at=T0)
        self.engine.register_market(market(), (incentive(target=10000),), observed_at=T0)
        self.engine.on_book(book(bid_qty=10, offer_qty=10), observed_at=T0)
        self.engine.on_book(book(bid_qty=10, offer_qty=10,
                                 at=T0 + timedelta(seconds=10)),
                            observed_at=T0 + timedelta(seconds=10))
        rows = self.store.query(
            "SELECT SUM(candidate_score_seconds) score FROM shadow_score_daily "
            "WHERE run_id='run-two'")
        self.assertAlmostEqual(rows[0]["score"], 0)
        observations = self.store.query(
            "SELECT candidate,reason FROM shadow_quote_observations "
            "WHERE run_id='run-two'")
        self.assertTrue(observations)
        self.assertTrue(all(row["candidate"] == 0 for row in observations))

    def test_deactivated_market_stops_accruing_candidate_score(self):
        self.engine.on_book(book(), observed_at=T0)
        self.engine.deactivate_market(
            "market-one", observed_at=T0 + timedelta(seconds=10))
        self.engine.finish(T0 + timedelta(seconds=100))
        score = self.store.query(
            "SELECT SUM(candidate_score_seconds) score FROM shadow_score_daily "
            "WHERE run_id='run-one'")[0]["score"]
        self.assertAlmostEqual(score, 40)

    def test_back_of_queue_requires_trade_volume_ahead_before_fill(self):
        small = incentive(target=5)
        self.engine = ShadowEngine("run-queue", self.store, self.policy, started_at=T0)
        self.engine.register_market(market(), (small,), observed_at=T0)
        self.engine.on_book(book(bid_qty=5, offer_qty=5), observed_at=T0)
        self.engine.on_trade(trade(
            price=0.50, qty=4, maker_side="ORDER_SIDE_BUY",
            at=T0 + timedelta(seconds=1)))
        self.assertEqual(self.store.query(
            "SELECT COUNT(*) n FROM shadow_fill_signals WHERE run_id='run-queue'")[0]["n"], 0)
        self.engine.on_trade(trade(
            price=0.50, qty=3, maker_side="ORDER_SIDE_BUY",
            at=T0 + timedelta(seconds=2)))
        fills = self.store.query(
            "SELECT qty,signal_kind,queue_before FROM shadow_fill_signals "
            "WHERE run_id='run-queue'")
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(fills[0]["qty"], 2)
        self.assertEqual(fills[0]["signal_kind"], "at_price_queue")
        self.assertAlmostEqual(fills[0]["queue_before"], 1)

    def test_hard_exit_uses_first_book_at_timeout(self):
        self.engine.on_book(book(), observed_at=T0)
        self.engine.on_trade(trade(
            price=0.49, qty=2, maker_side="ORDER_SIDE_BUY",
            at=T0 + timedelta(seconds=1)))
        due_book = book(bid=0.48, offer=0.50, at=T0 + timedelta(seconds=16))
        self.engine.on_book(due_book, observed_at=T0 + timedelta(seconds=16))
        rows = self.store.query(
            "SELECT horizon_seconds,method,complete,average_exit_price,lag_seconds "
            "FROM shadow_exit_replays ORDER BY horizon_seconds")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["horizon_seconds"], 15)
        self.assertEqual(rows[0]["method"], "hard_ioc")
        self.assertEqual(rows[0]["complete"], 1)
        self.assertAlmostEqual(rows[0]["average_exit_price"], 0.48)
        self.assertAlmostEqual(rows[0]["lag_seconds"], 0)

    def test_passive_opposite_fill_completes_all_longer_policies(self):
        small = incentive(target=5)
        self.engine = ShadowEngine("run-passive", self.store, self.policy, started_at=T0)
        self.engine.register_market(market(), (small,), observed_at=T0)
        self.engine.on_book(book(bid_qty=5, offer_qty=5), observed_at=T0)
        self.engine.on_trade(trade(
            price=0.49, qty=2, maker_side="ORDER_SIDE_BUY",
            at=T0 + timedelta(seconds=1)))
        self.engine.on_trade(trade(
            price=0.52, qty=7, maker_side="ORDER_SIDE_SELL",
            at=T0 + timedelta(seconds=10)))
        rows = self.store.query(
            "SELECT horizon_seconds,method,complete,pnl "
            "FROM shadow_exit_replays WHERE run_id='run-passive' "
            "ORDER BY horizon_seconds")
        self.assertEqual([row["horizon_seconds"] for row in rows], [15, 30, 60, 120])
        self.assertTrue(all(row["method"] == "passive" for row in rows))
        self.assertTrue(all(row["complete"] == 1 for row in rows))
        self.assertTrue(all(row["pnl"] > 0 for row in rows))


if __name__ == "__main__":
    unittest.main()
