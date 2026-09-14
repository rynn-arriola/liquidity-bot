"""Resting reducers must not repeatedly improve against their own touch."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest import TestCase

from test_reconciler import T0, book, frame, policy
from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler, WorkingOrder


class ReducerSelfChaseTests(TestCase):
    def test_long_and_short_keep_their_new_touch_across_repeated_snapshots(self):
        for position, side, entry, bid, offer in (
                ("10", "offer", .50, .48, .52),
                ("-10", "bid", .50, .48, .52)):
            with self.subTest(side=side):
                planner = DesiredStateReconciler(policy(age=900))
                first = planner.plan(frame(position=position, opened=T0, entry=entry,
                                           snapshot=book(bid=bid, offer=offer)))[0]
                self.assertEqual(first.kind, ActionKind.PLACE_POST_ONLY)
                resting = WorkingOrder("owned", side, first.price, Decimal(10))
                for second in range(1, 20):
                    now = T0 + timedelta(seconds=second)
                    visible = book(now, bid=first.price if side == "bid" else bid,
                                   offer=first.price if side == "offer" else offer)
                    self.assertEqual(planner.plan(frame(now=now, position=position,
                        opened=T0, entry=entry, snapshot=visible, orders=(resting,))), [])

    def test_competitor_better_touch_still_reprices_for_both_sides(self):
        for position, side, price, bid, offer in (
                ("10", "offer", .53, .48, .52),
                ("-10", "bid", .47, .48, .52)):
            with self.subTest(side=side):
                actions = DesiredStateReconciler(policy(age=900)).plan(frame(
                    position=position, opened=T0, entry=.50,
                    snapshot=book(bid=bid, offer=offer),
                    orders=(WorkingOrder("owned", side, price, Decimal(10)),)))
                self.assertEqual(actions[0].kind, ActionKind.CANCEL)
                self.assertEqual(actions[0].order_id, "owned")

    def test_partial_fill_keeps_exact_residual_but_cancels_wrong_size_and_duplicates(self):
        planner = DesiredStateReconciler(policy(age=900))
        exact = WorkingOrder("exact", "offer", .51, Decimal("6.85"))
        base = frame(position="6.85", opened=T0, entry=.50, orders=(exact,))
        self.assertEqual(planner.plan(base), [])
        wrong = replace(exact, order_id="wrong", remaining_qty=Decimal(10))
        duplicate = replace(exact, order_id="duplicate")
        actions = planner.plan(replace(base, working_orders=(exact, wrong, duplicate)))
        self.assertEqual({a.order_id for a in actions}, {"wrong", "duplicate"})

    def test_builder_and_deadline_still_cancel_even_with_matching_reducer(self):
        reducer = WorkingOrder("exit", "offer", .51, Decimal(10))
        builder = WorkingOrder("entry", "bid", .49, Decimal(10))
        planner = DesiredStateReconciler(policy(age=900))
        base = frame(position="10", opened=T0, entry=.50, orders=(reducer, builder))
        self.assertEqual([a.order_id for a in planner.plan(base)], ["entry"])
        now = T0 + timedelta(seconds=900)
        actions = planner.plan(replace(base, now=now, book=book(now), book_observed_at=now,
                                       working_orders=(reducer,)))
        self.assertEqual(actions[0].kind, ActionKind.CANCEL)
        self.assertEqual(actions[0].order_id, "exit")

    def test_already_improved_duplicate_keeps_existing_preference(self):
        at_touch = WorkingOrder("touch", "offer", .52, Decimal(10))
        improved = WorkingOrder("improved", "offer", .51, Decimal(10))
        actions = DesiredStateReconciler(policy(age=900)).plan(frame(
            position="10", opened=T0, entry=.50, snapshot=book(bid=.48, offer=.52),
            orders=(at_touch, improved)))
        self.assertEqual([a.order_id for a in actions], ["touch"])

    def test_unknown_state_and_stale_book_do_not_gain_authority(self):
        reducer = WorkingOrder("exit", "offer", .51, Decimal(10))
        base = frame(position="10", opened=T0, entry=.50, orders=(reducer,))
        for candidate in (replace(base, state_known=False),
                          replace(base, now=T0 + timedelta(seconds=10))):
            actions = DesiredStateReconciler(policy(age=900)).plan(candidate)
            self.assertTrue(actions)
            self.assertNotIn(ActionKind.PLACE_POST_ONLY, {a.kind for a in actions})
