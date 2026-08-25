from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from scripts.reward_table import (
    build_discord_payload, render_table, resting_side_hours_by_day)


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

    def test_discord_embed_contains_two_aligned_visual_tables(self):
        days = [date(2026, 8, 19 + offset) for offset in range(7)]
        payload = build_discord_payload(
            days,
            pnl={day.isoformat(): Decimal("-10") for day in days},
            paid={day.isoformat(): Decimal("15") for day in days},
            pending={},
            resting_hours={day.isoformat(): 24.0 for day in days},
            elapsed_hours={day.isoformat(): 24.0 for day in days},
            today=date(2026, 8, 25),
            generated_at=datetime(2026, 8, 25, 17, tzinfo=timezone.utc),
            unbalanced_markets=30)

        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Liquidity Bot · Last 7 ET Days")
        self.assertEqual(len(embed["fields"]), 2)
        pnl_table = embed["fields"][0]["value"]
        resting_table = embed["fields"][1]["value"]
        self.assertIn("Date ET        Trading", pnl_table)
        self.assertIn("2026-08-19     $-10.00", pnl_table)
        self.assertIn("2026-08-25*", pnl_table)
        self.assertIn("$+5.00", pnl_table)
        self.assertIn("Side-hours  Avg sides", resting_table)
        self.assertIn("2026-08-25*       24.00       1.00", resting_table)
        self.assertNotIn("|", pnl_table)
        pnl_lines = pnl_table.splitlines()[1:-1]
        resting_lines = resting_table.splitlines()[1:-1]
        self.assertEqual({len(line) for line in pnl_lines}, {53})
        self.assertEqual({len(line) for line in resting_lines}, {34})
        self.assertIn("**Finalized net:** $+35.00", embed["description"])
        self.assertIn("Generated 2026-08-25 13:00 EDT", embed["footer"]["text"])
        self.assertIn("30 unbalanced market(s)", embed["footer"]["text"])

    def test_discord_post_refuses_more_days_than_the_visual_layout_allows(self):
        days = [date(2026, 8, 18) + timedelta(days=offset)
                for offset in range(8)]
        with self.assertRaisesRegex(ValueError, "at most 7"):
            build_discord_payload(
                days, pnl={}, paid={}, pending={}, resting_hours={},
                elapsed_hours={}, today=date(2026, 8, 25),
                generated_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
                unbalanced_markets=0)

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
