from __future__ import annotations

import sqlite3
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
    TradeStateObservation,
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
              intent="ORDER_INTENT_BUY_LONG", at=T0,
              trade_id=None, trade_state=None) -> Execution:
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
        trade_id=trade_id,
        trade_state=trade_state,
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

    def test_read_only_open_reuses_accounting_without_writing(self):
        observed = self.path.stat()
        reader = ExecutionLedger.open_read_only(self.path)
        try:
            self.assertEqual(reader.realized_pnl(), Decimal("0"))
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                reader.connection.execute(
                    "INSERT INTO execution_orders(market_slug) VALUES ('x')")
        finally:
            reader.close()
        after = self.path.stat()
        self.assertEqual(
            (after.st_size, after.st_mtime_ns),
            (observed.st_size, observed.st_mtime_ns))

    def test_active_order_status_lookup_uses_status_leading_index(self):
        plan = self.ledger.query(
            "EXPLAIN QUERY PLAN SELECT market_slug FROM execution_orders "
            "WHERE status IN ('OPEN','PARTIAL','PENDING')")

        details = " ".join(str(row["detail"]) for row in plan)
        self.assertIn("SEARCH execution_orders", details)
        self.assertIn("idx_execution_orders_status", details)

    def test_market_fill_lookup_uses_market_leading_index(self):
        plan = self.ledger.query(
            "EXPLAIN QUERY PLAN SELECT price,qty,fee,intent,event_at "
            "FROM execution_fills WHERE reversed=0 AND market_slug=? "
            "ORDER BY event_at,received_at,execution_id", ("market-one",))

        details = " ".join(str(row["detail"]) for row in plan)
        self.assertIn("SEARCH execution_fills", details)
        self.assertIn("idx_execution_fills_market", details)

    def test_quote_observation_rejects_nonpositive_selection_rank(self):
        with self.assertRaisesRegex(ValueError, "selection rank"):
            quote_observation(selection_rank=0)
        with self.assertRaisesRegex(ValueError, "selection rank"):
            quote_observation(selection_rank=-1)

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
        observation = quote_observation(
            retained_value_ratio=.65,
            stressed_exit_value=3.25,
            would_refuse_retained_60=False,
            would_refuse_retained_70=True,
            would_refuse_retained_80=True,
            replacement_market_slug="market-spare",
            replacement_projected_reward=.004,
            replacement_expected_net=.001,
            replacement_reward_delta=.002,
            replacement_selection_age_seconds=30,
            replacement_reason="recent_selector_spare",
            slot_would_go_dark=False,
        )
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
        self.assertEqual(row["would_refuse_retained_70"], 1)
        self.assertEqual(row["replacement_market_slug"], "market-spare")
        self.assertEqual(row["slot_would_go_dark"], 0)
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

    def test_fill_roles_come_from_owned_place_role_not_buy_intent(self):
        for role, required in (("entry", True), ("exit", False)):
            self.ledger.prepare_intent(
                f"intent-{role}", "PLACE", "market-one", f"client-{role}",
                {"role": role}, created_at=T0,
                quote_observation_required=required)
        self.ledger.apply_execution(Execution(
            "entry-fill", "market-one", "ORDER_SIDE_BUY", Decimal("0.40"),
            Decimal("1"), Decimal("0"), T0,
            client_order_id="client-entry", intent="ORDER_INTENT_BUY_LONG"),
            received_at=T0)
        self.ledger.apply_execution(Execution(
            "exit-fill", "market-one", "ORDER_SIDE_SELL", Decimal("0.42"),
            Decimal("1"), Decimal("0"), T0,
            client_order_id="client-exit", intent="ORDER_INTENT_BUY_SHORT"),
            received_at=T0)

        self.assertEqual(
            self.ledger.fill_role_counts(since=T0), (1, 1))

    def test_fill_roles_exclude_manual_and_reversed_fills(self):
        self.ledger.prepare_intent(
            "intent-reversed", "PLACE", "market-one", "client-reversed", {},
            created_at=T0, quote_observation_required=False)
        self.ledger.apply_execution(Execution(
            "reversed-fill", "market-one", "ORDER_SIDE_SELL", Decimal("0.42"),
            Decimal("1"), Decimal("0"), T0,
            client_order_id="client-reversed", intent="ORDER_INTENT_BUY_SHORT"),
            received_at=T0)
        self.ledger.connection.execute(
            "UPDATE execution_fills SET reversed=1 WHERE execution_id=?",
            ("reversed-fill",))
        self.ledger.apply_execution(Execution(
            "manual-fill", "market-one", "ORDER_SIDE_SELL", Decimal("0.42"),
            Decimal("1"), Decimal("0"), T0,
            client_order_id="venue:manual", intent="ORDER_INTENT_BUY_SHORT"),
            received_at=T0)
        self.ledger.connection.commit()

        self.assertEqual(
            self.ledger.fill_role_counts(since=T0), (0, 0))

    def test_fill_roles_use_execution_time_not_late_receipt_time(self):
        self.ledger.prepare_intent(
            "intent-old", "PLACE", "market-one", "client-old", {},
            created_at=T0, quote_observation_required=True)
        self.ledger.apply_execution(Execution(
            "old-fill", "market-one", "ORDER_SIDE_BUY", Decimal("0.40"),
            Decimal("1"), Decimal("0"), T0,
            client_order_id="client-old", intent="ORDER_INTENT_BUY_LONG"),
            received_at=T0 + timedelta(hours=2))

        self.assertEqual(
            self.ledger.fill_role_counts(since=T0 + timedelta(hours=1)),
            (0, 0),
        )

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

    def test_existing_observation_table_gains_retained_value_columns(self):
        shadow_columns = {
            "retained_value_ratio",
            "stressed_exit_value",
            "would_refuse_retained_60",
            "would_refuse_retained_70",
            "would_refuse_retained_80",
            "replacement_market_slug",
            "replacement_projected_reward",
            "replacement_expected_net",
            "replacement_reward_delta",
            "replacement_selection_age_seconds",
            "replacement_reason",
            "slot_would_go_dark",
        }
        self.ledger.close()
        legacy = sqlite3.connect(self.path)
        for column in shadow_columns:
            legacy.execute(
                f"ALTER TABLE execution_quote_observations DROP COLUMN {column}")
        legacy.commit()
        legacy.close()

        self.ledger = ExecutionLedger(self.path)
        columns = {
            row["name"] for row in self.ledger.connection.execute(
                "PRAGMA table_info(execution_quote_observations)")}
        self.assertTrue(shadow_columns.issubset(columns))

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

    def test_busted_trade_reverses_position_and_economics_exactly_once(self):
        fill = execution("fill-busted", "2", trade_id="trade-one")
        self.ledger.apply_execution(fill, received_at=T0)
        bust = TradeStateObservation(
            "trade-one", "market-one", "BUSTED",
            T0 + timedelta(seconds=10))

        self.assertTrue(self.ledger.apply_trade_state(
            bust, received_at=T0 + timedelta(seconds=11)))
        self.assertFalse(self.ledger.apply_trade_state(
            bust, received_at=T0 + timedelta(seconds=12)))

        row = self.ledger.query(
            "SELECT reversed,trade_state FROM execution_fills")[0]
        self.assertEqual((row["reversed"], row["trade_state"]),
                         (1, "TRADE_STATE_BUSTED"))
        self.assertEqual(self.ledger.position("market-one"), Decimal("0"))
        self.assertEqual(self.ledger.realized_pnl("market-one"), Decimal("0"))

    def test_newer_position_snapshot_prevents_double_bust_reversal(self):
        self.ledger.apply_execution(
            execution("fill-before-snapshot", "2", trade_id="trade-two"),
            received_at=T0)
        snapshot_at = T0 + timedelta(seconds=20)
        self.ledger.apply_position_snapshot(
            "post-bust-snapshot", [], as_of=snapshot_at,
            observed_at=snapshot_at, complete_account=True)

        self.ledger.apply_trade_state(TradeStateObservation(
            "trade-two", "market-one", "TRADE_STATE_BUSTED",
            T0 + timedelta(seconds=10)), received_at=snapshot_at)

        self.assertEqual(self.ledger.position("market-one"), Decimal("0"))
        self.assertEqual(
            self.ledger.query("SELECT reversed FROM execution_fills")[0][0], 1)

    def test_bust_before_fill_marks_late_fill_reversed_without_position(self):
        bust = TradeStateObservation(
            "trade-three", "market-one", "BUSTED", T0)
        self.ledger.apply_trade_state(bust, received_at=T0)

        self.ledger.apply_execution(
            execution("late-busted-fill", "2", trade_id="trade-three"),
            received_at=T0 + timedelta(seconds=1))

        row = self.ledger.query(
            "SELECT reversed,trade_state FROM execution_fills")[0]
        self.assertEqual((row["reversed"], row["trade_state"]),
                         (1, "TRADE_STATE_BUSTED"))
        self.assertEqual(self.ledger.position("market-one"), Decimal("0"))

    def test_late_trade_link_repairs_known_bust_immediately(self):
        original = execution("fill-linked-late", "2")
        self.ledger.apply_execution(original, received_at=T0)
        bust = TradeStateObservation(
            "trade-four", "market-one", "BUSTED",
            T0 + timedelta(seconds=10))
        self.ledger.apply_trade_state(bust, received_at=T0)
        self.assertEqual(self.ledger.position("market-one"), Decimal("2"))

        enriched = execution(
            "fill-linked-late", "2", trade_id="trade-four",
            trade_state="BUSTED")
        self.assertFalse(self.ledger.apply_execution(
            enriched, received_at=T0 + timedelta(seconds=11)))

        self.assertEqual(self.ledger.position("market-one"), Decimal("0"))
        self.assertEqual(
            self.ledger.query("SELECT reversed FROM execution_fills")[0][0], 1)
        self.assertFalse(self.ledger.apply_trade_state(
            bust, received_at=T0 + timedelta(seconds=12)))
        self.assertEqual(self.ledger.position("market-one"), Decimal("0"))

    def test_trade_market_identity_mismatch_fails_without_reversal(self):
        self.ledger.apply_execution(
            execution("fill-market-one", "2", trade_id="trade-mismatch"),
            received_at=T0)

        with self.assertRaisesRegex(LedgerConflict, "market identities"):
            self.ledger.apply_trade_state(TradeStateObservation(
                "trade-mismatch", "market-two", "BUSTED",
                T0 + timedelta(seconds=10)), received_at=T0)

        self.assertEqual(self.ledger.position("market-one"), Decimal("2"))
        self.assertEqual(
            self.ledger.query("SELECT reversed FROM execution_fills")[0][0], 0)

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

    def test_daily_score_is_additive_and_survives_reopen(self):
        first = self.ledger.add_score_seconds(
            "2026-08-24", "3600.25", observed_at=T0)
        second = self.ledger.add_score_seconds(
            "2026-08-24", Decimal("1799.75"),
            observed_at=T0 + timedelta(minutes=5))
        self.ledger.add_score_seconds(
            "2026-08-25", "12.5", observed_at=T0 + timedelta(days=1))

        self.assertEqual(first, Decimal("3600.25"))
        self.assertEqual(second, Decimal("5400.00"))
        self.assertEqual(
            self.ledger.score_seconds_for_day("2026-08-24"),
            Decimal("5400.00"))
        self.ledger.close()
        self.ledger = ExecutionLedger(self.path)
        self.assertEqual(
            self.ledger.score_seconds_for_day("2026-08-24"),
            Decimal("5400.00"))
        self.assertEqual(
            self.ledger.score_seconds_for_day("2026-08-25"),
            Decimal("12.5"))

    def test_daily_score_rejects_bad_day_negative_and_naive_time(self):
        with self.assertRaises(ValueError):
            self.ledger.score_seconds_for_day("not-a-day")
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            self.ledger.add_score_seconds(
                "2026-08-24", "-0.01", observed_at=T0)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.ledger.add_score_seconds(
                "2026-08-24", "1", observed_at=T0.replace(tzinfo=None))


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

    def test_an_existing_database_gains_the_status_leading_order_index(self):
        root = Path(tempfile.mkdtemp(prefix="order-index-old-"))
        path = root / "old.db"
        legacy = sqlite3.connect(path)
        legacy.execute(
            "CREATE TABLE execution_orders (client_order_id TEXT PRIMARY KEY,"
            "exchange_order_id TEXT UNIQUE, market_slug TEXT NOT NULL,"
            "side TEXT, price TEXT, original_qty TEXT,"
            "filled_qty TEXT NOT NULL, status TEXT NOT NULL,"
            "event_at TEXT NOT NULL, received_at TEXT NOT NULL,"
            "unknown_reason TEXT, reject_reason TEXT)")
        legacy.commit()
        legacy.close()

        led = ExecutionLedger(path)
        self.addCleanup(led.close)

        indexes = {row["name"] for row in led.connection.execute(
            "PRAGMA index_list(execution_orders)")}
        self.assertIn("idx_execution_orders_status", indexes)

    def test_an_existing_database_gains_the_market_fill_index(self):
        root = Path(tempfile.mkdtemp(prefix="fill-index-old-"))
        path = root / "old.db"
        legacy = sqlite3.connect(path)
        legacy.execute(
            "CREATE TABLE execution_fills (execution_id TEXT PRIMARY KEY,"
            "client_order_id TEXT, exchange_order_id TEXT,"
            "market_slug TEXT NOT NULL, side TEXT NOT NULL, price TEXT NOT NULL,"
            "qty TEXT NOT NULL, fee TEXT NOT NULL, liquidity TEXT,"
            "intent TEXT NOT NULL, event_at TEXT NOT NULL,"
            "received_at TEXT NOT NULL, outcome TEXT, trade_id TEXT,"
            "trade_state TEXT, reversed INTEGER NOT NULL DEFAULT 0,"
            "reversed_at TEXT)")
        legacy.commit()
        legacy.close()

        led = ExecutionLedger(path)
        self.addCleanup(led.close)

        indexes = {row["name"] for row in led.connection.execute(
            "PRAGMA index_list(execution_fills)")}
        self.assertIn("idx_execution_fills_market", indexes)


