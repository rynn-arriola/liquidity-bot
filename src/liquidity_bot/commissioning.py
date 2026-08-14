"""Hard-locked authorization for one-market live commissioning.

This module does not connect to Polymarket. It turns the operator's explicit
risk acceptance into a narrow, reproducible commissioning configuration. The
actual runner must still pass exchange-state preflight before any mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


NEW_YORK = "America/New_York"
AUTO_REWARD_MARKET = "__AUTO_REWARD_MARKETS__"


class CommissioningRefused(RuntimeError):
    """The requested experimental live session is not narrowly authorized."""


class CommissioningUnavailable(CommissioningRefused):
    """Startup cannot proceed *yet*, but nothing unsafe was observed.

    A market that has not opened, an incentive programme that has not
    published, a private snapshot that arrived late, or a Discord blip are all
    conditions that clear on their own. Treating them like a refusal exits 2,
    which the supervisor never restarts, so one bad moment ended a whole run
    before a single order. These retry instead.

    Ownership, cross-market state, and configuration drift are never this:
    they stay ``CommissioningRefused`` and remain terminal.
    """


def next_six_am_new_york(now: datetime) -> datetime:
    """Return the next 06:00 New York cutoff as an aware datetime."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    try:
        zone = ZoneInfo(NEW_YORK)
    except ZoneInfoNotFoundError as exc:
        raise CommissioningRefused(
            "America/New_York timezone data is unavailable") from exc
    local = now.astimezone(zone)
    cutoff = datetime.combine(local.date(), time(6), tzinfo=zone)
    if cutoff <= local:
        cutoff += timedelta(days=1)
    return cutoff


