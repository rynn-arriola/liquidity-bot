"""Pure desired-state reconciler used by Phase 4 dry runs.

It returns actions but performs no API request. The hard inventory timeout is
required input and deliberately has no default selected for live use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    exit_depth_haircut: float = 0.5
    taker_theta: float = 0.06
    max_book_age_seconds: float = 5.0
    cancel_confirm_grace_seconds: float = 5.0
    max_hard_exit_attempts: int = 3
    max_hard_exit_retry_seconds: float = 30.0
    hard_exit_confirmation_timeout_seconds: float = 10.0
    minimum_entry_spread: float = 0.02
    spread_hysteresis: float = 0.005
    force_exit_before_start_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.quote_size <= 0 or self.max_inventory_age_seconds <= 0:
            raise ValueError("quote size and inventory age must be positive")
        if min(self.max_market_worst_loss, self.max_portfolio_worst_loss,
               self.max_exit_loss) <= 0:
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
        if (self.spread_hysteresis < 0
                or self.spread_hysteresis >= self.minimum_entry_spread):
            raise ValueError(
                "spread hysteresis must be nonnegative and below entry spread")
        if self.force_exit_before_start_seconds <= 0:
            raise ValueError("pre-kickoff flatten deadline must be positive")


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

    def __post_init__(self) -> None:
        for name, value in (("now", self.now),
                            ("book_observed_at", self.book_observed_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if not self.position.is_finite():
            raise ValueError("position must be finite")


@dataclass
class DesiredStateReconciler:
    policy: ReconcilerPolicy
    _hard_exit_sent: dict[str, datetime] = field(default_factory=dict)
    _hard_exit_attempts: dict[str, int] = field(default_factory=dict)
    _hard_exit_started_at: dict[str, datetime] = field(default_factory=dict)

    @staticmethod
    def _reducing_side(position: Decimal) -> str:
        return "offer" if position > 0 else "bid"

    @staticmethod
    def _touch(book: BookSnapshot, side: str) -> float:
        return book.best_bid.price if side == "bid" else book.best_offer.price

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
        self._hard_exit_sent.pop(slug, None)
        self._hard_exit_attempts.pop(slug, None)
        self._hard_exit_started_at.pop(slug, None)

    def _critical(self, frame: MarketFrame, reason: str) -> list[PlanAction]:
        return [PlanAction(ActionKind.CRITICAL, frame.market.slug, reason)]

    def plan(self, frame: MarketFrame) -> list[PlanAction]:
        slug = frame.market.slug
        if not frame.state_known:
            return self._critical(frame, "execution_state_unknown")
        if frame.book.slug != slug:
            return self._critical(frame, "book_market_mismatch")
        age = (frame.now - frame.book_observed_at).total_seconds()
        if age < 0 or age > self.policy.max_book_age_seconds:
            return self._critical(frame, "book_stale")
        if frame.position == 0:
            self._clear_hard_exit(slug)
            return self._flat_plan(frame)
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

        if inventory_age >= deadline or pre_kickoff_due:
            if frame.working_orders:
                # Measure lateness against whichever deadline fired, so a
                # kickoff-driven exit cannot sit forever waiting on a cancel.
                overdue = inventory_age - deadline
                if seconds_to_event is not None:
                    overdue = max(
                        overdue,
                        self.policy.force_exit_before_start_seconds
                        - seconds_to_event)
                if overdue > self.policy.cancel_confirm_grace_seconds:
                    return self._critical(
                        frame, "hard_exit_blocked_cancel_unconfirmed")
                return [PlanAction(
                    ActionKind.CANCEL, slug, "hard_timeout_cancel_confirm_first",
                    side=order.side, order_id=order.order_id)
                    for order in frame.working_orders]
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
                    and pre_kickoff_due):
                # The stress haircut exists to pick a price we could still get
                # if half the displayed depth vanished. At the kickoff deadline
                # the alternative is no longer a better fill later, it is
                # settlement: maker-lab's residue settled at -$5.51 per event
                # while a crossing costs cents. Re-price against displayed
                # depth rather than refuse and hold into the game.
                exit_result = cross_exit(
                    frame.book, entry_side=entry_side,
                    entry_price=frame.entry_price, qty=float(qty),
                    depth_haircut=1.0,
                    taker_theta=self.policy.taker_theta)
            if not exit_result.complete or exit_result.worst_price is None:
                if not pre_kickoff_due:
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
            if (exit_result.pnl < -self.policy.max_exit_loss
                    and not pre_kickoff_due):
                # Before the kickoff deadline a too-expensive exit can wait for
                # a better book. At the deadline it cannot: refusing here holds
                # the position into the game, and settlement is unbounded up to
                # the full contract value while the cap is a policy choice.
                return self._critical(frame, "hard_exit_price_cap_exceeded")
            self._hard_exit_started_at.setdefault(slug, frame.now)
            self._hard_exit_attempts[slug] = attempts + 1
            self._hard_exit_sent[slug] = frame.now
            return [PlanAction(
                ActionKind.PLACE_IOC, slug, "hard_inventory_timeout",
                side=reducing, price=exit_result.worst_price, qty=qty)]

        # Immediately pull every order that can add to the filled direction.
        actions = [PlanAction(
            ActionKind.CANCEL, slug, "cancel_building_after_fill",
            side=order.side, order_id=order.order_id)
            for order in frame.working_orders if order.side == building]
        active_touch = self._touch(frame.book, reducing)
        # A passive reducer is allowed to wait, so it must not lock in a loss.
        # Long inventory sells no lower than basis; short inventory buys back
        # no higher than basis.  The hard timeout remains the only path allowed
        # to pay for an emergency flatten, and the live commissioning runner
        # terminates the market after such an exit rather than immediately
        # beginning another fee-paying cycle.
        passive_price = (
            max(active_touch, frame.entry_price)
            if frame.position > 0
            else min(active_touch, frame.entry_price)
        )
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
        market_open = (frame.market.active and not frame.market.closed
                       and frame.market.game_start is not None
                       and frame.now < frame.market.game_start)
        desired_sides = ({"bid", "offer"} if market_open else set())
        if not frame.market.is_supported_moneyline:
            desired_sides.clear()
        desired_sides &= set(frame.reward_eligible)
        desired_sides = {
            side for side in desired_sides
            if math.isfinite(frame.expected_net_by_side.get(side, 0.0))
            and frame.expected_net_by_side.get(side, 0.0) > 0}

        # New building quotes require the measured 2-cent regime. Resting
        # quotes are retained down to 1.5 cents to prevent touch-noise churn,
        # then pulled if the book tightens further. Inventory reduction is
        # handled exclusively by _inventory_plan and is never spread-gated.
        spread = frame.book.best_offer.price - frame.book.best_bid.price
        resting_sides = {order.side for order in frame.working_orders}
        for side in tuple(desired_sides):
            threshold = self.policy.minimum_entry_spread
            if side in resting_sides:
                threshold -= self.policy.spread_hysteresis
            if spread + 1e-9 < threshold:
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
            desired_sides.clear()
            risk_halt = True
        else:
            risk_halt = False

        # Require a stressed complete close before either side can build risk.
        for side in tuple(desired_sides):
            result = cross_exit(
                frame.book, entry_side=side,
                entry_price=self._touch(frame.book, side),
                qty=float(quote_size),
                depth_haircut=self.policy.exit_depth_haircut,
                taker_theta=self.policy.taker_theta,
                exclude_entry_level=True)
            if not result.complete or result.pnl < -self.policy.max_exit_loss:
                desired_sides.remove(side)

        actions: list[PlanAction] = []
        kept_sides: set[str] = set()
        for order in frame.working_orders:
            desired_price = self._touch(frame.book, order.side)
            # Three very different faults used to share one reason string, so
            # the live place/cancel churn of 2026-08-14 could not be attributed
            # from the logs: a side going uneconomic, the touch moving away,
            # and a duplicate on one side are not the same problem.
            if order.side in kept_sides:
                reason = "duplicate_order_on_side"
            elif order.side not in desired_sides:
                reason = "side_no_longer_desired"
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
            side=side, price=self._touch(frame.book, side), qty=quote_size)
            for side in sorted(desired_sides - existing)]
