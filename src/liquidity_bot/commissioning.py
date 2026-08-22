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
    # Raised 5 -> 10 on 2026-08-20, by operator decision. Size is leverage,
    # not edge: FINDINGS measured reward and unwind BOTH scaling linearly
    # (25x size -> 24.4x reward, gate_ratio 0.0310 -> 0.0305), so doubling
    # doubles what a qualifying book pays and doubles what a bad fill costs,
    # leaving the ratio untouched. It was taken deliberately on that basis.
    #
    # Everything derived from contract count moves with it or the config stops
    # being coherent -- the exposure budget below, and `max_early_exit_loss_usd`
    # above all, whose $4 was calibrated on size-5 crossed losses.
    #
    # The LOSS STOPS deliberately do NOT move. They protect the account, not
    # the position, and equity did not double: $40 is 9% of ~$467, $80 would be
    # 18%. Measured headroom says they still work -- matched-pair trading is
    # -47.24 over seven days, about -6.75/day at size 5, so ~-13.50/day here
    # against a $40 stop. Three times the typical day rather than six.
    quote_size: Decimal = Decimal("10")
    # Eight is the measured ceiling, not a preference. The private
    # WebSocket accepts 10 subscriptions per connection; positions must be
    # scoped one market per subscription because positionSubscription
    # omits marketSlug, while orders can be account-wide because order
    # events always carry it. 1 orders + 8 positions + 1 balance = 10.
    # Twenty markets across three private connections (8 + 8 + 4).
    #
    # Not set by the socket cap. Measured 2026-08-15 after the market feed was
    # made survivable (one unusable book used to kill it), three runs at each
    # width, 25s each:
    #
    #     16 -> 16/16 slugs,  82-91 msgs, 0 dropped
    #     20 -> 20/20 slugs, 153-165 msgs, 0 dropped
    #     24 -> 22/24 slugs, 184-202 msgs, 0 dropped
    #     29 -> 27/29 slugs, 202-226 msgs, 0 dropped
    #
    # The socket sustains 29. **The real ceiling is what REST can carry alone
    # when the socket drops**, because that is when a held position still has
    # to be exitable: N markets refresh in N/4 seconds against a 5s staleness
    # bound, so 20 sits exactly at the limit and 24+ does not fit. Above 20 a
    # feed drop leaves every book stale, the exit unpriceable, and inventory
    # riding toward settlement -- how maker-lab lost $443.88.
    #
    # Worst case exposure: both sides of a market risk quote_size x p +
    # quote_size x (1 - p) = $5, so 20 markets is $100 against the $340 cap.
    max_markets: int = 20
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
    # What an EARLY cross may cost, before the kickoff deadline makes one
    # mandatory. A third member of the family split on 2026-08-14: it read
    # `max_market_loss_usd` and so could never fire, because five contracts
    # cannot lose $40. "Stop the session after losing $40" and "do not pay
    # more than $4 to leave one position ahead of the deadline" are different
    # statements, and the entry gate is a third thing again -- it keeps
    # `max_market_loss_usd`, so entry behaviour is unchanged by this.
    #
    # Chosen from the 17 crossed losses measured 2026-08-16 (median $2.32,
    # p75 $3.23, p90 $6.15): $4 blocks the three worst and defers $16.96 of
    # $41.96, 40% of the damage from 18% of the events, leaving the median
    # exit alone. Tighter values are not a tail cut -- $0.75 blocks 13 of 17 --
    # and a cap defers a loss rather than avoiding it, so buying that trade
    # needs mean-reversion evidence between the inventory clock and kickoff
    # that we do not have yet. See FINDINGS.
    # Doubled with quote_size on 2026-08-20 so it keeps its MEANING. The $4
    # was chosen from 17 crossed losses at size 5 (median $2.32, p75 $3.23,
    # p90 $6.15) to block the three worst while leaving the median exit alone.
    # At size 10 those same trades are median $4.64 and p75 $6.46, so a $4 cap
    # would sit BELOW the median and refuse almost every early cross --
    # silently converting the exit policy into "ride to the kickoff flatten".
    # FINDINGS measures P&L degrading monotonically with holding time
    # (+23.8c under 300s, -23.5c past an hour), so that would have been a real
    # cost taken by accident rather than by decision.
    max_early_exit_loss_usd: Decimal = Decimal("8")
    # How many CONSECUTIVE UTC days may hit a loss stop before it stops being
    # a pause and becomes terminal.
    #
    # A loss stop used to flatten, prove flat and exit 0 -- which the
    # supervisor never restarts, so a bad day cost every subsequent hour until
    # a human noticed. Operator decision 2026-08-22: the loss stops now PAUSE
    # instead. New risk is blocked, exits and the pre-kickoff flatten keep
    # working, and the day baseline reset at midnight clears the blocker on its
    # own, so the bot resumes without anyone touching it.
    #
    # That is unbounded on its own, and this is the bound. The budget resets
    # every midnight, so a systematically wrong strategy would lose its full
    # $40 daily forever with nothing forcing attention -- and the $45 emergency
    # ceiling cannot catch it either, because that re-baselines each morning
    # too. One bad day is variance; three in a row is a broken model. Worst
    # case before it demands a human is about $120.
    max_consecutive_loss_stop_days: int = 3
    # Exposure budgets, distinct from the loss stops above. Until 2026-08-14
    # one pair of numbers served as both, which is incoherent once size grows:
    # "stop after losing $40" and "never hold more than $40 of worst case" are
    # different statements, and capital cannot be deployed while they share a
    # value. The stops stay at $40 of *realised* loss; these bound *open*
    # worst-case exposure and are sized to the $385.88 balance with a ~10%
    # buffer, following maker-lab's CAPITAL_BUFFER_PCT.
    #
    # Per-market doubled 45 -> 90 with quote_size on 2026-08-20: worst case on
    # one market is `size x p + size x (1 - p)` = $10 at ten contracts, and the
    # budget keeps the same multiple of it. The PORTFOLIO budget stays at $340
    # -- it is sized to the account, not to the position, and 20 markets at $10
    # is $200, still comfortably inside it. Raising it too would have loosened
    # the one cap that bounds total open risk, which nothing here argues for.
    max_market_exposure_usd: Decimal = Decimal("90")
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
    # maker-lab's entry rule, adopted 2026-08-14 by operator decision. It asks
    # two things before quoting: is the spread at least 2c, and do the risk and
    # capital caps allow it. It never asks whether the quote is expected to be
    # profitable, and it collected 201 fills and $70.92 of rewards.
    #
    # Ours asked a third question -- does projected reward beat the
    # probability-weighted exit cost -- and the 2026-08-14 census measured that
    # rejecting 94% of every side on the venue, best gate_ratio 0.392 against
    # the 1.0 needed. 2,500 placements produced 2 fills.
    #
    # Turning it off does NOT adopt maker-lab's losses. Those came from
    # carrying positions into the game (-$443.88 over four days), and the
    # pre-kickoff flatten and 900-second exit already prevent that here. The
    # economics still get computed and logged on every pass, so the gate can be
    # re-armed from measured data rather than from the arbitrary 0.5 and 2x it
    # was built on.
    enforce_reward_cost_gate: bool = False
    # How long a mutation may sit unresolved before the session is stopped as
    # critical. This was hardcoded at 10 seconds and stopped the bot twice in
    # forty minutes on 2026-08-17.
    #
    # Measured that day over 5,023 intents, `updated_at - created_at`:
    #
    #     p50 4.54s   p90 8.22s   p95 9.49s   p99 16.36s   max 37.72s
    #
    # **The terminal stop was armed at the p95 of normal operation** -- 203 of
    # 5,023 mutations (4.0%) crossed 10 seconds as a matter of routine. The
    # slow tail is not a stuck venue; it is reconciliation itself queueing
    # behind the shared 4 req/s governor, so a burst of mutations makes its own
    # resolution slow.
    #
    # 60 seconds sits well clear of the whole observed distribution (nothing
    # all day exceeded 45s) while still catching a mutation that is genuinely
    # never coming back. Widening it does not weaken the invariant: unresolved
    # state still quarantines the market, still blocks new risk, and still
    # forces shutdown through equity and stream-freshness checks.
    unresolved_mutation_timeout_seconds: float = 60.0
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
        if self.quote_size != 10 or self.max_markets != 20:
            problems.append(
                "commissioning is hard-locked to ten contracts per quote "
                "across at most twenty markets")
        if (self.max_market_exposure_usd != 90
                or self.max_portfolio_exposure_usd != 340):
            problems.append(
                "commissioning exposure budgets must remain $90 per market "
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
        if self.max_consecutive_loss_stop_days != 3:
            problems.append(
                "a paused loss stop must become terminal after three "
                "consecutive days")
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
        if self.minimum_entry_spread != Decimal("0.02"):
            problems.append(
                "commissioning must keep two cents in the middle between the "
                "two outcomes' quotes")
        if self.minimum_entry_spread <= 0:
            problems.append("commissioning spread threshold is invalid")
        return tuple(problems)

    def assert_authorized(self, *, now: datetime) -> None:
        problems = self.blockers(now=now)
        if problems:
            raise CommissioningRefused("; ".join(problems))
