"""Per-market runtime state for a multi-market commissioning session.

The runner was built around a single bound market: one metadata record, one
book, one set of per-side economics. Quoting several markets at once needs one
of these per slug, held in a dict, while genuinely session-wide state (marked
equity, the risk guard, score-seconds, the taker-exit budget) stays single.

Nothing here reaches the network or the ledger. It is a container plus the
small amount of freshness logic that belongs with the data it guards, so the
runner can ask a market whether its book is stale without reaching inside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import BookSnapshot, MarketIncentive, MarketMetadata
from .rewards import RewardProjection


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
    # Each market keeps its own REST cadence so one quiet book cannot starve
    # the others through the shared request governor.
    next_book_refresh: datetime | None = None
    next_contract_refresh: datetime | None = None

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

    def forget_contracts(self) -> None:
        """Forget metadata and book so nothing stale can authorize risk."""

        self.market = None
        self.incentive = None
        self.book = None
        self.book_observed_at = None
        self.clear_economics()

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
