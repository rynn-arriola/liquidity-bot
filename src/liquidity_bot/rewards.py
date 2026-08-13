"""Pure liquidity-reward eligibility and scoring rules.

This module intentionally knows nothing about HTTP, WebSockets, credentials,
orders, or positions. API adapters must normalize venue data before calling
these functions, which keeps the money-bearing boundary small and testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence


class AmbiguousIncentiveError(ValueError):
    """More than one active program claims to apply to the same phase."""


@dataclass(frozen=True)
class IncentivePeriod:
    program_id: str
    period: str
    status: str
    reward_pool: float
    discount_factor: float
    target_size: float
    starts_at: datetime | None = None
    ends_at: datetime | None = None


@dataclass(frozen=True)
class RewardScore:
    eligible: bool
    score: float
    ticks_from_best: int | None
    cutoff_price: float | None
    total_depth: float
    reason: str


@dataclass(frozen=True)
class RewardProjection:
    """Conservative dollars attributable to one side over a short horizon.

    Liquidity rewards are normalized independently on each side of every
    snapshot.  The projection therefore uses no more than half of the period
    pool for one side and discounts the current order's observed score share.
    ``minimum_payout`` remains available to research callers, but live
    commissioning passes zero because the venue's $1 settlement threshold is
    aggregate rather than a per-market entry requirement.
    """

    eligible: bool
    order_score: float
    total_score: float
    snapshot_share: float
    projected_reward: float
    maximum_remaining_reward: float
    reason: str


_PERIOD_ALIASES = {
    "early": "early",
    "pre_day": "early",
    "pre-day": "early",
    "day_of": "day_of",
    "day-of": "day_of",
    "dayof": "day_of",
    "live": "live",
}


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def phase_for_event(event_start: datetime, now: datetime) -> str:
    """Return the documented reward lifecycle applying at ``now``.

    Early ends six hours before the event. Day-of runs from that boundary up
    to kickoff, and live begins at kickoff. Settled/closed filtering belongs
    to the market adapter because an event start alone cannot prove settlement.
    """

    _require_aware(event_start, "event_start")
    _require_aware(now, "now")
    seconds = (event_start - now).total_seconds()
    if seconds > 6 * 60 * 60:
        return "early"
    if seconds > 0:
        return "day_of"
    return "live"


def _normalized_period(value: str) -> str | None:
    return _PERIOD_ALIASES.get(str(value or "").strip().lower())


def select_applicable_period(
    periods: Iterable[IncentivePeriod],
    event_start: datetime,
    now: datetime,
) -> IncentivePeriod | None:
    """Select exactly one active period for the event clock, or fail closed.

    The venue can report early, day-of, and live rows as ``active`` at the
    same time. Status alone therefore cannot select the program. If duplicate
    rows claim the same current phase, guessing which pool applies would make
    expected revenue optimistic, so ambiguity raises explicitly.
    """

    phase = phase_for_event(event_start, now)
    matches = [
        item for item in periods
        if str(item.status).strip().lower() == "active"
        and _normalized_period(item.period) == phase
        and (item.starts_at is None or item.starts_at <= now)
        and (item.ends_at is None or now < item.ends_at)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        ids = ", ".join(sorted(item.program_id for item in matches))
        raise AmbiguousIncentiveError(
            f"multiple active {phase} incentive programs: {ids}")
    return matches[0]


def _validated_levels(levels: Sequence[tuple[float, float]]) -> dict[float, float]:
    combined: dict[float, float] = {}
    for raw_price, raw_size in levels:
        price, size = float(raw_price), float(raw_size)
        if not (0 < price < 1) or size <= 0:
            continue
        key = round(price, 8)
        combined[key] = combined.get(key, 0.0) + size
    return combined


def evaluate_order_score(
    *,
    side: str,
    price: float,
    size: float,
    levels: Sequence[tuple[float, float]],
    incentive: IncentivePeriod,
    tick_size: float,
    order_already_resting: bool = False,
) -> RewardScore:
    """Evaluate one proposed order against one side of the full book.

    The proposed order is added to the side before walking depth because its
    size can be what reaches ``targetSize``. Bids walk high-to-low and offers
    low-to-high. If aggregate side depth never reaches the target, every order
    on that side scores zero. Otherwise only prices at or inside the cutoff
    qualify, and the documented score is ``discountFactor ** ticks * size``.
    """

    side = str(side).strip().lower()
    price, size, tick_size = float(price), float(size), float(tick_size)
    target = float(incentive.target_size)
    discount = float(incentive.discount_factor)
    if side not in {"bid", "offer"}:
        raise ValueError("side must be 'bid' or 'offer'")
    if not (0 < price < 1):
        raise ValueError("price must be between 0 and 1")
    if size <= 0:
        raise ValueError("size must be positive")
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    if target <= 0:
        raise ValueError("target_size must be positive")
    if not (0 < discount <= 1):
        raise ValueError("discount_factor must be in (0, 1]")

    depth = _validated_levels(levels)
    order_price = round(price, 8)
    if not order_already_resting:
        depth[order_price] = depth.get(order_price, 0.0) + size
    ordered = sorted(depth.items(), reverse=(side == "bid"))
    best_price = ordered[0][0]

    cumulative = 0.0
    cutoff_price: float | None = None
    for level_price, level_size in ordered:
        cumulative += level_size
        if cumulative + 1e-9 >= target:
            cutoff_price = level_price
            break

    if cutoff_price is None:
        return RewardScore(
            eligible=False,
            score=0.0,
            ticks_from_best=None,
            cutoff_price=None,
            total_depth=cumulative,
            reason="target_not_met",
        )

    inside_cutoff = (
        order_price + 1e-9 >= cutoff_price
        if side == "bid"
        else order_price - 1e-9 <= cutoff_price
    )
    distance = (
        best_price - order_price if side == "bid"
        else order_price - best_price
    )
    # Valid venue prices should land exactly on ticks. Ceil is conservative if
    # a malformed/off-grid price reaches this pure boundary: never overstate
    # reward by rounding a partial tick toward the touch.
    ticks = max(0, math.ceil(distance / tick_size - 1e-9))
    if not inside_cutoff:
        return RewardScore(
            eligible=False,
            score=0.0,
            ticks_from_best=ticks,
            cutoff_price=cutoff_price,
            total_depth=cumulative,
            reason="outside_reward_cutoff",
        )

    return RewardScore(
        eligible=True,
        score=(discount ** ticks) * size,
        ticks_from_best=ticks,
        cutoff_price=cutoff_price,
        total_depth=cumulative,
        reason="eligible",
    )


def project_order_reward(
    *,
    side: str,
    price: float,
    size: float,
    levels: Sequence[tuple[float, float]],
    incentive: IncentivePeriod,
    tick_size: float,
    event_start: datetime,
    now: datetime,
    horizon_seconds: float,
    haircut: float = 0.5,
    minimum_payout: float = 1.0,
    order_already_resting: bool = False,
) -> RewardProjection:
    """Project a conservative one-side reward from the current full book.

    The documented ``day_of`` period has an exact six-hour duration.  Other
    periods do not expose a reliable per-market start/end clock in the Retail
    API, so they fail closed instead of dividing a headline pool by a guessed
    duration.  This is an entry authorization estimate, not booked income.
    """

    _require_aware(event_start, "event_start")
    _require_aware(now, "now")
    horizon = float(horizon_seconds)
    haircut = float(haircut)
    minimum_payout = float(minimum_payout)
    if horizon <= 0:
        raise ValueError("reward projection horizon must be positive")
    if not 0 < haircut <= 1:
        raise ValueError("reward projection haircut must be in (0, 1]")
    if minimum_payout < 0:
        raise ValueError("minimum payout cannot be negative")

    score = evaluate_order_score(
        side=side,
        price=price,
        size=size,
        levels=levels,
        incentive=incentive,
        tick_size=tick_size,
        order_already_resting=order_already_resting,
    )
    if not score.eligible:
        return RewardProjection(
            False, 0.0, 0.0, 0.0, 0.0, 0.0, score.reason)

    if _normalized_period(incentive.period) != "day_of":
        return RewardProjection(
            False, score.score, 0.0, 0.0, 0.0, 0.0,
            "period_duration_unproven")
    period_end = event_start
    period_start = event_start - timedelta(hours=6)
    if not period_start <= now < period_end:
        return RewardProjection(
            False, score.score, 0.0, 0.0, 0.0, 0.0,
            "outside_projection_period")

    side = str(side).strip().lower()
    price = float(price)
    size = float(size)
    tick_size = float(tick_size)
    discount = float(incentive.discount_factor)
    target = float(incentive.target_size)
    depth = _validated_levels(levels)
    order_price = round(price, 8)
    # A working order is normally already present in the aggregate book, but
    # the feed does not identify our quantity. Counting it once more in the
    # denominator is the conservative ambiguity treatment; it can only lower
    # the projected share. Eligibility above was evaluated against the book as
    # observed, so this extra denominator size cannot create eligibility.
    depth[order_price] = depth.get(order_price, 0.0) + size
    ordered = sorted(depth.items(), reverse=(side == "bid"))
    best_price = ordered[0][0]

    cumulative = 0.0
    qualifying: list[tuple[float, float]] = []
    for level_price, level_size in ordered:
        cumulative += level_size
        qualifying.append((level_price, level_size))
        if cumulative + 1e-9 >= target:
            break
    if cumulative + 1e-9 < target:
        return RewardProjection(
            False, score.score, 0.0, 0.0, 0.0, 0.0,
            "target_not_met")

    total_score = 0.0
    for level_price, level_size in qualifying:
        distance = (best_price - level_price if side == "bid"
                    else level_price - best_price)
        ticks = max(0, math.ceil(distance / tick_size - 1e-9))
        total_score += (discount ** ticks) * level_size
    if total_score <= 0:
        return RewardProjection(
            False, score.score, total_score, 0.0, 0.0, 0.0,
            "total_score_unknown")

    share = min(1.0, score.score / total_score)
    duration = (period_end - period_start).total_seconds()
    remaining = (period_end - now).total_seconds()
    # Each side is independently normalized to 1.0.  Reserving no more than
    # half the pool for either side is conservative when both sides qualify.
    dollars_per_second = (
        float(incentive.reward_pool) * 0.5 * share * haircut / duration)
    maximum = dollars_per_second * remaining
    if maximum + 1e-12 < minimum_payout:
        return RewardProjection(
            False, score.score, total_score, share, 0.0, maximum,
            "below_minimum_payout")
    projected = dollars_per_second * min(horizon, remaining)
    return RewardProjection(
        True, score.score, total_score, share, projected, maximum,
        "projected")
