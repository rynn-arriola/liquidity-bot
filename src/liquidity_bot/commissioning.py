"""Hard-locked authorization for one-market live commissioning.

This module does not connect to Polymarket. It turns the operator's explicit
risk acceptance into a narrow, reproducible commissioning configuration. The
actual runner must still pass exchange-state preflight before any mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .entry_quality import EntryQualityPolicy, load_entry_quality_policy
from .entry_expiry import EntryExpiryPolicy
from .risk_windows import RiskWindowPolicy, load_risk_window_policy
from .entry_order_policy import (
    EntryOrderPolicy, EntryOrderPolicyError, load_entry_order_policy)
from .quote_size import (
    DEFAULT_QUOTE_SIZE, MAX_QUOTE_SIZE, MIN_QUOTE_SIZE, parse_quote_size)
from .slot_capacity import DEFAULT_SLOT_CAPACITY, parse_slot_capacity

NEW_YORK = "America/New_York"
AUTO_REWARD_MARKET = "__AUTO_REWARD_MARKETS__"
RUNTIME_QUOTE_SIZE_PATH = Path("data/commissioning_quote_size")
RUNTIME_SLOT_CAPACITY_PATH = Path("data/commissioning_slot_capacity")
HARD_STOP_SIZE_10_32_USD = Decimal("90")


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
    """Bounded experimental authorization derived from one quote quantity.

    ``hard_stop_loss_usd`` is the sole money-loss stop: a fixed configured
    $25 per six-hour ET window, based on confirmed closed-result accounting.
    Passing ``risk_window_policy=None`` retains the historical daily policy
    for replay; production always loads the validated window policy.
    The stressed-exit entry cap is an economic eligibility check, not a stop.
    """

    market_slug: str
    economic_risk_accepted: bool
    exclusive_api_key_confirmed: bool
    maker_lab_stopped_confirmed: bool
    arm_live: bool = False
    recovery_only: bool = False
    # CLI loads the production policy; legacy replay constructors remain GTC.
    entry_expiry_policy: EntryExpiryPolicy = field(
        default_factory=lambda: EntryExpiryPolicy(enabled=False))
    # Raised 1 -> 5 on 2026-08-14, matching maker-lab's QUOTE_SIZE. At one
    # contract both the maker rebate and the taker fee round to zero (section 1
    # of FINDINGS: per-contract economics are ~37% worse at size 1), and reward
    # share is size-linear, so size 1 earns a rounding error. Worst case is
    # 5 x 8 markets x 2 sides x ~$0.50 = $40 was the original commissioning
    # notional. The current breadth-scaled HARD policy is defined separately.
    # Raised 5 -> 10 on 2026-08-20, returned to 5 on 2026-08-24, then restored
    # to 10 later that day by operator decision. Size is leverage, not edge:
    # FINDINGS measured reward
    # and unwind BOTH scaling linearly (25x size -> 24.4x reward, gate_ratio
    # 0.0310 -> 0.0305), so doubling pays more reward when a quote qualifies
    # but also doubles what a bad fill costs without improving the ratio. The
    # persisted operator setting is currently 10; 5 remains the safe fallback
    # when no runtime setting file exists.
    #
    # Everything derived from contract count moves with it or the config stops
    # being coherent. This includes the portfolio cap, but not the fixed window stop;
    # size is restart-scoped so none can move under live orders.
    quote_size: Decimal = DEFAULT_QUOTE_SIZE
    # Eight is the measured ceiling, not a preference. The private
    # WebSocket accepts 10 subscriptions per connection; positions must be
    # scoped one market per subscription because positionSubscription
    # omits marketSlug, while orders can be account-wide because order
    # events always carry it. 1 orders + 8 positions + 1 balance = 10.
    # Twenty markets use three private connections (8 + 8 + 4); 24 fills
    # those three shards and the authorized 32-slot canary fills four complete
    # shards (8 + 8 + 8 + 8).
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
    # The socket sustains at least 29, while the public documented subscription
    # limit is 100. REST fallback, account proof, mutations, recovery bursts
    # and the fourth private shard remain separate constraints. A fully silent
    # market stream cannot keep every flat book inside the five-second entry
    # freshness bound at any currently tested wide slate, so 32 is canary-only
    # and relies on the existing fail-closed stream/recovery behavior rather
    # than pretending REST can replace the stream.
    #
    # Worst case exposure: both sides of a market risk quote_size x p +
    # quote_size x (1 - p) = quote_size. The portfolio control funds the exact
    # restart-scoped width plus ten more quote-size units. That preserves the
    # operator's original 20 + 10 design when capacity changes instead of
    # silently shrinking its cushion. At size 13 / 24 this is 34 x 13 = $442.
    max_markets: int = DEFAULT_SLOT_CAPACITY
    # Restart-scoped pending-entry policy loaded from the validated operator
    # config. Its order counts are automatically clamped to two orders per
    # configured market, while its percentage thresholds follow the
    # breadth/quantity-scaled HARD value below.
    entry_order_policy: EntryOrderPolicy = field(
        default_factory=load_entry_order_policy)
    entry_quality_policy: EntryQualityPolicy = field(
        default_factory=load_entry_quality_policy)
    risk_window_policy: RiskWindowPolicy | None = field(
        default_factory=load_risk_window_policy)
    # maker-lab exit latency, 112 reconstructed position episodes on
    # 2026-08-11/12: a passive exit resolved within 30s for only 26% of
    # episodes, but 46% by 900s (median 100s, p75 653s). Crossing costs a
    # certain ~$0.01 taker commission; holding one contract risks at most
    # ~$0.99 and their markout was positive at 60s, 300s and 900s. Waiting is
    # therefore the cheaper side of the trade.
    max_inventory_age_seconds: int = 900
    # Tiny fractional residues have historically occupied a full reward slot
    # for roughly the whole 900-second passive window even though 0.01 orders
    # are accepted and their maximum exposure is only cents. After five
    # minutes, 0.01-0.09 contract positions use the exact-position IOC path.
    small_residual_min_qty: Decimal = Decimal("0.01")
    small_residual_max_qty: Decimal = Decimal("0.09")
    small_residual_timeout_seconds: int = 300
    # Kickoff overrides the inventory clock. maker-lab disabled its forced
    # exit on the same "holding is cheaper" evidence and the residue reached
    # settlement for -$88.20, 86% of that session's loss. Never settle.
    force_exit_before_start_seconds: int = 300
    # Risk values are computed properties below. At size 10 the stressed-exit
    # entry cap is $40 and the size-10/32-slot HARD reference is $90.
    # What an EARLY cross may cost, before the kickoff deadline makes one
    # mandatory. A third member of the family split on 2026-08-14: it read
    # the old shared $40 loss value and so could never fire, because five contracts
    # cannot lose $40. "Stop the session after losing $40" and "do not pay
    # more than $4 to leave one position ahead of the deadline" are different
    # statements, and the entry gate is a third thing again -- it keeps
    # the stressed-exit cap, so entry behaviour is unchanged by this.
    #
    # Chosen from the 17 crossed losses measured 2026-08-16 (median $2.32,
    # p75 $3.23, p90 $6.15): $4 blocks the three worst and defers $16.96 of
    # $41.96, 40% of the damage from 18% of the events, leaving the median
    # exit alone. Tighter values are not a tail cut -- $0.75 blocks 13 of 17 --
    # and a cap defers a loss rather than avoiding it, so buying that trade
    # needs mean-reversion evidence between the inventory clock and kickoff
    # that we do not have yet. See FINDINGS.
    # Doubled with quote_size on 2026-08-20 so it kept its MEANING. The $4
    # was chosen from 17 crossed losses at size 5 (median $2.32, p75 $3.23,
    # p90 $6.15) to block the three worst while leaving the median exit alone.
    # At size 10 those same trades are median $4.64 and p75 $6.46, so a $4 cap
    # would sit BELOW the median and refuse almost every early cross --
    # silently converting the exit policy into "ride to the kickoff flatten".
    # FINDINGS measures P&L degrading monotonically with holding time
    # (+23.8c under 300s, -23.5c past an hour), so that would have been a real
    # cost taken by accident rather than by decision. It returned to $4 with
    # quote size 5, then back to $8 when the operator restored size 10 later
    # on 2026-08-24.
    # Exposure budgets remain distinct from the HARD stop. Per-market
    # exposure has nine-times headroom. Portfolio exposure funds 20 fully
    # two-sided slots plus the operator-approved 50% proportional cushion.
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
    # maker-lab's entry rule, adopted 2026-08-14 by operator decision. Fresh
    # selection still asks whether the spread is at least 2c. The operator's
    # 2026-09-03 one-tick touch exception applies only after selection when a
    # bound live book tightens; risk and capital caps remain unchanged.
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
    earnings_refresh_seconds: int = 900
    auto_select_reward_markets: bool = False
    market_selection_refresh_seconds: int = 900
    # A bound pregame owner can temporarily fail current reward economics even
    # though its program remains active.  Do not churn on a moving book: only a
    # continuous ten-minute refusal may offer the flat, order-free slot to the
    # normal fifteen-minute replacement scan.
    economic_idle_replacement_seconds: int = 600
    # Was 25. Only ~33 markets survive the day_of and catalog-spread filters,
    # so 25 left a real blind spot for no reason: the census covered all 33 in
    # 40 seconds. 100 covers the whole candidate pool with room to grow, at
    # ~25s per scan against a 900s interval.
    max_selection_book_candidates: int = 100
    minimum_entry_spread: Decimal = Decimal("0.02")
    minimum_entry_price: Decimal = Decimal("0.10")
    manual_stop_only: bool = True

    @property
    def max_early_exit_loss_usd(self) -> Decimal:
        """Scale the size-5 $4 early-cross calibration with quantity."""

        return self.quote_size * Decimal("0.8")

    @property
    def max_stressed_exit_loss_usd(self) -> Decimal:
        """Keep the size-10 $40 stressed-exit entry check proportional."""

        return self.quote_size * Decimal("4")

    @property
    def hard_stop_loss_usd(self) -> Decimal:
        """Fixed window dollars, or the explicit historical daily replay value."""

        if self.risk_window_policy is not None:
            return self.risk_window_policy.stop_loss_usd
        return (
            HARD_STOP_SIZE_10_32_USD
            * self.quote_size
            / Decimal("10")
            * Decimal(self.max_markets)
            / Decimal("32")
        )

    @property
    def full_entry_order_capacity(self) -> int:
        """Maximum two-sided building orders at configured market breadth."""

        return self.max_markets * 2

    def entry_order_cap_for_loss(self, loss_usd: Decimal) -> int:
        """Return the operator-selected cap at the current HARD utilization.

        The runtime persists only downward transitions within a risk window, so
        this pure calculation may safely return a looser cap after a mark
        recovers.  The durable ratchet decides whether that recovery can widen
        the live order budget (it cannot until the next window).
        """

        if self.risk_window_policy is not None:
            return self.risk_window_policy.cap(
                loss_usd, full_cap=self.full_entry_order_capacity)
        loss = max(Decimal("0"), Decimal(loss_usd))
        hard = self.hard_stop_loss_usd
        fraction = Decimal("0") if hard <= 0 else loss / hard
        return self.entry_order_policy.loss_cap(
            fraction, full_cap=self.full_entry_order_capacity)

    def entry_order_cap_for_time(self, observed_at: datetime) -> int:
        """Return the fixed window ceiling, or historical daily clock band."""

        if (observed_at.tzinfo is None
                or observed_at.utcoffset() is None):
            raise ValueError("observed_at must be timezone-aware")
        if self.risk_window_policy is not None:
            return min(self.full_entry_order_capacity,
                       self.risk_window_policy.max_pending_entry_orders)
        try:
            return self.entry_order_policy.time_cap(
                observed_at, full_cap=self.full_entry_order_capacity)
        except EntryOrderPolicyError as exc:
            raise CommissioningRefused(str(exc)) from exc
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover - defensive domain wrap
            raise CommissioningRefused(str(exc)) from exc

    def entry_order_cap_is_overnight(self, observed_at: datetime) -> bool:
        """Return whether the configured lower clock band applies."""

        if self.risk_window_policy is not None:
            return False
        try:
            return self.entry_order_policy.is_overnight(observed_at)
        except EntryOrderPolicyError as exc:
            raise CommissioningRefused(str(exc)) from exc
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover - defensive domain wrap
            raise CommissioningRefused(str(exc)) from exc

    @property
    def max_market_exposure_usd(self) -> Decimal:
        """Keep nine quote-notionals of headroom in each market."""

        return self.quote_size * Decimal("9")

    @property
    def max_portfolio_exposure_usd(self) -> Decimal:
        """Fund every configured slot plus ten quote-size cushion units."""

        return self.quote_size * Decimal(self.max_markets + 10)

    @property
    def execution_fee_observation_usd(self) -> Decimal:
        """Scale the size-10/24-slot $10 ET-day fee telemetry reference."""

        return (
            self.quote_size
            * Decimal(self.max_markets)
            / Decimal("24")
        ).quantize(Decimal("0.01"))

    @property
    def weekly_operating_reserve_usd(self) -> Decimal:
        """Keep seven daily hard-stop budgets while rewards remain pending."""

        windows_per_day = 4 if self.risk_window_policy is not None else 1
        return self.hard_stop_loss_usd * Decimal(7 * windows_per_day)

    @classmethod
    def diagnostic_defaults(
        cls,
        *,
        quote_size: Decimal | None = None,
        runtime_quote_size_path: Path = RUNTIME_QUOTE_SIZE_PATH,
        runtime_slot_capacity_path: Path = RUNTIME_SLOT_CAPACITY_PATH,
    ) -> CommissioningConfig:
        """Return current defaults for read-only operator diagnostics.

        The false authorization flags make this object impossible to arm.
        Keeping diagnostics on the same dataclass defaults as the runner
        prevents quote size, horizon, and candidate-budget drift.
        """

        if quote_size is None:
            quote_size = configured_quote_size(runtime_quote_size_path)
        max_markets = configured_slot_capacity(runtime_slot_capacity_path)
        return cls(
            market_slug=AUTO_REWARD_MARKET,
            economic_risk_accepted=False,
            exclusive_api_key_confirmed=False,
            maker_lab_stopped_confirmed=False,
            quote_size=quote_size,
            max_markets=max_markets,
        )

    def reward_selection_policy(self):
        """Build the selector policy shared by live and diagnostic flows."""

        from .market_selector import RewardSelectionPolicy

        return RewardSelectionPolicy(
            quote_size=float(self.quote_size),
            projection_horizon_seconds=(
                self.reward_projection_horizon_seconds),
            projection_haircut=float(self.reward_projection_haircut),
            exit_depth_haircut=.5,
            exit_cost_safety_multiple=float(
                self.reward_cost_safety_multiple),
            max_book_candidates=self.max_selection_book_candidates,
            minimum_entry_spread=float(self.minimum_entry_spread),
            minimum_entry_price=float(self.minimum_entry_price),
            enforce_reward_cost_gate=self.enforce_reward_cost_gate,
            entry_quality_policy=self.entry_quality_policy,
        )

    def blockers(self, *, now: datetime) -> tuple[str, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        problems: list[str] = []
        if type(self.recovery_only) is not bool:
            problems.append("recovery_only must be boolean")
        if not isinstance(self.entry_expiry_policy, EntryExpiryPolicy):
            problems.append("entry expiry policy must be validated")
        if not self.arm_live:
            problems.append("live arming flag is absent")
        if not self.economic_risk_accepted:
            problems.append("commissioning economic risk is not accepted")
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
        try:
            parse_quote_size(self.quote_size)
        except ValueError as exc:
            problems.append(f"commissioning {exc}")
        try:
            parse_slot_capacity(self.max_markets)
        except ValueError as exc:
            problems.append(f"commissioning {exc}")
        if self.max_portfolio_exposure_usd < self.max_market_exposure_usd:
            problems.append(
                "the portfolio exposure budget must cover a single market")
        if not self.manual_stop_only:
            problems.append("commissioning must remain armed for operator stop")
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
        if self.earnings_refresh_seconds != 900:
            problems.append(
                "commissioning earnings must refresh every 900 seconds")
        if (self.max_inventory_age_seconds != 900
                or self.force_exit_before_start_seconds != 300
                or self.small_residual_min_qty != Decimal("0.01")
                or self.small_residual_max_qty != Decimal("0.09")
                or self.small_residual_timeout_seconds != 300):
            problems.append(
                "commissioning inventory limits must remain a 900-second "
                "passive exit window, a 300-second pre-kickoff flatten, and "
                "a five-minute cashout for 0.01-0.09 contract residuals")
        if self.force_exit_before_start_seconds >= self.max_inventory_age_seconds:
            problems.append(
                "the pre-kickoff flatten must fire before the inventory clock")
        if self.minimum_entry_spread != Decimal("0.02"):
            problems.append(
                "commissioning fresh selection and locked-book fallback must "
                "retain the two cents threshold")
        if self.minimum_entry_spread <= 0:
            problems.append("commissioning spread threshold is invalid")
        if self.minimum_entry_price != Decimal("0.10"):
            problems.append(
                "commissioning entry quotes must remain between 0.10 and 0.90")
        if self.economic_idle_replacement_seconds != 600:
            problems.append(
                "commissioning must retain an economically idle market for "
                "ten continuous minutes before replacement")
        return tuple(problems)

    def assert_authorized(self, *, now: datetime) -> None:
        problems = self.blockers(now=now)
        if problems:
            raise CommissioningRefused("; ".join(problems))


def configured_quote_size(
    path: Path = RUNTIME_QUOTE_SIZE_PATH,
) -> Decimal:
    """Read the restart-scoped quote size used by operator diagnostics."""

    if not path.exists():
        return DEFAULT_QUOTE_SIZE
    raw = path.read_text(encoding="utf-8").strip()
    try:
        return parse_quote_size(raw)
    except ValueError as exc:
        raise CommissioningRefused(f"runtime {exc}") from exc


def configured_slot_capacity(
    path: Path = RUNTIME_SLOT_CAPACITY_PATH,
) -> int:
    """Read the last validated restart-scoped capacity for diagnostics."""

    if not path.exists():
        return DEFAULT_SLOT_CAPACITY
    raw = path.read_text(encoding="utf-8").strip()
    try:
        return parse_slot_capacity(raw)
    except ValueError as exc:
        raise CommissioningRefused(f"runtime {exc}") from exc
