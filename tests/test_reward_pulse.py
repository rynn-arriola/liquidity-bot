"""Reward Pulse: per-market attribution, weighted normal, five levels, flags."""
import unittest
from datetime import datetime, timedelta, timezone

from scripts import reward_pulse as pulse

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def rested(slug, hours, cid):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=hours)
    return (dict(client_order_id=cid, created_at=start.isoformat(), quote_observation_required=1),
            dict(client_order_id=cid, market_slug=slug, status="EXPIRED", event_at=end.isoformat()))


class RewardPulseTests(unittest.TestCase):
    def test_levels_cover_the_five_bands(self):
        self.assertEqual([pulse.level(r) for r in (2.0, 1.5, 1.2, 0.9, 0.5, 0.1, 0.0, None)],
                         ["Hot", "Hot", "Good", "Average", "Low", "Dead", "Dead", "Unrated"])

    def test_resting_hours_count_only_entry_quotes_by_market(self):
        i1, o1 = rested("aec-cfb-a-b-2026-09-26", 2, "a")
        i2, o2 = rested("aec-cfb-a-b-2026-09-26", 1, "b")
        reducer = dict(client_order_id="r", created_at=NOW.isoformat(), quote_observation_required=0)
        orders = [o1, o2, dict(client_order_id="r", market_slug="aec-cfb-a-b-2026-09-26",
                               status="OPEN", event_at=None)]
        hours = pulse.resting_hours(orders, [i1, i2, reducer], now=NOW)
        self.assertAlmostEqual(hours["aec-cfb-a-b-2026-09-26"], 3.0)

    def test_market_is_graded_on_its_own_day_against_weighted_normal(self):
        hours = {
            "aec-cfb-w1-2026-09-22": 10.0,   # week 1 before 09-26: $1/h, weight 0.5
            "aec-cfb-w2-2026-09-15": 10.0,   # week 2: $3/h, weight 0.25
            "aec-cfb-w3-2026-09-08": 10.0,   # week 3: $3/h, weight 0.25
            "aec-cfb-old-2026-09-01": 10.0,  # outside three weeks: ignored
            "aec-cfb-day-2026-09-26": 4.0,   # graded: $8 -> $2/h
            "aec-cfb-dead-2026-09-26": 3.0,  # graded: unpaid with a real share -> flag
            "aec-cfb-next-2026-09-27": 5.0,  # next-day market: not graded on 09-26
        }
        paid = pulse.payouts([
            dict(market_slug="aec-cfb-w1-2026-09-22", reward="10", status="PAID"),
            dict(market_slug="aec-cfb-w2-2026-09-15", reward="30", status="PAID"),
            dict(market_slug="aec-cfb-w3-2026-09-08", reward="30", status="PAID"),
            dict(market_slug="aec-cfb-old-2026-09-01", reward="500", status="PAID"),
            dict(market_slug="aec-cfb-day-2026-09-26", reward="8", status="PENDING")])
        ev = pulse.evidence([dict(market_slug="aec-cfb-dead-2026-09-26", reward_pool=15,
                                  snapshot_share=0.3, queue_ahead=12)])
        normal = pulse.baseline("2026-09-26", hours, paid)
        # (0.5*10 + 0.25*30 + 0.25*30) / (0.5*10 + 0.25*10 + 0.25*10) = 20/10
        self.assertAlmostEqual(normal["cfb"], 2.0)
        report = pulse.grade_day("2026-09-26", hours, paid, ev)
        by = {m["market"]: m for m in report["markets_detail"]}
        self.assertEqual(set(by), {"aec-cfb-day-2026-09-26", "aec-cfb-dead-2026-09-26"})
        self.assertEqual((by["aec-cfb-day-2026-09-26"]["vs_normal"], by["aec-cfb-day-2026-09-26"]["level"]),
                         (1.0, "Average"))
        self.assertEqual(by["aec-cfb-dead-2026-09-26"]["level"], "Dead")
        self.assertEqual([m["market"] for m in report["flags"]], ["aec-cfb-dead-2026-09-26"])
        self.assertEqual(report["leagues"][0]["unpaid"], 1)
        self.assertTrue(report["pending"])
        # day: $8 over 7 h vs overall normal 2.0 (only cfb history) -> 0.571 -> Low
        self.assertEqual(report["level"], "Low")
        text = pulse.render(report)
        self.assertIn("Reward Pulse — 2026-09-26", text)
        self.assertIn("unpaid despite a real share", text)

    def test_league_without_enough_history_uses_overall_normal(self):
        hours = {"aec-cfb-x-2026-09-20": 30.0, "aec-new-y-2026-09-20": 1.0,
                 "aec-new-z-2026-09-26": 2.0}
        paid = pulse.payouts([dict(market_slug="aec-cfb-x-2026-09-20", reward="30", status="PAID"),
                              dict(market_slug="aec-new-z-2026-09-26", reward="2", status="PAID")])
        report = pulse.grade_day("2026-09-26", hours, paid, {})
        row = report["leagues"][0]
        self.assertEqual((row["league"], row["normal_basis"]), ("new", "overall"))

    def test_never_paid_league_grades_dead_not_unrated(self):
        hours = {"aec-utr-a-2026-09-20": 30.0, "aec-utr-b-2026-09-26": 5.0}
        report = pulse.grade_day("2026-09-26", hours, pulse.payouts([]), {})
        self.assertEqual(report["leagues"][0]["level"], "Dead")
        self.assertEqual(report["markets_detail"][0]["level"], "Dead")

    def test_cron_line_is_marked(self):
        from pathlib import Path
        self.assertIn(pulse.CRON_MARK, pulse.cron_line(Path("/bot")))


if __name__ == "__main__":
    unittest.main()
