from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.execution_ledger import (
    Execution,
    ExecutionLedger,
    LedgerConflict,
    OrderState,
    PositionState,
    QuoteObservation,
)
from liquidity_bot.models import RewardEarning


UTC = timezone.utc
T0 = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)


def order(*, filled="0", status="OPEN", at=T0) -> OrderState:
    return OrderState(
        client_order_id="client-one",
        exchange_order_id="exchange-one",
        market_slug="market-one",
        side="BUY",
        price=Decimal("0.50"),
        original_qty=Decimal("2.00"),
        filled_qty=Decimal(filled),
        status=status,
        event_at=at,
    )


def execution(execution_id: str, qty: str, *, side="BUY",
              intent="ORDER_INTENT_BUY_LONG", at=T0) -> Execution:
    return Execution(
        execution_id=execution_id,
        client_order_id="client-one",
        exchange_order_id="exchange-one",
        market_slug="market-one",
        side=side,
        price=Decimal("0.50"),
        qty=Decimal(qty),
        fee=Decimal("0.001250"),
        liquidity="MAKER",
        intent=intent,
        event_at=at,
    )


def quote_observation(**changes) -> QuoteObservation:
    values = dict(
        market_slug="market-one", side="bid", observed_at=T0,
        book_observed_at=T0, game_start=T0 + timedelta(hours=5),
        quote_price=Decimal("0.48"), quantity=Decimal("10"),
        decision_reason="reward_positive_candidate",
        best_bid=.49, best_bid_size=100, best_offer=.51,
        best_offer_size=120, spread=.02, bid_depth=500, offer_depth=600,
        model_queue_ahead=100, observed_queue_ahead=250,
        depth_better_than_quote=100, depth_at_quote=150,
        depth_through_quote=250, touch_changes_30s=2,
        touch_changes_120s=7, seconds_since_touch_change=4,
        seconds_to_event=18000, program_id="program-one",
        reward_period="day_of", reward_pool=35, discount_factor=.5,
        target_size=5000, cutoff_price=.47, depth_to_reward_cutoff=5100,
        ticks_from_best=1, order_score=5, total_score=4000,
        snapshot_share=.00125, projected_reward=.002,
        maximum_remaining_reward=1.44, fill_probability=.02,
        fill_risk_driver="queue:100_500", stressed_exit_complete=True,
        stressed_exit_closed_qty=10, stressed_exit_average_price=.47,
        stressed_exit_worst_price=.46, stressed_exit_fee=.01,
        stressed_exit_pnl=-.21, expected_net=-.004,
        reward_cost_safety_multiple=2,
    )
    values.update(changes)
    return QuoteObservation(**values)


class ExecutionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="liquidity-ledger-")) / "ledger.db"
        self.ledger = ExecutionLedger(self.path)

    def tearDown(self):
        self.ledger.close()

    def test_pre_submit_intent_survives_crash_and_is_idempotent(self):
        created = self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one",
            {"price": "0.50", "qty": "2.00"}, created_at=T0)
        self.assertTrue(created)
        self.ledger.close()
        self.ledger = ExecutionLedger(self.path)
        self.assertEqual(len(self.ledger.unresolved_intents()), 1)
        repeated = self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one",
            {"price": "0.50", "qty": "2.00"}, created_at=T0)
        self.assertFalse(repeated)
        with self.assertRaises(LedgerConflict):
            self.ledger.prepare_intent(
                "intent-one", "PLACE", "market-one", "client-one",
                {"price": "0.51", "qty": "2.00"}, created_at=T0)

    def test_quote_observation_is_immutable_and_linked_to_exact_intent(self):
        observation = quote_observation()
        created = self.ledger.prepare_intent(
            "intent-observed", "PLACE", "market-one", "client-observed",
            {"price": "0.48", "qty": "10"}, created_at=T0,
            quote_observation=observation,
            quote_observation_required=True)
        self.assertTrue(created)
        row = self.ledger.query(
            "SELECT * FROM execution_quote_observations")[0]
        self.assertEqual(row["intent_id"], "intent-observed")
        self.assertEqual(row["client_order_id"], "client-observed")
        self.assertEqual(row["observed_queue_ahead"], 250)
        self.assertEqual(row["model_queue_ahead"], 100)
        self.assertIsNone(row["trade_volume_30s"])
        self.assertFalse(self.ledger.prepare_intent(
            "intent-observed", "PLACE", "market-one", "client-observed",
            {"price": "0.48", "qty": "10"}, created_at=T0,
            quote_observation=observation,
            quote_observation_required=True))
        with self.assertRaisesRegex(LedgerConflict, "different content"):
            self.ledger.prepare_intent(
                "intent-observed", "PLACE", "market-one", "client-observed",
                {"price": "0.48", "qty": "10"}, created_at=T0,
                quote_observation=quote_observation(projected_reward=.003),
                quote_observation_required=True)

    def test_missing_required_quote_observation_is_measurable(self):
        self.ledger.prepare_intent(
            "intent-missing-observation", "PLACE", "market-one",
            "client-missing-observation", {}, created_at=T0,
            quote_observation_required=True)
        intent = self.ledger.query(
            "SELECT quote_observation_required FROM execution_intents")[0]
        self.assertEqual(intent["quote_observation_required"], 1)
        self.assertEqual(self.ledger.query(
            "SELECT * FROM execution_quote_observations"), [])

    def test_existing_intent_table_gains_observation_required_column(self):
        import sqlite3

        self.ledger.close()
        legacy = sqlite3.connect(self.path)
        legacy.execute("DROP TABLE execution_intents")
        legacy.execute(
            "CREATE TABLE execution_intents (intent_id TEXT PRIMARY KEY,"
            "operation TEXT NOT NULL,market_slug TEXT NOT NULL,"
            "client_order_id TEXT NOT NULL,exchange_order_id TEXT,"
            "payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,"
            "state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
            "error TEXT)")
        legacy.commit()
        legacy.close()

        self.ledger = ExecutionLedger(self.path)
        columns = {row["name"] for row in self.ledger.connection.execute(
            "PRAGMA table_info(execution_intents)")}
        self.assertIn("quote_observation_required", columns)

    def test_venue_cancel_resolves_an_order_marked_absent_first(self):
        # Reproduces the live failure of 2026-08-14T04:21:56Z exactly. The
        # cancel confirmed at the venue at .923; our own open-order snapshot
        # noticed the order missing at .941 and marked it UNKNOWN_ABSENT with
        # that local wall clock. The authoritative CANCELED event then looked
        # older than our own guess and was dropped, so the order never left
        # UNKNOWN_ABSENT and its CANCEL intent never reconciled -- which trips
        # the 10-second unresolved-mutation guard and kills the session. Every
        # quote cycle ends in a cancel, so this happened on the first one.
        placed = T0
        cancelled_at_venue = T0 + timedelta(microseconds=923497)
        snapshot_at = T0 + timedelta(microseconds=941940)

        self.ledger.apply_order_event(
            "ws", "open", order(status="OPEN", at=placed), received_at=placed)
        self.ledger.prepare_intent(
            "cancel-one", "CANCEL", "market-one", "client-one", {},
            created_at=placed)
        self.ledger.transition_intent(
            "cancel-one", "SUBMITTING", updated_at=placed)
        self.ledger.transition_intent(
            "cancel-one", "ACKNOWLEDGED", updated_at=placed,
            exchange_order_id="exchange-one")

        # Complete snapshot that no longer contains the cancelled order.
        self.ledger.apply_order_snapshot(
            "snapshot-one", [], as_of=snapshot_at,
            observed_at=snapshot_at, complete=True)
        self.assertEqual(self._status(), "UNKNOWN_ABSENT")
        self.assertFalse(self.ledger.risk_state_known())

        # The venue's own terminal record, timestamped before our snapshot.
        self.ledger.apply_order_event(
            "rest_reconcile", "retrieve",
            order(status="CANCELED", at=cancelled_at_venue),
            received_at=snapshot_at + timedelta(seconds=1))

        self.assertEqual(self._status(), "CANCELED")
        self.assertTrue(self.ledger.risk_state_known())
        self.assertEqual(self.ledger.unresolved_intents(), [])

    def test_an_event_recorded_but_not_applied_can_be_retried(self):
        # The live ledger held exactly this row after the 2026-08-14 failure:
        #   order:BVX54B6RRDK1:...923497  applied=0  note='stale_order_event'
        # Dedup then skipped it forever. A venue order id embeds a fixed
        # lastTransactTime, so the cancel could never be re-offered under a new
        # id, and the code fix could not heal the ledger because the retry
        # never reached the corrected logic.
        late = T0 + timedelta(seconds=5)
        self.ledger.apply_order_event(
            "ws", "late", order(status="OPEN", at=late), received_at=late)

        stale = order(status="OPEN", at=T0)
        self.assertFalse(self.ledger.apply_order_event(
            "rest", "retry-me", stale, received_at=late))
        self.assertEqual(
            self.ledger.query(
                "SELECT applied FROM execution_events "
                "WHERE event_id='retry-me'")[0]["applied"], 0)

        # Same id, same payload, offered again: dedup must let it through to
        # the upsert rather than reporting it already handled.
        self.assertTrue(self.ledger._accept_event(
            "rest", "retry-me", late.isoformat(), asdict(stale)))

        # And an id that did apply is still deduped, so nothing lands twice.
        self.assertFalse(self.ledger._accept_event(
            "ws", "late", late.isoformat(),
            asdict(order(status="OPEN", at=late))))

    def _status(self) -> str:
        return str(self.ledger.query(
            "SELECT status FROM execution_orders WHERE "
            "client_order_id='client-one'")[0]["status"])

    def test_timeout_requires_reconciliation_and_blocks_risk(self):
        self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one", {}, created_at=T0)
        self.ledger.transition_intent(
            "intent-one", "SUBMITTING", updated_at=T0 + timedelta(seconds=1))
        self.ledger.transition_intent(
            "intent-one", "UNCERTAIN", updated_at=T0 + timedelta(seconds=2),
            error="request timeout")
        self.assertFalse(self.ledger.risk_state_known())
        self.ledger.transition_intent(
            "intent-one", "RECONCILED", updated_at=T0 + timedelta(seconds=3))
        self.assertTrue(self.ledger.risk_state_known())

    def test_acknowledged_intent_blocks_risk_until_state_is_reconciled(self):
        self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one", {}, created_at=T0)
        self.ledger.transition_intent("intent-one", "SUBMITTING", updated_at=T0)
        self.ledger.transition_intent(
            "intent-one", "ACKNOWLEDGED", updated_at=T0,
            exchange_order_id="exchange-one")
        self.assertFalse(self.ledger.risk_state_known())
        self.ledger.transition_intent("intent-one", "RECONCILED", updated_at=T0)
        self.assertTrue(self.ledger.risk_state_known())

    def test_duplicate_fill_is_idempotent_and_position_is_exact_decimal(self):
        self.ledger.apply_order_event("ws", "order-1", order(), received_at=T0)
        item = execution("fill-one", "0.125")
        self.assertTrue(self.ledger.apply_execution(item, received_at=T0))
        self.assertFalse(self.ledger.apply_execution(item, received_at=T0))
        position = self.ledger.query(
            "SELECT net_qty FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(position["net_qty"], "0.125")
        self.assertEqual(self.ledger.query(
            "SELECT COUNT(*) n FROM execution_fills")[0]["n"], 1)

    def test_duplicate_event_id_with_different_content_fails_closed(self):
        self.ledger.apply_execution(
            execution("fill-one", "0.125"), received_at=T0)
        with self.assertRaises(LedgerConflict):
            self.ledger.apply_execution(
                execution("fill-one", "0.250"), received_at=T0)

    def test_buy_short_intent_reduces_position_even_with_buy_side(self):
        self.ledger.apply_execution(
            execution("short-fill", "0.25", side="BUY",
                      intent="ORDER_INTENT_BUY_SHORT"), received_at=T0)
        row = self.ledger.query(
            "SELECT net_qty FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "-0.25")

    def test_partial_fills_accumulate_once_and_complete_order(self):
        self.ledger.apply_order_event("ws", "order-1", order(), received_at=T0)
        self.ledger.apply_execution(execution("fill-1", "0.75"), received_at=T0)
        self.ledger.apply_execution(
            execution("fill-2", "1.25", at=T0 + timedelta(seconds=1)),
            received_at=T0 + timedelta(seconds=1))
        row = self.ledger.query(
            "SELECT filled_qty,status FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual(row["filled_qty"], "2.00")
        self.assertEqual(row["status"], "FILLED")

    def test_cumulative_order_update_and_execution_do_not_double_count(self):
        cumulative = order(filled="0.75", status="PARTIAL")
        self.ledger.apply_order_event("ws", "order-partial", cumulative,
                                      received_at=T0)
        self.ledger.apply_execution(execution("fill-1", "0.75"), received_at=T0)
        row = self.ledger.query(
            "SELECT filled_qty,status FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual(row["filled_qty"], "0.75")
        self.assertEqual(row["status"], "PARTIAL")

    def test_execution_before_order_snapshot_is_reflected_in_cumulative_fill(self):
        self.ledger.apply_execution(execution("fill-before-order", "0.75"),
                                    received_at=T0)
        self.ledger.apply_order_event("ws", "order-after-fill", order(),
                                      received_at=T0 + timedelta(seconds=1))
        row = self.ledger.query(
            "SELECT filled_qty,status FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual(row["filled_qty"], "0.75")
        self.assertEqual(row["status"], "PARTIAL")

    def test_stale_order_snapshot_cannot_reopen_newer_terminal_order(self):
        newer = order(filled="2.00", status="FILLED", at=T0 + timedelta(seconds=10))
        self.ledger.apply_order_event(
            "ws", "terminal", newer, received_at=T0 + timedelta(seconds=10))
        stale = order(filled="0", status="OPEN", at=T0)
        self.ledger.apply_order_snapshot(
            "snapshot-old", [stale], as_of=T0,
            observed_at=T0 + timedelta(seconds=20), complete=True)
        row = self.ledger.query(
            "SELECT status,filled_qty FROM execution_orders WHERE client_order_id='client-one'")[0]
        self.assertEqual((row["status"], row["filled_qty"]), ("FILLED", "2.00"))

    def test_complete_reconnect_snapshot_marks_missing_order_unknown_not_canceled(self):
        self.ledger.apply_order_event("ws", "open", order(), received_at=T0)
        self.ledger.apply_order_snapshot(
            "snapshot-new", [], as_of=T0 + timedelta(seconds=5),
            observed_at=T0 + timedelta(seconds=6), complete=True)
        row = self.ledger.query(
            "SELECT status,unknown_reason FROM execution_orders")[0]
        self.assertEqual(row["status"], "UNKNOWN_ABSENT")
        self.assertFalse(self.ledger.risk_state_known())

    def test_fresh_position_snapshot_reconciles_but_stale_snapshot_does_not(self):
        update = PositionState("market-one", Decimal("1.750000"),
                               T0 + timedelta(seconds=10))
        self.ledger.apply_position_event(
            "private_ws", "position-new", update,
            received_at=T0 + timedelta(seconds=10))
        self.ledger.apply_position_snapshot(
            "stale", [PositionState("market-one", Decimal("0"), T0)],
            as_of=T0, observed_at=T0 + timedelta(seconds=20),
            complete_account=True)
        row = self.ledger.query("SELECT net_qty FROM execution_positions")[0]
        self.assertEqual(row["net_qty"], "1.750000")
        self.ledger.apply_position_snapshot(
            "fresh", [PositionState("market-one", Decimal("1.50"),
                                    T0 + timedelta(seconds=30))],
            as_of=T0 + timedelta(seconds=30),
            observed_at=T0 + timedelta(seconds=31), complete_account=True)
        row = self.ledger.query("SELECT net_qty FROM execution_positions")[0]
        self.assertEqual(row["net_qty"], "1.50")
        reconciliation = self.ledger.query(
            "SELECT difference FROM execution_position_reconciliations")
        self.assertEqual(reconciliation[-1]["difference"], "-0.250000")

    def test_late_execution_already_in_position_snapshot_is_not_added_twice(self):
        self.ledger.apply_position_snapshot(
            "snapshot", [PositionState("market-one", Decimal("0.75"),
                                       T0 + timedelta(seconds=10))],
            as_of=T0 + timedelta(seconds=10),
            observed_at=T0 + timedelta(seconds=11), complete_account=True)
        self.ledger.apply_execution(
            execution("late-fill", "0.75", at=T0 + timedelta(seconds=5)),
            received_at=T0 + timedelta(seconds=12))
        row = self.ledger.query(
            "SELECT net_qty,source FROM execution_positions WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "0.75")
        self.assertEqual(row["source"], "position_snapshot")

    def test_snapshot_watermark_survives_a_newer_execution(self):
        self.ledger.apply_position_snapshot(
            "snapshot", [PositionState("market-one", Decimal("0.75"),
                                       T0 + timedelta(seconds=10))],
            as_of=T0 + timedelta(seconds=10),
            observed_at=T0 + timedelta(seconds=11), complete_account=True)
        self.ledger.apply_execution(
            execution("new-fill", "0.25", at=T0 + timedelta(seconds=12)),
            received_at=T0 + timedelta(seconds=12))
        self.ledger.apply_execution(
            execution("late-old-fill", "0.75", at=T0 + timedelta(seconds=5)),
            received_at=T0 + timedelta(seconds=13))
        row = self.ledger.query(
            "SELECT net_qty,authoritative_through FROM execution_positions "
            "WHERE market_slug='market-one'")[0]
        self.assertEqual(row["net_qty"], "1.00")
        self.assertEqual(row["authoritative_through"],
                         (T0 + timedelta(seconds=10)).isoformat())

    def test_inventory_basis_rebuilds_weighted_entry_and_flat_reset(self):
        def fill(fill_id, qty, price, intent, seconds):
            return Execution(
                execution_id=fill_id, client_order_id="client-one",
                exchange_order_id="exchange-one", market_slug="market-one",
                side="BUY", price=Decimal(price), qty=Decimal(qty),
                fee=Decimal("0"), intent=intent,
                event_at=T0 + timedelta(seconds=seconds))

        self.ledger.apply_execution(fill(
            "open-a", ".4", ".40", "ORDER_INTENT_BUY_LONG", 1),
            received_at=T0 + timedelta(seconds=1))
        self.ledger.apply_execution(fill(
            "open-b", ".6", ".60", "ORDER_INTENT_BUY_LONG", 2),
            received_at=T0 + timedelta(seconds=2))
        basis = self.ledger.inventory_basis("market-one")
        self.assertEqual(basis.net_qty, Decimal("1.0"))
        self.assertEqual(basis.average_entry_price, Decimal("0.52"))
        self.assertEqual(basis.opened_at, T0 + timedelta(seconds=1))

        self.ledger.apply_execution(fill(
            "close", "1", ".55", "ORDER_INTENT_SELL_LONG", 3),
            received_at=T0 + timedelta(seconds=3))
        self.assertIsNone(self.ledger.inventory_basis("market-one"))

    def test_inventory_basis_fails_closed_when_venue_position_disagrees(self):
        self.ledger.apply_execution(
            execution("one-fill", "1"), received_at=T0)
        self.ledger.apply_position_event(
            "private_ws", "disagree",
            PositionState("market-one", Decimal("2"), T0 + timedelta(seconds=1)),
            received_at=T0 + timedelta(seconds=1))
        self.assertIsNone(self.ledger.inventory_basis("market-one"))

    def test_reward_observations_are_separate_from_execution_pnl(self):
        earning = RewardEarning(
            "2026-08-13", "market-one", "liquidityProgram", 1.25, "PENDING")
        self.ledger.record_reward_earnings([earning], observed_at=T0)
        self.ledger.record_reward_earnings([earning], observed_at=T0)
        rows = self.ledger.query(
            "SELECT date,market_slug,status,reward FROM execution_reward_earnings")
        self.assertEqual(len(rows), 1)
        self.assertEqual(dict(rows[0]), {
            "date": "2026-08-13",
            "market_slug": "market-one",
            "status": "PENDING",
            "reward": "1.25",
        })


if __name__ == "__main__":
    unittest.main()


class RejectReasonTests(unittest.TestCase):
    """The venue's rejection reason must reach the ledger and survive."""

    def ledger(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp(prefix="reject-"))
        led = ExecutionLedger(root / "l.db")
        self.addCleanup(led.close)
        return led

    def order(self, status, reason=None):
        return OrderState(
            client_order_id="c1", exchange_order_id="x1",
            market_slug="market-one", side="ORDER_SIDE_BUY",
            price=Decimal("0.50"), original_qty=Decimal("5"),
            filled_qty=Decimal("0"), status=status,
            event_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
            reject_reason=reason)

    def test_a_rejection_reason_is_recorded(self):
        led = self.ledger()
        led.apply_order_event(
            "src", "e1", self.order("REJECTED", "ORD_REJECT_REASON_POST_ONLY"),
            received_at=datetime(2026, 8, 15, tzinfo=timezone.utc))

        row = led.query("SELECT reject_reason FROM execution_orders")[0]
        self.assertEqual(row["reject_reason"], "ORD_REJECT_REASON_POST_ONLY")

    def test_a_later_snapshot_does_not_erase_the_reason(self):
        """Snapshots of the same rejected order carry no reason.

        Overwriting with NULL would erase the only explanation we ever get.
        """
        led = self.ledger()
        moment = datetime(2026, 8, 15, tzinfo=timezone.utc)
        led.apply_order_event(
            "src", "e1", self.order("REJECTED", "ORD_REJECT_REASON_POST_ONLY"),
            received_at=moment)
        led.apply_order_event(
            "src", "e2", self.order("REJECTED", None), received_at=moment)

        row = led.query("SELECT reject_reason FROM execution_orders")[0]
        self.assertEqual(row["reject_reason"], "ORD_REJECT_REASON_POST_ONLY")

    def test_an_existing_database_gains_the_column(self):
        """CREATE TABLE IF NOT EXISTS never adds a column to a live ledger.

        This ledger is never rebuilt -- it is what proves what we own at the
        venue -- so a new column has to arrive by migration.
        """
        import sqlite3
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp(prefix="reject-old-"))
        path = root / "old.db"
        legacy = sqlite3.connect(path)
        legacy.execute(
            "CREATE TABLE execution_orders (client_order_id TEXT PRIMARY KEY,"
            "exchange_order_id TEXT UNIQUE, market_slug TEXT NOT NULL,"
            "side TEXT, price TEXT, original_qty TEXT,"
            "filled_qty TEXT NOT NULL, status TEXT NOT NULL,"
            "event_at TEXT NOT NULL, received_at TEXT NOT NULL,"
            "unknown_reason TEXT)")
        legacy.commit()
        legacy.close()

        led = ExecutionLedger(path)
        self.addCleanup(led.close)

        columns = {row["name"] for row in
                   led.connection.execute("PRAGMA table_info(execution_orders)")}
        self.assertIn("reject_reason", columns)


class PairSettlementTests(unittest.TestCase):
    """BUY_SHORT is a purchase, and a matched pair settles at one dollar."""

    def ledger(self):
        root = Path(tempfile.mkdtemp(prefix="pairs-"))
        led = ExecutionLedger(root / "l.db")
        self.addCleanup(led.close)
        return led

    def fill(self, led, eid, side, intent, price, qty, fee="0"):
        led.apply_execution(Execution(
            execution_id=eid, market_slug="market-one", side=side,
            price=Decimal(price), qty=Decimal(qty), fee=Decimal(fee),
            event_at=datetime(2026, 8, 15, tzinfo=UTC), intent=intent),
            received_at=datetime(2026, 8, 15, tzinfo=UTC))

    def test_a_round_trip_realises_the_pair_settlement(self):
        """The trade that exposed this: bought 0.39, "sold" 0.67, LOST money.

        BUY_SHORT at 0.67 buys the opposite outcome for 0.67; it does not sell
        at 0.67. Paying 0.39 + 0.67 = 1.06 for a pair worth 1.00 is a six-cent
        loss per contract. Reading the venue's ORDER_SIDE_SELL label as cash IN
        reported +$1.34 on aec-valorant-ff-ep, which actually lost about $0.30.
        """
        led = self.ledger()
        self.fill(led, "e1", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.39", "5")
        self.fill(led, "e2", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT",
                  "0.67", "5")

        # 5 x 0.39 + 5 x 0.67 = 5.30 paid; 5 pairs settle at 5.00.
        self.assertAlmostEqual(
            float(led.realized_pnl("market-one")), -0.30, places=2)

    def test_a_profitable_pair_costs_less_than_a_dollar(self):
        led = self.ledger()
        self.fill(led, "e1", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.45", "5")
        self.fill(led, "e2", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT",
                  "0.50", "5")

        # 0.95 per pair for something worth 1.00.
        self.assertAlmostEqual(
            float(led.realized_pnl("market-one")), 0.25, places=2)

    def test_an_unmatched_leg_realises_nothing(self):
        """One side alone is open inventory, not a completed round trip."""
        led = self.ledger()
        self.fill(led, "e1", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.39", "5")

        # Cash is out, but the position carries that value at its own basis,
        # so nothing is realised. Buying and holding is not a loss.
        self.assertAlmostEqual(
            float(led.realized_pnl("market-one")), 0.0, places=2)


class VenueBasisFallbackTests(unittest.TestCase):
    """A position whose opening fill never arrived must still be exitable."""

    def ledger(self):
        root = Path(tempfile.mkdtemp(prefix="venuebasis-"))
        led = ExecutionLedger(root / "l.db")
        self.addCleanup(led.close)
        return led

    def snapshot(self, led, qty, cost, at):
        led.apply_position_snapshot(
            f"snap-{at.isoformat()}",
            [PositionState(market_slug="market-one", net_qty=Decimal(qty),
                           event_at=at, cost=Decimal(cost))],
            as_of=at, observed_at=at, complete_account=True)

    def test_basis_falls_back_to_the_venue_cost(self):
        """No fill, no basis, no exit -- and no exit means settlement.

        _inventory_plan returns inventory_basis_or_clock_unknown without a
        basis, which retires the market, whose exit then still needs a basis.
        That deadlock stranded demciva-hryole and ff-ep on 2026-08-15. The
        venue already knows what the position cost.
        """
        led = self.ledger()
        at = datetime(2026, 8, 15, 22, tzinfo=UTC)

        self.snapshot(led, "5", "2.98", at)     # no fills at all

        basis = led.inventory_basis("market-one")
        self.assertIsNotNone(basis, "a costed position must be exitable")
        self.assertEqual(basis.net_qty, Decimal("5"))
        self.assertAlmostEqual(float(basis.average_entry_price), 0.596, places=3)

    def test_the_clock_starts_when_the_position_was_first_seen(self):
        """No fill time exists, so the deadline runs from first observation.

        That can only make the deadline LATER, never skip it.
        """
        led = self.ledger()
        first = datetime(2026, 8, 15, 22, tzinfo=UTC)
        later = datetime(2026, 8, 15, 22, 30, tzinfo=UTC)

        self.snapshot(led, "5", "2.98", first)
        self.snapshot(led, "5", "2.98", later)   # still open, clock must hold

        self.assertEqual(led.inventory_basis("market-one").opened_at, first)

    def test_going_flat_clears_the_clock_for_the_next_position(self):
        led = self.ledger()
        first = datetime(2026, 8, 15, 22, tzinfo=UTC)
        flat = datetime(2026, 8, 15, 22, 10, tzinfo=UTC)
        again = datetime(2026, 8, 15, 22, 20, tzinfo=UTC)

        self.snapshot(led, "5", "2.98", first)
        self.snapshot(led, "0", "0", flat)
        self.snapshot(led, "5", "2.50", again)

        self.assertEqual(led.inventory_basis("market-one").opened_at, again)
