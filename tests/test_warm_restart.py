import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from liquidity_bot import warm_restart
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.market_selector import RewardMarketSelector, RewardSelectionPolicy
import test_live_commissioning as live_tests
from test_live_commissioning import (
    FakeRewardSelector, fake_selection, multi_selection, no_viable_selection)
from test_market_selector import NOW, FakeGateway, book, incentive, market

KEEP = "aec-setkameua-keep-tight-2026-08-13"
STARTED = "aec-setkameua-started-x-2026-08-13"
NEAR = "aec-setkameua-near-kickoff-2026-08-13"
NO_REWARD = "aec-setkameua-no-reward-2026-08-13"
EXCLUDED = "aec-utr-excluded-x-2026-08-13"
BARRED = "aec-setkameua-barred-x-2026-08-13"
MISSING = "aec-setkameua-missing-x-2026-08-13"


class ScopeFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / warm_restart.SCOPE_FILENAME

    def test_round_trip_is_atomic_and_deduplicated(self):
        warm_restart.write_scope(self.path, ["a", "b", "a", " "], now=NOW)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        slugs, age = warm_restart.read_scope(self.path, now=NOW + timedelta(minutes=5))
        self.assertEqual(slugs, ("a", "b"))
        self.assertEqual(age, 300)

    def test_stale_future_malformed_and_missing_are_refused(self):
        warm_restart.write_scope(self.path, ["a"], now=NOW)
        with self.assertRaises(ValueError):
            warm_restart.read_scope(self.path, now=NOW + timedelta(hours=6, seconds=1))
        with self.assertRaises(ValueError):
            warm_restart.read_scope(self.path, now=NOW - timedelta(seconds=1))
        for payload in ("not json", json.dumps({"schema": 1, "markets": ["a"]}),
                        json.dumps({"schema": 2, "written_at": NOW.isoformat(), "markets": []}),
                        json.dumps({"schema": 1, "written_at": NOW.isoformat(), "markets": [1]})):
            with self.subTest(payload=payload):
                self.path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    warm_restart.read_scope(self.path, now=NOW)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            warm_restart.read_scope(self.path, now=NOW)


def readmission_runner(ledger_dir, *, gateway, barred=()):
    runner = object.__new__(CommissioningRunner)
    runner.ledger = SimpleNamespace(path=Path(ledger_dir) / "ledger.db")
    runner.reward_selector = RewardMarketSelector(
        gateway, RewardSelectionPolicy(excluded_leagues=frozenset({"utr"})))
    runner.config = SimpleNamespace(max_markets=32, force_exit_before_start_seconds=300)
    runner._barred_slugs = lambda now=None: set(barred)
    runner._now = lambda: NOW
    return runner


class ReadmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        tight = market(KEEP, starts_in_hours=2, catalog_bid=.50, catalog_offer=.51)
        markets = {
            KEEP: tight,
            STARTED: market(STARTED, starts_in_hours=0),
            NEAR: replace(market(NEAR), game_start=NOW + timedelta(minutes=4)),
            NO_REWARD: market(NO_REWARD, starts_in_hours=2),
            EXCLUDED: market(EXCLUDED, starts_in_hours=2),
            BARRED: market(BARRED, starts_in_hours=2),
        }
        self.gateway = FakeGateway(
            markets, {s: incentive(s, pool=35) for s in markets if s != NO_REWARD},
            {s: book(s) for s in markets})
        self.runner = readmission_runner(self.dir.name, gateway=self.gateway, barred={BARRED})
        self.scope = warm_restart.scope_path(self.runner.ledger.path)

    async def readmit(self):
        out = io.StringIO()
        with redirect_stdout(out):
            kept = await self.runner._warm_restart_readmission(NOW)
        return kept, out.getvalue()

    async def test_only_still_valid_markets_return_including_tight_ones(self):
        # The fresh 2-cent gate would refuse KEEP; retention keeps it.
        self.assertFalse(self.runner.reward_selector._catalog_spread_can_enter(
            self.gateway.markets[KEEP]))
        warm_restart.write_scope(self.scope, [KEEP, STARTED, NEAR, NO_REWARD, EXCLUDED,
                                              BARRED, MISSING], now=NOW - timedelta(minutes=10))
        kept, log = await self.readmit()
        self.assertEqual(kept, (KEEP,))
        for reason in ("not_found", "barred", "not_executable", "inside_flatten_window",
                       "no_active_reward"):
            self.assertIn(reason, log)
        self.assertIn("outcome=readmitted remembered=7 readmitted=1", log)
        self.assertEqual(len(self.gateway.metadata_calls), 1)
        self.assertEqual(len(self.gateway.incentive_symbol_calls), 1)
        self.assertEqual(self.gateway.book_calls, [])

    async def test_missing_stale_or_corrupt_scope_readmits_nothing_without_reads(self):
        kept, log = await self.readmit()
        self.assertEqual((kept, "outcome=no_scope" in log), ((), True))
        warm_restart.write_scope(self.scope, [KEEP], now=NOW - timedelta(hours=7))
        kept, log = await self.readmit()
        self.assertEqual((kept, "ignored:ValueError" in log), ((), True))
        self.scope.write_text("{broken", encoding="utf-8")
        kept, _ = await self.readmit()
        self.assertEqual(kept, ())
        self.assertEqual(self.gateway.metadata_calls, [])

    async def test_read_failure_is_contained(self):
        warm_restart.write_scope(self.scope, [KEEP], now=NOW)
        self.gateway.fetch_markets_by_slugs = AsyncMock(side_effect=RuntimeError("venue down"))
        kept, log = await self.readmit()
        self.assertEqual(kept, ())
        self.assertIn("read_failed:RuntimeError", log)


