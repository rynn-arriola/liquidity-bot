import ast
import inspect
import json
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path

from liquidity_bot import live_commissioning
from liquidity_bot.commissioning import AUTO_REWARD_MARKET, CommissioningConfig
from liquidity_bot.market_selector import RewardMarketSelector, RewardSelectionPolicy
from liquidity_bot.selection_exclusions import (
    DEFAULT_SELECTION_EXCLUSIONS_PATH,
    SelectionExclusions,
    load_selection_exclusions,
)
from test_market_selector import NOW, FakeGateway, book, incentive, market

UTR = "aec-utr-alpha-beta-2026-08-13"
SETKA = "aec-setkameua-gamma-delta-2026-08-13"


def write(payload):
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "selection_exclusions.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")
    return directory, path


class LoaderTests(unittest.TestCase):
    def test_shipped_file_is_the_operator_decision(self):
        # 2026-09-26 operator decision: exclude never-paid UTR and R6.
        self.assertEqual(load_selection_exclusions(DEFAULT_SELECTION_EXCLUSIONS_PATH),
                         SelectionExclusions(frozenset({"utr", "r6"})))

    def test_valid_leagues_load(self):
        directory, path = write({"excluded_leagues": ["utr", "r6"]})
        with directory:
            self.assertEqual(load_selection_exclusions(path).excluded_leagues,
                             frozenset({"utr", "r6"}))

    def test_malformed_files_refuse_instead_of_guessing(self):
        for payload in ({"excluded_leagues": ["UTR"]}, {"excluded_leagues": "utr"},
                        {"excluded_leagues": ["utr"], "extra": 1}, {},
                        {"excluded_leagues": ["utr", "utr"]},
                        {"excluded_leagues": ["a-b"]}, "not json"):
            with self.subTest(payload=payload):
                directory, path = write(payload)
                with directory, self.assertRaises(ValueError):
                    load_selection_exclusions(path)
        with self.assertRaises(ValueError):
            load_selection_exclusions(Path(tempfile.gettempdir()) / "missing-exclusions.json")


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    def gateway(self):
        markets = {UTR: market(UTR), SETKA: market(SETKA)}
        return FakeGateway(markets, {s: incentive(s, pool=35) for s in markets},
                           {s: book(s) for s in markets})

    async def select(self, excluded):
        gateway = self.gateway()
        selection = await RewardMarketSelector(
            gateway, RewardSelectionPolicy(excluded_leagues=frozenset(excluded))
        ).select(now=NOW)
        return gateway, selection

    async def test_excluded_league_is_dropped_before_any_reward_or_book_read(self):
        gateway, selection = await self.select({"utr"})
        requested = {slug for call in gateway.incentive_symbol_calls for slug in call}
        self.assertNotIn(UTR, requested)
        self.assertNotIn(UTR, gateway.book_calls)
        self.assertIn(SETKA, requested)
        self.assertEqual([item.market.slug for item in selection.opportunities], [SETKA])

    async def test_empty_exclusions_leave_selection_unchanged(self):
        gateway, selection = await self.select(set())
        requested = {slug for call in gateway.incentive_symbol_calls for slug in call}
        self.assertEqual(requested, {UTR, SETKA})
        self.assertEqual({item.market.slug for item in selection.opportunities}, {UTR, SETKA})

    def test_quality_tracker_can_archive_a_policy_with_exclusions(self):
        from liquidity_bot.market_quality_measurement import plain
        policy = RewardSelectionPolicy(excluded_leagues=frozenset({"utr", "r6"}))
        archived = json.loads(json.dumps(plain(policy)))
        self.assertEqual(archived["excluded_leagues"], ["r6", "utr"])

    def test_policy_rejects_non_frozenset(self):
        with self.assertRaises(ValueError):
            RewardSelectionPolicy(excluded_leagues={"utr"})


class ConfigAndStartupTests(unittest.TestCase):
    def config(self, exclusions):
        return CommissioningConfig(
            market_slug=AUTO_REWARD_MARKET, auto_select_reward_markets=True,
            economic_risk_accepted=True, exclusive_api_key_confirmed=True,
            maker_lab_stopped_confirmed=True, arm_live=True,
            selection_exclusions=exclusions)

    def test_config_passes_exclusions_to_selection_policy(self):
        policy = self.config(SelectionExclusions(frozenset({"utr"}))).reward_selection_policy()
        self.assertEqual(policy.excluded_leagues, frozenset({"utr"}))
        self.assertEqual(self.config(SelectionExclusions()).reward_selection_policy().excluded_leagues,
                         frozenset())

    def test_config_refuses_unvalidated_exclusions(self):
        now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
        self.assertIn("selection exclusions must be validated",
                      self.config({"utr"}).blockers(now=now))

    def test_startup_loads_file_and_passes_it_to_config(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(live_commissioning._run)))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertTrue(any(getattr(c.func, "id", None) == "load_selection_exclusions"
                            for c in calls))
        config_calls = [c for c in calls if getattr(c.func, "id", None) == "CommissioningConfig"]
        self.assertTrue(any(kw.arg == "selection_exclusions" for c in config_calls
                            for kw in c.keywords))
