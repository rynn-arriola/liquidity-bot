"""Trading Bots card (operator 2026-10-03): Liquidity Trader + Endgame Trader."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.bots_summary import (
    BotsModel, endgame_block, liquidity_block, render_bots_card, text_payload)

NOW = datetime(2026, 10, 3, 18, 50, tzinfo=timezone.utc)


class BotsSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.trader = self.dir / "trader.db"
        c = sqlite3.connect(self.trader)
        c.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, created_at TEXT, market_slug TEXT, cost REAL, "
                  "avg_price REAL, qty REAL, filled_qty REAL, settlement REAL, result REAL, settled_at TEXT, "
                  "game_state TEXT, mode TEXT, status TEXT)")
        rows = [
            ("2026-10-03T16:30:00Z", "aec-setkawoua-gorann-shevik-2026-10-03", .95, None, 10.1, 10.1, 1.0, -9.63,
             "2026-10-03T16:36:00Z", '{"type": "table-tennis"}', "live", "FILLED"),
            ("2026-10-03T16:31:00Z", "aec-cs2-lc-nti-2026-10-03", .98, None, 10.1, 10.1, 0.0, 0.19,
             "2026-10-03T16:48:00Z", '{"type": "esports"}', "live", "FILLED"),
            ("2026-10-03T18:40:00Z", "aec-nhl-tor-mtl-2026-10-03", .97, .97, 10.1, 10.1, None, None, None,
             '{"type": "hockey"}', "live", "FILLED"),
            ("2026-10-03T18:41:00Z", "aec-x-y-z-2026-10-03", .97, None, 10.1, 0, None, None, None,
             "{}", "live", "NOT_FILLED"),
        ]
        c.executemany("INSERT INTO trades(created_at, market_slug, cost, avg_price, qty, filled_qty, settlement, "
                      "result, settled_at, game_state, mode, status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.commit(); c.close()
        self.policy = self.dir / "late_favorite_policy.json"
        self.policy.write_text(json.dumps({"enabled": True, "live_orders": True, "daily_loss_stop_usd": 50,
                                           "min_price": .95, "max_price": .99, "max_spread": .1,
                                           "risk_per_trade_usd": 10, "max_open_positions": 50}))

    def test_endgame_block_counts_today_open_risk_and_recent(self):
        e = endgame_block(self.trader, self.policy, NOW, "python late_favorite_trader.py run")
        self.assertTrue(e.running and e.live)
        self.assertEqual((e.trades, e.won, e.lost), (3, 1, 1))
        self.assertEqual(e.result_today, Decimal("-9.44"))
        self.assertEqual((e.open_positions, e.at_risk), (1, Decimal("9.797")))
        self.assertEqual(e.recent[0][1:], ("hockey", "nhl-tor-mtl", "97¢", "open"))
        self.assertIn("95–99¢ · spread ≤10¢ · $10/trade · max 50 open", e.settings)

    def test_liquidity_block_off_without_database(self):
        cfg = self.dir / "config"; cfg.mkdir()
        shipped = Path(__file__).resolve().parents[1] / "config" / "risk_window_policy.json"
        (cfg / "risk_window_policy.json").write_text(shipped.read_text(encoding="utf-8"))
        b = liquidity_block(self.dir / "missing.db", cfg, NOW, "")
        self.assertFalse(b.running)
        self.assertEqual((b.window, b.window_limit), ("12:00–18:00 ET", Decimal("20")))

    def test_card_and_text_render(self):
        model = BotsModel(NOW, liquidity_block(self.dir / "missing.db", self.dir, NOW, ""),
                          endgame_block(self.trader, self.policy, NOW, "late_favorite_trader.py run"),
                          cash=Decimal("454.78"), buying_power=Decimal("454.78"))
        self.assertTrue(render_bots_card(model).startswith(b"\x89PNG"))
        text = text_payload(model)["embeds"][0]["description"]
        self.assertIn("Endgame Trader: LIVE · today 1-1 -$9.44 · open 1 ($9.80 at risk)", text)

    def test_runner_routine_summary_switch(self):
        from liquidity_bot.commissioning import CommissioningConfig
        self.assertTrue(CommissioningConfig("m", True, True, True).routine_summary)
        shipped = json.loads((Path(__file__).resolve().parents[1] / "config" / "summary_policy.json").read_text())
        self.assertFalse(shipped["runner_routine_summary"])


if __name__ == "__main__":
    unittest.main()
