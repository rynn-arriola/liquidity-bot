from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import unittest

from liquidity_bot.errors import AdapterError
from liquidity_bot.market_selector import RewardMarketSelector, RewardSelectionPolicy
from liquidity_bot.scan_coverage import (
    CoverageSelector, DiscoveryReads, WatchBooks, compare, observe,
)
from tests.test_market_selector import NOW, FakeGateway, FixedFillHazard, book, incentive, market


def setup(count=110, watch=300):
    slugs = [f"m{n:03d}" for n in range(count)]
    gateway = FakeGateway(
        {s: market(s) for s in slugs},
        {s: incentive(s, pool=20000) for s in slugs},
        {s: replace(book(s, touch_size=50), received_at=NOW) for s in slugs})
    selector = CoverageSelector(
        DiscoveryReads(gateway), RewardSelectionPolicy(max_book_candidates=100),
        FixedFillHazard(.01), watch_limit=watch)
    return gateway, selector


class CoverageTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_matches_real_selector_shortlist_without_book_rest(self):
        gateway, selector = setup(350)
        await selector.select(now=NOW)
        real = RewardMarketSelector(gateway, selector.policy, FixedFillHazard(.01))
        await real.select(now=NOW)
        self.assertEqual(selector.baseline_slugs, tuple(gateway.book_calls))
        self.assertEqual(len(selector.watched), 300)
        self.assertEqual(selector.candidate_count, 350)
        # Only the real selector called fetch_book; diagnostic exposes no path.
        self.assertEqual(len(gateway.book_calls), 100)
        self.assertFalse(hasattr(selector.gateway, "fetch_book"))
        self.assertEqual(gateway.traffic_classes, ["background"])

    async def test_comparison_detects_better_outside_candidate_same_books(self):
        gateway, selector = setup()
        await selector.select(now=NOW)
        # A deferred candidate with less competing depth has a higher share.
        gateway.books["m109"] = replace(book("m109", touch_size=5), received_at=NOW)
        result = compare(selector, gateway.books, now=NOW, discovered_at=NOW)
        self.assertIn("m109", result["outside_baseline_better_or_fills_gap"])
        self.assertTrue(result["baseline_comparable"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(gateway.book_calls, [])
        self.assertTrue(result["ranking_only_not_entry_proof"])

    async def test_missing_baseline_is_unknown_not_a_profit_improvement(self):
        gateway, selector = setup()
        await selector.select(now=NOW)
        del gateway.books[selector.baseline_slugs[0]]
        result = compare(selector, gateway.books, now=NOW, discovered_at=NOW)
        self.assertFalse(result["baseline_comparable"])
        self.assertIsNone(result["outside_baseline_better_or_fills_gap"])
        self.assertFalse(result["coverage_complete"])

    async def test_stale_future_missing_and_boundary_receipts_are_unknown(self):
        gateway, selector = setup(4)
        await selector.select(now=NOW)
        for slug, receipt in zip(gateway.books, (
            NOW - timedelta(seconds=3), NOW + timedelta(seconds=1), None, NOW)):
            gateway.books[slug] = replace(gateway.books[slug], received_at=receipt)
        result = compare(selector, gateway.books, now=NOW, discovered_at=NOW)
        self.assertEqual(len(result["unknown"]), 3)
        self.assertEqual(result["evaluated_count"], 1)
        self.assertFalse(result["baseline_comparable"])

    async def test_expired_metadata_and_clock_reversal_refuse_comparison(self):
        gateway, selector = setup(1)
        await selector.select(now=NOW)
        for discovered in (NOW - timedelta(seconds=121), NOW + timedelta(seconds=1)):
            result = compare(selector, gateway.books, now=NOW, discovered_at=discovered)
            self.assertEqual(result["unknown"], {"m000": "metadata_expired"})

    async def test_kickoff_and_reward_expiry_are_rechecked(self):
        gateway, selector = setup(1)
        gateway.markets["m000"] = replace(market("m000"), game_start=NOW + timedelta(seconds=10))
        await selector.select(now=NOW)
        later = NOW + timedelta(seconds=10)
        gateway.books["m000"] = replace(gateway.books["m000"], received_at=later)
        result = compare(selector, gateway.books, now=later, discovered_at=NOW)
        self.assertEqual(result["currently_ineligible"], ["m000"])

        before_midnight = NOW.replace(hour=3, minute=59, second=59)
        gateway, selector = setup(1)
        gateway.incentives["m000"] = replace(
            incentive("m000", pool=20000, phase="daily_event"), observed_at=before_midnight)
        await selector.select(now=before_midnight)
        self.assertEqual(len(selector.watched), 1)
        midnight = before_midnight + timedelta(seconds=1)
        gateway.books["m000"] = replace(gateway.books["m000"], received_at=midnight)
        result = compare(selector, gateway.books, now=midnight, discovered_at=before_midnight)
        self.assertEqual(result["currently_ineligible"], ["m000"])
        self.assertEqual(result["broader_top"], [])

    async def test_program_end_and_daily_midnight_do_not_keep_old_ranking(self):
        gateway, selector = setup(1)
        gateway.incentives["m000"] = incentive("m000", pool=20000,
                                               ends_at=NOW + timedelta(seconds=2))
        await selector.select(now=NOW)
        later = NOW + timedelta(seconds=2)
        gateway.books["m000"] = replace(gateway.books["m000"], received_at=later)
        result = compare(selector, gateway.books, now=later, discovered_at=NOW)
        self.assertEqual(result["currently_ineligible"], ["m000"])

    async def test_duplicate_or_mismatched_book_cannot_improve_baseline(self):
        gateway, selector = setup(1)
        await selector.select(now=NOW)
        gateway.books["m000"] = replace(book("wrong"), received_at=NOW)
        result = compare(selector, gateway.books, now=NOW, discovered_at=NOW)
        self.assertFalse(result["baseline_comparable"])

    async def test_invalid_limits_and_clocks_refuse(self):
        gateway, selector = setup(1)
        for limit in (99, 301, 100.5):
            with self.assertRaises(ValueError):
                setup(1, watch=limit)
        for seconds in (0, 121, float("nan")):
            with self.assertRaises(ValueError):
                await observe(selector, WatchBooks(None), seconds=seconds, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            compare(selector, {}, now=NOW.replace(tzinfo=None), discovered_at=NOW)

    async def test_empty_universe_and_truncation_remain_explicit(self):
        gateway, selector = setup(0)
        await selector.select(now=NOW)
        result = compare(selector, {}, now=NOW, discovered_at=NOW)
        self.assertEqual(result["broader_top"], [])
        self.assertTrue(result["coverage_complete"])
        gateway, selector = setup(301)
        await selector.select(now=NOW)
        result = compare(selector, gateway.books, now=NOW, discovered_at=NOW)
        self.assertEqual(result["unwatched_count"], 1)
        self.assertFalse(result["coverage_complete"])

    async def test_day_of_priority_and_policy_are_unchanged(self):
        gateway, selector = setup(2)
        gateway.incentives["m000"] = incentive("m000", pool=99999999, phase="daily_event")
        await selector.select(now=NOW)
        result = compare(selector, gateway.books, now=NOW, discovered_at=NOW)
        self.assertEqual(result["broader_top"][0], "m001")
        self.assertFalse(selector.policy.enforce_reward_cost_gate)
        self.assertEqual(selector.policy.max_book_candidates, 100)

    async def test_rotation_still_preserves_all_baseline_members(self):
        gateway, selector = setup(350, watch=150)
        await selector.select(now=NOW)
        original = set(selector.baseline_slugs)
        await selector.select(now=NOW)
        self.assertNotEqual(original, set(selector.baseline_slugs))
        self.assertTrue(set(selector.baseline_slugs) <= {m.slug for m, _, _ in selector.watched})
        self.assertEqual(gateway.book_calls, [])

    async def test_discovery_failure_does_not_start_sockets(self):
        gateway, selector = setup(1)
        async def fail(**kwargs):
            raise AdapterError("unavailable", status_code=429)
        gateway.list_upcoming_sports_markets = fail
        factory = Factory()
        with self.assertRaises(AdapterError):
            await observe(selector, WatchBooks(None, stream_factory=factory),
                          seconds=1, clock=lambda: NOW)
        self.assertEqual(factory.streams, [])


class Stream:
    def __init__(self):
        self.slugs = ()
        self.closed = False
        self.queue = asyncio.Queue()

    async def start(self, slugs):
        self.slugs = tuple(slugs)
        for slug in slugs:
            self.queue.put_nowait(replace(book(slug), received_at=NOW))

    async def next_book(self):
        result = await self.queue.get()
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self):
        self.closed = True


class Factory:
    def __init__(self):
        self.streams = []

    def __call__(self, client):
        stream = Stream()
        self.streams.append(stream)
        return stream


class WatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_failure_does_not_skip_other_streams_or_keep_books(self):
        factory = Factory()
        watcher = WatchBooks(None, stream_factory=factory)
        async with watcher.watch([str(n) for n in range(101)]):
            await asyncio.sleep(.01)
            async def fail():
                raise RuntimeError("close failed")
            factory.streams[0].close = fail
        self.assertTrue(factory.streams[1].closed)
        self.assertEqual(watcher.close_failures, {0: "RuntimeError"})
        self.assertEqual(watcher.books, {})

    async def test_failed_start_closes_and_does_not_retry(self):
        stream = Stream()
        async def fail(slugs):
            raise AdapterError("start failed")
        stream.start = fail
        watcher = WatchBooks(None, stream_factory=lambda client: stream)
        async with watcher.watch(["a"]):
            self.assertEqual(watcher.failures, {0: "AdapterError"})
            self.assertEqual(watcher.books, {})
        self.assertTrue(stream.closed)

    async def test_cancel_during_start_closes_feed(self):
        stream = Stream()
        starting = asyncio.Event()
        async def stall(slugs):
            starting.set()
            await asyncio.Future()
        stream.start = stall
        watcher = WatchBooks(None, stream_factory=lambda client: stream)
        async def run():
            async with watcher.watch(["a"]):
                self.fail("cancelled startup cannot enter observation")
        task = asyncio.create_task(run())
        await starting.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)

    async def test_300_books_split_into_three_persistent_scopes_and_close(self):
        factory = Factory()
        watcher = WatchBooks(None, stream_factory=factory)
        async with watcher.watch([f"m{n}" for n in range(300)]):
            await asyncio.sleep(.01)
            self.assertEqual([len(s.slugs) for s in factory.streams], [100, 100, 100])
            self.assertEqual(len(watcher.books), 300)
            await asyncio.sleep(.01)
            self.assertEqual(len(factory.streams), 3)
        self.assertEqual(watcher.books, {})
        self.assertTrue(all(s.closed for s in factory.streams))

    async def test_disconnect_invalidates_only_affected_shard_no_retries(self):
        factory = Factory()
        watcher = WatchBooks(None, stream_factory=factory)
        async with watcher.watch([f"m{n}" for n in range(101)]):
            await asyncio.sleep(.01)
            factory.streams[0].queue.put_nowait(AdapterError("disconnected"))
            await asyncio.sleep(.01)
            self.assertEqual(len(watcher.books), 1)
            self.assertEqual(watcher.failures, {0: "AdapterError"})
            self.assertEqual(len(factory.streams), 2)

    async def test_foreign_book_is_never_retained(self):
        factory = Factory()
        watcher = WatchBooks(None, stream_factory=factory)
        async with watcher.watch(["a"]):
            factory.streams[0].queue.put_nowait(book("foreign"))
            await asyncio.sleep(.01)
            self.assertEqual(watcher.books, {})
            self.assertEqual(watcher.failures, {0: "AdapterError"})

    async def test_cancellation_closes_all_feeds(self):
        factory = Factory()
        watcher = WatchBooks(None, stream_factory=factory)
        ready = asyncio.Event()
        async def run():
            async with watcher.watch(["a"]):
                ready.set()
                await asyncio.Future()
        task = asyncio.create_task(run())
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(factory.streams[0].closed)
        self.assertEqual(watcher.books, {})

    async def test_bounds_dedupe_empty_and_single_use(self):
        factory = Factory()
        watcher = WatchBooks(None, stream_factory=factory)
        with self.assertRaises(ValueError):
            async with watcher.watch([str(n) for n in range(301)]):
                pass
        async with watcher.watch(["a", "a"]):
            self.assertEqual(factory.streams[0].slugs, ("a",))
        with self.assertRaises(RuntimeError):
            async with watcher.watch(["a"]):
                pass
        async with WatchBooks(None, stream_factory=factory).watch([]):
            self.assertEqual(len(factory.streams), 1)

    async def test_end_to_end_is_bounded_and_has_no_rest_books(self):
        gateway, selector = setup(105)
        factory = Factory()
        result = await observe(selector, WatchBooks(None, stream_factory=factory),
                               seconds=1.1, clock=lambda: NOW)
        self.assertEqual(result["order_mutations"], 0)
        self.assertEqual(result["rest_book_requests"], 0)
        self.assertEqual(result["frames"], 105)
        self.assertTrue(result["samples"][-1]["coverage_complete"])
        self.assertEqual(gateway.book_calls, [])
        self.assertEqual(len(gateway.upcoming_calls), 1)
        self.assertTrue(all(s.closed for s in factory.streams))
