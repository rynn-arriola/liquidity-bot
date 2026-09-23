"""No live calls. Joint reporting/tracker/hybrid acceptance and failure isolation."""
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import current_thread
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS
import zlib

import test_summary_image_report as summary_tests
from test_summary_image_report import NOW
from test_live_commissioning import fake_selection
from test_market_selector import market, incentive, book, FakeGateway, NOW as SCAN_NOW
import test_hybrid_rest_comparison as hybrid_tests
from liquidity_bot.market_selector import RewardMarketSelector
from liquidity_bot.market_quality_measurement import emit_discovery, discovery_snapshot, OrderWitness
from liquidity_bot.market_quality_store import QualityStore, read_archive, PACKED
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.summary_report import prepare_summary
from liquidity_bot.summary_rewards import report_rewards


class ReportingCacheTests(unittest.TestCase):
    setUp = summary_tests.SummaryReportTests.setUp
    payload = summary_tests.SummaryReportTests.payload

    def test_restart_preserves_same_account_values_with_stale_label(self):
        _, fresh = prepare_summary(self.payload(reward_cache_scope='a'*64))
        _, recovered = prepare_summary(self.payload(reward_cache_scope='a'*64,
            rewards_observed_at=None, attributable_paid_rewards_usd=Decimal(0),
            pending_rewards_usd=Decimal(0)))
        self.assertEqual(recovered['lifetime'], fresh['lifetime'])
        self.assertIn('stale', recovered['reward_note'])
        self.assertIn('09/19 09:00 ET', recovered['reward_note'])

    def test_wrong_account_does_not_reuse_values(self):
        prepare_summary(self.payload(reward_cache_scope='a'*64))
        _, m = prepare_summary(self.payload(reward_cache_scope='b'*64, rewards_observed_at=None))
        self.assertEqual(m['lifetime'][5][1], '--')
        self.assertEqual(m['lifetime'][6][1], '--')

    def test_refresh_failure_marks_retained_values_stale_immediately(self):
        _, m = prepare_summary(self.payload(rewards_refresh_failed=True))
        self.assertIn('stale', m['reward_note'])
        self.assertEqual(m['lifetime'][5][1], '$+15.00')
        self.assertEqual(m['lifetime'][6][1], '$12.00')

    def test_valid_zero_replaces_previous_nonzero(self):
        prepare_summary(self.payload(reward_cache_scope='a'*64, rewards_observed_at=NOW-timedelta(seconds=1)))
        _, m = prepare_summary(self.payload(reward_cache_scope='a'*64,
            attributable_paid_rewards_usd=Decimal(0), pending_rewards_usd=Decimal(0)))
        self.assertEqual(m['lifetime'][5][1], '$+0.00')
        self.assertEqual(m['lifetime'][6][1], '$0.00')
        self.assertNotIn('stale', m['reward_note'])

    def test_cache_write_failure_preserves_current_value(self):
        with patch('liquidity_bot.summary_rewards.os.replace', side_effect=OSError('disk')):
            _, m = prepare_summary(self.payload(reward_cache_scope='a'*64))
        self.assertEqual(m['lifetime'][6][1], '$12.00')
        self.assertFalse(list(self.db.parent.glob('.summary-rewards-*')))

    def test_conflicting_same_instant_with_offset_is_unknown(self):
        s = self.payload(reward_cache_scope='a'*64)['_summary_report']
        report_rewards(s, NOW)
        s.update(rewards_observed_at='2026-09-19T09:00:00-04:00', pending_rewards_usd='99')
        self.assertEqual(report_rewards(s, NOW), (None, True))

    def test_older_summary_cannot_overwrite_newer_snapshot(self):
        prepare_summary(self.payload(reward_cache_scope='a'*64))
        _, m = prepare_summary(self.payload(reward_cache_scope='a'*64,
            rewards_observed_at=NOW-timedelta(seconds=1), pending_rewards_usd=Decimal(999)))
        self.assertEqual(m['lifetime'][6][1], '$12.00')
        self.assertIn('stale', m['reward_note'])

    def test_bad_or_future_cache_unknown_not_zero(self):
        for kind in ('corrupt', 'future', 'nonfinite', 'negative', 'oversize'):
            with self.subTest(kind=kind):
                prepare_summary(self.payload(reward_cache_scope='a'*64))
                path = Path(str(self.db)+'.summary-rewards.json')
                raw = json.loads(path.read_text())
                if kind == 'future': raw['snapshot']['at'] = (NOW+timedelta(days=1)).isoformat()
                if kind == 'nonfinite': raw['snapshot']['paid'] = 'NaN'
                if kind == 'negative': raw['snapshot']['pending'] = '-1'
                path.write_text('invalid' if kind == 'corrupt' else 'x'*4097 if kind == 'oversize' else json.dumps(raw))
                _, m = prepare_summary(self.payload(reward_cache_scope='a'*64, rewards_observed_at=None))
                self.assertEqual(m['lifetime'][6][1], '--')
                path.unlink()

    def test_image_still_renders_profit_pending_and_meter(self):
        from liquidity_bot.summary_image import render
        from PIL import ImageDraw
        _, m = prepare_summary(self.payload(rewards_refresh_failed=True))
        with patch.object(ImageDraw.ImageDraw, 'text', autospec=True) as draw:
            render(m)
        texts = [c.args[2] for c in draw.call_args_list]
        self.assertIn('$+15.00', texts)
        self.assertIn('$12.00', texts)
        self.assertIn('HARD · 24% used', texts)
        self.assertTrue(any('stale' in t for t in texts))


class ReportingRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotation_timeout_recovery_do_not_reset_reporting_or_add_reads(self):
        r = object.__new__(CommissioningRunner)
        r.clock = lambda: NOW
        r._active_market_slugs = ('active',)
        r._markets = {}; r._accrue_score = Mock()
        r.gateway = NS(fetch_earnings=AsyncMock(return_value=[NS(date='2026-09-19',
            market_slug='retired', program_type='liquidityProgram', reward=5, status='PAID')]))
        r.ledger = Mock(); r.quality_sink = None
        await r._refresh_earnings(NOW)
        saved = dict(r._bot_era_rewards)
        r._reset_selected_market_observations(NOW)
        self.assertIsNone(r._earnings_observed_at)
        self.assertEqual(r._report_rewards_observed_at, NOW)
        self.assertEqual(r._bot_era_rewards, saved)
        r.gateway.fetch_earnings.side_effect = TimeoutError
        with self.assertRaises(TimeoutError): await r._refresh_earnings(NOW+timedelta(minutes=1))
        self.assertEqual(r._report_rewards_observed_at, NOW)
        self.assertEqual(r._bot_era_rewards, saved)
        r._reward_refresh_failed = True
        r.gateway.fetch_earnings.side_effect = None
        r.gateway.fetch_earnings.return_value = []
        await r._refresh_earnings(NOW+timedelta(minutes=2))
        self.assertEqual(r._report_rewards_observed_at, NOW+timedelta(minutes=2))
        self.assertFalse(r._reward_refresh_failed)
        self.assertEqual(r._bot_era_rewards, {})
        self.assertEqual(r.gateway.fetch_earnings.await_count, 3)


