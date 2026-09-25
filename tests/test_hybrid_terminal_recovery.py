"""Terminal notifications improve scheduling, never replace account proof."""
from datetime import timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import test_hybrid_account as fixtures
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.execution_ledger import PositionState


class HybridTerminalRecoveryTests(IsolatedAsyncioTestCase):
    setUp = fixtures.HybridRunnerTests.setUp
    build = fixtures.HybridRunnerTests.build
    deliver = fixtures.HybridRunnerTests.deliver
    order_event = fixtures.HybridRunnerTests.order_event

    async def test_held_terminal_wakes_proof_but_flat_expiry_keeps_cadence(self):
        for quantity in ("-10", "10", "0"):
            for status in ("ORDER_STATE_EXPIRED", "ORDER_STATE_CANCELED"):
                with self.subTest(quantity=quantity, status=status):
                    runner, _ = await self.build()
                    self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
                    runner.ledger.apply_position_event("test", "held", PositionState(
                        "market-one", Decimal(quantity), self.now), received_at=self.now)
                    completed = runner._rest_poll_completed_at
                    self.now += timedelta(seconds=1)
                    await self.deliver(runner, self.order_event(status=status))
                    self.assertTrue(runner._hybrid_requires_reconcile)
                    self.assertEqual(runner.ledger.position("market-one"), Decimal(quantity))
                    if quantity != "0":
                        self.assertIsNone(runner._rest_poll_completed_at)
                    else:
                        self.assertEqual(runner._rest_poll_completed_at, completed)
                    call = AsyncMock()
                    with self.assertRaises(ApiAdmissionDeferred):
                        await runner.executor.gateway._mutation("POST /v1/orders", call)
                    call.assert_not_awaited()

    async def test_terminal_arriving_during_404_is_projected_without_another_read(self):
        runner, _ = await self.build()
        self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
        self.now += timedelta(seconds=1)
        async def read(*args, **kwargs):
            await runner.private_stream._put(self.order_event())
            raise AdapterError("NotFound", status_code=404)
        with patch.object(runner.executor, "read_order_state", AsyncMock(side_effect=read)) as lookup:
            failures = await runner._read_exact_order_states(["exchange-one"], self.now)
        self.assertEqual(failures, [])
        lookup.assert_awaited_once()
        self.assertFalse(runner.ledger.working_orders())
        self.assertTrue(runner._hybrid_requires_reconcile)
        self.assertEqual(runner.private_stream.pending, 0)

    async def test_absence_other_order_and_auth_failure_never_become_terminal_proof(self):
        for status, notification in ((404, False), (401, True), (503, True), (404, "other")):
            with self.subTest(status=status, notification=notification):
                runner, _ = await self.build()
                self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
                self.fixture.seed_working_order(runner, slug="market-two", side="bid", suffix="two")
                self.now += timedelta(seconds=1)
                async def read(*args, **kwargs):
                    if notification:
                        event = self.order_event()
                        if notification == "other":
                            event.payload["execution"]["order"].update(id="exchange-two", marketSlug="market-two")
                        await runner.private_stream._put(event)
                    raise AdapterError("read failed", status_code=status)
                with patch.object(runner.executor, "read_order_state", AsyncMock(side_effect=read)):
                    if status == 503:
                        with self.assertRaises(AdapterError):
                            await runner._read_exact_order_states(["exchange-one"], self.now)
                    else:
                        failures = await runner._read_exact_order_states(["exchange-one"], self.now)
                        self.assertEqual(len(failures), 1)
                        self.assertEqual(failures[0][0], "exchange-one")

    async def test_racing_terminal_still_requires_complete_account_proof(self):
        for fail_positions in (False, True):
            with self.subTest(fail_positions=fail_positions):
                runner, _ = await self.build()
                self.fixture.seed_working_order(runner, slug="market-one", side="bid", suffix="one")
                self.now += timedelta(seconds=1)
                async def read(*args, **kwargs):
                    await runner.private_stream._put(self.order_event())
                    raise AdapterError("NotFound", status_code=404)
                positions = runner.account.fetch_positions
                async def position_read(**kwargs):
                    if fail_positions:
                        raise AdapterError("position proof unavailable")
                    return await positions(**kwargs)
                with patch.object(runner.executor, "read_order_state", AsyncMock(side_effect=read)), \
                        patch.object(runner.account, "fetch_positions", AsyncMock(side_effect=position_read)) as pos, \
                        patch.object(runner.account, "fetch_open_orders", wraps=runner.account.fetch_open_orders) as orders:
                    if fail_positions:
                        with self.assertRaises(AdapterError):
                            await runner._rest_reconcile(self.now, tag="racing-terminal")
                        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))
                    else:
                        await runner._rest_reconcile(self.now, tag="racing-terminal")
                        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))
                    self.assertGreater(pos.await_count, 0)
                    self.assertGreater(orders.await_count, 0)
