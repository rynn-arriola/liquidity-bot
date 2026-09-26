"""Reward-day calendar and cross_exit depth pricing (used by the live trader).

Kept from the archived shadow experiment's tests on 2026-09-26; the shadow
engine/store tests moved to archive/experiments/tests/test_shadow.py.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.models import BookLevel, BookSnapshot
from liquidity_bot.reward_calendar import (
    NEW_YORK, reward_day, reward_day_bounds, risk_day)
from liquidity_bot.shadow_math import cross_exit, split_reward_day_interval


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



class ShadowMathTests(unittest.TestCase):
    def test_reward_day_changes_at_midnight_new_york_not_utc(self):
        self.assertEqual(
            reward_day(datetime(2026, 8, 22, 3, 59, 59, tzinfo=UTC)),
            "2026-08-21")
        self.assertEqual(
            reward_day(datetime(2026, 8, 22, 4, 0, 0, tzinfo=UTC)),
            "2026-08-22")

    def test_risk_day_uses_the_same_et_boundary(self):
        self.assertEqual(
            risk_day(datetime(2026, 8, 22, 3, 59, 59, tzinfo=UTC)),
            "2026-08-21")
        self.assertEqual(
            risk_day(datetime(2026, 8, 22, 4, 0, 0, tzinfo=UTC)),
            "2026-08-22")

    def test_reward_day_bounds_follow_daylight_saving(self):
        spring_start, spring_end = reward_day_bounds("2026-03-08")
        fall_start, fall_end = reward_day_bounds("2026-11-01")
        self.assertEqual((spring_end - spring_start).total_seconds(), 23 * 3600)
        self.assertEqual((fall_end - fall_start).total_seconds(), 25 * 3600)

    def test_reward_interval_counts_absolute_seconds_across_dst(self):
        spring_start = datetime(2026, 3, 8, 0, 0, tzinfo=NEW_YORK)
        fall_start = datetime(2026, 11, 1, 0, 0, tzinfo=NEW_YORK)
        self.assertEqual(
            split_reward_day_interval(
                spring_start, datetime(2026, 3, 9, 0, 0, tzinfo=NEW_YORK)),
            [("2026-03-08", 23 * 3600)])
        self.assertEqual(
            split_reward_day_interval(
                fall_start, datetime(2026, 11, 2, 0, 0, tzinfo=NEW_YORK)),
            [("2026-11-01", 25 * 3600)])

    def test_reward_day_refuses_a_naive_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            reward_day(datetime(2026, 8, 22, 4, 0, 0))

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


if __name__ == "__main__":
    unittest.main()
