from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts import dashboard
from scripts.daily_pnl import _unpaid_reward_display, trading_by_day


class DailyPnlRewardCalendarTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="liquidity-daily-pnl-"))
        self.database = folder / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.execute(
            """CREATE TABLE execution_fills (
                market_slug TEXT NOT NULL,
                intent TEXT,
                qty TEXT NOT NULL,
                price TEXT NOT NULL,
                received_at TEXT NOT NULL,
                event_at TEXT NOT NULL
            )""")
        connection.executemany(
            "INSERT INTO execution_fills VALUES (?,?,?,?,?,?)",
            [
                ("before-et-midnight", "ORDER_INTENT_BUY_LONG", "1", "0.40",
                 "2026-08-22T03:59:59+00:00", "2026-08-22T03:59:59+00:00"),
                ("before-et-midnight", "ORDER_INTENT_BUY_SHORT", "1", "0.50",
                 "2026-08-22T03:59:59+00:00", "2026-08-22T03:59:59+00:00"),
                ("at-et-midnight", "ORDER_INTENT_BUY_LONG", "1", "0.40",
                 "2026-08-22T04:00:00+00:00", "2026-08-22T04:00:00+00:00"),
                ("at-et-midnight", "ORDER_INTENT_BUY_SHORT", "1", "0.50",
                 "2026-08-22T04:00:00+00:00", "2026-08-22T04:00:00+00:00"),
            ])
        connection.commit()
        connection.close()

    def test_trading_uses_the_same_et_dates_as_venue_rewards(self):
        pnl, pairs, unbalanced = trading_by_day(
            str(self.database), "2026-08-21")
        self.assertEqual(unbalanced, 0)
        self.assertEqual(float(pairs["2026-08-21"]), 1.0)
        self.assertEqual(float(pairs["2026-08-22"]), 1.0)
        self.assertAlmostEqual(float(pnl["2026-08-21"]), 0.1)
        self.assertAlmostEqual(float(pnl["2026-08-22"]), 0.1)

    def test_dashboard_et_day_excludes_the_previous_utc_evening(self):
        original = dashboard.DB
        dashboard.DB = str(self.database)
        try:
            result = dashboard._today_trading(
                datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc))
        finally:
            dashboard.DB = original
        self.assertAlmostEqual(result, -0.1)

    def test_dashboard_names_the_boundary_it_uses(self):
        self.assertIn("ET DAY", dashboard._pnl_strip(0, 0))

    def test_all_pending_reward_prints_its_amount(self):
        amount, note = _unpaid_reward_display(Decimal("9.22"))
        self.assertEqual(amount.strip(), "9.22")
        self.assertEqual(note, "all PENDING, net withheld")


if __name__ == "__main__":
    unittest.main()
