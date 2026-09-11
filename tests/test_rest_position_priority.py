"""REST position scheduling; fake time/venue, no live mutations."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from liquidity_bot.execution_ledger import PositionState
from liquidity_bot.rest_fallback import RestFallbackRunner


class RestPositionPriorityTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW
        self.sequence = 0

    async def build(self, *, rest=True, count=32):
        if rest:
            with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
                runner, client, _ = self.fixture.build_runner(
                    clock=lambda: self.now, max_markets=32)
        else:
            runner, client, _ = self.fixture.build_runner(clock=lambda: self.now)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        slugs = (*(f"market-{i}" for i in range(count - 1)), "market-one")
        runner._bind_markets(slugs)
        return runner, client, slugs

    def position(self, runner, slug, qty="8.41"):
        self.sequence += 1
        runner.ledger.apply_position_event(
            "rest", f"position-{self.sequence}",
            PositionState(slug, Decimal(qty), self.now), received_at=self.now)

    async def sweep(self, runner, slugs, *, refresh=None, decide=None):
        visited = []

        async def record(slug, now):
            visited.append((slug, now))
            self.now += timedelta(seconds=4)
            if decide:
                return await decide(slug, now)
            return None, 0.0

        with patch.object(runner, "_refresh_execution_state_for_decision",
                          refresh or AsyncMock()), patch.object(
                              runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", record):
            outcome = await runner._decision_sweep(slugs)
        return visited, outcome

    async def test_existing_position_precedes_31_ranked_flat_markets(self):
        runner, _, slugs = await self.build()
        self.position(runner, slugs[-1])
        visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], [slugs[-1], *slugs[:-1]])

    async def test_fill_found_during_flat_poll_is_next_decision(self):
        runner, _, slugs = await self.build()
        discovered = None

        async def poll(now):
            nonlocal discovered
            if discovered is None:
                self.now += timedelta(seconds=12)
                self.position(runner, slugs[-1])
                discovered = self.now

        visited, _ = await self.sweep(runner, slugs, refresh=poll)
        self.assertEqual(visited[0], (slugs[-1], discovered))
        self.assertEqual(len(visited), 32)

    async def test_already_visited_owner_reenters_when_poll_finds_fill(self):
        runner, _, slugs = await self.build()
        calls = 0

        async def poll(now):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.position(runner, slugs[0])

        visited, _ = await self.sweep(runner, slugs, refresh=poll)
        self.assertEqual([s for s, _ in visited][:3], [slugs[0], slugs[0], slugs[1]])
        self.assertEqual(len(visited), 33)

    async def test_terminal_cancel_proof_wakes_position_again_in_same_sweep(self):
        runner, _, slugs = await self.build()
        held = slugs[-1]
        self.position(runner, held)
        self.fixture.seed_working_order(runner, slug=held, side="bid", suffix="building")
        pending = False

        async def decide(slug, now):
            nonlocal pending
            if slug == held and not pending:
                pending = True
            return None, 0.0

        async def poll(now):
            if pending:
                # Test fixture models POSITIVE terminal venue proof, never an ACK.
                runner.ledger.connection.execute(
                    "UPDATE execution_orders SET status='CANCELED' "
                    "WHERE exchange_order_id='exchange-building'")

        visited, _ = await self.sweep(runner, slugs, refresh=poll, decide=decide)
        self.assertEqual([s for s, _ in visited][:3], [held, held, slugs[0]])

    async def test_nonselected_position_is_serviced_without_changing_selection(self):
        runner, _, slugs = await self.build(count=2)
        self.position(runner, "outside")
        visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], ["outside", *slugs])
        self.assertEqual(runner.active_slugs, slugs)
        self.assertTrue(runner._is_barred_from_quoting("outside"))

    async def test_unresolved_position_intent_still_blocks_its_decision(self):
        runner, _, slugs = await self.build(count=2)
        self.position(runner, slugs[-1])
        runner.ledger.prepare_intent("unknown", "PLACE", slugs[-1], "client", {},
                                     created_at=self.now)
        visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], [slugs[0]])
        self.assertEqual(len(runner.ledger.unresolved_intents()), 1)

    async def test_position_backoff_is_not_bypassed(self):
        runner, _, slugs = await self.build(count=2)
        self.position(runner, slugs[-1])
        deadline = self.now + timedelta(seconds=30)
        runner._next_decision_at[slugs[-1]] = deadline
        visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], [slugs[0]])
        self.assertEqual(runner._next_decision_at[slugs[-1]], deadline)

    async def test_stop_arriving_during_poll_prevents_any_priority_mutation(self):
        runner, client, slugs = await self.build(count=2)

        async def poll(now):
            self.position(runner, slugs[-1])
            runner.request_stop("operator_stop")

        visited, outcome = await self.sweep(runner, slugs, refresh=poll)
        self.assertEqual(visited, [])
        self.assertEqual(outcome, "operator_stop")
        self.assertEqual(client.orders.create_calls, [])

    async def test_failed_account_refresh_not_retried_for_each_priority_owner(self):
        runner, _, slugs = await self.build(count=3)
        self.position(runner, slugs[-1])
        proof = AsyncMock(side_effect=RuntimeError("unavailable"))
        visited, _ = await self.sweep(runner, slugs, refresh=proof)
        self.assertEqual(proof.await_count, 1)
        self.assertEqual(len(visited), 3)

    async def test_unchanged_positions_do_not_starve_flat_reward_order(self):
        runner, _, slugs = await self.build()
        for slug in slugs[-3:]:
            self.position(runner, slug)
        visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], [*slugs[-3:], *slugs[:-3]])

    async def test_continuously_changing_position_has_bounded_extra_visits(self):
        runner, _, slugs = await self.build(count=3)
        self.position(runner, "outside")

        async def poll(now):
            self.now += timedelta(seconds=1)
            self.position(runner, "outside", str(self.sequence + 1))

        visited, _ = await self.sweep(runner, slugs, refresh=poll)
        self.assertEqual([s for s, _ in visited].count("outside"), 4)
        self.assertEqual([s for s, _ in visited if s != "outside"], list(slugs))

    async def test_default_websocket_and_empty_sweeps_unchanged(self):
        runner, _, slugs = await self.build(rest=False, count=3)
        self.position(runner, slugs[-1])
        visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], list(slugs))
        self.assertEqual(await self.sweep(runner, ()), ([], None))

    async def test_priority_that_flattens_during_proof_cannot_add_an_entry_pass(self):
        runner, _, slugs = await self.build(count=2)
        self.position(runner, slugs[-1])
        calls = 0

        async def poll(now):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.now += timedelta(seconds=1)
                self.position(runner, slugs[-1], "0")

        visited, _ = await self.sweep(runner, slugs, refresh=poll)
        self.assertEqual([s for s, _ in visited], [slugs[0]])
        self.assertIn(slugs[-1], runner._dirty_market_slugs)

    async def test_sign_and_partial_quantity_changes_wake_already_serviced_owner(self):
        runner, _, slugs = await self.build(count=3)
        held = slugs[-1]
        self.position(runner, held)
        quantities = []

        async def decide(slug, now):
            if slug == held:
                quantities.append(runner.ledger.position(held))
                if len(quantities) == 1:
                    self.position(runner, held, "2.13")
                elif len(quantities) == 2:
                    self.position(runner, held, "-0.05")
            return None, 0.0

        await self.sweep(runner, slugs, decide=decide)
        self.assertEqual(quantities, [Decimal("8.41"), Decimal("2.13"), Decimal("-0.05")])

    async def test_terminal_intent_without_working_order_still_reawakens_owner(self):
        runner, _, slugs = await self.build(count=3)
        held = slugs[-1]
        self.position(runner, held)
        runner.ledger.prepare_intent("unknown", "PLACE", held, "client", {},
                                     created_at=self.now)

        async def decide(slug, now):
            if slug == slugs[0]:
                # Simulate the existing exact-resolution path, not a retry.
                runner.ledger.transition_intent("unknown", "FAILED", updated_at=self.now)
            return None, 0.0

        visited, _ = await self.sweep(runner, slugs, decide=decide)
        self.assertEqual([s for s, _ in visited], [slugs[0], held, slugs[1]])

    async def test_terminal_hard_exit_state_reenters_before_flat_owner(self):
        runner, _, slugs = await self.build(count=2)
        held = slugs[-1]
        self.position(runner, held)
        runner.reconciler._hard_exit_attempts[held] = 1
        runner.reconciler._hard_exit_started_at[held] = self.now
        runner.reconciler._hard_exit_sent[held] = self.now
        held_visits = 0

        async def decide(slug, now):
            nonlocal held_visits
            if slug == held:
                held_visits += 1
                if held_visits == 1:
                    # Positive terminal proof changes no position or working
                    # order row when an IOC opened and expired between polls.
                    runner.reconciler.confirm_hard_exit_terminal(held)
            return None, 0.0

        visited, _ = await self.sweep(runner, slugs, decide=decide)

        self.assertEqual([s for s, _ in visited], [held, held, slugs[0]])

    async def test_hard_exit_priority_budget_covers_three_send_proof_pairs(self):
        runner, _, slugs = await self.build(count=2)
        held = slugs[-1]
        self.position(runner, held)
        runner.reconciler._hard_exit_attempts[held] = 1
        runner.reconciler._hard_exit_started_at[held] = self.now
        runner.reconciler._hard_exit_sent[held] = self.now
        held_visits = 0

        async def decide(slug, now):
            nonlocal held_visits
            if slug == held:
                held_visits += 1
                if held_visits < 6:
                    if held_visits % 2:
                        runner.reconciler.confirm_hard_exit_terminal(held)
                    else:
                        runner.reconciler._hard_exit_attempts[held] += 1
                        runner.reconciler._hard_exit_sent[held] = self.now
            return None, 0.0

        visited, _ = await self.sweep(runner, slugs, decide=decide)

        self.assertEqual([s for s, _ in visited].count(held), 6)
        self.assertIn(slugs[0], [s for s, _ in visited])

    async def test_hard_exit_confirmation_uses_scoped_non_authorizing_proof(self):
        runner, _, _ = await self.build(count=2)
        slug = runner.market_slug
        completed_before = runner._rest_poll_completed_at

        with patch.object(
                fixtures.CommissioningRunner, "_rest_reconcile",
                new_callable=AsyncMock) as reconcile:
            await runner._reconcile_hard_exit_state(slug)

        reconcile.assert_awaited_once()
        self.assertEqual(
            reconcile.await_args.kwargs["strict_order_markets"],
            frozenset({slug}),
        )
        self.assertFalse(reconcile.await_args.kwargs["advance_gate"])
        self.assertEqual(runner._rest_poll_completed_at, completed_before)

    async def test_pending_hard_exit_routes_through_focused_proof(self):
        runner, _, _ = await self.build(count=2)
        slug = runner.market_slug
        self.position(runner, slug, "10")
        runner.reconciler._hard_exit_sent[slug] = self.now

        with patch.object(
                runner, "_reconcile_hard_exit_state",
                new_callable=AsyncMock) as proof:
            outcome = await runner._reconcile_pending_hard_exit(slug)

        proof.assert_awaited_once_with(slug)
        self.assertEqual(outcome, "retry")
        self.assertNotIn(slug, runner.reconciler._hard_exit_sent)

    async def test_rest_terminal_hard_exit_retry_has_no_extra_backoff(self):
        runner, _, _ = await self.build(count=2)
        slug = runner.market_slug
        runner.reconciler._hard_exit_attempts[slug] = 1

        self.assertEqual(runner._hard_exit_retry_delay(slug), 0.0)

    async def test_real_position_path_cancels_confirms_then_places_exact_exit_before_entries(self):
        runner, client, slugs = await self.build()
        held = "market-one"
        runner._bind_markets((*slugs[:-1], held))
        runner.state_for(held).market = replace(
            runner.state_for(held).market, minimum_trade_qty=0.01)
        for side in ("bid", "offer"):
            self.fixture.seed_working_order(runner, slug=held, side=side, suffix=side)
            client.orders.rows[f"exchange-{side}"] = {
                "id": f"exchange-{side}", "marketSlug": held,
                "side": "BUY" if side == "bid" else "SELL",
                "intent": "ORDER_INTENT_BUY_LONG" if side == "bid" else "ORDER_INTENT_SELL_LONG",
                "price": {"value": "0.49", "currency": "USD"},
                "quantity": "10", "cumQuantity": "8.41" if side == "bid" else "0",
                "state": "ORDER_STATE_PARTIALLY_FILLED" if side == "bid" else "ORDER_STATE_NEW",
                "createTime": self.now.isoformat(),
            }
        execution = {
            "id": "priority-fill", "tradeId": "priority-trade", "aggressor": False,
            "type": "EXECUTION_TYPE_FILL", "lastShares": "8.41",
            "lastPx": {"value": "0.49", "currency": "USD"},
            "commissionNotionalCollected": {"value": "0", "currency": "USD"},
            "transactTime": self.now.isoformat(), "order": client.orders.rows["exchange-bid"],
        }
        client.portfolio.trades = [{"id": "priority-trade", "isAggressor": True,
                                    "aggressorExecution": execution}]
        client.portfolio.rows[held] = {
            "netPositionDecimal": "8.41", "cost": {"value": "4.1209", "currency": "USD"}}
        original_decide = runner._decide_for_market
        visited = []
        pending_after_cancel = []

        async def poll(now):
            self.now += timedelta(seconds=1)
            if not client.orders.create_calls:
                await runner._rest_reconcile(self.now)

        async def decide(slug, now):
            visited.append(slug)
            if slug != held:
                return None, 0.0
            runner.state_for(held).book_observed_at = self.now
            result = await original_decide(slug, self.now)
            if len(visited) == 1:
                pending_after_cancel.extend(runner.ledger.unresolved_intents())
                self.assertEqual(client.orders.create_calls, [])
            return result

        runner._evaluate_rewards(self.now)
        with patch.object(runner, "_refresh_execution_state_for_decision", poll), \
                patch.object(runner, "_decide_for_market", decide):
            await runner._decision_sweep(runner.active_slugs)
        self.assertEqual(visited[:3], [held, held, held])
        self.assertTrue(pending_after_cancel)  # ACK alone did not allow a reducer.
        self.assertEqual(len(client.orders.cancel_calls), 2)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(Decimal(str(client.orders.create_calls[0]["quantity"])), Decimal("8.41"))
        # Preserve the existing Retail price-frame mapping for an offer.
        self.assertEqual(client.orders.create_calls[0]["intent"], "ORDER_INTENT_BUY_SHORT")
