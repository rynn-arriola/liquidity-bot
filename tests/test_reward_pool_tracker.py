"""Reward Pool Tracker: offered pools by program family, changes and new programs."""
import unittest

from scripts import reward_pool_tracker as pw


class RewardPoolTrackerTests(unittest.TestCase):
    def test_family_strips_date_and_version(self):
        self.assertEqual(pw.family("table_tennis_moneyline_day_of_20260924"), "table_tennis_moneyline_day_of")
        self.assertEqual(pw.family("culture_low_20260924_v2"), "culture_low")
        self.assertEqual(pw.family("itf_moneyline_dayof"), "itf_moneyline_dayof")

    def test_detects_a_cut_and_a_new_program(self):
        obs = [("table_tennis_moneyline_day_of_20260914", "day_of", 35, f"2026-09-{d}T10:00:00+00:00")
               for d in (20, 21, 22, 23)]
        obs += [("table_tennis_moneyline_day_of_20260924", "day_of", 15, f"2026-09-{d}T10:00:00+00:00")
                for d in (24, 25, 26)]
        disc = [dict(at="2026-09-29T12:00:00+00:00", pools=[dict(identity=[
            "nhl_regular_moneyline_day_of_20260929", "day_of", "x", "None", 50.0, 15000.0, 0.25])])]
        rows = pw.summarize(pw.collect(obs, disc), today="2026-09-30")
        by = {r["family"]: r for r in rows}
        tt = by["table_tennis_moneyline_day_of"]
        self.assertEqual((tt["previous"], tt["latest"], tt["changed_on"]), (35.0, 15.0, "2026-09-24"))
        self.assertAlmostEqual(tt["change"], -20 / 35)
        self.assertEqual(tt["alert"], "")                 # older than 2 days
        self.assertEqual(tt["program_id"], "table_tennis_moneyline_day_of_20260924")
        self.assertEqual(by["nhl_regular_moneyline_day_of"]["alert"], "NEW")
        text = pw.render(rows, generated_at="2026-09-30T12:00:00+00:00", today="2026-09-30")
        self.assertIn("**NEW** `nhl_regular_moneyline_day_of`", text)
        self.assertIn("| table_tennis_moneyline_day_of | day_of | $35 | $15 | -57% | 2026-09-24 |", text)

    def test_parses_program_rows_from_the_rewards_page(self):
        html = ('<table><tr class="x"><td><div class="a"><span>▸</span>Table Tennis Moneyline DAY OF</div>'
                '<div class="m">table_tennis_moneyline_day_of_20260924</div></td><td><span>Sports · TT</span></td>'
                '<td><span>Day-of</span></td><td><span><span>$</span><span>15</span></span></td><td>0.50</td>'
                '<td>5,000</td><td>817</td></tr>'
                '<tr class="x"><td><div>ATP WTA Moneyline Live</div><div>atp_wta_moneyline_live_20260924</div></td>'
                '<td>Sports</td><td>Live</td><td>$480</td><td>0.30</td><td>15,000</td><td>93</td></tr></table>')
        rows = pw.parse_site(html)
        self.assertEqual([(r["family"], r["period"], r["pool"], r["markets"]) for r in rows],
                         [("table_tennis_moneyline_day_of", "day_of", 15.0, 817),
                          ("atp_wta_moneyline_live", "live", 480.0, 93)])
        self.assertEqual(rows[1]["name"], "ATP WTA Moneyline Live")

    def test_score_is_now_over_best_in_30_days_and_5_or_under_is_not_profitable(self):
        obs = [("table_tennis_moneyline_day_of_20260914", "day_of", 35, "2026-09-20T10:00:00+00:00"),
               ("itf_moneyline_dayof_20260914", "day_of", 30, "2026-09-20T10:00:00+00:00")]
        current = [dict(program_id="table_tennis_moneyline_day_of_20260924", family="table_tennis_moneyline_day_of",
                        period="day_of", pool=15.0, markets=1),
                   dict(program_id="itf_moneyline_dayof_20260924", family="itf_moneyline_dayof",
                        period="day_of", pool=15.0, markets=1),
                   dict(program_id="nfl_live_20260922", family="nfl_live", period="live", pool=1000.0, markets=1)]
        collected = pw.collect(obs, [], [dict(at="2026-10-01T10:00:00+00:00", programs=current)])
        s = pw.score(current, collected, today="2026-10-01")
        self.assertEqual(s["ours"]["score"], round(10 * 30 / 65, 1))      # 4.6
        self.assertEqual(s["profitable"], "NO")
        self.assertEqual(s["all"]["score"], round(10 * 1030 / 1065, 1))   # nfl has no history: counts at its level
        self.assertEqual([pw.profit_verdict(v) for v in (5.0, 5.1, 7.9, 8.0)], ["NO", "MAYBE", "MAYBE", "YES"])
        rows = pw.summarize(collected, today="2026-10-01")
        self.assertEqual({r["family"]: r["alert"] for r in rows}["nfl_live"], "")   # first rewards-page day: not NEW

    def test_our_score_by_day_follows_the_pool_cut(self):
        obs = []
        for d, tt, itf in (("2026-09-22", 35, 30), ("2026-09-23", 35, 30), ("2026-09-24", 15, 15)):
            obs += [("table_tennis_moneyline_day_of_20260914", "day_of", tt, f"{d}T10:00:00+00:00"),
                    ("itf_moneyline_dayof_20260914", "day_of", itf, f"{d}T10:00:00+00:00")]
        by_day = pw.our_scores_by_day(pw.collect(obs, []))
        self.assertEqual(by_day, {"2026-09-22": 10.0, "2026-09-23": 10.0,
                                  "2026-09-24": round(10 * 30 / 65, 1)})

    def test_recent_change_alerts(self):
        obs = [("x_day_of_20260901", "day_of", 100, "2026-09-28T01:00:00+00:00"),
               ("x_day_of_20260929", "day_of", 150, "2026-09-29T01:00:00+00:00")]
        rows = pw.summarize(pw.collect(obs, []), today="2026-09-30")
        self.assertEqual((rows[0]["alert"], rows[0]["change"]), ("CHANGED", 0.5))


if __name__ == "__main__":
    unittest.main()
