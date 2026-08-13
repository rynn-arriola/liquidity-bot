from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from liquidity_bot.models import BookLevel, BookSnapshot, MarketMetadata
from liquidity_bot.reconciler import (
    ActionKind,
    DesiredStateReconciler,
    MarketFrame,
    ReconcilerPolicy,
    WorkingOrder,
)


UTC = timezone.utc
T0 = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)


def book(at=T0, *, bid=0.49, offer=0.51, depth=100) -> BookSnapshot:
    return BookSnapshot(
        "market-one",
        (BookLevel(bid, depth), BookLevel(round(bid - .01, 2), depth)),
        (BookLevel(offer, depth), BookLevel(round(offer + .01, 2), depth)),
        "MARKET_STATE_OPEN", at)


def market() -> MarketMetadata:
    return MarketMetadata(
        "market-one", T0 + timedelta(hours=5), .01, True, False,
        "MARKET_STATE_OPEN", "moneyline")


def policy(age=15, **overrides) -> ReconcilerPolicy:
    values = dict(
        quote_size=Decimal("2"), max_inventory_age_seconds=age,
        max_market_worst_loss=5, max_portfolio_worst_loss=15,
        max_exit_loss=5, exit_depth_haircut=.5)
    values.update(overrides)
    return ReconcilerPolicy(**values)


def frame(*, now=T0, position="0", orders=(), opened=None, entry=None,
          state_known=True, snapshot=None, portfolio_loss=0) -> MarketFrame:
    snapshot = snapshot or book(now)
    return MarketFrame(
        market(), now, snapshot, now, Decimal(position), tuple(orders),
        state_known, frozenset({"bid", "offer"}), {"bid": .01, "offer": .01},
        portfolio_loss, opened, entry)


