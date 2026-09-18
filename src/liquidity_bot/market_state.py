"""Per-market runtime state for a multi-market commissioning session.

The runner was built around a single bound market: one metadata record, one
book, one set of per-side economics. Quoting several markets at once needs one
of these per slug, held in a dict, while genuinely session-wide state (marked
equity, the risk guard and score-seconds) stays single.

Nothing here reaches the network or the ledger. It is a container plus the
small amount of freshness logic that belongs with the data it guards, so the
runner can ask a market whether its book is stale without reaching inside.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import BookSnapshot, MarketIncentive, MarketMetadata
from .rewards import RewardProjection, RewardScore
from .risk import OpportunityEconomics
from .shadow_math import ExitResult


@dataclass
class RetiredMarket:
    """A market this session has stopped quoting but must still flatten.

    Retirement is the per-market answer to a failed exit. The market keeps its
    streams, its book refresh and its reconciler so the position keeps being
    worked, but no side of it may ever be quoted again this session. The slot
    is released once the venue proves it flat.
    """

    slug: str
    reason: str
    retired_at: datetime
    rounds: int = 1
    # Retirement resets the exit budget, so without a cooldown the same
    # condition re-fires on the very next pass and the runner spins.
    next_retry_at: datetime | None = None


@dataclass
class MarketState:
    """Everything the runner knows about one bound market."""

    slug: str
    market: MarketMetadata | None = None
    incentive: MarketIncentive | None = None
    book: BookSnapshot | None = None
    book_observed_at: datetime | None = None
    reward_eligible: frozenset[str] = frozenset()
    score_by_side: dict[str, float] = field(default_factory=dict)
    expected_net_by_side: dict[str, float] = field(default_factory=dict)
    reward_projection_by_side: dict[str, RewardProjection] = field(
        default_factory=dict)
    economic_blocker_by_side: dict[str, str] = field(default_factory=dict)
    fill_probability_by_side: dict[str, float] = field(default_factory=dict)
    fill_risk_driver_by_side: dict[str, str] = field(default_factory=dict)
    # Decision-time inputs retained only long enough to write an immutable
    # building-quote observation.  They are never read by the reconciler.
    reward_score_by_side: dict[str, RewardScore] = field(default_factory=dict)
    stressed_exit_by_side: dict[str, ExitResult] = field(default_factory=dict)
    opportunity_economics_by_side: dict[str, OpportunityEconomics] = field(
        default_factory=dict)
    model_queue_ahead_by_side: dict[str, float] = field(default_factory=dict)
    # A bounded stream of BBO CHANGE times, not every book message.  This
    # measures touch velocity without increasing public subscriptions or REST
    # work, and has no scheduler/strategy consumer.
    touch_change_times: deque[datetime] = field(default_factory=deque)
    last_touch: tuple[float | None, float | None] | None = None
    last_touch_changed_at: datetime | None = None
    last_touch_observed_at: datetime | None = None
    # Each market keeps its own REST cadence so one quiet book cannot starve
    # the others through the shared request governor.
    next_book_refresh: datetime | None = None
    next_contract_refresh: datetime | None = None
    # The exact reward program that last authorized entry for this slot.
    # Replacement after reward expiry is allowed only when a later complete
    # incentive response contains this same id with an explicit elapsed end.
    reward_program_id: str | None = None
    reward_program_ends_at: datetime | None = None
    reward_period_ended_at: datetime | None = None
    reward_period_end_confirmed_at: datetime | None = None
    # A reward can stop authorizing this market without the program itself
    # ending.  The venue may postpone a game out of the six-hour day-of window
    # while continuing to publish the same active day-of program with no
    # ``ends_at``.  Keep that positive observation separate from an explicit
    # program end so the scheduler can release the idle slot without claiming
    # the reward program ended.
    reward_authorization_lost_at: datetime | None = None
    reward_authorization_loss_reason: str | None = None
    reward_authorization_loss_confirmed_at: datetime | None = None
    # A current-book economic refusal is not a lifecycle end.  Keep its dwell
    # separate so normal eligible observations and any execution ownership
    # immediately reset the clock instead of rotating on a transient tick.
    economically_ineligible_since: datetime | None = None
    economic_ineligibility_reasons: tuple[str, ...] = ()
    # Consecutive successful contract reads cannot authorize this idle owner.
    # This is optional replacement evidence, never proof a program ended.
    reward_unavailable_since: datetime | None = None
    reward_unavailable_observed_at: datetime | None = None

    def clear_reward_unavailability(self) -> None:
        self.reward_unavailable_since = None
        self.reward_unavailable_observed_at = None

    def clear_economics(self) -> None:
        """Drop every derived per-side value before a fresh evaluation.

        Reward eligibility, projections, hazards and blockers are recomputed
        from scratch on each pass. Leaving a stale entry behind would let a
        side that no longer qualifies keep accruing score or authorize a quote.
        """

        self.reward_eligible = frozenset()
        self.score_by_side = {}
        self.expected_net_by_side = {}
        self.reward_projection_by_side = {}
        self.economic_blocker_by_side = {}
        self.fill_probability_by_side = {}
        self.fill_risk_driver_by_side = {}
        self.reward_score_by_side = {}
        self.stressed_exit_by_side = {}
        self.opportunity_economics_by_side = {}
        self.model_queue_ahead_by_side = {}

    def forget_contracts(self) -> None:
        """Forget metadata and book so nothing stale can authorize risk."""

        self.market = None
        self.incentive = None
        self.book = None
        self.book_observed_at = None
        self.touch_change_times.clear()
        self.last_touch = None
        self.last_touch_changed_at = None
        self.last_touch_observed_at = None
        self.reward_program_id = None
        self.reward_program_ends_at = None
        self.reward_period_ended_at = None
        self.reward_period_end_confirmed_at = None
        self.reward_authorization_lost_at = None
        self.reward_authorization_loss_reason = None
        self.reward_authorization_loss_confirmed_at = None
        self.economically_ineligible_since = None
        self.economic_ineligibility_reasons = ()
        self.clear_reward_unavailability()
        self.clear_economics()

    def observe_book_touch(
        self, book: BookSnapshot, *, observed_at: datetime,
    ) -> None:
        """Record only BBO changes and retain at most the last two minutes."""

        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("book observation must be timezone-aware")
        if (self.last_touch_observed_at is not None
                and observed_at < self.last_touch_observed_at):
            # A delayed/coalesced book may be applied after a newer receipt.
            # It must not make the telemetry clock run backwards.
            return
        self.last_touch_observed_at = observed_at
        touch = (
            book.best_bid.price if book.bids else None,
            book.best_offer.price if book.offers else None,
        )
        if self.last_touch is None:
            self.last_touch = touch
            self.last_touch_changed_at = observed_at
        elif touch != self.last_touch:
            self.last_touch = touch
            self.last_touch_changed_at = observed_at
            self.touch_change_times.append(observed_at)
        cutoff = observed_at - timedelta(seconds=120)
        while self.touch_change_times and self.touch_change_times[0] < cutoff:
            self.touch_change_times.popleft()

    def touch_changes(self, now: datetime, *, seconds: float) -> int:
        cutoff = now - timedelta(seconds=float(seconds))
        return sum(at >= cutoff for at in self.touch_change_times)

    def seconds_since_touch_change(self, now: datetime) -> float | None:
        if self.last_touch_changed_at is None:
            return None
        return max(0.0, (now - self.last_touch_changed_at).total_seconds())

    def book_age_seconds(self, now: datetime) -> float | None:
        if self.book_observed_at is None:
            return None
        return (now - self.book_observed_at).total_seconds()

    def book_is_quiet(self, now: datetime, *, max_age_seconds: float) -> bool:
        """True when the WebSocket book is old enough to need a REST refresh."""

        age = self.book_age_seconds(now)
        if age is None:
            return True
        return age < 0 or age >= max_age_seconds

    @property
    def is_bound(self) -> bool:
        """True once metadata and a book have both been observed."""

        return (self.market is not None
                and self.book is not None
                and self.book_observed_at is not None)
