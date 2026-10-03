"""Phone alerts (ntfy) when a bot starts, stops or crashes (operator 2026-10-03)."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import bot_watch
from liquidity_bot import ntfy

NOW = datetime(2026, 10, 3, 19, 30, tzinfo=timezone.utc)
BOTH = "python -m liquidity_bot.live_commissioning\npython late_favorite_trader.py run\n"
ENDGAME_ONLY = "python late_favorite_trader.py run\n"


class BotWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        Path("logs").mkdir()
        self.sent = []

    def notify(self, title, body, **kw):
        self.sent.append((title, body, kw["priority"]))
        return True

    def test_first_run_announces_then_alerts_only_on_changes(self):
        bot_watch.check(ENDGAME_ONLY, NOW, self.notify)
        self.assertEqual([s[0] for s in self.sent], ["Bot alerts on"])
        self.assertIn("Liquidity Trader OFF, Endgame Trader ON", self.sent[0][1])
        bot_watch.check(ENDGAME_ONLY, NOW, self.notify)
        self.assertEqual(len(self.sent), 1)                       # nothing changed
        bot_watch.check(BOTH, NOW, self.notify)
        self.assertEqual(self.sent[-1][0], "Liquidity Trader started")

    def test_endgame_crash_and_watchdog_reasons(self):
        bot_watch.check(ENDGAME_ONLY, NOW, self.notify)
        Path("logs/late-favorite-trader.log").write_text("ok\nTimeout (0:03:00)!\nThread 0x1\n")
        bot_watch.check("", NOW, self.notify)
        title, body, priority = self.sent[-1]
        self.assertEqual((title, priority), ("Endgame Trader CRASHED", 5))
        self.assertIn("watchdog", body)
        bot_watch.check(ENDGAME_ONLY, NOW, self.notify)
        self.assertEqual(self.sent[-1][0], "Endgame Trader started")
        Path("logs/late-favorite-trader.log").write_text("[late-favorite-trader] status ok\n")
        bot_watch.check("", NOW, self.notify)
        self.assertEqual(self.sent[-1][:1], ("Endgame Trader stopped",))

    def test_liquidity_operator_stop_is_not_a_crash(self):
        bot_watch.check(BOTH, NOW, self.notify)
        Path("logs/commissioning.log").write_text('{\n  "stop_reason": "operator_sigterm",\n}\n')
        bot_watch.check(ENDGAME_ONLY, NOW, self.notify)
        title, body, priority = self.sent[-1]
        self.assertEqual((title, priority), ("Liquidity Trader stopped", 4))
        self.assertIn("operator_sigterm", body)

    def test_ntfy_topic_and_send_never_raise(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": ""}):
            self.assertIsNone(ntfy.topic(Path("data/missing")))
            self.assertFalse(ntfy.send("t", "m", topic_name=None))
        Path("data").mkdir()
        Path("data/ntfy-topic").write_text("abc-123\n")
        with patch.dict(os.environ, {"NTFY_TOPIC": ""}):
            self.assertEqual(ntfy.topic(Path("data/ntfy-topic")), "abc-123")
        self.assertFalse(ntfy.send("t", "m", topic_name="x", server="http://127.0.0.1:9", timeout=1))


if __name__ == "__main__":
    unittest.main()
