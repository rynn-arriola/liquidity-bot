"""Pure desired-state reconciler used by Phase 4 dry runs.

It returns actions but performs no API request. The hard inventory timeout is
required input and deliberately has no default selected for live use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
import math
from typing import Mapping

from .models import BookSnapshot, MarketMetadata
from .shadow_math import cross_exit


class ActionKind(str, Enum):
    PLACE_POST_ONLY = "place_post_only"
    CANCEL = "cancel"
    PLACE_IOC = "place_ioc"
    HALT_NEW_RISK = "halt_new_risk"
    CRITICAL = "critical"


@dataclass(frozen=True)
class PlanAction:
    kind: ActionKind
    market_slug: str
    reason: str
    side: str | None = None
    price: float | None = None
    qty: Decimal | None = None
    order_id: str | None = None


@dataclass(frozen=True)
class WorkingOrder:
    order_id: str
    side: str
    price: float
    remaining_qty: Decimal

    def __post_init__(self) -> None:
        if self.side not in {"bid", "offer"} or self.remaining_qty <= 0:
            raise ValueError("working order is invalid")


@dataclass(frozen=True)
class ReconcilerPolicy:
    quote_size: Decimal
    max_inventory_age_seconds: int
    max_market_worst_loss: float
    max_portfolio_worst_loss: float
    max_exit_loss: float
    # What an EARLY cross may cost, before the kickoff deadline makes it
    # mandatory. Separate from `max_exit_loss` above, which is the ENTRY gate
    # -- do not open a side whose stressed close would cost more than this.
    # Both read the same number until 2026-08-16, and it was wired to the old
    # $40 shared loss value, so the exit half could never fire: five contracts cannot
    # lose $40. It was harmless at the original 1 contract / 1 market / $2 and
    # became meaningless as size grew, exactly as the old loss controls and the
    # exposure budgets did before they were split.
    #
    # $4.00 chosen from the 17 observed crossed losses: 0.10 0.36 0.64 0.70
    # 0.83 1.01 1.59 2.15 2.32 2.42 3.00 3.05 3.23 3.60 4.20 6.15 6.61.
    # Median 2.32, p75 3.23, p90 6.15. A cap here blocks 3 and defers $16.96
    # of $41.96 -- 40% of the damage from 18% of the events -- while leaving
    # the median exit untouched. Deliberately NOT tighter: at $0.75 it blocks
    # 13 of 17 and defers $40.16, which is not trimming a tail, it is a policy
    # of never crossing before kickoff, and it defers rather than avoids the
    # loss. That trade needs evidence about mean reversion between the
    # inventory clock and kickoff, which we do not have.
    max_early_exit_loss: float = 4.0
    exit_depth_haircut: float = 0.5
    taker_theta: float = 0.06
    max_book_age_seconds: float = 5.0
    cancel_confirm_grace_seconds: float = 5.0
    max_hard_exit_attempts: int = 3
    max_hard_exit_retry_seconds: float = 30.0
    hard_exit_confirmation_timeout_seconds: float = 10.0
    # Fresh selection threshold and locked-book fallback gap. See
    # entry_quote_price: an exact one-tick live book is the explicit operator-
    # selected touch exception; a locked book is priced back to this gap.
    minimum_entry_spread: float = 0.02
    # How close to 0.00 or 1.00 a BUILDING quote may rest. Entry only; exits
    # are unaffected (invariant 42). See the measurement at the use site.
    minimum_entry_price: float = 0.10
    # How long a *resting* side must stay reward-ineligible before it is
    # pulled. evaluate_order_score and project_order_reward are separate calls
    # that can disagree for a single pass, and the score call is depth-based,
    # so an incentive refresh flipped every market at once: measured
    # 2026-08-15, seven markets cancelled inside 1.5s and re-placed nine
    # seconds later, several at the identical price. A side that is genuinely
    # dead stays ineligible and still clears this dwell.
    reward_eligibility_dwell_seconds: float = 90.0
    force_exit_before_start_seconds: float = 300.0
    # A fractional maker fill can leave only a few cents of maximum settlement
    # exposure while still retaining a whole reward slot. Give that position a
    # short passive chance, then use the ordinary cancel-confirm-IOC machinery
    # rather than waiting through the full inventory clock. Quantity, not the
    # UI's marked dollar value, defines this band.
    small_residual_min_qty: Decimal = Decimal("0.01")
    small_residual_max_qty: Decimal = Decimal("0.09")
    small_residual_timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.quote_size <= 0 or self.max_inventory_age_seconds <= 0:
            raise ValueError("quote size and inventory age must be positive")
        if min(self.max_market_worst_loss, self.max_portfolio_worst_loss,
               self.max_exit_loss, self.max_early_exit_loss) <= 0:
            raise ValueError("loss budgets must be positive")
        if self.max_portfolio_worst_loss < self.max_market_worst_loss:
            raise ValueError("portfolio budget must cover a market budget")
        if not 0 < self.exit_depth_haircut <= 1:
            raise ValueError("exit depth haircut must be in (0, 1]")
        if self.max_book_age_seconds <= 0 or self.cancel_confirm_grace_seconds < 0:
            raise ValueError("freshness and cancel grace are invalid")
        if self.max_hard_exit_attempts <= 0:
            raise ValueError("hard-exit attempt limit must be positive")
        if min(self.max_hard_exit_retry_seconds,
               self.hard_exit_confirmation_timeout_seconds) <= 0:
            raise ValueError("hard-exit time limits must be positive")
        if not 0 < self.minimum_entry_spread < 1:
            raise ValueError("minimum entry spread must be in (0, 1)")
            raise ValueError(
                "spread hysteresis must be nonnegative and below entry spread")
        if self.force_exit_before_start_seconds <= 0:
            raise ValueError("pre-kickoff flatten deadline must be positive")
        if (not self.small_residual_min_qty.is_finite()
                or not self.small_residual_max_qty.is_finite()
                or self.small_residual_min_qty <= 0
                or self.small_residual_max_qty < self.small_residual_min_qty):
            raise ValueError("small residual quantity band is invalid")
        if (not math.isfinite(self.small_residual_timeout_seconds)
                or self.small_residual_timeout_seconds <= 0):
            raise ValueError("small residual timeout must be positive")


@dataclass(frozen=True)
class MarketFrame:
    market: MarketMetadata
    now: datetime
    book: BookSnapshot
    book_observed_at: datetime
    position: Decimal
    working_orders: tuple[WorkingOrder, ...]
    state_known: bool
    reward_eligible: frozenset[str]
    expected_net_by_side: Mapping[str, float]
    existing_portfolio_worst_loss: float
    position_opened_at: datetime | None = None
    entry_price: float | None = None
    # Account-wide permission to add exposure. False cancels flat-state
    # building quotes immediately, without passing through reward hysteresis;
    # inventory planning ignores it and therefore keeps every exit available.
    new_risk_allowed: bool = True
    # A positively scheduled production-maintenance window is an account-wide
    # flatten deadline. It changes only held-inventory timing: cancellation,
    # exact terminal proof, IOC bounds, ownership and displayed-depth checks
    # remain the ordinary hard-exit path.
    maintenance_flatten_required: bool = False

    def __post_init__(self) -> None:
        for name, value in (("now", self.now),
                            ("book_observed_at", self.book_observed_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if not self.position.is_finite():
            raise ValueError("position must be finite")


def entry_quote_price(
    book: BookSnapshot, side: str, minimum_gap: float, tick: float | None,
) -> float | None:
    """Where a *building* quote rests. Exits never use this.

    The venue publishes bids only; the offer on one outcome is one minus the
    best bid on the other. So `best_offer - best_bid` is exactly the gap an
    operator reads between the two outcomes' best bids, and the rule is stated
    in those terms: keep at least two cents in the middle.

    Join the touch whenever the book already leaves that much -- a 7c book
    quoted at 50 and 43 stays at 50 and 43, with no stepping inside. An exact
    one-tick book also joins both touches. Previously a 49/50 book kept the bid
    at 49 but moved the offer to 51, systematically putting only the offer
    behind the queue. The operator chose the symmetric 49/50 quote instead:
    it gives up one cent of possible paired spread in exchange for a better
    chance that the second side balances the first fill and for touch-level
    reward score.

    A locked 50/50 book cannot join both touches without crossing, so it still
    places the two-cent window as a unit at 49/51. The gap lands on exactly two
    cents rather than overshooting. Returns None when the two sides overlap,
    which is stale or bad data rather than a tradeable book.

    Both the runner's economics and the planner's placement call this, so the
    reward projection is computed for the price we will actually rest at.
    Those two used to disagree -- the economics scored the touch while a tight
    book was refused outright -- and a gate that lives in two places is one
    where changing a single side does nothing.
    """

    step_size = tick if tick and tick > 0 else 0.01
    spread = book.best_offer.price - book.best_bid.price
    if spread < -1e-9:
        return None
    # An exact one-tick book is the only narrow-book touch exception. Fresh
    # market selection still requires the configured two-cent spread; this
    # path governs a selected market whose live book tightens afterward. Do
    # not widen the exception to a locked book: equal touches would make the
    # two post-only orders cross one another.
    if abs(spread - step_size) <= 1e-9:
        return book.best_bid.price if side == "bid" else book.best_offer.price
    if spread + 1e-9 >= minimum_gap:
        return book.best_bid.price if side == "bid" else book.best_offer.price
    # Place the whole window at once so the gap comes out *exactly* at the
    # minimum. Backing each side off half the shortfall and rounding outward
    # gave 3c on an odd-cent book -- a 49/50 book became 48/49 when 49/49 is
    # available, giving up a tick on both sides for nothing.
    gap_ticks = math.ceil(minimum_gap / step_size - 1e-9)
    mid = (book.best_bid.price + book.best_offer.price) / 2.0
    bid_ticks = math.ceil((mid - gap_ticks * step_size / 2.0) / step_size - 1e-9)
    ticks = bid_ticks if side == "bid" else bid_ticks + gap_ticks
    # Round the grid multiplication. `41 * 0.01` is 0.41000000000000003 in
    # binary floating point, and build_order_payload validates alignment in
    # Decimal, so that price is rejected outright -- which retired the market
    # for the whole session. Nine of the ninety-nine cent prices land this way
    # (35, 41, 47, 57, 69, 70, 82, 83, 94), so roughly one backed-off quote in
    # eleven was burning a market slot. Observed live 2026-08-15T07:01Z.
    return round(ticks * step_size, 10)


def exit_quote_price(
    book: BookSnapshot, reducing_side: str, tick: float | None,
    entry_price: float | None = None,
) -> float:
    """Where a *reducing* quote rests. Entries never use this.

    Steps one tick inside the touch instead of joining it, because the exit is
    priced for the probability of leaving, not for the last cent. Joining puts
    us behind everyone already resting there; one tick inside makes us the
    touch and puts us first in the queue.

    **Only while the step still does not lose money.** Stepping inside is a
    concession, and on a position already under water it concedes in the wrong
    direction -- a long whose touch is 0.41 would sell at 0.40 and deepen the
    loss by a tick to leave sooner. So the step is taken only when the improved
    price is still at or above entry (at or below, for a short); otherwise the
    exit joins the touch exactly as before.

    That keeps this strictly an improvement. It is emphatically NOT a
    cost-basis floor: a losing exit still rests AT the touch and still follows
    it down, which is invariant 5 and is deliberate -- a floor only fills when
    the market returns to entry, so it exits winners and strands losers into
    settlement. This changes where a *winner* rests, and nothing else.

    The tick is cheap and the alternative is not. Measured 2026-08-16 over the
    whole ledger: exits that ended in a taker cross were **-$30.22 across 28
    markets**, about 76% of all real recorded losses, and the losers cluster at
    911s and 966s of hold time -- right on the 900s inventory clock. Individual
    crosses cost 8c, 13c, 31c per contract. One tick on five contracts is
    $0.05, so this has to fail ten to thirty times to cost what a single
    avoided cross saves.

    It does not cost reward either. Score is `discountFactor ^ ticks * size`,
    penalising distance from the touch -- and stepping inside makes us the
    touch, at zero ticks, rather than resting behind a queue at the same price.

    Never prices through the opposite touch: a post-only order that would match
    is rejected outright, so when there is no room the exit simply joins the
    touch as before. A one-sided book has no opposite touch to respect, and
    joins for the same reason.
    """

    step = tick if tick and tick > 0 else 0.01
    if reducing_side == "offer":
        touch = book.best_offer.price
        if not book.has_side("bid"):
            return touch
        # Step in tick space, never by subtracting floats. `41 * 0.01` is
        # 0.41000000000000003 in binary floating point and the payload
        # validates alignment in Decimal, so an unrounded price is rejected
        # and retires the market for the session -- see `entry_quote_price`.
        improved = round((round(touch / step) - 1) * step, 10)
        if entry_price is not None and improved < entry_price - 1e-9:
            return touch
        if improved - book.best_bid.price > 1e-9:
            return improved
        return touch
    if reducing_side == "bid":
        touch = book.best_bid.price
        if not book.has_side("offer"):
            return touch
        improved = round((round(touch / step) + 1) * step, 10)
        if entry_price is not None and improved > entry_price + 1e-9:
            return touch
        if book.best_offer.price - improved > 1e-9:
            return improved
        return touch
    raise ValueError("reducing_side must be 'bid' or 'offer'")


@dataclass
class DesiredStateReconciler:
    policy: ReconcilerPolicy
    _ineligible_since: dict[tuple[str, str], datetime] = field(
        default_factory=dict)
    _hard_exit_cancel_at: dict[str, datetime] = field(default_factory=dict)
    _hard_exit_cancel_order_id: dict[str, str] = field(default_factory=dict)
    _hard_exit_sent: dict[str, datetime] = field(default_factory=dict)
    _hard_exit_attempts: dict[str, int] = field(default_factory=dict)
    _hard_exit_started_at: dict[str, datetime] = field(default_factory=dict)

    @staticmethod
    def _reducing_side(position: Decimal) -> str:
        return "offer" if position > 0 else "bid"

    @staticmethod
    def _touch(book: BookSnapshot, side: str) -> float:
        return book.best_bid.price if side == "bid" else book.best_offer.price

    def _entry_price(
        self, book: BookSnapshot, side: str, tick: float | None,
    ) -> float | None:
        return entry_quote_price(
            book, side, self.policy.minimum_entry_spread, tick)

    @staticmethod
    def _matches(order: WorkingOrder, *, price: float, qty: Decimal) -> bool:
        return abs(order.price - price) < 1e-9 and order.remaining_qty == qty

    def confirm_hard_exit_terminal(self, market_slug: str) -> None:
        """Permit another exact-position exit after venue terminal proof.

        The sent marker prevents duplicate IOC submissions while the first
        mutation is still ambiguous. The live runner may clear it only after
        REST/private reconciliation proves that IOC terminal and no order is
        still working. If inventory remains (for example after a partial or
        expired IOC), the next plan can safely price a new exact-position exit.
        """

        self._hard_exit_sent.pop(str(market_slug), None)

    def hard_exit_attempts(self, market_slug: str) -> int:
        return self._hard_exit_attempts.get(str(market_slug), 0)

    def hard_exit_confirmation_expired(
        self, market_slug: str, *, now: datetime,
    ) -> bool:
        sent_at = self._hard_exit_sent.get(str(market_slug))
        if sent_at is None:
            return False
        return (now - sent_at).total_seconds() >= (
            self.policy.hard_exit_confirmation_timeout_seconds)

    def next_reward_eligibility_deadline(
        self, market_slug: str, *, resting_sides: frozenset[str] | None = None,
    ) -> datetime | None:
        """Return the next time an ineligible resting side must be revisited.

        The eligibility dwell is owned here, beside the exact timestamps used
        by ``_flat_plan``. Exposing its deadline lets the runner skip unchanged
        flat markets without duplicating or changing this strategy rule in the
        scheduler.
        """

        deadlines = [
            observed_at + timedelta(
                seconds=self.policy.reward_eligibility_dwell_seconds)
            for (slug, side), observed_at in self._ineligible_since.items()
            if slug == str(market_slug)
            and (resting_sides is None or side in resting_sides)
        ]
        return min(deadlines, default=None)

    def note_hard_exit_cancel_submitted(
        self, market_slug: str, order_id: str, *, submitted_at: datetime,
    ) -> None:
        """Start cancel-confirmation grace for the exact submitted order.

        Planning is not proof that a request crossed the network boundary: a
        local admission delay can stop it before the driver creates an intent.
        The live runner calls this only after the driver returns a cancel
        intent, so a later plan can distinguish "not sent yet" from "sent and
        awaiting positive terminal proof" without retrying an ambiguous
        mutation.
        """

        slug = str(market_slug)
        self._hard_exit_cancel_order_id[slug] = str(order_id)
        self._hard_exit_cancel_at[slug] = submitted_at

    def _clear_hard_exit_cancel(self, market_slug: str) -> None:
        slug = str(market_slug)
        self._hard_exit_cancel_at.pop(slug, None)
        self._hard_exit_cancel_order_id.pop(slug, None)

    def restart_hard_exit_budget(self, market_slug: str) -> None:
        """Give a retired market a fresh exit budget on a slower cadence.

        The attempt and window limits stop a fast retry loop from hammering the
        venue. They are not a verdict that the position can never leave: a book
        too thin now can refill, and the pre-kickoff flatten is the deadline
        that actually matters. Clearing them lets the exit be retried after the
        operator has been told, instead of ending the session with the position
        still open and every other market still healthy.
        """

        self._clear_hard_exit(market_slug)

    def _clear_hard_exit(self, market_slug: str) -> None:
        slug = str(market_slug)
        self._clear_hard_exit_cancel(slug)
        self._hard_exit_sent.pop(slug, None)
        self._hard_exit_attempts.pop(slug, None)
        self._hard_exit_started_at.pop(slug, None)

    def _critical(self, frame: MarketFrame, reason: str) -> list[PlanAction]:
        return [PlanAction(ActionKind.CRITICAL, frame.market.slug, reason)]

    def plan(self, frame: MarketFrame) -> list[PlanAction]:
        slug = frame.market.slug
        # Removing known flat-state exposure needs neither a fresh book nor
        # fresh private state.  UI/operator maintenance can begin while a
        # market stream is quiet; waiting for book recovery before cancelling
        # would leave the very entry quotes the pause exists to withdraw.
        # Ownership was already proved when WorkingOrder was constructed, and
        # CANCEL cannot add inventory. The one-mutation driver still sequences
        # these through durable intents and exact terminal reconciliation.
        if (frame.position == 0 and not frame.new_risk_allowed
                and frame.working_orders):
            return [PlanAction(
                ActionKind.CANCEL, slug,
                "side_no_longer_desired:account_new_risk_paused",
                side=order.side, order_id=order.order_id)
                for order in frame.working_orders]
        if not frame.state_known:
            return self._critical(frame, "execution_state_unknown")
        if frame.book.slug != slug:
            return self._critical(frame, "book_market_mismatch")
        age = (frame.now - frame.book_observed_at).total_seconds()
        if age < 0 or age > self.policy.max_book_age_seconds:
            return self._critical(frame, "book_stale")
        if frame.position == 0:
            self._clear_hard_exit(slug)
            # Quoting needs BOTH touches -- the two-cent rule is defined
            # between them -- so a one-sided book simply has nothing to quote.
            # Nothing is wrong, there is just no work.
            if not frame.book.is_two_sided:
                return []
            return self._flat_plan(frame)
        # Holding. Exiting a long needs only the bid side to sell into, a short
        # only the offer side, so a one-sided book is usually still enough.
        # Requiring both here is what stranded positions until they settled.
        return self._inventory_plan(frame)

    def _inventory_plan(self, frame: MarketFrame) -> list[PlanAction]:
        slug = frame.market.slug
        if frame.position_opened_at is None or frame.entry_price is None:
            return self._critical(frame, "inventory_basis_or_clock_unknown")
        if frame.position_opened_at > frame.now:
            return self._critical(frame, "inventory_clock_in_future")
        qty = abs(frame.position)
        reducing = self._reducing_side(frame.position)
        building = "bid" if reducing == "offer" else "offer"
        inventory_age = (frame.now - frame.position_opened_at).total_seconds()
        deadline = self.policy.max_inventory_age_seconds

        sent_at = self._hard_exit_sent.get(slug)
        if sent_at is not None:
            return self._critical(frame, "hard_exit_sent_but_inventory_remains")

        # Kickoff is an absolute deadline that overrides the inventory clock.
        # maker-lab measured holding as ~3.13c/contract cheaper than crossing
        # and therefore disabled its forced exit entirely; the residue that
        # policy left behind reached settlement and cost $88.20, 86% of that
        # session's loss. Waiting for the free passive exit is right, but never
        # past the point where the position can settle.
        seconds_to_event = (
            (frame.market.game_start - frame.now).total_seconds()
            if frame.market.game_start is not None else None)
        pre_kickoff_due = (
            seconds_to_event is not None
            and seconds_to_event <= self.policy.force_exit_before_start_seconds)
        maintenance_due = frame.maintenance_flatten_required
        # Use the durable position-open clock. An initially tiny partial fill
        # gets the full grace period; a position already held for five minutes
        # that later shrinks into the tiny band exits the remainder at once
        # instead of spending another five minutes of slot capacity.
        venue_minimum = (
            Decimal(str(frame.market.minimum_trade_qty))
            if frame.market.minimum_trade_qty is not None else None)
        small_residual_exactly_tradable = (
            venue_minimum is not None
            and venue_minimum > 0
            and qty >= venue_minimum
            and qty % venue_minimum == 0)
        small_residual_due = (
            self.policy.small_residual_min_qty
            <= qty <= self.policy.small_residual_max_qty
            and small_residual_exactly_tradable
            and inventory_age >= self.policy.small_residual_timeout_seconds)
        normal_exit_due = (
            inventory_age >= deadline or pre_kickoff_due or maintenance_due)

        if normal_exit_due or small_residual_due:
            if frame.working_orders:
                # The grace measures how long OUR CANCEL has been outstanding,
                # not how long we are past the deadline. Measuring from the
                # deadline made this permanently critical: `overdue` only
                # grows, so once it passed the grace every pass returned the
                # critical *instead of* issuing the cancel, and a cancel that
                # is never re-sent can never confirm. Observed 2026-08-15 on
                # tkamak-tsokos -- ten retirement rounds over ten minutes, the
                # position still open when a deploy landed, and a break-even
                # exit turned into -$1.55.
                # The driver applies at most ONE mutation from a plan. Returning
                # every cancel here made the first tuple member authoritative,
                # then started one slug-level grace clock as though every order
                # had been cancelled. With a long position and (offer, bid),
                # the reducer was cancelled while the untouched bid remained
                # able to increase the position. Track the exact submitted
                # order and wait for its positive terminal proof before
                # selecting the next one. Cancel the position-building side
                # first so exposure cannot grow while reducers are cleared.
                target_id = self._hard_exit_cancel_order_id.get(slug)
                if target_id is not None:
                    target_still_working = any(
                        order.order_id == target_id
                        for order in frame.working_orders)
                    if target_still_working:
                        issued_at = self._hard_exit_cancel_at.get(slug)
                        if (issued_at is not None
                                and (frame.now - issued_at).total_seconds()
                                > self.policy.cancel_confirm_grace_seconds):
                            return self._critical(
                                frame,
                                "hard_exit_blocked_cancel_unconfirmed")
                        return []
                    # The runner does not plan a market with an unresolved
                    # intent. Therefore a tracked order disappearing here is
                    # backed by terminal ledger proof, not snapshot absence.
                    self._clear_hard_exit_cancel(slug)

                target = next(
                    (order for order in frame.working_orders
                     if order.side == building),
                    frame.working_orders[0],
                )
                return [PlanAction(
                    ActionKind.CANCEL, slug, "hard_timeout_cancel_confirm_first",
                    side=target.side, order_id=target.order_id)]
            self._clear_hard_exit_cancel(slug)
            attempts = self._hard_exit_attempts.get(slug, 0)
            started_at = self._hard_exit_started_at.get(slug)
            if attempts >= self.policy.max_hard_exit_attempts:
                return self._critical(frame, "hard_exit_attempt_limit_exceeded")
            if (started_at is not None
                    and (frame.now - started_at).total_seconds()
                    >= self.policy.max_hard_exit_retry_seconds):
                return self._critical(frame, "hard_exit_retry_window_exceeded")
            entry_side = "bid" if frame.position > 0 else "offer"
            exit_result = cross_exit(
                frame.book, entry_side=entry_side,
                entry_price=frame.entry_price, qty=float(qty),
                depth_haircut=self.policy.exit_depth_haircut,
                taker_theta=self.policy.taker_theta)
            if ((not exit_result.complete or exit_result.worst_price is None)
                    and (pre_kickoff_due or small_residual_due
                         or maintenance_due)):
                # The stress haircut exists to pick a price we could still get
                # if half the displayed depth vanished. At the kickoff deadline
                # the alternative is no longer a better fill later, it is
                # settlement: maker-lab's residue settled at -$5.51 per event
                # while a crossing costs cents. Re-price against displayed
                # depth rather than refuse and hold into the game. A tiny
                # residual gets the same displayed-depth attempt because its
                # entire maximum settlement exposure is nine cents; if even
                # displayed depth cannot fill it, the ordinary recoverable
                # thin-book path below keeps the reducer working.
                exit_result = cross_exit(
                    frame.book, entry_side=entry_side,
                    entry_price=frame.entry_price, qty=float(qty),
                    depth_haircut=1.0,
                    taker_theta=self.policy.taker_theta)
            if not exit_result.complete or exit_result.worst_price is None:
                if not pre_kickoff_due and not maintenance_due:
                    # A book too thin to absorb the exit is not a reason to end
                    # the session. The inventory clock is a preference for
                    # leaving early; the pre-kickoff flatten is the deadline
                    # that actually matters, and it re-prices against displayed
                    # depth above. Keep holding, keep the passive reducer
                    # working, and retry when the book refills. Measured: at
                    # one contract this fires whenever exit-side depth is under
                    # 2.0, and the observed universe contains live books with
                    # exit-side depth of 1.0 (FINDINGS section 5).
                    return self._critical(frame, "hard_exit_deferred_thin_book")
                return self._critical(frame, "hard_exit_insufficient_stressed_depth")
            if (exit_result.pnl < -self.policy.max_early_exit_loss
                    and not pre_kickoff_due and not maintenance_due):
                # Before the kickoff deadline a too-expensive exit can wait for
                # a better book. At the deadline it cannot: refusing here holds
                # the position into the game, and settlement is unbounded up to
                # the full contract value while the cap is a policy choice.
                return self._critical(frame, "hard_exit_price_cap_exceeded")
            self._hard_exit_started_at.setdefault(slug, frame.now)
            self._hard_exit_attempts[slug] = attempts + 1
            self._hard_exit_sent[slug] = frame.now
            if maintenance_due:
                reason = "scheduled_maintenance_evacuation"
            elif small_residual_due and not normal_exit_due:
                reason = "tiny_residual_timeout"
            else:
                reason = "hard_inventory_timeout"
            return [PlanAction(
                ActionKind.PLACE_IOC, slug, reason,
                side=reducing, price=exit_result.worst_price, qty=qty)]

        # Immediately pull every order that can add to the filled direction.
        actions = [PlanAction(
            ActionKind.CANCEL, slug, "cancel_building_after_fill",
            side=order.side, order_id=order.order_id)
            for order in frame.working_orders if order.side == building]
        # At the touch, with NO cost-basis floor. This is invariant 5, and the
        # code contradicted it.
        #
        # A floor sounds protective and is the opposite. It can only fill when
        # the market comes back to your entry, so it exits winners and strands
        # losers: nobody buys at 0.39 while the market is 0.25. Losing
        # positions are then forced into the expensive paths -- crossing the
        # spread and paying taker fees, or riding to settlement.
        #
        # maker-lab ran a floor and measured the result on 101 exits: passive
        # 60 winners against 1 loser, settlement 2 winners against 14. They
        # documented the settlement pool themselves as "79% losers BY
        # CONSTRUCTION". The floor does not avoid losses, it defers and
        # concentrates them -- $88.20 into settlement in one session, 86% of
        # that session's loss. At larger scale 234 settled positions cost
        # $443.88 where crossing at T-15 would have cost $64.18: a $379.70
        # swing with 78% of positions individually better off.
        #
        # Without the floor a loser can leave passively at the touch, earning
        # the maker rebate instead of paying a taker fee, and early instead of
        # at a binary. The hard timeout remains the only path that pays for an
        # emergency flatten.
        # A passive reducer JOINS the reducing side, so it needs that side to
        # exist. With it empty there is no touch to rest at -- but the hard
        # exit above has already had its turn, and it crosses into the OTHER
        # side, which is exactly the side a one-sided book still carries. That
        # is the asymmetry that matters: the deadline exit keeps working even
        # when the passive one cannot be priced, so the position is no longer
        # stranded until settlement.
        if not frame.book.has_side(reducing):
            return [PlanAction(
                ActionKind.CANCEL, slug, "cancel_building_after_fill",
                side=order.side, order_id=order.order_id)
                for order in frame.working_orders if order.side == building]
        passive_price = exit_quote_price(
            frame.book, reducing, frame.market.tick_size, frame.entry_price)
        reducing_orders = [order for order in frame.working_orders
                           if order.side == reducing]
        matching = [order for order in reducing_orders
                    if self._matches(order, price=passive_price, qty=qty)]
        keep_id = matching[0].order_id if matching else None
        wrong = [order for order in reducing_orders
                 if order.order_id != keep_id]
        actions.extend(PlanAction(
            ActionKind.CANCEL, slug, "replace_reducing_at_active_touch",
            side=order.side, order_id=order.order_id) for order in wrong)
        if actions:
            return actions
        if keep_id is None:
            return [PlanAction(
                ActionKind.PLACE_POST_ONLY, slug,
                "soft_reducing_quote_at_active_touch",
                side=reducing, price=passive_price, qty=qty)]
        return []

    def _flat_plan(self, frame: MarketFrame) -> list[PlanAction]:
        slug = frame.market.slug
        # Not merely "before kickoff" -- before the flatten deadline.
        #
        # Entry used to gate on `now < game_start`, so the bot opened quotes
        # inside the same window `_inventory_plan` has already committed to
        # flattening at any price, with no loss cap and no depth haircut. A
        # fill there is force-crossed almost immediately, which makes it a
        # position taken on purpose and closed at a taker price on purpose.
        # Measured 2026-08-16: pre-kickoff crossings were the worst exit group
        # at -0.075/contract median (FINDINGS), and 8 of 9 markets resting at
        # the time started within fifteen minutes, one of them within 3.6.
        #
        # Emptying `desired_sides` also cancels anything already resting, so
        # the bot withdraws at the deadline rather than waiting to be filled
        # into it. Held positions are unaffected: this path only runs flat.
        seconds_to_event = (
            (frame.market.game_start - frame.now).total_seconds()
            if frame.market.game_start is not None else None)
        market_open = (frame.market.active and not frame.market.closed
                       and seconds_to_event is not None
                       and seconds_to_event
                       > self.policy.force_exit_before_start_seconds)
        # Why each side stopped being wanted, so the cancel can name it. A
        # single `side_no_longer_desired` could not be attributed from the
        # logs -- the 2026-08-14 churn was never diagnosed for exactly this
        # reason, and on 2026-08-19 the first guess (the stressed-exit gate)
        # was wrong: the reason arrived bare, meaning the side had already
        # been dropped further up. Every removal now records its cause.
        drop_cause: dict[str, str] = {}
        desired_sides = ({"bid", "offer"} if market_open else set())
        if not frame.new_risk_allowed:
            for side in desired_sides:
                drop_cause[side] = "account_new_risk_paused"
            desired_sides.clear()
        if not frame.market.is_supported_moneyline:
            desired_sides.clear()
        resting_sides = {order.side for order in frame.working_orders}
        for side in tuple(desired_sides):
            if side in frame.reward_eligible:
                self._ineligible_since.pop((slug, side), None)
                continue
            if side not in resting_sides:
                # Nothing resting yet, so there is no accrual to protect.
                desired_sides.discard(side)
                continue
            since = self._ineligible_since.setdefault((slug, side), frame.now)
            if (frame.now - since).total_seconds() >= (
                    self.policy.reward_eligibility_dwell_seconds):
                drop_cause[side] = "reward_ineligible"
                desired_sides.discard(side)
        # "Not evaluated" is not "uneconomic". `clear_economics()` wipes
        # `expected_net_by_side` before every pass, so a side the runner
        # skipped this pass -- an overlapping book, or any early return out of
        # `_evaluate_rewards` -- simply has no entry. Reading that absence
        # through `.get(side, 0.0)` turned it into a hard zero and cancelled
        # the resting order.
        #
        # That defeated the dwell. `reward_eligibility_dwell_seconds` (90s)
        # exists so a side that momentarily stops qualifying keeps its quote
        # rather than flapping; a side inside that grace was killed here
        # instead, by a dictionary default. Measured 2026-08-19: mean resting
        # fraction 57.3% across 179 sides, with the same side re-placed at the
        # same price every 2-3 minutes.
        #
        # Absence is safe to skip because the two real reasons are caught
        # elsewhere and by their own names: an overlapping book fails
        # `_entry_price` below and drops as `no_entry_price`, and a side that
        # stays unqualified is dropped by the eligibility dwell above. A side
        # that WAS evaluated and priced at or below zero still drops here.
        for side in tuple(desired_sides):
            if side not in frame.expected_net_by_side:
                continue
            value = frame.expected_net_by_side[side]
            if not math.isfinite(value):
                drop_cause[side] = "expected_net_not_finite"
                desired_sides.discard(side)
            elif value <= 0:
                drop_cause[side] = "expected_net_not_positive"
                desired_sides.discard(side)

        # A tight book is priced, not refused. An exact one-tick book joins
        # both touches; a locked book backs off until two cents sit in the
        # middle. Only an overlapping book -- the two outcomes' best bids
        # summing past one -- drops the side, because that is stale or bad data
        # rather than a tradeable market. Inventory reduction is handled
        # exclusively by _inventory_plan and is never spread-gated.
        tick = frame.market.tick_size
        entry_prices = {
            side: self._entry_price(frame.book, side, tick)
            for side in tuple(desired_sides)}
        for side, price in entry_prices.items():
            if price is None:
                drop_cause[side] = "no_entry_price"
                desired_sides.remove(side)

        # Refuse to OPEN near either end of the book. Nothing here touches
        # exits -- invariant 42 keeps reduction and the bounded hard exit
        # available at every price, so this gate can never strand inventory.
        #
        # Measured 2026-08-21 across 321 markets, bucketed by the most extreme
        # price each one traded at, in the frame we actually paid in:
        #
        #     0.00-0.05    8 markets   -13.65   -1.71 each
        #     0.05-0.10   28 markets   -41.05   -1.47 each
        #     0.10-0.15   26 markets   -21.41   -0.82 each
        #     0.15-0.25   45 markets   -20.61   -0.46 each
        #     0.25-0.50  214 markets   -38.28   -0.18 each
        #
        # Loss per market rises about 9x toward the ends, monotonically across
        # all five buckets. Everything below 0.10 is 11% of markets and 40% of
        # all losses. Section 1 says the same thing from the fee side: the
        # maker rebate is 0.0125 x qty x p x (1-p), so it peaks at 0.50 and at
        # p=0.07 pays about a quarter of that -- least reward exactly where the
        # adverse selection is worst.
        #
        # The trade that prompted this: 2026-08-21, a CS2 match whose venue
        # `gameStartTime` had not yet been corrected, so a LIVE game sat in the
        # pregame universe. The bot chased the touch down 0.19 -> 0.07 as one
        # side collapsed, filled 10 at 0.07 on the side that lost, and the
        # market resolved minutes later for -$9.29. The clock could not have
        # saved it -- the clock was the thing that was wrong -- but this gate
        # would have refused the fill on its own economics.
        floor = self.policy.minimum_entry_price
        for side in tuple(desired_sides):
            price = entry_prices[side]
            # `price` is quoted in this side's own frame, and the opposite side
            # pays 1 - price, so one comparison covers both ends.
            if min(price, 1.0 - price) < floor - 1e-9:
                drop_cause[side] = "price_too_extreme"
                desired_sides.remove(side)

        quote_size = self.policy.quote_size
        candidate_loss = 0.0
        if "bid" in desired_sides:
            candidate_loss += float(quote_size) * frame.book.best_bid.price
        if "offer" in desired_sides:
            candidate_loss += float(quote_size) * (1.0 - frame.book.best_offer.price)
        if (candidate_loss > self.policy.max_market_worst_loss + 1e-9
                or frame.existing_portfolio_worst_loss + candidate_loss
                > self.policy.max_portfolio_worst_loss + 1e-9):
            for side in desired_sides:
                drop_cause[side] = "worst_loss_budget"
            desired_sides.clear()
            risk_halt = True
        else:
            risk_halt = False

        # Require a stressed complete close before either side can build risk.
        for side in tuple(desired_sides):
            result = cross_exit(
                frame.book, entry_side=side,
                entry_price=entry_prices[side],
                qty=float(quote_size),
                depth_haircut=self.policy.exit_depth_haircut,
                taker_theta=self.policy.taker_theta,
                exclude_entry_level=True)
            if not result.complete:
                drop_cause[side] = "exit_incomplete"
                desired_sides.remove(side)
            elif result.pnl < -self.policy.max_exit_loss:
                drop_cause[side] = "exit_loss"
                desired_sides.remove(side)

        actions: list[PlanAction] = []
        kept_sides: set[str] = set()
        for order in frame.working_orders:
            desired_price = entry_prices.get(order.side)
            # Three very different faults used to share one reason string, so
            # the live place/cancel churn of 2026-08-14 could not be attributed
            # from the logs: a side going uneconomic, the touch moving away,
            # and a duplicate on one side are not the same problem.
            if order.side in kept_sides:
                reason = "duplicate_order_on_side"
            elif order.side not in desired_sides:
                cause = drop_cause.get(order.side)
                reason = ("side_no_longer_desired:" + cause if cause
                          else "side_no_longer_desired")
            elif not self._matches(order, price=desired_price, qty=quote_size):
                reason = "resting_price_left_the_touch"
            else:
                kept_sides.add(order.side)
                continue
            actions.append(PlanAction(
                ActionKind.CANCEL, slug, reason,
                side=order.side, order_id=order.order_id))
        if actions:
            return actions
        if risk_halt:
            return [PlanAction(ActionKind.HALT_NEW_RISK, slug,
                               "worst_case_loss_budget")]
        existing = kept_sides
        return [PlanAction(
            ActionKind.PLACE_POST_ONLY, slug, "reward_positive_candidate",
            side=side, price=entry_prices[side], qty=quote_size)
            for side in sorted(desired_sides - existing)]
