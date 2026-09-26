"""Review regressions: passive exit continuity and actual REST receipt time."""

import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from functools import partial
from unittest.mock import patch

import test_live_commissioning as live
import test_reconciler as plans
from test_venue_state import raw_order
from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.execution_ledger import Execution
from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler, WorkingOrder
from liquidity_bot.risk_windows import RiskWindowPolicy, load_risk_window_policy


class DeferredExitTests(unittest.TestCase):
    def held(self, *, short=False, thin=False, orders=True):
        now = plans.T0 + timedelta(seconds=60)
        book = plans.book(now, bid=.93 if short else .05,
                          offer=.95 if short else .07,
                          depth=.1 if thin else 100)
        side, price = ("bid", .93) if short else ("offer", .07)
        return plans.frame(
            now=now, position="-2" if short else "2", opened=plans.T0,
            entry=.10 if short else .90, snapshot=book,
            orders=(WorkingOrder("reducer", side, price, D(2)),) if orders else ())

    def reconciler(self):
        return DesiredStateReconciler(plans.policy(max_early_exit_loss=.5))

    def test_expensive_or_thin_exit_keeps_exact_reducer_on_both_sides(self):
        for short in (False, True):
            for thin in (False, True):
                with self.subTest(short=short, thin=thin):
                    r = self.reconciler()
                    self.assertEqual(r.plan(self.held(short=short, thin=thin)), [])
                    self.assertEqual(r.hard_exit_attempts("market-one"), 0)

    def test_deferred_exit_replaces_missing_reducer_without_ioc(self):
        for short in (False, True):
            for thin in (False, True):
                with self.subTest(short=short, thin=thin):
                    f = self.held(short=short, thin=thin, orders=False)
                    actions = self.reconciler().plan(f)
                    self.assertEqual(len(actions), 1)
                    self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
                    self.assertEqual(actions[0].side, "bid" if short else "offer")
                    self.assertEqual(actions[0].qty, abs(f.position))

    def test_builders_and_duplicate_or_oversize_reducers_still_cancel_first(self):
        f = self.held()
        builder = WorkingOrder("builder", "bid", .05, D(2))
        wrong = WorkingOrder("oversized", "offer", .07, D(3))
        duplicate = WorkingOrder("duplicate", "offer", .07, D(2))
        actions = self.reconciler().plan(replace(
            f, working_orders=(*f.working_orders, builder, wrong, duplicate)))
        self.assertTrue(all(a.kind == ActionKind.CANCEL for a in actions))
        self.assertEqual([a.order_id for a in actions], ["builder", "oversized", "duplicate"])

    def test_partial_fill_replaces_only_remaining_quantity(self):
        f = replace(self.held(), position=D("0.5"))
        r = self.reconciler()
        # Thin rather than expensive ensures the small remaining exit defers.
        f = replace(f, book=plans.book(f.now, bid=.05, offer=.07, depth=.01))
        self.assertEqual(r.plan(f)[0].kind, ActionKind.CANCEL)
        action = r.plan(replace(f, working_orders=()))[0]
        self.assertEqual(action.kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(action.qty, D("0.5"))

    def test_inflight_cancel_and_unknown_state_never_create_replacement(self):
        f, r = self.held(), self.reconciler()
        r.note_hard_exit_cancel_submitted(f.market.slug, "reducer", submitted_at=f.now)
        self.assertEqual(r.plan(f), [])
        late = replace(f, now=f.now + timedelta(seconds=20), book_observed_at=f.now + timedelta(seconds=20))
        self.assertEqual(r.plan(late)[0].reason, "hard_exit_blocked_cancel_unconfirmed")
        unknown = replace(f, working_orders=(), state_known=False)
        self.assertTrue(all(a.kind not in {ActionKind.PLACE_POST_ONLY, ActionKind.PLACE_IOC}
                            for a in r.plan(unknown)))
        self.assertEqual(r.plan(replace(f, working_orders=()))[0].kind, ActionKind.PLACE_POST_ONLY)

    def test_improving_book_resumes_sequential_cancel_then_ioc(self):
        f, r = self.held(), self.reconciler()
        self.assertEqual(r.plan(f), [])
        f = replace(f, book=plans.book(f.now, bid=.89, offer=.91))
        action = r.plan(f)[0]
        self.assertEqual(action.kind, ActionKind.CANCEL)
        self.assertEqual(action.reason, "hard_timeout_cancel_confirm_first")
        r.note_hard_exit_cancel_submitted(f.market.slug, action.order_id, submitted_at=f.now)
        self.assertEqual(r.plan(f), [])
        self.assertEqual(r.plan(replace(f, working_orders=()))[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(r.hard_exit_attempts(f.market.slug), 1)
        self.assertEqual(r.plan(replace(f, working_orders=()))[0].reason,
                         "hard_exit_sent_but_inventory_remains")

    def test_absolute_deadlines_override_price_deferral(self):
        for cause in ("kickoff", "maintenance", "hard"):
            with self.subTest(cause=cause):
                f, r = self.held(), self.reconciler()
                if cause == "kickoff":
                    f = replace(f, market=replace(f.market, game_start=f.now + timedelta(seconds=299)))
                else:
                    field = "maintenance_flatten_required" if cause == "maintenance" else "hard_stop_flatten_required"
                    f = replace(f, **{field: True})
                self.assertEqual(r.plan(f)[0].kind, ActionKind.CANCEL)
                self.assertEqual(r.plan(replace(f, working_orders=()))[0].kind, ActionKind.PLACE_IOC)


class ReceiptWindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = live.LiveCommissioningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def test_runner_places_deferred_reducer_without_retiring_or_repeated_cancel(self):
        for mode in ("rest", "websocket"):
            with self.subTest(mode=mode):
                clock = [live.NOW]
                with patch.object(live, "CommissioningRunner", partial(
                        AccountSourceRunner, account_state_mode=mode)):
                    runner, client, _ = self.fixture.build_runner(
                        clock=lambda: clock[0], risk_window_policy=RiskWindowPolicy())
                self.addAsyncCleanup(runner.market_stream.close)
                self.addAsyncCleanup(runner.private_stream.close)
                await runner.preflight()
                slug = runner.market_slug
                runner.ledger.apply_execution(Execution(
                    "open", slug, "BUY", D(".95"), D(10), D(0),
                    live.NOW, intent="ORDER_INTENT_BUY_LONG"), received_at=live.NOW)
                client.portfolio.rows[slug] = {
                    "netPositionDecimal": "10", "cost": {"value": "9.5", "currency": "USD"},
                    "cashValue": {"value": ".7", "currency": "USD"}}
                clock[0] += timedelta(seconds=901)
                await runner._rest_reconcile(clock[0])
                await runner._refresh_balance(clock[0])
                state = runner.state_for(slug)
                state.book = plans.book(clock[0], bid=.05, offer=.07)
                state.book_observed_at = clock[0]
                result = await runner._decide_for_market(slug, clock[0])
                self.assertEqual(result, (None, 0.0))
                self.assertNotIn(slug, runner._retired)
                self.assertEqual(len(client.orders.create_calls), 1)
                self.assertEqual(client.orders.create_calls[0]["tif"], "TIME_IN_FORCE_GOOD_TILL_CANCEL")
                self.assertEqual(D(str(client.orders.create_calls[0]["quantity"])), D(10))
                client.orders.rows["fake-order-1"] = raw_order(
                    id="fake-order-1", marketSlug=slug, side="SELL", quantity="10", cumQuantity="0",
                    state="ORDER_STATE_NEW", createTime=clock[0].isoformat(),
                    price={"value": ".07", "currency": "USD"})
                clock[0] += timedelta(seconds=1)
                await runner._rest_reconcile(clock[0])
                await runner._decide_for_market(slug, clock[0])
                self.assertEqual(len(client.orders.create_calls), 1)
                self.assertEqual(client.orders.cancel_calls, [])
                self.assertNotIn(slug, runner._retired)

    async def test_async_reconcile_spanning_boundary_counts_loss_once_in_both_sources(self):
        for mode in ("rest", "websocket"):
            for hour in (4, 10, 16, 22):  # midnight/06/12/18 ET in September
                with self.subTest(mode=mode, hour=hour):
                    boundary = datetime(2026, 9, 17, hour, tzinfo=timezone.utc)
                    clock = [live.NOW]
                    with patch.object(live, "CommissioningRunner", partial(
                            AccountSourceRunner, account_state_mode=mode)):
                        runner, client, _ = self.fixture.build_runner(
                            clock=lambda: clock[0], risk_window_policy=RiskWindowPolicy())
                    self.addAsyncCleanup(runner.market_stream.close)
                    self.addAsyncCleanup(runner.private_stream.close)
                    await runner.preflight()
                    start, received = boundary - timedelta(seconds=1), boundary + timedelta(seconds=2)
                    clock[0] = start
                    slug = runner.market_slug
                    entry = Execution("open", slug, "BUY", D(".8"), D(10), D(0),
                                      start - timedelta(seconds=10), intent="ORDER_INTENT_BUY_LONG")
                    runner.ledger.apply_execution(entry, received_at=entry.event_at)
                    # Snapshot begins before the fill; history finishes after it.
                    client.portfolio.rows[slug] = {
                        "netPositionDecimal": "10", "cost": {"value": "8", "currency": "USD"},
                        "cashValue": {"value": "8", "currency": "USD"}}
                    await runner._refresh_balance(start)
                    self.assertEqual(runner._window_risk.realized_equity, D(0))
                    close = Execution("close", slug, "SELL", D(".2"), D(10), D(0),
                                      boundary + timedelta(seconds=1), intent="ORDER_INTENT_SELL_LONG")

                    async def history(_):
                        await asyncio.sleep(0)
                        clock[0] = received
                        return (close, entry)  # newest-first and duplicate older entry

                    with patch.object(runner, "_fetch_execution_history", history):
                        await runner._rest_reconcile(start)
                    receipts = {row["execution_id"]: row["received_at"] for row in
                                runner.ledger.query("SELECT execution_id,received_at FROM execution_fills")}
                    self.assertEqual(receipts["close"], received.isoformat())
                    self.assertEqual(receipts["open"], entry.event_at.isoformat())
                    client.portfolio.rows = {}
                    await runner._refresh_balance(received)
                    self.assertEqual(runner._window_risk.realized_equity, D(-6))
                    self.assertEqual(runner._loss_entry_order_cap, 25)
                    self.assertFalse(runner._hard_stop_active())
                    # Replay in another window must not move or charge either fill twice.
                    later = boundary + timedelta(hours=6, seconds=2)
                    clock[0] = later
                    async def replay(_):
                        return (close, entry)
                    with patch.object(runner, "_fetch_execution_history", replay):
                        await runner._rest_reconcile(later)
                    self.assertEqual(runner.ledger.realized_pnl(received_since=boundary,
                                     received_through=received), D(-6))
                    self.assertEqual(runner.ledger.realized_pnl(received_since=later), D(0))
