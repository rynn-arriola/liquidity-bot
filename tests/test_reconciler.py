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
        minimum_trade_qty=.01,
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
          eligible=frozenset({"bid", "offer"}), new_risk_allowed=True,
          metadata=None,
          ) -> MarketFrame:
    snapshot = snapshot or book(now)
    return MarketFrame(
        metadata or market(), now, snapshot, now, Decimal(position), tuple(orders),
        state_known, eligible, {"bid": .01, "offer": .01},
        portfolio_loss, opened, entry, new_risk_allowed)


class ReconcilerTests(unittest.TestCase):
    def test_account_pause_immediately_cancels_building_quotes(self):
        orders = (
            WorkingOrder("bid-order", "bid", .49, Decimal("2")),
            WorkingOrder("offer-order", "offer", .51, Decimal("2")),
        )

        actions = DesiredStateReconciler(policy()).plan(frame(
            orders=orders, new_risk_allowed=False))

        self.assertEqual({action.kind for action in actions}, {
            ActionKind.CANCEL})
        self.assertEqual({action.order_id for action in actions}, {
            "bid-order", "offer-order"})
        self.assertTrue(all(
            action.reason ==
            "side_no_longer_desired:account_new_risk_paused"
            for action in actions))

    def test_account_pause_does_not_disable_inventory_reducer(self):
        actions = DesiredStateReconciler(policy()).plan(frame(
            position="2", opened=T0, entry=.49,
            new_risk_allowed=False))

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(
            actions[0].reason, "soft_reducing_quote_at_active_touch")

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

    def test_tiny_residual_gets_five_minutes_to_exit_passively(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=299), position="0.09", orders=(),
            opened=T0, entry=.49))

        self.assertEqual([action.kind for action in actions], [
            ActionKind.PLACE_POST_ONLY])
        self.assertEqual(
            actions[0].reason, "soft_reducing_quote_at_active_touch")
        self.assertEqual(actions[0].qty, Decimal("0.09"))

    def test_tiny_residual_cashout_uses_exact_quantity_after_five_minutes(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="0.09", orders=(),
            opened=T0, entry=.49))

        self.assertEqual([action.kind for action in actions], [
            ActionKind.PLACE_IOC])
        self.assertEqual(actions[0].reason, "tiny_residual_timeout")
        self.assertEqual(actions[0].qty, Decimal("0.09"))
        self.assertEqual(actions[0].side, "offer")

    def test_tiny_short_residual_cashout_uses_the_reducing_bid(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="-0.01", orders=(),
            opened=T0, entry=.51))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(actions[0].reason, "tiny_residual_timeout")
        self.assertEqual(actions[0].qty, Decimal("0.01"))
        self.assertEqual(actions[0].side, "bid")

    def test_point_ten_position_keeps_the_full_inventory_timeout(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="0.10", orders=(),
            opened=T0, entry=.49))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(
            actions[0].reason, "soft_reducing_quote_at_active_touch")

    def test_tiny_residual_cashout_requires_venue_aligned_quantity(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_min_qty=Decimal("0.01"),
            small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))
        coarse_market = MarketMetadata(
            "market-one", T0 + timedelta(hours=5), .01, True, False,
            "MARKET_STATE_OPEN", "moneyline", minimum_trade_qty=.05,
            sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE")

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="0.03", orders=(),
            opened=T0, entry=.49, metadata=coarse_market))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(
            actions[0].reason, "soft_reducing_quote_at_active_touch")

    def test_tiny_residual_cashout_uses_displayed_depth_after_grace(self):
        # Half-depth is 0.05 and cannot cover 0.09, but displayed depth can.
        # The tiny cashout is bounded to nine cents and should therefore use
        # the same exact displayed-depth fallback as the kickoff flatten.
        thin = BookSnapshot(
            "market-one",
            (BookLevel(.49, .10),),
            (BookLevel(.51, .10),),
            state="MARKET_STATE_OPEN")
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="0.09", orders=(),
            snapshot=thin, opened=T0, entry=.49))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(actions[0].reason, "tiny_residual_timeout")

    def test_tiny_residual_still_cancels_one_order_before_cashout(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))
        reducer = WorkingOrder(
            "reduce-offer", "offer", .51, Decimal("0.09"))
        builder = WorkingOrder(
            "build-bid", "bid", .49, Decimal("2"))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="0.09",
            orders=(reducer, builder), opened=T0, entry=.49))

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.CANCEL)
        self.assertEqual(actions[0].order_id, "build-bid")
        self.assertEqual(
            actions[0].reason, "hard_timeout_cancel_confirm_first")

    def test_tiny_residual_sequences_every_cancel_before_cashout(self):
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))
        reducer = WorkingOrder(
            "reduce-offer", "offer", .51, Decimal("0.09"))
        builder = WorkingOrder(
            "build-bid", "bid", .49, Decimal("2"))
        due = T0 + timedelta(seconds=300)

        first = reconciler.plan(frame(
            now=due, position="0.09", orders=(reducer, builder),
            opened=T0, entry=.49))
        self.assertEqual(first[0].order_id, "build-bid")
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "build-bid", submitted_at=due)

        waiting = reconciler.plan(frame(
            now=due + timedelta(seconds=1), position="0.09",
            orders=(reducer, builder), opened=T0, entry=.49))
        self.assertEqual(waiting, [])

        second = reconciler.plan(frame(
            now=due + timedelta(seconds=2), position="0.09",
            orders=(reducer,), opened=T0, entry=.49))
        self.assertEqual(second[0].kind, ActionKind.CANCEL)
        self.assertEqual(second[0].order_id, "reduce-offer")
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "reduce-offer",
            submitted_at=due + timedelta(seconds=2))

        cashout = reconciler.plan(frame(
            now=due + timedelta(seconds=3), position="0.09", orders=(),
            opened=T0, entry=.49))
        self.assertEqual(cashout[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(cashout[0].reason, "tiny_residual_timeout")
        self.assertEqual(cashout[0].qty, Decimal("0.09"))

    def test_tiny_residual_with_insufficient_displayed_depth_stays_recoverable(self):
        thinner_than_residual = BookSnapshot(
            "market-one",
            (BookLevel(.49, .08),),
            (BookLevel(.51, .08),),
            state="MARKET_STATE_OPEN")
        reconciler = DesiredStateReconciler(policy(
            age=900, small_residual_max_qty=Decimal("0.09"),
            small_residual_timeout_seconds=300))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=300), position="0.09", orders=(),
            snapshot=thinner_than_residual, opened=T0, entry=.49))

        self.assertEqual(actions[0].kind, ActionKind.CRITICAL)
        self.assertEqual(
            actions[0].reason, "hard_exit_deferred_thin_book")

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
        # The reducer rests one tick INSIDE the 0.51 touch, at 0.50, because
        # 0.50 still clears the 0.49 entry. Resting at 0.51 is no longer the
        # correct price and would be re-priced -- which is a different test.
        orders = (
            WorkingOrder("bid-one", "bid", .49, Decimal("2")),
            WorkingOrder("offer-one", "offer", .50, Decimal("2")),
        )
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", orders=orders,
            opened=T0, entry=.49))
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0].kind, actions[0].order_id),
                         (ActionKind.CANCEL, "bid-one"))

    def test_a_long_rests_its_exit_on_an_offers_only_book(self):
        """A passive sell JOINS the offer side, so that is the side it needs.

        The book going one-sided used to strand a position until settlement:
        parse_book_response demanded both sides, so the book never refreshed,
        went stale, and the reconciler returned book_stale before planning --
        losing the passive reducer, the hard exit and the pre-kickoff flatten
        together. Measured 2026-08-15 on chevic-scovla: 254 log lines, exactly
        one order action, position rode to settlement.
        """
        offers_only = BookSnapshot(
            "market-one", (), (BookLevel(.60, 100), BookLevel(.61, 100)),
            "MARKET_STATE_OPEN", T0 + timedelta(seconds=1))

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", opened=T0,
            entry=.50, snapshot=offers_only))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "offer")
        self.assertEqual(actions[0].price, .60)

    def test_a_long_still_crosses_out_at_the_deadline_on_a_bids_only_book(self):
        """The case that caused settlement, and the reason this matters.

        With no offer side there is no touch to rest at, so no passive quote is
        possible -- but the deadline exit crosses INTO the bids, which is
        exactly the side a bids-only book still carries. Previously the whole
        market was unplannable and the position simply rode to settlement.
        """
        bids_only = BookSnapshot(
            "market-one",
            (BookLevel(.45, 500), BookLevel(.44, 500)), (),
            "MARKET_STATE_OPEN", T0 + timedelta(seconds=20))

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=20), position="2", opened=T0,
            entry=.50, snapshot=bids_only))

        self.assertTrue(actions, "a held position must still be plannable")
        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(actions[0].reason, "hard_inventory_timeout")

    def test_a_book_near_zero_is_never_opened(self):
        """Refuse to BUILD near either end of the book.

        Measured 2026-08-21 across 321 markets, bucketed by the most extreme
        price each traded at: loss per market runs -1.71 below 0.05 and -1.47
        from 0.05-0.10, against -0.18 in the 0.25-0.50 bucket. Monotonic across
        all five buckets, and everything under 0.10 is 11% of markets and 40%
        of all losses. Section 1 agrees from the fee side -- the maker rebate
        is 0.0125 x qty x p x (1-p), so it pays least exactly where adverse
        selection is worst.

        The trade that prompted it: a CS2 match whose venue gameStartTime had
        not been corrected, so a LIVE game sat in the pregame universe. The bot
        chased the touch 0.19 -> 0.07 and filled 10 on the side that lost, for
        -$9.29. The clock could not have saved it; the economics can.
        """
        collapsing = book(T0 + timedelta(seconds=1), bid=0.06, offer=0.08)

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), snapshot=collapsing))

        self.assertEqual(
            [a for a in actions if a.kind == ActionKind.PLACE_POST_ONLY], [],
            "a quote resting at 7 cents earns the least rebate available and "
            "carries the whole asymmetry of selling a near-certainty")

    def test_a_book_near_one_is_never_opened(self):
        """The far end is the same trade seen from the other side.

        One comparison covers both, because the opposite side pays 1 - price.
        """
        collapsing = book(T0 + timedelta(seconds=1), bid=0.92, offer=0.94)

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), snapshot=collapsing))

        self.assertEqual(
            [a for a in actions if a.kind == ActionKind.PLACE_POST_ONLY], [])

    def test_an_extreme_book_still_lets_a_position_exit(self):
        """The gate must never strand inventory.

        Invariant 42: spread and structure gates apply only to BUILDING risk.
        Once a position exists, passive reduction and the bounded hard exit
        stay available at every price -- otherwise a market that collapsed
        after filling us would trap the position it created.
        """
        collapsing = book(T0 + timedelta(seconds=1), bid=0.05, offer=0.07)

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), snapshot=collapsing,
            position="2", entry=0.40, opened=T0))

        self.assertTrue(actions, "a held position must still be worked")
        # The reason names the exit path; nothing here may be a BUILDING quote.
        self.assertTrue(
            all("reducing" in a.reason or "exit" in a.reason
                or "hard" in a.reason or a.kind != ActionKind.PLACE_POST_ONLY
                for a in actions),
            f"a held position must only produce exit work, got "
            f"{[(a.kind.name, a.reason) for a in actions]}")

    def test_a_one_sided_book_is_never_quoted(self):
        """Quoting needs both touches -- the 2c rule is defined between them."""
        bids_only = BookSnapshot(
            "market-one", (BookLevel(.60, 100),), (),
            "MARKET_STATE_OPEN", T0 + timedelta(seconds=1))

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), snapshot=bids_only))

        self.assertEqual(actions, [])

    def test_a_losing_long_exit_rests_at_the_touch_not_at_entry(self):
        """Invariant 5: no cost-basis floor. The code used to contradict it.

        A floor can only fill when the market comes back to entry, so it exits
        winners and strands losers -- nobody buys at 0.50 while the market is
        0.41. maker-lab ran one and measured its settlement pool at 2 winners
        against 14 losers, documenting it as "79% losers BY CONSTRUCTION": 234
        settled positions cost $443.88 where crossing at T-15 would have cost
        $64.18. Resting at the touch lets a loser leave passively, earning the
        maker rebate instead of paying a taker fee.
        """
        falling = book(T0 + timedelta(seconds=1), bid=.39, offer=.41)

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", opened=T0,
            entry=.50, snapshot=falling))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "offer")
        self.assertEqual(actions[0].price, .41)

    def test_a_losing_short_exit_rests_at_the_touch_not_at_entry(self):
        rising = book(T0 + timedelta(seconds=1), bid=.61, offer=.63)

        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="-2", opened=T0,
            entry=.50, snapshot=rising))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "bid")
        self.assertEqual(actions[0].price, .61)

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

    def test_the_hard_exit_cancels_before_it_declares_a_stuck_cancel(self):
        """The grace measures our cancel, not how late the deadline is.

        Measuring from the deadline made this permanently critical: `overdue`
        only grows, so past the grace every pass returned the critical instead
        of issuing the cancel, and a cancel that is never re-sent can never
        confirm. Observed 2026-08-15 on tkamak-tsokos: ten retirement rounds
        over ten minutes, position still open when a deploy landed.
        """
        reconciler = DesiredStateReconciler(policy())
        active = WorkingOrder("exit", "offer", .51, Decimal("2"))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="2", orders=(active,),
            opened=T0, entry=.49))

        self.assertEqual([item.kind for item in actions], [ActionKind.CANCEL])
        self.assertEqual(
            actions[0].reason, "hard_timeout_cancel_confirm_first")

    def test_cancel_stuck_past_grace_has_explicit_critical_failure(self):
        """Once OUR cancel has been outstanding past the grace, escalate."""
        reconciler = DesiredStateReconciler(policy())
        active = WorkingOrder("exit", "offer", .51, Decimal("2"))
        reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="2", orders=(active,),
            opened=T0, entry=.49))
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "exit",
            submitted_at=T0 + timedelta(seconds=21))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=30), position="2", orders=(active,),
            opened=T0, entry=.49))

        self.assertEqual([item.kind for item in actions], [ActionKind.CRITICAL])
        self.assertEqual(
            actions[0].reason, "hard_exit_blocked_cancel_unconfirmed")

    def test_hard_exit_cancels_building_order_then_reducer_one_at_a_time(self):
        """Two working orders cannot share one cancel-confirmation timer."""
        reconciler = DesiredStateReconciler(policy())
        reducer = WorkingOrder("reduce-offer", "offer", .51, Decimal("2"))
        builder = WorkingOrder("build-bid", "bid", .49, Decimal("2"))
        due = T0 + timedelta(seconds=21)

        first = reconciler.plan(frame(
            now=due, position="2", orders=(reducer, builder),
            opened=T0, entry=.49))

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].kind, ActionKind.CANCEL)
        self.assertEqual(first[0].order_id, "build-bid")
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "build-bid", submitted_at=due)

        waiting = reconciler.plan(frame(
            now=due + timedelta(seconds=1), position="2",
            orders=(reducer, builder), opened=T0, entry=.49))
        self.assertEqual(waiting, [])

        second = reconciler.plan(frame(
            now=due + timedelta(seconds=2), position="2", orders=(reducer,),
            opened=T0, entry=.49))
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].kind, ActionKind.CANCEL)
        self.assertEqual(second[0].order_id, "reduce-offer")
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "reduce-offer",
            submitted_at=due + timedelta(seconds=2))

        exit_action = reconciler.plan(frame(
            now=due + timedelta(seconds=3), position="2", orders=(),
            opened=T0, entry=.49))
        self.assertEqual([item.kind for item in exit_action], [
            ActionKind.PLACE_IOC])
        self.assertEqual(exit_action[0].qty, Decimal("2"))

    def test_short_hard_exit_cancels_building_offer_first(self):
        reconciler = DesiredStateReconciler(policy())
        reducer = WorkingOrder("reduce-bid", "bid", .49, Decimal("2"))
        builder = WorkingOrder("build-offer", "offer", .51, Decimal("2"))

        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="-2",
            orders=(reducer, builder), opened=T0, entry=.51))

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].order_id, "build-offer")

    def test_hard_exit_cancel_grace_starts_only_after_submission(self):
        reconciler = DesiredStateReconciler(policy())
        active = WorkingOrder("exit", "offer", .51, Decimal("2"))

        first = reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="2", orders=(active,),
            opened=T0, entry=.49))
        later = reconciler.plan(frame(
            now=T0 + timedelta(seconds=40), position="2", orders=(active,),
            opened=T0, entry=.49))

        self.assertEqual(first[0].kind, ActionKind.CANCEL)
        self.assertEqual(later[0].kind, ActionKind.CANCEL)

    def test_restart_clears_exact_hard_exit_cancel_tracking(self):
        reconciler = DesiredStateReconciler(policy())
        active = WorkingOrder("exit", "offer", .51, Decimal("2"))
        reconciler.note_hard_exit_cancel_submitted(
            "market-one", "exit", submitted_at=T0)

        reconciler.restart_hard_exit_budget("market-one")
        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=21), position="2", orders=(active,),
            opened=T0, entry=.49))

        self.assertEqual(actions[0].kind, ActionKind.CANCEL)
        self.assertNotIn("market-one", reconciler._hard_exit_cancel_at)
        self.assertNotIn("market-one", reconciler._hard_exit_cancel_order_id)

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

    def test_every_backed_off_price_lands_on_the_tick_grid(self):
        """A price off the grid is rejected and retires the market.

        `41 * 0.01` is 0.41000000000000003 in binary floating point, and
        build_order_payload validates alignment in Decimal. Nine of the
        ninety-nine cent prices land this way, so roughly one backed-off quote
        in eleven burned a market slot for the whole session. Observed live
        2026-08-15T07:01Z on merval-soprus.
        """
        reconciler = DesiredStateReconciler(policy())
        for cents in range(2, 99):
            touch = round(cents / 100.0, 10)
            snapshot = book(bid=touch, offer=touch)
            for side in ("bid", "offer"):
                price = reconciler._entry_price(snapshot, side, 0.01)
                if price is None:
                    continue
                self.assertEqual(
                    Decimal(str(price)) % Decimal("0.01"), Decimal("0"),
                    f"{side} price {price!r} off the tick grid at {cents}c")

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

    def test_an_expensive_early_cross_is_refused_and_keeps_resting(self):
        """The cap that could never fire.

        `max_exit_loss` served BOTH this and the entry gate, wired to the $40
        session stop -- and five contracts cannot lose $40, so every crossed
        loss went through unchallenged, including the $6.61 and $6.15 measured
        on 2026-08-16. `max_early_exit_loss` separates the two.
        """

        collapsed = book(T0, bid=.05, offer=.07, depth=100)
        reconciler = DesiredStateReconciler(
            policy(age=15, max_early_exit_loss=0.5))
        actions = reconciler.plan(frame(
            now=T0 + timedelta(seconds=60), position="2", orders=(),
            opened=T0, entry=.90, snapshot=collapsed))

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.CRITICAL)
        self.assertEqual(actions[0].reason, "hard_exit_price_cap_exceeded")

    def test_the_kickoff_deadline_overrides_the_early_exit_cap(self):
        """A cap must never hold a position into settlement."""

        collapsed = book(T0, bid=.05, offer=.07, depth=100)
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300,
                   max_early_exit_loss=0.5))
        at_deadline = T0 + timedelta(hours=5) - timedelta(seconds=200)

        actions = reconciler.plan(frame(
            now=at_deadline, position="2", orders=(),
            opened=at_deadline - timedelta(seconds=5), entry=.90,
            snapshot=collapsed))

        self.assertEqual(actions[0].kind, ActionKind.PLACE_IOC)
        self.assertEqual(actions[0].reason, "hard_inventory_timeout")

    def test_the_early_exit_cap_does_not_touch_the_entry_gate(self):
        """Entry reads `max_exit_loss`; only the early cross reads the new one.

        Tightening the shared number would have tightened entry ~50x and
        refused most markets. Same book, same entry gate, both caps.
        """

        tight = DesiredStateReconciler(policy(max_early_exit_loss=0.01))
        loose = DesiredStateReconciler(policy(max_early_exit_loss=40.0))

        self.assertEqual(
            [(a.kind, a.side, a.price) for a in tight.plan(frame())],
            [(a.kind, a.side, a.price) for a in loose.plan(frame())])

    def test_a_profitable_exit_steps_one_tick_inside_the_touch(self):
        """Priority is leaving, not the last cent.

        Joining the touch queues us behind everyone already there. One tick
        inside makes us the touch. Measured 2026-08-16: exits that ended in a
        taker cross were -$30.22 across 28 markets, ~76% of real losses, and
        the losers sit at 911s/966s hold time -- right on the 900s clock. One
        tick on five contracts is $0.05.
        """

        wide = book(T0, bid=.44, offer=.50)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="5", orders=(),
            opened=T0, entry=.44, snapshot=wide))

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, ActionKind.PLACE_POST_ONLY)
        self.assertEqual(actions[0].side, "offer")
        self.assertEqual(actions[0].price, .49)

    def test_a_losing_exit_does_not_step_inside_and_deepen_the_loss(self):
        """The step is a concession, and a loser concedes the wrong way.

        Stepping inside a 0.41 touch on a 0.44 long sells at 0.40 -- a tick
        worse -- to leave sooner. It still rests AT the touch and still follows
        it down: this is not a cost-basis floor, which invariant 5 forbids.
        """

        under_water = book(T0, bid=.39, offer=.41)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="5", orders=(),
            opened=T0, entry=.44, snapshot=under_water))

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].price, .41, "must join the touch, not 0.40")

    def test_the_exit_step_never_prices_through_the_opposite_touch(self):
        """A post-only order that would match is rejected outright."""

        one_tick = book(T0, bid=.49, offer=.50)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="5", orders=(),
            opened=T0, entry=.40, snapshot=one_tick))

        # 0.49 would sit on the bid, so it joins the 0.50 touch instead.
        self.assertEqual(actions[0].price, .50)

    def test_a_short_exit_steps_inside_upward(self):
        """Mirror image: a short reduces on the bid and steps UP toward it."""

        wide = book(T0, bid=.44, offer=.50)
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="-5", orders=(),
            opened=T0, entry=.50, snapshot=wide))

        self.assertEqual(actions[0].side, "bid")
        self.assertEqual(actions[0].price, .45)

    def test_the_exit_price_lands_on_the_tick_grid(self):
        """`41 * 0.01` is 0.41000000000000003 and the payload validates
        alignment in Decimal, so an unrounded price is rejected outright and
        retires the market for the session."""

        from decimal import Decimal as D
        for bid, offer, entry in ((.40, .42, .35), (.46, .48, .40),
                                  (.56, .58, .50), (.68, .70, .60),
                                  (.81, .83, .75), (.93, .95, .85)):
            actions = DesiredStateReconciler(policy()).plan(frame(
                now=T0 + timedelta(seconds=1), position="5", orders=(),
                opened=T0, entry=entry, snapshot=book(T0, bid=bid, offer=offer)))
            price = actions[0].price
            self.assertEqual(
                D(str(price)) % D("0.01"), D("0"),
                f"{price!r} is off the tick grid for book {bid}/{offer}")

    def test_duplicate_reducing_order_is_canceled_to_prevent_overclose(self):
        # Both at the desired price, so only the duplicate is what gets pulled.
        orders = (
            WorkingOrder("exit-one", "offer", .50, Decimal("2")),
            WorkingOrder("exit-two", "offer", .50, Decimal("2")),
        )
        actions = DesiredStateReconciler(policy()).plan(frame(
            now=T0 + timedelta(seconds=1), position="2", orders=orders,
            opened=T0, entry=.49))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].order_id, "exit-two")



