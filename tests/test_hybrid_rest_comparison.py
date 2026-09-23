"""Identical fake-venue workloads for the REST baseline and optional hybrid.

Counts are SDK read calls; fake-clock times exclude network and limiter latency.
No credentials or live ledger are used.
"""
import asyncio
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
import test_rest_reducer_continuity as continuity
from test_hybrid_account import SilentStream
from test_venue_state import raw_execution, raw_order
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.errors import ApiAdmissionDeferred
from liquidity_bot.hybrid_stream import HybridGap
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind as Kind
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class HybridRestComparisonTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self, mode, count=30):
        self.now = fixtures.NOW
        with patch.object(fixtures, "CommissioningRunner", partial(
                AccountSourceRunner, account_state_mode=mode)):
            runner, client, _ = self.fixture.build_runner(
                clock=lambda: self.now, max_markets=32)
        runner._websocket_factory = SilentStream
        runner.rest_polling_policy = RestPollingPolicy(enabled=True)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        slugs = tuple(f"market-{i}" for i in range(32))
        runner._bind_markets(slugs)
        for i in range(count):
            slug = slugs[i]
            self.fixture.seed_working_order(runner, slug=slug, side="bid", suffix=str(i))
            client.orders.rows[f"exchange-{i}"] = raw_order(
                id=f"exchange-{i}", marketSlug=slug, side="BUY",
                intent="ORDER_INTENT_BUY_LONG", quantity="10", cumQuantity="0",
                state="ORDER_STATE_NEW", createTime=self.now.isoformat(),
                price={"value": "0.49", "currency": "USD"})
        await runner._rest_reconcile(self.now, tag="baseline")
        return runner, client

    def event(self, client, i=0, *, terminal=False):
        order = dict(client.orders.rows[f"exchange-{i}"])
        # A delayed/repeated ACK retains its venue order timestamp. Receipt
        # time must not invent an order update newer than the REST row.
        return PrivateEvent(Kind.ORDER_UPDATE, {"execution": raw_execution(
            id=f"event-{i}-{self.now.isoformat()}", order=order,
            type="EXECUTION_TYPE_CANCELED" if terminal else "EXECUTION_TYPE_NEW",
            transactTime=self.now.isoformat())}, "orders", received_at=self.now)

    @contextmanager
    def reads(self, client):
        counts = Counter()
        with ExitStack() as stack:
            for obj, method, label in (
                    (client.orders, "list", "orders"),
                    (client.orders, "retrieve", "exact"),
                    (client.portfolio, "positions", "positions"),
                    (client.portfolio, "activities", "history"),
                    (client.account, "balances", "balance")):
                original = getattr(obj, method)
                async def counted(*args, _fn=original, _label=label, **kwargs):
                    counts[_label] += 1
                    return await _fn(*args, **kwargs)
                stack.enter_context(patch.object(obj, method, counted))
            yield counts

    async def test_quiet_ack_and_gap_workloads_do_not_add_rest_reads(self):
        results = {}
        for mode, traffic in (("rest", "quiet"), ("hybrid", "quiet"),
                              ("hybrid", "ack"), ("hybrid", "gap")):
            runner, client = await self.build(mode)
            with self.reads(client) as counts:
                for tick in range(1, 21):
                    self.now = fixtures.NOW + timedelta(seconds=tick / 2)
                    if traffic == "ack":
                        await runner.private_stream._put(self.event(client, tick % 30))
                    elif traffic == "gap":
                        await runner.private_stream._put(HybridGap("disconnected"))
                    await runner._refresh_execution_state_for_decision(self.now)
            results[mode, traffic] = dict(counts)
            self.assertEqual(len(runner.ledger.working_orders()), 30)
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        baseline = results["rest", "quiet"]
        self.assertEqual(baseline, {"orders": 4, "positions": 2, "history": 2})
        for name, result in results.items():
            self.assertEqual(result, baseline, name)

    async def test_rest_older_than_private_order_still_requires_exact_read(self):
        runner, client = await self.build("hybrid")
        self.now += timedelta(seconds=1)
        event = self.event(client)
        event.payload["execution"]["order"]["lastTransactTime"] = self.now.isoformat()
        await runner.private_stream._put(event)
        await runner._drain_private_events()
        self.now += timedelta(seconds=4)
        with self.reads(client) as counts:
            await runner._refresh_execution_state_for_decision(self.now)
        # A list lagging a positively observed private update is not eligible
        # for the unchanged-order shortcut. Performance must not erase this.
        self.assertEqual(counts["exact"], 1)

    async def test_terminal_wave_saves_exact_reads_with_same_final_state(self):
        results = {}
        for mode in ("rest", "hybrid"):
            runner, client = await self.build(mode)
            with self.reads(client) as counts:
                self.now = fixtures.NOW + timedelta(seconds=1)
                for i in range(30):
                    row = client.orders.rows[f"exchange-{i}"]
                    row["state"] = "ORDER_STATE_CANCELED"
                    row["lastTransactTime"] = self.now.isoformat()
                    if mode == "hybrid":
                        await runner.private_stream._put(self.event(client, i, terminal=True))
                await runner._refresh_execution_state_for_decision(self.now)
                self.assertEqual(sum(counts.values()), 0)
                self.now = fixtures.NOW + timedelta(seconds=5)
                await runner._refresh_execution_state_for_decision(self.now)
            results[mode] = counts
            self.assertFalse(runner.ledger.working_orders())
            self.assertFalse(runner.ledger.unresolved_intents())
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
            self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertEqual(results["rest"]["exact"], 30)
        self.assertEqual(results["hybrid"]["exact"], 0)
        self.assertGreaterEqual(sum(results["rest"].values()) -
                                sum(results["hybrid"].values()), 30)

    async def test_skipped_periodic_poll_cannot_clear_unproved_terminal_hint(self):
        runner, client = await self.build("hybrid", count=1)
        self.now += timedelta(seconds=1)
        client.orders.rows["exchange-0"]["state"] = "ORDER_STATE_CANCELED"
        await runner.private_stream._put(self.event(client, terminal=True))
        with self.reads(client) as counts:
            await runner._rest_reconcile(self.now, tag="periodic")
        self.assertEqual(sum(counts.values()), 0)
        self.assertTrue(runner._hybrid_requires_reconcile)
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create({"marketSlug": "market-0"})
        self.assertFalse(client.orders.create_calls)
        self.now += timedelta(seconds=4)
        with self.reads(client) as counts:
            await runner._rest_reconcile(self.now, tag="periodic")
        self.assertGreater(counts["orders"], 0)
        self.assertGreater(counts["positions"], 0)
        self.assertGreater(counts["history"], 0)
        self.assertFalse(runner._hybrid_requires_reconcile)

    async def test_position_hint_advances_detection_without_weakening_rest_proof(self):
        detected = {}
        for mode in ("rest", "hybrid"):
            runner, client = await self.build(mode, count=0)
            self.now = fixtures.NOW + timedelta(seconds=1)
            client.portfolio.rows["market-0"] = {
                "netPositionDecimal": "15", "cost": {"value": "7.35", "currency": "USD"}}
            if mode == "hybrid":
                await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "positions"))
            with self.reads(client) as counts:
                await runner._refresh_execution_state_for_decision(self.now)
                if runner.ledger.position("market-0") == 15:
                    detected[mode] = 1
                    self.assertEqual(counts["positions"], 1)
                else:
                    self.assertEqual(sum(counts.values()), 0)
                    self.now = fixtures.NOW + timedelta(seconds=5)
                    await runner._refresh_execution_state_for_decision(self.now)
                    detected[mode] = 5
                self.assertEqual(runner.ledger.position("market-0"), 15)
                self.assertGreater(counts["history"], 0)
                self.assertGreater(counts["orders"], 0)
        self.assertEqual(detected, {"rest": 5, "hybrid": 1})

    async def test_reducer_replacement_saves_read_with_same_price_and_quantity(self):
        results = {}
        for mode in ("rest", "hybrid"):
            self.now = fixtures.NOW
            original_build = self.fixture.build_runner
            def build_runner(**kwargs):
                runner, client, notifier = original_build(**kwargs)
                runner._websocket_factory = SilentStream
                return runner, client, notifier
            with patch.object(continuity, "RestFallbackRunner", partial(
                    AccountSourceRunner, account_state_mode=mode)), \
                    patch.object(self.fixture, "build_runner", build_runner):
                runner, client, held = await continuity.RestReducerContinuityTests.build(self)
            runner._bind_markets((held, "flat"))
            original_cancel = client.orders.cancel
            async def cancel(*args):
                result = await original_cancel(*args)
                if mode == "hybrid":
                    await runner.private_stream._put(PrivateEvent(Kind.ORDER_UPDATE,
                        {"execution": raw_execution(id="cancel-compare",
                            order=dict(client.orders.rows[args[0]]),
                            type="EXECUTION_TYPE_CANCELED", transactTime=self.now.isoformat())},
                        "orders", received_at=self.now))
                return result
            original_decide = runner._decide_for_market
            visited = []
            async def decide(slug, now):
                visited.append(slug)
                if slug != held:
                    self.assertEqual(len(client.orders.create_calls), 1)
                    return None, 0
                runner.state_for(held).book_observed_at = self.now
                return await original_decide(slug, now)
            with self.reads(client) as counts, \
                    patch.object(client.orders, "cancel", cancel), \
                    patch.object(runner, "_decide_for_market", decide), \
                    patch.object(runner, "_refresh_equity_for_decision", AsyncMock()):
                await runner._decision_sweep((held, "flat"))
            self.assertEqual(visited[:2], [held, held])
            self.assertEqual(len(client.orders.cancel_calls), 1)
            self.assertEqual(len(client.orders.create_calls), 1)
            payload = client.orders.create_calls[0]
            self.assertEqual(Decimal(payload["quantity"]), 10)
            results[mode] = (dict(counts), payload["quantity"], payload["price"], payload["intent"])
        self.assertEqual(results["hybrid"][1:], results["rest"][1:])
        self.assertEqual(results["rest"][0],
                         {"exact": 1, "orders": 1, "positions": 1, "history": 1})
        self.assertEqual(results["hybrid"][0],
                         {"orders": 1, "positions": 1, "history": 1})

    async def run_scan(self, runner, client, traffic, *, step_seconds=.1):
        reads = 0
        async def scan(*args, **kwargs):
            nonlocal reads
            async def read():
                nonlocal reads
                reads += 1
                self.now += timedelta(seconds=step_seconds)
                if traffic == "ack":
                    await runner.private_stream._put(self.event(client))
                elif traffic == "gap":
                    await runner.private_stream._put(HybridGap("disconnected"))
                elif traffic == "position":
                    await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "positions"))
                await asyncio.sleep(0)
                return {}
            for _ in range(30):
                await runner.gateway._call("events.list", read)
            return ((), "fake", None)
        with patch.object(CommissioningRunner, "_replacement_candidates_for_top_up", scan):
            await runner._replacement_candidates_for_top_up(self.now)
        return reads

    async def test_harmless_stream_traffic_keeps_scan_and_rest_watch_progress(self):
        results = {}
        for mode, traffic in (("rest", "quiet"), ("hybrid", "quiet"),
                              ("hybrid", "ack"), ("hybrid", "gap")):
            runner, client = await self.build(mode)
            with self.reads(client) as counts:
                count = await self.run_scan(runner, client, traffic)
            self.assertEqual(count, 30)
            self.assertGreater(counts["positions"], 0)
            results[mode, traffic] = dict(counts)
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        for result in results.values():
            self.assertEqual(result, results["rest", "quiet"])

    async def test_long_read_only_scan_keeps_same_rest_watch_without_renewing_gate(self):
        results = {}
        for mode in ("rest", "hybrid"):
            runner, client = await self.build(mode, count=0)
            before = runner.gate.last_reconcile_at
            with self.reads(client) as counts:
                self.assertEqual(await self.run_scan(
                    runner, client, "quiet", step_seconds=2), 30)
            results[mode] = dict(counts)
            self.assertGreater(counts["positions"], 0)
            self.assertEqual(runner.gate.last_reconcile_at, before)
            self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
        self.assertEqual(results["rest"], results["hybrid"])

    async def test_scan_still_yields_for_queued_and_already_applied_uncertainty(self):
        for applied in (False, True):
            runner, client = await self.build("hybrid")
            if applied:
                await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "positions"))
                await runner._drain_private_events()
                self.assertEqual(runner.private_stream.pending, 0)
            with self.assertRaises(ApiAdmissionDeferred):
                await self.run_scan(runner, client, "position")
            self.assertTrue(runner._hybrid_requires_reconcile)
            self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)
