"""Evidence must explain a decision, never affect it or make a venue call."""
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch

import test_live_commissioning as fixtures
from test_reconciler import T0, book, frame, policy
from test_venue_state import raw_order
from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler, PlanAction, WorkingOrder
from liquidity_bot.reducer_diagnostics import capture_reducer_decision, completed_reducer_decision
from liquidity_bot.rest_fallback import RestFallbackRunner
from liquidity_bot.rest_polling_policy import RestPollingPolicy


class ReducerDiagnosticTests(TestCase):
    def capture(self, candidate, action=None, source="rest"):
        if action is None:
            action = DesiredStateReconciler(policy(age=900)).plan(candidate)[0]
        return capture_reducer_decision(candidate, action, source=source,
                                        apply_position=candidate.position)

    def test_long_short_reprice_inputs_and_plan_are_unchanged(self):
        for qty, side, old_price, target_price in (
                ("10", "offer", .53, .51), ("-10", "bid", .47, .49)):
            for source in ("rest", "websocket"):
                with self.subTest(qty=qty, source=source):
                    candidate = frame(position=qty, entry=.50, opened=T0,
                        snapshot=book(bid=.48, offer=.52),
                        orders=(WorkingOrder("old", side, old_price, Decimal(10)),))
                    planner = DesiredStateReconciler(policy(age=900))
                    before = planner.plan(candidate)
                    row = self.capture(candidate, before[0], source)
                    self.assertEqual(planner.plan(candidate), before)
                    self.assertEqual(row["reason"], "replace_reducing_at_active_touch")
                    self.assertEqual(row["target"]["price"], old_price)
                    self.assertTrue(row["target"]["exact_quantity"])
                    self.assertFalse(row["target"]["at_reducing_touch"])
                    self.assertEqual(row["passive_candidate_price"], target_price)
                    self.assertEqual(row["source"], source)
                    self.assertEqual((row["best_bid"], row["best_offer"]), (.48, .52))
                    self.assertEqual(row["enforcement"], "none")
                    json.dumps(row, allow_nan=False)

    def test_partial_size_and_duplicate_are_distinguishable(self):
        exact = WorkingOrder("keep", "offer", .51, Decimal("6.85"))
        for other in (replace(exact, order_id="wrong", remaining_qty=Decimal(10)),
                      replace(exact, order_id="duplicate")):
            row = self.capture(frame(position="6.85", opened=T0, entry=.50,
                                     orders=(exact, other)))
            self.assertEqual(row["preferred_passive_order_id"], "keep")
            self.assertEqual(row["target"]["exact_quantity"], other.order_id == "duplicate")
            self.assertTrue(row["target"]["at_reducing_touch"])
            self.assertEqual(row["reducing_order_count"], 2)

    def test_builder_is_not_described_as_exit_and_price_can_be_missing(self):
        candidate = frame(position="10", opened=T0, entry=.50, orders=(
            WorkingOrder("entry", "bid", .49, Decimal(10)),))
        row = self.capture(replace(candidate, book=replace(candidate.book, offers=())))
        self.assertEqual(row["reason"], "cancel_building_after_fill")
        self.assertFalse(row["target"]["is_reducing"])
        self.assertEqual(row["building_order_count"], 1)
        self.assertIsNone(row["best_offer"])
        self.assertIsNone(row["passive_candidate_price"])

    def test_one_sided_losing_and_unknown_basis_follow_existing_price_helper(self):
        for qty, side, entry, expected in (("10", "offer", .80, .52),
                                           ("-10", "bid", .20, .48),
                                           ("10", "offer", None, .51)):
            candidate = frame(position=qty, entry=entry, opened=T0,
                              snapshot=book(bid=.48, offer=.52))
            action = PlanAction(ActionKind.PLACE_POST_ONLY, "market-one", "diagnostic_only",
                                side=side, price=expected, qty=Decimal(10))
            row = self.capture(candidate, action)
            self.assertEqual(row["passive_candidate_price"], expected)
            self.assertIsNone(row["target"])
            one_sided = replace(candidate, book=replace(candidate.book,
                bids=() if side == "offer" else candidate.book.bids,
                offers=() if side == "bid" else candidate.book.offers))
            row = self.capture(one_sided, action)
            self.assertEqual(row["passive_candidate_price"], .52 if side == "offer" else .48)

    def test_unknown_future_and_stale_evidence_remain_explicit(self):
        candidate = frame(position="10", state_known=False,
            orders=(WorkingOrder("exit", "offer", .53, Decimal(10)),))
        action = PlanAction(ActionKind.CANCEL, "market-one", "test", order_id="exit")
        for seconds in (-2, 10):
            row = self.capture(replace(candidate, now=T0 + timedelta(seconds=seconds),
                                       book_known=False), action)
            self.assertEqual(row["book_age_seconds"], seconds)
            self.assertFalse(row["state_known"])
            self.assertFalse(row["book_known"])

    def test_flat_empty_and_critical_do_not_emit_exit_evidence(self):
        self.assertIsNone(self.capture(frame()))
        self.assertIsNone(self.capture(frame(position="1"),
            PlanAction(ActionKind.CRITICAL, "market-one", "unknown")))

    def test_capture_is_detached_and_ack_is_not_terminal_proof(self):
        candidate = frame(position="10", opened=T0, entry=.50)
        row = self.capture(candidate)
        before = dict(row)
        ack = SimpleNamespace(submitted=SimpleNamespace(exchange_order_id="new"),
                              cancel_intent_id=None)
        output = completed_reducer_decision(row, at=T0, result=ack)
        self.assertEqual(output["outcome"], "place_acknowledged")
        self.assertEqual(output["submitted_order_id"], "new")
        self.assertEqual(row, before)
        cancelled = completed_reducer_decision(row, at=T0,
            result=SimpleNamespace(submitted=None, cancel_intent_id="cancel"))
        self.assertEqual(cancelled["outcome"], "cancel_acknowledged")
        self.assertEqual(completed_reducer_decision(row, at=T0)["outcome"], "no_acknowledgement")
        failed = completed_reducer_decision(row, at=T0, error_type="AdapterError")
        self.assertEqual(failed["outcome"], "driver_exception")
        self.assertNotIn("error_message", failed)

    def test_deadline_action_is_not_confused_with_passive_candidate(self):
        candidate = frame(position="10", opened=T0, entry=.50,
                          maintenance_flatten_required=True)
        action = PlanAction(ActionKind.PLACE_IOC, "market-one", "hard", side="offer",
                            price=.40, qty=Decimal(10))
        row = self.capture(candidate, action)
        self.assertEqual(row["action_price"], .40)
        self.assertNotEqual(row["passive_candidate_price"], .40)
        self.assertTrue(row["maintenance_flatten_required"])


