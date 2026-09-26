"""Post-fill markout sampler: no backfill, due horizons, stale/late books, bounds."""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.fill_markouts import FillMarkoutSampler
from liquidity_bot.models import BookLevel, BookSnapshot

T0 = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def book(bid=0.44, offer=0.46):
    return BookSnapshot(slug="m", bids=(BookLevel(bid, 100),), offers=(BookLevel(offer, 100),))


class FillMarkoutTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE execution_fills(execution_id, client_order_id, market_slug,"
                        " side, price, qty, liquidity, intent, event_at, reversed)")
        self.query = lambda sql, params=(): self.db.execute(sql, params).fetchall()
        self.records = []
        self.books = {"m": (book(), T0)}

    def add_fill(self, eid, at, *, intent="ORDER_INTENT_BUY_LONG", reversed_=0):
        self.db.execute("INSERT INTO execution_fills VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (eid, "c-" + eid, "m", "ORDER_SIDE_BUY", "0.44", "15", "MAKER",
                         intent, at.isoformat(), reversed_))

    def poll(self, sampler, now):
        sampler.poll(self.query, now, self.books.get, self.records.append)

    def test_history_is_not_backfilled_and_new_fills_sample_each_horizon(self):
        self.add_fill("old", T0 - timedelta(hours=1))
        sampler = FillMarkoutSampler()
        self.poll(sampler, T0)
        self.assertEqual(sampler.pending, [])
        self.add_fill("new", T0)
        self.add_fill("busted", T0, reversed_=1)
        self.poll(sampler, T0 + timedelta(seconds=2))
        self.assertEqual(len(sampler.pending), 3)
        self.books["m"] = (book(0.47, 0.49), T0 + timedelta(seconds=29))
        self.poll(sampler, T0 + timedelta(seconds=31))
        (first,) = self.records
        self.assertEqual((first["execution_id"], first["horizon_seconds"], first["role"]),
                         ("new", 30, "entry"))
        self.assertAlmostEqual(first["mid"], 0.48)
        self.assertAlmostEqual(first["late_seconds"], 1.0)

    def test_stale_or_missing_book_and_late_samples_record_no_mid(self):
        sampler = FillMarkoutSampler(horizons=(30,))
        self.poll(sampler, T0)
        self.add_fill("f", T0, intent="ORDER_INTENT_SELL_LONG")
        self.poll(sampler, T0 + timedelta(seconds=2))
        self.books["m"] = (book(), T0)  # 31 s old at sampling time
        self.poll(sampler, T0 + timedelta(seconds=31))
        self.assertIsNone(self.records[-1]["mid"])
        self.assertEqual(self.records[-1]["role"], "exit")
        self.add_fill("late", T0)  # observed only after 30 s + 61 s
        self.poll(sampler, T0 + timedelta(seconds=91))
        self.assertIsNone(self.records[-1]["mid"])
        self.assertGreater(self.records[-1]["late_seconds"], 60)

    def test_poll_cadence_and_pending_bound(self):
        sampler = FillMarkoutSampler(horizons=(600,), max_pending=2)
        self.poll(sampler, T0)
        for n in range(3):
            self.add_fill(f"f{n}", T0 + timedelta(seconds=n))
        self.poll(sampler, T0 + timedelta(seconds=1))  # inside the 2 s cadence
        self.assertEqual(sampler.pending, [])
        self.poll(sampler, T0 + timedelta(seconds=3))
        self.assertEqual(len(sampler.pending), 2)
        self.assertEqual(sampler.dropped, 1)
        self.assertEqual({item[2]["execution_id"] for item in sampler.pending}, {"f1", "f2"})


if __name__ == "__main__":
    unittest.main()
