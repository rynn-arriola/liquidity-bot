"""Regression of canceled reducers parked behind flat work/discovery."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from test_venue_state import raw_order
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.rest_fallback import RestFallbackRunner
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class RestReducerContinuityTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self):
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            runner, client, _ = self.fixture.build_runner(clock=lambda: self.now, max_markets=32)
        runner.rest_polling_policy = RestPollingPolicy(enabled=True)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        held = runner.market_slug
        self.fixture.seed_working_order(runner, slug=held, side="offer", suffix="reducer")
        client.orders.rows["exchange-reducer"] = raw_order(
            id="exchange-reducer", marketSlug=held, side="SELL", quantity="10", cumQuantity="0",
            state="ORDER_STATE_NEW", createTime=self.now.isoformat(),
            price={"value": "0.49", "currency": "USD"})
        client.portfolio.rows[held] = {
            "netPositionDecimal": "10", "cost": {"value": "4.90", "currency": "USD"}}
        self.now += timedelta(seconds=1)
        await runner._rest_reconcile(self.now)
        runner._evaluate_rewards(self.now)
        return runner, client, held

    async def test_reprice_confirms_and_replaces_before_next_flat_market(self):
        runner, client, held = await self.build()
        runner._bind_markets((held, "flat"))
        original = runner._decide_for_market
        visited = []

        async def decide(slug, now):
            visited.append(slug)
            if slug != held:
                self.assertEqual(len(client.orders.create_calls), 1)
                return None, 0.0
            runner.state_for(held).book_observed_at = self.now
            result = await original(slug, self.now)
            self.now += timedelta(seconds=.2)
            return result

        with patch.object(runner, "_decide_for_market", decide), \
                patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()):
            await runner._decision_sweep((held, "flat"))
        self.assertEqual(visited, [held, held, "flat"])
        self.assertEqual(len(client.orders.cancel_calls), 1)
        self.assertEqual(Decimal(str(client.orders.create_calls[0]["quantity"])), Decimal(10))
        self.assertLess((self.now - fixtures.NOW).total_seconds(), 5)

    async def test_unconfirmed_cancel_never_replaces(self):
        runner, client, held = await self.build()
        with patch.object(client.orders, "cancel", AsyncMock(return_value={})), \
                patch.object(runner, "_retry_uncertain_cancels", AsyncMock()):
            await runner._decide_for_market(held, self.now)
        self.assertTrue(runner.ledger.unresolved_intents(held))
        self.assertFalse(client.orders.create_calls)

    async def test_immediate_replacement_shares_cancel_proof_but_still_checks_equity(self):
        runner, client, held = await self.build()
        entries = tuple(f"entry-{i}" for i in range(30))
        runner._bind_markets((held, *entries))
        for slug in entries:
            self.fixture.seed_working_order(runner, slug=slug, side="bid", suffix=slug)
            client.orders.rows[f"exchange-{slug}"] = raw_order(
                id=f"exchange-{slug}", marketSlug=slug, side="BUY", quantity="10", cumQuantity="0",
                state="ORDER_STATE_NEW", createTime=self.now.isoformat(),
                price={"value": "0.49", "currency": "USD"})
        self.now += timedelta(seconds=1)
        await runner._rest_reconcile(self.now)
        original = runner._decide_for_market
        prior_poll = runner._rest_poll_completed_at

        async def decide(slug, now):
            runner.state_for(held).book_observed_at = self.now
            return await original(slug, self.now)

        with patch.object(runner, "_decide_for_market", decide), \
                patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()) as refresh, \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()) as equity:
            await runner._decision_sweep((held,))
        # Cancel's complete exact-owner proof is also the replacement's proof;
        # no second account-wide collection, but equity is checked both times.
        self.assertEqual(refresh.await_count, 1)
        self.assertEqual(equity.await_count, 2)
        self.assertEqual(len(client.orders.cancel_calls), 1)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(runner._rest_poll_completed_at, prior_poll)

    async def test_shared_confirmation_is_one_use_and_never_renews_entry_gate(self):
        runner, _, held = await self.build()
        before = runner._rest_poll_completed_at
        await runner._decide_for_market(held, self.now)
        self.assertTrue(runner._position_priority_has_fresh_account_proof(held))
        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
        self.assertEqual(runner._rest_poll_completed_at, before)

    async def test_shared_confirmation_refuses_stale_changed_unknown_stop_and_wrong_owner(self):
        runner, _, held = await self.build()
        await runner._decide_for_market(held, self.now)
        cached = runner._position_confirmation_proofs[held]
        self.assertFalse(runner._position_priority_has_fresh_account_proof("unrelated"))
        for change in ("expired", "future", "gate", "write", "scope", "source", "stop"):
            with self.subTest(change=change):
                runner._position_confirmation_proofs[held] = cached
                if change == "expired":
                    with patch.object(runner, "_now", return_value=self.now + timedelta(seconds=1)):
                        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                elif change == "future":
                    with patch.object(runner, "_now", return_value=self.now - timedelta(seconds=1)):
                        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                elif change == "gate":
                    with patch.object(runner.gate, "session_ready", return_value=False):
                        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                elif change == "scope":
                    with patch.object(runner, "_active_market_slugs", (held, "other")):
                        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                elif change == "source":
                    with patch.object(runner, "account_state_mode", "websocket"):
                        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                elif change == "stop":
                    with patch.object(runner, "_stop_requested", "operator"):
                        self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                else:
                    runner.ledger.connection.execute("UPDATE execution_positions SET net_qty=net_qty")
                    self.assertFalse(runner._position_priority_has_fresh_account_proof(held))
                    # Re-capture only for independent subsequent invalidations.
                    cached = (runner._position_confirmation_version(), cached[1], cached[2])

    async def test_fill_during_cancel_replaces_only_remaining_quantity(self):
        runner, client, held = await self.build()
        original = client.orders.cancel

        async def partial(order_id, payload):
            result = await original(order_id, payload)
            client.orders.rows[order_id]["cumQuantity"] = "4"
            client.portfolio.rows[held]["netPositionDecimal"] = "6"
            client.portfolio.rows[held]["cost"]["value"] = "2.94"
            return result

        with patch.object(client.orders, "cancel", partial):
            await runner._decide_for_market(held, self.now)
        self.assertFalse(runner.ledger.unresolved_intents(held))
        self.assertEqual(runner.ledger.position(held), Decimal(6))
        self.now += timedelta(seconds=.2)
        await runner._decide_for_market(held, self.now)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(Decimal(str(client.orders.create_calls[0]["quantity"])), Decimal(6))

    async def test_flat_during_cancel_proof_does_not_place_extra_entry(self):
        runner, client, held = await self.build()
        original = client.orders.cancel

        async def flatten(order_id, payload):
            result = await original(order_id, payload)
            client.orders.rows[order_id]["cumQuantity"] = "10"
            client.portfolio.rows.clear()
            return result

        with patch.object(client.orders, "cancel", flatten), \
                patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()):
            await runner._decision_sweep((held,))
        self.assertEqual(runner.ledger.position(held), 0)
        self.assertFalse(runner.ledger.unresolved_intents(held))
        self.assertFalse(client.orders.create_calls)

    async def test_failed_cancel_proof_revokes_authority_and_never_replaces(self):
        runner, client, held = await self.build()
        with patch.object(runner.account, "fetch_positions", AsyncMock(side_effect=AdapterError("503"))):
            result = await runner._decide_for_market(held, self.now)
        self.assertEqual(result, (None, 1.0))
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertFalse(client.orders.create_calls)

    async def test_account_throttle_yields_slate_without_cancelling_reducers_or_retiring(self):
        runner, client, held = await self.build()
        runner._bind_markets((held, "flat"))
        for error in (AdapterError("rate limited", status_code=429),
                      AdapterError("service unavailable", status_code=503),
                      AdapterError("gateway timeout", status_code=504),
                      TimeoutError("network timeout"),
                      ApiAdmissionDeferred("wait", error_code="LOCAL_ACCOUNT_READ_COOLDOWN",
                                           retry_after_seconds=10)):
            with self.subTest(error=type(error).__name__):
                runner.gate.on_reconciliation_conflict()
                with patch.object(runner, "_refresh_execution_state_for_decision",
                                  AsyncMock(side_effect=error)) as proof, \
                        patch.object(runner, "_decide_for_market", AsyncMock(return_value=(None, 0))) as decide:
                    self.assertIsNone(await runner._decision_sweep((held, "flat")))
                self.assertEqual(proof.await_count, 1)
                decide.assert_not_awaited()
                self.assertFalse(client.orders.cancel_calls)
                self.assertFalse(client.orders.create_calls)
                self.assertFalse(runner._retired)
                self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.now += timedelta(seconds=11)
        await runner._rest_reconcile(self.now)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_exact_order_throttle_preserves_typed_error_after_workers_finish(self):
        from liquidity_bot.account_source_runner import AccountSourceRunner
        from liquidity_bot.execution_ledger import LedgerConflict
        runner, _, held = await self.build()
        error = ApiAdmissionDeferred("wait", error_code="LOCAL_ACCOUNT_READ_COOLDOWN")
        # Exercise the permanent runner's pure-reader override on the same
        # real executor/ledger, including precedence of ownership conflicts.
        with patch.object(runner.executor, "read_order_state", AsyncMock(side_effect=error)):
            with self.assertRaises(ApiAdmissionDeferred):
                await AccountSourceRunner._read_exact_order_states(runner, ["exchange-reducer"], self.now)
        with patch.object(runner.executor, "read_order_state", AsyncMock(
                side_effect=[error, LedgerConflict("foreign identity")])):
            with self.assertRaises(LedgerConflict):
                await AccountSourceRunner._read_exact_order_states(runner, ["one", "two"], self.now)
        outage = AdapterError("unavailable", status_code=503)
        with patch.object(runner.executor, "read_order_state", AsyncMock(side_effect=outage)):
            with self.assertRaises(AdapterError) as caught:
                await AccountSourceRunner._read_exact_order_states(runner, ["one", "two"], self.now)
            self.assertIs(caught.exception, outage)
        with patch.object(runner.executor, "read_order_state", AsyncMock(
                side_effect=[outage, LedgerConflict("foreign identity")])):
            with self.assertRaises(LedgerConflict):
                await AccountSourceRunner._read_exact_order_states(runner, ["one", "two"], self.now)

    async def test_stop_after_cancel_does_not_start_confirmation_or_replacement(self):
        runner, client, held = await self.build()
        original = client.orders.cancel

        async def stop(order_id, payload):
            response = await original(order_id, payload)
            runner.request_stop("operator")
            return response

        with patch.object(client.orders, "cancel", stop), \
                patch.object(runner, "_reconcile_position_state", AsyncMock()) as proof:
            await runner._decide_for_market(held, self.now)
        proof.assert_not_awaited()
        self.assertFalse(client.orders.create_calls)

    async def test_discovery_blocked_during_gap_and_before_inventory_deadline(self):
        runner, client, held = await self.build()
        self.assertFalse(runner._position_near_its_flatten_deadline(self.now))
        basis = runner.ledger.inventory_basis(held)
        self.now = basis.opened_at + timedelta(seconds=841)
        self.assertTrue(runner._position_near_its_flatten_deadline(self.now))
        self.now = basis.opened_at + timedelta(seconds=100)
        client.orders.rows["exchange-reducer"]["state"] = "ORDER_STATE_CANCELED"
        await runner._rest_reconcile(self.now)
        self.assertTrue(runner._position_near_its_flatten_deadline(self.now))

    async def test_inventory_deadline_crossing_changes_priority_without_order_change(self):
        runner, _, held = await self.build()
        deadline = runner._position_exit_deadline(held)
        self.now = deadline - timedelta(microseconds=1)
        before = runner._position_priority_tokens()[held]
        self.now = deadline
        after = runner._position_priority_tokens()[held]
        self.assertNotEqual(before, after)
        self.assertFalse(before[-2])
        self.assertTrue(after[-2])
        self.assertEqual(before[:-2], after[:-2])

    async def test_visit_budget_yields_instead_of_parking_uncovered_position(self):
        runner, _, held = await self.build()
        runner._bind_markets(("flat", held))
        visited = []

        async def decide(slug, now):
            visited.append(slug)
            if slug == held:
                # Positive terminal proof leaves an exact held owner uncovered.
                runner.ledger.connection.execute(
                    "UPDATE execution_orders SET status='CANCELED' WHERE market_slug=?", (held,))
            return None, 0.0

        with patch.object(runner, "_position_priority_visit_limit", return_value=1), \
                patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", decide):
            await runner._decision_sweep(("flat", held))
        self.assertEqual(visited, [held])
        self.assertIn(held, runner._dirty_market_slugs)

    async def test_tiny_and_kickoff_clocks_use_existing_policy(self):
        runner, _, held = await self.build()
        opened = self.now
        fake_basis = SimpleNamespace(opened_at=opened)
        runner.state_for(held).market = replace(runner.state_for(held).market,
                                                minimum_trade_qty=.01)
        with patch.object(runner.ledger, "inventory_basis", return_value=fake_basis), \
                patch.object(runner.ledger, "position", return_value=Decimal(".05")):
            self.assertEqual(runner._position_exit_deadline(held), opened + timedelta(seconds=300))
        runner.state_for(held).market = replace(runner.state_for(held).market,
                                                game_start=opened + timedelta(seconds=400))
        with patch.object(runner.ledger, "inventory_basis", return_value=fake_basis):
            self.assertEqual(runner._position_exit_deadline(held), opened + timedelta(seconds=100))

    async def test_successful_position_service_does_not_wait_for_reward_dwell(self):
        runner, _, held = await self.build()
        with patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", AsyncMock(return_value=(None, 0))), \
                patch.object(runner.reconciler, "next_flat_decision_deadline",
                             return_value=self.now + timedelta(seconds=100)):
            await runner._decision_sweep((held,))
        self.assertNotIn(held, runner._next_decision_at)

    async def test_flat_strategy_deadline_is_scheduled_without_stream_traffic(self):
        runner, _, _ = await self.build()
        runner._bind_markets((runner.market_slug, "flat"))
        deadline = self.now + timedelta(seconds=10)
        with patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", AsyncMock(return_value=(None, 0))), \
                patch.object(runner, "_position_priority_tokens", return_value={}), \
                patch.object(runner.reconciler, "next_flat_decision_deadline",
                             return_value=deadline):
            await runner._decision_sweep(("flat",))

        self.assertEqual(runner._next_decision_at["flat"], deadline)

    async def test_exhausted_owner_does_not_starve_other_positions(self):
        runner, _, held = await self.build()
        runner._bind_markets(("flat", held, "other"))
        runner.ledger.apply_position_event("test", "other",
            PositionState("other", Decimal(1), self.now), received_at=self.now)
        visited = []

        async def decide(slug, now):
            visited.append(slug)
            runner.ledger.connection.execute(
                "UPDATE execution_orders SET status='CANCELED' WHERE market_slug=?", (held,))
            return None, 0.0

        with patch.object(runner, "_position_priority_visit_limit", return_value=1), \
                patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", decide):
            await runner._decision_sweep(("flat", held, "other"))
        # The uncovered owner now precedes the covered owner's optional
        # reprice; both still receive one visit despite the exhausted budget.
        self.assertEqual(visited, ["other", held])

    async def test_real_cancel_and_three_iocs_complete_before_flat_work(self):
        runner, client, held = await self.build()
        unrelated = tuple(f"entry-{i}" for i in range(30))
        runner._bind_markets((held, *unrelated, "flat"))
        for slug in unrelated:
            self.fixture.seed_working_order(runner, slug=slug, side="bid", suffix=slug)
            client.orders.rows[f"exchange-{slug}"] = raw_order(
                id=f"exchange-{slug}", marketSlug=slug, side="BUY", quantity="10", cumQuantity="0",
                state="ORDER_STATE_NEW", createTime=self.now.isoformat(),
                price={"value": "0.49", "currency": "USD"})
        self.now = runner._position_exit_deadline(held)
        await runner._rest_reconcile(self.now)
        runner.state_for(held).book_observed_at = self.now
        start = self.now
        original_decide = runner._decide_for_market
        original_proof = runner._reconcile_position_state
        original_state_refresh = runner._refresh_execution_state_for_decision
        original_equity_refresh = runner._refresh_equity_for_decision
        visited = []
        proof_tags = []
        redundant_refreshes = []

        class GovernedClock:
            async def wait(inner):
                self.now += timedelta(seconds=.25)

        runner.gateway.limiter = GovernedClock()

        async def create(payload):
            client.orders.create_calls.append(payload)
            index = len(client.orders.create_calls)
            order_id = f"ioc-{index}"
            client.orders.rows[order_id] = raw_order(
                id=order_id, marketSlug=held, side="SELL", quantity="10",
                cumQuantity="10" if index == 3 else "0",
                state="ORDER_STATE_FILLED" if index == 3 else "ORDER_STATE_EXPIRED",
                price=payload["price"], createTime=self.now.isoformat())
            if index == 3:
                client.portfolio.rows.clear()
            self.now += timedelta(seconds=.25)
            return {"id": order_id, "executions": []}

        async def decide(slug, now):
            visited.append(slug)
            if slug != held:
                self.assertEqual(runner.ledger.position(held), 0)
                return None, 0.0
            runner.state_for(held).book_observed_at = self.now
            result = await original_decide(slug, self.now)
            self.now += timedelta(seconds=.25)
            return result

        async def proof(slug, *, tag):
            proof_tags.append(tag)
            result = await original_proof(slug, tag=tag)
            if (tag == "position-transition"
                    and runner.reconciler.hard_exit_schedule_token(held)[1]
                    is not None):
                # Production-width scoped proof can outlive the prior global
                # entry proof. Exact owner reduction must continue without
                # granting flat entry authority.
                runner.gate.on_reconciliation_conflict()
            return result

        async def state_refresh(now):
            if runner._hard_exit_terminal_proofs.get(held) is not None:
                redundant_refreshes.append("account")
                self.now += timedelta(seconds=31)
            return await original_state_refresh(now)

        async def equity_refresh(now):
            if runner._hard_exit_terminal_proofs.get(held) is not None:
                redundant_refreshes.append("equity")
                self.now += timedelta(seconds=31)
            return await original_equity_refresh(now)

        with patch.object(client.orders, "create", create), \
                patch.object(runner, "_decide_for_market", decide), \
                patch.object(runner, "_reconcile_position_state", proof), \
                patch.object(runner, "_refresh_execution_state_for_decision",
                             state_refresh), \
                patch.object(runner, "_refresh_equity_for_decision",
                             equity_refresh):
            await runner._decision_sweep((held, "flat"))
        self.assertEqual(len(client.orders.cancel_calls), 1)
        self.assertEqual(len(client.orders.create_calls), 3)
        self.assertEqual(runner.ledger.position(held), 0)
        self.assertFalse(runner.ledger.unresolved_intents(held))
        self.assertLess((self.now - start).total_seconds(), 30)
        self.assertEqual(redundant_refreshes, [])
        self.assertTrue(all(p["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
                            for p in client.orders.create_calls))
        self.assertEqual(proof_tags, ["position-transition"] * 4)

    async def test_recent_terminal_transition_proof_allows_exact_partial_retry(self):
        runner, _, held = await self.build()
        runner.ledger.connection.execute(
            "UPDATE execution_orders SET status='CANCELED' WHERE market_slug=?", (held,))
        self.now = runner._position_exit_deadline(held)
        # The scoped post-IOC proof never renews global entry authority. A
        # positive terminal result can finish after the ten-second confirmation
        # preference and still be fresh at completion; the unchanged 30-second
        # retry window remains the authority for another IOC.
        runner.gate.on_rest_reconciled(self.now)
        runner.state_for(held).book_observed_at = self.now
        sent_at = self.now - timedelta(seconds=13)
        runner.reconciler._hard_exit_attempts[held] = 1
        runner.reconciler._hard_exit_started_at[held] = sent_at
        runner.reconciler._hard_exit_sent[held] = sent_at
        token = runner.reconciler.hard_exit_schedule_token(held)
        runner._hard_exit_terminal_proofs[held] = (token, self.now)
        with patch.object(runner, "_reconcile_position_state", AsyncMock()) as proof, \
                patch.object(runner.ledger, "position", return_value=Decimal(6)):
            outcome = await runner._reconcile_pending_hard_exit(held)
            proof.assert_not_awaited()
            self.assertEqual(outcome, "retry")
            ready_token, ready_at = runner._hard_exit_terminal_proofs[held]
            self.assertEqual(
                ready_token, runner.reconciler.hard_exit_schedule_token(held))
            self.assertIsNone(ready_token[1])
            self.assertEqual(ready_at, self.now)
            retry = runner.reconciler.plan(runner._frame(self.now, held))
        self.assertEqual(retry[0].kind.value, "place_ioc", retry[0])
        self.assertEqual(retry[0].qty, Decimal(6))

    async def test_cached_terminal_proof_cannot_bypass_changed_stale_or_working_state(self):
        runner, _, held = await self.build()
        runner.ledger.connection.execute(
            "UPDATE execution_orders SET status='CANCELED' WHERE market_slug=?", (held,))
        runner.reconciler._hard_exit_attempts[held] = 1
        runner.reconciler._hard_exit_sent[held] = self.now
        runner.reconciler._hard_exit_started_at[held] = self.now
        token = runner.reconciler.hard_exit_schedule_token(held)
        with patch.object(runner, "_reconcile_position_state", AsyncMock()) as proof:
            runner._hard_exit_terminal_proofs[held] = (
                (token[0] + 1, token[1], token[2]), self.now)
            await runner._reconcile_hard_exit_state(held)
            runner._hard_exit_terminal_proofs[held] = (
                token, self.now - timedelta(seconds=11))
            await runner._reconcile_hard_exit_state(held)
            runner.reconciler._hard_exit_sent[held] = self.now - timedelta(seconds=11)
            old_token = runner.reconciler.hard_exit_schedule_token(held)
            runner._hard_exit_terminal_proofs[held] = (old_token, self.now)
            await runner._reconcile_hard_exit_state(held)
            self.assertEqual(proof.await_count, 2)
            runner.reconciler._hard_exit_sent[held] = self.now
            token = runner.reconciler.hard_exit_schedule_token(held)
            runner.ledger.connection.execute(
                "UPDATE execution_orders SET status='OPEN' WHERE market_slug=?", (held,))
            runner._hard_exit_terminal_proofs[held] = (token, self.now)
            await runner._reconcile_hard_exit_state(held)
            runner.ledger.connection.execute(
                "UPDATE execution_orders SET status='UNKNOWN_ABSENT' WHERE market_slug=?",
                (held,))
            runner._hard_exit_terminal_proofs[held] = (token, self.now)
            await runner._reconcile_hard_exit_state(held)
        self.assertEqual(proof.await_count, 4)