class TrackerStorageTests(unittest.TestCase):
    def args(self, count=2):
        catalog = [market(str(i)) for i in range(count)]
        return [catalog, {m.slug:m for m in catalog}, {}, [], [], [], fake_selection('a'), NOW]

    def test_discovery_builds_in_worker_and_freezes_collections(self):
        args = self.args()
        built = []
        def capture(*a, **k):
            built.append(current_thread().name)
            return discovery_snapshot(*a, **k)
        with tempfile.TemporaryDirectory() as td, patch('liquidity_bot.market_quality_measurement.discovery_snapshot', capture):
            with patch('liquidity_bot.market_quality_store.Thread.start'):
                store = QualityStore(Path(td), reserve_bytes=0)
                emit_discovery(store, *args)
                args[0].clear(); args[1].clear()
                self.assertEqual(built, [])
            store.thread.start(); store.close(); store.thread.join(3)
            records, h = read_archive(store.path)
            self.assertEqual(built, ['market-quality'])
            self.assertEqual(len(records[0]['payload']['candidates']), 2)
            self.assertTrue(h['closed'])

    def test_one_pending_scan_and_queue_full_are_visible_gaps(self):
        with patch('liquidity_bot.market_quality_store.Thread.start'):
            store = QualityStore(Path('unused'), queue_size=1)
            self.assertTrue(store.defer_discovery(*self.args()))
            self.assertFalse(store.defer_discovery(*self.args()))
            self.assertEqual(store.dropped, 1)
            self.assertEqual(store.queue.qsize(), 1)
            other = QualityStore(Path('unused'), queue_size=1)
            other.emit('test', {})
            self.assertFalse(other.defer_discovery(*self.args()))
            self.assertTrue(other.discovery_slot.acquire(blocking=False))

    def test_failed_builder_does_not_disable_later_records(self):
        with tempfile.TemporaryDirectory() as td, patch('liquidity_bot.market_quality_measurement.discovery_snapshot', side_effect=ValueError('bad')):
            store = QualityStore(Path(td), reserve_bytes=0)
            store.defer_discovery(*self.args())
            store.emit('test', {'ok':1})
            store.close(); store.thread.join(3)
            records, h = read_archive(store.path)
            self.assertEqual([r['kind'] for r in records], ['test'])
            self.assertFalse(h['sequence_complete'])
            self.assertEqual(h['dropped'], 1)
            self.assertFalse(h['disabled'])

    def test_large_catalog_compression_and_compact_reader(self):
        with tempfile.TemporaryDirectory() as td:
            store = QualityStore(Path(td), reserve_bytes=0)
            store.defer_discovery(*self.args(12311))
            store.close(); store.thread.join(10)
            records, h = read_archive(store.path)
            compact, hc = read_archive(store.path, summarize_discovery=True)
            self.assertEqual(len(records[0]['payload']['candidates']), 12311)
            self.assertEqual(compact[0]['payload']['candidates'], [])
            self.assertEqual(compact[0]['payload']['candidate_observations_by_stage'], {'reward_missing':12311})
            self.assertEqual(records[0]['sha256'], compact[0]['sha256'])
            self.assertTrue(h['sequence_complete'])
            self.assertEqual(h, hc)
            self.assertLess(store.path.stat().st_size, 400000)

    def test_legacy_text_records_and_invalid_compression(self):
        with tempfile.TemporaryDirectory() as td:
            store = QualityStore(Path(td), reserve_bytes=0)
            store.emit('test', {'old':1});store.close();store.thread.join(3)
            with sqlite3.connect(store.path) as c:
                body = '{"old":1}'
                c.execute('UPDATE records SET body=?,sha256=?', (body, hashlib.sha256(body.encode()).hexdigest()))
            self.assertEqual(read_archive(store.path)[0][0]['payload'], {'old':1})
            for invalid in (b'unknown', PACKED+zlib.compress(b'{}')[:-1], PACKED+zlib.compress(b'{}')+b'extra'):
                with sqlite3.connect(store.path) as c:c.execute('UPDATE records SET body=?', (invalid,))
                with self.assertRaises(ValueError):read_archive(store.path)

    def test_projected_write_cannot_spend_disk_reserve(self):
        with tempfile.TemporaryDirectory() as td, patch(
                'liquidity_bot.market_quality_store.shutil.disk_usage', return_value=NS(free=100001)):
            store = QualityStore(Path(td), reserve_bytes=100000)
            store.emit('test', {})
            store.close();store.thread.join(3)
            self.assertTrue(store.disabled)
            self.assertEqual(store.written, 0)


class CombinedModeTests(unittest.IsolatedAsyncioTestCase):
    setUp = hybrid_tests.HybridRestComparisonTests.setUp
    build = hybrid_tests.HybridRestComparisonTests.build
    reads = hybrid_tests.HybridRestComparisonTests.reads

    async def test_tracker_and_reporting_keep_rest_hybrid_read_counts_identical(self):
        counts_by_mode = {}
        for mode in ('rest', 'hybrid'):
            r, client = await self.build(mode, count=30)
            with tempfile.TemporaryDirectory() as td:
                store = QualityStore(Path(td), reserve_bytes=0)
                r.quality_sink = store
                r.ledger.quality_observer = OrderWitness(store)
                with self.reads(client) as counts:
                    for tick in range(1, 6):
                        self.now += timedelta(seconds=5)
                        await r._rest_reconcile(self.now, tag='combined')
                        # Reporting consumes only existing cached reward evidence.
                        report_rewards(dict(rewards_observed_at=self.now.isoformat(),
                            attributable_paid_rewards_usd='2',pending_rewards_usd='3'), self.now)
                counts_by_mode[mode] = dict(counts)
                store.close(); store.thread.join(3)
                records, health = read_archive(store.path)
                self.assertTrue(health['closed'])
                self.assertEqual(len([row for row in records if row['kind']=='order_witness']), 30)
        self.assertEqual(counts_by_mode['rest'], counts_by_mode['hybrid'])

    async def test_real_tracker_does_not_change_selection_calls_or_result(self):
        outputs=[]; calls=[]
        with tempfile.TemporaryDirectory() as td:
            store = QualityStore(Path(td), reserve_bytes=0)
            for sink in (None, store):
                gateway = FakeGateway({'a':market('a')}, {'a':incentive('a',pool=100)}, {'a':book('a')})
                outputs.append(await RewardMarketSelector(gateway,quality_sink=sink).select(now=SCAN_NOW))
                calls.append((gateway.book_calls,gateway.upcoming_calls,gateway.incentive_symbol_calls))
            store.close();store.thread.join(3)
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(read_archive(store.path)[0][0]['kind'], 'discovery')


if __name__ == '__main__': unittest.main()
