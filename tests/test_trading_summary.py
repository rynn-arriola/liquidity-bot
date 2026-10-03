"""Trading summary card (operator 2026-10-03): Liquidity Trader + Endgame Trader."""
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts.reward_table import build_summary_payload, endgame_by_day

DAYS = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
NOW = datetime(2026, 10, 3, 18, 30, tzinfo=timezone.utc)


def trader_db(path: Path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE trades(created_at TEXT, cost REAL, avg_price REAL, qty REAL, filled_qty REAL, "
              "settlement REAL, result REAL, mode TEXT, status TEXT)")
    c.executemany("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?)", rows)
    c.commit(); c.close()


class TradingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "trader.db"
        trader_db(self.db, [
            ("2026-10-03T16:30:00Z", .95, None, 10.1, 10.1, 1.0, 0.28, "live", "FILLED"),
            ("2026-10-03T16:31:00Z", .95, None, 10.1, 10.1, 0.0, -9.63, "live", "FILLED"),
            ("2026-10-03T16:40:00Z", .97, .97, 10.1, 10.1, None, None, "live", "FILLED"),
            ("2026-10-03T16:41:00Z", .97, None, 10.1, 0.0, None, None, "live", "NOT_FILLED"),
            ("2026-10-03T16:01:00Z", .96, None, 10.3, 10.3, None, None, "dry", "FILLED"),
        ])

    def test_endgame_by_day_counts_real_fills_results_and_open_risk(self):
        e = endgame_by_day(DAYS, self.db)
        today = e["days"]["2026-10-03"]
        self.assertEqual((today["trades"], today["won"], today["lost"]), (3, 1, 1))
        self.assertAlmostEqual(float(today["result"]), -9.35)
        self.assertEqual(e["open"], 1)
        self.assertAlmostEqual(float(e["at_risk"]), 9.797, places=3)
        self.assertIsNone(e["days"]["2026-10-02"]["result"])
        missing = endgame_by_day(DAYS, Path(self.tmp.name) / "none.db")
        self.assertFalse(missing["available"])

    def test_payload_layout_totals_and_notes(self):
        payload = build_summary_payload(
            DAYS, paid={"2026-10-01": Decimal("10")}, pending={"2026-10-02": Decimal("5")},
            today=DAYS[-1], generated_at=NOW,
            settled_history={"2026-10-01": Decimal("-4"), "2026-10-02": Decimal("-1"), "2026-10-03": None},
            endgame=endgame_by_day(DAYS, self.db), pools={"ours": {"score": 3.0}},
            running={"Liquidity": False, "Endgame": True})
        embed = payload["embeds"][0]
        self.assertEqual(embed["description"].replace("**", "").splitlines(),
                         ["Liquidity: +10.00", "Endgame: -9.35", "Total: +0.65"])
        liq = embed["fields"][0]["value"].splitlines()
        self.assertEqual(liq[1], "Date Trading Rewards Net")
        self.assertEqual(liq[3:6], ["10/1 -4.00 10.00 +6.00", "10/2 -1.00 5.00 +4.00", "10/3* -- -- --"])
        end = embed["fields"][1]["value"].splitlines()
        self.assertEqual(end[3:-1], ["10/3* 3 1-1 -9.35"])                 # only days it traded
        footer = embed["footer"]["text"]
        self.assertIn("Liquidity OFF · Endgame ON · Endgame open 1 ($9.80 at risk)", footer)
        self.assertIn("3.0/10 - not profitable to farm", footer)

    def test_png_renders_the_same_values(self):
        from scripts.reward_image import render_summary_png
        payload = build_summary_payload(
            DAYS, paid={}, pending={}, today=DAYS[-1], generated_at=NOW,
            settled_history={d.isoformat(): Decimal("1") for d in DAYS},
            endgame=endgame_by_day(DAYS, self.db), running={"Liquidity": True, "Endgame": True})
        png = render_summary_png(payload)
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
