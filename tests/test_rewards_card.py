"""Rewards card (operator 2026-10-03: "change the reward discord summary so it looks better")."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from scripts.reward_table import build_rewards_payload

DAYS = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
NOW = datetime(2026, 10, 3, 19, tzinfo=timezone.utc)


class RewardsCardTests(unittest.TestCase):
    def test_totals_daily_rows_and_notes(self):
        payload = build_rewards_payload(
            DAYS, paid={"2026-10-01": Decimal("10")}, pending={"2026-10-02": Decimal("5")},
            today=DAYS[-1], generated_at=NOW,
            settled_history={"2026-10-01": Decimal("-4"), "2026-10-02": Decimal("-1"), "2026-10-03": None},
            pools={"ours": {"score": 3.0}})
        embed = payload["embeds"][0]
        self.assertEqual(embed["description"].replace("**", "").splitlines(),
                         ["Trading: -5.00", "Rewards: +15.00", "Net: +10.00"])
        rows = embed["fields"][0]["value"].splitlines()
        self.assertEqual(rows[1], "Date Trading Paid Pending Net")
        self.assertEqual(rows[3:6], ["10/1 -4.00 10.00 posting +6.00", "10/2 -1.00 0.00 5.00 +4.00",
                                     "10/3* -- -- -- --"])
        self.assertIn("3.0/10 - not profitable to farm", embed["footer"]["text"])

    def test_png(self):
        from scripts.reward_image import render_summary_png
        payload = build_rewards_payload(DAYS, paid={}, pending={}, today=DAYS[-1], generated_at=NOW,
                                        settled_history={d.isoformat(): Decimal("1") for d in DAYS})
        self.assertTrue(render_summary_png(payload).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
