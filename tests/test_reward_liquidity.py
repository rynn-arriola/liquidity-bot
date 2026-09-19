from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.reward_liquidity import resting_period_hours_by_day


class RewardLiquidityTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="liquidity-reward-period-"))
        self.database = folder / "ledger.db"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE execution_intents ("
                "client_order_id TEXT, operation TEXT, created_at TEXT)")
            connection.execute(
                "CREATE TABLE execution_orders ("
                "client_order_id TEXT, market_slug TEXT, side TEXT, "
                "status TEXT, event_at TEXT)")
            connection.execute(
                "CREATE TABLE execution_quote_observations ("
                "client_order_id TEXT, market_slug TEXT, side TEXT, "
                "observation_json TEXT)")

    def _order(self, client_id: str, market: str, side: str, start: str,
               end: str, reward_period: str | None, *, modified: bool = False):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO execution_intents VALUES (?,?,?)",
                (client_id, "PLACE", start))
            if modified:
                connection.execute(
                    "INSERT INTO execution_intents VALUES (?,?,?)",
                    (client_id, "MODIFY", start))
            connection.execute(
                "INSERT INTO execution_orders VALUES (?,?,?,?,?)",
                (client_id, market, side, "CANCELED", end))
            if reward_period is not None:
                normalized_side = "bid" if "BUY" in side else "offer"
                connection.execute(
                    "INSERT INTO execution_quote_observations VALUES (?,?,?,?)",
                    (client_id, market, normalized_side, json.dumps({
                        "market_slug": market,
                        "side": normalized_side,
                        "observed_at": "2026-09-18T03:59:00+00:00",
                        "reward_period": reward_period,
                    })))

    def test_exact_periods_and_missing_evidence_are_kept_separate(self):
        self._order("day", "market-day", "ORDER_SIDE_BUY",
                    "2026-09-18T04:00:00+00:00",
                    "2026-09-18T06:00:00+00:00", "day_of")
        self._order("daily", "market-daily", "ORDER_SIDE_SELL",
                    "2026-09-18T05:00:00+00:00",
                    "2026-09-18T08:00:00+00:00", "daily_event")
        self._order("unknown", "market-unknown", "ORDER_SIDE_BUY",
                    "2026-09-18T06:00:00+00:00",
                    "2026-09-18T07:30:00+00:00", None)
        self._order("modified", "market-modified", "ORDER_SIDE_BUY",
                    "2026-09-18T08:00:00+00:00",
                    "2026-09-18T09:00:00+00:00", "day_of",
                    modified=True)

        result = resting_period_hours_by_day(
            str(self.database), [date(2026, 9, 18)],
            now=datetime(2026, 9, 19, 4, tzinfo=timezone.utc))

        self.assertEqual(result["2026-09-18"], {
            "day_of": 2.0,
            "daily_event": 3.0,
            "unallocated": 2.5,
        })

    def test_conflicting_overlap_is_unallocated_not_double_counted(self):
        self._order("daily", "same-market", "ORDER_SIDE_BUY",
                    "2026-09-18T04:00:00+00:00",
                    "2026-09-18T06:00:00+00:00", "daily_event")
        self._order("day", "same-market", "ORDER_SIDE_BUY",
                    "2026-09-18T05:00:00+00:00",
                    "2026-09-18T07:00:00+00:00", "day_of")

        result = resting_period_hours_by_day(
            str(self.database), [date(2026, 9, 18)],
            now=datetime(2026, 9, 19, 4, tzinfo=timezone.utc))

        self.assertEqual(result["2026-09-18"], {
            "day_of": 1.0,
            "daily_event": 1.0,
            "unallocated": 1.0,
        })

    def test_missing_observation_table_is_unknown_not_zero(self):
        self._order("old", "old", "BUY", "2026-09-18T04:00:00+00:00",
                    "2026-09-18T05:00:00+00:00", None)
        with sqlite3.connect(self.database) as c:
            c.execute("DROP TABLE execution_quote_observations")
        row = self._read()["2026-09-18"]
        self.assertEqual(row, dict(day_of=0.0, daily_event=0.0, unallocated=1.0))

    def _read(self, days=None, now=None):
        return resting_period_hours_by_day(str(self.database), days or [date(2026, 9, 18)],
            now=now or datetime(2026, 9, 19, 4, tzinfo=timezone.utc))

    def test_same_period_overlap_merges_and_opposite_sides_add(self):
        for ident, side, start, end in [
                ("one", "BUY", 4, 6), ("two", "BUY", 5, 7), ("three", "SELL", 4, 5)]:
            self._order(ident, "same", side, f"2026-09-18T{start:02}:00:00+00:00",
                        f"2026-09-18T{end:02}:00:00+00:00", "day_of")
        self.assertEqual(self._read()["2026-09-18"]["day_of"], 4.0)

    def test_open_order_clips_to_now_and_splits_midnight(self):
        self._order("one", "same", "BUY", "2026-09-18T23:00:00-04:00",
                    "2026-09-19T06:00:00+00:00", "day_of")
        with sqlite3.connect(self.database) as c:
            c.execute("UPDATE execution_orders SET status='OPEN'")
        rows = self._read([date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20)],
                          datetime(2026, 9, 19, 5, tzinfo=timezone.utc))
        self.assertEqual([rows[d]["day_of"] for d in rows], [1.0, 1.0, 0.0])

    def test_dst_day_uses_elapsed_utc_hours(self):
        self._order("one", "same", "BUY", "2026-11-01T00:00:00-04:00",
                    "2026-11-02T00:00:00-05:00", "day_of")
        rows = self._read([date(2026, 11, 1)], datetime(2026, 11, 2, 6, tzinfo=timezone.utc))
        self.assertEqual(rows["2026-11-01"]["day_of"], 25.0)

    def test_rejected_future_and_reversed_intervals_do_not_count(self):
        self._order("reject", "a", "BUY", "2026-09-18T04:00:00+00:00",
                    "2026-09-18T05:00:00+00:00", "day_of")
        self._order("future", "b", "BUY", "2026-09-20T04:00:00+00:00",
                    "2026-09-20T05:00:00+00:00", "day_of")
        self._order("reverse", "c", "BUY", "2026-09-18T05:00:00+00:00",
                    "2026-09-18T04:00:00+00:00", "day_of")
        with sqlite3.connect(self.database) as c:
            c.execute("UPDATE execution_orders SET status='REJECTED' WHERE client_order_id='reject'")
        self.assertEqual(sum(self._read()["2026-09-18"].values()), 0)

    def test_bad_identity_json_and_future_observation_are_unallocated(self):
        self._order("one", "same", "BUY", "2026-09-18T04:00:00+00:00",
                    "2026-09-18T05:00:00+00:00", "day_of")
        good = dict(market_slug="same", side="bid", observed_at="2026-09-18T03:59:00Z", reward_period="day_of")
        for bad in ["broken", "null", "[]", json.dumps(dict(good, market_slug="other")),
                    json.dumps(dict(good, side="offer")),
                    json.dumps(dict(good, observed_at="2026-09-18T05:00:01Z")),
                    json.dumps(dict(good, reward_period="other"))]:
            with self.subTest(bad=bad), sqlite3.connect(self.database) as c:
                c.execute("UPDATE execution_quote_observations SET observation_json=?", (bad,))
            self.assertEqual(self._read()["2026-09-18"]["unallocated"], 1)

    def test_atomic_observation_after_intent_timestamp_keeps_period(self):
        self._order("one", "same", "BUY", "2026-09-18T04:00:00+00:00",
                    "2026-09-18T05:00:00+00:00", "day_of")
        with sqlite3.connect(self.database) as c:
            doc = json.loads(c.execute("SELECT observation_json FROM execution_quote_observations").fetchone()[0])
            doc["observed_at"] = "2026-09-18 04:00:00.004693+00:00"
            c.execute("UPDATE execution_quote_observations SET observation_json=?", (json.dumps(doc),))
        self.assertEqual(self._read()["2026-09-18"]["day_of"], 1.0)

    def test_empty_readonly_and_naive_clock(self):
        before = self.database.read_bytes()
        self.assertEqual(resting_period_hours_by_day(str(self.database), [],
                         now=datetime.now(timezone.utc)), {})
        self._read()
        self.assertEqual(self.database.read_bytes(), before)
        with self.assertRaises(ValueError):
            self._read(now=datetime(2026, 9, 19))


if __name__ == "__main__":
    unittest.main()
