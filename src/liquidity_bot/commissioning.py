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
    # Raised 1 -> 5 on 2026-08-14, matching maker-lab's QUOTE_SIZE. At one
    # contract both the maker rebate and the taker fee round to zero (section 1
    # of FINDINGS: per-contract economics are ~37% worse at size 1), and reward
    # share is size-linear, so size 1 earns a rounding error. Worst case is
    # 5 x 8 markets x 2 sides x ~$0.50 = $40 notional, which is what the loss
    # stop below is set to.
    quote_size: Decimal = Decimal("5")
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
    # One operative stop, raised to $40 on 2026-08-14 alongside quote size 5.
    # $40 is the fully-adverse exposure of the whole authorized set: five
    # contracts across eight markets on both sides, each contract able to lose
    # about a dollar. Both tiers sit at $40 so exactly one threshold can fire;
    # the $45 ceiling exists only because the emergency stop is required to sit
    # above the normal stop, and it should never trigger.
    max_market_loss_usd: Decimal = Decimal("40")
    max_session_loss_usd: Decimal = Decimal("40")
    hard_stop_loss_usd: Decimal = Decimal("45")
    # Exposure budgets, distinct from the loss stops above. Until 2026-08-14
    # one pair of numbers served as both, which is incoherent once size grows:
    # "stop after losing $40" and "never hold more than $40 of worst case" are
    # different statements, and capital cannot be deployed while they share a
    # value. The stops stay at $40 of *realised* loss; these bound *open*
    # worst-case exposure and are sized to the $385.88 balance with a ~10%
    # buffer, following maker-lab's CAPITAL_BUFFER_PCT.
    max_market_exposure_usd: Decimal = Decimal("45")
    max_portfolio_exposure_usd: Decimal = Decimal("340")
    rest_requests_per_second: Decimal = Decimal("4")
    max_hard_exit_attempts: int = 3
    max_hard_exit_retry_seconds: int = 30
    hard_exit_confirmation_timeout_seconds: int = 10
    max_shutdown_exit_attempts: int = 3
    shutdown_alert_interval_seconds: int = 300
    max_shutdown_seconds: int = 900
    reward_projection_horizon_seconds: int = 30
    reward_projection_haircut: Decimal = Decimal("0.5")
    # Was 2. The cost side of the gate already carries two independent
    # pessimistic estimates: P is a Wilson upper bound, and unwind is priced
    # against stressed (half) depth. This multiple padded the same term a third
    # time, while the reward side carried only the 0.5 haircut -- an asymmetry
    # nobody chose, accumulated by adding guards one at a time. The 2026-08-14
    # census measured 94% of all sides in the universe failing on this one
    # test, best gate_ratio 0.392, so the stack was rejecting everything.
    # Operator-approved 2026-08-14. The haircut stays: projected reward is the
    # least-padded quantity in the expression.
    reward_cost_safety_multiple: Decimal = Decimal("1")
    aggregate_reward_payout_floor_usd: Decimal = Decimal("1")
    # A single permitted taker exit ended the session on its first crossed
    # fill, which made exit evidence impossible to collect: SCALE_REVIEW asks
    # for 100 exit samples and this capped it at one. maker-lab crossed 40
    # times in 24 hours, so 20 is a real sample at a bounded cost of about
    # $0.20 in commission at one contract.
    max_taker_exit_orders: int = 20
    # Scaled to quote size. The taker fee is 0.06 x qty x p x (1-p), peaking
    # at p = 0.5: $0.075 per exit at five contracts. $1.50 is the same twenty
    # exits. The old $0.25 was calibrated for one contract and would have
    # tripped before twenty exits could ever be collected.
    max_session_execution_fees_usd: Decimal = Decimal("1.50")
    earnings_refresh_seconds: int = 900
    auto_select_reward_markets: bool = False
    market_selection_refresh_seconds: int = 900
    # Was 25. Only ~33 markets survive the day_of and catalog-spread filters,
    # so 25 left a real blind spot for no reason: the census covered all 33 in
    # 40 seconds. 100 covers the whole candidate pool with room to grow, at
    # ~25s per scan against a 900s interval.
    max_selection_book_candidates: int = 100
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
                    or self.max_selection_book_candidates != 100):
                problems.append(
                    "automatic selection must refresh every 900 seconds and "
                    "deeply evaluate at most 100 reward markets")
        elif (not self.market_slug.strip()
              or self.market_slug == AUTO_REWARD_MARKET):
            problems.append("one explicit market slug is required")
        if self.quote_size != 5 or self.max_markets != 8:
            problems.append(
                "commissioning is hard-locked to five contracts per quote "
                "across at most eight markets")
        if (self.max_market_exposure_usd != 45
                or self.max_portfolio_exposure_usd != 340):
            problems.append(
                "commissioning exposure budgets must remain $45 per market "
                "and $340 across the portfolio")
        if self.max_portfolio_exposure_usd < self.max_market_exposure_usd:
            problems.append(
                "the portfolio exposure budget must cover a single market")
        if not self.manual_stop_only:
            problems.append("commissioning must remain armed for operator stop")
        if (self.max_session_loss_usd != 40 or self.max_market_loss_usd != 40
                or self.hard_stop_loss_usd != 45):
            problems.append(
                "commissioning must keep a single $40 stop with a $45 "
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
                or self.reward_cost_safety_multiple != Decimal("1")
                or self.aggregate_reward_payout_floor_usd != Decimal("1")):
            problems.append(
                "commissioning reward economics must remain a 30-second "
                "projection, 50% haircut, 1x probability-weighted cost "
                "margin, and $1 aggregate reporting floor")
        if (self.max_taker_exit_orders != 20
                or self.max_session_execution_fees_usd != Decimal("1.50")
                or self.earnings_refresh_seconds != 900):
            problems.append(
                "commissioning loss learning must stop after 20 taker exits, "
                "cap execution fees at $1.50, and refresh earnings every 900 seconds")
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