def selection_runner(selection, readmitted, *, max_markets=3):
    runner = object.__new__(CommissioningRunner)
    runner.reward_selector = FakeRewardSelector(selection)
    runner._warm_restart_readmission = AsyncMock(return_value=tuple(readmitted))
    runner._log_selection_scan = lambda selection: None
    runner.bound = []

    def bind(slugs):
        runner.bound.append(tuple(slugs))
        runner._active_market_slugs = tuple(slugs)
    runner._bind_markets = bind
    runner._barred_slugs = lambda now=None: set()
    runner.config = SimpleNamespace(max_markets=max_markets, market_selection_refresh_seconds=900)
    runner._runtime_error_counts = {}
    runner._now = lambda: NOW
    return runner


class StartupSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_readmitted_bind_first_then_fresh_fill_remaining_slots(self):
        runner = selection_runner(multi_selection("keep-1", "fresh-a", "fresh-b", "fresh-c"),
                                  ["keep-1", "keep-2"], max_markets=3)
        await runner._select_reward_market(NOW)
        self.assertEqual(runner.bound, [("keep-1", "keep-2", "fresh-a")])

    async def test_readmitted_markets_start_even_when_scan_has_nothing_viable(self):
        runner = selection_runner(no_viable_selection(), ["keep-1"])
        await runner._select_reward_market(NOW)
        self.assertEqual(runner.bound, [("keep-1",)])

    async def test_no_readmission_keeps_todays_selection(self):
        runner = selection_runner(multi_selection("fresh-a", "fresh-b"), [])
        await runner._select_reward_market(NOW)
        self.assertEqual(runner.bound, [("fresh-a", "fresh-b")])


class RunnerScopeMemoryTests(unittest.IsolatedAsyncioTestCase):
    # Borrow only the harness, not the 489 inherited tests.
    setUp = live_tests.LiveCommissioningTests.setUp
    build_runner = live_tests.LiveCommissioningTests.build_runner

    async def test_binding_remembers_scope_and_restart_fails_safe_without_venue_reads(self):
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(fake_selection("market-two")))
        await runner.preflight()
        slugs, _ = warm_restart.read_scope(
            warm_restart.scope_path(runner.ledger.path), now=runner._now())
        self.assertEqual(slugs, tuple(runner.active_slugs))
        await runner.private_stream.close()
        await runner.market_stream.close()
        # A restart over the same ledger: this fake selector has no gateway, so
        # re-admission cannot read and must fall back to today's startup.
        restarted, client2, _ = self.build_runner(
            auto=True, ledger=runner.ledger, client=client,
            reward_selector=FakeRewardSelector(fake_selection("market-two")))
        out = io.StringIO()
        with redirect_stdout(out):
            mode = await restarted.preflight()
        self.assertEqual(mode, "TRADE")
        self.assertIn("outcome=read_failed", out.getvalue())
        self.assertEqual(restarted.market_slug, "market-two")
        self.assertEqual(client.orders.create_calls, [])
        await restarted.private_stream.close()
        await restarted.market_stream.close()

