"""Pool Watch: offered pools by program family, changes and new programs."""
import unittest

from scripts import pool_watch as pw


class PoolWatchTests(unittest.TestCase):
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

    def test_recent_change_alerts(self):
        obs = [("x_day_of_20260901", "day_of", 100, "2026-09-28T01:00:00+00:00"),
               ("x_day_of_20260929", "day_of", 150, "2026-09-29T01:00:00+00:00")]
        rows = pw.summarize(pw.collect(obs, []), today="2026-09-30")
        self.assertEqual((rows[0]["alert"], rows[0]["change"]), ("CHANGED", 0.5))


if __name__ == "__main__":
    unittest.main()