class SideDropCauseTests(unittest.TestCase):
    """A cancelled side must say WHICH exit test failed.

    Measured 2026-08-19: markets cancelled and re-placed the same side at the
    same price every 2-3 minutes for hours -- 141 of 143 orders cancelled with
    only 2 resting, 87 places against 80 cancels -- and the single reason
    `side_no_longer_desired` could not say whether the simulated exit had gone
    incomplete or merely expensive.

    They want opposite fixes. An incomplete exit means the book is genuinely
    too thin to get out of a fill, and the gate is right to fire. A pnl
    flickering either side of the threshold on an unchanged price is a case
    for hysteresis. Guessing between them is how a fix gets built on an
    inferred mechanism, so the reason carries the cause.
    """

    def resting_bid(self):
        return (WorkingOrder("order-1", "bid", .49, Decimal("2")),)

    def test_an_impossible_exit_is_named_exit_incomplete(self):
        # Depth of 1 cannot absorb a quote size of 2, so crossing out never
        # completes however cheap it looks.
        thin = BookSnapshot(
            "market-one",
            (BookLevel(.49, 1), BookLevel(.48, 1)),
            (BookLevel(.51, 1), BookLevel(.52, 1)),
            "MARKET_STATE_OPEN", T0)
        actions = DesiredStateReconciler(policy()).plan(
            frame(orders=self.resting_bid(), snapshot=thin))
        cancels = [a for a in actions if a.kind is ActionKind.CANCEL]
        self.assertTrue(cancels, "a side that cannot be exited must cancel")
        self.assertEqual(
            cancels[0].reason, "side_no_longer_desired:exit_incomplete")

    def test_an_affordable_book_that_breaches_the_loss_cap_is_named_exit_loss(self):
        # Deep enough to complete, but the cap is set below what crossing a
        # two-cent book costs, so it fails on price rather than feasibility.
        actions = DesiredStateReconciler(
            policy(max_exit_loss=0.001)).plan(frame(orders=self.resting_bid()))
        cancels = [a for a in actions if a.kind is ActionKind.CANCEL]
        self.assertTrue(cancels, "a side that cannot be exited cheaply cancels")
        self.assertEqual(cancels[0].reason, "side_no_longer_desired:exit_loss")

    def test_a_zero_expected_net_names_itself_as_the_cause(self):
        """The churn driver, reproduced.

        `expected_net_by_side` is floored to 1e-9 only when the gate says NOT
        to quote (`live_commissioning`, "floored the same way the selector
        already floors it"). When the gate says quote, the raw value is used
        and can sit at or below zero -- and the reconciler wants a side only
        when it is strictly positive. So a side the gate wants gets cancelled,
        re-placed at the same price, and cancelled again.

        Measured 2026-08-19: 141 of 143 orders cancelled with 2 resting, the
        same side re-placed at 0.45 every 2-3 minutes, `blockers={}` and
        `net={'bid': 0.0}` throughout -- 0.0 because round(1e-9, 6) is 0.0.
        """

        f = frame(orders=(WorkingOrder("order-1", "bid", .49, Decimal("2")),))
        f.expected_net_by_side["bid"] = 0.0
        actions = DesiredStateReconciler(policy()).plan(f)
        cancels = [a for a in actions if a.kind is ActionKind.CANCEL]
        self.assertTrue(cancels)
        self.assertEqual(
            cancels[0].reason,
            "side_no_longer_desired:expected_net_not_positive",
            "the churn must be attributable from the log alone")

    def test_a_side_not_evaluated_this_pass_is_not_treated_as_uneconomic(self):
        """The dwell was being defeated by a dictionary default.

        `clear_economics()` wipes `expected_net_by_side` before every pass, so
        a side the runner skipped -- an overlapping book, or an early return
        out of `_evaluate_rewards` -- has no entry at all. Reading that through
        `.get(side, 0.0)` turned "we did not look" into "this is worthless" and
        cancelled the resting order, inside the 90s reward-eligibility grace
        that exists to stop exactly that flapping.

        Measured 2026-08-19: mean resting fraction 57.3% across 179 sides, the
        same side re-placed at the same price every 2-3 minutes.
        """

        f = frame(orders=(WorkingOrder("order-1", "bid", .49, Decimal("2")),))
        f.expected_net_by_side.clear()          # nothing evaluated this pass
        actions = DesiredStateReconciler(policy()).plan(f)
        self.assertEqual(
            [a for a in actions if a.kind is ActionKind.CANCEL], [],
            "a side that was not evaluated must keep its resting quote")

    def test_a_side_evaluated_at_zero_is_still_dropped(self):
        """The fix must not swallow a real refusal. Absence is skipped;
        a value that was actually computed and came out at or below zero
        still cancels, and still names itself."""

        f = frame(orders=(WorkingOrder("order-1", "bid", .49, Decimal("2")),))
        f.expected_net_by_side["bid"] = 0.0     # evaluated, and worthless
        cancels = [a for a in DesiredStateReconciler(policy()).plan(f)
                   if a.kind is ActionKind.CANCEL]
        self.assertTrue(cancels)
        self.assertEqual(
            cancels[0].reason,
            "side_no_longer_desired:expected_net_not_positive")

    def test_a_healthy_book_keeps_the_side_and_cancels_nothing(self):
        actions = DesiredStateReconciler(policy()).plan(
            frame(orders=self.resting_bid()))
        self.assertEqual(
            [a for a in actions if a.kind is ActionKind.CANCEL], [],
            "a side that can be exited must not be cancelled at all")


