"""Temporary account source: no socket shortcuts and no real venue writes."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import LedgerConflict
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.rest_fallback import (
    DisabledPrivateStream, RestFallbackRunner, RestStateGate)
from liquidity_bot.reconciler import ActionKind


class RestFallbackTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    def build(self):
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            return self.fixture.build_runner(clock=lambda: self.now)

    async def test_default_still_requires_real_private_snapshot(self):
        runner, _, _ = self.fixture.build_runner()
        self.assertIs(type(runner), CommissioningRunner)
        runner.gate.on_connect()
        runner.gate.on_rest_reconciled(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_rest_preflight_and_order_without_any_private_event(self):
        runner, client, _ = self.build()
        await runner.preflight()
        self.assertIsInstance(runner.private_stream, DisabledPrivateStream)
        self.assertIsInstance(runner.gate, RestStateGate)
        self.assertIsNone(runner.gate.last_message_at)
        self.assertFalse(runner.gate.balance_snapshot_seen)
        self.assertTrue(runner.gate.ready(runner.ledger, now=self.now))
        runner._evaluate_rewards(self.now)
        actions = runner.reconciler.plan(runner._frame(self.now))
        await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal(0), now=self.now)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(client.orders.create_calls[0]["quantity"],
                         int(runner.config.quote_size))
        await runner.market_stream.close()

    async def test_failed_history_revokes_proof_and_recovers(self):
        runner, _, _ = self.build()
        await runner.preflight()
        self.now += timedelta(seconds=1)
        with patch.object(runner.account, "fetch_executions",
                          AsyncMock(side_effect=AdapterError("history unavailable"))):
            with self.assertRaises(AdapterError):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.ready(runner.ledger, now=self.now))
        self.now += timedelta(seconds=1)
        await runner._rest_reconcile(self.now)
        self.assertTrue(runner.gate.ready(runner.ledger, now=self.now))

    async def test_failed_positions_revokes_proof_immediately(self):
        runner, _, _ = self.build()
        await runner.preflight()
        with patch.object(runner.account, "fetch_positions",
                          AsyncMock(side_effect=AdapterError("503"))):
            with self.assertRaises(AdapterError):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_fill_conflict_cannot_be_swallowed_in_rest_mode(self):
        runner, _, _ = self.build()
        await runner.preflight()
        with patch.object(runner.account, "fetch_executions", AsyncMock(return_value=[object()])), \
                patch.object(runner.ledger, "apply_execution", side_effect=LedgerConflict("conflicting fill")):
            with self.assertRaises(LedgerConflict):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_post_admission_expiry_is_failed_not_unknown_place(self):
        runner, client, _ = self.build()
        await runner.preflight()
        runner._evaluate_rewards(self.now)
        actions = runner.reconciler.plan(runner._frame(self.now))

        class SlowAdmission:
            async def wait(inner):
                self.now += timedelta(seconds=31)

        runner.gateway.limiter = SlowAdmission()
        with patch.object(runner.gateway, "event_entry_allowed", return_value=True):
            with self.assertRaisesRegex(ApiAdmissionDeferred, "REST account proof"):
                await runner.driver.apply_one(
                    actions, market=runner.market, position=Decimal(0), now=self.now)
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(runner.ledger.unresolved_intents())
        row = runner.ledger.query("SELECT state FROM execution_intents ORDER BY rowid DESC LIMIT 1")
        self.assertEqual(row[0]["state"], "FAILED")

    async def test_poll_duration_counts_toward_proof_age(self):
        runner, _, _ = self.build()
        runner.gate.on_connect()

        async def slow_proof(*args, **kwargs):
            self.now += timedelta(seconds=31)
            runner.gate.on_rest_reconciled(self.now)

        with patch.object(CommissioningRunner, "_rest_reconcile", slow_proof):
            with self.assertRaises(AdapterError):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_cancelled_proof_revokes_authority(self):
        runner, _, _ = self.build()
        await runner.preflight()
        with patch.object(CommissioningRunner, "_rest_reconcile",
                          AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await runner._rest_reconcile(self.now)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_scoped_recovery_cannot_skip_owned_orders(self):
        runner, _, _ = self.build()
        with patch.object(CommissioningRunner, "_rest_reconcile", AsyncMock()) as proof:
            await runner._rest_reconcile(self.now, strict_order_markets=frozenset())
        self.assertNotIn("strict_order_markets", proof.call_args.kwargs)

    async def test_gate_boundary_clock_rollback_and_ownership(self):
        runner, _, _ = self.build()
        runner.gate.on_connect()
        runner.gate.on_rest_reconciled(self.now)
        for age, expected in ((-1, False), (0, True), (30, True), (30.001, False)):
            self.assertEqual(runner.gate.session_ready(
                runner.ledger, now=self.now + timedelta(seconds=age)), expected)
        with patch.object(runner.ledger, "ownership_is_known", return_value=False):
            self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        runner.gate.on_disconnect()
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_admission_expiry_blocks_create_not_cancel(self):
        runner, client, _ = self.build()
        await runner.preflight()

        class SlowAdmission:
            async def wait(inner):
                self.now += timedelta(seconds=31)

        runner.gateway.limiter = SlowAdmission()
        with self.assertRaises(ApiAdmissionDeferred):
            await runner.executor.gateway.create({"marketSlug": "market-one"}, reducing=True)
        self.assertEqual(client.orders.create_calls, [])
        client.orders.rows["owned"] = {"state": "ORDER_STATE_OPEN"}
        await runner.executor.gateway.cancel("owned", "market-one")
        self.assertEqual(len(client.orders.cancel_calls), 1)

    async def test_idle_source_never_fabricates_messages_and_cancels_promptly(self):
        source = DisabledPrivateStream(object())
        await source.start(["market-one"])
        self.assertEqual(source.drain_events(), ())
        with self.assertRaises(TimeoutError):
            await source.next_event(timeout=0.001)
        task = asyncio.create_task(source.next_event())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await source.close()

    async def test_rebind_uses_rest_again_and_dirties_all_owners(self):
        runner, _, _ = self.build()
        await runner.preflight()
        runner._dirty_market_slugs.clear()
        self.now += timedelta(seconds=1)
        await runner._open_streams_for_active_market(strict_order_markets=frozenset())
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertIsNone(runner.gate.last_message_at)
        self.assertIn("market-one", runner._dirty_market_slugs)

    async def test_decision_poll_cadence_does_not_multiply_by_slot_count(self):
        runner, _, _ = self.build()
        await runner.preflight()
        with patch.object(runner, "_rest_reconcile", wraps=runner._rest_reconcile) as proof:
            for _ in range(40):
                await runner._refresh_execution_state_for_decision(self.now)
            self.assertEqual(proof.await_count, 0)
            self.now += timedelta(seconds=5)
            for _ in range(40):
                await runner._refresh_execution_state_for_decision(self.now)
            self.assertEqual(proof.await_count, 1)

    async def test_queued_book_is_consumed_without_held_task_rollback(self):
        runner, _, _ = self.build()
        await runner.preflight()
        held = replace(runner.book, received_at=self.now)
        self.now += timedelta(seconds=12)
        fresh = replace(runner.book, received_at=self.now)
        with patch.object(runner.market_stream, "drain_books", create=True,
                          return_value=(fresh,)):
            await runner._refresh_event_guard()
        self.assertEqual(runner.book_observed_at, self.now)
        runner._accept_book(held)
        self.assertEqual(runner.book_observed_at, self.now)
        self.assertIs(runner.book, fresh)

    async def test_public_failure_still_requires_rebind(self):
        runner, _, _ = self.build()
        await runner.preflight()
        with patch.object(runner.market_stream, "drain_books", create=True,
                          side_effect=AdapterError("public stream failed")):
            with self.assertRaises(AdapterError):
                await runner._refresh_event_guard()
        self.assertTrue(runner._streams_need_rebind)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_no_queued_book_does_not_refresh_old_price(self):
        runner, _, _ = self.build()
        await runner.preflight()
        original = runner.book_observed_at
        self.now += timedelta(seconds=12)
        with patch.object(runner.market_stream, "drain_books", create=True, return_value=()):
            await runner._refresh_event_guard()
        self.assertEqual(runner.book_observed_at, original)

    async def test_rest_fill_cancel_terminal_proof_then_exact_reducer(self):
        runner, client, _ = self.build()
        await runner.preflight()
        for side in ("bid", "offer"):
            self.fixture.seed_working_order(
                runner, slug="market-one", side=side, suffix=side)
            client.orders.rows[f"exchange-{side}"] = {
                "id": f"exchange-{side}", "marketSlug": "market-one",
                "side": "BUY" if side == "bid" else "SELL",
                "intent": "ORDER_INTENT_BUY_LONG" if side == "bid" else "ORDER_INTENT_SELL_LONG",
                "price": {"value": "0.49", "currency": "USD"},
                "quantity": "10", "cumQuantity": "10" if side == "bid" else "0",
                "state": "ORDER_STATE_FILLED" if side == "bid" else "ORDER_STATE_NEW",
                "createTime": self.now.isoformat(),
            }
        execution = {
            "id": "rest-fill", "tradeId": "rest-trade", "aggressor": False,
            "type": "EXECUTION_TYPE_FILL", "lastShares": "10",
            "lastPx": {"value": "0.49", "currency": "USD"},
            "commissionNotionalCollected": {"value": "0", "currency": "USD"},
            "transactTime": self.now.isoformat(),
            "order": client.orders.rows["exchange-bid"],
        }
        client.portfolio.trades = [{"id": "rest-trade", "isAggressor": True,
                                    "aggressorExecution": execution}]
        client.portfolio.rows["market-one"] = {
            "netPositionDecimal": "10.0000", "cost": {"value": "4.90", "currency": "USD"}}
        self.now += timedelta(seconds=1)
        await runner._rest_reconcile(self.now)
        self.assertEqual(runner.ledger.position("market-one"), Decimal("10"))
        runner._evaluate_rewards(self.now)
        actions = runner.reconciler.plan(runner._frame(self.now))
        self.assertEqual(actions[0].kind, ActionKind.CANCEL)
        await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal(10), now=self.now)
        self.assertTrue(runner.ledger.unresolved_intents())  # ACK is not terminal.
        self.now += timedelta(seconds=1)
        await runner._rest_reconcile(self.now)
        self.assertFalse(runner.ledger.unresolved_intents())
        self.assertFalse(runner.ledger.working_orders())
        actions = runner.reconciler.plan(runner._frame(self.now))
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].qty, Decimal(10))
        self.assertEqual(actions[0].side, "offer")
        await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal(10), now=self.now)
        self.assertEqual(client.orders.create_calls[-1]["quantity"], 10)


# Re-run selected permanent lifecycle regressions against the alternate source,
# using their original clients/ledger assertions instead of mocking the planner.
def _rest_variant(name):
    async def test(self):
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            await getattr(self.fixture, name)()
    return test


for _name in (
    "test_event_live_cancels_flat_quote_despite_future_clock",
    "test_event_live_forces_exit_without_waiting_inventory_timeout",
    "test_event_unknown_blocks_entry_but_not_passive_reduction",
    "test_unowned_exchange_order_refuses_and_never_mutates",
    "test_a_fill_only_the_venue_saw_still_reaches_the_stop",
    "test_a_cancel_is_not_re_issued_while_the_order_is_unknown",
    "test_only_hard_stop_has_money_loss_authority",
    "test_reward_cash_cannot_mask_the_hard_stop",
    "test_hard_equity_excludes_proven_nontrading_cash",
    "test_shutdown_does_not_submit_a_fourth_ioc",
    "test_unresolved_create_prevents_shutdown_flat_proof",
):
    setattr(RestFallbackTests, "test_rest_" + _name[5:], _rest_variant(_name))
