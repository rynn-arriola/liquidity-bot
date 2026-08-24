from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.execution_ledger import ExecutionLedger, OrderState, QuoteObservation
from liquidity_bot.models import BookLevel, BookSnapshot, TradePrint
from liquidity_bot.queue_measurement import QueueMeasurementTracker


UTC = timezone.utc
T0 = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def observation(**changes) -> QuoteObservation:
    values = dict(
        market_slug="market-one", side="bid", observed_at=T0,
        book_observed_at=T0, game_start=T0 + timedelta(hours=5),
        quote_price=Decimal("0.50"), quantity=Decimal("10"),
        decision_reason="reward_positive_candidate",
        best_bid=.51, best_bid_size=100, best_offer=.53,
        best_offer_size=120, spread=.02, bid_depth=500, offer_depth=600,
        model_queue_ahead=100, observed_queue_ahead=300,
        depth_better_than_quote=100, depth_at_quote=200,
        depth_through_quote=300, touch_changes_30s=2,
        touch_changes_120s=7, seconds_since_touch_change=4,
        seconds_to_event=18000, program_id="program-one",
        reward_period="day_of", reward_pool=35, discount_factor=.5,
        target_size=5000, cutoff_price=.49, depth_to_reward_cutoff=5100,
        ticks_from_best=1, order_score=5, total_score=4000,
        snapshot_share=.00125, projected_reward=.002,
        maximum_remaining_reward=1.44, fill_probability=.02,
        fill_risk_driver="queue:100_500", stressed_exit_complete=True,
        stressed_exit_closed_qty=10, stressed_exit_average_price=.49,
        stressed_exit_worst_price=.48, stressed_exit_fee=.01,
        stressed_exit_pnl=-.21, expected_net=-.004,
        reward_cost_safety_multiple=2,
    )
    values.update(changes)
    return QuoteObservation(**values)


def book(*, better=100, same=200, at=T0 + timedelta(seconds=1)) -> BookSnapshot:
    return BookSnapshot(
        "market-one",
        bids=(BookLevel(.51, better), BookLevel(.50, same), BookLevel(.49, 500)),
        offers=(BookLevel(.53, 500),),
        transact_time=at, received_at=at,
    )


def trade(*, price=.50, qty=10, maker="ORDER_SIDE_BUY",
          at=T0 + timedelta(seconds=2)) -> TradePrint:
    return TradePrint(
        "market-one", price, qty, maker, at, request_id=f"trade-{at.timestamp()}")