class ReducerDiagnosticIntegrationTests(IsolatedAsyncioTestCase):
    async def build(self):
        fixture = fixtures.LiveCommissioningTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.now = fixtures.NOW
        with patch.object(fixtures, "CommissioningRunner", RestFallbackRunner):
            runner, client, _ = fixture.build_runner(clock=lambda: self.now, max_markets=32)
        runner.rest_polling_policy = RestPollingPolicy(enabled=True)
        await runner.preflight()
        self.addAsyncCleanup(runner.market_stream.close)
        self.addAsyncCleanup(runner.private_stream.close)
        held = runner.market_slug
        fixture.seed_working_order(runner, slug=held, side="offer", suffix="reducer")
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

    async def test_real_cancel_emits_planning_frame_not_later_book(self):
        runner, client, held = await self.build()
        original = runner.driver.apply_one
        old_book = runner.state_for(held).book

        async def apply(*args, **kwargs):
            result = await original(*args, **kwargs)
            runner.state_for(held).book = book(bid=.10, offer=.90)
            return result

        with patch.object(runner.driver, "apply_one", apply), \
                patch("liquidity_bot.runner_decisions.print") as output:
            outcome, delay = await runner._decide_for_market(held, self.now)
        rows = [json.loads(c.args[0].split(" ", 1)[1]) for c in output.call_args_list
                if str(c.args[0]).startswith("[reducer-decision] ")]
        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        self.assertEqual(len(client.orders.cancel_calls), 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"]["price"], .49)
        self.assertEqual(rows[0]["best_offer"], old_book.best_offer.price)
        self.assertEqual(rows[0]["outcome"], "cancel_acknowledged")

    async def test_capture_failure_does_not_change_cancel(self):
        runner, client, held = await self.build()
        with patch("liquidity_bot.runner_decisions.capture_reducer_decision",
                   side_effect=ValueError("bad observation")):
            outcome, delay = await runner._decide_for_market(held, self.now)
        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        self.assertEqual(len(client.orders.cancel_calls), 1)

    async def test_output_failure_does_not_turn_ack_into_execution_failure(self):
        runner, client, held = await self.build()

        def output(*args, **kwargs):
            if str(args[0]).startswith("[reducer-decision]"):
                raise OSError("sink unavailable")

        with patch("liquidity_bot.runner_decisions.print", side_effect=output):
            outcome, delay = await runner._decide_for_market(held, self.now)
        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        self.assertEqual(len(client.orders.cancel_calls), 1)

    async def test_driver_exception_is_recorded_and_original_recovery_retained(self):
        runner, client, held = await self.build()
        from unittest.mock import AsyncMock
        with patch.object(runner.driver, "apply_one", side_effect=RuntimeError("secret must not log")), \
                patch.object(runner, "_handle_action_failure", AsyncMock(return_value=None)) as recover, \
                patch("liquidity_bot.runner_decisions.print") as output:
            outcome, delay = await runner._decide_for_market(held, self.now)
        self.assertIsNone(outcome)
        self.assertGreater(delay, 0)
        recover.assert_awaited_once()
        rows = [c.args[0] for c in output.call_args_list
                if str(c.args[0]).startswith("[reducer-decision]")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("secret", rows[0])
        self.assertEqual(json.loads(rows[0].split(" ", 1)[1])["outcome"], "driver_exception")
        self.assertFalse(client.orders.cancel_calls)
