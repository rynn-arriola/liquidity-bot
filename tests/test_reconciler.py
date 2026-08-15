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
        "MARKET_STATE_OPEN", "moneyline",
        sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE")


def policy(age=15, **overrides) -> ReconcilerPolicy:
    values = dict(
        quote_size=Decimal("2"), max_inventory_age_seconds=age,
        max_market_worst_loss=5, max_portfolio_worst_loss=15,
        max_exit_loss=5, exit_depth_haircut=.5)
    values.update(overrides)
    return ReconcilerPolicy(**values)


def frame(*, now=T0, position="0", orders=(), opened=None, entry=None,
          state_known=True, snapshot=None, portfolio_loss=0,
          eligible=frozenset({"bid", "offer"})) -> MarketFrame:
    snapshot = snapshot or book(now)
    return MarketFrame(
        market(), now, snapshot, now, Decimal(position), tuple(orders),
        state_known, eligible, {"bid": .01, "offer": .01},
        portfolio_loss, opened, entry)


class ReconcilerTests(unittest.TestCase):
    def test_flat_positive_candidate_places_two_small_post_only_quotes(self):
        actions = DesiredStateReconciler(policy()).plan(frame())
        self.assertEqual({action.side for action in actions}, {"bid", "offer"})
        self.assertTrue(all(action.kind == ActionKind.PLACE_POST_ONLY
                            for action in actions))

    def test_kickoff_forces_the_exit_before_the_inventory_clock(self):
        # maker-lab disabled its forced exit because holding beat crossing by
        # ~3.13c/contract, and the residue then settled for -$88.20. Waiting
        # for a free passive exit is right; settling is not. Kickoff overrides
        # the inventory clock even when the position is seconds old.
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300))
        just_opened = T0 + timedelta(hours=5) - timedelta(seconds=200)

        actions = reconciler.plan(frame(
            now=just_opened, position="2", orders=(),
            opened=just_opened - timedelta(seconds=5), entry=.49))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(actions[0].reason, "hard_inventory_timeout")

    def test_young_inventory_far_from_kickoff_still_works_passively(self):
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300))
        early = T0 + timedelta(seconds=30)

        actions = reconciler.plan(frame(
            now=early, position="2", orders=(),
            opened=T0, entry=.49))

        # Well inside the 900s window and hours from kickoff: the reducing
        # quote must rest rather than pay the certain crossing cost.
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(
            actions[0].reason, "soft_reducing_quote_at_active_touch")

    def test_kickoff_exit_uses_displayed_depth_rather_than_settling(self):
        # Thin book: half the displayed depth is not enough to clear the
        # position, so the stressed check alone would refuse and hold into the
        # game. Settlement is worse than any crossing price.
        thin = BookSnapshot(
            "market-one",
            (BookLevel(.49, 3.0), BookLevel(.48, 1.0)),
            (BookLevel(.51, 3.0), BookLevel(.52, 1.0)),
            state="MARKET_STATE_OPEN")
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300))
        at_deadline = T0 + timedelta(hours=5) - timedelta(seconds=200)

        actions = reconciler.plan(frame(
            now=at_deadline, position="2", orders=(), snapshot=thin,
            opened=at_deadline - timedelta(seconds=5), entry=.49))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)

    def test_stressed_depth_still_refuses_away_from_kickoff(self):
        thin = BookSnapshot(
            "market-one",
            (BookLevel(.49, 1.0),),
            (BookLevel(.51, 1.0),),
            state="MARKET_STATE_OPEN")
        reconciler = DesiredStateReconciler(
            policy(age=15, force_exit_before_start_seconds=300))
        early = T0 + timedelta(seconds=30)

        actions = reconciler.plan(frame(
            now=early, position="2", orders=(), snapshot=thin,
            opened=T0, entry=.49))

        # Hours from kickoff the book can still improve, so the conservative
        # refusal is correct and must not be relaxed: no IOC is sent.
        self.assertEqual(actions[0].kind, ActionKind.CRITICAL)
        # But refusing to cross must not end the session. The reason is the
        # recoverable one, so the position keeps its passive reducer and every
        # other market keeps quoting; the pre-kickoff flatten is the deadline
        # that actually forces the exit.
        self.assertEqual(actions[0].reason, "hard_exit_deferred_thin_book")
        from liquidity_bot.live_commissioning import (
            RECOVERABLE_STRATEGY_CRITICALS)
        self.assertIn(actions[0].reason, RECOVERABLE_STRATEGY_CRITICALS)

    def test_thin_book_at_kickoff_is_still_terminal(self):
        thin = BookSnapshot(
            "market-one",
            (BookLevel(.49, 1.0),),
            (BookLevel(.51, 1.0),),
            state="MARKET_STATE_OPEN")
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300))
        near_kickoff = T0 + timedelta(hours=5) - timedelta(seconds=200)

        actions = reconciler.plan(frame(
            now=near_kickoff, position="2", orders=(), snapshot=thin,
            opened=near_kickoff - timedelta(seconds=5), entry=.49))

        # At the kickoff deadline the alternative to crossing is settlement,
        # so an exit that still cannot complete against displayed depth is a
        # genuine emergency and stays terminal.
        self.assertEqual(actions[0].kind, ActionKind.CRITICAL)
        self.assertEqual(
            actions[0].reason, "hard_exit_insufficient_stressed_depth")
        from liquidity_bot.live_commissioning import (
            RECOVERABLE_STRATEGY_CRITICALS)
        self.assertNotIn(actions[0].reason, RECOVERABLE_STRATEGY_CRITICALS)

    def test_a_retired_market_still_works_its_exit(self):
        # Retirement clears reward eligibility so no side can be quoted. The
        # position must still be reduced: a market that stops quoting but also
        # stops exiting would hold inventory to settlement, which is the most
        # expensive failure either project has measured.
        actions = DesiredStateReconciler(policy(age=900)).plan(frame(
            now=T0 + timedelta(seconds=30), position="2", orders=(),
            opened=T0, entry=.49, eligible=frozenset()))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(
            actions[0].reason, "soft_reducing_quote_at_active_touch")
        self.assertEqual(actions[0].side, "offer")

    def test_a_retired_market_still_hard_exits_at_the_deadline(self):
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300))
        near_kickoff = T0 + timedelta(hours=5) - timedelta(seconds=200)

        actions = reconciler.plan(frame(
            now=near_kickoff, position="2", orders=(),
            opened=near_kickoff - timedelta(seconds=5), entry=.49,
            eligible=frozenset()))

        # The pre-kickoff flatten is what stops a retired market settling.
        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)

    def test_a_flat_retired_market_plans_nothing_at_all(self):
        actions = DesiredStateReconciler(policy()).plan(
            frame(eligible=frozenset()))

        # No eligibility, no position: nothing to do. Not a critical, which
        # would churn the retirement counter for a market that is simply done.
        self.assertEqual(actions, [])

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

    def test_soft_long_exit_never_quotes_below_entry(self):
        falling = book(T0 + timedelta(seconds=1), bid=.39, offer=.41)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", opened=T0,
            entry=.50, snapshot=falling))
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "offer")
        self.assertEqual(actions[0].price, .50)

    def test_soft_short_exit_never_quotes_above_entry(self):
        rising = book(T0 + timedelta(seconds=1), bid=.61, offer=.63)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="-2", opened=T0,
            entry=.50, snapshot=rising))
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "bid")
        self.assertEqual(actions[0].price, .50)

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

    def test_a_one_pass_eligibility_flip_does_not_cancel_a_resting_quote(self):
        """The score and projection calls can disagree for a single pass.

        Measured 2026-08-15: an incentive refresh flipped every market at once,
        seven markets cancelled inside 1.5 seconds and re-placed nine seconds
        later, several at the identical price. Reward pays for resting time, so
        each of those was pure loss.
        """
        orders = (
            WorkingOrder("bid-one", "bid", .49, Decimal("2")),
            WorkingOrder("offer-one", "offer", .51, Decimal("2")),
        )

        actions = DesiredStateReconciler(policy()).plan(frame(
            snapshot=book(bid=.49, offer=.51), orders=orders,
            eligible=frozenset()))

        self.assertEqual(
            [a for a in actions if a.kind == ActionKind.CANCEL], [])

    def test_a_sustained_ineligible_side_is_still_pulled(self):
        """A side that is genuinely dead still clears the dwell."""
        orders = (
            WorkingOrder("bid-one", "bid", .49, Decimal("2")),
            WorkingOrder("offer-one", "offer", .51, Decimal("2")),
        )
        reconciler = DesiredStateReconciler(policy())
        reconciler.plan(frame(
            snapshot=book(bid=.49, offer=.51), orders=orders,
            eligible=frozenset()))

        later = T0 + timedelta(seconds=120)
        actions = reconciler.plan(frame(
            now=later, snapshot=book(later, bid=.49, offer=.51),
            orders=orders, eligible=frozenset()))

        self.assertEqual(
            {a.order_id for a in actions if a.kind == ActionKind.CANCEL},
            {"bid-one", "offer-one"})

    def test_an_ineligible_side_with_nothing_resting_is_never_quoted(self):
        """The dwell protects accrued resting time, not new risk."""
        actions = DesiredStateReconciler(policy()).plan(
            frame(eligible=frozenset()))

        self.assertEqual(
            [a for a in actions if a.kind == ActionKind.PLACE_POST_ONLY], [])

    def test_a_tight_book_is_priced_back_to_two_cents_not_refused(self):
        """50/50 is quoted 49/49, not skipped.

        The venue publishes bids only, so `best_offer - best_bid` is the gap an
        operator reads between the two outcomes' best bids. Both outcomes bid
        at 50 leaves nothing in the middle; backing each side off one tick
        leaves the two cents the rule asks for, and still earns.
        """
        actions = DesiredStateReconciler(policy()).plan(
            frame(snapshot=book(bid=.50, offer=.50)))

        prices = {action.side: action.price for action in actions}
        self.assertTrue(all(action.kind == ActionKind.PLACE_POST_ONLY
                            for action in actions))
        self.assertAlmostEqual(prices["bid"], .49, places=6)
        self.assertAlmostEqual(prices["offer"], .51, places=6)

    def test_a_book_already_wider_than_two_cents_is_quoted_at_the_touch(self):
        """A 7c book stays where it is -- no stepping inside the touch."""
        actions = DesiredStateReconciler(policy()).plan(
            frame(snapshot=book(bid=.50, offer=.57)))

        prices = {action.side: action.price for action in actions}
        self.assertAlmostEqual(prices["bid"], .50, places=6)
        self.assertAlmostEqual(prices["offer"], .57, places=6)

    def test_an_overlapping_book_is_not_quoted(self):
        """Best bids summing past one is stale data, not a tradeable book."""
        actions = DesiredStateReconciler(policy()).plan(
            frame(snapshot=book(bid=.60, offer=.40)))

        self.assertEqual(
            [a for a in actions if a.kind == ActionKind.PLACE_POST_ONLY], [])

    def test_a_dip_to_a_tight_book_reprices_to_the_same_place_it_rests(self):
        """The back-off lands exactly where the touch sits at two cents.

        So a book oscillating between a 2c touch and a 0c touch asks for the
        same quote either way, and a resting order is left alone rather than
        cancelled and re-placed. Reward pays for resting time.
        """
        orders = (
            WorkingOrder("bid-one", "bid", .49, Decimal("2")),
            WorkingOrder("offer-one", "offer", .51, Decimal("2")),
        )

        at_touch = DesiredStateReconciler(policy()).plan(
            frame(snapshot=book(bid=.49, offer=.51), orders=orders))
        dipped = DesiredStateReconciler(policy()).plan(
            frame(snapshot=book(bid=.50, offer=.50), orders=orders))

        self.assertEqual(at_touch, [])
        self.assertEqual(dipped, [])

    def test_tight_spread_never_blocks_inventory_reducer(self):
        tight = book(T0 + timedelta(seconds=1), bid=.49, offer=.50)

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", opened=T0,
            entry=.49, snapshot=tight))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "offer")

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