class PairSettlementTests(unittest.TestCase):
    """Stored prices are LONG-frame, and a matched pair settles at one dollar."""

    def ledger(self):
        root = Path(tempfile.mkdtemp(prefix="pairs-"))
        led = ExecutionLedger(root / "l.db")
        self.addCleanup(led.close)
        return led

    def fill(
            self, led, eid, side, intent, price, qty, fee="0", *,
            market="market-one", event_at=None):
        event_at = event_at or datetime(2026, 8, 15, tzinfo=UTC)
        led.apply_execution(Execution(
            execution_id=eid, market_slug=market, side=side,
            price=Decimal(price), qty=Decimal(qty), fee=Decimal(fee),
            event_at=event_at, intent=intent), received_at=event_at)

    def test_a_round_trip_realises_the_pair_settlement(self):
        """BUY_SHORT at 0.33 costs 0.67 in the opposite outcome.

        Paying 0.39 + (1 - 0.33) = 1.06 for a pair worth 1.00 is a
        six-cent loss per contract.
        """
        led = self.ledger()
        self.fill(led, "e1", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.39", "5")
        self.fill(led, "e2", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT",
                  "0.33", "5")

        # 5 x 0.39 + 5 x (1 - 0.33) = 5.30; five pairs settle at 5.00.
        self.assertAlmostEqual(
            float(led.realized_pnl("market-one")), -0.30, places=2)

    def test_live_asymmetric_prices_use_the_long_frame(self):
        led = self.ledger()
        self.fill(led, "e1", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.83", "5")
        self.fill(led, "e2", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT",
                  "0.85", "5")

        # 5 x 0.83 + 5 x (1 - 0.85) = 4.90; payout is 5.00.
        self.assertEqual(led.realized_pnl("market-one"), Decimal("0.10"))

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

    def test_an_unmatched_short_leg_realises_nothing(self):
        led = self.ledger()
        self.fill(led, "e1", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT",
                  "0.85", "5")

        self.assertEqual(led.realized_pnl("market-one"), Decimal("0"))

    def test_since_replays_prior_basis_and_attributes_the_close(self):
        led = self.ledger()
        before = datetime(2026, 8, 15, tzinfo=UTC)
        after = before + timedelta(hours=1)
        led.apply_execution(Execution(
            "open", "market-one", "ORDER_SIDE_BUY", Decimal("0.40"),
            Decimal("5"), Decimal("0"), before,
            intent="ORDER_INTENT_BUY_LONG"), received_at=before)
        led.apply_execution(Execution(
            "close", "market-one", "ORDER_SIDE_SELL", Decimal("0.50"),
            Decimal("5"), Decimal("0.05"), after,
            intent="ORDER_INTENT_BUY_SHORT"), received_at=after)

        self.assertEqual(
            led.realized_pnl("market-one", since=after), Decimal("0.45"))

    def test_realized_by_market_matches_individual_market_replays(self):
        led = self.ledger()
        self.fill(led, "one-long", "ORDER_SIDE_BUY",
                  "ORDER_INTENT_BUY_LONG", "0.40", "5")
        self.fill(led, "one-short", "ORDER_SIDE_SELL",
                  "ORDER_INTENT_BUY_SHORT", "0.50", "5", fee="0.05")
        self.fill(led, "two-long", "ORDER_SIDE_BUY",
                  "ORDER_INTENT_BUY_LONG", "0.55", "3",
                  market="market-two")
        self.fill(led, "two-short", "ORDER_SIDE_SELL",
                  "ORDER_INTENT_BUY_SHORT", "0.40", "3",
                  market="market-two")

        by_market = led.realized_by_market()

        self.assertEqual(by_market, {
            "market-one": led.realized_pnl("market-one"),
            "market-two": led.realized_pnl("market-two"),
        })

    def test_realized_by_market_is_empty_without_fill_history(self):
        led = self.ledger()

        self.assertEqual(led.realized_by_market(), {})

    def test_realized_by_market_replays_old_basis_for_since_cutoff(self):
        led = self.ledger()
        before = datetime(2026, 8, 15, tzinfo=UTC)
        after = before + timedelta(hours=1)
        self.fill(led, "open", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.40", "5", event_at=before)
        self.fill(led, "close", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT",
                  "0.50", "5", fee="0.05", event_at=after)

        self.assertEqual(
            led.realized_by_market(since=after)["market-one"],
            Decimal("0.45"))

    def test_realized_by_market_keeps_an_all_reversed_market_at_zero(self):
        led = self.ledger()
        self.fill(led, "busted", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.40", "5", market="market-reversed")
        with led.connection:
            led.connection.execute(
                "UPDATE execution_fills SET reversed=1 "
                "WHERE execution_id='busted'")

        self.assertEqual(
            led.realized_by_market()["market-reversed"], Decimal("0"))

    def test_realized_by_market_reads_the_fill_history_once(self):
        led = self.ledger()
        self.fill(led, "one", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.40", "5")
        self.fill(led, "two", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG",
                  "0.50", "5", market="market-two")
        statements = []
        led.connection.set_trace_callback(statements.append)
        self.addCleanup(led.connection.set_trace_callback, None)

        led.realized_by_market()

        fill_reads = [statement for statement in statements
                      if statement.lstrip().upper().startswith("SELECT")
                      and "FROM EXECUTION_FILLS" in statement.upper()]
        self.assertEqual(len(fill_reads), 1, fill_reads)


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

    def test_short_venue_cost_converts_to_the_strategy_price_frame(self):
        led = self.ledger()
        at = datetime(2026, 8, 15, 22, tzinfo=UTC)

        self.snapshot(led, "-10", "7.07", at)

        basis = led.inventory_basis("market-one")
        self.assertEqual(basis.net_qty, Decimal("-10"))
        self.assertEqual(basis.average_entry_price, Decimal("0.293"))

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


class RiskDayBaselineTests(unittest.TestCase):
    def ledger(self):
        root = Path(tempfile.mkdtemp(prefix="risk-day-"))
        led = ExecutionLedger(root / "l.db")
        self.addCleanup(led.close)
        return led

    def test_first_et_day_preserves_the_baseline_enforced_at_cutover(self):
        """Changing calendars must not hand the account a fresh loss budget."""

        led = self.ledger()
        with led.connection:
            led.connection.execute(
                "INSERT INTO execution_day_baselines"
                "(utc_date,opening_equity,opening_realized,opening_cash,created_at) "
                "VALUES (?,?,?,?,?)",
                ("2026-08-25", "478.6998", "-207.3918", "478.6998",
                 "2026-08-25T00:00:02+00:00"))

        baseline = led.day_baseline(
            "2026-08-25",
            opening_equity=Decimal("430"),
            opening_realized=Decimal("-240"),
            opening_cash=Decimal("430"),
            created_at=datetime(2026, 8, 25, 15, tzinfo=UTC),
            legacy_utc_date="2026-08-25")

        self.assertEqual(
            baseline,
            (Decimal("478.6998"), Decimal("-207.3918"),
             Decimal("478.6998")))
        row = led.query(
            "SELECT risk_date,source FROM execution_risk_day_baselines")[0]
        self.assertEqual(dict(row), {
            "risk_date": "2026-08-25", "source": "legacy_utc_cutover"})

    def test_legacy_rows_are_ignored_after_the_one_time_cutover(self):
        led = self.ledger()
        led.day_baseline(
            "2026-08-25",
            opening_equity=Decimal("450"),
            opening_realized=Decimal("-200"),
            opening_cash=Decimal("450"),
            created_at=datetime(2026, 8, 25, 15, tzinfo=UTC))
        with led.connection:
            led.connection.execute(
                "INSERT INTO execution_day_baselines"
                "(utc_date,opening_equity,opening_realized,opening_cash,created_at) "
                "VALUES (?,?,?,?,?)",
                ("2026-08-26", "999", "999", "999",
                 "2026-08-26T00:00:00+00:00"))

        baseline = led.day_baseline(
            "2026-08-26",
            opening_equity=Decimal("440"),
            opening_realized=Decimal("-210"),
            opening_cash=Decimal("440"),
            created_at=datetime(2026, 8, 26, 4, tzinfo=UTC),
            legacy_utc_date="2026-08-26")

        self.assertEqual(
            baseline, (Decimal("440"), Decimal("-210"), Decimal("440")))
        source = led.query(
            "SELECT source FROM execution_risk_day_baselines "
            "WHERE risk_date='2026-08-26'")[0]["source"]
        self.assertEqual(source, "fresh_et_day")

    def test_stopped_bot_crossing_utc_midnight_keeps_latest_prior_baseline(self):
        """Downtime in the UTC/ET gap must not grant a fresh risk budget."""

        led = self.ledger()
        with led.connection:
            led.connection.execute(
                "INSERT INTO execution_day_baselines"
                "(utc_date,opening_equity,opening_realized,opening_cash,created_at) "
                "VALUES (?,?,?,?,?)",
                ("2026-08-25", "478.6998", "-207.3918", "478.6998",
                 "2026-08-25T00:00:02+00:00"))

        # The bot stopped on August 25 and therefore never created the legacy
        # August 26 row. At 00:30Z it is still the August 25 ET risk day.
        baseline = led.day_baseline(
            "2026-08-25",
            opening_equity=Decimal("430"),
            opening_realized=Decimal("-240"),
            opening_cash=Decimal("430"),
            created_at=datetime(2026, 8, 26, 0, 30, tzinfo=UTC),
            legacy_utc_date="2026-08-26")

        self.assertEqual(
            baseline,
            (Decimal("478.6998"), Decimal("-207.3918"),
             Decimal("478.6998")))
        source = led.query(
            "SELECT source FROM execution_risk_day_baselines")[0]["source"]
        self.assertEqual(source, "legacy_utc_cutover")

    def test_entry_order_cap_ratchets_for_day_and_survives_restart(self):
        led = self.ledger()
        first = datetime(2026, 8, 25, 15, tzinfo=UTC)

        self.assertEqual(led.ratchet_entry_order_cap(
            "2026-08-25", candidate_cap=40, full_cap=64,
            loss_fraction=Decimal("0.50"), observed_at=first), 40)
        self.assertEqual(led.ratchet_entry_order_cap(
            "2026-08-25", candidate_cap=64, full_cap=64,
            loss_fraction=Decimal("0.20"),
            observed_at=first + timedelta(minutes=1)), 40)
        self.assertEqual(led.ratchet_entry_order_cap(
            "2026-08-25", candidate_cap=20, full_cap=64,
            loss_fraction=Decimal("0.80"),
            observed_at=first + timedelta(minutes=2)), 20)

        path = led.path
        led.close()
        restarted = ExecutionLedger(path)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.ratchet_entry_order_cap(
            "2026-08-25", candidate_cap=64, full_cap=64,
            loss_fraction=Decimal("0.10"),
            observed_at=first + timedelta(minutes=3)), 20)

    def test_new_et_day_restores_full_entry_order_capacity(self):
        led = self.ledger()
        first = datetime(2026, 8, 25, 15, tzinfo=UTC)
        led.ratchet_entry_order_cap(
            "2026-08-25", candidate_cap=0, full_cap=64,
            loss_fraction=Decimal("0.95"), observed_at=first)

        self.assertEqual(led.ratchet_entry_order_cap(
            "2026-08-26", candidate_cap=64, full_cap=64,
            loss_fraction=Decimal("0"),
            observed_at=first + timedelta(days=1)), 64)
