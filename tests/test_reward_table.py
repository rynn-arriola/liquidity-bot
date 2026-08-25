from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts.reward_table import (
    discord_message, render_table, resting_side_hours_by_day)


class RewardTableTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="liquidity-reward-table-"))
        self.database = folder / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.execute(
            "CREATE TABLE execution_intents ("
            "client_order_id TEXT, operation TEXT, created_at TEXT)")
        connection.execute(
            "CREATE TABLE execution_orders ("
            "client_order_id TEXT, market_slug TEXT, side TEXT, "
            "status TEXT, event_at TEXT)")
        connection.executemany(
            "INSERT INTO execution_intents VALUES (?,?,?)",
            [
                ("one", "PLACE", "2026-08-24T03:00:00+00:00"),
                ("two", "PLACE", "2026-08-24T04:30:00+00:00"),
                ("three", "PLACE", "2026-08-25T04:00:00+00:00"),
            ])
        connection.executemany(
            "INSERT INTO execution_orders VALUES (?,?,?,?,?)",
            [
                ("one", "market-one", "ORDER_SIDE_BUY", "CANCELED",
                 "2026-08-24T05:00:00+00:00"),
                # Same market/side overlaps `one` by 30 minutes. The report
                # must merge the coverage instead of counting 2.5 hours.
                ("two", "market-one", "ORDER_SIDE_BUY", "CANCELED",
                 "2026-08-24T06:00:00+00:00"),
                ("three", "market-two", "ORDER_SIDE_SELL", "OPEN",
                 "2026-08-25T04:00:00+00:00"),
            ])
        connection.commit()
        connection.close()

    def test_resting_hours_split_on_et_days_and_merge_overlap(self):
        days = [date(2026, 8, 24), date(2026, 8, 25)]
        hours, elapsed = resting_side_hours_by_day(
            str(self.database), days,
            now=datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc))

        self.assertAlmostEqual(hours["2026-08-24"], 2.0)
        self.assertAlmostEqual(hours["2026-08-25"], 2.0)
        self.assertAlmostEqual(elapsed["2026-08-24"], 24.0)
        self.assertAlmostEqual(elapsed["2026-08-25"], 2.0)

    def test_table_never_counts_pending_or_unposted_reward_as_net(self):
        days = [date(2026, 8, 23), date(2026, 8, 24), date(2026, 8, 25)]
        output = render_table(
            days,
            pnl={day.isoformat(): Decimal("-10") for day in days},
            paid={
                "2026-08-23": Decimal("15"),
                "2026-08-24": Decimal("5"),
            },
            pending={"2026-08-24": Decimal("20")},
            resting_hours={day.isoformat(): 24.0 for day in days},
            elapsed_hours={day.isoformat(): 24.0 for day in days},
            today=date(2026, 8, 25),
        )

        self.assertIn("| 2026-08-23 | $-10.00 | $15.00 | $0.00 | **+5.00**", output)
        self.assertIn("| 2026-08-24 | $-10.00 | $5.00 | $20.00 | Pending", output)
        self.assertIn("| 2026-08-25* | $-10.00 | Not posted | Not posted | Pending", output)
        self.assertIn("Finalized net (complete reward days only): **$+5.00**", output)

    def test_discord_table_is_complete_and_within_the_message_limit(self):
        days = [date(2026, 8, 19 + offset) for offset in range(7)]
        table = render_table(
            days,
            pnl={day.isoformat(): Decimal("-10") for day in days},
            paid={day.isoformat(): Decimal("15") for day in days},
            pending={},
            resting_hours={day.isoformat(): 24.0 for day in days},
            elapsed_hours={day.isoformat(): 24.0 for day in days},
            today=date(2026, 8, 25),
        )

        message = discord_message(
            table,
            generated_at=datetime(2026, 8, 25, 17, tzinfo=timezone.utc),
            unbalanced_markets=30)

        self.assertLessEqual(len(message), 2000)
        self.assertIn("7-Day Reward Table (ET)", message)
        self.assertIn("2026-08-19", message)
        self.assertIn("2026-08-25*", message)
        self.assertIn("Pending reward is never counted", message)
        self.assertIn("Generated 2026-08-25 13:00 EDT", message)
        self.assertIn("30 unbalanced market(s)", message)

    def test_discord_post_refuses_to_silently_truncate_a_table(self):
        with self.assertRaisesRegex(ValueError, "2,000-character cap"):
            discord_message("x" * 2000)

    def test_hourly_wrapper_is_bounded_locked_and_lower_priority(self):
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "post_hourly_reward_table.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("flock -n", wrapper)
        self.assertIn("timeout 90s", wrapper)
        self.assertIn("nice -n 10", wrapper)
        self.assertIn("--days 7 --post-discord --quiet", wrapper)
        self.assertNotIn("discord.com/api/webhooks", wrapper)


if __name__ == "__main__":
    unittest.main()
