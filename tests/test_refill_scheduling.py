"""Offline refill regressions; no credentials or venue requests."""
import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.errors import ApiAdmissionDeferred
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind


class RefillSchedulingTests(IsolatedAsyncioTestCase):
    async def test_completed_scan_retry_revalidates_instead_of_repeating_catalog(self):
        runner = await self.build()
        selection = fixtures.multi_selection('spare')
        runner.reward_selector.select = AsyncMock(return_value=selection)
        runner.reward_selector.revalidate_viable = AsyncMock(return_value=selection.opportunities)
        first = await runner._replacement_candidates_for_top_up(
            runner._now(), exclude=set(), wanted=1, allow_cached_selection=False)
        self.assertEqual(first[1], 'fresh_scan')
        # Simulate later top-up account proof yielding. The retry is marked as
        # a normal due scan by the run loop, but the catalog already completed.
        second = await runner._replacement_candidates_for_top_up(
            runner._now(), exclude=set(), wanted=1, allow_cached_selection=False)
        self.assertEqual(second[1], 'cached_revalidated')
        self.assertEqual(second[0], ('spare',))
        runner.reward_selector.select.assert_awaited_once()
        runner.reward_selector.revalidate_viable.assert_awaited_once()

    async def test_partial_scan_cannot_seed_retry_and_expired_retry_requires_scan(self):
        runner = await self.build()
        original = runner._market_selection
        runner.reward_selector.select = AsyncMock(side_effect=ApiAdmissionDeferred(
            'account changed', error_code='LOCAL_CATALOG_ACCOUNT_CHANGED'))
        with self.assertRaises(ApiAdmissionDeferred):
            await runner._replacement_candidates_for_top_up(
                runner._now(), exclude=set(), wanted=1, allow_cached_selection=False)
        self.assertIsNone(runner._top_up_retry_selection)
        self.assertIs(runner._market_selection, original)
        selection = fixtures.multi_selection('spare')
        runner._market_selection = runner._top_up_retry_selection = selection
        runner._market_selection_observed_at = runner._now() - timedelta(seconds=121)
        runner.reward_selector.select = AsyncMock(return_value=selection)
        runner.reward_selector.revalidate_viable = AsyncMock()
        _, source, _ = await runner._replacement_candidates_for_top_up(
            runner._now(), exclude=set(), wanted=1, allow_cached_selection=False)
        self.assertEqual(source, 'fresh_scan')
        runner.reward_selector.revalidate_viable.assert_not_awaited()

    async def test_completed_retry_never_accepts_failed_revalidation_or_renews_clock(self):
        runner = await self.build()
        selection = fixtures.multi_selection('spare')
        runner._market_selection = runner._top_up_retry_selection = selection
        observed = runner._now()
        runner._market_selection_observed_at = observed
        runner.reward_selector.select = AsyncMock()
        runner.reward_selector.revalidate_viable = AsyncMock(side_effect=ApiAdmissionDeferred(
            'cooldown', error_code='LOCAL_ACCOUNT_READ_COOLDOWN'))
        with self.assertRaises(ApiAdmissionDeferred):
            await runner._replacement_candidates_for_top_up(
                runner._now(), exclude=set(), wanted=1, allow_cached_selection=False)
        runner.reward_selector.select.assert_not_awaited()
        self.assertEqual(runner._market_selection_observed_at, observed)

    async def test_finished_top_up_clears_retry_selection(self):
        fixture = fixtures.LiveCommissioningTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        runner, _, _ = fixture.build_runner()
        async def top_up(*args, **kwargs):
            runner._top_up_retry_selection = fixtures.multi_selection('spare')
            runner.request_stop('test_done')
            return False
        with patch.object(runner, '_incremental_top_up_due', return_value=True), \
                patch.object(runner, '_decision_sweep', new=AsyncMock(return_value=None)), \
                patch.object(runner, '_top_up_active_markets', side_effect=top_up):
            async with asyncio.timeout(5):
                await runner.run()
        self.assertIsNone(runner._top_up_retry_selection)

    async def build(self):
        fixture = fixtures.TopUpSafetyTests()
        self.addCleanup(fixture.doCleanups)
        runner = await fixture.armed()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        return runner

    async def test_restored_coverage_with_remaining_position_is_not_post_flat(self):
        runner = await self.build()
        slug, now = runner.market_slug, runner._now()
        runner.ledger.apply_position_event('test', 'held',
            PositionState(slug, Decimal('-30'), now), received_at=now)
        event = PrivateEvent(PrivateEventKind.POSITION_UPDATE, {
            'marketSlug': slug,
            'beforePosition': {'netPositionDecimal': '-30'},
            'afterPosition': {'netPositionDecimal': '-15'},
            'updateTime': now.isoformat()}, 'coverage-restored')
        with patch.object(runner, '_position_near_its_flatten_deadline',
                          side_effect=[True, False]):
            await runner._observe_private(event, now)
        self.assertEqual(runner.ledger.position(slug), -15)
        self.assertIsNone(runner._post_flat_top_up_requested_at)

    async def test_partial_validated_supply_does_not_repeat_discovery(self):
        runner = await self.build()
        cached = fixtures.multi_selection('spare')
        runner._market_selection = cached
        runner._market_selection_observed_at = runner._now()
        runner.reward_selector.revalidate_viable = AsyncMock(return_value=cached.opportunities)
        runner.reward_selector.select = AsyncMock(return_value=cached)
        slugs, source, _ = await runner._replacement_candidates_for_top_up(
            runner._now(), exclude=set(), wanted=20, allow_cached_selection=True)
        self.assertEqual(slugs, ('spare',))
        self.assertEqual(source, 'cached_revalidated')
        runner.reward_selector.select.assert_not_awaited()

    async def test_recent_empty_remainder_does_not_repeat_discovery(self):
        runner = await self.build()
        cached = fixtures.multi_selection(runner.market_slug)
        runner._market_selection = cached
        runner._market_selection_observed_at = runner._now()
        runner.reward_selector.select = AsyncMock(return_value=cached)
        slugs, source, _ = await runner._replacement_candidates_for_top_up(
            runner._now(), exclude={runner.market_slug}, wanted=20,
            allow_cached_selection=True)
        self.assertEqual(slugs, ())
        self.assertEqual(source, 'cached_no_supply')
        runner.reward_selector.select.assert_not_awaited()

    async def test_decision_pass_precedes_scan_even_when_scan_defers(self):
        fixture = fixtures.LiveCommissioningTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        runner, _, _ = fixture.build_runner()
        events = []
        async def decide(*args, **kwargs):
            events.append('decision')
        async def scan(*args, **kwargs):
            events.append('scan')
            runner.request_stop('test_done')
            raise ApiAdmissionDeferred('catalog changed',
                error_code='LOCAL_CATALOG_ACCOUNT_CHANGED', retry_after_seconds=1)
        with patch.object(runner, '_incremental_top_up_due', return_value=True), \
                patch.object(runner, '_decision_sweep', side_effect=decide), \
                patch.object(runner, '_top_up_active_markets', side_effect=scan):
            async with asyncio.timeout(5):
                await runner.run()
        self.assertIn('scan', events)
        self.assertIn('decision', events[:events.index('scan')])

    async def test_real_flat_transition_still_requests_refill(self):
        runner = await self.build()
        slug, now = runner.market_slug, runner._now()
        runner.ledger.apply_position_event('test', 'held',
            PositionState(slug, Decimal('15'), now), received_at=now)
        event = PrivateEvent(PrivateEventKind.POSITION_UPDATE, {
            'marketSlug': slug, 'beforePosition': {'netPositionDecimal': '15'},
            'afterPosition': {'netPositionDecimal': '0'},
            'updateTime': now.isoformat()}, 'actually-flat')
        with patch.object(runner, '_position_near_its_flatten_deadline',
                          side_effect=[True, False]):
            await runner._observe_private(event, now)
        self.assertEqual(runner.ledger.position(slug), 0)
        self.assertIsNotNone(runner._post_flat_top_up_requested_at)

    async def test_expired_or_future_cache_requires_full_scan(self):
        runner = await self.build()
        cached = fixtures.multi_selection('spare')
        runner._market_selection = cached
        runner.reward_selector.select = AsyncMock(return_value=cached)
        for offset in (-121, 30):
            runner._market_selection_observed_at = runner._now() + timedelta(seconds=offset)
            _, source, _ = await runner._replacement_candidates_for_top_up(
                runner._now(), exclude=set(), wanted=20, allow_cached_selection=True)
            self.assertEqual(source, 'fresh_scan')
        self.assertEqual(runner.reward_selector.select.await_count, 2)

    async def test_empty_cache_preserves_original_expiry_and_skips_rebind(self):
        runner = await self.build()
        runner._market_selection = fixtures.multi_selection(runner.market_slug)
        observed = runner._now()
        runner._market_selection_observed_at = observed
        runner.reward_selector.select = AsyncMock()
        with patch.object(runner, '_rest_reconcile', new=AsyncMock()) as proof, \
                patch.object(runner, '_open_streams_for_active_market', new=AsyncMock()) as bind:
            changed = await runner._top_up_active_markets(
                runner._now(), allow_cached_selection=True, allow_economic_idle=False)
        self.assertFalse(changed)
        self.assertEqual(runner._top_up_cache_expires_at, observed + timedelta(seconds=120))
        self.assertEqual(runner._market_selection_observed_at, observed)
        runner.reward_selector.select.assert_not_awaited()
        proof.assert_not_awaited()
        bind.assert_not_awaited()

    async def test_stop_from_decision_prevents_scan(self):
        fixture = fixtures.LiveCommissioningTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        runner, _, _ = fixture.build_runner()
        async def decide(*args, **kwargs):
            runner.request_stop('test_done')
            return 'test_done'
        with patch.object(runner, '_incremental_top_up_due', return_value=True), \
                patch.object(runner, '_decision_sweep', side_effect=decide), \
                patch.object(runner, '_top_up_active_markets', new=AsyncMock()) as scan:
            async with asyncio.timeout(5):
                await runner.run()
        scan.assert_not_awaited()

    async def test_stop_during_selection_or_precommit_proof_never_rebinds(self):
        for boundary in ('selection', 'proof'):
            runner = await self.build()
            cached = fixtures.multi_selection('spare')
            async def select(*, now):
                if boundary == 'selection':
                    runner.request_stop('test_done')
                return cached
            async def proof(*args, **kwargs):
                runner.request_stop('test_done')
            runner.reward_selector.select = select
            before = runner.active_slugs
            with patch.object(runner, '_rest_reconcile', side_effect=proof), \
                    patch.object(runner, '_open_streams_for_active_market', new=AsyncMock()) as bind:
                self.assertFalse(await runner._top_up_active_markets(runner._now()))
            self.assertEqual(runner.active_slugs, before)
            bind.assert_not_awaited()

    async def test_pending_stop_or_rebind_blocks_even_urgent_refill(self):
        runner = await self.build()
        now = runner._now()
        runner._post_flat_top_up_requested_at = now
        runner._streams_need_rebind = True
        self.assertFalse(runner._incremental_top_up_due(now, now))
        runner._streams_need_rebind = False
        runner.request_stop('test_done')
        self.assertFalse(runner._incremental_top_up_due(now, now))
