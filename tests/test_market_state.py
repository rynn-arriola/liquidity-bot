from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.market_state import MarketState
from liquidity_bot.models import BookLevel, BookSnapshot


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def book(bid: float, offer: float) -> BookSnapshot:
    return BookSnapshot(
        "market-one", (BookLevel(bid, 100),), (BookLevel(offer, 100),))


class MarketStateTelemetryTests(unittest.TestCase):
    def test_touch_history_counts_changes_only_and_is_bounded(self):
        state = MarketState("market-one")
        state.observe_book_touch(book(.48, .52), observed_at=NOW)
        state.observe_book_touch(
            book(.48, .52), observed_at=NOW + timedelta(seconds=5))
        state.observe_book_touch(
            book(.49, .51), observed_at=NOW + timedelta(seconds=10))
        state.observe_book_touch(
            book(.50, .50), observed_at=NOW + timedelta(seconds=40))

        self.assertEqual(state.touch_changes(
            NOW + timedelta(seconds=40), seconds=30), 2)
        self.assertEqual(state.touch_changes(
            NOW + timedelta(seconds=40), seconds=120), 2)
        self.assertEqual(state.seconds_since_touch_change(
            NOW + timedelta(seconds=45)), 5)

        state.observe_book_touch(
            book(.51, .49), observed_at=NOW + timedelta(seconds=140))
        self.assertEqual(state.touch_changes(
            NOW + timedelta(seconds=140), seconds=120), 2)
        self.assertLessEqual(len(state.touch_change_times), 2)

    def test_delayed_book_cannot_move_touch_clock_backwards(self):
        state = MarketState("market-one")
        state.observe_book_touch(
            book(.49, .51), observed_at=NOW + timedelta(seconds=10))
        state.observe_book_touch(
            book(.49, .51), observed_at=NOW + timedelta(seconds=20))
        state.observe_book_touch(
            book(.48, .52), observed_at=NOW + timedelta(seconds=15))
        self.assertEqual(state.last_touch, (.49, .51))
        self.assertEqual(
            state.last_touch_changed_at, NOW + timedelta(seconds=10))


if __name__ == "__main__":
    unittest.main()