@dataclass(frozen=True)
class CommissioningConfig:
    """Non-scalable one-contract experimental authorization.

    Three loss limits, one operative threshold. ``max_market_loss_usd`` and
    ``max_session_loss_usd`` are both stops -- they appear in
    ``runtime_safety.blockers()`` and halt quoting -- and both sit at $30 so
    exactly one number can fire. ``hard_stop_loss_usd`` is the emergency
    ceiling on marked account equity; it sits at $35 only because it is
    required to exceed the normal stop, and should never trigger.
    """

    market_slug: str
    economic_risk_accepted: bool
    exclusive_api_key_confirmed: bool
    maker_lab_stopped_confirmed: bool
    arm_live: bool = False
    quote_size: Decimal = Decimal("1")
    # Eight is the measured ceiling, not a preference. The private
    # WebSocket accepts 10 subscriptions per connection; positions must be
    # scoped one market per subscription because positionSubscription
    # omits marketSlug, while orders can be account-wide because order
    # events always carry it. 1 orders + 8 positions + 1 balance = 10.
    max_markets: int = 8
    # maker-lab exit latency, 112 reconstructed position episodes on
    # 2026-08-11/12: a passive exit resolved within 30s for only 26% of
    # episodes, but 46% by 900s (median 100s, p75 653s). Crossing costs a
    # certain ~$0.01 taker commission; holding one contract risks at most
    # ~$0.99 and their markout was positive at 60s, 300s and 900s. Waiting is
    # therefore the cheaper side of the trade.
    max_inventory_age_seconds: int = 900
    # Kickoff overrides the inventory clock. maker-lab disabled its forced
    # exit on the same "holding is cheaper" evidence and the residue reached
    # settlement for -$88.20, 86% of that session's loss. Never settle.
    force_exit_before_start_seconds: int = 300
    # One operative stop. Both tiers sit at $30 so exactly one threshold
    # can fire; the $35 ceiling exists only because the emergency stop is
    # required to sit above the normal stop, and it should never trigger.
    max_market_loss_usd: Decimal = Decimal("30")
    max_session_loss_usd: Decimal = Decimal("30")
    hard_stop_loss_usd: Decimal = Decimal("35")
    rest_requests_per_second: Decimal = Decimal("4")
    max_hard_exit_attempts: int = 3
    max_hard_exit_retry_seconds: int = 30
    hard_exit_confirmation_timeout_seconds: int = 10
    max_shutdown_exit_attempts: int = 3
    shutdown_alert_interval_seconds: int = 300
    max_shutdown_seconds: int = 900
    reward_projection_horizon_seconds: int = 30
    reward_projection_haircut: Decimal = Decimal("0.5")
    reward_cost_safety_multiple: Decimal = Decimal("2")
    aggregate_reward_payout_floor_usd: Decimal = Decimal("1")
    # A single permitted taker exit ended the session on its first crossed
    # fill, which made exit evidence impossible to collect: SCALE_REVIEW asks
    # for 100 exit samples and this capped it at one. maker-lab crossed 40
    # times in 24 hours, so 20 is a real sample at a bounded cost of about
    # $0.20 in commission at one contract.
    max_taker_exit_orders: int = 20
    max_session_execution_fees_usd: Decimal = Decimal("0.25")
    earnings_refresh_seconds: int = 900
    auto_select_reward_markets: bool = False
    market_selection_refresh_seconds: int = 900
    max_selection_book_candidates: int = 25
    minimum_entry_spread: Decimal = Decimal("0.02")
    spread_hysteresis: Decimal = Decimal("0.005")
    manual_stop_only: bool = True

    def blockers(self, *, now: datetime) -> tuple[str, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        problems: list[str] = []
        if not self.arm_live:
            problems.append("live arming flag is absent")
        if not self.economic_risk_accepted:
            problems.append("one-contract economic risk is not accepted")
        if not self.exclusive_api_key_confirmed:
            problems.append("exclusive API-key use is not confirmed")
        if not self.maker_lab_stopped_confirmed:
            problems.append("maker-lab is not confirmed stopped")
        if self.auto_select_reward_markets:
            if self.market_slug != AUTO_REWARD_MARKET:
                problems.append(
                    "automatic selection requires the reserved reward-market slug")
            if (self.market_selection_refresh_seconds != 900
                    or self.max_selection_book_candidates != 25):
                problems.append(
                    "automatic selection must refresh every 900 seconds and "
                    "deeply evaluate at most 25 reward markets")
        elif (not self.market_slug.strip()
              or self.market_slug == AUTO_REWARD_MARKET):
            problems.append("one explicit market slug is required")
        if self.quote_size != 1 or self.max_markets != 8:
            problems.append(
                "commissioning is hard-locked to one contract per quote "
                "across at most eight markets")
        if not self.manual_stop_only:
            problems.append("commissioning must remain armed for operator stop")
        if (self.max_session_loss_usd != 30 or self.max_market_loss_usd != 30
                or self.hard_stop_loss_usd != 35):
            problems.append(
                "commissioning must keep a single $30 stop with a $35 "
                "emergency equity ceiling above it")
        if self.hard_stop_loss_usd <= self.max_session_loss_usd:
            problems.append("hard stop must exceed the normal session stop")
        if self.rest_requests_per_second > 4:
            problems.append("commissioning REST budget cannot exceed 4 requests/second")
        if (self.max_hard_exit_attempts != 3
                or self.max_hard_exit_retry_seconds != 30
                or self.hard_exit_confirmation_timeout_seconds != 10):
            problems.append(
                "commissioning hard-exit limits must remain 3 attempts, "
                "30 seconds total, and 10 seconds for confirmation")
        if (self.max_shutdown_exit_attempts != 3
                or self.shutdown_alert_interval_seconds != 300
                or self.max_shutdown_seconds != 900):
            problems.append(
                "commissioning shutdown limits must remain 3 exit attempts, "
                "300-second alert repeats, and a 900-second window")
        if (self.reward_projection_horizon_seconds != 30
                or self.reward_projection_haircut != Decimal("0.5")
                or self.reward_cost_safety_multiple != Decimal("2")
                or self.aggregate_reward_payout_floor_usd != Decimal("1")):
            problems.append(
                "commissioning reward economics must remain a 30-second "
                "projection, 50% haircut, 2x probability-weighted cost "
                "margin, and $1 aggregate reporting floor")
        if (self.max_taker_exit_orders != 20
                or self.max_session_execution_fees_usd != Decimal("0.25")
                or self.earnings_refresh_seconds != 900):
            problems.append(
                "commissioning loss learning must stop after 20 taker exits, "
                "cap execution fees at $0.25, and refresh earnings every 900 seconds")
        if (self.max_inventory_age_seconds != 900
                or self.force_exit_before_start_seconds != 300):
            problems.append(
                "commissioning inventory limits must remain a 900-second "
                "passive exit window with a 300-second pre-kickoff flatten")
        if self.force_exit_before_start_seconds >= self.max_inventory_age_seconds:
            problems.append(
                "the pre-kickoff flatten must fire before the inventory clock")
        if (self.minimum_entry_spread != Decimal("0.02")
                or self.spread_hysteresis != Decimal("0.005")):
            problems.append(
                "commissioning entry spread must remain 2 cents with "
                "0.5-cent resting-order hysteresis")
        if (self.minimum_entry_spread <= 0
                or self.spread_hysteresis < 0
                or self.spread_hysteresis >= self.minimum_entry_spread):
            problems.append("commissioning spread thresholds are invalid")
        return tuple(problems)

    def assert_authorized(self, *, now: datetime) -> None:
        problems = self.blockers(now=now)
        if problems:
            raise CommissioningRefused("; ".join(problems))
