"""Pure liquidity-reward eligibility and scoring rules.

This module intentionally knows nothing about HTTP, WebSockets, credentials,
orders, or positions. API adapters must normalize venue data before calling
these functions, which keeps the money-bearing boundary small and testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo


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
    "daily_event": "daily_event",
    "daily-event": "daily_event",
    "live": "live",
}

_REWARD_TIMEZONE = ZoneInfo("America/New_York")


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
    seconds = (event_start.astimezone(timezone.utc)
               - now.astimezone(timezone.utc)).total_seconds()
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
    *,
    allow_daily_event: bool = False,
) -> IncentivePeriod | None:
    """Select exactly one active period for the event clock, or fail closed.

    The venue can report early, day-of, and live rows as ``active`` at the
    same time. Status alone therefore cannot select the program. If duplicate
    rows claim the same current phase, guessing which pool applies would make
    expected revenue optimistic, so ambiguity raises explicitly.

    ``daily_event`` is a separate midnight-to-midnight ET program.  When it is
    enabled it is only a fallback during the six-hour pregame/day-of phase;
    an applicable event-lifecycle row always wins.  This preserves the
    established day-of strategy and cannot use a daily row to authorize live
    trading.
    """

    # Materialize once: callers may supply a generator, and fallback needs a
    # second pass after the event-lifecycle lookup.
    periods = tuple(periods)
    phase = phase_for_event(event_start, now)
    matches = [
        item for item in periods
        if str(item.status).strip().lower() == "active"
        and _normalized_period(item.period) == phase
        and (item.starts_at is None or item.starts_at <= now)
        and (item.ends_at is None or now < item.ends_at)
    ]
    if len(matches) > 1:
        ids = ", ".join(sorted(item.program_id for item in matches))
        raise AmbiguousIncentiveError(
            f"multiple active {phase} incentive programs: {ids}")
    if matches:
        return matches[0]
    if not allow_daily_event or phase != "day_of":
        return None

    daily_matches = [
        item for item in periods
        if str(item.status).strip().lower() == "active"
        and _normalized_period(item.period) == "daily_event"
        and (item.starts_at is None or item.starts_at <= now)
        and (item.ends_at is None or now < item.ends_at)
    ]
    if len(daily_matches) > 1:
        ids = ", ".join(sorted(item.program_id for item in daily_matches))
        raise AmbiguousIncentiveError(
            f"multiple active daily_event incentive programs: {ids}")
    return daily_matches[0] if daily_matches else None


def _projection_period_bounds(
    incentive: IncentivePeriod,
    event_start: datetime,
    now: datetime,
) -> tuple[datetime, datetime, datetime] | None:
    """Return pool start/end and this pregame bot's accessible end.

    The pool duration determines dollars per second.  ``accessible_end`` can
    be earlier: a daily pool may continue after kickoff, but this executor may
    not.  All arithmetic is converted to UTC so 23/25-hour New York DST days
    remain real 23/25-hour reward windows rather than nominal 24-hour days.
    """

    kind = _normalized_period(incentive.period)
    if kind == "day_of":
        period_start = event_start - timedelta(hours=6)
        period_end = event_start
    elif kind == "daily_event":
        local_date = now.astimezone(_REWARD_TIMEZONE).date()
        period_start = datetime.combine(
            local_date, time.min, tzinfo=_REWARD_TIMEZONE,
        ).astimezone(timezone.utc)
        period_end = datetime.combine(
            local_date + timedelta(days=1), time.min,
            tzinfo=_REWARD_TIMEZONE,
        ).astimezone(timezone.utc)
    else:
        return None

    # A daily headline remains a per-day amount. A partial first/last day
    # must not compress that denominator and inflate the projected rate.
    if kind == "daily_event":
        accessible_end = min(period_end, event_start)
        if incentive.ends_at is not None:
            _require_aware(incentive.ends_at, "incentive.ends_at")
            accessible_end = min(accessible_end, incentive.ends_at)
        return period_start, period_end, accessible_end

    if incentive.starts_at is not None:
        _require_aware(incentive.starts_at, "incentive.starts_at")
        period_start = max(period_start, incentive.starts_at)
    if incentive.ends_at is not None:
        _require_aware(incentive.ends_at, "incentive.ends_at")
        period_end = min(period_end, incentive.ends_at)
    accessible_end = min(period_end, event_start)
    return period_start, period_end, accessible_end


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
    # Our size counts toward depth whether or not the order is already
    # resting. The public book does contain a resting order of ours, so in
    # principle skipping it here avoids double-counting -- but the book is
    # only eventually consistent, and in the window before it catches up the
    # depth is short by exactly our size. Eligibility turns on cumulative
    # depth reaching targetSize, so on a book near that boundary the answer
    # flipped the instant our order rested: eligible while flat,
    # `target_not_met` while resting, eligible again once cancelled. Live that
    # was 1,817 place/cancel cycles in 5.7 hours at the same price, and reward
    # pays for resting time.
    #
    # Counting it in both cases makes the two questions -- "worth placing?"
    # and "worth keeping?" -- answer the same thing. The residual error is
    # over-counting one contract against a targetSize in the thousands once
    # the book has caught up, which biases toward leaving a quote resting.
    # That is the safe direction: resting is what earns.
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

    ``day_of`` uses its documented six-hour window. ``daily_event`` uses the
    documented midnight-to-midnight ET window, including 23/25-hour DST days,
    while stopping this pregame executor's accessible reward at kickoff.
    Other periods fail closed instead of dividing a headline pool by a guessed
    duration. This is a ranking estimate, not booked income; venue payouts
    remain authoritative.
    """

    _require_aware(event_start, "event_start")
    _require_aware(now, "now")
    event_start = event_start.astimezone(timezone.utc)
    now = now.astimezone(timezone.utc)
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

    bounds = _projection_period_bounds(incentive, event_start, now)
    if bounds is None:
        return RewardProjection(
            False, score.score, 0.0, 0.0, 0.0, 0.0,
            "period_duration_unproven")
    period_start, period_end, accessible_end = bounds
    if incentive.starts_at is not None:
        _require_aware(incentive.starts_at, "incentive.starts_at")
        if now < incentive.starts_at:
            return RewardProjection(
                False, score.score, 0.0, 0.0, 0.0, 0.0,
                "outside_projection_period")
    if period_end <= period_start:
        return RewardProjection(
            False, score.score, 0.0, 0.0, 0.0, 0.0,
            "period_duration_unproven")
    if not period_start <= now < accessible_end:
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
    remaining = (accessible_end - now).total_seconds()
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