class QueueMeasurementTests(unittest.TestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp(prefix="queue-measurement-")) / "ledger.db"
        self.ledger = ExecutionLedger(path)
        self.ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one",
            {"price": "0.50", "quantity": 10}, created_at=T0,
            quote_observation=observation(), quote_observation_required=True)
        self.ledger.transition_intent(
            "intent-one", "SUBMITTING", updated_at=T0)
        self.ledger.transition_intent(
            "intent-one", "ACKNOWLEDGED", updated_at=T0,
            exchange_order_id="exchange-one")
        self.tracker = QueueMeasurementTracker(
            self.ledger, collection_started_at=T0)
        self.tracker.sync_orders(observed_at=T0)

    def tearDown(self):
        self.ledger.close()

    def row(self):
        return self.ledger.query(
            "SELECT * FROM execution_queue_measurements "
            "WHERE client_order_id='client-one'")[0]

    def apply_order(self, *, filled, status, at):
        self.ledger.apply_order_event(
            "test", f"order-{status}-{at.isoformat()}",
            OrderState(
                client_order_id="client-one", exchange_order_id="exchange-one",
                market_slug="market-one", side="BUY", price=Decimal("0.50"),
                original_qty=Decimal("10"), filled_qty=Decimal(str(filled)),
                status=status, event_at=at),
            received_at=at)

    def test_starts_from_exact_decision_time_queue_components(self):
        row = self.row()
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(row["initial_queue_ahead"], 300)
        self.assertEqual(row["initial_better_depth"], 100)
        self.assertEqual(row["initial_same_price_depth"], 200)
        events = self.ledger.query(
            "SELECT event_kind FROM execution_queue_measurement_events")
        self.assertEqual([event["event_kind"] for event in events], ["START"])

    def test_book_cancellation_widens_bounds_and_new_same_price_size_stays_behind(self):
        at = T0 + timedelta(seconds=1)
        self.tracker.on_book(book(better=100, same=200, at=at), observed_at=at)
        changed = at + timedelta(seconds=1)
        self.tracker.on_book(
            book(better=80, same=150, at=changed), observed_at=changed)
        state = self.tracker._active["client-one"]
        self.assertEqual((state.lower, state.upper), (80, 230))
        self.assertEqual(state.ambiguous_removed, 50)

        later = changed + timedelta(seconds=1)
        self.tracker.on_book(book(better=80, same=300, at=later), observed_at=later)
        state = self.tracker._active["client-one"]
        # The additional 150 arrived after us. It cannot move us backward.
        self.assertEqual((state.lower, state.upper), (80, 230))
        self.assertEqual(state.same_added, 150)

    def test_first_post_ack_book_captures_same_price_growth_during_place_latency(self):
        at = T0 + timedelta(seconds=1)
        # Decision-time same depth was 200. Fifty contracts arrived before
        # ACK and our own ten may also be displayed, so 260 is the safe upper.
        self.tracker.on_book(book(better=100, same=260, at=at), observed_at=at)
        state = self.tracker._active["client-one"]
        self.assertEqual(state.baseline_same_depth, 260)
        self.assertEqual((state.lower, state.upper), (100, 360))

    def test_book_received_while_place_was_in_flight_cannot_rewrite_queue(self):
        state = self.tracker._active["client-one"]
        state.started_at = T0 + timedelta(seconds=5)
        stale = T0 + timedelta(seconds=2)
        self.tracker.on_book(book(better=0, same=0, at=stale), observed_at=stale)
        self.assertEqual((state.lower, state.upper), (300, 300))
        self.assertEqual(state.book_events, 0)

    def test_trades_reduce_only_the_consumed_side_and_price_path(self):
        baseline = T0 + timedelta(seconds=1)
        self.tracker.on_book(
            book(better=100, same=200, at=baseline), observed_at=baseline)
        at = T0 + timedelta(seconds=2)
        self.tracker.on_trade(trade(price=.50, qty=60, at=at), observed_at=at)
        state = self.tracker._active["client-one"]
        # Trade is a definite label, but bounds wait for a full book because
        # the independently ordered book may already reflect this print.
        self.assertEqual((state.lower, state.upper), (100, 300))

        self.tracker.on_trade(
            trade(price=.51, qty=30, at=at + timedelta(seconds=1)),
            observed_at=at + timedelta(seconds=1))
        state = self.tracker._active["client-one"]
        # Better-price trades wait for the next full-book boundary so a trade
        # already reflected by that independently ordered socket is not
        # subtracted twice.
        self.assertEqual((state.lower, state.upper), (100, 300))
        refreshed = at + timedelta(seconds=1, milliseconds=1)
        self.tracker.on_book(
            book(better=70, same=140, at=refreshed), observed_at=refreshed)
        self.assertEqual(
            (self.tracker._active["client-one"].lower,
             self.tracker._active["client-one"].upper),
            (70, 210))

        # Offer-side consumption cannot advance a bid.
        self.tracker.on_trade(
            trade(price=.50, qty=500, maker="ORDER_SIDE_SELL",
                  at=at + timedelta(seconds=2)),
            observed_at=at + timedelta(seconds=2))
        self.assertEqual(self.tracker._active["client-one"].upper, 210)

        # A print through the level proves the displayed queue path depleted,
        # but still does not claim our order filled.
        self.tracker.on_trade(
            trade(price=.49, qty=5, at=at + timedelta(seconds=3)),
            observed_at=at + timedelta(seconds=3))
        state = self.tracker._active["client-one"]
        self.assertEqual((state.lower, state.upper), (0, 0))
        self.assertEqual(self.row()["status"], "ACTIVE")

    def test_book_and_trade_socket_reordering_cannot_double_count_consumption(self):
        # The book already reflects a 50-lot at-price trade, but reaches the
        # runner before the public trade callback from the separate socket.
        book_at = T0 + timedelta(seconds=2)
        self.tracker.on_book(
            book(better=100, same=150, at=book_at), observed_at=book_at)
        trade_at = book_at + timedelta(milliseconds=1)
        self.tracker.on_trade(
            trade(price=.50, qty=50, at=book_at - timedelta(milliseconds=1)),
            observed_at=trade_at)
        state = self.tracker._active["client-one"]
        self.assertEqual(state.same_upper, 150)
        self.assertEqual(state.upper, 250)

        # The same protection applies to a better-price trade: the full book
        # is the boundary and the late callback cannot subtract it again.
        later_book = T0 + timedelta(seconds=3)
        self.tracker.on_book(
            book(better=70, same=150, at=later_book), observed_at=later_book)
        self.tracker.on_trade(
            trade(price=.51, qty=30,
                  at=later_book - timedelta(milliseconds=1)),
            observed_at=later_book + timedelta(milliseconds=1))
        self.assertEqual(self.tracker._active["client-one"].better_depth, 70)

    def test_private_fill_is_the_only_fill_proof_and_closes_terminal_order(self):
        partial_at = T0 + timedelta(seconds=3)
        self.apply_order(filled=2, status="PARTIAL", at=partial_at)
        self.tracker.sync_orders(observed_at=partial_at)
        state = self.tracker._active["client-one"]
        self.assertEqual((state.lower, state.upper), (0, 0))
        self.assertEqual(self.row()["last_filled_qty"], 2)

        filled_at = partial_at + timedelta(seconds=1)
        self.apply_order(filled=10, status="FILLED", at=filled_at)
        self.tracker.sync_orders(observed_at=filled_at)
        row = self.row()
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["terminal_status"], "FILLED")
        self.assertEqual(self.tracker.active_count, 0)
        kinds = [row["event_kind"] for row in self.ledger.query(
            "SELECT event_kind FROM execution_queue_measurement_events "
            "ORDER BY id")]
        self.assertEqual(kinds[-3:], ["OWN_FILL", "OWN_FILL", "CLOSE"])

    def test_fast_terminal_fill_is_not_lost_before_tracker_first_sync(self):
        self.ledger.prepare_intent(
            "intent-fast", "PLACE", "market-one", "client-fast",
            {"price": "0.50", "quantity": 10}, created_at=T0,
            quote_observation=observation(), quote_observation_required=True)
        self.ledger.transition_intent("intent-fast", "SUBMITTING", updated_at=T0)
        self.ledger.transition_intent(
            "intent-fast", "ACKNOWLEDGED", updated_at=T0,
            exchange_order_id="exchange-fast")
        at = T0 + timedelta(seconds=1)
        self.ledger.apply_order_event(
            "test", "fast-fill",
            OrderState(
                "client-fast", "market-one", "BUY", Decimal("0.50"),
                Decimal("10"), Decimal("10"), "FILLED", at,
                exchange_order_id="exchange-fast"),
            received_at=at)
        self.tracker.sync_orders(observed_at=at)
        row = self.ledger.query(
            "SELECT status,terminal_status,last_filled_qty "
            "FROM execution_queue_measurements WHERE client_order_id='client-fast'")[0]
        self.assertEqual(tuple(row), ("CLOSED", "FILLED", 10))

    def test_historical_terminal_orders_are_not_backfilled_on_migration(self):
        # Move the collection boundary forward, simulating first deployment
        # against a ledger containing older observations.
        other = QueueMeasurementTracker(
            self.ledger, collection_started_at=T0 + timedelta(days=1))
        other.sync_orders(observed_at=T0 + timedelta(days=1))
        self.assertEqual(len(self.ledger.query(
            "SELECT * FROM execution_queue_measurements")), 1)

    def test_event_history_is_banded_not_one_row_per_book(self):
        for index in range(100):
            at = T0 + timedelta(seconds=index + 1)
            # Small changes remain in gt_10x and update only the summary.
            self.tracker.on_book(
                book(better=100, same=200 - (index % 2), at=at),
                observed_at=at)
        events = self.ledger.query(
            "SELECT event_kind FROM execution_queue_measurement_events")
        self.assertEqual(
            [event["event_kind"] for event in events],
            ["START", "QUEUE_BASELINE"])

    def test_almost_front_trigger_is_shadow_only_after_minimum_dwell(self):
        resting_at = T0 + timedelta(seconds=1)
        self.apply_order(filled=0, status="OPEN", at=resting_at)
        self.tracker.sync_orders(observed_at=resting_at)
        early = T0 + timedelta(seconds=30)
        self.tracker.on_book(book(better=0, same=10, at=early), observed_at=early)
        self.assertIsNone(self.row()["would_reset_at"])
        self.assertEqual(self.row()["status"], "ACTIVE")

        due = T0 + timedelta(seconds=61)
        self.tracker.on_book(book(better=0, same=10, at=due), observed_at=due)
        row = self.row()
        self.assertEqual(row["would_reset_at"], due.isoformat())
        self.assertEqual(row["would_reset_queue_upper_bound"], 10)
        # The tracker has no cancellation path: the order remains active.
        self.assertEqual(row["status"], "ACTIVE")
        kinds = [row["event_kind"] for row in self.ledger.query(
            "SELECT event_kind FROM execution_queue_measurement_events")]
        self.assertEqual(kinds.count("WOULD_QUEUE_RESET"), 1)

    def test_coverage_gap_blocks_a_false_shadow_signal(self):
        resting_at = T0 + timedelta(seconds=1)
        self.apply_order(filled=0, status="OPEN", at=resting_at)
        self.tracker.sync_orders(observed_at=resting_at)
        gap_at = T0 + timedelta(seconds=10)
        self.tracker.mark_coverage_gap(
            observed_at=gap_at, dropped=2, reason="test gap")
        due = T0 + timedelta(seconds=61)
        self.tracker.on_book(book(better=0, same=0, at=due), observed_at=due)
        row = self.row()
        self.assertEqual(row["coverage_gaps"], 2)
        self.assertIsNone(row["would_reset_at"])

    def test_pending_cancel_suppresses_shadow_signal_for_order_already_leaving(self):
        resting_at = T0 + timedelta(seconds=1)
        self.apply_order(filled=0, status="OPEN", at=resting_at)
        self.tracker.sync_orders(observed_at=resting_at)
        baseline = T0 + timedelta(seconds=30)
        self.tracker.on_book(
            book(better=0, same=10, at=baseline), observed_at=baseline)
        self.ledger.prepare_intent(
            "cancel-one", "CANCEL", "market-one", "client-one",
            {"orderId": "exchange-one"}, created_at=T0 + timedelta(seconds=40))
        self.ledger.transition_intent(
            "cancel-one", "SUBMITTING", updated_at=T0 + timedelta(seconds=40))
        self.ledger.transition_intent(
            "cancel-one", "ACKNOWLEDGED", updated_at=T0 + timedelta(seconds=40),
            exchange_order_id="exchange-one")
        self.tracker.sync_orders(observed_at=T0 + timedelta(seconds=40))

        due = T0 + timedelta(seconds=61)
        self.tracker.on_book(book(better=0, same=10, at=due), observed_at=due)
        self.assertIsNone(self.row()["would_reset_at"])

    def test_stream_session_persists_coverage_health(self):
        session_id = self.tracker.start_stream_session(
            observed_at=T0, slugs=("market-one", "market-two"))
        self.tracker.update_stream_session(
            session_id, observed_at=T0 + timedelta(minutes=5),
            trades_received=12, dropped_trades=0, decode_failures=0,
            last_trade_at=T0 + timedelta(minutes=4))
        self.tracker.finish_stream_session(
            session_id, observed_at=T0 + timedelta(minutes=6),
            status="REBOUND", trades_received=14, dropped_trades=0,
            decode_failures=0, last_trade_at=T0 + timedelta(minutes=6))
        row = self.ledger.query(
            "SELECT * FROM execution_queue_stream_sessions")[0]
        self.assertEqual(row["scope_size"], 2)
        self.assertEqual(row["status"], "REBOUND")
        self.assertEqual(row["trades_received"], 14)


if __name__ == "__main__":
    unittest.main()
