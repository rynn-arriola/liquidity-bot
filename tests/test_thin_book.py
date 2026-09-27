"""BC-2026-09-27-A: skip new entries on thin one-tick books in listed leagues."""
import json
import tempfile
import unittest
from pathlib import Path

from liquidity_bot.thin_book import (
    THIN_BOOK_BLOCKER, ThinBookPolicy, load_thin_book_policy)
import test_live_commissioning as live

POLICY = ThinBookPolicy(True, 0.01, 50.0, frozenset({"itfme", "cs2"}))


class ThinBookPolicyTests(unittest.TestCase):
    def test_skips_only_new_entries_on_thin_one_tick_books_in_listed_leagues(self):
        slug = "aec-itfme-a-b-2026-09-27"
        self.assertEqual(POLICY.blocker(slug=slug, spread=0.01, queue_ahead=49,
                                        already_resting=False), THIN_BOOK_BLOCKER)
        for kwargs in (dict(spread=0.02, queue_ahead=10),          # two-cent book
                       dict(spread=0.01, queue_ahead=50),          # enough ahead
                       dict(spread=0.01, queue_ahead=10, already_resting=True)):
            args = dict(slug=slug, already_resting=False) | kwargs
            self.assertIsNone(POLICY.blocker(**args), kwargs)
        self.assertIsNone(POLICY.blocker(slug="aec-setkameua-a-b-2026-09-27", spread=0.01,
                                         queue_ahead=1, already_resting=False))

    def test_disabled_or_missing_file_is_the_old_behaviour(self):
        off = ThinBookPolicy(False, 0.01, 50.0, frozenset({"itfme"}))
        self.assertIsNone(off.blocker(slug="aec-itfme-a-b-2026-09-27", spread=0.01,
                                      queue_ahead=1, already_resting=False))
        self.assertFalse(load_thin_book_policy(Path("/nonexistent/thin.json")).enabled)

    def test_loader_is_strict_and_shipped_file_matches_the_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "p.json"
            for raw in ({"enabled": True}, {"enabled": "yes", "max_spread": 0.01,
                                             "min_queue_ahead": 50, "leagues": []},
                        {"enabled": True, "max_spread": 0.01, "min_queue_ahead": 50,
                         "leagues": ["ITF"]}):
                bad.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_thin_book_policy(bad)
        shipped = load_thin_book_policy()
        self.assertTrue(shipped.enabled)
        self.assertEqual((shipped.max_spread, shipped.min_queue_ahead), (0.01, 50))
        self.assertEqual(shipped.leagues, frozenset(
            {"itfme", "itfwo", "wtt", "cs2", "dota2", "pdcdarts", "valorant",
             "modus", "wta", "atpdb"}))
        self.assertTrue({"setkameua", "setkamemd", "setkamecz", "atp"}.isdisjoint(shipped.leagues))


class ThinBookRunnerTests(unittest.IsolatedAsyncioTestCase):
    """The live economics pass applies the rule to new entries only."""

    setUp = live.LiveCommissioningTests.setUp
    build_runner = live.LiveCommissioningTests.build_runner

    async def prepared(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        return runner, client

    async def test_thin_one_tick_book_blocks_new_entries_and_is_replaceable(self):
        from dataclasses import replace
        from unittest.mock import patch
        from liquidity_bot.models import BookLevel, BookSnapshot
        runner, client = await self.prepared()
        self.assertIn(THIN_BOOK_BLOCKER, runner.REPLACEABLE_ECONOMIC_BLOCKERS)
        runner.config = replace(runner.config, thin_book_policy=ThinBookPolicy(
            True, 0.01, 50.0, frozenset({"x"})))
        state = runner.state_for("market-one")
        state.book = BookSnapshot("market-one", (BookLevel(.49, 10), BookLevel(.48, 500)),
                                  (BookLevel(.50, 10), BookLevel(.51, 500)))
        reads = client.markets.book_calls
        with patch("liquidity_bot.thin_book.league_from_slug", return_value="x"):
            runner._evaluate_rewards(live.NOW, "market-one")
        self.assertEqual(state.economic_blocker_by_side["bid"], THIN_BOOK_BLOCKER)
        self.assertEqual(state.economic_blocker_by_side["offer"], THIN_BOOK_BLOCKER)
        self.assertEqual((state.expected_net_by_side["bid"], state.expected_net_by_side["offer"]), (0.0, 0.0))
        self.assertEqual(reads, client.markets.book_calls)   # no venue read added
        # Another league on the same book: untouched.
        runner._evaluate_rewards(live.NOW, "market-one")
        self.assertNotEqual(state.economic_blocker_by_side.get("bid"), THIN_BOOK_BLOCKER)


if __name__ == "__main__":
    unittest.main()
