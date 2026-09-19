"""Exact coverage and urgency between held owners; fake venue/time only."""
from datetime import timedelta
from dataclasses import replace
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
import test_rest_position_priority as priority_fixtures
from test_venue_state import raw_order
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class PositionUrgencyTests(IsolatedAsyncioTestCase):
    setUp = priority_fixtures.RestPositionPriorityTests.setUp
    build = priority_fixtures.RestPositionPriorityTests.build
    position = priority_fixtures.RestPositionPriorityTests.position
    sweep = priority_fixtures.RestPositionPriorityTests.sweep

    def order(self, runner, slug, side, suffix):
        self.fixture.seed_working_order(runner, slug=slug, side=side, suffix=suffix)

    async def test_builder_wrong_size_duplicate_and_ambiguity_are_urgent(self):
        runner, _, slugs = await self.build(count=2)
        held = slugs[-1]
        self.position(runner, held, "10")
        future = self.now + timedelta(minutes=10)
        with patch.object(runner, "_position_exit_deadline", return_value=future):
            self.assertTrue(runner._position_priority_must_yield(held))
            self.order(runner, held, "bid", "builder")
            self.assertTrue(runner._position_priority_must_yield(held))
            self.assertTrue(runner._position_near_its_flatten_deadline(self.now))
            self.order(runner, held, "offer", "exit")
            self.assertTrue(runner._position_priority_must_yield(held))
            runner.ledger.connection.execute(
                "UPDATE execution_orders SET status='CANCELED' WHERE exchange_order_id='exchange-builder'")
            self.assertFalse(runner._position_priority_must_yield(held))
            self.assertFalse(runner._position_near_its_flatten_deadline(self.now))
            self.position(runner, held, "6.85")
            self.assertTrue(runner._position_priority_must_yield(held))
            runner.ledger.connection.execute(
                "UPDATE execution_orders SET filled_qty='3.15' WHERE exchange_order_id='exchange-exit'")
            self.assertFalse(runner._position_priority_must_yield(held))
            runner.ledger.prepare_intent("uncertain", "CANCEL", held, "client-exit", {}, created_at=self.now)
            self.assertTrue(runner._position_priority_must_yield(held))
            self.assertTrue(runner._position_near_its_flatten_deadline(self.now))
            runner.ledger.transition_intent("uncertain", "FAILED", updated_at=self.now)
            self.assertFalse(runner._position_priority_must_yield(held))
            self.order(runner, held, "offer", "duplicate")
            self.assertTrue(runner._position_priority_must_yield(held))

    async def test_short_exact_quantity_and_absent_order_are_not_confused(self):
        runner, _, slugs = await self.build(count=2)
        held = slugs[-1]
        self.position(runner, held, "-10")
        self.order(runner, held, "bid", "exit")
        with patch.object(runner, "_position_exit_deadline", return_value=self.now + timedelta(minutes=10)):
            self.assertFalse(runner._position_priority_must_yield(held))
            self.assertTrue(runner._preserve_position_orders_during_bound_recovery(held))
            self.order(runner, held, "offer", "absent")
            runner.ledger.connection.execute(
                "UPDATE execution_orders SET status='UNKNOWN_ABSENT' WHERE exchange_order_id='exchange-absent'")
            self.assertTrue(runner._position_priority_must_yield(held))
            self.assertFalse(runner._preserve_position_orders_during_bound_recovery(held))

    async def test_due_and_unknown_deadline_retain_priority_with_exact_reducer(self):
        runner, _, slugs = await self.build(count=2)
        held = slugs[-1]
        self.position(runner, held, "10")
        self.order(runner, held, "offer", "exit")
        for deadline, expected in ((self.now, 0), (None, 1),
                                    (self.now + timedelta(seconds=30), 2)):
            with self.subTest(deadline=deadline), patch.object(
                    runner, "_position_exit_deadline", return_value=deadline):
                self.assertEqual(runner._position_priority_rank(held), expected)
                self.assertTrue(runner._position_near_its_flatten_deadline(self.now))
        self.position(runner, held, "0")
        self.assertFalse(runner._position_priority_must_yield(held))

    async def test_new_fill_preempts_existing_covered_owner(self):
        runner, _, slugs = await self.build(count=2)
        covered, filled = slugs
        self.position(runner, covered, "10")
        self.order(runner, covered, "offer", "covered")
        discovered = False

        async def refresh(now):
            nonlocal discovered
            if not discovered:
                self.position(runner, filled, "10")
                self.order(runner, filled, "bid", "building")
                discovered = True

        with patch.object(runner, "_position_exit_deadline", return_value=self.now + timedelta(minutes=10)), \
                patch.object(runner, "_service_market_book", AsyncMock()):
            visited, _ = await self.sweep(runner, slugs, refresh=refresh)
        self.assertEqual([s for s, _ in visited], [filled, covered])

    async def test_due_owner_wins_before_uncovered_and_covered_then_flat(self):
        runner, _, slugs = await self.build(count=4)
        flat, covered, uncovered, due = slugs
        for held in (covered, uncovered, due):
            self.position(runner, held, "10")
        self.order(runner, covered, "offer", "covered")
        self.order(runner, due, "offer", "due")
        future = self.now + timedelta(minutes=10)
        with patch.object(runner, "_position_exit_deadline",
                          side_effect=lambda s: self.now if s == due else future), \
                patch.object(runner, "_service_market_book", AsyncMock()):
            visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], [due, uncovered, covered, flat])

    async def test_urgent_outer_pass_includes_builder_and_sorts_due_first(self):
        runner, _, slugs = await self.build(count=2)
        building, due = slugs
        self.position(runner, building, "10")
        self.order(runner, building, "bid", "builder")
        self.position(runner, due, "10")
        self.order(runner, due, "offer", "exit")
        future = self.now + timedelta(minutes=10)
        with patch.object(runner, "_position_exit_deadline",
                          side_effect=lambda s: self.now if s == due else future), \
                patch.object(runner, "_service_bound_markets", AsyncMock(return_value=None)) as service, \
                patch.object(runner, "_decision_sweep", AsyncMock(return_value=None)) as sweep:
            await runner._service_urgent_positions()
        service.assert_awaited_once_with((due, building), refresh_books=False)
        sweep.assert_awaited_once_with((due, building), position_only=True)

    async def test_backoff_unknown_and_isolated_owners_do_not_preempt_covered(self):
        for blocked in ("backoff", "unknown", "isolated"):
            with self.subTest(blocked=blocked):
                runner, _, slugs = await self.build(count=2)
                covered, urgent = slugs
                self.position(runner, covered, "10")
                self.order(runner, covered, "offer", "covered")
                self.position(runner, urgent, "10")
                if blocked == "backoff":
                    runner._next_decision_at[urgent] = self.now + timedelta(seconds=60)
                if blocked == "unknown":
                    runner.ledger.prepare_intent("unknown", "PLACE", urgent, "client", {}, created_at=self.now)
                isolated = frozenset({urgent}) if blocked == "isolated" else frozenset()
                with patch.object(runner, "_position_exit_deadline", return_value=self.now + timedelta(minutes=10)), \
                        patch.object(runner, "_service_market_book", AsyncMock()), \
                        patch.object(runner, "_isolated_unexecutable_slugs", return_value=isolated), \
                        patch.object(runner, "_can_wait_unexecutable", return_value=True):
                    visited, _ = await self.sweep(runner, slugs)
                self.assertEqual([s for s, _ in visited], [covered])

    async def test_preempted_priority_owner_cannot_turn_into_flat_entry(self):
        runner, _, slugs = await self.build(count=3)
        flat, covered, due = slugs
        self.position(runner, covered, "10")
        self.order(runner, covered, "offer", "covered")
        calls = 0

        async def refresh(now):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.position(runner, due, "10")

        async def decide(slug, now):
            if slug == due:
                self.position(runner, covered, "0")
            return None, 0.0

        future = self.now + timedelta(minutes=10)
        with patch.object(runner, "_position_exit_deadline", side_effect=lambda s: self.now if s == due else future), \
                patch.object(runner, "_service_market_book", AsyncMock()):
            visited, _ = await self.sweep(runner, slugs, refresh=refresh, decide=decide)
        self.assertEqual([s for s, _ in visited], [due, flat])

    async def test_websocket_scheduling_is_unchanged(self):
        runner, _, slugs = await self.build(rest=False, count=2)
        self.position(runner, slugs[-1], "10")
        with patch.object(runner, "_service_market_book", AsyncMock()):
            visited, _ = await self.sweep(runner, slugs)
        self.assertEqual([s for s, _ in visited], list(slugs))

    async def test_rest_class_in_websocket_mode_keeps_old_hooks(self):
        runner, _, slugs = await self.build(count=2)
        self.position(runner, slugs[-1], "10")
        with patch.object(runner, "_using_rest_source", return_value=False):
            self.assertEqual(runner._position_priority_tokens(), {})
            self.assertEqual(runner._position_priority_rank(slugs[-1]), 2)
            self.assertFalse(runner._position_priority_must_yield(slugs[-1]))

    async def test_real_builder_cancel_confirm_and_exact_reducer_precede_reprice(self):
        runner, client, slugs = await self.build(count=2)
        runner.rest_polling_policy = RestPollingPolicy(enabled=True)
        covered, filled = slugs
        source = runner.state_for(filled)
        runner.state_for(covered).market = replace(source.market, slug=covered)
        runner.state_for(covered).book = replace(source.book, slug=covered)
        runner.state_for(covered).book_observed_at = self.now
        self.order(runner, covered, "offer", "covered")
        self.order(runner, filled, "bid", "builder")
        for slug, suffix, side in ((covered, "covered", "SELL"), (filled, "builder", "BUY")):
            client.orders.rows[f"exchange-{suffix}"] = raw_order(
                id=f"exchange-{suffix}", marketSlug=slug, side=side, quantity="10", cumQuantity="0",
                state="ORDER_STATE_NEW", createTime=self.now.isoformat(),
                price={"value": "0.49", "currency": "USD"})
            client.portfolio.rows[slug] = {
                "netPositionDecimal": "10", "cost": {"value": "4.90", "currency": "USD"}}
        self.now += timedelta(seconds=1)
        await runner._rest_reconcile(self.now)
        runner._evaluate_rewards(self.now)
        original = runner._decide_for_market
        visited = []

        async def decide(slug, now):
            visited.append(slug)
            if slug == covered:
                self.assertEqual(len(client.orders.cancel_calls), 1)
                self.assertEqual(len(client.orders.create_calls), 1)
                self.assertFalse(runner.ledger.query(
                    "SELECT intent_id FROM execution_intents WHERE market_slug=? "
                    "AND operation='CANCEL' AND state!='RECONCILED'", (filled,)))
                self.assertEqual(runner.ledger.query(
                    "SELECT status FROM execution_orders WHERE exchange_order_id='exchange-builder'"
                )[0]["status"], "CANCELED")
                self.assertEqual(client.orders.create_calls[0]["marketSlug"], filled)
                self.assertEqual(Decimal(str(client.orders.create_calls[0]["quantity"])), Decimal(10))
                return None, 0.0
            runner.state_for(filled).book_observed_at = self.now
            outcome = await original(slug, now)
            self.now += timedelta(seconds=.2)
            return outcome

        with patch.object(runner, "_refresh_execution_state_for_decision", AsyncMock()), \
                patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_service_market_book", AsyncMock()), \
                patch.object(runner, "_decide_for_market", decide):
            await runner._decision_sweep(slugs)
        self.assertEqual(visited, [filled, filled, covered])

    async def test_exhausted_current_owner_does_not_starve_other_inventory(self):
        runner, _, slugs = await self.build(count=3)
        held, other, flat = slugs
        self.position(runner, held, "10")
        self.position(runner, other, "10")
        calls = 0

        async def decide(slug, now):
            nonlocal calls
            if slug == held:
                calls += 1
                self.position(runner, held, str(10 + calls))
                # A bounded hard token continuation; no real IOC is submitted.
                runner.reconciler._hard_exit_started_at[held] = now
            return None, 0.0

        with patch.object(runner, "_position_priority_visit_limit", return_value=1), \
                patch.object(runner, "_service_market_book", AsyncMock()):
            visited, _ = await self.sweep(runner, slugs, decide=decide)
        self.assertEqual([s for s, _ in visited], [held, other])
