"""Late-favourite tracker: parsing the live event listing and scoring held favourites."""
import unittest

from liquidity_bot.late_favorite import favourite_entry, parse_event, summarize, trade_result

EVENT = {
    "slug": "cfb-mphs-charlt-2026-10-03", "title": "Memphis vs. Charlotte", "startTime": "2026-10-03T15:00:00Z",
    "seriesSlug": "cfb-2026", "score": "0-0", "period": "Q1", "live": True, "ended": False,
    "eventState": {"type": "football", "score": "14-3", "elapsed": "08:10", "period": "Q4", "live": True,
                   "ended": False, "periodScores": [
                       {"label": "Q1", "scores": [{"score": 7}, {"score": 0}]},
                       {"label": "Q4", "scores": [{"score": 7}, {"score": 3}]}]},
    "markets": [
        {"slug": "aec-cfb-mphs-charlt-2026-10-03", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
         "sportsMarketType": "football_team_full_game_winner", "outcomes": '["Tigers","49ers"]',
         "feeCoefficient": 0.0695, "orderPriceMinTickSize": 0.005, "status": "MARKET_STATUS_OPEN",
         "bestBidQuote": {"value": "0.9100"}, "bestAskQuote": {"value": "0.9150"}},
        {"slug": "broken"},
        "not a market",
    ],
}


class LateFavouriteTests(unittest.TestCase):
    def test_parse_live_event_prefers_event_state(self):
        ev, quotes = parse_event(EVENT)
        self.assertEqual((ev.sport, ev.period, ev.score, ev.live, ev.ended), ("football", "Q4", "14-3", True, False))
        self.assertEqual(ev.period_scores, '[["Q1",[7,0]],["Q4",[7,3]]]')
        q = quotes[0]
        self.assertEqual((q.best_bid, q.best_ask, q.fee_coefficient, q.tick), (0.91, 0.915, 0.0695, 0.005))
        self.assertEqual(quotes[1].best_bid, None)
        self.assertEqual(len(quotes), 2)
        self.assertEqual(parse_event({"no": "slug"}), (None, []))

    def test_favourite_entry_buys_long_at_ask_or_short_at_one_minus_bid(self):
        self.assertEqual(favourite_entry(0.91, 0.92, 0.90), ("long", 0.92))
        self.assertEqual(favourite_entry(0.07, 0.08, 0.90), ("short", 0.93))
        self.assertIsNone(favourite_entry(0.50, 0.52, 0.90))
        self.assertIsNone(favourite_entry(None, 1.0, 0.90))

    def test_trade_result_includes_taker_fee(self):
        self.assertAlmostEqual(trade_result("long", 0.90, 1.0, 0.06), 0.10 - 0.06 * 0.9 * 0.1)
        self.assertAlmostEqual(trade_result("short", 0.90, 1.0, None), -0.90 - 0.06 * 0.9 * 0.1)
        self.assertAlmostEqual(trade_result("short", 0.95, 0.0, 0.0695), 0.05 - 0.0695 * 0.95 * 0.05)

    def test_summarize_counts_first_crossing_per_stage(self):
        rows = [
            {"market_slug": "a", "sport": "tennis", "market_type": "MONEYLINE", "settlement": 1.0, "fee": 0.06,
             "obs": [("t1", "S1", 0.80, 0.82, 60), ("t2", "S3", 0.90, 0.91, 4), ("t3", "S3", 0.95, 0.96, 1)]},
            {"market_slug": "b", "sport": "tennis", "market_type": "MONEYLINE", "settlement": 1.0, "fee": 0.06,
             "obs": [("t1", "S3", 0.05, 0.06, 3)]},          # short favourite at 0.95 -> loses
        ]
        table = summarize(rows, thresholds=(0.90,))
        s3 = next(r for r in table if r["stage_kind"] == "period" and r["stage"] == "S3")
        self.assertEqual((s3["trades"], s3["won"]), (2, 1))
        late = next(r for r in table if r["stage_kind"] == "minutes_before_end" and r["stage"] == "<=5")
        self.assertEqual(late["trades"], 2)
        self.assertAlmostEqual(s3["avg_price"], (0.91 + 0.95) / 2)


class TrackerCronTests(unittest.TestCase):
    def test_cron_keeps_it_running_from_the_bot_directory(self):
        from pathlib import Path
        from scripts import late_favorite_tracker as t
        run, rep = t.cron_lines(Path("/bot"), Path("/code"))
        self.assertTrue(run.startswith("*/10 * * * * cd /bot && flock -n data/late-favorite/cron.lock "))
        self.assertIn("/code/scripts/late_favorite_tracker.py run", run)
        self.assertIn("late_favorite_tracker.py report", rep)


if __name__ == "__main__":
    unittest.main()
