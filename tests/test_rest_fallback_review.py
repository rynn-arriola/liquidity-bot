"""Moving-clock, full-slate and degraded-source regressions; fake venue only."""
import asyncio
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from test_venue_state import raw_execution, raw_order
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred, RuntimeInvariantViolation
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.rest_fallback import RestFallbackRunner


class RestFallbackReviewTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    def build(self):
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            return self.fixture.build_runner(clock=lambda: self.now, max_markets=32)

    async def test_slow_proof_refreshes_equity_before_planning_not_false_cancel(self):
        runner, _, _ = self.build()
        await runner.preflight()
        runner._evaluate_rewards(self.now)
        self.fixture.seed_working_order(
            runner, slug=runner.market_slug, side="bid", suffix="timing")
        self.assertTrue(runner._frame(self.now).new_risk_allowed)
        self.now += timedelta(seconds=5)
        frames = []

        async def slow_proof(*args, **kwargs):
            self.now += timedelta(seconds=16)
            runner.gate.on_rest_reconciled(self.now)

        async def inspect(slug, now):
            runner.state_for(slug).book_observed_at = now
            frame = runner._frame(now, slug)
            frames.append(frame)
            self.assertFalse(any(
                a.reason.endswith("account_new_risk_paused")
                for a in runner.reconciler.plan(frame)))
            return None, 0.0

        with patch.object(CommissioningRunner, "_rest_reconcile", slow_proof), \
                patch.object(runner, "_decide_for_market", inspect), \
                patch.object(runner, "_refresh_balance", wraps=runner._refresh_balance) as balance:
            await runner._decision_sweep((runner.market_slug,))
        self.assertEqual(balance.await_count, 1)
        self.assertTrue(frames[0].new_risk_allowed)
        self.assertTrue(frames[0].state_known)

    def test_temporary_cap_30_preserves_lower_limits_at_all_clock_bands(self):
        runner, _, _ = self.build()
        original, _, _ = self.fixture.build_runner(max_markets=32)
        for hours in (0, 7, 8, 9, 13, 23, 24, 32):
            now = self.now + timedelta(hours=hours)
            for loss_cap in (0, 10, 15, 20, 25, 30, 35, 40, 64):
                runner._loss_entry_order_cap = original._loss_entry_order_cap = loss_cap
                self.assertEqual(runner._effective_entry_order_cap(now), min(
                    30, original._effective_entry_order_cap(now)))
        self.assertIsNone(original.PENDING_ENTRY_ORDER_CEILING)
        self.assertEqual(runner.config.max_markets, 32)
        self.assertEqual(runner.config.hard_stop_loss_usd, original.config.hard_stop_loss_usd)

    def test_thirtieth_entry_and_unresolved_reservation_use_same_cap(self):
        runner, _, _ = self.build()
        self.now += timedelta(hours=12)
        runner._loss_entry_order_cap = 40
        for i in range(29):
            self.fixture.seed_working_order(
                runner, slug=f"market-{i}", side="bid", suffix=str(i))
        self.assertFalse(runner._loss_cap_blocks_new_entry(self.now))
        runner.ledger.prepare_intent(
            "reservation", "PLACE", "market-reserved", "client-reserved",
            {"marketSlug": "market-reserved"}, created_at=self.now)
        self.assertTrue(runner._loss_cap_blocks_new_entry(self.now))

    async def test_reads_overlap_boundedly_but_visit_every_owner(self):
        runner, _, _ = self.build()
        active = maximum = 0
        visited = []

        async def read(order_id, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(.001)
                visited.append(order_id)
            finally:
                active -= 1

        with patch.object(runner.executor, "reconcile_order", read):
            failures = await runner._read_owned_order_states(
                [str(i) for i in range(40)], self.now)
        self.assertEqual(failures, [])
        self.assertEqual(set(visited), {str(i) for i in range(40)})
        self.assertEqual(maximum, 4)
        self.assertEqual(active, 0)

    async def test_failed_order_does_not_skip_other_owners_or_hide_invariant(self):
        runner, _, _ = self.build()
        visited = []
        async def read(order_id, **kwargs):
            visited.append(order_id)
            if order_id == "bad":
                raise AdapterError("unavailable", status_code=503)
        with patch.object(runner.executor, "reconcile_order", read):
            failures = await runner._read_owned_order_states(["bad", "good"], self.now)
        self.assertEqual(visited, ["bad", "good"])
        self.assertEqual(failures[0][0], "bad")
        with patch.object(runner.executor, "reconcile_order", AsyncMock(
                side_effect=RuntimeInvariantViolation("test", "unsafe"))):
            with self.assertRaises(RuntimeInvariantViolation):
                await runner._read_owned_order_states(["bad"], self.now)

    async def test_complete_proof_reads_40_orders_including_above_entry_ceiling(self):
        runner, client, _ = self.build()
        await runner.preflight()
        for i in range(40):
            slug, side, suffix = f"market-{i // 2}", "bid" if i % 2 == 0 else "offer", str(i)
            self.fixture.seed_working_order(runner, slug=slug, side=side, suffix=suffix)
            client.orders.rows[f"exchange-{suffix}"] = raw_order(
                id=f"exchange-{suffix}", marketSlug=slug,
                side="BUY" if side == "bid" else "SELL",
                price={"value": "0.49", "currency": "USD"},
                quantity="10", cumQuantity="0", state="ORDER_STATE_NEW",
                createTime=self.now.isoformat())
        self.now += timedelta(seconds=1)
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as retrieve:
            await runner._rest_reconcile(self.now, strict_order_markets=frozenset())
        self.assertEqual(retrieve.await_count, 40)
        self.assertEqual(len(runner.ledger.working_orders()), 40)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_cancelled_read_batch_leaves_no_workers_running(self):
        runner, _, _ = self.build()
        active = 0
        started = asyncio.Event()
        async def read(*args, **kwargs):
            nonlocal active
            active += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
        with patch.object(runner.executor, "reconcile_order", read):
            task = asyncio.create_task(runner._read_owned_order_states(
                [str(i) for i in range(30)], self.now))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(active, 0)

    async def test_expired_complete_proof_is_retryable_not_a_startup_refusal(self):
        runner, _, _ = self.build()
        runner.gate.on_connect()
        async def slow(*args, **kwargs):
            runner._rest_history_pending = frozenset({"applied-fill"})
            self.now += timedelta(seconds=31)
        with patch.object(CommissioningRunner, "_rest_reconcile", slow):
            with self.assertRaises(ApiAdmissionDeferred) as raised:
                await runner._rest_reconcile(self.now)
        self.assertTrue(runner._is_transient_recovery_error(raised.exception))
        self.assertFalse(runner._is_venue_unavailable(raised.exception))
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertEqual(runner._rest_history_seen, frozenset({"applied-fill"}))

    async def test_one_cancelled_read_cleans_up_other_workers(self):
        runner, _, _ = self.build()
        active = 0
        async def read(order_id, **kwargs):
            nonlocal active
            active += 1
            try:
                await asyncio.sleep(0)
                if order_id == "cancelled":
                    raise asyncio.CancelledError()
                await asyncio.Event().wait()
            finally:
                active -= 1
        with patch.object(runner.executor, "reconcile_order", read):
            with self.assertRaises(asyncio.CancelledError):
                await runner._read_owned_order_states(["cancelled", "waiting"], self.now)
        self.assertEqual(active, 0)

    async def test_startup_500_keeps_retry_path_and_then_recovers(self):
        runner, client, _ = self.build()
        original = runner._refresh_contracts
        count = 0
        async def once(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                raise AdapterError("temporary server error", status_code=500)
            return await original(*args, **kwargs)
        with patch.object(runner, "_refresh_contracts", once):
            await runner.preflight_until_ready()
        self.assertGreaterEqual(count, 2)
        self.assertTrue(runner.gate.ready(runner.ledger, now=self.now))
        self.assertFalse(client.orders.create_calls)

    def history(self, client, total=201):
        calls = []
        async def pages(params):
            page = int(params.get("cursor", "0"))
            calls.append(dict(params))
            end = min((page+1)*100, total)
            return {"activities": [{"type": "ACTIVITY_TYPE_TRADE", "trade": {
                "id": f"trade-{i}", "isAggressor": False,
                "passiveExecution": raw_execution(id=f"execution-{i}")}}
                for i in range(page*100, end)], "eof": end == total,
                "nextCursor": str(page+1) if end < total else ""}
        client.portfolio.activities = pages
        return calls

    async def test_first_history_walk_reaches_fill_201_and_eof(self):
        runner, client, _ = self.build()
        calls = self.history(client)
        batch = await runner._fetch_execution_history(self.now)
        self.assertEqual(len(batch), 201)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c["sortOrder"] == "SORT_ORDER_DESCENDING" for c in calls))
        # An uncommitted proof cannot establish next-poll coverage.
        self.assertIsNone(runner._rest_history_seen)

    async def test_history_overlap_needs_whole_known_page_not_one_known_fill(self):
        runner, client, _ = self.build()
        calls = self.history(client, total=350)
        runner._rest_history_seen = frozenset({"execution-100"})
        batch = await runner._fetch_execution_history(self.now)
        self.assertEqual(len(batch), 350)
        self.assertEqual(len(calls), 4)
        calls.clear()
        runner._rest_history_seen = frozenset(f"execution-{i}" for i in range(350))
        self.assertEqual(len(await runner._fetch_execution_history(self.now)), 200)
        self.assertEqual(len(calls), 2)

    async def test_history_exhaustion_and_repeated_cursor_fail_closed(self):
        runner, client, _ = self.build()
        self.history(client, total=201)
        runner.gateway.max_pages = 2
        with self.assertRaisesRegex(AdapterError, "bounded page limit"):
            await runner._fetch_execution_history(self.now)
        runner.gateway.max_pages = 100
        client.portfolio.activities = AsyncMock(return_value={
            "activities": [], "eof": False, "nextCursor": "same"})
        with self.assertRaisesRegex(AdapterError, "repeated pagination cursor"):
            await runner._fetch_execution_history(self.now)
        self.assertIsNone(runner._rest_history_seen)

    async def test_history_missing_eof_and_malformed_fill_are_not_proof(self):
        runner, client, _ = self.build()
        client.portfolio.activities = AsyncMock(return_value={"activities": []})
        with self.assertRaisesRegex(AdapterError, "EOF"):
            await runner._fetch_execution_history(self.now)
        client.portfolio.activities = AsyncMock(return_value={"activities": [
            {"trade": {"isAggressor": False, "passiveExecution": {
                "type": "EXECUTION_TYPE_FILL"}}}], "eof": True})
        with self.assertRaises(AdapterError):
            await runner._fetch_execution_history(self.now)

    async def test_empty_retained_history_is_valid_but_not_a_flatness_proof(self):
        runner, client, _ = self.build()
        client.portfolio.activities = AsyncMock(return_value={"activities": [], "eof": True})
        self.assertEqual(len(await runner._fetch_execution_history(self.now)), 0)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_known_fill_page_cannot_hide_a_repeated_cursor(self):
        runner, client, _ = self.build()
        runner._rest_history_seen = frozenset({"execution-one"})
        client.portfolio.activities = AsyncMock(return_value={"activities": [
            {"trade": {"isAggressor": False, "passiveExecution": raw_execution()}}],
            "eof": False, "nextCursor": "same"})
        with self.assertRaisesRegex(AdapterError, "repeated pagination cursor"):
            await runner._fetch_execution_history(self.now)

    async def test_missing_or_ambiguous_account_execution_is_not_silently_dropped(self):
        runner, client, _ = self.build()
        for activity in (None, {"type": "ACTIVITY_TYPE_TRADE"},
                         {"trade": {"id": "missing-execution"}},
                         {"trade": {"isAggressor": "false", "aggressorExecution": raw_execution()}}):
            client.portfolio.activities = AsyncMock(return_value={
                "activities": [activity], "eof": True})
            with self.assertRaises(AdapterError):
                await runner._fetch_execution_history(self.now)


def rest_variant(name):
    async def test(self):
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            await getattr(self.fixture, name)()
    return test


for name in (
    "test_position_orders_and_reducers_do_not_consume_entry_cap",
    "test_unsafe_startup_refusal_is_never_retried",
    "test_operator_stop_ends_startup_retries",
    "test_startup_does_not_call_schema_failure_maintenance",
    "test_ui_maintenance_pauses_only_new_risk",
    "test_loss_entry_cap_keeps_higher_ranked_market_pair",
    "test_loss_entry_cap_prefers_complete_pair_over_ranked_orphan",
):
    setattr(RestFallbackReviewTests, "test_rest_" + name[5:], rest_variant(name))