if __name__ == "__main__":
    unittest.main()


class PreKickoffEntryTests(unittest.TestCase):
    """Entry must not open what the exit is already committed to flattening."""

    KICKOFF = T0 + timedelta(hours=5)

    def plan_at(self, seconds_to_event, orders=()):
        reconciler = DesiredStateReconciler(
            policy(age=900, force_exit_before_start_seconds=300))
        moment = self.KICKOFF - timedelta(seconds=seconds_to_event)
        return reconciler.plan(frame(
            now=moment, position="0", orders=orders,
            snapshot=book(moment)))

    def test_flat_market_still_quotes_outside_the_flatten_window(self):
        actions = self.plan_at(600)
        self.assertEqual({action.side for action in actions}, {"bid", "offer"})
        self.assertTrue(all(action.kind == ActionKind.PLACE_POST_ONLY
                            for action in actions))

    def test_flat_market_opens_nothing_inside_the_flatten_window(self):
        # Entry gated on `now < game_start`, so the bot opened positions inside
        # the window `_inventory_plan` flattens at any price -- no loss cap, no
        # depth haircut. Measured 2026-08-16: pre-kickoff crossings were the
        # worst exit group at -0.075/contract, and a market 3.6 minutes from
        # kickoff was resting quotes at the time.
        self.assertEqual(self.plan_at(200), [])

    def test_resting_quotes_are_withdrawn_at_the_flatten_deadline(self):
        # Withdraw rather than wait to be filled into the window.
        resting = (WorkingOrder("order-bid", "bid", .49, Decimal("2")),)
        actions = self.plan_at(200, orders=resting)
        self.assertEqual([action.kind for action in actions],
                         [ActionKind.CANCEL])
        self.assertEqual(actions[0].order_id, "order-bid")
