"""Fake-only regressions for late notifications between account proof and plan."""
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_hybrid_account as hybrid
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.errors import AdapterError
from liquidity_bot.execution_ledger import LedgerConflict
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind as Kind


class HybridPreplanTests(IsolatedAsyncioTestCase):
    setUp = hybrid.HybridRunnerTests.setUp
    build = hybrid.HybridRunnerTests.build
    order_event = hybrid.HybridRunnerTests.order_event

    async def test_late_ack_drained_after_event_guard_before_planning_without_rest(self):
        runner, client = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        event = self.order_event(status="ORDER_STATE_NEW", execution_id="late")
        event.payload["execution"]["type"] = "EXECUTION_TYPE_NEW"
        async def event_guard():
            await runner.private_stream._put(event)
        planned = []
        def plan(frame):
            planned.append((runner.private_stream.pending, runner._hybrid_requires_reconcile))
            return []
        with patch.object(runner, "_refresh_event_guard", event_guard), \
                patch.object(runner.reconciler, "plan", plan), \
                patch.object(runner, "_rest_reconcile", AsyncMock()) as proof:
            self.assertEqual(await runner._decide_for_market("market-one", self.now), (None, 0))
        proof.assert_not_awaited()
        self.assertEqual(planned, [(0, False)])
        self.assertFalse(client.orders.create_calls)

    async def test_applied_hint_waits_without_intent_or_mutation_admission(self):
        runner, client = await self.build()
        runner._evaluate_rewards(self.now)
        await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        with patch.object(runner, "_rest_reconcile", AsyncMock()) as proof:
            for _ in range(3):
                self.assertEqual(await runner._decide_for_market("market-one", self.now), (None, 1))
        self.assertFalse(runner.ledger.query("SELECT * FROM execution_intents"))
        self.assertFalse(client.orders.create_calls)
        proof.assert_not_awaited()

    async def test_pending_hint_does_not_block_required_cancel(self):
        runner, _ = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        runner._manual_entry_paused = True
        await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        with patch.object(runner.driver, "apply_one", AsyncMock(return_value=None)) as apply:
            await runner._decide_for_market("market-one", self.now)
        self.assertEqual(apply.await_count, 1)
        self.assertEqual(apply.call_args.args[0][0].kind.value, "cancel")

    async def test_projection_conflict_is_not_retired_as_a_plan_error(self):
        runner, _ = await self.build()
        with patch.object(runner, "_drain_private_events", AsyncMock(side_effect=LedgerConflict("conflict"))), \
                patch.object(runner, "_retire_market", AsyncMock()) as retire:
            with self.assertRaises(LedgerConflict):
                await runner._decide_for_market("market-one", self.now)
        retire.assert_not_awaited()


class HybridReducerPreplanTests(IsolatedAsyncioTestCase):
    setUp = hybrid.HybridRunnerTests.setUp
    async def build(self):
        original = self.fixture.build_runner
        def build_runner(**kwargs):
            runner, client, notifier = original(**kwargs)
            runner._websocket_factory = hybrid.SilentStream
            return runner, client, notifier
        with patch.object(hybrid.continuity, "RestFallbackRunner", partial(
                AccountSourceRunner, account_state_mode="hybrid")), \
                patch.object(self.fixture, "build_runner", build_runner):
            return await hybrid.continuity.RestReducerContinuityTests.build(self)

    async def uncovered(self):
        runner, client, held = await self.build()
        client.orders.rows["exchange-reducer"]["state"] = "ORDER_STATE_CANCELED"
        await runner._reconcile_position_state(held, tag="position-transition")
        runner._hybrid_reducer_proofs.clear()
        await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "hint"))
        return runner, client, held

    async def test_uncovered_reducer_gets_one_exact_proof_before_plan(self):
        runner, client, held = await self.uncovered()
        before = runner.gate.last_reconcile_at
        original = runner._reconcile_position_state
        with patch.object(runner, "_reconcile_position_state", wraps=original) as proof:
            await runner._decide_for_market(held, self.now)
        self.assertEqual(proof.await_count, 1)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(Decimal(client.orders.create_calls[0]["quantity"]), 10)
        self.assertEqual(runner.gate.last_reconcile_at, before)
        self.assertTrue(runner._hybrid_requires_reconcile)

    async def test_event_during_proof_yields_before_hard_exit_plan_and_intent(self):
        runner, client, held = await self.uncovered()
        token = runner.reconciler.hard_exit_schedule_token(held)
        original = runner.account.fetch_positions
        async def positions(**kwargs):
            await runner.private_stream._put(PrivateEvent(Kind.POSITION_UPDATE, {}, "during"))
            return await original(**kwargs)
        intents_before = len(runner.ledger.query("SELECT * FROM execution_intents"))
        with patch.object(runner.account, "fetch_positions", positions), \
                patch.object(runner.reconciler, "plan", side_effect=AssertionError("planned too early")):
            self.assertEqual(await runner._decide_for_market(held, self.now), (None, 1))
        self.assertEqual(runner.reconciler.hard_exit_schedule_token(held), token)
        self.assertEqual(len(runner.ledger.query("SELECT * FROM execution_intents")), intents_before)
        self.assertFalse(client.orders.create_calls)

    async def test_failed_proof_yields_without_retirement(self):
        runner, client, held = await self.uncovered()
        with patch.object(runner.account, "fetch_positions", AsyncMock(side_effect=AdapterError("503"))), \
                patch.object(runner, "_retire_market", AsyncMock()) as retire:
            self.assertEqual(await runner._decide_for_market(held, self.now), (None, 1))
        retire.assert_not_awaited()
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_proof_flatten_stop_and_generation_change_never_plan_entry(self):
        for changed in ("flat", "stop", "generation"):
            with self.subTest(changed=changed):
                runner, client, held = await self.uncovered()
                original = runner.account.fetch_positions
                async def positions(**kwargs):
                    if changed == "flat":
                        client.portfolio.rows.clear()
                    elif changed == "stop":
                        runner._stop_requested = True
                    else:
                        runner.source_control.generation += 1
                    return await original(**kwargs)
                with patch.object(runner.account, "fetch_positions", positions), \
                        patch.object(runner.reconciler, "plan", side_effect=AssertionError("must yield")):
                    self.assertEqual(await runner._decide_for_market(held, self.now), (None, 1))
                self.assertFalse(client.orders.create_calls)

    async def test_existing_scoped_certificate_is_reused_without_read(self):
        runner, _, held = await self.uncovered()
        await runner._reconcile_position_state(held, tag="position-transition")
        with patch.object(runner, "_reconcile_position_state", AsyncMock()) as proof:
            self.assertTrue(await runner._prepare_market_decision(held))
        proof.assert_not_awaited()