class ReconcilerTests(unittest.TestCase):
    def test_flat_positive_candidate_places_two_small_post_only_quotes(self):
        actions = DesiredStateReconciler(policy()).plan(frame())
        self.assertEqual({action.side for action in actions}, {"bid", "offer"})
        self.assertTrue(all(action.kind == ActionKind.PLACE_POST_ONLY
                            for action in actions))

    def test_unknown_state_fails_closed_for_new_risk(self):
        actions = DesiredStateReconciler(policy()).plan(frame(state_known=False))
        self.assertEqual([action.kind for action in actions], [ActionKind.CRITICAL])

    def test_first_fill_cancels_building_side_and_keeps_correct_reducer(self):
        orders = (
            WorkingOrder("bid-one", "bid", .49, Decimal("2")),
            WorkingOrder("offer-one", "offer", .51, Decimal("2")),
        )
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", orders=orders,
            opened=T0, entry=.49))
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].kind, actions[0].order_id),
                         (ActionKind.CANCEL, "bid-one"))

    def test_soft_exit_quotes_active_touch_even_below_entry(self):
        falling = book(T0 + timedelta(seconds=1), bid=.39, offer=.41)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", opened=T0,
            entry=.50, snapshot=falling))
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "offer")
        self.assertEqual(actions[0].price, .41)

    def test_timeout_confirms_cancel_before_ioc(self):
        reconciler = DesiredStateReconciler(policy())
        active = WorkingOrder("exit", "offer", .51, Decimal("2"))
        first = reconciler.plan(frame(
            now=T0 + timedelta(seconds=15), position="2", orders=(active,),
            opened=T0, entry=.49))
        self.assertEqual([item.kind for item in first], [ActionKind.CANCEL])
        second = reconciler.plan(frame(
            now=T0 + timedelta(seconds=16), position="2", orders=(),
            opened=T0, entry=.49))
        self.assertEqual([item.kind for item in second], [ActionKind.PLACE_IOC])
        self.assertEqual(second[0].price, .49)

    def test_cancel_stuck_past_grace_has_explicit_critical_failure(self):
        reconciler = DesiredStateReconciler(policy())
        active = WorkingOrder("exit", "offer", .51, Decimal("2"))
        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="2", orders=(active,),
            opened=T0, entry=.49))
        self.assertEqual([item.kind for item in actions], [ActionKind.CRITICAL])
        self.assertEqual(
            actions[0].reason, "hard_exit_blocked_cancel_unconfirmed")

    def test_position_remaining_after_ioc_is_critical(self):
        reconciler = DesiredStateReconciler(policy())
        reconciler.plan(frame(
            now=T0 + timedelta(seconds=16), position="2", orders=(),
            opened=T0, entry=.49))
        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=17), position="2", orders=(),
            opened=T0, entry=.49))
        self.assertEqual(actions[0].reason, "hard_exit_sent_but_inventory_remains")

    def test_terminal_hard_exit_confirmation_allows_exact_retry(self):
        reconciler = DesiredStateReconciler(policy())
        pending = frame(
            now=T0 + timedelta(seconds=16), position="2", orders=(),
            opened=T0, entry=.49)
        first = reconciler.plan(pending)
        self.assertEqual(first[0].kind, ActionKind.PLACE_IOC)
        reconciler.confirm_hard_exit_terminal("market-one")
        retry = reconciler.plan(pending)
        self.assertEqual(retry[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(retry[0].qty, Decimal("2"))

    def test_hard_exit_ioc_attempts_are_bounded(self):
        reconciler = DesiredStateReconciler(policy(max_hard_exit_attempts=3))
        for offset in range(3):
            actions = reconciler.plan(frame(
                now=T0 + timedelta(seconds=16 + offset), position="2",
                orders=(), opened=T0, entry=.49))
            self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)
            reconciler.confirm_hard_exit_terminal("market-one")

        terminal = reconciler.plan(frame(
            now=T0 + timedelta(seconds=20), position="2", orders=(),
            opened=T0, entry=.49))

        self.assertEqual(terminal[0].kind, ActionKind.CRITICAL)
        self.assertEqual(
            terminal[0].reason, "hard_exit_attempt_limit_exceeded")

    def test_hard_exit_retry_window_is_bounded(self):
        reconciler = DesiredStateReconciler(policy(
            max_hard_exit_attempts=5, max_hard_exit_retry_seconds=5))
        first = reconciler.plan(frame(
            now=T0 + timedelta(seconds=16), position="2", orders=(),
            opened=T0, entry=.49))
        self.assertEqual(first[0].kind, ActionKind.PLACE_IOC)
        reconciler.confirm_hard_exit_terminal("market-one")

        terminal = reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="2", orders=(),
            opened=T0, entry=.49))

        self.assertEqual(
            terminal[0].reason, "hard_exit_retry_window_exceeded")

    def test_pending_hard_exit_confirmation_has_a_deadline(self):
        reconciler = DesiredStateReconciler(policy(
            hard_exit_confirmation_timeout_seconds=10))
        reconciler.plan(frame(
            now=T0 + timedelta(seconds=16), position="2", orders=(),
            opened=T0, entry=.49))

        self.assertFalse(reconciler.hard_exit_confirmation_expired(
            "market-one", now=T0 + timedelta(seconds=25)))
        self.assertTrue(reconciler.hard_exit_confirmation_expired(
            "market-one", now=T0 + timedelta(seconds=26)))

    def test_portfolio_worst_loss_breaker_blocks_quotes(self):
        actions = DesiredStateReconciler(policy()).plan(
            frame(portfolio_loss=14.5))
        self.assertEqual([item.kind for item in actions],
                         [ActionKind.HALT_NEW_RISK])

    def test_duplicate_flat_order_on_one_side_is_canceled(self):
        orders = (
            WorkingOrder("bid-one", "bid", .49, Decimal("2")),
            WorkingOrder("bid-two", "bid", .49, Decimal("2")),
        )
        actions = DesiredStateReconciler(policy()).plan(frame(orders=orders))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].order_id, "bid-two")

    def test_duplicate_reducing_order_is_canceled_to_prevent_overclose(self):
        orders = (
            WorkingOrder("exit-one", "offer", .51, Decimal("2")),
            WorkingOrder("exit-two", "offer", .51, Decimal("2")),
        )
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", orders=orders,
            opened=T0, entry=.49))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].order_id, "exit-two")


if __name__ == "__main__":
    unittest.main()
