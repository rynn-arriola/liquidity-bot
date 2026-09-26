from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from liquidity_bot.adapters import AsyncIntervalLimiter, _market
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.event_guard import EventAwareGateway, event_phase
from liquidity_bot.execution_ledger import ExecutionLedger, LedgerConflict
from liquidity_bot.market_selector import RewardMarketSelector, RewardSelectionPolicy
from liquidity_bot.live_orders import AsyncTradingGateway, LiveOrderExecutor, OrderRequest
from decimal import Decimal


NOW = datetime(2026, 9, 8, 16, 26, tzinfo=timezone.utc)


def market_row(slug="aec-fixture"):
    return {"slug": slug, "active": True, "closed": False,
            "status": "MARKET_STATUS_OPEN", "gameStartTime": "2026-09-08T21:00:00Z",
            "orderPriceMinTickSize": {"value": "0.01", "currency": "USD"},
            "minimumTradeQty": "1", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
            "bestBidQuote": {"value": "0.49"}, "bestAskQuote": {"value": "0.51"}}


def event_row(slug="fixture", **state):
    return {"slug": slug, "markets": [market_row("aec-" + slug)],
            "live": False, "ended": False, **state}


class EventGuardTests(unittest.IsolatedAsyncioTestCase):
    def gateway(self, events=None, ledger=None):
        self.now = NOW
        self.events = AsyncMock(return_value={"events": events or [event_row()]})
        client = SimpleNamespace(events=SimpleNamespace(list=self.events),
            markets=SimpleNamespace(retrieve_by_slug=AsyncMock(
                return_value={"market": market_row()})))
        gate = EventAwareGateway(client, now=lambda: self.now, ledger=ledger)
        gate.limiter = SimpleNamespace(wait=AsyncMock())
        return gate

    def test_observed_incident_state_beats_future_clock_and_false_group(self):
        for slug, period, score in (
            ("wtt-matmut-danpin-2026-09-08", "S4", "10-12, 11-3, 10-12, 8-6"),
            ("wtt-vinpic-tugyil-2026-09-08", "S5", "9-11, 11-9, 11-2, 9-11, 5-4"),
            ("wtt-debviv-tsvgeo-2026-09-08", "S3", "11-9, 11-13, 8-4")):
            with self.subTest(slug=slug):
                gate = self.gateway()
                event = event_row(slug, live=True, period=period, score=score,
                                  marketGroups=[{"live": False}])
                gate._consume_events([event], NOW)
                market = gate.apply_event_status(_market(event["markets"][0]))
                self.assertEqual(market.game_start, NOW)
                self.assertFalse(market.event_allows_entry(NOW))
                selector = RewardMarketSelector(gate, RewardSelectionPolicy())
                self.assertFalse(selector._catalog_market_is_executable(
                    market, now=NOW, window_end=NOW + timedelta(hours=6)))

    def test_phase_nested_conflict_and_unknown(self):
        for state in ({"live": True}, {"ended": True}, {"period": "S1"},
                      {"period": "1H"}, {"score": "0-1"},
                      {"eventState": {"live": True}},
                      {"metadata": {"gameState": {"period": "S5"}}}):
            self.assertEqual(event_phase(event_row(**state)), "started")
        for state in ({}, {"live": "false", "ended": False}, {"period": "new-code"},
                      {"live": False}, {"score": "0-0"},
                      {"live": False, "ended": False, "metadata": "bad"}):
            self.assertEqual(event_phase(state), "unknown", state)
        self.assertEqual(event_phase({"period": "NS"}), "pregame")
        self.assertEqual(event_phase(event_row(score="0-0")), "pregame")

    async def test_freshness_exact_boundary_and_batch_load(self):
        rows = [event_row(f"fixture-{n}") for n in range(32)]
        gate = self.gateway(rows)
        slugs = [e["markets"][0]["slug"] for e in rows]
        await gate.refresh_event_states(slugs)
        market = gate.apply_event_status(_market(rows[0]["markets"][0]))
        self.assertTrue(market.event_allows_entry(NOW))
        self.assertTrue(market.event_allows_entry(NOW + timedelta(seconds=29.999)))
        self.assertFalse(market.event_allows_entry(NOW + timedelta(seconds=30)))
        self.assertFalse(market.event_allows_entry(NOW - timedelta(microseconds=1)))
        await gate.refresh_event_states(slugs)
        self.events.assert_awaited_once()
        self.assertEqual(len(self.events.call_args.args[0]["slug"]), 32)
        self.now += timedelta(seconds=30)
        await gate.refresh_event_states(slugs)
        self.assertEqual(self.events.await_count, 2)

    async def test_failure_blocks_entry_preserves_contract_and_bounded_retry(self):
        gate = self.gateway()
        await gate.refresh_event_states(["aec-fixture"])
        self.now += timedelta(seconds=30)
        self.events.side_effect = AdapterError("503")
        market = await gate.fetch_market("aec-fixture")
        self.assertIsNotNone(market.game_start)
        self.assertFalse(market.event_allows_entry(self.now))
        await gate.refresh_event_states(["aec-fixture"])
        self.assertEqual(self.events.await_count, 2)
        self.now += timedelta(seconds=10)
        self.events.side_effect = None
        market = await gate.fetch_market("aec-fixture")
        self.assertTrue(market.event_allows_entry(self.now))

    async def test_contract_batch_logs_why_event_evidence_is_unfresh(self):
        import contextlib
        import io
        from unittest.mock import patch
        parent = EventAwareGateway.__mro__[1]
        market = _market(market_row())
        fetch = AsyncMock(return_value={"aec-fixture": market})
        gate = self.gateway()
        self.events.side_effect = AdapterError("event lifecycle batch has missing events")
        with patch.object(parent, "fetch_markets_by_slugs", fetch):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = await gate.fetch_markets_by_slugs(["aec-fixture"])
            self.assertIn("[event-evidence-unfresh]", out.getvalue())
            self.assertIn("markets=1/1", out.getvalue())
            self.assertIn("missing events", out.getvalue())
            self.assertFalse(result["aec-fixture"].event_allows_entry(self.now))
            repeat = io.StringIO()
            with contextlib.redirect_stdout(repeat):
                await gate.fetch_markets_by_slugs(["aec-fixture"])
            self.assertEqual(repeat.getvalue(), "", "logged at most once a minute")
            self.now += timedelta(seconds=61)
            self.events.side_effect = None
            fresh = io.StringIO()
            with contextlib.redirect_stdout(fresh):
                result = await gate.fetch_markets_by_slugs(["aec-fixture"])
            self.assertEqual(fresh.getvalue(), "")
            self.assertTrue(result["aec-fixture"].event_allows_entry(self.now))

    async def test_exact_membership_cannot_be_inferred_or_removed(self):
        gate = self.gateway()
        await gate.refresh_event_states(["aec-fixture"])
        for rows in ([], [event_row("other")], [event_row(), event_row()],
                     [event_row(markets=[market_row("aec-other")])]):
            self.events.return_value = {"events": rows}
            with self.assertRaises(AdapterError):
                await gate.refresh_event_states(["aec-fixture"], force=True)
            self.assertFalse(gate.apply_event_status(_market(market_row())).event_allows_entry(NOW))

    def test_started_is_durable_and_never_moved_later_or_reopened(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.db"
            ledger = ExecutionLedger(path)
            gate = self.gateway(ledger=ledger)
            gate._consume_events([event_row(live=True)], NOW)
            gate._consume_events([event_row()], NOW + timedelta(seconds=10))
            self.assertFalse(gate.apply_event_status(_market(market_row())).event_allows_entry(NOW))
            ledger.record_event_starts({"aec-fixture": ("fixture", NOW + timedelta(hours=1))})
            with self.assertRaises(LedgerConflict):
                ledger.record_event_starts({"aec-fixture": ("different", NOW)})
            ledger.close()
            ledger = ExecutionLedger(path)
            try:
                restarted = self.gateway(ledger=ledger)
                market = restarted.apply_event_status(_market(market_row()))
                self.assertEqual(market.event_started_at, NOW)
                self.assertEqual(market.game_start, NOW)
                self.assertFalse(market.event_allows_entry(NOW))
                no_clock = restarted.apply_event_status(replace(market, game_start=None))
                self.assertEqual(no_clock.game_start, NOW)
            finally:
                ledger.close()

    async def test_one_missing_event_does_not_cancel_other_proven_pregame_owners(self):
        gate = self.gateway([event_row("one"), event_row("two")])
        await gate.refresh_event_states(["aec-one", "aec-two"])
        self.now += timedelta(seconds=30)
        self.events.return_value = {"events": [event_row("one")]}
        with self.assertRaises(AdapterError):
            await gate.refresh_event_states(["aec-one", "aec-two"])
        self.assertTrue(gate.event_entry_allowed("aec-one"))
        self.assertFalse(gate.event_entry_allowed("aec-two"))

    async def test_discovery_preserves_parent_status_before_flattening(self):
        gate = self.gateway([event_row(live=True)])
        markets = await gate.list_upcoming_sports_markets(
            start=NOW, end=NOW + timedelta(days=1), require_complete=True)
        self.assertEqual(len(markets), 1)
        self.assertFalse(markets[0].event_allows_entry(NOW))
        self.assertEqual(markets[0].game_start, NOW)

    def test_lifecycle_reads_use_safety_not_discovery_lane(self):
        self.assertEqual(AsyncIntervalLimiter._traffic_class(
            "events.list(lifecycle-batch)", "read"), "safety")

    async def test_expiry_during_admission_never_sends_or_creates_unknown_intent(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = ExecutionLedger(Path(root) / "ledger.db")
            try:
                gate = self.gateway(ledger=ledger)
                await gate.refresh_event_states(["aec-fixture"])
                market = gate.apply_event_status(_market(market_row()))
                gate.client.orders = SimpleNamespace(create=AsyncMock(return_value={"id": "reducer"}))
                async def wait():
                    self.now = NOW + timedelta(seconds=30)
                gate.limiter.wait = wait
                executor = LiveOrderExecutor(AsyncTradingGateway(gate), ledger)
                request = OrderRequest("aec-fixture", "bid", Decimal("0.49"), Decimal("10"))
                with self.assertRaises(ApiAdmissionDeferred):
                    await executor.place(request, market, now=NOW)
                gate.client.orders.create.assert_not_awaited()
                self.assertFalse(ledger.unresolved_intents())
                self.assertEqual(ledger.query("SELECT state FROM execution_intents")[0]["state"], "FAILED")
                # Missing event proof is never an exit veto.
                await executor.place(replace(request, reducing=True), market, now=self.now)
                gate.client.orders.create.assert_awaited_once()
            finally:
                ledger.close()

    async def test_selection_uses_completed_read_clock_not_scan_start(self):
        from test_market_selector import incentive, book
        gate = self.gateway()
        async def delayed_events(params):
            self.now = NOW + timedelta(seconds=2)
            return {"events": [event_row()]}
        self.events.side_effect = delayed_events
        gate.fetch_incentives = AsyncMock(return_value={"aec-fixture": incentive("aec-fixture", pool=20000)})
        gate.fetch_book = AsyncMock(return_value=book("aec-fixture"))
        selector = RewardMarketSelector(gate, clock=lambda: self.now)
        selection = await selector.select(now=NOW)
        self.assertEqual(selection.executable_catalog_markets, 1)
        self.assertIsNotNone(selection.selected)

    async def test_long_full_scan_does_not_misclassify_expired_event_receipt_as_bad_economics(self):
        from test_market_selector import incentive, book
        gate = self.gateway()
        gate.fetch_incentives = AsyncMock(return_value={
            "aec-fixture": incentive("aec-fixture", pool=20000)})

        async def delayed_book(_slug):
            self.now = NOW + timedelta(seconds=411.6)
            return book("aec-fixture")

        gate.fetch_book = delayed_book
        selection = await RewardMarketSelector(
            gate, clock=lambda: self.now).select(now=NOW)

        # Selection is ranking evidence only. The candidate stays visible, but
        # its old event receipt cannot authorize an order.
        self.assertIsNotNone(selection.selected)
        self.assertFalse(selection.selected.market.event_allows_entry(self.now))
        self.assertTrue(selection.selected.market.game_start > self.now)
        # A real exact-parent refresh, not a local timestamp renewal, restores
        # fresh execution authority at the later boundary.
        await gate.refresh_event_states(["aec-fixture"], force=True)
        refreshed = gate.apply_event_status(selection.selected.market)
        self.assertTrue(refreshed.event_allows_entry(self.now))
