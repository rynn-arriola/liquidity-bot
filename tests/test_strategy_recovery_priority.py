"""Scoped REST recovery: production proof/projector, fake venue and clock only."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import io
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.errors import (AdapterError, ApiAdmissionDeferred, MarketStreamUnavailable,
                                  RuntimeInvariantViolation)
from liquidity_bot.execution_ledger import OrderState, PositionState
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class StrategyRecoveryPriorityTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW
        self.events = []
        self.orders = {}
        self.positions = []

    async def build(self, *, rest=True, target=False, flats=30):
        kind = partial(AccountSourceRunner, account_state_mode="rest" if rest else "websocket",
                       rest_polling_policy=RestPollingPolicy(enabled=True))
        with patch.object(fixtures, "CommissioningRunner", kind):
            runner, client, _ = self.fixture.build_runner(clock=lambda: self.now, max_markets=32)
        await runner.preflight()
        self.addCleanup(runner.ledger.close)
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        slugs = ("market-one", "held", *(f"flat-{i}" for i in range(flats)))
        runner._bind_markets(slugs)
        for slug in slugs[1:]:
            self.fixture.seed_working_order(runner, slug=slug, side="bid", suffix=slug)
        if target:
            self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="target")
        for row in runner.ledger.working_orders():
            self.orders[row["exchange_order_id"]] = OrderState(
                client_order_id=row["client_order_id"], exchange_order_id=row["exchange_order_id"],
                market_slug=row["market_slug"], side=row["side"], price=Decimal(row["price"]),
                original_qty=Decimal(row["original_qty"]), filled_qty=Decimal(row["filled_qty"]),
                status=row["status"], event_at=self.now)
        self.positions = [PositionState("held", Decimal(10), self.now)]
        runner.ledger.apply_position_event("test", "held-position", self.positions[0], received_at=self.now)
        return runner, client

    @contextmanager
    def venue(self, runner, *, service=None, cancel=None):
        read_owned = runner._read_owned_order_states
        async def exact(ids, now):
            self.events.append(("exact", tuple(sorted(ids))))
            self.now += timedelta(seconds=len(ids) * .25)
            return await read_owned(ids, now)

        async def read_order(key, **kwargs):
            return self.orders[key]

        async def snapshots(now):
            self.events.append(("snapshots",))
            self.now += timedelta(seconds=.5)
            return ([row for row in self.orders.values() if row.status == "OPEN"],
                    [replace(row, event_at=self.now) for row in self.positions])

        async def history(now):
            self.events.append(("history",))
            self.now += timedelta(seconds=.5)
            return []

        async def default_service():
            self.events.append(("urgent",))
            return None

        async def default_cancel(order_id, payload):
            self.events.append(("cancel", order_id))
            self.orders[order_id] = replace(self.orders[order_id], status="CANCELED", event_at=self.now)

        with ExitStack() as stack:
            stack.enter_context(patch.object(runner.executor, "read_order_state", side_effect=read_order))
            for name, value in (("_read_owned_order_states", exact),
                                ("_collect_rest_snapshots", snapshots),
                                ("_fetch_execution_history", history),
                                ("_service_urgent_positions", service or default_service)):
                stack.enter_context(patch.object(runner, name, side_effect=value))
            stack.enter_context(patch.object(runner.gateway.client.orders, "cancel",
                                            side_effect=cancel or default_cancel))
            stack.enter_context(patch.object(runner, "_position_near_its_flatten_deadline", return_value=True))
            stack.enter_context(patch.object(runner, "_request_post_flat_top_up_if_needed"))
            yield

    async def test_clear_owner_reads_full_account_once_then_services_positions(self):
        runner, client = await self.build()
        output = io.StringIO()
        with self.venue(runner), redirect_stdout(output):
            await runner._recover_strategy_state("execution_state_unknown", "market-one")
        self.assertIn("called_by=_quarantine_strategy_orders market=market-one source=rest", output.getvalue())
        self.assertEqual([e[0] for e in self.events], ["exact", "snapshots", "history", "urgent"])
        self.assertEqual(len(self.events[0][1]), 31)
        self.assertFalse(client.orders.create_calls)
        self.assertIsNone(runner._strategy_recovery_owner)

    async def test_cancel_followup_reads_only_target_without_renewing_entry_gate(self):
        runner, client = await self.build(target=True)
        started = self.now
        with self.venue(runner):
            await runner._quarantine_strategy_orders("market-one")
        reads = [e[1] for e in self.events if e[0] == "exact"]
        self.assertEqual(len(reads[0]), 32)
        self.assertEqual(reads[1], ("exchange-target",))
        self.assertEqual([e[0] for e in self.events],
                         ["exact", "snapshots", "history", "urgent", "cancel", "exact", "snapshots", "history"])
        self.assertEqual(runner.gate.last_reconcile_at, started)
        self.assertLess(runner._rest_poll_completed_at, self.now)
        self.assertFalse(runner.ledger.working_orders("market-one"))
        self.assertFalse(runner.ledger.unresolved_intents())
        self.assertEqual(len(runner.ledger.working_orders()), 31)
        self.assertFalse(client.orders.create_calls)

    async def test_websocket_path_retains_original_two_scoped_proofs(self):
        runner, _ = await self.build(rest=False)
        with self.venue(runner):
            await runner._recover_strategy_state("execution_state_unknown", "market-one")
        self.assertEqual([e[0] for e in self.events],
                         ["exact", "snapshots", "history", "exact", "snapshots", "history"])
        self.assertTrue(all(not e[1] for e in self.events if e[0] == "exact"))

    async def test_initial_failure_prevents_service_or_cancellation(self):
        runner, client = await self.build(target=True)
        with self.venue(runner), patch.object(runner, "_fetch_execution_history",
                                             AsyncMock(side_effect=AdapterError("history unavailable"))):
            with self.assertRaises(AdapterError):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] in {"urgent", "cancel"} for e in self.events))
        self.assertFalse(runner.gate.session_evidence_ready(now=self.now))
        self.assertIsNone(runner._strategy_recovery_owner)
        self.assertFalse(client.orders.create_calls)

    async def test_ack_is_not_terminal_and_unconfirmed_cancel_is_not_retried(self):
        runner, client = await self.build(target=True)
        async def ack(order_id, payload):
            self.events.append(("cancel", order_id))
        with self.venue(runner, cancel=ack):
            with self.assertRaisesRegex(Exception, "terminal cancel"):
                await runner._quarantine_strategy_orders("market-one")
        self.assertEqual(sum(e[0] == "cancel" for e in self.events), 1)
        self.assertTrue(runner.ledger.unresolved_intents("market-one"))
        self.assertFalse(runner.gate.session_evidence_ready(now=self.now))
        self.assertFalse(client.orders.create_calls)

    async def test_fill_winning_cancel_race_keeps_exact_position(self):
        runner, client = await self.build(target=True)
        async def fill(order_id, payload):
            self.orders[order_id] = replace(self.orders[order_id], status="FILLED",
                                            filled_qty=Decimal(10), event_at=self.now)
            self.positions.append(PositionState("market-one", Decimal(10), self.now))
        with self.venue(runner, cancel=fill):
            await runner._quarantine_strategy_orders("market-one")
        self.assertEqual(runner.ledger.position("market-one"), Decimal(10))
        self.assertFalse(runner.ledger.unresolved_intents("market-one"))
        self.assertFalse(client.orders.create_calls)

    async def test_partial_fill_keeps_remaining_order_quarantined(self):
        runner, _ = await self.build(target=True)
        async def partial_fill(order_id, payload):
            self.orders[order_id] = replace(self.orders[order_id], status="OPEN",
                                            filled_qty=Decimal("3.25"), event_at=self.now)
            self.positions.append(PositionState("market-one", Decimal("3.25"), self.now))
        with self.venue(runner, cancel=partial_fill):
            with self.assertRaisesRegex(Exception, "terminal cancel"):
                await runner._quarantine_strategy_orders("market-one")
        self.assertEqual(runner.ledger.position("market-one"), Decimal("3.25"))
        self.assertTrue(runner.ledger.working_orders("market-one"))

    async def test_stop_during_priority_service_prevents_target_cancel(self):
        runner, _ = await self.build(target=True)
        async def stop():
            runner._stop_requested = "operator_stop"
        with self.venue(runner, service=stop):
            self.assertEqual(await runner._quarantine_strategy_orders("market-one"), "operator_stop")
        self.assertFalse(any(e[0] == "cancel" for e in self.events))
        self.assertIsNone(runner._strategy_recovery_owner)

    async def test_priority_terminal_outcome_reaches_strategy_caller(self):
        runner, _ = await self.build()
        with self.venue(runner, service=AsyncMock(return_value="critical:ownership")):
            self.assertEqual(await runner._handle_strategy_critical("execution_state_unknown", "market-one"),
                             "critical:ownership")

    async def test_nested_recovery_is_deferred_without_extra_proof(self):
        runner, _ = await self.build()
        async def nested():
            with self.assertRaises(ApiAdmissionDeferred):
                await runner._quarantine_strategy_orders("held")
            self.assertEqual(runner._strategy_recovery_owner, "market-one")
        with self.venue(runner, service=nested):
            await runner._quarantine_strategy_orders("market-one")
        self.assertEqual(sum(e[0] == "exact" for e in self.events), 1)

    async def test_expired_proof_after_priority_service_never_cancels(self):
        runner, _ = await self.build(target=True)
        async def expire():
            self.now += timedelta(seconds=31)
        with self.venue(runner, service=expire):
            with self.assertRaises(ApiAdmissionDeferred):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] == "cancel" for e in self.events))
        self.assertFalse(runner.gate.session_evidence_ready(now=self.now))

    async def test_scope_change_after_service_cannot_cancel(self):
        runner, _ = await self.build(target=True)
        async def rebind():
            runner._bind_markets(("market-one",))
        with self.venue(runner, service=rebind):
            with self.assertRaises(ApiAdmissionDeferred):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] == "cancel" for e in self.events))

    async def test_foreign_order_remains_account_wide_failure(self):
        runner, _ = await self.build()
        self.orders["foreign"] = replace(next(iter(self.orders.values())),
                                         exchange_order_id="foreign", client_order_id="venue:foreign")
        with self.venue(runner):
            with self.assertRaises(RuntimeInvariantViolation):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] == "urgent" for e in self.events))
        self.assertFalse(runner.gate.session_evidence_ready(now=self.now))

    async def test_recovering_position_excluded_only_until_quarantine_finishes(self):
        runner, _ = await self.build()
        position = PositionState("market-one", Decimal(4), self.now)
        self.positions.append(position)
        runner.ledger.apply_position_event("test", "target-position", position, received_at=self.now)
        async def service():
            self.assertNotIn("market-one", runner._position_priority_tokens())
            self.assertIn("held", runner._position_priority_tokens())
        with self.venue(runner, service=service):
            await runner._quarantine_strategy_orders("market-one")
        self.assertIn("market-one", runner._position_priority_tokens())

    async def test_generic_recovery_wrapper_stays_account_wide(self):
        runner, _ = await self.build()
        with patch.object(CommissioningRunner, "_rest_reconcile", AsyncMock()) as proof:
            await runner._rest_reconcile(self.now, strict_order_markets=frozenset())
        self.assertNotIn("strict_order_markets", proof.call_args.kwargs)

    async def test_stop_before_proof_avoids_all_reads(self):
        runner, _ = await self.build(target=True)
        runner._stop_requested = "operator_stop"
        with self.venue(runner):
            self.assertEqual(await runner._quarantine_strategy_orders("market-one"), "operator_stop")
        self.assertEqual(self.events, [])

    async def test_stop_after_cancel_leaves_intent_for_shutdown(self):
        runner, _ = await self.build(target=True)
        async def stop_after_ack(order_id, payload):
            runner._stop_requested = "operator_stop"
        with self.venue(runner, cancel=stop_after_ack):
            self.assertEqual(await runner._quarantine_strategy_orders("market-one"), "operator_stop")
        self.assertEqual(sum(e[0] == "exact" for e in self.events), 1)
        self.assertTrue(runner.ledger.unresolved_intents("market-one"))

    async def test_source_generation_change_prevents_cancel(self):
        runner, _ = await self.build(target=True)
        async def change():
            runner.source_control.generation += 1
        with self.venue(runner, service=change):
            with self.assertRaises(ApiAdmissionDeferred):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] == "cancel" for e in self.events))

    async def test_public_failure_yields_shared_recovery(self):
        runner, _ = await self.build(target=True)
        async def disconnect():
            runner._streams_need_rebind = True
        with self.venue(runner, service=disconnect):
            with self.assertRaises(MarketStreamUnavailable):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] == "cancel" for e in self.events))
        self.assertIsNone(runner._strategy_recovery_owner)

    async def test_target_can_clear_during_priority_service_without_cancel(self):
        runner, _ = await self.build(target=True)
        async def terminal():
            order = replace(self.orders["exchange-target"], status="CANCELED", event_at=self.now)
            runner.executor.apply_order_state("exchange-target", order, observed_at=self.now)
        with self.venue(runner, service=terminal):
            await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(any(e[0] == "cancel" for e in self.events))
        self.assertEqual(sum(e[0] == "exact" for e in self.events), 1)

    async def test_terminal_history_failure_revokes_authority(self):
        runner, _ = await self.build(target=True)
        async def break_history(order_id, payload):
            self.orders[order_id] = replace(self.orders[order_id], status="CANCELED", event_at=self.now)
            runner._fetch_execution_history.side_effect = AdapterError("history unavailable")
        with self.venue(runner, cancel=break_history):
            with self.assertRaises(AdapterError):
                await runner._quarantine_strategy_orders("market-one")
        self.assertFalse(runner.gate.session_evidence_ready(now=self.now))
        self.assertIsNone(runner._strategy_recovery_owner)

    async def test_no_id_unknown_create_is_not_retried(self):
        runner, client = await self.build()
        runner.ledger.prepare_intent("unknown", "PLACE", "market-one", "client-unknown", {},
                                     created_at=self.now)
        runner.ledger.transition_intent("unknown", "SUBMITTING", updated_at=self.now)
        runner.ledger.transition_intent("unknown", "UNCERTAIN", updated_at=self.now)
        with self.venue(runner):
            with self.assertRaises(Exception):
                await runner._quarantine_strategy_orders("market-one")
        self.assertTrue(runner.ledger.unresolved_intents("market-one"))
        self.assertFalse(client.orders.create_calls)
        self.assertFalse(any(e[0] == "cancel" for e in self.events))

    async def test_absent_order_does_not_satisfy_terminal_postcondition(self):
        runner, _ = await self.build(target=True)
        async def absent(order_id, payload):
            # Deliberately model only an empty list, NOT terminal venue proof.
            self.orders.pop(order_id)
        with self.venue(runner, cancel=absent):
            with self.assertRaises(Exception):
                await runner._quarantine_strategy_orders("market-one")
        self.assertTrue(runner.ledger.unresolved_intents("market-one"))
        self.assertTrue(runner.ledger.absent_orders("market-one"))
        self.assertFalse(runner.gate.session_evidence_ready(now=self.now))

    async def test_real_priority_sweep_services_other_owner_without_flat_entry(self):
        runner, client = await self.build(target=True, flats=1)
        real_service = runner._service_urgent_positions
        visited = []
        async def decide(slug, now, **kwargs):
            visited.append(slug)
            return None, 0.0
        with self.venue(runner, service=real_service), \
                patch.object(runner, "_service_bound_markets", AsyncMock(return_value=None)), \
                patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_book", AsyncMock()), \
                patch.object(runner, "_refresh_event_guard", AsyncMock()), \
                patch.object(runner, "_decide_for_market", side_effect=decide):
            await runner._quarantine_strategy_orders("market-one")
        self.assertEqual(visited, ["held"])
        self.assertFalse(client.orders.create_calls)

    async def test_priority_backoff_is_preserved(self):
        runner, _ = await self.build()
        real_service = runner._service_urgent_positions
        runner._next_decision_at["held"] = self.now + timedelta(seconds=30)
        with self.venue(runner, service=real_service), \
                patch.object(runner, "_decision_sweep", AsyncMock()) as sweep:
            await runner._quarantine_strategy_orders("market-one")
        sweep.assert_not_awaited()
