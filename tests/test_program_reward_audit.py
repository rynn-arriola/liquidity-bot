from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

STAGING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGING))

from scripts.program_reward_audit import (  # noqa: E402
    _latest_local_earnings,
    build_audit,
    parse_snapshot,
    render,
)


SCHEMA = """
CREATE TABLE execution_intents (
  intent_id TEXT PRIMARY KEY, operation TEXT, market_slug TEXT,
  client_order_id TEXT, created_at TEXT
);
CREATE TABLE execution_orders (
  client_order_id TEXT PRIMARY KEY, market_slug TEXT, status TEXT, event_at TEXT
);
CREATE TABLE execution_fills (
  execution_id TEXT PRIMARY KEY, market_slug TEXT, intent TEXT,
  qty TEXT, price TEXT, received_at TEXT, fee TEXT, reversed INTEGER DEFAULT 0
);
CREATE TABLE execution_quote_observations (
  client_order_id TEXT PRIMARY KEY, market_slug TEXT, side TEXT,
  observed_at TEXT, observation_json TEXT
);
CREATE TABLE execution_reward_earnings (
  date TEXT, market_slug TEXT, program_type TEXT, status TEXT,
  reward TEXT, observed_at TEXT
);
"""


def observation(
    slug: str,
    *,
    side: str = "bid",
    program: str = "program-a",
    pool: float = 100.0,
    rank: int | None = 1,
    observed_at: str = "2026-09-02T05:00:00+00:00",
) -> str:
    document = {
        "market_slug": slug,
        "side": side,
        "observed_at": observed_at,
        "program_id": program,
        "reward_period": "day-of",
        "reward_pool": pool,
        "discount_factor": 0.3,
        "target_size": 15000,
        "quantity": 10,
        "ticks_from_best": 1,
        "order_score": 10,
        "total_score": 1010,
        "snapshot_share": 10 / 1010,
    }
    if rank is not None:
        document["selection_rank"] = rank
    return json.dumps(document)


class ProgramRewardAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db = Path(handle.name)
        connection = sqlite3.connect(self.db)
        connection.executescript(SCHEMA)
        connection.close()
        self.addCleanup(self.db.unlink, missing_ok=True)

    def add_order(
        self,
        order_id: str,
        slug: str,
        *,
        side: str = "bid",
        program: str = "program-a",
        pool: float = 100.0,
        rank: int | None = 1,
        start: str = "2026-09-02T05:00:00+00:00",
        end: str = "2026-09-02T06:00:00+00:00",
    ) -> None:
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO execution_intents VALUES (?,?,?,?,?)",
            (f"intent-{order_id}", "PLACE", slug, order_id, start))
        connection.execute(
            "INSERT INTO execution_orders VALUES (?,?,?,?)",
            (order_id, slug, "CANCELED", end))
        connection.execute(
            "INSERT INTO execution_quote_observations VALUES (?,?,?,?,?)",
            (order_id, slug, side, start,
             observation(slug, side=side, program=program, pool=pool,
                         rank=rank, observed_at=start)))
        connection.commit()
        connection.close()

    def audit(self, earnings=()):
        return build_audit(
            self.db, earnings, days=1,
            now=datetime(2026, 9, 3, 3, 59, tzinfo=timezone.utc))

    def test_shared_pool_is_not_summed_once_per_market(self):
        self.add_order("one", "market-one")
        self.add_order("two", "market-two")

        result = self.audit()

        self.assertEqual(len(result.programs), 1)
        item = result.programs[0]
        self.assertEqual(item.facts.pools, {100.0})
        self.assertEqual(len(item.facts.markets), 2)
        self.assertAlmostEqual(item.facts.seconds / 3600, 2.0)
        self.assertIn("100.00", render(result))
        self.assertNotIn("200.00", render(result))

    def test_replacement_overlap_counts_one_resting_side(self):
        self.add_order(
            "old", "market-one", start="2026-09-02T05:00:00+00:00",
            end="2026-09-02T06:10:00+00:00")
        self.add_order(
            "new", "market-one", start="2026-09-02T06:00:00+00:00",
            end="2026-09-02T07:00:00+00:00")

        item = self.audit().programs[0]

        self.assertAlmostEqual(item.facts.seconds / 3600, 2.0)

    def test_rank_change_during_replacement_does_not_double_count(self):
        self.add_order(
            "old", "market-one", rank=24,
            start="2026-09-02T05:00:00+00:00",
            end="2026-09-02T06:10:00+00:00")
        self.add_order(
            "new", "market-one", rank=25,
            start="2026-09-02T06:00:00+00:00",
            end="2026-09-02T07:00:00+00:00")

        result = self.audit()

        total = sum(facts.seconds for facts in result.cohorts.values())
        self.assertAlmostEqual(total / 3600, 2.0)
        self.assertAlmostEqual(
            result.cohorts[("2026-09-02", "program-a", "top24")].seconds,
            3600)
        self.assertAlmostEqual(
            result.cohorts[("2026-09-02", "program-a", "slots25plus")].seconds,
            3600)

    def test_interval_is_split_at_et_midnight(self):
        self.add_order(
            "cross", "market-one", start="2026-09-02T03:30:00+00:00",
            end="2026-09-02T04:30:00+00:00")

        result = build_audit(
            self.db, (), days=2,
            now=datetime(2026, 9, 3, 3, 59, tzinfo=timezone.utc))

        by_day = {item.day: item.facts.seconds / 3600 for item in result.programs}
        self.assertAlmostEqual(by_day["2026-09-01"], 0.5)
        self.assertAlmostEqual(by_day["2026-09-02"], 0.5)

    def test_pending_reward_withholds_net(self):
        self.add_order("one", "market-one")
        earnings = [
            {"date": "2026-09-02", "market_slug": "market-one",
             "status": "PAID", "reward": "4.00"},
            {"date": "2026-09-02", "market_slug": "market-one",
             "status": "PENDING", "reward": "6.00"},
        ]

        item = self.audit(earnings).programs[0]

        self.assertEqual(item.paid, 4)
        self.assertEqual(item.pending, 6)
        self.assertIsNone(item.complete_net)
        self.assertIn("--      --", render(self.audit(earnings)))

    def test_virtual_width_cohorts_use_recorded_rank(self):
        self.add_order("core", "market-core", rank=24)
        self.add_order("extra", "market-extra", rank=25)
        self.add_order("old", "market-old", rank=None)

        result = self.audit()

        self.assertAlmostEqual(
            result.cohorts[("2026-09-02", "program-a", "top24")].seconds,
            3600)
        self.assertAlmostEqual(
            result.cohorts[("2026-09-02", "program-a", "slots25plus")].seconds,
            3600)
        self.assertAlmostEqual(
            result.cohorts[("2026-09-02", "program-a", "rank_unknown")].seconds,
            3600)
        self.assertEqual(result.observations_missing_rank, 1)

    def test_earnings_without_unique_program_mapping_are_explicit(self):
        self.add_order("known", "known-market")
        self.add_order("a", "ambiguous", program="program-a")
        self.add_order("b", "ambiguous", program="program-b", side="offer")
        earnings = [
            {"date": "2026-09-02", "market_slug": "unknown",
             "status": "PAID", "reward": "2"},
            {"date": "2026-09-02", "market_slug": "ambiguous",
             "status": "PAID", "reward": "3"},
        ]

        result = self.audit(earnings)

        self.assertEqual(result.unmapped_earnings, 2)
        self.assertEqual(result.ambiguous_earnings, 3)

    def test_earnings_outside_requested_window_are_ignored(self):
        self.add_order("known", "known-market")
        earnings = [{
            "date": "2026-08-01", "market_slug": "unknown",
            "status": "PAID", "reward": "999",
        }]

        result = self.audit(earnings)

        self.assertEqual(result.unmapped_earnings, 0)

    def test_balanced_pair_trading_pnl_joins_program(self):
        self.add_order("one", "market-one")
        connection = sqlite3.connect(self.db)
        connection.executemany(
            "INSERT INTO execution_fills VALUES (?,?,?,?,?,?,?,?)",
            [
                ("fill-long", "market-one", "ORDER_INTENT_BUY_LONG",
                 "10", ".48", "2026-09-02T05:30:00+00:00", ".01", 0),
                ("fill-short", "market-one", "ORDER_INTENT_BUY_SHORT",
                 "10", ".49", "2026-09-02T05:31:00+00:00", ".01", 0),
            ])
        connection.commit()
        connection.close()

        item = self.audit().programs[0]

        self.assertEqual(item.trading, Decimal("0.080"))

    def test_trading_pnl_is_grouped_by_each_fill_et_day(self):
        self.add_order(
            "day-one", "market-one",
            start="2026-09-01T05:00:00+00:00",
            end="2026-09-01T06:00:00+00:00")
        self.add_order("day-two", "market-one")
        connection = sqlite3.connect(self.db)
        connection.executemany(
            "INSERT INTO execution_fills VALUES (?,?,?,?,?,?,?,?)",
            [
                ("d1-long", "market-one", "ORDER_INTENT_BUY_LONG",
                 "10", ".48", "2026-09-01T05:30:00+00:00", ".01", 0),
                ("d1-short", "market-one", "ORDER_INTENT_BUY_SHORT",
                 "10", ".49", "2026-09-01T05:31:00+00:00", ".01", 0),
                ("d2-long", "market-one", "ORDER_INTENT_BUY_LONG",
                 "10", ".40", "2026-09-02T05:30:00+00:00", ".01", 0),
                ("d2-short", "market-one", "ORDER_INTENT_BUY_SHORT",
                 "10", ".50", "2026-09-02T05:31:00+00:00", ".01", 0),
            ])
        connection.commit()
        connection.close()

        result = build_audit(
            self.db, (), days=2,
            now=datetime(2026, 9, 3, 3, 59, tzinfo=timezone.utc))

        by_day = {item.day: item.trading for item in result.programs}
        self.assertEqual(by_day["2026-09-01"], Decimal("0.080"))
        self.assertEqual(by_day["2026-09-02"], Decimal("0.980"))

    def test_latest_local_snapshot_drops_stale_pending(self):
        connection = sqlite3.connect(self.db)
        connection.row_factory = sqlite3.Row
        connection.executemany(
            "INSERT INTO execution_reward_earnings VALUES (?,?,?,?,?,?)",
            [
                ("2026-09-02", "market-one", "liquidityProgram", "PENDING",
                 "3", "2026-09-03T01:00:00+00:00"),
                ("2026-09-02", "market-one", "liquidityProgram", "PAID",
                 "4", "2026-09-03T02:00:00+00:00"),
            ])

        rows = _latest_local_earnings(connection)
        connection.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PAID")

    def test_parser_rejects_missing_program(self):
        row = {
            "market_slug": "market-one", "side": "bid",
            "observed_at": "2026-09-02T05:00:00+00:00",
            "observation_json": json.dumps({"reward_pool": 100}),
        }
        self.assertIsNone(parse_snapshot(row))


if __name__ == "__main__":
    unittest.main()
