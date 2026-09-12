"""Bulk-first REST proofs: fake venue only, including between-read races."""
import asyncio
import json
import tempfile
from datetime import timedelta
from functools import partial
from decimal import Decimal
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import test_live_commissioning as fixtures
from test_venue_state import raw_order
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import LedgerConflict, PositionState
from liquidity_bot.rest_fallback import RestFallbackRunner
from liquidity_bot.rest_polling_policy import RestPollingPolicy, load_rest_polling_policy


class RestBulkPollingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = fixtures.NOW

    async def build(self, count=30):
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            runner, client, _ = self.fixture.build_runner(
                clock=lambda: self.now, max_markets=32)
        runner.rest_polling_policy = RestPollingPolicy(enabled=True)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        for i in range(count):
            slug = f"market-{i}"
            self.fixture.seed_working_order(runner, slug=slug, side="bid", suffix=str(i))
            client.orders.rows[f"exchange-{i}"] = raw_order(
                id=f"exchange-{i}", marketSlug=slug, side="BUY",
                quantity="10", cumQuantity="0", state="ORDER_STATE_NEW",
                price={"value": "0.49", "currency": "USD"},
                createTime=self.now.isoformat())
        self.now += timedelta(seconds=5)
        return runner, client

    async def poll(self, runner):
        await runner._rest_reconcile(self.now, tag="rest-source-decision")

    def position(self, runner, client, slug="market-0", qty="10"):
        runner.ledger.apply_position_event(
            "test", "held", PositionState(slug, Decimal(qty), self.now),
            received_at=self.now)
        client.portfolio.rows[slug] = {
            "netPositionDecimal": qty, "cost": {"value": "4.90", "currency": "USD"}}

    async def test_30_unchanged_orders_use_two_bulk_reads_and_no_exact_gets(self):
        runner, client = await self.build()
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact, \
                patch.object(client.orders, "list", wraps=client.orders.list) as bulk, \
                patch.object(runner.account, "fetch_positions", wraps=runner.account.fetch_positions) as positions, \
                patch.object(client.portfolio, "activities", wraps=client.portfolio.activities) as history:
            await self.poll(runner)
        self.assertEqual(exact.await_count, 0)
        self.assertEqual(bulk.await_count, 2)
        self.assertEqual(positions.await_count, 1)
        self.assertEqual(history.await_count, 1)
        self.assertEqual(len(runner.ledger.working_orders()), 30)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertFalse(client.orders.create_calls or client.orders.cancel_calls)

    async def test_disabled_and_startup_rebind_shutdown_stay_strict(self):
        runner, client = await self.build(2)
        for tag in ("startup", "rest-source-bind", "shutdown", "", "recovery"):
            self.now += timedelta(seconds=1)
            with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
                await runner._rest_reconcile(self.now, tag=tag)
            self.assertEqual(exact.await_count, 2, tag)
        runner.rest_polling_policy = RestPollingPolicy(enabled=False)
        self.now += timedelta(seconds=1)
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual(exact.await_count, 2)

    async def test_failed_prior_proof_recovers_strictly(self):
        runner, client = await self.build(2)
        with patch.object(client.orders, "list", AsyncMock(side_effect=AdapterError("503"))):
            with self.assertRaises(AdapterError):
                await self.poll(runner)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertFalse(runner._bulk_order_proof or runner._bulk_skipped)
        self.now += timedelta(seconds=1)
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual(exact.await_count, 2)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_position_owner_keeps_exact_reads_others_skip(self):
        runner, client = await self.build()
        self.position(runner, client)
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual([c.args[0] for c in exact.await_args_list], ["exchange-0"])
        self.assertEqual(runner.ledger.position("market-0"), 10)

    async def test_pending_cancel_keeps_exact_read_and_ack_is_not_terminal(self):
        runner, client = await self.build(2)
        runner.ledger.prepare_intent("cancel", "CANCEL", "market-0", "client-0", {},
                                     created_at=self.now)
        runner.ledger.transition_intent("cancel", "SUBMITTING", updated_at=self.now)
        runner.ledger.transition_intent("cancel", "ACKNOWLEDGED", updated_at=self.now,
                                       exchange_order_id="exchange-0")
        # Disable the existing policy's cancel reissue for this read-only test.
        with patch.object(runner, "_retry_uncertain_cancels", AsyncMock()), \
                patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual([c.args[0] for c in exact.await_args_list], ["exchange-0"])
        self.assertEqual(len(runner.ledger.unresolved_intents()), 1)

    async def test_initial_partial_order_is_exactly_read(self):
        runner, client = await self.build(2)
        client.orders.rows["exchange-0"].update(
            state="ORDER_STATE_PARTIALLY_FILLED", cumQuantity="3.25",
            lastTransactTime=self.now.isoformat())
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual([c.args[0] for c in exact.await_args_list], ["exchange-0"])
        row = next(row for row in runner.ledger.working_orders()
                   if row["exchange_order_id"] == "exchange-0")
        self.assertEqual(Decimal(row["filled_qty"]), Decimal("3.25"))

    async def test_partial_fill_between_bulk_reads_triggers_exact_confirmation(self):
        runner, client = await self.build(2)
        original = client.orders.list
        count = 0

        async def changing(params):
            nonlocal count
            count += 1
            if count == 2:
                client.orders.rows["exchange-0"].update(
                    state="ORDER_STATE_PARTIALLY_FILLED", cumQuantity="2.35",
                    lastTransactTime=self.now.isoformat())
                client.portfolio.rows["market-0"] = {"netPositionDecimal": "2.35"}
            return await original(params)

        with patch.object(client.orders, "list", changing), \
                patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual([c.args[0] for c in exact.await_args_list], ["exchange-0"])
        self.assertEqual(runner.ledger.position("market-0"), Decimal("2.35"))

    async def test_missing_between_bulk_reads_is_resolved_only_by_terminal_get(self):
        runner, client = await self.build(2)
        original = client.orders.list
        count = 0

        async def changing(params):
            nonlocal count
            count += 1
            if count == 2:
                client.orders.rows["exchange-0"].update(
                    state="ORDER_STATE_CANCELED", lastTransactTime=self.now.isoformat())
            return await original(params)

        with patch.object(client.orders, "list", changing), \
                patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual(exact.await_count, 1)
        self.assertEqual(len(runner.ledger.working_orders()), 1)
        self.assertFalse(runner.ledger.absent_orders())

    async def test_disappearance_without_terminal_proof_cannot_authorize_entry(self):
        runner, client = await self.build(1)
        original = client.orders.list
        count = 0

        async def missing(params):
            nonlocal count
            count += 1
            return await original(params) if count == 1 else {"orders": []}

        with patch.object(client.orders, "list", missing):
            await self.poll(runner)
        self.assertEqual(len(runner.ledger.absent_orders()), 1)
        self.assertFalse(runner.gate.ready(runner.ledger, now=self.now))

    async def test_exact_failure_after_disappearance_revokes_proof(self):
        runner, client = await self.build(1)
        first = await client.orders.list(None)
        with patch.object(client.orders, "list", AsyncMock(side_effect=[first, {"orders": []}])), \
                patch.object(client.orders, "retrieve", AsyncMock(side_effect=AdapterError("404"))):
            with self.assertRaises(AdapterError):
                await self.poll(runner)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
        self.assertEqual(len(runner.ledger.working_orders()), 1)

    async def test_duplicate_and_foreign_rows_fail_even_if_next_snapshot_would_hide_them(self):
        for foreign in (False, True):
            runner, client = await self.build(1)
            row = dict(client.orders.rows["exchange-0"])
            if foreign:
                row["id"] = "someone-elses-order"
            reply = {"orders": [client.orders.rows["exchange-0"], row]}
            with patch.object(client.orders, "list", AsyncMock(return_value=reply)):
                with self.assertRaises(LedgerConflict):
                    await self.poll(runner)
            self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
            if foreign:
                self.assertFalse(runner.ledger.ownership_is_known())

    async def test_cancelled_bulk_proof_discards_context_and_authority(self):
        runner, client = await self.build(1)
        with patch.object(client.orders, "list", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.poll(runner)
        self.assertFalse(runner._bulk_order_proof or runner._bulk_skipped)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_slow_bulk_proof_does_not_refresh_age_at_completion(self):
        runner, client = await self.build(1)
        original = client.orders.list

        async def slow(params):
            self.now += timedelta(seconds=16)
            return await original(params)

        with patch.object(client.orders, "list", slow):
            with self.assertRaises(ApiAdmissionDeferred):
                await self.poll(runner)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_periodic_poll_coalesces_only_when_proof_is_current(self):
        runner, client = await self.build(1)
        await self.poll(runner)
        self.now += timedelta(seconds=.5)
        with patch.object(client.orders, "list", wraps=client.orders.list) as bulk:
            await runner._rest_reconcile(self.now, tag="periodic")
        self.assertEqual(bulk.await_count, 0)
        runner.gate.on_reconciliation_conflict()
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await runner._rest_reconcile(self.now, tag="periodic")
        self.assertEqual(exact.await_count, 1)

    async def test_poll_cadence_flat_active_disabled_and_backward_clock(self):
        runner, client = await self.build(1)
        runner._rest_poll_completed_at = self.now
        self.assertFalse(runner._rest_poll_due(self.now + timedelta(seconds=4.99)))
        self.assertTrue(runner._rest_poll_due(self.now + timedelta(seconds=5)))
        self.position(runner, client)
        self.assertFalse(runner._rest_poll_due(self.now + timedelta(seconds=.99)))
        self.assertTrue(runner._rest_poll_due(self.now + timedelta(seconds=1)))
        self.assertTrue(runner._rest_poll_due(self.now - timedelta(seconds=1)))
        runner.rest_polling_policy = RestPollingPolicy(enabled=False)
        self.assertFalse(runner._rest_poll_due(self.now + timedelta(seconds=1)))

    async def test_failed_history_still_revokes_optimized_proof(self):
        runner, client = await self.build(2)
        with patch.object(client.portfolio, "activities", AsyncMock(side_effect=AdapterError("history"))):
            with self.assertRaises(AdapterError):
                await self.poll(runner)
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_partial_or_missing_quantity_response_fails_closed(self):
        for kind in ("eof", "nextCursor", "quantity"):
            runner, client = await self.build(1)
            reply = await client.orders.list(None)
            if kind == "quantity":
                del reply["orders"][0]["cumQuantity"]
            else:
                reply[kind] = False if kind == "eof" else "next-page"
            with patch.object(client.orders, "list", AsyncMock(return_value=reply)):
                with self.assertRaises(AdapterError):
                    await self.poll(runner)
            self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_stale_bulk_row_keeps_exact_read(self):
        runner, client = await self.build(1)
        client.orders.rows["exchange-0"]["createTime"] = (
            fixtures.NOW - timedelta(seconds=1)).isoformat()
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual(exact.await_count, 1)

    async def test_pending_create_and_unknown_absence_are_never_skipped(self):
        runner, client = await self.build(2)
        runner.ledger.prepare_intent("new", "PLACE", "market-0", "new-client", {},
                                     created_at=self.now)
        runner.ledger.transition_intent("new", "SUBMITTING", updated_at=self.now)
        runner.ledger.transition_intent("new", "ACKNOWLEDGED", updated_at=self.now,
                                       exchange_order_id="exchange-0")
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertIn("exchange-0", [c.args[0] for c in exact.await_args_list])
        self.now += timedelta(seconds=1)
        runner.ledger.apply_order_snapshot("missing", [], as_of=self.now,
                                            observed_at=self.now, complete=True)
        self.assertEqual(len(runner.ledger.absent_orders()), 2)
        self.now += timedelta(seconds=1)
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual(exact.await_count, 2)

    async def test_empty_account_needs_only_one_bulk_read(self):
        runner, client = await self.build(0)
        with patch.object(client.orders, "list", wraps=client.orders.list) as bulk:
            await self.poll(runner)
        self.assertEqual(bulk.await_count, 1)
        self.assertTrue(runner.gate.ready(runner.ledger, now=self.now))

    async def test_fill_discovered_by_real_bulk_poll_precedes_flat_decision(self):
        runner, client = await self.build(2)
        runner._bind_markets(("market-1", "market-0"))
        client.orders.rows["exchange-0"].update(
            state="ORDER_STATE_PARTIALLY_FILLED", cumQuantity="3.25",
            lastTransactTime=self.now.isoformat())
        client.portfolio.rows["market-0"] = {"netPositionDecimal": "3.25"}
        visited = []

        async def decide(slug, now):
            visited.append(slug)
            return None, 0.0

        with patch.object(runner, "_refresh_equity_for_decision", AsyncMock()), \
                patch.object(runner, "_decide_for_market", decide):
            await runner._decision_sweep(runner.active_slugs)
        self.assertEqual(visited, ["market-0", "market-1"])
        self.assertEqual(runner.ledger.position("market-0"), Decimal("3.25"))

    async def test_more_than_30_orders_and_multiple_positions_stay_in_proof(self):
        runner, client = await self.build(40)
        for index, qty in ((0, "10"), (1, "-3.25"), (2, ".05")):
            slug = f"market-{index}"
            runner.ledger.apply_position_event(
                "test", slug, PositionState(slug, Decimal(qty), self.now),
                received_at=self.now)
            client.portfolio.rows[slug] = {"netPositionDecimal": qty}
        with patch.object(client.orders, "retrieve", wraps=client.orders.retrieve) as exact:
            await self.poll(runner)
        self.assertEqual({c.args[0] for c in exact.await_args_list},
                         {"exchange-0", "exchange-1", "exchange-2"})
        self.assertEqual(len(runner.ledger.working_orders()), 40)
        self.assertEqual(runner.ledger.position("market-1"), Decimal("-3.25"))
        self.assertEqual(runner.ledger.position("market-2"), Decimal(".05"))


def _optimized_variant(name):
    async def test(self):
        factory = partial(RestFallbackRunner,
                          rest_polling_policy=RestPollingPolicy(enabled=True))
        with patch.object(fixtures, "CommissioningRunner", factory):
            await getattr(self.fixture, name)()
    return test


for _name in (
    "test_shutdown_local_not_sent_does_not_spend_ioc_attempts",
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
    setattr(RestBulkPollingTests, "test_optimized_" + _name[5:], _optimized_variant(_name))


class RestPollingPolicyTests(TestCase):
    def test_strict_policy_validation_and_roundtrip(self):
        good = dict(enabled=True, normal_poll_seconds=5, active_poll_seconds=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(good))
            self.assertEqual(load_rest_polling_policy(path), RestPollingPolicy(**good))
            for bad in ({**good, "enabled": 1}, {**good, "active_poll_seconds": 0},
                        {**good, "normal_poll_seconds": 6}, {**good, "extra": 1},
                        {**good, "active_poll_seconds": float("nan")}, [], {}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    load_rest_polling_policy(path)
            path.write_text('{"enabled": true, "enabled": false}')
            with self.assertRaises(ValueError):
                load_rest_polling_policy(path)
            with self.assertRaises(ValueError):
                load_rest_polling_policy(path.parent / "missing.json")
