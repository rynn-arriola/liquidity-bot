from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch

import test_live_commissioning as fixtures
from liquidity_bot.execution_timing import ExecutionTimings
from liquidity_bot.reconciler import ActionKind, PlanAction


class TimingTests(TestCase):
    def setUp(self):
        self.rows = []
        self.timing = ExecutionTimings(self.rows.append, limit=2)
        self.now = fixtures.NOW
        self.action = PlanAction(ActionKind.PLACE_IOC, "one", "hard_inventory_timeout")
        self.ack = SimpleNamespace(submitted=object(), cancel_intent_id=None)

    def observe(self, quantities, seconds=0, complete=True, source="rest"):
        self.timing.observe(quantities, at=self.now + timedelta(seconds=seconds),
                            source=source, complete=complete)

    def action_at(self, seconds, result=None, position=Decimal(2)):
        self.timing.action(self.action, self.ack if result is None else result,
            position=position, at=self.now + timedelta(seconds=seconds),
            source="rest", deadline=self.now + timedelta(seconds=6))

    def test_observation_cancel_replacement_and_deadline_are_measured(self):
        self.observe({"one": Decimal(2)})
        self.timing.cancel_confirmed("one", at=self.now + timedelta(seconds=3), source="rest")
        self.action_at(8)
        row = self.rows[-1]
        self.assertEqual(row["observation_kind"], "position_initial")
        self.assertEqual(row["position_observation_age_seconds"], 8)
        self.assertEqual(row["cancel_confirmation_age_seconds"], 5)
        self.assertEqual(row["deadline_offset_seconds"], 2)
        self.action_at(10)
        self.assertIsNone(self.rows[-1]["cancel_confirmation_age_seconds"])

    def test_unknown_restart_future_and_no_send_are_not_zero_latency(self):
        self.action_at(1)
        self.assertIsNone(self.rows[-1]["position_observation_age_seconds"])
        self.observe({"one": Decimal(2)}, 5)
        self.action_at(4)
        self.assertIsNone(self.rows[-1]["position_observation_age_seconds"])
        count = len(self.rows)
        self.action_at(6, result=SimpleNamespace(submitted=None, cancel_intent_id=None))
        self.action_at(6, position=Decimal(0))
        self.assertEqual(len(self.rows), count)

    def test_replay_source_change_flat_and_new_episode(self):
        self.observe({"one": Decimal(2)})
        self.observe({"one": Decimal(2)}, 4, source="websocket")
        self.assertEqual(len(self.rows), 1)
        self.action_at(5)
        self.assertEqual(self.rows[-1]["position_observation_age_seconds"], 5)
        self.observe({}, 6, complete=False)
        self.assertIn("one", self.timing.positions)
        self.observe({}, 7)
        self.assertEqual(self.rows[-1]["event"], "flat_observed")
        self.observe({"one": Decimal(3)}, 8)
        self.assertEqual(self.rows[-1]["event"], "position_initial")

    def test_bounded_memory_and_clock_regression(self):
        for i in range(5):
            self.observe({str(i): Decimal(1)}, i, complete=False)
            self.timing.cancel_confirmed(str(i), at=self.now, source="rest")
        self.assertEqual(len(self.timing.positions), 2)
        self.assertEqual(len(self.timing.cancels), 2)
        self.observe({"4": Decimal(2)}, 3, complete=False)
        self.assertEqual(self.timing.positions["4"][0], 1)


class TimingIsolationTests(IsolatedAsyncioTestCase):
    async def test_timing_failure_does_not_break_actual_live_event_exit(self):
        fixture = fixtures.LiveCommissioningTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from liquidity_bot.execution_ledger import Execution
        runner, client, _ = fixture.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        runner._evaluate_rewards(fixtures.NOW)
        runner.ledger.apply_execution(Execution(
            execution_id="timing-held", market_slug="market-one", side="BUY",
            price=Decimal("0.49"), qty=Decimal("10"), fee=Decimal("0"),
            intent="ORDER_INTENT_BUY_LONG", event_at=fixtures.NOW), received_at=fixtures.NOW)
        client.events.live.add("market-one")
        await runner.gateway.refresh_event_states(runner.active_slugs, force=True)
        with patch.object(ExecutionTimings, "action", side_effect=OSError("output unavailable")) as record:
            outcome, delay = await runner._decide_for_market("market-one", fixtures.NOW)
        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(client.orders.create_calls[0]["tif"], "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
        self.assertGreater(record.call_count, 0)
