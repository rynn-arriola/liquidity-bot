"""Explicitly armed liquidity commissioning runner.

The economic evidence gate remains red. This runner exists only for the
operator-accepted experiment and is mechanically constrained to 20 selected
rewarded markets, a restart-scoped whole quote size from 5 to 10 contracts, an
operator-controlled stop, an $80 normal session stop, and an $80 emergency hard
stop.

Importing this module has no side effect. Exchange access is possible only
through ``main()`` with all explicit arming acknowledgements.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
import socket
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from polymarket_us import AsyncPolymarketUS

from .account_snapshots import AsyncAccountSnapshotGateway
from .bot_era import bot_era_rewards_by_status
from .adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from .commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    CommissioningUnavailable,
)
from .discord_summary import (
    LiquiditySummary, build_liquidity_summary, build_runtime_alert)
from .errors import AdapterError, RuntimeInvariantViolation
from .execution_ledger import ExecutionLedger, LedgerConflict, QuoteObservation
from .fill_hazard import MondayFillHazardModel
from .live_orders import (
    AsyncTradingGateway,
    LiveOrderExecutor,
    OPEN_MARKET_STATUSES,
    OrderOutcomeUncertain,
    OrderRequest,
    build_order_payload,
)
from .live_session import (
    ControlledActionDriver,
    ControlledTrialStartup,
    MarketKillSwitch,
    PrivateStateGate,
)
from .market_state import MarketState, RetiredMarket
from .market_selector import (
    RewardMarketSelection,
    RewardMarketSelector,
)
from .models import AccountBalance, BookSnapshot, MarketIncentive, MarketMetadata
from .notifications import DiscordNotifier
from .readiness import ReadinessVerdict
from .reward_calendar import risk_day
from .reconciler import (
    ActionKind,
    DesiredStateReconciler,
    MarketFrame,
    PlanAction,
    ReconcilerPolicy,
    WorkingOrder,
    entry_quote_price,
)
from .rewards import (
    AmbiguousIncentiveError,
    IncentivePeriod,
    RewardProjection,
    evaluate_order_score,
    project_order_reward,
    select_applicable_period,
)
from .risk import OpportunityEconomics
from .runtime_safety import SessionRiskGuard, SingleInstanceLease
from .runtime_secrets import load_runtime_secrets
from .shadow_math import cross_exit
from .queue_measurement import QueueMeasurementTracker
from .streams import MarketReadOnlyStream, PrivateStreamPool, TradeReadOnlyStream
from .venue_state import PrivateLedgerProjector


Clock = Callable[[], datetime]

# Generous: with backoff capped at 60s this is roughly an hour of retrying
# before a transient startup condition is finally treated as a refusal.
MAX_STARTUP_UNAVAILABLE_ATTEMPTS = 60

# Refresh before the risk guard's hard freshness edge. A twenty-market sweep
# can itself take several seconds when individual books need REST recovery.
DECISION_EQUITY_REFRESH_MARGIN_SECONDS = 5.0
DECISION_STATE_REFRESH_MARGIN_SECONDS = 5.0

# Conditions that describe one market's inventory or exit, never the account.
# The reconciler plans per market, so these are per-market verdicts: the right
# response is to stop quoting that market and keep working its exit, not to end
# a session in which every other market is healthy and flat. Kept as an
# explicit set so an unrecognised future critical still fails closed.
# A retired market waits this long before its exit is planned again. Long
# enough that a failing exit cannot spin the loop, short enough that many
# retries still fit inside the 300-second pre-kickoff flatten window.
# How many times a "recoverable" strategy condition may fail to recover for
# one market before that market is retired instead of retried forever.
MAX_RECOVERY_ATTEMPTS = 12

# Ordinary reconnects are bounded so a local/schema defect cannot hold a live
# process dormant forever. Positively classified venue transport/5xx outages
# cross into the maintenance coordinator below instead. Neither path infers
# flatness; shutdown remains the only authority for that proof.
MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS = 12
MAX_MARKET_STREAM_RECOVERY_ATTEMPTS = 12
MAX_BOUND_STREAM_RECOVERY_ATTEMPTS = 12

# A venue outage is different from an unsafe account.  After the ordinary
# bounded reconnect has proved that the failure persists, keep this process
# dormant and probe only complete authenticated reads.  Three passes one
# minute apart means "back" is sustained for about two minutes, rather than a
# single lucky 200 during a rolling maintenance restart.
MAINTENANCE_FAILURE_THRESHOLD = 3
MAINTENANCE_STABLE_READS = 3
MAINTENANCE_POLL_SECONDS = 60.0

RETIRED_EXIT_RETRY_SECONDS = 60

# After this many failed rounds the runner stops planning a retired market at
# all. Ten rounds spans ten minutes, which covers the whole 300-second
# pre-kickoff flatten window with room to spare. Past that the exit is not
# coming back and replanning forever would spin the loop for the rest of the
# session -- including after kickoff, when nothing can be done anyway. The
# position is bounded at one contract, shutdown still makes a final attempt,
# and every other market keeps quoting.
MAX_RETIREMENT_ROUNDS = 10

# How recently a fault must have landed to count toward the systemic bound.
# The bound exists to catch a whole slate failing AT ONCE. Measured instead
# against a session-lifetime total it catches every long HEALTHY session,
# because that total only ever grows: `unrecoverable_book_*` is permanent by
# PERMANENT_RETIREMENT_PREFIXES, so a retired market never leaves the count
# and a session trips the bound simply by outliving twenty of its markets.
# Fifteen minutes covers a real cascade -- book_stale needs MAX_RECOVERY_ATTEMPTS
# failures at ~60s each, so a dead feed retires the whole slate inside ~12
# minutes -- while a day's ordinary attrition (measured 2026-08-16: 31 markets
# over 7h09m, about 4.4/hour) never comes close.
RETIREMENT_BURST_WINDOW_SECONDS = 900

# How long a retirement bars a market from being quoted or reselected.
#
# This was the rest of the session, and with only max_markets slots that is a
# slow starvation. Measured 2026-08-16: twenty markets were barred inside the
# first forty minutes and the quoting slate fell 19 -> 11 -> 8 -> 7 -> 6 while
# the venue still had candidates. Thirty of the retirements were
# `unrecoverable_book_stale`, which describes the book AT THAT MOMENT -- a book
# too quiet to refresh at 00:30 is routinely fine at 03:00, and banning it for
# seven hours spends a slot to avoid a condition that has already passed.
#
# Thirty minutes is chosen from both directions: long enough to break the
# retire/release/retire churn that made the ban permanent in the first place
# (observed at ~45 seconds on chevic-scovla, 2026-08-15), and short enough that
# a market returns while its game is still ahead of it.
RETIREMENT_COOLDOWN_SECONDS = 1800

RETIRING_STRATEGY_CRITICALS = frozenset({
    "hard_exit_attempt_limit_exceeded",
    "hard_exit_retry_window_exceeded",
    "hard_exit_blocked_cancel_unconfirmed",
    "hard_exit_insufficient_stressed_depth",
    "hard_exit_price_cap_exceeded",
    "inventory_basis_or_clock_unknown",
    "inventory_clock_in_future",
})

RECOVERABLE_STRATEGY_CRITICALS = frozenset({
    "book_stale",
    "execution_state_unknown",
    # The exit book was too thin to cross before the kickoff deadline. Holding
    # is the correct response and the pre-kickoff flatten still forces the
    # position out, so ending the session here would trade a bounded
    # one-owner hold for hours of lost quoting across every other market.
    "hard_exit_deferred_thin_book",
    # Reachable only while flat, and repaired by refetching the book and
    # rebinding the singleton stream scope. Every remaining critical implies
    # either held inventory or an ownership violation, which stay terminal.
    "book_market_mismatch",
})


@dataclass(frozen=True)
class CommissioningResult:
    started_at: datetime
    cutoff_at: datetime | None
    stopped_at: datetime
    stop_reason: str
    flat: bool
    orders_sent_to_polymarket: int


class CommissioningRunner:
    """Compose the tested primitives into one deliberately narrow live loop."""

    # How long "up but resting nothing" may last before it is worth waking
    # someone. Long enough that a rotation, a top-up rebind or a slate between
    # kickoffs passes quietly; short enough that neither 2026-08-16 outage
    # could have run its full length unannounced. The summary that drives it
    # runs every 5 minutes, so this is effectively three summaries of silence.
    QUIET_ALARM_SECONDS = 900.0

    # How often to re-ask the venue about an order we have lost sight of. See
    # the throttle in `_rest_reconcile`: unbounded re-asking puts a 404's
    # retry backoff on the critical path of every reconcile.
    ABSENT_RECHECK_SECONDS = 60.0

    # How often to re-issue a cancel the venue has confirmed did not land.
    # Well under `unresolved_mutation_timeout_seconds`, because the whole point
    # is to resolve the intent before that stop fires: at 10s the reconcile
    # gets roughly six attempts inside a 60s budget. Not faster, because each
    # attempt is a venue mutation sharing one 4 req/s governor with quoting,
    # and a cancel that keeps failing will not start working in 2s.
    # The two blockers that mean "this day lost too much", as opposed to
    # something being wrong with the account. Only these pause; ownership
    # violations, clock failures and named criticals stay terminal, and the
    # $80 emergency equity ceiling is checked earlier and never reaches here.
    LOSS_STOP_BLOCKERS = frozenset({
        "session loss limit reached",
        "market loss limit reached",
    })

    CANCEL_RETRY_SECONDS = 10.0

    # Background terminal repair is grouped by owner, not by order. Five
    # owners lets a twenty-slot recovery complete one fair circuit in four
    # passes while bounding how much REST work can precede normal decisions.
    # Reads within the batch run concurrently, but the shared limiter still
    # starts at most four requests per second. This is important for the
    # venue's multi-minute NotFound consistency gaps: one slow id must not
    # serialize every other owner's positive terminal proof.
    UNRESOLVED_RECONCILE_OWNER_BATCH_SIZE = 5

    # How long to wait before even re-checking a market that is retired, flat,
    # order-free and past kickoff. Nothing about it can change in a way this
    # process cares about, so the only cost of a long interval is bookkeeping
    # latency on a slot that is already barred.
    DEAD_MARKET_REFRESH_SECONDS = 300.0

    def __init__(
        self,
        client: Any,
        config: CommissioningConfig,
        ledger: ExecutionLedger,
        notifier: DiscordNotifier,
        *,
        clock: Clock | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        market_stream_factory: Callable[[Any], Any] = MarketReadOnlyStream,
        private_stream_factory: Callable[[Any], Any] = PrivateStreamPool,
        trade_stream_factory: Callable[[Any], Any] = TradeReadOnlyStream,
        reward_selector: RewardMarketSelector | None = None,
        fill_hazard_model: MondayFillHazardModel | None = None,
    ):
        self.client = client
        self.config = config
        self.ledger = ledger
        self.notifier = notifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        limiter = AsyncIntervalLimiter.from_rate(
            float(config.rest_requests_per_second))
        self.gateway = AsyncReadOnlyGateway(client, limiter=limiter)
        self.fill_hazard_model = fill_hazard_model or MondayFillHazardModel()
        self.reward_selector = reward_selector
        if self.reward_selector is None and config.auto_select_reward_markets:
            self.reward_selector = RewardMarketSelector(
                self.gateway,
                config.reward_selection_policy(),
                fill_hazard_model=self.fill_hazard_model,
            )
        self.account = AsyncAccountSnapshotGateway(
            self.gateway, resolve_client_id=ledger.resolve_client_order_id)
        self.executor = LiveOrderExecutor(
            AsyncTradingGateway(self.gateway), ledger)
        self.driver: ControlledActionDriver | None = None
        self.kill_switch = MarketKillSwitch(self.executor, ledger)
        self.projector = PrivateLedgerProjector(ledger)
        self.gate = PrivateStateGate(max_silence_seconds=30)
        # Set when `_open_streams_for_active_market` fails and leaves both
        # stream attributes pointing at objects it has already closed. The run
        # loop drives `_recover_bound_streams` until it clears. Without it the
        # runner stays up holding closed streams with `gate.connected` false,
        # every market reports `execution_state_unknown` forever, and nothing
        # ever retries -- observed live 2026-08-16, 24 minutes of silent
        # downtime before an operator noticed. See BUGFIXES 2026-08-16.
        self._streams_need_rebind = False
        # Set once when fresh reward metadata positively proves that the exact
        # program which authorized a bound slot has ended. The run loop spends
        # it on one immediate incremental top-up attempt; a failed attempt then
        # uses the ordinary bounded backoff instead of retrying every pass.
        self._reward_end_top_up_requested = False
        # "Up, healthy, and resting nothing" -- see `_alarm_if_quoting_nothing`.
        self._quiet_since: datetime | None = None
        self._quiet_alarm_sent = False
        # exchange_order_id -> when we last asked the venue about an absence.
        self._absent_asked_at: dict[str, datetime] = {}
        # exchange_order_id -> when we last re-issued a cancel the venue had
        # confirmed did not land. See `_retry_uncertain_cancels`.
        self._cancel_retried_at: dict[str, datetime] = {}
        # market -> when it last placed or cancelled. A market between a
        # cancel and its re-place holds no order for a few seconds and is not
        # stuck; without this it was counted as stuck and the alarm fired on
        # ordinary churn. See `_cycling_markets`.
        self._last_action_at: dict[str, datetime] = {}
        # Recovery delay belongs to the market that requested it. The old
        # sweep let one unhealthy slot pause the other nineteen.
        self._next_decision_at: dict[str, datetime] = {}
        # Markets whose CANCEL intent became terminal on the private stream.
        # They are serviced and replanned before the next full bound-market
        # pass. Without this, a confirmed cancel waits behind REST service and
        # decisions for the whole slate before its replacement is considered.
        self._priority_replan_slugs: set[str] = set()
        # Resolve one attributable market per background pass, round-robin.
        # Asking every unresolved id serially before decisions recreated the
        # same twenty-slot head-of-line stall the per-market scheduler removed.
        self._unresolved_reconcile_cursor = 0
        self.market_stream_factory = market_stream_factory
        self.private_stream_factory = private_stream_factory
        # Public trades are measurement-only and use their own socket. They
        # never share the book/private recovery path or the REST limiter that
        # authorizes mutations. A failed study therefore loses coverage, not
        # orders or reward time.
        self.trade_stream_factory = trade_stream_factory
        self._queue_measurement = QueueMeasurementTracker(
            ledger, collection_started_at=self._now())
        self._queue_measurement_error_count = 0
        self._queue_measurement_last_error_at: datetime | None = None
        self._queue_measurement_stream_healthy = False
        self.market_stream = market_stream_factory(client)
        self.private_stream = private_stream_factory(client)
        self._markets: dict[str, MarketState] = {}
        # Markets this session has stopped quoting but must still flatten.
        # They stay in the active set so their exit keeps being worked.
        self._retired: dict[str, RetiredMarket] = {}
        # Did the last `_refresh_quiet_book_if_due` actually fetch a book, or
        # return early because it was not due? The caller clears a failure
        # counter on success and both look like success without this.
        self._refreshed_book_this_call: bool = False
        # Every slug this session has retired, for reporting only. Barring is
        # driven by `_retired_until` so it expires; see the constant.
        self._retired_ever: set[str] = set()
        # slug -> the moment its retirement stops barring it.
        self._retired_until: dict[str, datetime] = {}
        # Bound by recovery to be exited, not retired for a fault. Barred for
        # the whole session by `_barred_slugs`, never on a cooldown: their
        # games have already started, so they can never become quotable again.
        self._recovery_slugs: set[str] = set()
        self._active_market_slugs: tuple[str, ...] = (
            () if config.auto_select_reward_markets
            else (config.market_slug,))
        for slug in self._active_market_slugs:
            self._markets[slug] = MarketState(slug)
        self._market_selection: RewardMarketSelection | None = None
        self._market_selection_observed_at: datetime | None = None
        self.balance: AccountBalance | None = None
        self.started_at: datetime | None = None
        # Realised P&L already on the ledger when this session began. The loss
        # stop measures the change since then, never the lifetime total: it is
        # a guardrail against blowing the account *today*, not an accounting
        # record. Unbounded, once the ledger had ever lost $40 the bot could
        # never start again -- observed 2026-08-15, when the fill back-fill let
        # the stop see real closed round trips and it totalled several days of
        # test losses on startup and halted in 73 seconds.
        self._realized_baseline: Decimal = Decimal("0")
        self._baseline_day: date | None = None
        # Settled cash at session start. The session stop measures the change
        # in this rather than a total rebuilt from fills: cash moves only when
        # a trade actually executes, so there is no price frame to decode and
        # no fill that can be missed. Every P&L defect found 2026-08-15 --
        # counterparty legs recorded as ours, closing fills lost on a restart,
        # prices quoted against the opposite outcome -- came from rebuilding a
        # number the venue already knows.
        #
        # Settled cash, NOT marked equity. The reason marked equity was
        # rejected (320 contracts move $38 on a twelve-cent mark) does not
        # apply -- cash does not move on paper price changes.
        self._cash_baseline: Decimal | None = None
        self._day_opening_equity: Decimal | None = None
        # (ET risk date, blockers, streak) set by `_stop_reason` when a loss stop
        # pauses, consumed once by `_announce_loss_stop_pause`.
        self._loss_pause_pending: tuple[str, list[str], int] | None = None
        self._loss_pause_announced_day: str | None = None
        # Footer identity, so an alert says which box and which build produced
        # it without the operator having to ask.
        self._host_name = socket.gethostname()
        try:
            self._deployed_commit = (
                Path("DEPLOYED_COMMIT").read_text(
                    encoding="utf-8").splitlines()[0].strip() or None)
        except Exception:
            self._deployed_commit = None
        self._realized_baseline_by_market: dict[str, Decimal] = {}
        self.cutoff_at: datetime | None = None
        self.risk_guard: SessionRiskGuard | None = None
        self.reconciler = DesiredStateReconciler(ReconcilerPolicy(
            quote_size=config.quote_size,
            max_inventory_age_seconds=config.max_inventory_age_seconds,
            # Exposure budgets, not the loss stops. The stops live in
            # SessionRiskGuard and fire on realised P&L; these bound how much
            # open worst case may exist at once.
            max_market_worst_loss=float(config.max_market_exposure_usd),
            max_portfolio_worst_loss=float(config.max_portfolio_exposure_usd),
            max_exit_loss=float(config.max_market_loss_usd),
            max_early_exit_loss=float(config.max_early_exit_loss_usd),
            exit_depth_haircut=.5,
            max_book_age_seconds=5,
            cancel_confirm_grace_seconds=5,
            max_hard_exit_attempts=config.max_hard_exit_attempts,
            max_hard_exit_retry_seconds=config.max_hard_exit_retry_seconds,
            hard_exit_confirmation_timeout_seconds=(
                config.hard_exit_confirmation_timeout_seconds),
            minimum_entry_spread=float(config.minimum_entry_spread),
            minimum_entry_price=float(config.minimum_entry_price),
            force_exit_before_start_seconds=float(
                config.force_exit_before_start_seconds),
        ))
        self._stop_requested: str | None = None
        # Cleared only when shutdown gives up before proving flat, so the
        # process can report exit 3 instead of silently looking clean.
        self._shutdown_proved_flat = True
        self._score_seconds = 0.0
        self._score_seconds_by_market: dict[str, float] = {}
        self._score_observed_at: datetime | None = None
        self._earnings_observed_at: datetime | None = None
        self._earnings_by_status: dict[str, Decimal] = {}
        # Account-wide but date-scoped to this bot; see `bot_era.py`.
        self._bot_era_rewards: dict[str, Decimal] = {}
        self._orders_before = 0
        self._runtime_error_counts: dict[str, int] = {}
        self._selection_scan_seconds_last: float | None = None
        self._rotation_transition_seconds_last: float | None = None
        # (market, side) -> (label, since). Drives `[eligibility]`. Pruned in
        # `_bind_markets` alongside `_markets`: a market that is dropped and
        # later re-selected must not report the interval it spent UNBOUND as
        # though it had held that state, which would put hour-long `held`
        # values into the one number the log exists to measure.
        self._eligibility_state: dict[tuple[str, str], tuple[str, datetime]] = {}

    def request_stop(self, reason: str = "operator_requested") -> None:
        self._stop_requested = str(reason or "operator_requested")

    @property
    def active_slugs(self) -> tuple[str, ...]:
        """Every market this session may currently hold risk in."""

        return tuple(
            slug for slug in self._active_market_slugs
            if slug and slug != AUTO_REWARD_MARKET)

    @property
    def market_slug(self) -> str:
        """The primary bound market.

        Multi-market work migrates call sites onto explicit slugs one at a
        time; until then this keeps single-market behaviour exactly as it was.
        """

        slugs = self.active_slugs
        if not slugs:
            raise RuntimeError("an active reward market has not been selected")
        return slugs[0]

    def state_for(self, slug: str) -> MarketState:
        """Return the per-market state, creating it on first use."""

        existing = self._markets.get(slug)
        if existing is None:
            existing = MarketState(slug)
            self._markets[slug] = existing
        return existing

    def _bind_markets(self, slugs: tuple[str, ...]) -> None:
        """Replace the active set, discarding state for markets left behind.

        Dropping the old entries matters: a stale book or incentive from a
        market this session no longer holds must never be able to authorize a
        quote or satisfy a freshness check.
        """

        clean = tuple(dict.fromkeys(
            slug for slug in slugs
            if slug and slug != AUTO_REWARD_MARKET))
        if len(clean) > self.config.max_markets:
            raise RuntimeInvariantViolation(
                "market_count_exceeds_authorization",
                f"{len(clean)} markets exceeds the authorized "
                f"{self.config.max_markets}")
        self._active_market_slugs = clean
        self._markets = {
            slug: self._markets.get(slug) or MarketState(slug)
            for slug in clean}
        # Same contract as `_markets` above, for the same reason: state for a
        # market we no longer hold must not survive to describe it later.
        self._eligibility_state = {
            key: value for key, value in self._eligibility_state.items()
            if key[0] in self._markets}
        self._next_decision_at = {
            slug: value for slug, value in self._next_decision_at.items()
            if slug in self._markets}
        self._priority_replan_slugs.intersection_update(self._markets)

    @property
    def _primary(self) -> MarketState:
        return self.state_for(self.market_slug)

    @property
    def market(self) -> MarketMetadata | None:
        return self._primary.market

    @market.setter
    def market(self, value: MarketMetadata | None) -> None:
        self._primary.market = value

    @property
    def incentive(self) -> MarketIncentive | None:
        return self._primary.incentive

    @incentive.setter
    def incentive(self, value: MarketIncentive | None) -> None:
        self._primary.incentive = value

    @property
    def book(self) -> BookSnapshot | None:
        return self._primary.book

    @book.setter
    def book(self, value: BookSnapshot | None) -> None:
        self._primary.book = value

    @property
    def book_observed_at(self) -> datetime | None:
        return self._primary.book_observed_at

    @book_observed_at.setter
    def book_observed_at(self, value: datetime | None) -> None:
        self._primary.book_observed_at = value

    @property
    def _reward_eligible(self) -> frozenset[str]:
        return self._primary.reward_eligible

    @_reward_eligible.setter
    def _reward_eligible(self, value: frozenset[str]) -> None:
        self._primary.reward_eligible = value

    @property
    def _score_by_side(self) -> dict[str, float]:
        return self._primary.score_by_side

    @_score_by_side.setter
    def _score_by_side(self, value: dict[str, float]) -> None:
        self._primary.score_by_side = value

    @property
    def _expected_net_by_side(self) -> dict[str, float]:
        return self._primary.expected_net_by_side

    @_expected_net_by_side.setter
    def _expected_net_by_side(self, value: dict[str, float]) -> None:
        self._primary.expected_net_by_side = value

    @property
    def _reward_projection_by_side(self) -> dict[str, RewardProjection]:
        return self._primary.reward_projection_by_side

    @_reward_projection_by_side.setter
    def _reward_projection_by_side(
        self, value: dict[str, RewardProjection],
    ) -> None:
        self._primary.reward_projection_by_side = value

    @property
    def _economic_blocker_by_side(self) -> dict[str, str]:
        return self._primary.economic_blocker_by_side

    @_economic_blocker_by_side.setter
    def _economic_blocker_by_side(self, value: dict[str, str]) -> None:
        self._primary.economic_blocker_by_side = value

    @property
    def _fill_probability_by_side(self) -> dict[str, float]:
        return self._primary.fill_probability_by_side

    @_fill_probability_by_side.setter
    def _fill_probability_by_side(self, value: dict[str, float]) -> None:
        self._primary.fill_probability_by_side = value

    @property
    def _fill_risk_driver_by_side(self) -> dict[str, str]:
        return self._primary.fill_risk_driver_by_side

    @_fill_risk_driver_by_side.setter
    def _fill_risk_driver_by_side(self, value: dict[str, str]) -> None:
        self._primary.fill_risk_driver_by_side = value

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("runner clock must be timezone-aware")
        return value

    def _identified_recovery_markets(self) -> tuple[str, ...]:
        """Return every ledger market that may still require retirement."""

        slugs = {
            str(row["market_slug"])
            for row in self.ledger.working_orders(owned_only=True)
        }
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.unresolved_intents()
        )
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        )
        slugs.discard("")
        # Recover the whole authorized set, not a single market. This raised
        # when leftover state spanned more than one market, and
        # CommissioningRefused exits 2, which the supervisor never restarts --
        # so the bot stayed down. Written when the runner held one market at a
        # time; with eight quoting, state in several markets after any hard
        # stop is the expected case rather than an anomaly. Observed
        # 2026-08-14T23:03Z, down until an operator noticed.
        #
        # More markets than the authorization is still refused: that is state
        # this session could not have created, so it is not ours to retire.
        if len(slugs) > self.config.max_markets:
            raise CommissioningRefused(
                f"automatic recovery found state in {len(slugs)} markets, "
                f"above the authorized {self.config.max_markets}")
        return tuple(sorted(slugs))

    async def _select_reward_market(self, now: datetime) -> str:
        """Wait for a viable reward market rather than refusing to start.

        A universe with nothing viable right now is the normal case, not a
        misconfiguration: economically authorized windows are short and
        infrequent. Refusing startup returns exit 2, which the supervisor
        treats as terminal, so the process would stay down until an operator
        noticed. Waiting costs nothing, because a runner with no bound market
        holds no orders, no position and no stream subscription.

        Only an operator stop, a loss stop, or a genuine adapter failure ends
        the wait. Every scan is read-only and paced by the shared governor.
        """

        if self.reward_selector is None:
            raise RuntimeError("automatic reward selector is unavailable")
        attempt = 0
        while True:
            attempt += 1
            scan_started = time.monotonic()
            try:
                selection = await self.reward_selector.select(now=self._now())
            finally:
                self._selection_scan_seconds_last = (
                    time.monotonic() - scan_started)
            if selection.selected is not None:
                self._market_selection = selection
                self._market_selection_observed_at = self._now()
                # Bind the whole authorized set, not just the best one.
                # Binding a single market meant the session quoted one market
                # until the first rotation 900 seconds later, so the measured
                # constraint -- authorized uptime, not per-market size -- went
                # untouched for the first quarter hour of every run.
                chosen = tuple(
                    item.market.slug for item in selection.top_viable(
                        self.config.max_markets, exclude=self._barred_slugs()))
                self._bind_markets(
                    chosen or (selection.selected.market.slug,))
                self._runtime_error_counts.pop("startup_market_wait", None)
                return self.market_slug
            self._market_selection = selection
            self._market_selection_observed_at = self._now()
            if self._stop_requested:
                raise CommissioningRefused(
                    "operator stopped while waiting for a viable reward market")
            # Not a fault. The gate rejected every book it scanned, which is
            # the common case -- the backtest authorized one quote in 24 hours.
            # Routing it through _report_runtime_error paged Discord as
            # critical with "new risk is disabled until reconnect and
            # reconciliation pass", which describes a broken connection and is
            # simply untrue here. Alerts that cry wolf on the normal idle state
            # are how a real one gets ignored. Log every scan; say it out loud
            # only once, and only so a silent night is distinguishable from a
            # dead process.
            print(
                f"[market-wait] at={self._now().isoformat()} attempt={attempt} "
                f"executable={selection.executable_catalog_markets} "
                f"books={selection.books_evaluated} viable=0",
                flush=True,
            )
            if attempt == 1:
                await self.notifier.send_async(
                    "Liquidity Bot is scanning and waiting: no market clears "
                    f"the reward gate yet ({selection.executable_catalog_markets} "
                    f"executable, {selection.books_evaluated} books). Nothing is "
                    "wrong; it will quote when one qualifies.",
                    critical=False)
            await self.sleep(
                float(self.config.market_selection_refresh_seconds))

    async def _refresh_contracts(
        self, now: datetime, slug: str | None = None, *,
        for_exit: bool = False,
    ) -> None:
        """Load metadata and incentive for one market.

        ``for_exit`` relaxes the gates that exist to stop us *opening* risk.
        A market we hold a position in may have started, lost its incentive
        programme or left the pregame window, and none of that changes the need
        to get out of it -- it makes the need urgent. Observed
        2026-08-15T01:2xZ: recovery bound a market holding 5 contracts whose
        game had begun, this raised `no longer pregame`, and startup retried
        for over two hours while the position sat there unmanaged.
        """
        state = self.state_for(slug or self.market_slug)
        slug = state.slug
        state.market = await self.gateway.fetch_market(slug)
        incentives = await self.gateway.fetch_incentives([slug])
        state.incentive = incentives.get(slug)
        if for_exit:
            return
        if state.incentive is None:
            raise CommissioningUnavailable(
                f"{slug} has no liquidityProgram incentive")
        if (not state.market.active or state.market.closed
                or state.market.status not in OPEN_MARKET_STATUSES):
            raise CommissioningUnavailable(f"{slug} is not open")
        if state.market.game_start is None:
            raise CommissioningUnavailable(
                f"{slug} start clock is unavailable")
        if state.market.game_start <= now:
            raise CommissioningUnavailable(f"{slug} is no longer pregame")
        try:
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now)
        except AmbiguousIncentiveError as exc:
            raise CommissioningUnavailable(
                f"{slug} has ambiguous incentive periods") from exc
        if period is None:
            ended = next((
                item for item in state.incentive.periods
                if item.program_id == state.reward_program_id
                and item.ends_at is not None
                and item.ends_at <= now
            ), None)
            if ended is not None:
                newly_confirmed = (
                    state.reward_period_ended_at != ended.ends_at
                    or state.reward_period_end_confirmed_at is None)
                state.reward_period_ended_at = ended.ends_at
                state.reward_period_end_confirmed_at = self._now()
                if newly_confirmed:
                    self._reward_end_top_up_requested = True
                return
        if period is None or period.period == "live":
            raise CommissioningUnavailable(
                f"{slug} has no applicable pregame liquidity period")
        state.reward_program_id = period.program_id
        state.reward_program_ends_at = period.ends_at
        state.reward_period_ended_at = None
        state.reward_period_end_confirmed_at = None

    async def _refresh_book(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        state = self.state_for(slug or self.market_slug)
        state.book = await self.gateway.fetch_book(state.slug)
        # Stamp receipt, not request start. REST and limiter latency can be a
        # material fraction of the five-second decision freshness budget.
        state.book_observed_at = self._now()
        state.observe_book_touch(
            state.book, observed_at=state.book_observed_at)
        self._queue_measurement_book(
            state.book, observed_at=state.book_observed_at)
        if state.market is None:
            raise RuntimeError("market metadata is unavailable")
        # Validate both possible passive wire payloads before the first
        # private subscription can authorize mutation.
        size = self.config.quote_size
        build_order_payload(OrderRequest(
            state.market.slug, "bid",
            Decimal(str(state.book.best_bid.price)), size), state.market)
        build_order_payload(OrderRequest(
            state.market.slug, "offer",
            Decimal(str(state.book.best_offer.price)), size), state.market)

    def _accept_book(self, book: BookSnapshot) -> None:
        """Route a streamed book to the market it belongs to.

        The stream already rejects slugs outside its subscription, so an
        unknown slug here means the active set moved on mid-flight. Dropping it
        is correct: binding a book to a market this session no longer holds
        would let stale depth satisfy a freshness check.
        """

        state = self._markets.get(book.slug)
        if state is None:
            return
        observed_at = book.received_at or self._now()
        state.observe_book_touch(book, observed_at=observed_at)
        state.book = book
        state.book_observed_at = observed_at
        self._queue_measurement_book(book, observed_at=observed_at)

    def _queue_measurement_safe(
        self, component: str, operation: Callable[[], Any],
    ) -> Any | None:
        """Run optional telemetry without letting it affect live decisions."""

        try:
            result = operation()
            self._queue_measurement_error_count = 0
            return result
        except Exception as exc:
            self._queue_measurement_error_count += 1
            now = self._now()
            due = (self._queue_measurement_last_error_at is None
                   or (now - self._queue_measurement_last_error_at
                       ).total_seconds() >= 60)
            if due:
                self._queue_measurement_last_error_at = now
                print(
                    f"[queue-measurement] at={now.isoformat()} "
                    f"status=degraded component={component} "
                    f"errors={self._queue_measurement_error_count} "
                    f"error={type(exc).__name__}:{str(exc)[:160]}",
                    flush=True,
                )
            return None

    def _queue_measurement_sync(self, *, observed_at: datetime) -> None:
        def apply() -> None:
            self._queue_measurement.sync_orders(observed_at=observed_at)
            if not self._queue_measurement_stream_healthy:
                self._queue_measurement.mark_coverage_gap(
                    observed_at=observed_at, dropped=1,
                    reason="trade_stream_unavailable", only_clean=True)
        self._queue_measurement_safe("order_sync", apply)

    def _queue_measurement_book(
        self, book: BookSnapshot, *, observed_at: datetime,
    ) -> None:
        self._queue_measurement_safe(
            "book",
            lambda: self._queue_measurement.on_book(
                book, observed_at=observed_at))

    async def _queue_measurement_loop(self) -> None:
        """Maintain the isolated public-trade study, reconnecting fail-open."""

        stream: Any | None = None
        scope: tuple[str, ...] = ()
        failures = 0
        dropped_seen = 0
        decode_seen = 0
        stream_session_id: int | None = None
        trades_received = 0
        last_trade_at: datetime | None = None
        last_health_update_at: datetime | None = None
        first_trade_persisted = False
        try:
            while True:
                desired = tuple(self.active_slugs)
                if not desired:
                    if stream is not None:
                        if stream_session_id is not None:
                            now = self._now()
                            self._queue_measurement_safe(
                                "stream_health",
                                lambda now=now: self._queue_measurement.finish_stream_session(
                                    stream_session_id, observed_at=now,
                                    status="REBOUND", trades_received=trades_received,
                                    dropped_trades=dropped_seen,
                                    decode_failures=decode_seen,
                                    last_trade_at=last_trade_at))
                        try:
                            await stream.close()
                        except Exception:
                            pass
                        stream = None
                        scope = ()
                        stream_session_id = None
                        self._queue_measurement_stream_healthy = False
                    await asyncio.sleep(1)
                    continue
                if stream is None or desired != scope:
                    if stream is not None:
                        if stream_session_id is not None:
                            now = self._now()
                            self._queue_measurement_safe(
                                "stream_health",
                                lambda now=now: self._queue_measurement.finish_stream_session(
                                    stream_session_id, observed_at=now,
                                    status="REBOUND", trades_received=trades_received,
                                    dropped_trades=dropped_seen,
                                    decode_failures=decode_seen,
                                    last_trade_at=last_trade_at))
                        try:
                            await stream.close()
                        except Exception:
                            pass
                    scope = desired
                    dropped_seen = 0
                    decode_seen = 0
                    trades_received = 0
                    last_trade_at = None
                    first_trade_persisted = False
                    stream_session_id = None
                    self._queue_measurement_stream_healthy = False
                    try:
                        stream = self.trade_stream_factory(self.client)
                        await stream.start(list(scope))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures += 1
                        failed_at = self._now()
                        failed_session = self._queue_measurement_safe(
                            "stream_health",
                            lambda failed_at=failed_at: (
                                self._queue_measurement.start_stream_session(
                                    observed_at=failed_at, slugs=scope)))
                        if failed_session is not None:
                            self._queue_measurement_safe(
                                "stream_health",
                                lambda failed_session=failed_session,
                                failed_at=failed_at, exc=exc: (
                                    self._queue_measurement.finish_stream_session(
                                        failed_session, observed_at=failed_at,
                                        status="ERROR", trades_received=0,
                                        dropped_trades=0, decode_failures=0,
                                        last_trade_at=None,
                                        error=f"startup:{type(exc).__name__}:{exc}")))
                        self._queue_measurement_safe(
                            "trade_stream_start",
                            lambda exc=exc: (_ for _ in ()).throw(exc))
                        self._queue_measurement_safe(
                            "coverage_gap",
                            lambda: self._queue_measurement.mark_coverage_gap(
                                observed_at=self._now(), dropped=1,
                                reason="trade_stream_start_failed",
                                only_clean=True))
                        if stream is not None:
                            try:
                                await stream.close()
                            except Exception:
                                pass
                        stream = None
                        await asyncio.sleep(min(2 ** min(failures - 1, 5), 30))
                        continue
                    failures = 0
                    self._queue_measurement_stream_healthy = True
                    started_at = self._now()
                    stream_session_id = self._queue_measurement_safe(
                        "stream_health",
                        lambda started_at=started_at: (
                            self._queue_measurement.start_stream_session(
                                observed_at=started_at, slugs=scope)))
                    last_health_update_at = started_at
                    print(
                        f"[queue-measurement] at={self._now().isoformat()} "
                        f"status=collecting markets={len(scope)} "
                        f"active_orders={self._queue_measurement.active_count}",
                        flush=True,
                    )
                try:
                    trade = await stream.next_trade(timeout=1)
                except asyncio.TimeoutError:
                    trade = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._queue_measurement_safe(
                        "trade_stream",
                        lambda exc=exc: (_ for _ in ()).throw(exc))
                    self._queue_measurement_safe(
                        "coverage_gap",
                        lambda exc=exc: self._queue_measurement.mark_coverage_gap(
                            observed_at=self._now(), dropped=1,
                            reason=f"stream:{type(exc).__name__}"))
                    if stream_session_id is not None:
                        failed_at = self._now()
                        self._queue_measurement_safe(
                            "stream_health",
                            lambda failed_at=failed_at, exc=exc: (
                                self._queue_measurement.finish_stream_session(
                                    stream_session_id, observed_at=failed_at,
                                    status="ERROR", trades_received=trades_received,
                                    dropped_trades=dropped_seen,
                                    decode_failures=decode_seen,
                                    last_trade_at=last_trade_at,
                                    error=f"{type(exc).__name__}:{exc}")))
                    stream_session_id = None
                    self._queue_measurement_stream_healthy = False
                    try:
                        await stream.close()
                    except Exception:
                        pass
                    stream = None
                    failures += 1
                    await asyncio.sleep(min(2 ** min(failures - 1, 5), 30))
                    continue
                dropped = int(getattr(stream, "dropped_trades", 0))
                decoded = int(getattr(stream, "decode_failures", 0))
                gap = max(0, dropped - dropped_seen) + max(0, decoded - decode_seen)
                if gap:
                    self._queue_measurement_safe(
                        "coverage_gap",
                        lambda gap=gap: self._queue_measurement.mark_coverage_gap(
                            observed_at=self._now(), dropped=gap,
                            reason="bounded_queue_or_decode"))
                dropped_seen = dropped
                decode_seen = decoded
                if trade is not None:
                    observed_at = self._now()
                    trades_received += 1
                    last_trade_at = observed_at
                    self._queue_measurement_safe(
                        "trade",
                        lambda trade=trade, observed_at=observed_at:
                            self._queue_measurement.on_trade(
                                trade, observed_at=observed_at))
                health_now = self._now()
                if (stream_session_id is not None
                        and ((trades_received > 0 and not first_trade_persisted)
                             or last_health_update_at is None
                             or (health_now - last_health_update_at
                                 ).total_seconds() >= 300)):
                    self._queue_measurement_safe(
                        "stream_health",
                        lambda health_now=health_now: (
                            self._queue_measurement.update_stream_session(
                                stream_session_id, observed_at=health_now,
                                trades_received=trades_received,
                                dropped_trades=dropped_seen,
                                decode_failures=decode_seen,
                                last_trade_at=last_trade_at)))
                    if trades_received > 0:
                        first_trade_persisted = True
                    last_health_update_at = health_now
        finally:
            self._queue_measurement_stream_healthy = False
            if stream_session_id is not None:
                stopped_at = self._now()
                self._queue_measurement_safe(
                    "stream_health",
                    lambda stopped_at=stopped_at: (
                        self._queue_measurement.finish_stream_session(
                            stream_session_id, observed_at=stopped_at,
                            status="STOPPED", trades_received=trades_received,
                            dropped_trades=dropped_seen,
                            decode_failures=decode_seen,
                            last_trade_at=last_trade_at)))
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
            self._queue_measurement_safe(
                "flush",
                lambda: self._queue_measurement.flush(
                    observed_at=self._now()))

    async def _refresh_earnings(self, now: datetime) -> None:
        """Persist authenticated venue earnings without treating them as P&L."""

        # ONE account-wide read, not one per bound market. The per-slug loop
        # cost up to twenty calls through the same 4 req/s governor the quoting
        # loop uses, and returned strictly less: the venue answers a slug
        # filter correctly (verified 2026-08-20 -- a probe slug returned its
        # exact $2.88), but asking only about markets bound RIGHT NOW can never
        # see this bot's lifetime, because reward posts days after the market
        # it was earned in has closed.
        fetched = await self.gateway.fetch_earnings(
            program_type="liquidityProgram")
        rows_all = tuple(
            row for row in fetched
            if row.program_type == "liquidityProgram")

        # Scoped view, unchanged semantics: only markets in the authorized set
        # may be persisted or reported as THIS SESSION'S earnings. A venue
        # deployment that silently ignored a query filter must not be able to
        # import a shared-account reward as ours -- pinned by
        # `test_ignored_earnings_query_cannot_import_other_market_rewards`.
        authorized = set(self.active_slugs)
        scoped = tuple(row for row in rows_all if row.market_slug in authorized)
        self.ledger.record_reward_earnings(scoped, observed_at=now)
        totals: dict[str, Decimal] = {}
        for row in scoped:
            status = str(row.status).upper()
            totals[status] = totals.get(status, Decimal("0")) + Decimal(
                str(row.reward))
        self._earnings_by_status = totals

        # Lifetime view, for the bot's own bottom line. Account-wide by
        # necessity -- the markets it earned in are long closed -- and scoped
        # by DATE to this bot's era, because the account carries $86.51 of
        # maker-lab reward predating it. Counting that here overstates the bot
        # by about 2x, which has happened twice. See `bot_era.py`.
        self._bot_era_rewards = bot_era_rewards_by_status(rows_all)
        self._earnings_observed_at = now

    async def _retry_uncertain_cancels(
        self, now: datetime, *, markets: frozenset[str] | None = None,
    ) -> None:
        """Re-issue a cancel the venue has just confirmed did not land.

        `cancel` moves its intent to `UNCERTAIN` when the request fails, and a
        CANCEL intent resolves only on a terminal order state -- correctly,
        since an accepted HTTP response is not cancellation confirmation. But
        nothing ever re-sent the request, so a cancel that failed stayed failed
        and its intent stayed unresolved for the rest of the session.

        Measured 2026-08-18T15:16:52Z: `POST /v1/order/BYRYM21VADK0/cancel`
        returned InternalServerError, the venue answered `OPEN` for the next
        sixteen minutes, the 60s critical stop fired, and the same intent then
        made its own market's flatten unprovable -- `kill_switch.status` counts
        unresolved intents as a blocker -- so shutdown exited 3, which the
        supervisor never restarts. One 500 cost about three hours.

        Retried **only** while the order's freshly re-read state is active.
        That is positive evidence the cancel did not take effect, so re-sending
        it assumes nothing -- the exact opposite of reading absence as
        cancellation. An absent, unknown or terminal order is never retried.

        Each attempt opens a new intent, because the state machine does not
        allow `UNCERTAIN -> SUBMITTING` and reusing the id would misreport what
        happened. That is harmless: `_upsert_order` resolves every intent
        matching the exchange id in one statement, so the stuck one and its
        retries all clear together on the terminal state.
        """

        seen: set[str] = set()
        for row in self.ledger.unresolved_intents():
            if str(row["operation"]).upper() != "CANCEL":
                continue
            if str(row["state"]).upper() != "UNCERTAIN":
                continue
            exchange_id = str(row["exchange_order_id"] or "")
            if not exchange_id:
                continue
            seen.add(exchange_id)
            if (markets is not None
                    and str(row["market_slug"]) not in markets):
                continue
            if not self.ledger.order_is_active(exchange_id):
                continue
            last = self._cancel_retried_at.get(exchange_id)
            if (last is not None
                    and (now - last).total_seconds() < self.CANCEL_RETRY_SECONDS):
                continue
            self._cancel_retried_at[exchange_id] = now
            try:
                await self.executor.cancel(
                    client_order_id=str(row["client_order_id"]),
                    exchange_order_id=exchange_id,
                    market_slug=str(row["market_slug"]),
                    now=now)
                self._runtime_error_counts.pop("uncertain_cancel_retry", None)
            except Exception as exc:
                # Best-effort, like the absent sweep. A cancel the venue keeps
                # refusing is already the failure being repaired, and letting
                # it abort the reconcile would take every other market down
                # with it. The critical stop remains the backstop.
                await self._report_runtime_error("uncertain_cancel_retry", exc)
        # Forget orders that are no longer stuck, so the throttle cannot grow
        # without bound across a long session.
        self._cancel_retried_at = {
            key: at for key, at in self._cancel_retried_at.items()
            if key in seen}

    async def _rest_reconcile(
        self, now: datetime, *, tag: str = "",
        strict_order_markets: frozenset[str] | None = None,
    ) -> None:
        """Re-read the whole account from REST and apply it to the ledger.

        `tag` namespaces the snapshot event ids. Two reconciles at the same
        clock reading would otherwise build the same id from different content,
        which the ledger correctly rejects as a replay -- so a caller that may
        run alongside another at the same instant passes its own tag.

        By default, every owned live/ambiguous order is retrieved and any
        failure fails the reconcile. A recovery already scoped to particular
        markets may pass those markets in ``strict_order_markets``. Individual
        reads and absent-order repairs for other positively-attributed markets
        are deferred to their own recovery/normal sweep; complete account
        snapshots still update them, and their per-market gates remain closed
        if ambiguity survives. An empty set is the private-stream form:
        reconnect visibility account-wide, but keep every ambiguous owner
        dormant. An unattributable order is always read strictly.
        """

        unresolved_cancels = self._unresolved_cancel_owners()
        # First ask for terminal state on every bot-owned working order. A
        # missing order in an open-order snapshot is not cancellation proof.
        exchange_markets: dict[str, set[str]] = {}
        for row in (
            *self.ledger.working_orders(owned_only=True),
            *self.ledger.unresolved_intents(),
        ):
            exchange_id = str(row["exchange_order_id"] or "").strip()
            if not exchange_id:
                continue
            market = str(row["market_slug"] or "").strip()
            exchange_markets.setdefault(exchange_id, set())
            if market:
                exchange_markets[exchange_id].add(market)
        exchange_ids = set(exchange_markets)
        # Ask about every one before failing, rather than raising on the first
        # id the venue will not answer for. `sorted()` makes which id that is
        # deterministic rather than harmless: measured 2026-08-18, the first in
        # sort order was a just-placed order the venue served no state for over
        # two minutes, so the second -- which would have resolved on the first
        # try -- was never asked at all. Both intents stayed unresolved, and
        # the 60s critical stop is armed by exactly that, so the account-wide
        # terminal stop fired and the bot was down for four hours.
        #
        # Default strictness is deliberately unchanged: any failure still
        # fails the reconcile, because a working order we could not re-read is
        # live risk and nothing may proceed as if it were known. Recovery may
        # explicitly contain a positively-owned failure to that market; its
        # per-market gate remains closed. Either way, one unreadable order no
        # longer hides every other order's answer or the complete snapshots.
        reconcile_failures: list[tuple[str, str]] = []
        skipped_order_ids: set[str] = set()
        skipped_order_markets: set[str] = set()
        for exchange_id in sorted(exchange_ids):
            markets = exchange_markets.get(exchange_id, set())
            if strict_order_markets is not None and len(markets) == 1:
                market = next(iter(markets))
                if market not in strict_order_markets:
                    skipped_order_ids.add(exchange_id)
                    skipped_order_markets.add(market)
                    continue
            try:
                await self.executor.reconcile_order(
                    exchange_id, observed_at=now)
            except RuntimeInvariantViolation:
                # Terminal by contract, and never absorbed by handling that
                # exists to survive transport failures. Nothing reachable from
                # `reconcile_order` raises this today -- only this module and
                # `live_session` raise it at all -- and this is what keeps that
                # true if anything below ever starts to.
                raise
            except Exception as exc:
                reconcile_failures.append((
                    exchange_id, f"{type(exc).__name__}: {exc}"))
        # Deliberately before failure classification. The sweep above has just
        # written the venue's current word on every readable order, which is
        # the only thing that licenses re-issuing a cancel -- and an unreadable
        # order in some other market must not postpone repairing this one.
        await self._retry_uncertain_cancels(
            now, markets=strict_order_markets)
        blocking_reconcile_failures: list[tuple[str, str]] = []
        for exchange_id, detail in reconcile_failures:
            markets = exchange_markets.get(exchange_id, set())
            # More than one owner is a ledger conflict, and no owner means the
            # ambiguity cannot be contained. Both stay account-wide failures.
            if len(markets) != 1:
                blocking_reconcile_failures.append((exchange_id, detail))
                continue
            market = next(iter(markets))
            if (strict_order_markets is None
                    or market in strict_order_markets):
                blocking_reconcile_failures.append((exchange_id, detail))
        # Then the orders we have already lost sight of. These are in neither
        # set above -- `UNKNOWN_ABSENT` is not a working state and their
        # intents are long since RECONCILED -- so before this nothing ever
        # asked about them again and they stayed absent for the rest of the
        # session. One such row holds `risk_state_known(None)` false
        # account-wide, which is what wedged every stream bind on 2026-08-16.
        #
        # Best-effort, unlike the default-strict sweep above: these are already
        # lost and a persistent 404 must not take the whole reconcile -- and
        # every bind that depends on it -- down with it. Absence is still never
        # read as cancellation: only a terminal state from the venue clears the
        # row.
        absent_ids = set()
        for row in self.ledger.absent_orders():
            exchange_id = row["exchange_order_id"]
            if not exchange_id or exchange_id in exchange_ids:
                continue
            absent_ids.add(exchange_id)
            if (strict_order_markets is not None
                    and str(row["market_slug"]) not in strict_order_markets):
                continue
            # Throttled, unlike the strict sweep above. `_rest_reconcile` runs
            # on every service cycle and every bind, and `retrieve` answers a
            # 404 by retrying four times with 0.5/1/2s backoff -- so an order
            # the venue will not resolve would add up to 3.5s of sleeping to
            # EVERY reconcile, indefinitely. That ages books, and `book_stale`
            # already blocks quoting on its own. An absence is not urgent: it
            # blocks only its own market, and one attempt a minute resolves a
            # transient inconsistency just as surely as sixty do.
            asked_at = self._absent_asked_at.get(exchange_id)
            if (asked_at is not None
                    and (now - asked_at).total_seconds()
                    < self.ABSENT_RECHECK_SECONDS):
                continue
            self._absent_asked_at[exchange_id] = now
            # Checked BEFORE the re-ask, and deliberately not inside an error
            # branch. The first version of this only resolved on a NotFound
            # from `retrieve`, and measured 2026-08-17 that never happened:
            # all twenty stuck rows sat in markets the venue reported
            # RESOLVED, `retrieve` answered without raising, and the rows
            # never left `UNKNOWN_ABSENT` -- so rotation stayed blocked
            # account-wide and the bot quoted almost nothing for an evening.
            #
            # An order in a resolved market cannot be live whatever `retrieve`
            # returns, so settlement is the whole test and the re-ask is
            # irrelevant once it holds.
            if await self._market_has_settled(str(row["market_slug"])):
                if self.ledger.resolve_absent_order_in_settled_market(
                        exchange_id, observed_at=now, reason="market_resolved"):
                    await self.notifier.send_async(
                        "Liquidity Bot resolved an absent order in settled "
                        f"market {row['market_slug']}; it can no longer be "
                        "live there, and rotation is unblocked.")
                self._runtime_error_counts.pop("absent_order", None)
                continue
            try:
                # Not `reconcile_order`: its event id is the venue's own event
                # time and status, so a re-ask after the terminal state is
                # already on file is a replay and repairs nothing. See
                # `reconcile_absent_order`.
                await self.executor.reconcile_absent_order(
                    exchange_id, observed_at=now)
                self._runtime_error_counts.pop("absent_order", None)
            except Exception as exc:
                # Escalates on repeat through the usual attempt counter. A
                # wedge that looks healthy is the worst outcome there is: the
                # 2026-08-16 stall was invisible for 24 minutes because nothing
                # about this state was ever reported.
                await self._report_runtime_error("absent_order", exc)
        # Forget orders that are no longer absent, so the throttle cannot grow
        # without bound across a long session.
        self._absent_asked_at = {
            key: at for key, at in self._absent_asked_at.items()
            if key in absent_ids}
        orders = await self.account.fetch_open_orders(observed_at=now)
        positions = await self.account.fetch_positions(observed_at=now)
        # Fills, from the one source that survives a restart. Without this the
        # $40 stop reads one-sided round trips: measured 2026-08-15, a $1.55
        # loss was recorded as a $3.26 gain because only the opening fill was
        # ever written. Failing this must not fail the whole reconcile -- the
        # order and position snapshots above are what gate new risk.
        try:
            for execution in await self.account.fetch_executions(
                    observed_at=now):
                try:
                    self.ledger.apply_execution(execution, received_at=now)
                except LedgerConflict:
                    # Already recorded. The dedupe hashes the whole payload, so
                    # adding a field to Execution -- `outcome` did exactly this
                    # -- makes a replay of an existing fill look like a
                    # conflicting rewrite. It is not: the execution id is the
                    # identity and we already hold it. Aborting the sweep here
                    # left the position unprovable and the runner exited
                    # non-flat, refusing to restart. Observed 2026-08-15T21:24Z.
                    continue
            self._runtime_error_counts.pop("execution_backfill", None)
        except Exception as exc:
            await self._report_runtime_error("execution_backfill", exc)
        stamp = f"{tag}:{now.isoformat()}" if tag else now.isoformat()
        self.ledger.apply_order_snapshot(
            f"commissioning-rest-orders:{stamp}", orders,
            as_of=now, observed_at=now, complete=True)
        self.ledger.apply_position_snapshot(
            f"commissioning-rest-positions:{stamp}", positions,
            as_of=now, observed_at=now, complete_account=True)
        self._record_resolved_cancel_owners(unresolved_cancels)
        if blocking_reconcile_failures:
            raise AdapterError(
                "could not re-read "
                f"{len(blocking_reconcile_failures)} working order(s): "
                + "; ".join(
                    f"{exchange_id}: {detail}"
                    for exchange_id, detail in blocking_reconcile_failures))
        if skipped_order_ids:
            print(
                f"[rest-reconcile] at={self._now().isoformat()} "
                f"scoped_order_reads_skipped={len(skipped_order_ids)} "
                f"markets={','.join(sorted(skipped_order_markets))}",
                flush=True,
            )
        # Freshness begins when the multi-call reconciliation has completed,
        # not when its first request entered the shared REST limiter.
        self.gate.on_rest_reconciled(self._now())

    def _unresolved_cancel_owners(self) -> dict[str, str]:
        """Map unresolved CANCEL intents to their positively-known owner."""

        return {
            str(row["intent_id"]): str(row["market_slug"] or "").strip()
            for row in self.ledger.unresolved_intents()
            if str(row["operation"]).upper() == "CANCEL"
        }

    def _record_resolved_cancel_owners(
        self, unresolved_cancels: dict[str, str],
    ) -> None:
        """Prioritize owners whose CANCEL gained terminal proof."""

        if not unresolved_cancels:
            return
        still_unresolved = {
            str(row["intent_id"])
            for row in self.ledger.unresolved_intents()
        }
        self._priority_replan_slugs.update(
            slug for intent_id, slug in unresolved_cancels.items()
            if intent_id not in still_unresolved and slug in self._markets)

    async def _observe_private(self, event: Any, now: datetime) -> None:
        unresolved_cancels = self._unresolved_cancel_owners()
        received_at = getattr(event, "received_at", None) or now
        self.gate.observe(event, received_at=received_at)
        self.projector.apply(event, received_at=received_at)
        self._record_resolved_cancel_owners(unresolved_cancels)
        self._queue_measurement_sync(observed_at=received_at)

    def _take_priority_replans(self) -> tuple[str, ...]:
        """Take cancel-confirmed markets in stable active-slot order."""

        selected = tuple(
            slug for slug in self.active_slugs
            if slug in self._priority_replan_slugs)
        self._priority_replan_slugs.difference_update(selected)
        return selected

    async def _wait_for_initial_private_state(self, timeout_seconds: float = 15) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            event = await self.private_stream.next_event(timeout=remaining)
            now = self._now()
            await self._observe_private(event, now)
            # `session_ready`, not `ready`: a bind proves we can SEE the venue.
            # Asking the account-wide `ready` here meant one market's unsettled
            # bookkeeping could fail the bind for all twenty -- and a bind that
            # fails closes both streams, so the markets that were perfectly
            # fine stopped quoting too. Whether any given market may quote is
            # asked per market, every pass, in `_frame`.
            if self.gate.session_ready(self.ledger, now=now):
                return
        raise CommissioningUnavailable(
            "private stream did not become reconciled and fresh")

    def _startup_recovery_is_scoped(self, blockers: tuple[str, ...]) -> bool:
        """Allow restart only to retire this bot's identified risk.

        Scoped to the whole active set, not the primary market. Checking only
        slot zero refused recovery whenever a crash left a position on any
        other slot, which raises CommissioningRefused and exits 2 -- a code the
        supervisor never restarts. The bot would stay down for exactly the
        state it was built to recover. See invariant 58: the authorized scope
        is the active set.
        """

        allowed = {
            "shared account has open orders",
            "shared account has nonzero positions",
            "execution ledger has unresolved intents",
        }
        if not blockers or not set(blockers).issubset(allowed):
            return False
        authorized = set(self.active_slugs)
        if not authorized:
            return False
        working = self.ledger.working_orders()
        if any(str(row["client_order_id"]).startswith("venue:")
               or row["market_slug"] not in authorized for row in working):
            return False
        nonzero = self.ledger.query(
            "SELECT market_slug FROM execution_positions "
            "WHERE CAST(net_qty AS REAL)!=0")
        if any(row["market_slug"] not in authorized for row in nonzero):
            return False
        for row in nonzero:
            try:
                basis = self.ledger.inventory_basis(row["market_slug"])
            except Exception:
                return False
            if basis is None:
                # A venue position that merely happens to be in the selected
                # market is not bot-owned recovery state. Persisted fills must
                # reconstruct its exact quantity before shutdown may mutate it.
                return False
        unresolved = self.ledger.unresolved_intents()
        if any(row["market_slug"] not in authorized
               or not row["exchange_order_id"] for row in unresolved):
            return False
        return bool(working or nonzero)

    async def preflight_until_ready(self) -> str:
        """Retry startup while the only obstacle is a transient condition.

        A market that has not opened, an unpublished incentive programme, a
        late private snapshot or a Discord blip all clear on their own, but
        each used to exit 2, which the supervisor never restarts. One bad
        moment therefore ended an entire run before a single order.

        Nothing unsafe is retried: ownership, cross-market state and config
        drift raise ``CommissioningRefused`` and still exit 2 immediately. The
        wait itself carries no risk, because a runner that has not completed
        preflight holds no orders and no position, and each attempt re-proves
        the whole account from scratch.
        """

        attempt = 0
        venue_failure_streak = 0
        while True:
            attempt += 1
            try:
                mode = await self.preflight()
                self._runtime_error_counts.pop("startup_unavailable", None)
                return mode
            except AdapterError as exc:
                # Do not let maintenance consume the supervisor's three-fast-
                # crash allowance.  Only positively identified transport/5xx
                # failures enter the long-lived pause; malformed responses,
                # validation failures and ownership/state defects still leave
                # this process and surface normally.
                if not self._is_venue_unavailable(exc):
                    raise
                venue_failure_streak += 1
                await self._report_runtime_error(
                    "startup_venue_unavailable", exc, venue_failure_streak)
                await self._release_partial_startup()
                if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                    stop = await self._wait_for_venue_recovery("startup", exc)
                    if stop:
                        raise CommissioningRefused(
                            f"operator stopped during maintenance pause: {stop}"
                        ) from exc
                    venue_failure_streak = 0
                    attempt = 0
                    continue
                await self.sleep(self._recovery_delay(
                    "startup_venue_unavailable", base=5.0, cap=30.0))
            except CommissioningUnavailable as exc:
                venue_failure_streak = 0
                if attempt >= MAX_STARTUP_UNAVAILABLE_ATTEMPTS:
                    raise CommissioningRefused(
                        f"startup blocked after {attempt} attempts: {exc}"
                    ) from exc
                if self._stop_requested:
                    raise CommissioningRefused(
                        f"operator stopped during startup: {exc}") from exc
                await self._report_runtime_error(
                    "startup_unavailable", exc, attempt)
                # Discard anything the partial attempt bound so the retry
                # re-selects and re-proves rather than trusting stale state.
                await self._release_partial_startup()
                await self.sleep(self._recovery_delay(
                    "startup_unavailable", base=5.0, cap=60.0))

    async def _release_partial_startup(self) -> None:
        """Drop streams and market state left by a failed startup attempt."""

        for stream in (self.private_stream, self.market_stream):
            try:
                await stream.close()
            except Exception:
                pass
        self.gate.on_disconnect()
        self.market_stream = self.market_stream_factory(self.client)
        self.private_stream = self.private_stream_factory(self.client)
        self.market = None
        self.incentive = None
        self.book = None
        self.book_observed_at = None
        if self.config.auto_select_reward_markets:
            self._bind_markets(())

    async def preflight(self) -> str:
        now = self._now()
        self.started_at = now
        # Deliberately not gated on "this ledger ever placed an order". That
        # test is true forever once a single order has been placed, so every
        # later session became recovery-only and the bot could never trade
        # again without a fresh database. It was survivable while recovery
        # exited 0 and simply stopped; once recovery started exiting 5 to
        # restart, it became an infinite loop that the supervisor's fast-crash
        # guard killed -- observed 2026-08-14T05:07-05:09Z. Genuine leftover
        # state is already detected by `recovery_only` below, from live venue
        # orders, positions and unresolved intents.
        self.config.assert_authorized(now=now)
        self.cutoff_at = None
        # Reconcile against the venue BEFORE deciding what to recover.
        #
        # `_identified_recovery_markets` reads the ledger, and the ledger stops
        # updating the moment a runner dies -- so after a crash it describes the
        # account as it was at the crash, not as it is now. Observed
        # 2026-08-16T21:36Z and again at 21:41: the ledger still held one
        # working order and a 5-lot position in `dumvit-midmih` from the 19:01
        # crash, while the venue held neither. Preflight bound that market for
        # recovery, its game had kicked off at 19:45 and finished, so its book
        # came back empty on both sides and startup exited 2 -- twice, refusing
        # to start over a position that did not exist.
        #
        # Cheap and unconditional: one whole-account read, before any decision
        # depends on what we think we hold. Everything downstream -- the
        # recovery binding, `ControlledTrialStartup`, the flat proof -- is more
        # accurate for it, and on a clean start it finds nothing and costs one
        # round trip.
        reconciled_at_startup = False
        try:
            await self._rest_reconcile(now, tag="startup")
            reconciled_at_startup = True
            now = self._now()
        except Exception as exc:
            # Not fatal on its own. `ControlledTrialStartup` below still proves
            # the account against the venue and refuses if it cannot, so a
            # transient read here must not be the thing that stops the session.
            await self._report_runtime_error("startup_reconcile", exc)
        if self.config.auto_select_reward_markets:
            recovery_slugs = self._identified_recovery_markets()
            if recovery_slugs:
                self._bind_markets(recovery_slugs)
                # Recovery exists to get out, never to quote. Marking these
                # retired up front lets the pregame and incentive gates be
                # skipped when loading them (see _refresh_contracts for_exit)
                # with no risk of building on a market that has started:
                # `_recovery_slugs` is barred from quoting for the whole
                # session -- the one bar that did NOT become a cooldown --
                # while the reconciler keeps planning its exit.
                for slug in recovery_slugs:
                    self._retired_ever.add(slug)
                    self._recovery_slugs.add(slug)
            else:
                await self._select_reward_market(now)
                now = self._now()
        # Every bound market is best effort, including slot zero. Requiring all
        # of them would let one unlucky market block startup entirely, and
        # startup failure is the most expensive kind -- nothing quotes at all.
        #
        # Slot zero used to be exempt from that reasoning and refreshed
        # unguarded, which made it the single point of failure the rest of this
        # loop exists to avoid. Observed 2026-08-16T21:36Z: the primary
        # published a book with no levels on either side, `parse_book_response`
        # raised `book is empty on both sides`, and the whole startup exited 2
        # -- a code the supervisor never restarts -- over one dud market out of
        # twenty. About one market in four publishes a one-sided book (see
        # adapters.py), so drawing a bad slot zero is not rare.
        #
        # A market we are RECOVERING is different and still refuses: it holds a
        # position we were restarted to retire, and dropping it would abandon
        # live risk.
        # `_recovery_slugs`, not `_retired_ever`. They are equal here -- at
        # startup nothing else has retired yet -- but `_retired_ever` is a
        # reporting set that now outlives the bar it used to imply, and a
        # control decision must not read from it.
        await self._refresh_bound_books(now)
        # A singleton private subscription makes the documented position
        # update (which omits marketSlug) attributable without guessing. Whole-
        # account REST reconciliation remains the authoritative safety check.
        await self.private_stream.start(list(self.active_slugs))
        self.gate.on_connect()
        await self.market_stream.start(list(self.active_slugs))
        # Absent orders belong here for the same reason they belong in the
        # `_rest_reconcile` sweep: they are in neither of the other two sets,
        # so without this the reconcile that could clear them never runs at
        # startup. `ControlledTrialStartup` then reads `risk_state_known()`
        # account-wide, finds the absence, and blocks on "exchange order
        # ownership or state is unknown" -- which `_startup_recovery_is_scoped`
        # does not allow, so preflight raises CommissioningRefused and exits 2,
        # a code the supervisor never restarts. One order we lost sight of
        # would have kept the bot from ever starting again.
        # Skipped when the startup reconcile above already ran: it is the same
        # whole-account read, and repeating it at the same clock reading reuses
        # the snapshot event id, which the ledger correctly rejects as a replay
        # with different content. This runs only when that one failed.
        if (not reconciled_at_startup
                and (self.ledger.working_orders(owned_only=True)
                     or self.ledger.unresolved_intents()
                     or self.ledger.absent_orders())):
            await self._rest_reconcile(now)
        try:
            await self._refresh_earnings(now)
        except Exception as exc:
            # Earnings are reporting, not a safety input: they are delayed by
            # days, never authorize an entry or an exit, and nothing below
            # reads them. The run loop already treats a failure here as
            # recoverable; preflight did not, so a venue-side blip refused
            # startup outright. Observed 2026-08-17T03:05Z --
            # `GET /v1/incentives/earnings` returned InternalServerError and
            # the process exited 2, a code the supervisor never restarts, with
            # a flat account and nothing wrong with it.
            self._earnings_observed_at = None
            await self._report_runtime_error("reward_earnings_refresh", exc)
        accepted = ReadinessVerdict(
            "COMMISSIONING_REWARD_ECONOMICS_ENFORCED", True, False, (),
            ("scale remains blocked until attributable paid rewards calibrate",))
        report = await ControlledTrialStartup(
            self.account, self.ledger).reconcile(accepted, observed_at=now)
        recovery_only = (
            not report.ready
            and self._startup_recovery_is_scoped(report.blockers))
        if not report.ready and not recovery_only:
            raise CommissioningRefused("; ".join(report.blockers))
        # The startup proof can span several paginated REST calls. Start its
        # freshness window when that proof completed, just like periodic
        # reconciliation, or a slow preflight can arrive at the loop stale.
        self.gate.on_rest_reconciled(self._now())
        self.balance = await self.account.fetch_balance(observed_at=now)
        self.risk_guard = SessionRiskGuard(
            started_at=now,
            starting_equity=self.balance.marked_equity,
            max_session_loss=self.config.max_session_loss_usd,
            max_market_loss=self.config.max_market_loss_usd,
            max_duration_seconds=float("inf"),
            max_equity_age_seconds=15,
            hard_stop_loss=self.config.hard_stop_loss_usd,
        )
        self.risk_guard.observe_equity(
            self.balance.marked_equity, observed_at=now)
        # Before any order can be placed. `SessionRiskGuard` stamped
        # `starting_equity` from this instant, which measures the process; this
        # re-points it at the ET day's opening equity from the ledger, so a
        # restart inherits the day's drawdown instead of resetting it.
        self._roll_realized_baseline()
        self.driver = ControlledActionDriver(
            self.executor, self.ledger, self.risk_guard)
        self._orders_before = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        if recovery_only:
            return "RECOVERY_ONLY"
        await self._wait_for_initial_private_state()
        selection_note = ""
        if self._market_selection is not None:
            selection_note = (
                f" Auto-selected {self.market_slug} from "
                f"{self._market_selection.rewarded_executable_markets} rewarded "
                f"executable markets joined from "
                f"{self._market_selection.catalog_markets} catalog rows after evaluating "
                f"{self._market_selection.books_evaluated} books; "
                f"{self._market_selection.unsupported_programs} programs used "
                "an unsupported or undocumented lifecycle and remained read-only.")
        # Built from config, never from literals. This announced "one market,
        # one contract per quote, $5 soft stop, $15 hard stop" long after every
        # one of those had changed -- four wrong facts as the operator's first
        # sight of each run. Nothing tied the message to the numbers, which is
        # the actual defect; the test does that now.
        gate = ("enforced" if self.config.enforce_reward_cost_gate
                else "advisory")
        delivery = await self.notifier.send_async(
            f"🟠 Liquidity Bot LIVE commissioning started: up to "
            f"{self.config.max_markets} markets, {self.config.quote_size} "
            f"contracts per quote, reward-versus-exit economics {gate}, "
            f"${self.config.max_session_loss_usd} session stop, "
            f"${self.config.max_market_loss_usd} per-market stop, "
            f"${self.config.hard_stop_loss_usd} hard stop, "
            "operator-controlled stop; pregame and loss gates remain active."
            + selection_note)
        if not getattr(delivery, "sent", False):
            raise CommissioningRefused(
                "Discord startup alert delivery failed before order entry")
        return "TRADE"

    def _accrue_score(self, now: datetime) -> None:
        """Accrue resting score across every bound market.

        Score is a time integral over orders actually resting, so with several
        markets the session total is the sum of their rates. Per-market totals
        are kept alongside it: a session number alone cannot tell you which
        market earned the exposure the phase is judged on.
        """

        if self._score_observed_at is not None:
            seconds = (now - self._score_observed_at).total_seconds()
            if seconds < 0:
                raise RuntimeError("commissioning score clock moved backwards")
            for slug, state in self._markets.items():
                earned = seconds * sum(state.score_by_side.values())
                if earned:
                    self._score_seconds += earned
                    self._score_seconds_by_market[slug] = (
                        self._score_seconds_by_market.get(slug, 0.0) + earned)
        self._score_observed_at = now

    @staticmethod
    def _furthest_resting_price(
        orders: tuple[WorkingOrder, ...], side: str,
    ) -> float | None:
        """Return the owned price on ``side`` that is furthest from the touch.

        The reconciler keeps at most one order per side, so more than one can
        exist only transiently during a cancel/replace race. Scoring the
        furthest price keeps recorded accrual at or below what the venue will
        credit rather than above it.
        """

        prices = [order.price for order in orders if order.side == side]
        if not prices:
            return None
        return min(prices) if side == "bid" else max(prices)

    def _resting_score(
        self,
        *,
        state: MarketState,
        side: str,
        period: IncentivePeriod,
        touch_price: float,
        touch_score: float,
        resting_price: float | None,
    ) -> float:
        """Score the order actually resting, not a candidate at the touch.

        Reward score decays as ``discount_factor ** ticks_from_best``, so a
        quote that drifted one tick behind the touch earns a fraction of the
        touch score. Attributing the touch score to it would overstate
        ``score_seconds``, which is the accrual metric the session is judged
        on. An order that has drifted outside the reward cutoff earns nothing.
        """

        if (resting_price is None or state.book is None
                or state.market is None or state.market.tick_size is None):
            return touch_score
        if abs(resting_price - touch_price) < 1e-9:
            return touch_score
        drifted = evaluate_order_score(
            side=side, price=resting_price,
            size=float(self.config.quote_size),
            levels=state.book.levels(side), incentive=period,
            tick_size=state.market.tick_size,
            order_already_resting=True)
        return drifted.score if drifted.eligible else 0.0

    def _evaluate_rewards(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        """Evaluate this market's economics, then record any CHANGE in them.

        The evaluation itself is `_evaluate_rewards_inner`; this wrapper exists
        only to emit `[eligibility]`. Three separate investigations in one
        session (2026-08-22/23) failed on the same missing fact -- a bound
        market resting no order leaves no trace of why, because the runner logs
        what it DID and a side dropped for ineligibility produces no mutation
        and therefore no `[action]` line. `price_too_extreme` appears zero
        times in a 50MB log for exactly this reason.

        Transitions rather than levels, so the volume is bounded: markets are
        serviced roughly once a second across twenty slots, and logging a level
        would add ~40 lines/second to a file that is already 12MB a session.
        A transition also carries the quantity a level cannot -- `held`, how
        long the previous state lasted, which is the oscillation period that
        decides between the three candidate fixes recorded in FINDINGS for the
        `reward_ineligible` churn.
        """

        state = self.state_for(slug or self.market_slug)
        self._evaluate_rewards_inner(now, slug)
        self._record_eligibility(state, self._now())

    def _record_eligibility(self, state: MarketState, now: datetime) -> None:
        """Log a side whose reason-to-quote changed, with how long it held.

        Never raises. This is reporting, and `_evaluate_rewards` is reached
        from the shutdown path where an exception reads as a failure to prove
        flat -- which exits 3, which the supervisor never restarts. Same
        argument `_idle_market_reasons` and `_awaiting_rotation` already carry.
        """

        try:
            for side in ("bid", "offer"):
                label = (
                    "eligible" if side in state.reward_eligible
                    else state.economic_blocker_by_side.get(
                        side, "not_evaluated"))
                key = (state.slug, side)
                previous = self._eligibility_state.get(key)
                if previous is not None and previous[0] == label:
                    continue
                self._eligibility_state[key] = (label, now)
                if previous is None:
                    # First observation of this side carries no duration, and
                    # a bare "from=none" line per market per bind is noise.
                    continue
                resting = any(
                    order.side == side
                    for order in self._working_orders(state.slug))
                print(
                    f"[eligibility] at={now.isoformat()} "
                    f"market={state.slug} side={side} "
                    f"from={previous[0]} to={label} "
                    f"held={(now - previous[1]).total_seconds():.1f}s "
                    f"resting={'yes' if resting else 'no'}",
                    flush=True,
                )
        except Exception:
            return

    def _evaluate_rewards_inner(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        self._accrue_score(now)
        state = self.state_for(slug or self.market_slug)
        state.clear_economics()
        if self._is_barred_from_quoting(state.slug):
            # A retired market is never re-authorized. Retirement clears
            # economics once; without this the very next evaluation would
            # recompute them from a healthy-looking book and quote it again.
            # The reconciler still plans its exit, which needs no economics.
            state.economic_blocker_by_side = {
                "bid": "market_retired", "offer": "market_retired"}
            return
        if state.market is None or state.book is None or state.incentive is None:
            # Named rather than bare: these three are the difference between
            # "we looked and declined" and "we never got the data", and they
            # were indistinguishable in the log. Reporting only.
            missing = ("no_market_metadata" if state.market is None
                       else "no_book" if state.book is None
                       else "no_incentive")
            state.economic_blocker_by_side = {"bid": missing, "offer": missing}
            return
        if not state.market.is_supported_moneyline:
            state.economic_blocker_by_side = {
                "bid": "unsupported_market_structure",
                "offer": "unsupported_market_structure",
            }
            return
        try:
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now)
        except AmbiguousIncentiveError:
            state.economic_blocker_by_side = {
                "bid": "ambiguous_incentive", "offer": "ambiguous_incentive"}
            return
        if period is None or period.period == "live" or state.market.tick_size is None:
            # Invariant 39: only `day_of` has a provable clock. Which of the
            # three it is matters -- "no applicable period" is a slate that has
            # not opened yet, "live" is a market we may never quote, and a
            # missing tick is bad metadata. Reporting only.
            why = ("no_applicable_period" if period is None
                   else "period_is_live" if period.period == "live"
                   else "no_tick_size")
            state.economic_blocker_by_side = {"bid": why, "offer": why}
            return
        if not state.book.is_two_sided:
            # `best_bid`/`best_offer` are `max`/`min` over a list, so an empty
            # side raises ValueError rather than returning None -- and this ran
            # outside any book guard. A single one-sided book took the whole
            # runner down with `runner_failure:ValueError` after two hours of
            # healthy quoting, and the supervisor then refused to restart
            # because shutdown could not prove flat. Observed 2026-08-16T19:01Z.
            #
            # Quoting needs BOTH touches -- the two-cent rule is defined
            # between them -- so there is genuinely nothing to evaluate here.
            # `Reconciler.plan` already returns no actions for exactly this
            # case; this is the same rule, applied one layer earlier. Exits are
            # unaffected: they are planned by the reconciler, which needs only
            # the side being sold into and no economics at all.
            state.economic_blocker_by_side = {
                "bid": "book_one_sided", "offer": "book_one_sided"}
            return
        eligible: set[str] = set()
        working_orders = self._working_orders(state.slug)
        working_sides = {order.side for order in working_orders}
        resting_price_by_side = {
            side: self._furthest_resting_price(working_orders, side)
            for side in ("bid", "offer")}
        spread = state.book.best_offer.price - state.book.best_bid.price
        for side in ("bid", "offer"):
            # Score the price we will actually rest at. A tight book is priced
            # back to the two-cent gap rather than refused, so pricing the raw
            # touch here would project a score for an order the planner is
            # never going to place.
            price = entry_quote_price(
                state.book, side,
                float(self.config.minimum_entry_spread),
                state.market.tick_size)
            if price is None:
                state.economic_blocker_by_side[side] = "book_sides_overlap"
                continue
            already_resting = side in working_sides
            result = evaluate_order_score(
                side=side, price=price, size=float(self.config.quote_size),
                levels=state.book.levels(side), incentive=period,
                tick_size=state.market.tick_size,
                order_already_resting=already_resting)
            state.reward_score_by_side[side] = result
            if result.eligible:
                eligible.add(side)
            else:
                # Record why. This drop is what removes the side from
                # reward_eligible, and it used to write nothing at all -- so a
                # burst of cancels across every market logged `blockers={}`
                # with both nets present and could not be attributed for two
                # days. A later projection failure overwrites this with its own
                # more specific reason; nothing here changes behaviour.
                state.economic_blocker_by_side[side] = (
                    result.reason or "score_ineligible")
            projection = project_order_reward(
                side=side,
                price=price,
                size=float(self.config.quote_size),
                levels=state.book.levels(side),
                incentive=period,
                tick_size=state.market.tick_size,
                event_start=state.market.game_start,
                now=now,
                horizon_seconds=self.config.reward_projection_horizon_seconds,
                haircut=float(self.config.reward_projection_haircut),
                # The venue's $1 threshold is aggregate settlement policy, not
                # a per-market or per-order eligibility threshold.
                minimum_payout=0.0,
                order_already_resting=already_resting,
            )
            state.reward_projection_by_side[side] = projection
            queue_ahead = (
                state.book.best_bid.size
                if side == "bid"
                else state.book.best_offer.size)
            # Preserve the exact approximation fed to the current model.  A
            # backed-off quote can have several better levels ahead of it, so
            # the durable observation also records actual cumulative queue
            # separately.  Measurement only: do not silently recalibrate the
            # live model while collecting its replacement data.
            state.model_queue_ahead_by_side[side] = queue_ahead
            seconds_to_event = max(
                0.0, (state.market.game_start - now).total_seconds())
            fill_hazard = self.fill_hazard_model.estimate(
                slug=state.market.slug,
                side=side,
                price=price,
                spread=spread,
                queue_ahead=queue_ahead,
                seconds_to_event=seconds_to_event,
                horizon_seconds=(
                    self.config.reward_projection_horizon_seconds),
            )
            state.fill_probability_by_side[side] = fill_hazard.probability
            state.fill_risk_driver_by_side[side] = fill_hazard.driver
            if not projection.eligible:
                state.economic_blocker_by_side[side] = projection.reason
                continue
            exit_result = cross_exit(
                state.book,
                entry_side=side,
                entry_price=price,
                qty=float(self.config.quote_size),
                depth_haircut=.5,
                # If this quote fills, liquidity ahead at its entry level was
                # consumed. Never count that same level as guaranteed exit
                # depth, even while our order is already visible in the book.
                exclude_entry_level=True,
            )
            state.stressed_exit_by_side[side] = exit_result
            if not exit_result.complete:
                state.economic_blocker_by_side[side] = (
                    "stressed_exit_incomplete")
                continue
            unwind_cost = max(0.0, -exit_result.pnl)
            economics = OpportunityEconomics(
                # Lower-bound score accrual by assigning the full horizon's
                # reward only to the no-fill branch.
                conservative_reward=(
                    projection.projected_reward
                    * (1.0 - fill_hazard.probability)),
                maker_rebate=0.0,
                # Pair-completion profit remains zero until live evidence can
                # estimate it without optimism.
                spread_capture=0.0,
                fill_probability=fill_hazard.probability,
                unwind_loss_if_filled=unwind_cost,
                operational_buffer=(
                    fill_hazard.probability * unwind_cost
                    * (float(self.config.reward_cost_safety_multiple) - 1.0)),
            )
            state.opportunity_economics_by_side[side] = economics
            if economics.should_quote or not self.config.enforce_reward_cost_gate:
                # The reconciler only quotes a side whose expected net is
                # strictly positive. With the gate advisory this value is
                # frequently negative, so a market would be selected and then
                # refuse to quote -- and because the sign flickers between
                # passes, a resting quote got cancelled seconds after being
                # placed. Measured: zero orders resting at the venue across
                # repeated samples, while 27 markets were "quoted" in 15
                # minutes. Reward is paid for resting, so that earned nothing.
                #
                # Floored the same way the selector already floors it. The
                # true value stays in reward_projection_by_side for the census
                # and the summary, so nothing measurable is lost.
                expected = economics.expected_net_value
                if not economics.should_quote:
                    expected = max(expected, 1e-9)
                state.expected_net_by_side[side] = expected
                # Live score-seconds represent actual attributable resting
                # exposure, not a hypothetical candidate. A newly authorized
                # side begins accruing only after its owned order is observed
                # working; a newly blocked side stops at this evaluation.
                if already_resting:
                    state.score_by_side[side] = self._resting_score(
                        state=state,
                        side=side,
                        period=period,
                        touch_price=price,
                        touch_score=result.score,
                        resting_price=resting_price_by_side[side])
            else:
                state.economic_blocker_by_side[side] = (
                    "projected_reward_does_not_cover_probability_"
                    "weighted_exit_cost")
                # Record the refusal as a value, not as an absent key.
                #
                # The reconciler treats a MISSING entry as "not evaluated this
                # pass" and leaves the eligibility dwell to decide, so absence
                # must not also mean "refused" -- that would let an enforced
                # refusal place the order it forbade. `should_quote` is
                # `expected_net_value > 0`, so this is at or below zero and the
                # reconciler drops the side naming `expected_net_not_positive`.
                state.expected_net_by_side[side] = min(
                    economics.expected_net_value, 0.0)
        if not self.config.enforce_reward_cost_gate:
            # Keep the measurement, drop the veto: the blocker above is still
            # computed and logged so the gate can be re-armed from evidence.
            state.economic_blocker_by_side = {
                side: reason
                for side, reason in state.economic_blocker_by_side.items()
                if not reason.startswith("projected_reward_does_not_cover")}
        state.reward_eligible = frozenset(eligible)

    def _portfolio_worst_loss(self, *, excluding: str) -> float:
        """Worst-case open exposure across every market except one.

        Hardcoded to 0 until 2026-08-14, which silently disabled the portfolio
        half of invariant 3: each market checked its own candidate against the
        whole portfolio budget and nothing ever accumulated. Harmless at one
        contract, where eight markets totalled $8. At forty it is the
        difference between a bounded book and $320 of exposure behind a $40
        stop that can only react after the loss.

        Worst case per market mirrors the candidate calculation in the
        reconciler: a long loses its full entry price if the market settles to
        zero, a short loses the complement, and a resting order is valued as
        though it fills.
        """

        total = 0.0
        for slug in self._shutdown_slugs():
            if slug == excluding:
                continue
            position = self.ledger.position(slug)
            if position:
                basis = self.ledger.inventory_basis(slug)
                price = (float(basis.average_entry_price)
                         if basis is not None else 1.0)
                total += (float(abs(position))
                          * (price if position > 0 else 1.0 - price))
            for order in self._working_orders(slug):
                total += float(order.remaining_qty) * (
                    order.price if order.side == "bid" else 1.0 - order.price)
        return total

    def _working_orders(
        self, slug: str | None = None,
    ) -> tuple[WorkingOrder, ...]:
        result: list[WorkingOrder] = []
        side_by_intent = {
            "ORDER_INTENT_BUY_LONG": "bid",
            "ORDER_INTENT_SELL_SHORT": "bid",
            "ORDER_INTENT_BUY_SHORT": "offer",
            "ORDER_INTENT_SELL_LONG": "offer",
        }
        for row in self.ledger.working_orders(slug or self.market_slug):
            intent_row = self.ledger.query(
                "SELECT payload_json FROM execution_intents "
                "WHERE operation='PLACE' AND client_order_id=? "
                "ORDER BY created_at DESC LIMIT 1", (row["client_order_id"],))
            if not intent_row:
                raise RuntimeInvariantViolation(
                    "working_order_without_owned_intent",
                    "working order has no owned place intent")
            payload = json.loads(intent_row[0]["payload_json"])
            strategy_side = side_by_intent.get(str(payload.get("intent") or ""))
            if strategy_side is None:
                raise RuntimeInvariantViolation(
                    "working_order_intent_unmapped",
                    "working order intent cannot be mapped")
            remaining = Decimal(row["original_qty"]) - Decimal(row["filled_qty"])
            if remaining > 0:
                result.append(WorkingOrder(
                    row["exchange_order_id"], strategy_side,
                    float(Decimal(row["price"])), remaining))
        return tuple(result)

    def _assert_runtime_scope(self) -> None:
        """Reject ownership or market identity drift before any mutation.

        The authorized scope is the whole active set, not one slug, so several
        markets may legitimately hold risk at once. Two things are unchanged
        and must stay that way: a ``venue:`` prefixed row means the account has
        an order this bot never placed, and a market outside the active set
        means risk exists somewhere the session did not authorize. Both remain
        terminal. Widening this to "any market" would stop the bot noticing
        that something else is trading the shared account.
        """

        authorized = set(self.active_slugs)
        working = self.ledger.working_orders()
        if any(str(row["client_order_id"]).startswith("venue:")
               for row in working):
            raise RuntimeInvariantViolation(
                "unowned_working_order",
                "runtime account contains an unowned working order")
        outside = sorted({
            str(row["market_slug"]) for row in working
            if str(row["market_slug"]) not in authorized})
        if outside:
            raise RuntimeInvariantViolation(
                "cross_market_working_order",
                "runtime account contains a working order outside the "
                f"authorized set: {', '.join(outside)}")
        cross_positions = sorted({
            str(row["market_slug"]) for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
            if str(row["market_slug"]) not in authorized})
        if cross_positions:
            raise RuntimeInvariantViolation(
                "cross_market_position",
                "runtime account contains a position outside the authorized "
                f"set: {', '.join(cross_positions)}")
        stray_intents = sorted({
            str(row["market_slug"]) for row in self.ledger.unresolved_intents()
            if str(row["market_slug"]) not in authorized})
        if stray_intents:
            raise RuntimeInvariantViolation(
                "cross_market_unresolved_intent",
                "execution ledger contains an unresolved intent outside the "
                f"authorized set: {', '.join(stray_intents)}")

    def _frame(
        self, now: datetime, slug: str | None = None,
    ) -> MarketFrame:
        self._assert_runtime_scope()
        state = self.state_for(slug or self.market_slug)
        if state.market is None or state.book is None or state.book_observed_at is None:
            raise RuntimeError("market frame is incomplete")
        position = self.ledger.position(state.market.slug)
        basis = self.ledger.inventory_basis(state.market.slug)
        if position != 0 and basis is None:
            return MarketFrame(
                state.market, now, state.book, state.book_observed_at, position,
                self._working_orders(state.slug), False, state.reward_eligible, {}, 0)
        eligible = state.reward_eligible
        if (position == 0 and self.risk_guard is not None
                and not self.risk_guard.can_add_new_risk(now=now)):
            # A stale/unknown marked balance is recoverable, but existing flat-
            # state quotes would still add risk if filled. Make them undesired
            # so the reconciler cancels them while the process stays alive.
            eligible = frozenset()
        return MarketFrame(
            market=state.market,
            now=now,
            book=state.book,
            book_observed_at=state.book_observed_at,
            position=position,
            working_orders=self._working_orders(state.slug),
            # Scoped to this market. The gate stays account-wide because it
            # measures whether our whole view is fresh and reconciled; only
            # the intent test is per market. See FINDINGS 6g.
            state_known=self.gate.ready(
                self.ledger, now=now, market_slug=state.slug),
            reward_eligible=eligible,
            expected_net_by_side=state.expected_net_by_side,
            existing_portfolio_worst_loss=self._portfolio_worst_loss(
                excluding=state.slug),
            position_opened_at=basis.opened_at if basis else None,
            entry_price=float(basis.average_entry_price) if basis else None,
        )

    def _quote_observation_for_action(
        self,
        state: MarketState,
        action: PlanAction,
        now: datetime,
        position: Decimal,
    ) -> QuoteObservation | None:
        """Describe a building PLACE without influencing whether it is sent.

        A failure here is reported and the order follows the unchanged action
        path.  Coverage is independently measurable because the durable PLACE
        intent is marked ``quote_observation_required`` even when this returns
        ``None``.
        """

        if (action.kind != ActionKind.PLACE_POST_ONLY
                or action.side not in {"bid", "offer"}
                or action.price is None or action.qty is None
                or position != 0):
            return None
        try:
            if (state.market is None or state.book is None
                    or state.book_observed_at is None
                    or state.incentive is None):
                raise RuntimeError("market observation state is incomplete")
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now)
            if period is None:
                raise RuntimeError("applicable reward period is unavailable")
            side = action.side
            score = state.reward_score_by_side[side]
            projection = state.reward_projection_by_side[side]
            hazard = state.fill_probability_by_side[side]
            driver = state.fill_risk_driver_by_side[side]
            exit_result = state.stressed_exit_by_side[side]
            economics = state.opportunity_economics_by_side[side]
            model_queue = state.model_queue_ahead_by_side[side]

            quote_price = float(action.price)
            better = at_quote = 0.0
            for level_price, level_size in state.book.levels(side):
                if (side == "bid" and level_price > quote_price + 1e-9
                        or side == "offer" and level_price < quote_price - 1e-9):
                    better += level_size
                elif abs(level_price - quote_price) <= 1e-9:
                    at_quote += level_size
            observed_queue = better + at_quote
            seconds_to_event = (
                max(0.0, (state.market.game_start - now).total_seconds())
                if state.market.game_start is not None else None)
            return QuoteObservation(
                market_slug=state.slug,
                side=side,
                observed_at=now,
                book_observed_at=state.book_observed_at,
                game_start=state.market.game_start,
                quote_price=Decimal(str(action.price)),
                quantity=action.qty,
                decision_reason=action.reason,
                best_bid=state.book.best_bid.price,
                best_bid_size=state.book.best_bid.size,
                best_offer=state.book.best_offer.price,
                best_offer_size=state.book.best_offer.size,
                spread=state.book.best_offer.price - state.book.best_bid.price,
                bid_depth=state.book.bid_depth,
                offer_depth=state.book.offer_depth,
                model_queue_ahead=model_queue,
                observed_queue_ahead=observed_queue,
                depth_better_than_quote=better,
                depth_at_quote=at_quote,
                depth_through_quote=observed_queue,
                touch_changes_30s=state.touch_changes(now, seconds=30),
                touch_changes_120s=state.touch_changes(now, seconds=120),
                seconds_since_touch_change=(
                    state.seconds_since_touch_change(now)),
                seconds_to_event=seconds_to_event,
                program_id=period.program_id,
                reward_period=period.period,
                reward_pool=period.reward_pool,
                discount_factor=period.discount_factor,
                target_size=period.target_size,
                cutoff_price=score.cutoff_price,
                depth_to_reward_cutoff=score.total_depth,
                ticks_from_best=score.ticks_from_best,
                order_score=score.score,
                total_score=projection.total_score,
                snapshot_share=projection.snapshot_share,
                projected_reward=projection.projected_reward,
                maximum_remaining_reward=(
                    projection.maximum_remaining_reward),
                fill_probability=hazard,
                fill_risk_driver=driver,
                stressed_exit_complete=exit_result.complete,
                stressed_exit_closed_qty=exit_result.closed_qty,
                stressed_exit_average_price=exit_result.average_price,
                stressed_exit_worst_price=exit_result.worst_price,
                stressed_exit_fee=exit_result.taker_fee,
                stressed_exit_pnl=exit_result.pnl,
                expected_net=economics.expected_net_value,
                reward_cost_safety_multiple=float(
                    self.config.reward_cost_safety_multiple),
            )
        except Exception as exc:
            # Observation is reporting.  It must never turn a valid action
            # into a strategy refusal or make shutdown unable to prove flat.
            print(
                f"[quote-observation] at={now.isoformat()} "
                f"market={state.slug} side={action.side} "
                f"missing={type(exc).__name__}:{exc}",
                flush=True,
            )
            return None

    async def _refresh_balance(self, now: datetime) -> None:
        if self.risk_guard is None:
            raise RuntimeError("risk guard is not initialized")
        self.balance = await self.account.fetch_balance(observed_at=now)
        # Freshness begins when the response is received, not when the request
        # entered the shared 4 req/s governor.
        observed_at = self._now()
        self.risk_guard.observe_equity(
            self.balance.marked_equity, observed_at=observed_at)
        # Realised P&L from the fill ledger, per market and in total. This
        # used to attribute the whole account's marked-equity change to the
        # primary market, which was wrong twice over: marked equity is not
        # realised, and one market is not all of them.
        self._roll_realized_baseline()
        self.risk_guard.observe_realized(self._session_realized())
        self.risk_guard.observe_market_realized({
            slug: value - self._realized_baseline_by_market.get(slug, Decimal("0"))
            for slug, value in self.ledger.realized_by_market().items()})

    async def _refresh_equity_for_decision(self, now: datetime) -> None:
        """Keep the account-risk observation fresh across a long slot sweep."""

        guard = self.risk_guard
        if guard is None:
            raise RuntimeError("risk guard is not initialized")
        observed_at = guard.equity_observed_at
        if observed_at is None:
            await self._refresh_balance(now)
            return
        age = (now - observed_at).total_seconds()
        refresh_at = max(
            0.0,
            guard.max_equity_age_seconds
            - DECISION_EQUITY_REFRESH_MARGIN_SECONDS,
        )
        if age < 0 or age >= refresh_at:
            await self._refresh_balance(now)

    async def _refresh_execution_state_for_decision(
        self, now: datetime,
    ) -> None:
        """Reconcile before the private/session gate expires mid-sweep."""

        observations = tuple(
            at for at in (
                self.gate.last_message_at,
                self.gate.last_reconcile_at,
            ) if at is not None)
        if not observations:
            await self._rest_reconcile(
                now, strict_order_markets=frozenset())
            return
        age = (now - max(observations)).total_seconds()
        refresh_at = max(
            0.0,
            self.gate.max_silence_seconds
            - DECISION_STATE_REFRESH_MARGIN_SECONDS,
        )
        if age < 0 or age >= refresh_at:
            await self._rest_reconcile(
                now, strict_order_markets=frozenset())

    def _session_realized(self) -> Decimal:
        """Session P&L from settled cash, falling back to the fill ledger.

            realised = (cash now - cash at session start) + position x basis

        Open inventory has already spent cash, so it is added back at its own
        basis -- the same shape realized_pnl uses, but with cash taken from the
        venue instead of a total rebuilt from fills.

        Falls back to the ledger whenever cash or a basis is unavailable. A
        stop that raises is worse than one computed the old way.
        """

        ledger_value = self.ledger.realized_pnl() - self._realized_baseline
        if self.balance is None or self._cash_baseline is None:
            return ledger_value
        try:
            realised = self.balance.current_balance - self._cash_baseline
            for slug in self._shutdown_slugs() or self.active_slugs:
                position = self.ledger.position(slug)
                if not position:
                    continue
                basis = self.ledger.inventory_basis(slug)
                if basis is None:
                    # Cannot value what we hold, so cash alone would read the
                    # entry cost as a loss. Use the old path.
                    return ledger_value
                realised += position * basis.average_entry_price
            return realised
        except Exception:
            return ledger_value

    def _bot_overall_pnl(self) -> Decimal:
        """This bot's own bottom line, and only this bot's.

        Two terms, each scoped deliberately:

        * ledger trading -- bot-scoped by construction, because the ledger
          holds only this bot's fills and no date filter is needed.
        * PAID reward in this bot's era. **PAID only**: invariant 22 allows
          pending as context and forbids it in accounted net, because an
          unsettled reward is not income. And era-scoped, because the account
          carries $86.51 of maker-lab reward from before this bot's first
          order -- counting it here roughly doubles the apparent result.

        Understates losses, and says so where it is displayed: a settled
        position has its entry recorded and its exit invisible, $67.38 across
        29 markets as of 2026-08-19.
        """

        return (self.ledger.realized_pnl()
                + self._bot_era_rewards.get("PAID", Decimal("0")))

    def _roll_realized_baseline(self) -> None:
        """Adopt the ET risk day's opening marks from the durable ledger.

        Both stops used to baseline per PROCESS, which is what "a restart
        forgives the day's drawdown" means. Ten sessions ran on 2026-08-18, so
        the $40 budget was handed back ten times; the $45 hard stop was worse,
        because re-stamping `starting_equity` at a lower equity ratchets the
        floor down and every step stays inside the limit while the account
        walks away.

        The marks now live in the ledger, which survives a restart. The first
        process of an ET day writes them and every later one reads them back,
        so a mid-day restart inherits the day's loss. A new ET date has no row
        and gets a genuinely fresh budget, which is the only reset intended.
        """

        now = self._now()
        today = date.fromisoformat(risk_day(now))
        if self._baseline_day == today:
            return
        if self.balance is None:
            # Never write a baseline we cannot measure. `INSERT OR IGNORE`
            # makes the first write of a risk date permanent, so a zero stored
            # here could not be corrected for the rest of that day -- and a
            # zero opening equity makes `starting_equity - current_equity`
            # negative, which disables the emergency stop silently until
            # midnight. Both call sites fetch the balance immediately before
            # this, so skipping simply defers to the next pass.
            return
        equity = self.balance.marked_equity
        realized, cash = self.ledger.realized_pnl(), self.balance.current_balance
        opening_equity, opening_realized, opening_cash = (
            self.ledger.day_baseline(
                today.isoformat(),
                opening_equity=equity,
                opening_realized=realized,
                opening_cash=cash,
                created_at=now,
                # One-time safe migration: preserve whatever opening marks
                # the UTC-based process was already enforcing. Once any ET
                # row exists, the ledger ignores legacy rows forever.
                legacy_utc_date=now.astimezone(timezone.utc).date().isoformat()))
        self._baseline_day = today
        self._realized_baseline = opening_realized
        self._realized_baseline_by_market = dict(
            self.ledger.realized_by_market())
        self._cash_baseline = opening_cash
        self._day_opening_equity = opening_equity
        # The hard stop measures against this. Without it the emergency
        # ceiling stays pinned to whatever equity this process happened to
        # start at, which is the ratchet above.
        if self.risk_guard is not None:
            self.risk_guard.adopt_day_baseline(opening_equity)

    def _stop_reason(self, now: datetime) -> str | None:
        if self._stop_requested:
            return self._stop_requested
        if self.risk_guard is None:
            return "risk_guard_missing"
        if self.risk_guard.hard_stop_reached(now=now):
            return "EMERGENCY_EQUITY_HARD_STOP"
        if self.started_at is not None:
            rows = self.ledger.query(
                "SELECT client_order_id,fee FROM execution_fills "
                "WHERE received_at>=? AND liquidity='TAKER' AND intent IN "
                "('ORDER_INTENT_SELL_LONG','ORDER_INTENT_SELL_SHORT')",
                (self.started_at.isoformat(),),
            )
            taker_exit_orders = {
                str(row["client_order_id"] or "") for row in rows
            }
            positive_fees = sum(
                (max(Decimal("0"), Decimal(str(row["fee"]))) for row in rows),
                Decimal("0"),
            )
            if len(taker_exit_orders) >= self.config.max_taker_exit_orders:
                return "commissioning_taker_exit_limit"
            if positive_fees >= self.config.max_session_execution_fees_usd:
                return "commissioning_execution_fee_limit"
        blockers = self.risk_guard.blockers(now=now)
        # Missing/stale equity is recoverable only by staying alive with new
        # risk disabled. A clock failure or explicit critical condition still
        # enters terminal flatten-and-stop handling.
        transient = {
            "marked account equity is unknown",
            "marked account equity is stale",
        }
        # A loss stop PAUSES rather than ending the session -- operator
        # decision 2026-08-22. It used to flatten, prove flat and exit 0, which
        # the supervisor never restarts, so one bad day cost every hour after
        # it until a human noticed.
        #
        # Pausing works only because the stops now baseline on the ET day. New
        # risk is blocked while the blocker stands, exits and the pre-kickoff
        # flatten keep working so nothing rides to settlement, and at midnight
        # `_roll_realized_baseline` writes a fresh day and the blocker clears
        # by itself. Nothing has to be restarted.
        #
        # Bounded, because on its own it is not: the budget resets every
        # midnight, so a strategy that is systematically wrong rather than
        # unlucky would lose its full allowance daily forever, and the
        # emergency ceiling cannot catch that either since it re-baselines each
        # morning too. After `max_consecutive_loss_stop_days` the pause becomes
        # terminal and a human is required.
        loss_hit = self.LOSS_STOP_BLOCKERS.intersection(blockers)
        if loss_hit:
            today = risk_day(now)
            self.ledger.record_loss_stop(today, at=now)
            streak = self.ledger.consecutive_loss_stop_days(today)
            if streak < self.config.max_consecutive_loss_stop_days:
                transient = transient | self.LOSS_STOP_BLOCKERS
                print(f"[loss-stop-pause] at={now.isoformat()} "
                      f"blockers={sorted(loss_hit)} day={today} "
                      f"consecutive={streak} "
                      f"resumes=next ET day", flush=True)
                # Announced from an async caller: this method is sync, and a
                # pause that nobody is told about is the failure mode the
                # whole change risks -- the bot sits flat and silent instead
                # of stopping loudly.
                self._loss_pause_pending = (today, sorted(loss_hit), streak)
        terminal = tuple(item for item in blockers if item not in transient)
        if terminal:
            return "risk_stop:" + ",".join(terminal)
        return None

    async def _announce_loss_stop_pause(self) -> None:
        """Tell Discord once per day that a loss stop paused trading.

        The old behaviour ended the process, which was at least loud. A pause
        is silent by construction -- the bot stays up, stays flat and simply
        stops quoting -- so without this the first anyone would know is a
        day of missing reward. Sent critical: it is money, and it means the
        day's budget is spent.

        Once per ET day. The blocker stands for the rest of the day by
        design, and `_stop_reason` runs every pass, so an unguarded send would
        be thousands of messages.
        """

        pending, self._loss_pause_pending = self._loss_pause_pending, None
        if pending is None:
            return
        day, blockers, streak = pending
        if self._loss_pause_announced_day == day:
            return
        self._loss_pause_announced_day = day
        remaining = self.config.max_consecutive_loss_stop_days - streak
        await self.notifier.send_async(
            f"🛑 Liquidity Bot hit a loss stop and has PAUSED trading "
            f"for {day} ({', '.join(blockers)}). New risk is blocked; exits "
            f"and the pre-kickoff flatten still run, so nothing rides to "
            f"settlement. It resumes on its own at the next ET midnight. "
            f"Consecutive loss-stop days: {streak} -- "
            f"{remaining} more and it stops for good and needs a human.",
            critical=True)

    def _stuck_mutations(
        self, now: datetime,
    ) -> tuple[dict[str, float], float | None]:
        """Split overdue unresolved intents into per-market and unattributable.

        Returns ``({slug: worst age}, unattributable_age_or_None)``.

        The timeout used to be read account-wide: one overdue intent anywhere
        tripped a terminal stop that took all twenty markets down, and because
        shutdown then could not prove flat it exited 3, which the supervisor
        never restarts. That cost outages on 2026-08-17, 2026-08-18 and twice
        on 2026-08-20.

        An unresolved intent in one market says nothing about the other
        nineteen -- the same argument that scoped the rotation fix. So the
        blast radius now matches the evidence. What stays account-wide is
        ownership: a `venue:` order or an unexplained absence still stops
        everything, because those really are statements about the account.

        An intent with no market to blame cannot be scoped and keeps the old
        terminal behaviour, which is the fail-closed direction.
        """

        limit = float(self.config.unresolved_mutation_timeout_seconds)
        per_market: dict[str, float] = {}
        unattributable: float | None = None
        for row in self.ledger.unresolved_intents():
            age = (now - datetime.fromisoformat(row["updated_at"])).total_seconds()
            if age <= limit:
                continue
            slug = str(row["market_slug"] or "").strip()
            if not slug:
                unattributable = max(unattributable or 0.0, age)
                continue
            per_market[slug] = max(per_market.get(slug, 0.0), age)
        return per_market, unattributable

    def _oldest_unresolved_age_seconds(self, now: datetime) -> float | None:
        rows = self.ledger.unresolved_intents()
        if not rows:
            return None
        oldest = min(datetime.fromisoformat(row["updated_at"]) for row in rows)
        return (now - oldest).total_seconds()

    def _next_unresolved_reconcile_scope(
        self, rows: list[Any] | None = None,
    ) -> frozenset[str] | None:
        """Choose a bounded owner batch, round-robin; None means unscoped."""

        unresolved = rows if rows is not None else self.ledger.unresolved_intents()
        if any(not str(row["market_slug"] or "").strip()
               for row in unresolved):
            return None
        # A single exchange id attributed to multiple markets is not scoped
        # ownership. Include current working rows because a conflicting owner
        # can be split between the order table and its unresolved intent. The
        # strict account-wide reconcile retains the existing fail-closed
        # treatment for this case.
        exchange_markets: dict[str, set[str]] = {}
        for row in (*self.ledger.working_orders(owned_only=True), *unresolved):
            exchange_id = str(row["exchange_order_id"] or "").strip()
            market = str(row["market_slug"] or "").strip()
            if exchange_id and market:
                exchange_markets.setdefault(exchange_id, set()).add(market)
        if any(len(owners) != 1 for owners in exchange_markets.values()):
            return None
        markets = sorted({
            str(row["market_slug"]) for row in unresolved
            if str(row["market_slug"] or "").strip()
        })
        if not markets:
            return frozenset()
        size = min(self.UNRESOLVED_RECONCILE_OWNER_BATCH_SIZE, len(markets))
        start = self._unresolved_reconcile_cursor % len(markets)
        selected = {
            markets[(start + offset) % len(markets)]
            for offset in range(size)
        }
        self._unresolved_reconcile_cursor += size
        return frozenset(selected)

    async def _repair_unresolved_orders(
        self, now: datetime, *, markets: frozenset[str],
    ) -> None:
        """Seek positive terminal proof without repeating account snapshots.

        The normal ten-second reconcile already refreshes complete open-order
        and position snapshots. Repeating those account reads once per owner
        made a private disconnect's twenty cancel owners take several minutes
        to repair. This focused path asks only for the unresolved ids in a
        bounded owner batch. Each read is independent and concurrent so one
        venue NotFound retry ladder cannot hide the other answers; the shared
        REST limiter remains the authority on request rate.

        No absence is interpreted here. An intent resolves only if
        ``reconcile_order`` applies an affirmative venue state.
        """

        started_at = self._now()
        unresolved_rows = self.ledger.unresolved_intents()
        scoped_intent_ids = {
            str(row["intent_id"])
            for row in unresolved_rows
            if str(row["market_slug"] or "").strip() in markets
        }
        unresolved_cancels = self._unresolved_cancel_owners()
        exchange_ids = sorted({
            str(row["exchange_order_id"] or "").strip()
            for row in unresolved_rows
            if str(row["market_slug"] or "").strip() in markets
            and str(row["exchange_order_id"] or "").strip()
        })

        async def repair(exchange_id: str) -> tuple[str, str] | None:
            try:
                await self.executor.reconcile_order(
                    exchange_id, observed_at=now)
            except RuntimeInvariantViolation:
                raise
            except Exception as exc:
                return exchange_id, f"{type(exc).__name__}: {exc}"
            return None

        results = await asyncio.gather(*(repair(item) for item in exchange_ids))
        # A positive OPEN read may license retrying an uncertain cancel. This
        # still uses the normal per-owner throttle and mutation state machine.
        await self._retry_uncertain_cancels(now, markets=markets)
        self._record_resolved_cancel_owners(unresolved_cancels)
        failures = [result for result in results if result is not None]
        remaining_intent_ids = {
            str(row["intent_id"])
            for row in self.ledger.unresolved_intents()
            if str(row["market_slug"] or "").strip() in markets
        }
        finished_at = self._now()
        print(
            f"[unresolved-repair] at={finished_at.isoformat()} "
            f"owners={len(markets)} ids={len(exchange_ids)} "
            f"resolved={len(scoped_intent_ids - remaining_intent_ids)} "
            f"remaining={len(remaining_intent_ids)} failures={len(failures)} "
            f"elapsed={max(0.0, (finished_at - started_at).total_seconds()):.1f} "
            f"markets={','.join(sorted(markets))}",
            flush=True,
        )
        if failures:
            raise AdapterError(
                "could not re-read "
                f"{len(failures)} unresolved order(s): "
                + "; ".join(
                    f"{exchange_id}: {detail}"
                    for exchange_id, detail in failures))

    def _quiet_book_needs_rest_refresh(self, now: datetime) -> bool:
        if self.book_observed_at is None:
            return True
        age = (now - self.book_observed_at).total_seconds()
        return age < 0 or age >= 3

    async def _refresh_quiet_book_if_due(
        self, next_refresh: datetime, slug: str | None = None,
    ) -> datetime:
        """Refresh a quiet book, and record whether a refresh actually ran.

        ``_refreshed_book_this_call`` exists because the caller clears the
        `quiet_book_rest_refresh` failure counter whenever this returns without
        raising -- and this returns without raising in two very different
        cases: a refresh that succeeded, and a refresh that was never attempted
        because it was not due yet.

        Treating the second as success reset the counter it could never then
        accumulate. Measured live 2026-08-17: 440 failures on one market with
        a permanently empty book, escalation never passing attempt=3 against a
        MAX_RECOVERY_ATTEMPTS of 12, so `unrecoverable_book_stale` never fired
        and the market held its slot for **two hours** while nineteen slots sat
        free and the bot placed no orders at all.
        """

        # Read time inside this boundary, after every upstream awaited call.
        state = self.state_for(slug or self.market_slug)
        now = self._now()
        self._refreshed_book_this_call = False
        if now < next_refresh:
            return next_refresh
        if self._book_refresh_would_be_pointless(state.slug, now):
            # Nothing to quote and nothing to exit. Backing the schedule well
            # off keeps the slot's bookkeeping intact without spending the
            # governor on a book that will never parse again.
            return now + timedelta(seconds=self.DEAD_MARKET_REFRESH_SECONDS)
        if state.book_is_quiet(now, max_age_seconds=3):
            await self._refresh_book(now, state.slug)
            self._refreshed_book_this_call = True
        # Anchor the next check to the actual book receipt. If a due check was
        # skipped because a WebSocket book arrived 2.9 seconds ago, scheduling
        # three seconds from *now* would cross the five-second stale limit.
        anchor = state.book_observed_at or self._now()
        return anchor + timedelta(seconds=3)

    def _book_is_fresh_enough_to_plan(self, slug: str) -> bool:
        """Would the reconciler accept this book right now?

        Mirrors the gate in ``ReconcilerPolicy`` (``reconciler.py``): the book
        must exist and be no older than ``max_book_age_seconds``.
        """

        # `.get`, not `state_for`: this is a predicate and must not create
        # state. `_bind_markets` deliberately discards entries for markets the
        # session no longer holds, so that a stale book can never authorize a
        # quote -- a read that resurrects the key works against that contract.
        state = self._markets.get(slug)
        if state is None or state.book is None or state.book_observed_at is None:
            return False
        age = (self._now() - state.book_observed_at).total_seconds()
        return 0 <= age <= self.reconciler.policy.max_book_age_seconds

    def _has_kicked_off(self, slug: str) -> bool:
        """Has this market's event already started?

        A started market cannot become pregame again, so it will never quote
        and is only waiting to be rotated out. Fails closed: an unknown start
        time is NOT treated as kicked off, because that would quietly excuse a
        genuinely stuck slot as "waiting for rotation".
        """

        try:
            start = self.state_for(slug).market.game_start
        except Exception:
            return False
        return start is not None and start <= self._now()

    # A market that placed or cancelled this recently is mid-cycle, not stuck.
    # Measured 2026-08-19: markets cycled every 17-40s under the churn, so a
    # sample taken at any instant catches several between a cancel and its
    # re-place. Counting those as stuck is what made the alarm fire on
    # ordinary operation. Comfortably above the observed cycle time.
    CYCLING_WINDOW_SECONDS = 60.0

    def _cycling_markets(self) -> int:
        """Bound markets holding no order that acted within the window.

        Never raises, for the same reason `_idle_market_reasons` does not: it
        is reporting, and the summary is sent from the shutdown loop where an
        exception reads as a failure to prove flat.
        """

        now = self._now()
        cycling = 0
        for slug in self.active_slugs:
            try:
                if self.ledger.working_orders(slug) or self._is_retired(slug):
                    continue
                if self._has_kicked_off(slug):
                    continue
                last = self._last_action_at.get(slug)
                if (last is not None
                        and (now - last).total_seconds()
                        <= self.CYCLING_WINDOW_SECONDS):
                    cycling += 1
            except Exception:
                continue
        return cycling

    def _awaiting_rotation(self) -> int:
        """Idle slots held only because their market has already started.

        Never raises, for the same reason `_idle_market_reasons` does not:
        this is reporting, and the summary is sent from the shutdown loop
        where an exception reads as a failure to prove flat -- which exits 3,
        which the supervisor never restarts. A reporting metric must not be
        able to keep a flat process from stopping cleanly.

        Counted per market rather than in one comprehension so that one
        market that cannot be inspected costs only its own row. Undercounting
        here is the safe direction: it moves a slot into `SLOTS NOT WORKING`,
        which over-reports a fault rather than hiding one.
        """

        waiting = 0
        for slug in self.active_slugs:
            try:
                if (not self.ledger.working_orders(slug)
                        and not self._is_retired(slug)
                        and self._has_kicked_off(slug)):
                    waiting += 1
            except Exception:
                continue
        return waiting

    def _idle_market_reasons(self) -> tuple[tuple[str, int], ...]:
        """Why each managed market is not resting an order, most common first.

        "Nine of twenty are working" does not say whether the other eleven are
        broken, priced out, or simply not worth quoting -- and those need very
        different responses. A market blocked on `target_not_met` is the reward
        gate declining correctly; one blocked on `book_stale` is a fault.

        Never raises: this exists only to be reported, and the summary is sent
        from the shutdown loop where an exception reads as a failure to prove
        flat.
        """

        counts: dict[str, int] = {}
        for slug in self.active_slugs:
            try:
                if self.ledger.working_orders(slug):
                    continue
                if self._is_retired(slug):
                    reason = "retired"
                elif self._has_kicked_off(slug):
                    # Not a fault. The market started, so it can never be
                    # quoted again and is simply waiting for the next rotation
                    # sweep to release the slot. Counting it as broken made
                    # `SLOTS NOT WORKING` fire on a normal, harmless condition
                    # -- measured 2026-08-18, 96 markets did this in one day,
                    # each holding a slot ~11 minutes, and free slots never
                    # dropped below 9, so none of it cost anything. An alarm
                    # that cries wolf on the ordinary case stops being read.
                    reason = "kicked_off"
                elif (state := self.state_for(slug)).book is None:
                    reason = "no_book"
                elif blockers := sorted(set(
                        state.economic_blocker_by_side.values())):
                    reason = str(blockers[0])
                else:
                    reason = "no_quote_yet"
            except Exception:
                reason = "unknown"
            counts[reason] = counts.get(reason, 0) + 1
        return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def _ledger_bytes_on_disk(self) -> int:
        """Ledger file plus its WAL sidecar, in bytes.

        The ledger is the audit trail and is never rebuilt, so it only grows --
        47,784 events and 22MB inside three days, on a droplet where nothing
        else warns before the disk fills. Reporting the `.db` alone would
        understate it: WAL mode keeps an uncommitted tail in `-wal` that can be
        a large fraction of the total.

        Never raises. A summary that cannot be sent because a stat failed is a
        worse outcome than a size it could not read.
        """

        total = 0
        path = getattr(self.ledger, "path", None)
        if path is None:
            return 0
        for candidate in (path, Path(f"{path}-wal")):
            try:
                total += Path(candidate).stat().st_size
            except OSError:
                continue
        return total

    def _log_shutdown(self, market_slug: str, decision: str, **fields: Any) -> None:
        """Log one shutdown decision, the way `[action]` logs a live one.

        Shutdown builds its own ``PlanAction`` and calls ``driver.apply_one``
        directly, so none of it ever reached the `[action]` log. When the exit
        budget was exhausted on krovit-sukiho (2026-08-15) the alert said the
        budget was gone and nothing recorded where it went -- not the prices
        tried, not whether depth refused, not whether the market was skipped
        for a working order. Shutdown is when a position gets stranded
        overnight, so it is the last path that should be invisible.
        """

        detail = " ".join(
            f"{name}={value}" for name, value in fields.items()
            if value is not None)
        print(
            f"[shutdown] at={self._now().isoformat()} market={market_slug} "
            f"decision={decision}" + (f" {detail}" if detail else ""),
            flush=True,
        )

    async def _decide_for_market(
        self, slug: str, now: datetime, *, replanned: bool = False,
    ) -> tuple[str | None, float]:
        """Plan and apply at most one mutation for one market.

        Returns ``(stop_reason, backoff_seconds)``. A stop reason is terminal
        for the whole session; a backoff defers only this market's next pass.
        Recoverable conditions are handled here so one market's stale book or
        pending exit cannot stall the other nineteen.

        The clock is read per market rather than once per sweep: issuing twenty
        mutations through a 4 requests/second governor takes real time, and a
        market whose book aged meanwhile must report ``book_stale`` and be
        skipped rather than quote on data that is no longer fresh.
        """

        state = self.state_for(slug)
        retired = self._retired.get(slug)
        if retired is not None and retired.rounds >= MAX_RETIREMENT_ROUNDS:
            # Abandoned. Still flattened by shutdown, never planned again.
            return None, 0.0
        if (retired is not None and retired.next_retry_at is not None
                and now < retired.next_retry_at):
            # Inside the cooldown the position simply rests. Its reducing quote
            # is already working and the pre-kickoff flatten is unaffected;
            # replanning here would re-fire the same condition immediately.
            return None, 0.0
        try:
            actions = self.reconciler.plan(self._frame(now, slug))
        except RuntimeInvariantViolation as invariant:
            # A violated runtime invariant is about the process, not this
            # market. It stays terminal.
            return await self._handle_strategy_plan_failure(invariant), 0.0
        except Exception as exc:
            # Planning raised on this market's own state. Ending the session
            # would take seven healthy markets down with it and stop all
            # accrual; retiring keeps them quoting while this one is worked out
            # and never quoted again. Retirement is bounded -- ten rounds per
            # market, and a full slate of retirements still stops the session,
            # so a systemic planning fault is not silently absorbed.
            await self._report_runtime_error(f"strategy_plan:{slug}", exc)
            stop = await self._retire_market(
                slug, f"plan_failed_{type(exc).__name__}")
            return stop, 0.0
        if actions and actions[0].kind == ActionKind.CRITICAL:
            reason = actions[0].reason
            if reason == "hard_exit_sent_but_inventory_remains":
                try:
                    outcome = await self._reconcile_pending_hard_exit(slug)
                    print(
                        "[runtime-recovery] "
                        f"at={self._now().isoformat()} market={slug} "
                        f"component=hard_exit_confirmation outcome={outcome}",
                        flush=True,
                    )
                    if (outcome == "pending"
                            and self.reconciler.hard_exit_confirmation_expired(
                                slug, now=self._now())):
                        stop = await self._retire_market(
                            slug, "hard_exit_confirmation_timeout",
                            reset_exit_budget=False)
                        return stop, 0.0
                    self._runtime_error_counts.pop(
                        f"hard_exit_confirmation:{slug}", None)
                    if outcome == "flat":
                        return None, 0.0
                    attempts = self.reconciler.hard_exit_attempts(slug)
                    return None, float(min(2 ** max(attempts - 1, 0), 5))
                except Exception as exc:
                    await self._report_runtime_error(
                        f"hard_exit_confirmation:{slug}", exc)
                    if self.reconciler.hard_exit_confirmation_expired(
                            slug, now=self._now()):
                        stop = await self._retire_market(
                            slug, "hard_exit_confirmation_timeout",
                            reset_exit_budget=False)
                        return stop, 0.0
                    return None, self._recovery_delay(
                        f"hard_exit_confirmation:{slug}")
            stop = await self._handle_strategy_critical(reason, slug)
            if stop:
                return stop, 0.0
            # A refreshed book is only worth fetching if we plan on it while it
            # is still fresh. Returning straight to `_recovery_delay` -- which
            # escalates to a 30s cap -- outlives the five-second freshness
            # budget the refresh just satisfied, so the next visit reports
            # `book_stale` again and the refresh was wasted. That loop feeds
            # itself: measured 2026-08-16, 7,535 book_stale events with
            # consecutive ones 34-75s apart on the same market, and 30 of the
            # session's 31 retirements were `unrecoverable_book_stale`.
            #
            # It also selects against exactly the markets we want. Quiet books
            # are the ones with the least competition for reward share, and
            # reward is paid for RESTING, not for trading -- a market with
            # nothing to send is not a market that is broken.
            #
            # Replanning once here also lets the success path below clear
            # `strategy_state:{slug}`, so a market that recovers stops marching
            # toward MAX_RECOVERY_ATTEMPTS. `replanned` bounds this to a single
            # extra attempt per sweep, so a book that will not refresh still
            # backs off instead of spinning.
            if reason == "book_stale":
                # Diagnostic only, no behaviour change. Measured 2026-08-17:
                # seven markets held slots for 54-130 minutes and never placed
                # a single order, every visit reporting `book_stale` at
                # attempt=1 -- while their books fetched cleanly from REST,
                # two-sided and open, and no `quiet_book_rest_refresh` error
                # was ever logged for them. So the refresh does not fail; the
                # planner just never sees a fresh book, and nothing recorded
                # enough to say why. These three numbers separate the
                # candidates: a refresh that never fires (age keeps growing,
                # `since_refresh` large), one that fires but lands stale
                # (`age` already over budget right after a refresh), and a
                # replan that is never reached (`fresh_enough` false).
                state = self.state_for(slug)
                now_at = self._now()
                age = state.book_age_seconds(now_at)
                due = state.next_book_refresh
                print(
                    f"[book-stale-why] at={now_at.isoformat()} market={slug} "
                    f"age={'none' if age is None else round(age, 1)} "
                    f"refresh_due_in="
                    f"{'none' if due is None else round((due - now_at).total_seconds(), 1)} "
                    f"fresh_enough={self._book_is_fresh_enough_to_plan(slug)} "
                    f"replanned={replanned}",
                    flush=True,
                )
            if (not replanned and reason == "book_stale"
                    and self._book_is_fresh_enough_to_plan(slug)):
                return await self._decide_for_market(
                    slug, self._now(), replanned=True)
            return None, self._recovery_delay(f"strategy_state:{slug}")
        # Per market. A shared counter let one market's repeated failure
        # inflate every market's backoff toward the 30s cap, and let any one
        # market's success reset a different market's escalation.
        for key in ("runtime_state_gate", "runtime_state_quarantine",
                    "strategy_plan", "strategy_plan_recovery"):
            self._runtime_error_counts.pop(key, None)
        for key in ("strategy_state", "strategy_state_recovery",
                    "action_driver", "action_driver_recovery"):
            self._runtime_error_counts.pop(f"{key}:{slug}", None)
        if self.driver is None or state.market is None:
            raise RuntimeError("commissioning driver is unavailable")
        # Log the mutation and why the planner asked for it. Without this the
        # ledger records that an order was cancelled but never the reason, so
        # the 2026-08-14 place/cancel churn could not be diagnosed from the
        # data it left behind -- only guessed at.
        # An empty plan is the normal healthy case -- the desired state already
        # matches, so there is nothing to do and nothing to log. Indexing it
        # unconditionally crashed the runner every time the planner was
        # satisfied, which is most passes.
        if actions:
            chosen = actions[0]
            print(
                f"[action] at={now.isoformat()} market={slug} "
                f"kind={chosen.kind.name} reason={chosen.reason} "
                f"side={chosen.side} price={chosen.price} "
                f"working={len(self._working_orders(slug))} "
                f"pending={len(actions)} "
                f"blockers={state.economic_blocker_by_side or '{}'} "
                f"net={ {k: round(v, 6) for k, v in
                         state.expected_net_by_side.items()} }",
                flush=True,
            )
        try:
            # Reuse one ledger snapshot for both observation classification
            # and the action driver.  Observation must not add another query
            # to each placement or see a different position than the driver.
            position = self.ledger.position(slug)
            quote_observation = (
                self._quote_observation_for_action(
                    state, actions[0], now, position)
                if actions else None)
            await self.driver.apply_one(
                actions, market=state.market,
                position=position, now=now,
                quote_observation=quote_observation)
            self._queue_measurement_sync(observed_at=self._now())
        except Exception as exc:
            stop = await self._handle_action_failure(exc, slug)
            if stop:
                return stop, 0.0
            return None, self._recovery_delay(f"action_driver:{slug}")
        if actions:
            # Stamp a successful mutation, not an empty healthy plan.
            self._last_action_at[slug] = self._now()
        return None, 0.0

    async def _decision_sweep(
        self, slugs: tuple[str, ...] | None = None,
    ) -> str | None:
        """Plan every due market without cross-slot intent/backoff blocking."""

        state_refresh_failed = False
        planned_slugs = self.active_slugs if slugs is None else slugs
        for slug in planned_slugs:
            if slug not in self._markets:
                continue
            if self.ledger.market_has_unresolved_intent(slug):
                continue
            now = self._now()
            due_at = self._next_decision_at.get(slug)
            if due_at is not None and now < due_at:
                continue
            if not state_refresh_failed:
                try:
                    await self._refresh_execution_state_for_decision(now)
                except Exception:
                    # Preserve the last observation until its 30-second bound;
                    # once stale, the frame refuses new risk. Do not retry this
                    # account-wide REST proof for every remaining market in the
                    # same sweep; the outer loop will give it another chance.
                    state_refresh_failed = True
            try:
                await self._refresh_equity_for_decision(now)
            except Exception:
                # Preserve the last observation until its 15-second bound;
                # once stale, `_frame` removes entry eligibility fail-closed.
                pass
            outcome, delay = await self._decide_for_market(slug, self._now())
            # A normal full sweep can reach a market whose REST terminal proof
            # queued priority earlier in this same loop. Its replan has now
            # happened, so do not service it again next iteration.
            self._priority_replan_slugs.discard(slug)
            if outcome:
                return outcome
            if delay > 0:
                self._next_decision_at[slug] = (
                    self._now() + timedelta(seconds=delay))
            else:
                self._next_decision_at.pop(slug, None)
        return None

    async def _consume_ready_stream_tasks(
        self,
        market_task: asyncio.Task[Any],
        private_task: asyncio.Task[Any],
    ) -> tuple[asyncio.Task[Any], asyncio.Task[Any], str | None]:
        """Apply completed stream work and drain data queued behind it.

        Called both when the loop wakes and after serial REST service. A task
        can consume the first event while service is in progress; draining the
        adapter without applying that held result first would reorder private
        mutations. This method preserves that order, then catches up fully
        before the decision gate is evaluated.
        """

        if market_task.done():
            try:
                self._accept_book(market_task.result())
                drain_books = getattr(self.market_stream, "drain_books", None)
                if callable(drain_books):
                    for book in drain_books():
                        self._accept_book(book)
            except Exception as exc:
                stop = await self._recover_market_stream(exc)
                if stop:
                    return market_task, private_task, stop
            market_task = asyncio.create_task(self.market_stream.next_book())

        if private_task.done():
            try:
                await self._observe_private(private_task.result(), self._now())
                # Private updates cannot be coalesced safely (fills and order
                # transitions are ordered), so drain in wire order.
                drain_events = getattr(self.private_stream, "drain_events", None)
                if callable(drain_events):
                    for event in drain_events():
                        await self._observe_private(event, self._now())
            except Exception as exc:
                stop = await self._recover_private_stream(exc)
                if stop:
                    return market_task, private_task, stop
            private_task = asyncio.create_task(self.private_stream.next_event())

        return market_task, private_task, None

    async def _refresh_bound_books(self, now: datetime) -> None:
        """Prove a book for every bound market, dropping the ones that fail.

        Every slot is best effort, including slot zero. Requiring all of them
        would let one unlucky market block startup entirely, and startup
        failure is the most expensive kind -- nothing quotes at all.

        Slot zero used to be exempt and refreshed unguarded, which made it the
        single point of failure the rest of this loop exists to avoid. Observed
        2026-08-16T21:36Z: the primary published a book with no levels on
        either side, `parse_book_response` raised `book is empty on both
        sides`, and startup exited 2 -- a code the supervisor never restarts --
        over one dud market out of twenty. About one market in four publishes a
        one-sided book (see adapters.py), so drawing a bad slot zero is not
        rare.

        Two things still refuse. A market we are RECOVERING holds a position we
        were restarted to retire, so dropping it would abandon live risk. And
        dropping every market would bind an empty set, which leaves
        `market_slug` raising and takes the run loop down on its next call.
        """

        recovering = set(self._recovery_slugs) & set(self.active_slugs)
        healthy: list[str] = []
        for slug in self.active_slugs:
            try:
                await self._refresh_contracts(
                    now, slug, for_exit=slug in recovering)
                await self._refresh_book(now, slug)
                healthy.append(slug)
            except Exception as exc:
                if slug in recovering:
                    raise
                await self._report_runtime_error(
                    f"startup_market_dropped:{slug}", exc)
        if not healthy:
            raise CommissioningRefused("no bound market has a usable book")
        if len(healthy) != len(self.active_slugs):
            self._bind_markets(tuple(healthy))

    async def _service_bound_markets(
        self, slugs: tuple[str, ...] | None = None,
    ) -> str:
        """Visit every bound market, surviving any one of them failing.

        Serviced, not merely selected: a market holding risk must keep being
        visited after selection moves on, or its exit is never planned.

        Returns a stop reason, or "" to keep running. A market that raises is
        reported and retired rather than allowed to end the session -- the same
        treatment `_plan_and_apply` already gives a planning failure. That
        guard sat only around `reconciler.plan`, so anything raising in the
        servicing steps AROUND it escaped. `_evaluate_rewards` did exactly that
        on 2026-08-16T19:01Z with a book whose bids had all been pulled: the
        runner died, shutdown parked a live position rather than cross at a
        loss, and being unable to prove flat the supervisor refused to restart.
        79 minutes down, and nineteen healthy markets taken along with the one
        that broke.

        Absorbing a local defect is not the same as hiding a systemic one.
        Retirement is bounded at ten rounds per market, and a full slate of
        retirements still stops the session, so a fault in every market still
        surfaces as one.
        """

        serviced_slugs = self.managed_slugs() if slugs is None else slugs
        for slug in serviced_slugs:
            if slug not in self._markets:
                continue
            try:
                await self._service_market_state(slug, self._now())
            except RuntimeInvariantViolation as invariant:
                # About the process, not this market. Stays terminal.
                return await self._handle_strategy_plan_failure(invariant)
            except Exception as exc:
                await self._report_runtime_error(f"market_service:{slug}", exc)
                stop = await self._retire_market(
                    slug, f"service_failed_{type(exc).__name__}")
                if stop:
                    return stop
        return ""

    async def _service_market_state(self, slug: str, now: datetime) -> None:
        """Bring one market's contracts, book and economics up to date.

        Each market carries its own REST cadence. A shared schedule would let a
        single quiet book consume the governor on behalf of all of them, and a
        single failing market would reset everyone else's timers.
        """

        state = self.state_for(slug)
        if state.next_contract_refresh is None or now >= state.next_contract_refresh:
            try:
                # A barred market never quotes, so the gates that guard
                # quoting have nothing to protect here -- applying them would
                # only raise `no longer pregame` on every refresh for the whole
                # time we are exiting, once a second, for hours.
                # Exit-only markets load with the gates relaxed: those gates
                # exist to stop us OPENING risk, and a market we are trying to
                # leave has already opened it. Without this an exit-only market
                # whose game has started raises `no longer pregame` on every
                # refresh and never gets a fresh book to price against.
                await self._refresh_contracts(
                    now, slug, for_exit=self._is_barred_from_quoting(slug))
                self._runtime_error_counts.pop(
                    f"market_incentive_refresh:{slug}", None)
            except Exception as exc:
                # Only this market stops quoting. The others are unaffected.
                state.incentive = None
                state.reward_eligible = frozenset()
                await self._report_runtime_error(
                    f"market_incentive_refresh:{slug}", exc)
            state.next_contract_refresh = self._now() + timedelta(minutes=1)
        # Keep the book last among this market's REST work so metadata latency
        # cannot age the book before the decision that follows.
        try:
            state.next_book_refresh = await self._refresh_quiet_book_if_due(
                state.next_book_refresh or now, slug)
            # Only a refresh that actually RAN proves the book is readable. A
            # "not due yet" return proves nothing, and clearing the counter on
            # it stopped consecutive failures from ever accumulating to
            # MAX_RECOVERY_ATTEMPTS -- see _refresh_quiet_book_if_due.
            if self._refreshed_book_this_call:
                self._runtime_error_counts.pop(
                    f"quiet_book_rest_refresh:{slug}", None)
        except Exception as exc:
            await self._report_runtime_error(
                f"quiet_book_rest_refresh:{slug}", exc)
            state.next_book_refresh = self._now() + timedelta(seconds=3)
            # A book that will not parse is not a book we can ever quote, and
            # retrying it every three seconds holds a market slot while burning
            # REST budget. `book is not two-sided` was the most common error in
            # the log at 2,121 occurrences across 21 of 77 markets, with two
            # markets retried about 1,100 times each. Retire it on the same
            # bound the strategy-state recoveries use.
            if (self._runtime_error_counts.get(
                    f"quiet_book_rest_refresh:{slug}", 0)
                    >= MAX_RECOVERY_ATTEMPTS):
                stop = await self._retire_market(
                    slug, f"unrecoverable_book_{type(exc).__name__}",
                    reset_exit_budget=False)
                if stop:
                    raise RuntimeInvariantViolation(
                        "market_retirement_limit_exceeded", stop)
        # Check before evaluating: a retired market that reached flat should
        # stop being treated as retired in the same pass that would otherwise
        # keep blocking its slot.
        self._release_retired_if_flat(slug)
        self._evaluate_rewards(self._now(), slug)

    async def _send_summary(self, now: datetime, *, critical: bool = False,
                            problems: tuple[str, ...] = ()) -> None:
        # Whole-account, not the primary market. Reporting only slot zero
        # meant a position on any other slot showed as open_positions 0 and an
        # inventory age of 0 -- the two numbers an operator watches to decide
        # whether to intervene.
        reported_slugs = self._shutdown_slugs() or self.active_slugs
        positions_by_slug = {
            slug: self.ledger.position(slug) for slug in reported_slugs}
        open_positions = sum(
            1 for value in positions_by_slug.values() if value != 0)
        position = positions_by_slug.get(self.market_slug, Decimal("0"))
        oldest_inventory_age = 0.0
        for slug in reported_slugs:
            slug_basis = self.ledger.inventory_basis(slug)
            if slug_basis is None:
                continue
            oldest_inventory_age = max(
                oldest_inventory_age,
                max(0.0, (now - slug_basis.opened_at).total_seconds()))
        working = self.ledger.working_orders()
        unresolved = self.ledger.unresolved_intents()
        unknown = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_orders "
            "WHERE status='UNKNOWN_ABSENT' OR client_order_id LIKE 'venue:%'")[0]["n"]
        equity_change = Decimal("0")
        if self.balance is not None and self.risk_guard is not None:
            equity_change = (
                self.balance.marked_equity - self.risk_guard.starting_equity)
        place_count = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        session_start = (self.started_at or now).isoformat()
        entry_fills = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_fills WHERE intent IN "
            "('ORDER_INTENT_BUY_LONG','ORDER_INTENT_BUY_SHORT') "
            "AND received_at>=?", (session_start,))[0]["n"]
        exit_fills = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_fills WHERE intent IN "
            "('ORDER_INTENT_SELL_LONG','ORDER_INTENT_SELL_SHORT') "
            "AND received_at>=?", (session_start,))[0]["n"]
        observed = [self.state_for(slug).book_observed_at
                    for slug in self.active_slugs]
        oldest_book = min((at for at in observed if at is not None),
                          default=None)
        market_age = ((now - oldest_book).total_seconds()
                      if oldest_book is not None else None)
        private_age = ((now - self.gate.last_message_at).total_seconds()
                       if self.gate.last_message_at else None)
        # Counted from resting orders, never from `[action]` log activity: a
        # market that placed its quotes and is resting quietly logs no actions
        # at all, so an activity-based count falls exactly when things are
        # going well. Measured 2026-08-16 -- it read 17 while only 9 markets
        # actually held an order.
        #
        # Reads the ledger directly rather than `_working_orders`, which raises
        # RuntimeInvariantViolation on an order with no owned place intent. The
        # summary is also sent from the shutdown loop, where that exception is
        # caught as a failure to prove flat -- so a reporting metric would have
        # kept a flat process from stopping.
        resting_markets = sum(
            1 for slug in self.active_slugs
            if self.ledger.working_orders(slug))
        idle_reasons = self._idle_market_reasons()
        payload = build_liquidity_summary(LiquiditySummary(
            mode="LIVE",
            readiness_status="COMMISSIONING_REWARD_ECONOMICS_ENFORCED",
            observed_at=now,
            session_started_at=self.started_at or now,
            active_markets=len(self.active_slugs),
            resting_markets=resting_markets,
            idle_market_reasons=idle_reasons,
            reward_eligible_sides=sum(
                len(self.state_for(slug).reward_eligible)
                for slug in self.active_slugs),
            score_seconds=self._score_seconds,
            entry_fills=entry_fills,
            exit_samples=exit_fills,
            exit_completion_rate=(
                1.0 if open_positions == 0 and entry_fills
                else min(exit_fills / entry_fills, 1.0) if entry_fills else 0.0),
            open_positions=open_positions,
            working_orders=len(working),
            max_inventory_age_seconds=oldest_inventory_age,
            unresolved_intents=len(unresolved),
            unknown_orders=unknown,
            trading_realized_usd=equity_change,
            open_marked_usd=Decimal("0"),
            session_loss_limit_usd=self.config.max_session_loss_usd,
            hard_stop_loss_usd=self.config.hard_stop_loss_usd,
            # The stop's own input, so the meter measures the thing that
            # actually fires. `trading_realized_usd` above is marked equity
            # and swings with unrealised ticks -- 320 open contracts move $38
            # on twelve cents -- so a bar drawn from it would show the bot
            # walking into a limit nothing is walking into.
            session_realized_pnl_usd=(
                self.risk_guard.realized_pnl if self.risk_guard is not None
                else Decimal("0")),
            # Supply, straight off the last selection scan. These counters
            # already exist on the selection object and were never reported
            # anywhere -- which is why "is it quoting 3 because 3 is all
            # there is, or because it is broken?" needed a 50-call script on
            # the droplet to answer.
            slot_capacity=self.config.max_markets,
            awaiting_rotation_markets=self._awaiting_rotation(),
            cycling_markets=self._cycling_markets(),
            viable_markets=(
                self._market_selection.viable_markets
                if self._market_selection is not None else 0),
            rewarded_markets=(
                self._market_selection.rewarded_executable_markets
                if self._market_selection is not None else 0),
            spread_filtered_markets=(
                self._market_selection.catalog_spread_filtered_markets
                if self._market_selection is not None else 0),
            selection_failures=(
                len(self._market_selection.failures)
                if self._market_selection is not None else 0),
            # Marked equity against the DAY's opening equity, from the
            # ledger so a restart cannot reset it. This is what
            # `hard_stop_reached` fires on, so the HARD meter and the headline
            # now read the same number the stop does. Account-wide by nature:
            # equity carries no per-system attribution (CLAUDE.md).
            today_pnl_usd=(
                self.balance.marked_equity - self._day_opening_equity
                if self.balance is not None
                and self._day_opening_equity is not None else Decimal("0")),
            # Lifetime, and genuinely this bot's: the ledger holds only this
            # bot's fills, so no date filter is needed. Understates losses,
            # because a settled position has its entry recorded and its exit
            # invisible -- $67.38 across 29 markets as of 2026-08-19.
            # This bot's own bottom line: its ledger trading (the ledger holds
            # only this bot's fills) plus the reward it actually earned, dated
            # from its first order. PAID only -- invariant 22 keeps pending out
            # of accounted net -- and pending is carried separately as context.
            # Understates losses: a settled position has its entry recorded and
            # its exit invisible, $67.38 across 29 markets as of 2026-08-19.
            overall_pnl_usd=self._bot_overall_pnl(),
            attributable_paid_rewards_usd=self._bot_era_rewards.get(
                "PAID", Decimal("0")),
            pending_rewards_usd=self._bot_era_rewards.get(
                "PENDING", Decimal("0")),
            worst_market_realized_pnl_usd=(
                min(self.risk_guard.realized_by_market.values(),
                    default=Decimal("0"))
                if self.risk_guard is not None else Decimal("0")),
            pnl_label="marked account equity Δ",
            rest_requests_per_second=float(self.config.rest_requests_per_second),
            market_stream_age_seconds=market_age,
            private_stream_age_seconds=private_age,
            ledger_bytes=self._ledger_bytes_on_disk(),
            integrity_problems=problems,
        ))
        # Expose actual create count only in the description, never imply that
        # shadow observations were venue orders.
        economically_allowed = sorted({
            side for slug in self.active_slugs
            for side in self.state_for(slug).expected_net_by_side})
        maximum_remaining = max(
            (item.maximum_remaining_reward
             for slug in self.active_slugs
             for item in self.state_for(
                 slug).reward_projection_by_side.values()),
            default=0.0,
        )
        paid_observed = self._earnings_by_status.get("PAID", Decimal("0"))
        pending_observed = self._earnings_by_status.get("PENDING", Decimal("0"))
        risk_by_market_side = {
            (slug, side): value
            for slug in self.active_slugs
            for side, value in self.state_for(
                slug).fill_probability_by_side.items()}
        maximum_fill_probability = max(
            risk_by_market_side.values(), default=0.0)
        fill_driver = "none"
        if risk_by_market_side:
            riskiest = max(
                risk_by_market_side, key=risk_by_market_side.__getitem__)
            fill_driver = self.state_for(
                riskiest[0]).fill_risk_driver_by_side.get(
                    riskiest[1], "unknown")
        # These are diagnostics, not decisions. They used to be appended to the
        # embed footer, which grew to one unreadable line of twenty values.
        # Logged instead, where they can be grepped.
        print(
            f"[selection] at={now.isoformat()} "
            f"session_creates={place_count - self._orders_before} "
            f"economic_sides={','.join(economically_allowed) or 'none'} "
            f"max_remaining_reward={maximum_remaining:.4f} "
            f"fill_risk_30s={maximum_fill_probability:.4f} "
            f"fill_driver={fill_driver} "
            f"earnings_paid={paid_observed:.2f} "
            f"earnings_pending={pending_observed:.2f} "
            f"retired={len(self._retired_ever)}"
            + (f" catalog={self._market_selection.catalog_markets}"
               f" executable={self._market_selection.executable_catalog_markets}"
               f" rewarded={self._market_selection.rewarded_executable_markets}"
               f" books_ranked={self._market_selection.books_evaluated}"
               f" viable={self._market_selection.viable_markets}"
               if self._market_selection is not None else "")
            + (f" scan_seconds={self._selection_scan_seconds_last:.1f}"
               if self._selection_scan_seconds_last is not None else ""),
            flush=True,
        )
        await self.notifier.send_payload_async(payload, critical=critical)
        await self._alarm_if_quoting_nothing(
            now, resting=resting_markets, reasons=idle_reasons)

    async def _alarm_if_quoting_nothing(
        self, now: datetime, *, resting: int, reasons: tuple[tuple[str, int], ...],
    ) -> None:
        """Say something when the process is healthy and earning nothing.

        Every other guard in this file targets a failure already seen. This one
        targets the shape they all shared: the process up, the log ticking, the
        supervisor content, and not one order resting. Both 2026-08-16 outages
        looked exactly like that -- 36 minutes wedged on a stale absence, then
        79 minutes down after a crash -- and both were found by an operator
        happening to look, not by anything the bot said.

        Reward accrues only for resting time, so "up but resting nothing" is
        the symptom that matters regardless of which defect caused it, which is
        why this alarms on the symptom and merely reports the cause.

        Zero resting is not automatically a fault -- the economic gate declining
        every market is a legitimate quiet, and so is a slate between kickoffs.
        The blocker breakdown is what separates them, so it is carried in the
        message rather than judged here: `target_not_met` reads very
        differently from `execution_state_unknown` on twenty markets.

        Edge-triggered. It fires once when the quiet passes the threshold and
        once more when quoting resumes, never on every summary -- an alert that
        repeats every five minutes is one that gets muted, and a muted alert is
        worse than none.
        """

        if not self.active_slugs:
            return
        if resting > 0:
            if self._quiet_since is not None:
                if self._quiet_alarm_sent:
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot is quoting again: {resting} of "
                        f"{len(self.active_slugs)} markets resting.")
                self._quiet_since = None
                self._quiet_alarm_sent = False
            return
        if self._quiet_since is None:
            self._quiet_since = now
            return
        quiet_for = (now - self._quiet_since).total_seconds()
        if self._quiet_alarm_sent or quiet_for < self.QUIET_ALARM_SECONDS:
            return
        self._quiet_alarm_sent = True
        detail = ", ".join(f"{name}={count}" for name, count in reasons) or "none"
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot has rested NO orders for {quiet_for / 60:.0f} "
            f"minutes across {len(self.active_slugs)} bound markets. Reward "
            f"accrues only while resting. Idle reasons: {detail}.",
            critical=True)

    async def _report_runtime_error(
        self, component: str, exc: Exception, attempt: int | None = None,
    ) -> None:
        if attempt is None:
            attempt = self._runtime_error_counts.get(component, 0) + 1
        self._runtime_error_counts[component] = max(
            attempt, self._runtime_error_counts.get(component, 0))
        now = self._now()
        detail = str(exc).replace("\n", " ")
        for name in ("key_id", "secret_key"):
            secret = str(getattr(self.client, name, "") or "")
            if secret:
                detail = detail.replace(secret, "<redacted>")
        print(
            f"[runtime-recovery] at={now.isoformat()} component={component} "
            f"attempt={attempt} error={type(exc).__name__}: {detail}",
            flush=True,
        )
        if exc.__traceback__ is not None:
            traceback.print_tb(exc.__traceback__)
        # Keep Discord useful during a long outage while every occurrence is
        # still retained in commissioning.log.
        # Severity, not volume, is what made Discord unreadable: every runtime
        # recovery was sent critical. Measured 2026-08-14: 1,517 of these in
        # one hour, all transient retries -- a stale book, an incentive fetch
        # that failed, a recovery that will run again in a second. None asked
        # the operator to do anything, and burying the ones that do is how a
        # real alert gets missed.
        #
        # Critical is reserved for what needs a human: a terminal condition, a
        # market retired or abandoned, an ownership violation, a loss stop, or
        # a runner failure. Those are raised by their own call sites.
        routine = not (component.endswith("_terminal")
                       or component.startswith("unowned")
                       or "ownership" in component)
        # Volume, separately from severity. A routine recovery that clears on
        # its first or second try never needed a human -- and `book_stale` at
        # attempt 1 is the *normal* startup state, fired once by every market
        # before its first book arrives, so twenty markets meant twenty
        # messages announcing that the bot was working. Every occurrence is
        # still in commissioning.log.
        #
        # Routine conditions therefore announce only once they have failed
        # repeatedly -- attempt 4, then 8, 16, 32 -- which is a real outage
        # rather than a retry. Non-routine keeps its early warning.
        if routine:
            should_alert = attempt >= 4 and (attempt & (attempt - 1)) == 0
        else:
            should_alert = attempt <= 2 or (attempt & (attempt - 1)) == 0
        if should_alert:
            slug = component.split(":", 1)[1] if ":" in component else None
            await self.notifier.send_payload_async(
                build_runtime_alert(
                    component=component.split(":", 1)[0],
                    error=f"{type(exc).__name__}: {detail[:300]}",
                    attempt=attempt,
                    critical=not routine,
                    market=slug,
                    host=self._host_name,
                    commit=self._deployed_commit),
                critical=not routine,
                # Operational, not a report. The summary webhook is for
                # reporting; recoveries and failures go to the errors webhook
                # whether or not they are critical.
                channel="errors",
            )

    async def _quarantine_working_orders(
        self, slug: str | None = None, *, require_terminal: bool = True,
    ) -> None:
        """Cancel known quotes, optionally requiring terminal proof to return.

        ``slug`` limits the quarantine to one market. A per-market fault --
        this market's execution state is unknown, this market's action failed
        -- says nothing about the other seven, whose quotes are resting
        legitimately and earning. Cancelling them destroys the accrual that
        reward pays for and buys no safety. Observed 2026-08-15T01:42Z: one
        market reporting `execution_state_unknown` cancelled every other
        market's quote within seconds of placement, on repeat.

        The global form (``slug=None``) is still correct for faults that really
        are account-wide: a stream reconnect, and rotation, which is about to
        rebind every market anyway.
        """

        # Name the caller. This is one of only two paths that cancel an order,
        # the planner never asked for a cancel in the live logs, and the live
        # ratio was PLACE 2637 against CANCEL 2636 -- so something reaches here
        # constantly and the logs could not say what.
        import traceback as _tb
        caller = "?"
        for frame in reversed(_tb.extract_stack()[:-1]):
            if frame.name not in {"_quarantine_working_orders"}:
                caller = f"{frame.name}:{frame.lineno}"
                break
        print(
            f"[quarantine] at={self._now().isoformat()} called_by={caller}",
            flush=True,
        )
        now = self._now()
        # A single-market repair must not wait on an unreadable order owned by
        # another market. Private-stream repair uses the empty set: reconnect
        # account visibility, then let each ambiguous market's normal gate
        # contain it. The global terminal form remains strict for every order.
        terminal_order_markets = (
            None if slug is None and require_terminal
            else frozenset({slug}) if require_terminal and slug is not None
            else frozenset()
        )
        # The first pass is observational: tolerate only positively-attributed
        # read failures long enough to apply the complete account snapshots.
        # Positive OPEN evidence can then license a cancel even when GET-by-id
        # is lagging. The second pass below enforces the requested terminal
        # postcondition.
        await self._rest_reconcile(
            now, strict_order_markets=frozenset())
        self._assert_runtime_scope()
        # Retire quotes in every market that could hold them, not just the
        # primary. Resuming with one market's order still working would let it
        # fill unobserved while the others rebind.
        # Nothing bound and nothing in the ledger means nothing to retire.
        slugs = (slug,) if slug is not None else self._shutdown_slugs()
        for market in slugs:
            if self.ledger.market_has_unresolved_intent(market):
                # The intent and UNKNOWN_ABSENT row identify exactly which
                # market is ambiguous. After a fresh private bind, the normal
                # per-market gate keeps this owner dormant while other markets
                # resume. Keep walking so one ambiguous owner cannot prevent
                # cancellation of positively-known orders in later markets.
                # Reissuing a cancel without positive OPEN evidence is
                # forbidden; see `_retry_uncertain_cancels`.
                continue
            if self.ledger.working_orders(market):
                await self.kill_switch.initiate(market, now=self._now())
        await self._rest_reconcile(
            self._now(), strict_order_markets=terminal_order_markets)
        self._assert_runtime_scope()
        if not require_terminal:
            return
        for market in slugs:
            if self.ledger.market_has_unresolved_intent(market):
                raise AdapterError(
                    f"runtime recovery cancel is not yet reconciled in {market}")
            if self.ledger.working_orders(market):
                raise AdapterError(
                    f"runtime recovery order is still working in {market}")

    async def _market_has_settled(self, slug: str) -> bool:
        """Has the venue RESOLVED this market? Proof, not inference.

        The only thing that licenses resolving an absent order, so it must be
        affirmative and it must fail closed. A market that is merely closed,
        missing, unparseable, or unreachable returns False and the order stays
        absent -- the safe direction, because a stuck row costs quoting while a
        wrongly-resolved one costs a position we think we do not hold.
        """

        try:
            raw = await self.gateway._call(
                f"GET /v1/markets/{slug}",
                lambda: self.gateway.client.markets.retrieve_by_slug(slug))
        except Exception:
            return False
        market = raw.get("market", raw) if isinstance(raw, dict) else None
        if not isinstance(market, dict):
            return False
        return str(market.get("status")) == "MARKET_STATUS_RESOLVED"

    def _rotation_blockers(self) -> tuple[str, ...]:
        """Require whole-account flat, owned, resolved state before rebinding."""

        blockers: list[str] = []
        if self.ledger.working_orders():
            blockers.append("working orders remain")
        if self.ledger.unresolved_intents():
            blockers.append("order intents remain unresolved")
        if self._has_open_position():
            blockers.append("a nonzero position remains")
        if not self.ledger.risk_state_known():
            blockers.append("whole-account execution state is not known")
        return tuple(blockers)

    async def _open_streams_for_active_market(
        self, *, strict_order_markets: frozenset[str] | None = None,
    ) -> None:
        """Bind fresh public/private streams and re-prove account state.

        Normal active-set changes remain account-wide strict. Recovery may
        pass an empty set after it has already requested cancellation for all
        known quotes: fresh account snapshots then restore the session while
        each unresolved order's existing per-market gate contains its owner.
        """

        # Close what we are about to drop. `_rotate_reward_market_inner` closes
        # the old pair itself, but the top-up path did not, so every top-up
        # leaked a private and a market socket -- four top-ups in the
        # 2026-08-16 session alone, on an account whose private feed is already
        # split across several connections to stay under the per-connection
        # subscription cap. Doing it here covers every caller. Best-effort: a
        # socket we are discarding must never be able to fail the bind that
        # replaces it, and closing an already-closed stream is a no-op.
        for stale in (self.private_stream, self.market_stream):
            if stale is None:
                continue
            try:
                await stale.close()
            except Exception:
                traceback.print_exc()
        private = self.private_stream_factory(self.client)
        market = self.market_stream_factory(self.client)
        self.private_stream = private
        self.market_stream = market
        try:
            await private.start(list(self.active_slugs))
            self.gate.on_connect()
            await market.start(list(self.active_slugs))
            await self._rest_reconcile(
                self._now(), strict_order_markets=strict_order_markets)
            await self._wait_for_initial_private_state()
        except Exception:
            self.gate.on_disconnect()
            await private.close()
            await market.close()
            # Both attributes now point at closed streams, and the gate reads
            # disconnected. Neither `_recover_private_stream` nor
            # `_recover_market_stream` will fire on their own: those trigger
            # when a `next_*` task RAISES, and reading a stream we closed
            # deliberately never produced that exception -- the 2026-08-16
            # session logged no `component=private_stream` line at all while
            # stuck. The run loop must be told explicitly.
            self._streams_need_rebind = True
            raise
        self._streams_need_rebind = False

    def _reset_selected_market_observations(self, now: datetime) -> None:
        self._accrue_score(now)
        self._score_observed_at = now
        for state in self._markets.values():
            state.clear_economics()
        self._earnings_observed_at = None
        self._earnings_by_status = {}

    def _discovery_is_free(self) -> bool:
        """True when a universe scan cannot cost any reward accrual.

        Rotation cancels resting quotes and stops draining both streams for the
        whole scan, so it is only free while nothing is resting and no side here
        is authorized to rest. Reward is a time integral over resting orders,
        and maker-lab measured 24.2% of its clock scoring zero purely because
        nothing was on the book, so resting time is the expensive resource.

        Scanning while a viable market is being quoted would trade measured
        score-seconds for an unmeasured chance of finding a better market. The
        selection interval still bounds how often a scan may run; this only
        defers it to a moment when it costs nothing. A market that stops being
        executable already forces the interval due, so a dead market is
        replaced as soon as its quotes are retired rather than at the next tick.
        """

        # Any market still authorized to quote, or still resting an order,
        # makes a scan expensive. Checking only the primary would let seven
        # earning markets be cancelled to re-scan on behalf of one idle one.
        for slug in self.active_slugs:
            if self.state_for(slug).expected_net_by_side:
                return False
            if self.ledger.working_orders(slug):
                return False
        return True

    def _expired_and_flat(self, slug: str, now: datetime) -> bool:
        """True when a market can never quote again and holds nothing.

        A market whose game has started is finished: the selector drops it from
        the opportunity set, so it can never be quoted again, but the runner
        kept servicing it -- one book refresh every ~70 seconds, forever.

        Flatness is required, and it is the whole reason this is separate from
        the rotation blockers. A started market still HOLDING inventory must
        stay in the active set, because that is the exit path; releasing it
        would strand the position with nothing working its exit.
        """

        if not self._retired_past_kickoff(slug, now):
            return False
        return (self.ledger.position(slug) == 0
                and not self.ledger.working_orders(slug)
                and not any(str(row["market_slug"]) == slug
                            for row in self.ledger.unresolved_intents()))

    def _reward_ended_and_flat(self, slug: str, now: datetime) -> bool:
        """True only after fresh metadata proves this slot's program ended.

        Missing reward rows are not proof: a partial/transient incentive
        response must make the market idle, never release its ownership scope.
        `_refresh_contracts` records confirmation only when a later complete
        response contains the exact program that previously authorized entry
        and its explicit `ends_at` has elapsed.
        """

        state = self._markets.get(slug)
        if (state is None
                or state.reward_period_end_confirmed_at is None
                or state.reward_period_ended_at is None
                or state.reward_period_ended_at > now):
            return False
        market = state.market
        if (market is None or market.game_start is None
                or market.game_start <= now):
            # Past kickoff belongs to `_expired_and_flat`, whose separate
            # lifecycle rule is already covered and logged.
            return False
        return (self.ledger.position(slug) == 0
                and not self.ledger.working_orders(slug)
                and not any(str(row["market_slug"]) == slug
                            for row in self.ledger.unresolved_intents()))

    def _position_near_its_flatten_deadline(self, now: datetime) -> bool:
        """True when held inventory is close enough to kickoff that a scan hurts.

        The top-up runs in exactly the state full rotation refuses to run in --
        `_safe_to_scan()` False, which includes holding a position. The
        rotation blockers are not only about quarantining orders: they also
        keep a long blocking scan away from live inventory. A selection scan
        measured 25-26 seconds, and for its duration `_decide_for_market` runs
        for no market, so `_inventory_plan` cannot issue the pre-kickoff
        flatten. Spending any of the 300-second window that way risks the one
        outcome the whole exit design exists to prevent: settlement.

        Unknown game time counts as near. A scan is deferrable; a missed
        flatten is not.
        """

        margin = self.reconciler.policy.force_exit_before_start_seconds * 2
        for slug in self.active_slugs:
            if self.ledger.position(slug) == 0:
                continue
            state = self._markets.get(slug)
            start = getattr(getattr(state, "market", None), "game_start", None)
            if start is None:
                return True
            if (start - now).total_seconds() <= margin:
                return True
        return False

    def _active_set_needs_top_up(self, now: datetime) -> bool:
        """True when safe, incremental discovery has useful work to do.

        A slot can be free for two different reasons: a bound market reached
        kickoff and became releasable, or startup validation rejected a
        selected market before it was ever bound.  The second case used to be
        invisible to the scheduler, leaving (for example) 18 active markets
        despite a fresh scan proving that far more than 20 were viable.
        """

        return (
            len(self.active_slugs) < self.config.max_markets
            or any(self._expired_and_flat(slug, now)
                   for slug in self.active_slugs)
            or any(self._reward_ended_and_flat(slug, now)
                   for slug in self.active_slugs)
        )

    def _initial_market_selection_due(self, now: datetime) -> datetime:
        """Schedule an immediate refill when startup bound fewer than 20."""

        if not self.config.auto_select_reward_markets:
            return datetime.max.replace(tzinfo=timezone.utc)
        if self._active_set_needs_top_up(now):
            return now
        return now + timedelta(
            seconds=self.config.market_selection_refresh_seconds)

    def _incremental_top_up_due(
        self, now: datetime, next_selection: datetime,
    ) -> bool:
        """Whether the run loop should perform an incremental discovery scan."""

        return (
            self.config.auto_select_reward_markets
            and (now >= next_selection or self._reward_end_top_up_requested)
            and (self._reward_end_top_up_requested
                 or not self._safe_to_scan())
            and not self._position_near_its_flatten_deadline(now)
            and self._active_set_needs_top_up(now)
        )

    async def _top_up_active_markets(self, now: datetime) -> bool:
        """Fill free slots without disturbing markets that are still resting.

        Full rotation requires a whole-account flat state (`_rotation_blockers`,
        `_discovery_is_free`) because it quarantines every resting order and
        rebinds from scratch. With twenty markets something is almost always
        resting, so that condition is essentially never met: measured
        2026-08-16, rotation ran exactly twice, both at startup, and the active
        set stayed frozen while markets kicked off one by one. Coverage decayed
        19 -> 11 -> 8 -> 7 -> 6 across the previous session for this reason as
        much as for the retirement ban.

        This path is deliberately narrower. It releases ONLY markets that are
        past kickoff and flat, or whose exact reward program is positively
        confirmed ended and which are flat with no order ownership left. It
        keeps every other market and its resting orders exactly where they
        are, fills any capacity startup validation left free, and never
        quarantines. Streams are restarted because they cannot subscribe
        incrementally, but a stream restart does not touch resting orders --
        those live on the venue and keep earning through it. That is the whole
        difference from full rotation, which cancels them.
        """

        if not self.config.auto_select_reward_markets:
            return False
        if self.reward_selector is None:
            return False
        if self._position_near_its_flatten_deadline(now):
            return False
        expired = [slug for slug in self.active_slugs
                   if self._expired_and_flat(slug, now)]
        reward_ended = [
            slug for slug in self.active_slugs
            if slug not in expired and self._reward_ended_and_flat(slug, now)]
        released = expired + reward_ended
        keep = tuple(slug for slug in self.active_slugs
                     if slug not in released)
        free = max(0, self.config.max_markets - len(keep))
        if not released and free == 0:
            return False
        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=self._now())
        finally:
            self._selection_scan_seconds_last = time.monotonic() - scan_started
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        exclude = self._barred_slugs() | set(keep) | set(released)
        replacements = tuple(
            item.market.slug
            for item in selection.top_viable(self.config.max_markets,
                                             exclude=exclude))[:free]
        print(
            f"[top-up] at={self._now().isoformat()} "
            f"released={len(released)} kept={len(keep)} "
            f"added={len(replacements)} free_slots={free} "
            f"expired={len(expired)} reward_ended={len(reward_ended)} "
            f"released_slugs={','.join(sorted(released))}",
            flush=True,
        )
        if not replacements and not keep:
            # Releasing everything to hold nothing is never an improvement on
            # holding dead markets. `_bind_markets(())` leaves `market_slug`
            # raising RuntimeError, and the next caller that omits a slug --
            # `_refresh_book(now)` among them -- takes the run loop down.
            print(
                f"[top-up] at={self._now().isoformat()} skipped=nothing_retained "
                f"expired={len(expired)} reward_ended={len(reward_ended)}",
                flush=True,
            )
            return False
        if not replacements and not released:
            # Capacity is free, but this scan found nothing safe to add. Keep
            # the current subscriptions intact and retry only on the normal
            # bounded selection cadence.
            return False
        if not replacements:
            # Still worth releasing: a dead market costs a REST refresh every
            # service cycle and reports `book_stale` every time.
            self._bind_markets(keep)
        else:
            self._bind_markets(keep + replacements)
        await self._open_streams_for_active_market()
        return True

    async def _rotate_reward_market(self, now: datetime) -> bool:
        transition_started = time.monotonic()
        try:
            return await self._rotate_reward_market_inner(now)
        finally:
            self._rotation_transition_seconds_last = (
                time.monotonic() - transition_started)

    async def _rotate_reward_market_inner(self, now: datetime) -> bool:
        """Select from executable rewards and switch only from proven-flat state.

        The caller must cancel its outstanding ``next_*`` tasks first.  The
        underlying current streams may remain connected while discovery reads
        the shared, rate-governed REST API because all current maker orders
        have already been quarantined.
        """

        if not self.config.auto_select_reward_markets:
            return False
        await self._quarantine_working_orders()
        await self._rest_reconcile(self._now())
        blockers = self._rotation_blockers()
        if blockers:
            raise AdapterError(
                "reward-market rotation blocked: " + "; ".join(blockers))
        if self.reward_selector is None:
            raise RuntimeError("automatic reward selector is unavailable")
        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=self._now())
        finally:
            self._selection_scan_seconds_last = (
                time.monotonic() - scan_started)
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        if selection.selected is None:
            # A zero-viable scan is a normal idle result, never permission to
            # bind a different monitoring-only market. Keep the proven stream
            # scope and refresh it after the potentially long discovery pass.
            #
            # The bound market can expire while the scan runs, most commonly by
            # reaching its own start. That is still an idle result rather than a
            # discovery failure, so it must not escape into the caller's fast
            # error-retry cadence: doing so re-scans the whole universe every 60
            # seconds and starves the shared REST budget that book and account
            # freshness depend on. Clear the incentive so no side can quote, and
            # stay dormant on the normal selection interval instead.
            try:
                await self._refresh_contracts(self._now())
                await self._refresh_book(self._now())
                self._runtime_error_counts.pop("idle_market_refresh", None)
            except Exception as exc:
                self.incentive = None
                self._reward_eligible = frozenset()
                await self._report_runtime_error("idle_market_refresh", exc)
            await self._rest_reconcile(self._now())
            return False
        # Markets retired earlier this session are never re-entered. The
        # exclusion happens inside top_viable so a barred market cannot take a
        # slot that a quotable one could have used.
        chosen = tuple(
            item.market.slug
            for item in selection.top_viable(
                self.config.max_markets, exclude=self._barred_slugs()))
        previous = tuple(self.active_slugs)
        if chosen == previous:
            for slug in previous:
                await self._refresh_contracts(self._now(), slug)
                await self._refresh_book(self._now(), slug)
            await self._rest_reconcile(self._now())
            return False

        old_private = self.private_stream
        old_market = self.market_stream
        await old_private.close()
        await old_market.close()
        self.gate.on_disconnect()
        # Per-slot replacement: _bind_markets discards only the entries being
        # dropped, so a retained market keeps its identity rather than being
        # torn down because a different slot changed. Its *contracts* are still
        # forgotten below -- both streams were just closed, so every book is
        # potentially stale and must be re-proven before it can authorize a
        # quote. Accrual is unaffected either way: score-seconds live in
        # _score_seconds_by_market, not in MarketState.
        self._bind_markets(chosen)
        for slug in chosen:
            self.state_for(slug).forget_contracts()
        self._reset_selected_market_observations(self._now())
        try:
            for slug in chosen:
                await self._refresh_contracts(self._now(), slug)
                await self._refresh_book(self._now(), slug)
            await self._open_streams_for_active_market()
            await self._refresh_earnings(self._now())
        except Exception as selection_error:
            # No mutation occurred after the flat proof. Restore the prior
            # scope so a transient discovery/bind failure leaves the process
            # safely monitoring instead of silently streamless.
            self._bind_markets(previous)
            for slug in previous:
                self.state_for(slug).forget_contracts()
            try:
                for slug in previous:
                    await self._refresh_contracts(self._now(), slug)
                    await self._refresh_book(self._now(), slug)
                await self._open_streams_for_active_market()
                await self._refresh_earnings(self._now())
            except Exception as restore_error:
                raise RuntimeInvariantViolation(
                    "market_rotation_restore_failed",
                    "automatic market rotation could not restore a safe "
                    "stream scope",
                ) from restore_error
            raise AdapterError(
                "selected reward markets could not be safely bound: "
                f"{type(selection_error).__name__}") from selection_error
        await self.notifier.send_async(
            "🔄 Liquidity Bot rotated while flat from "
            f"[{', '.join(previous) or 'none'}] to "
            f"[{', '.join(chosen)}]. "
            f"Joined {selection.catalog_markets} catalog markets to "
            f"{selection.rewarded_executable_markets} executable reward programs, "
            f"evaluated {selection.books_evaluated} books, and "
            f"found {selection.viable_markets} economically viable markets; "
            f"selection scan {self._selection_scan_seconds_last:.1f}s; "
            f"{selection.unsupported_programs} unsupported lifecycle programs "
            "remained read-only.")
        return True

    async def _recover_private_stream(self, initial: Exception) -> str | None:
        """Stay alive but dormant until private state is proven again."""

        exc = initial
        attempt = 0
        venue_failure_streak = 0
        self.gate.on_disconnect()
        while True:
            attempt += 1
            await self._report_runtime_error("private_stream", exc, attempt)
            try:
                try:
                    await self.private_stream.close()
                except Exception:
                    traceback.print_exc()
                try:
                    await self._refresh_balance(self._now())
                except Exception as balance_exc:
                    print(
                        "[runtime-recovery] balance refresh failed: "
                        f"{type(balance_exc).__name__}: {balance_exc}",
                        flush=True,
                    )
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                # A private disconnect is account-wide, so first request
                # cancellation for every known quote. Cancellation proof can
                # lag for minutes: a complete snapshot may omit the order while
                # GET-by-id still reports it active. That ambiguity belongs to
                # the order's market. Reconnect the account stream and let the
                # existing per-market gate keep only those owners dormant;
                # requiring every cancel terminal here held all twenty markets
                # dark for 20+ minutes on 2026-08-23.
                await self._quarantine_working_orders(require_terminal=False)
                stream = self.private_stream_factory(self.client)
                self.private_stream = stream
                await stream.start(list(self.active_slugs))
                self.gate.on_connect()
                await self._rest_reconcile(
                    self._now(), strict_order_markets=frozenset())
                await self._wait_for_initial_private_state()
                ambiguous_markets = sorted({
                    str(row["market_slug"])
                    for row in (
                        *self.ledger.unresolved_intents(),
                        *self.ledger.absent_orders(),
                    )
                    if str(row["market_slug"] or "").strip()
                })
                if ambiguous_markets:
                    print(
                        f"[private-recovery] at={self._now().isoformat()} "
                        f"resumed=yes scoped_ambiguous={len(ambiguous_markets)} "
                        f"markets={','.join(ambiguous_markets)}",
                        flush=True,
                    )
                await self.notifier.send_async(
                    "✅ Liquidity Bot private stream recovered; REST and fresh "
                    "private state passed. "
                    + (
                        f"Quoting may resume on unaffected markets; "
                        f"{len(ambiguous_markets)} market(s) remain scoped "
                        "until terminal order proof."
                        if ambiguous_markets else "Quoting may resume."
                    ))
                self._runtime_error_counts.pop("private_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "private_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                self.gate.on_disconnect()
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_venue_unavailable(recovery_exc) else 0)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                if attempt >= MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS:
                    if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                        stop = await self._wait_for_venue_recovery(
                            "private_stream", recovery_exc)
                        if stop:
                            return stop
                        attempt = 0
                        venue_failure_streak = 0
                        continue
                    exhausted = AdapterError(
                        "private stream recovery exhausted "
                        f"{MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS} attempts: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}")
                    await self._report_runtime_error(
                        "private_stream_terminal", exhausted, attempt)
                    # `main` maps runner_failure to restart only after shutdown
                    # proves the whole account flat. Unresolved/absent orders
                    # make `result.flat` false and therefore exit 3 instead.
                    return "runner_failure:private_stream_recovery_exhausted"
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    async def _recover_market_stream(self, initial: Exception) -> str | None:
        """Reconnect market data after canceling quotes made on the old book."""

        exc = initial
        attempt = 0
        venue_failure_streak = 0
        while True:
            attempt += 1
            await self._report_runtime_error("market_stream", exc, attempt)
            try:
                try:
                    await self.market_stream.close()
                except Exception:
                    traceback.print_exc()
                try:
                    await self._refresh_balance(self._now())
                except Exception as balance_exc:
                    print(
                        "[runtime-recovery] balance refresh failed: "
                        f"{type(balance_exc).__name__}: {balance_exc}",
                        flush=True,
                    )
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self._quarantine_working_orders()
                stream = self.market_stream_factory(self.client)
                self.market_stream = stream
                await stream.start(list(self.active_slugs))
                self.book = await stream.next_book(timeout=15)
                self.book_observed_at = self._now()
                await self.notifier.send_async(
                    "✅ Liquidity Bot market stream recovered with a fresh full book. "
                    "Quoting may resume.")
                self._runtime_error_counts.pop("market_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "market_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_venue_unavailable(recovery_exc) else 0)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                if attempt >= MAX_MARKET_STREAM_RECOVERY_ATTEMPTS:
                    if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                        stop = await self._wait_for_venue_recovery(
                            "market_stream", recovery_exc)
                        if stop:
                            return stop
                        attempt = 0
                        venue_failure_streak = 0
                        continue
                    exhausted = AdapterError(
                        "market stream recovery exhausted "
                        f"{MAX_MARKET_STREAM_RECOVERY_ATTEMPTS} attempts: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}")
                    await self._report_runtime_error(
                        "market_stream_terminal", exhausted, attempt)
                    return "runner_failure:market_stream_recovery_exhausted"
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    async def _recover_bound_streams(self, initial: Exception) -> str | None:
        """Rebind both streams after a failed bind left them closed.

        `_recover_private_stream` and `_recover_market_stream` each repair one
        side and assume the other is healthy. A failed
        `_open_streams_for_active_market` closes BOTH, so neither is usable and
        the whole bind has to be redone -- which is exactly what that method
        does, so this retries it rather than reimplementing the sequence.

        Quarantines first, like both sibling recoveries. A top-up deliberately
        leaves resting orders alone, so cancelling here gives up the reward
        they were still accruing -- but the private stream is down, which means
        a fill can land unseen, and quoting blind is the one thing the gate
        exists to prevent. Reward lost on a rare bind failure is cheaper than
        an unobserved position.
        """

        exc = initial
        attempt = 0
        venue_failure_streak = 0
        await self.notifier.send_async(
            "⚠️ Liquidity Bot lost both streams while rebinding the active "
            "set. Quoting is stopped and no market can report state until "
            "they are back. Retrying.")
        while True:
            attempt += 1
            await self._report_runtime_error("bound_streams", exc, attempt)
            try:
                try:
                    await self._refresh_balance(self._now())
                except Exception as balance_exc:
                    print(
                        "[runtime-recovery] balance refresh failed: "
                        f"{type(balance_exc).__name__}: {balance_exc}",
                        flush=True,
                    )
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                # Terminal proof can lag for one cancelled quote even after a
                # complete snapshot stops listing it. That ambiguity is owned
                # by one market; requiring it here held every market dormant
                # after the 2026-08-24 failed top-up bind. Request every known
                # cancel, then restore both streams with non-strict order reads.
                # The fresh private-state gate still blocks each ambiguous
                # owner, while unaffected markets become safe to quote again.
                await self._quarantine_working_orders(require_terminal=False)
                await self._open_streams_for_active_market(
                    strict_order_markets=frozenset())
                ambiguous_markets = sorted({
                    str(row["market_slug"])
                    for row in (
                        *self.ledger.unresolved_intents(),
                        *self.ledger.absent_orders(),
                    )
                    if str(row["market_slug"] or "").strip()
                })
                if ambiguous_markets:
                    print(
                        f"[bound-stream-recovery] at={self._now().isoformat()} "
                        f"resumed=yes scoped_ambiguous={len(ambiguous_markets)} "
                        f"markets={','.join(ambiguous_markets)}",
                        flush=True,
                    )
                await self.notifier.send_async(
                    "✅ Liquidity Bot rebound both streams; REST and fresh "
                    "private state passed. "
                    + (
                        "Quoting may resume on unaffected markets; "
                        f"{len(ambiguous_markets)} market(s) remain scoped "
                        "until terminal order proof."
                        if ambiguous_markets else "Quoting may resume."
                    ))
                self._runtime_error_counts.pop("bound_streams", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "bound_streams_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_venue_unavailable(recovery_exc) else 0)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                if attempt >= MAX_BOUND_STREAM_RECOVERY_ATTEMPTS:
                    if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                        stop = await self._wait_for_venue_recovery(
                            "bound_streams", recovery_exc)
                        if stop:
                            return stop
                        attempt = 0
                        venue_failure_streak = 0
                        continue
                    exhausted = AdapterError(
                        "bound stream recovery exhausted "
                        f"{MAX_BOUND_STREAM_RECOVERY_ATTEMPTS} attempts: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}")
                    await self._report_runtime_error(
                        "bound_streams_terminal", exhausted, attempt)
                    # Shutdown determines restartability only after proving the
                    # whole account flat. This cap never converts ambiguity or
                    # inventory into a restartable result.
                    return "runner_failure:bound_stream_recovery_exhausted"
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    @staticmethod
    def _is_venue_unavailable(exc: Exception) -> bool:
        """Recognize transport/server failure without swallowing code defects.

        The SDK exception is wrapped by ``AsyncReadOnlyGateway`` but retains
        its cause.  Inspect both.  HTTP 4xx, NotFound consistency lag and
        response-shape validation are deliberately absent: those need their
        existing scoped or terminal handling, not an indefinite maintenance
        label.
        """

        current: BaseException | None = exc
        seen: set[int] = set()
        texts: list[str] = []
        names: list[str] = []
        statuses: list[int] = []
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            texts.append(str(current).lower())
            names.append(type(current).__name__.lower())
            raw_status = getattr(current, "status_code", None)
            if raw_status is None:
                raw_status = getattr(
                    getattr(current, "response", None), "status_code", None)
            try:
                if raw_status is not None:
                    statuses.append(int(raw_status))
            except (TypeError, ValueError):
                pass
            current = current.__cause__ or current.__context__

        combined = " ".join((*names, *texts))
        if any(token in combined for token in (
                "notfound", "not found", "rate limit", "status 429")):
            return False
        if any(500 <= status <= 599 for status in statuses):
            return True
        return any(token in combined for token in (
            "internalservererror", "internal server error",
            "serviceunavailable", "service unavailable",
            "badgateway", "bad gateway", "gatewaytimeout", "gateway timeout",
            "temporarily unavailable", "under maintenance", "maintenance",
            "connecterror", "connectionerror", "connection error",
            "connection reset", "connection refused", "name resolution",
            "gaierror", "readtimeout", "connecttimeout", "timed out",
            "timeouterror", "request timeout", "websocket disconnected",
        ))

    def _maintenance_stop_reason(self) -> str | None:
        """Read stop state safely even if preflight has no risk guard yet."""

        if self._stop_requested:
            return self._stop_requested
        if self.risk_guard is None:
            return None
        return self._stop_reason(self._now())

    async def _maintenance_sleep(self, seconds: float) -> str | None:
        """Sleep in short pieces so SIGTERM is prompt during a long pause."""

        remaining = max(0.0, float(seconds))
        while remaining > 0:
            stop = self._maintenance_stop_reason()
            if stop:
                return stop
            step = min(5.0, remaining)
            await self.sleep(step)
            remaining -= step
        return self._maintenance_stop_reason()

    async def _wait_for_venue_recovery(
        self, component: str, initial: Exception,
    ) -> str | None:
        """Hold dormant until authenticated venue reads are stably healthy.

        Probes are intentionally read-only and are not applied as ledger
        snapshots.  In particular, an order missing from these observations is
        never interpreted as cancelled.  The component-specific recovery then
        performs the normal quarantine, reconciliation and fresh-stream gates
        before the run loop can quote again.
        """

        print(
            f"[maintenance-pause] at={self._now().isoformat()} "
            f"component={component} state=entered "
            f"reason={type(initial).__name__}:{initial}",
            flush=True,
        )
        await self.notifier.send_async(
            "🛠️ Liquidity Bot detected sustained Polymarket service "
            f"unavailability in {component}. New quoting is paused; no "
            "order absence is treated as cancellation. Read-only account "
            "health must pass three times over about two minutes before the "
            "normal reconciliation and stream gates are retried.",
            critical=True,
        )
        stable = 0
        while stable < MAINTENANCE_STABLE_READS:
            stop = self._maintenance_stop_reason()
            if stop:
                return stop
            try:
                observed_at = self._now()
                orders = await self.account.fetch_open_orders(
                    observed_at=observed_at)
                positions = await self.account.fetch_positions(
                    observed_at=observed_at)
                balance = await self.account.fetch_balance(
                    observed_at=observed_at)
                stable += 1
                print(
                    f"[maintenance-pause] at={self._now().isoformat()} "
                    f"component={component} health=success "
                    f"stable={stable}/{MAINTENANCE_STABLE_READS} "
                    f"open_orders={len(orders)} positions={len(positions)} "
                    f"equity={balance.marked_equity}",
                    flush=True,
                )
            except RuntimeInvariantViolation:
                raise
            except Exception as probe_exc:
                if not self._is_venue_unavailable(probe_exc):
                    await self._report_runtime_error(
                        "maintenance_probe_terminal", probe_exc)
                    raise
                stable = 0
                await self._report_runtime_error(
                    "maintenance_probe", probe_exc)
                print(
                    f"[maintenance-pause] at={self._now().isoformat()} "
                    f"component={component} health=failed stable=0/"
                    f"{MAINTENANCE_STABLE_READS} "
                    f"reason={type(probe_exc).__name__}:{probe_exc}",
                    flush=True,
                )
            if stable < MAINTENANCE_STABLE_READS:
                stop = await self._maintenance_sleep(MAINTENANCE_POLL_SECONDS)
                if stop:
                    return stop
        await self.notifier.send_async(
            "✅ Liquidity Bot maintenance health gate passed three complete "
            "authenticated account reads. Normal stream recovery and exact "
            "order/position reconciliation are resuming; quoting remains "
            "blocked until those existing gates pass.")
        print(
            f"[maintenance-pause] at={self._now().isoformat()} "
            f"component={component} state=health_gate_passed",
            flush=True,
        )
        self._runtime_error_counts.pop("maintenance_probe", None)
        return None

    async def _reconcile_pending_hard_exit(
        self, slug: str | None = None,
    ) -> str:
        """Confirm a submitted IOC without converting latency into shutdown."""

        slug = slug or self.market_slug
        await self._rest_reconcile(self._now())
        if (self.ledger.market_has_unresolved_intent(slug)
                or self.ledger.working_orders(slug)):
            return "pending"
        if self.ledger.position(slug) == 0:
            self.reconciler.confirm_hard_exit_terminal(slug)
            return "flat"
        # Venue state is terminal and exact inventory remains. This is a safe
        # retry boundary, not permission to duplicate an ambiguous mutation.
        self.reconciler.confirm_hard_exit_terminal(slug)
        return "retry"

    async def _recover_strategy_state(self, reason: str, slug: str) -> None:
        """Quarantine an unexpected strategy state without ending the process.

        The refresh must target the market that actually went stale. Refreshing
        the primary would leave a stale book on any other slot untouched, so it
        would report the same condition forever and never quote again.
        """

        await self._report_runtime_error(
            f"strategy_state:{slug}", AdapterError(str(reason or "unknown")))
        try:
            if reason in {"book_stale", "book_market_mismatch",
                          "hard_exit_deferred_thin_book"}:
                await self._refresh_book(self._now(), slug)
                if reason == "book_stale":
                    # Refreshing the book IS the recovery. Quarantining below
                    # cancels every resting quote, and a stale book does not
                    # make an already-resting post-only order unsafe -- it only
                    # means we cannot make a NEW decision until the book
                    # arrives. Measured 2026-08-14: 226 book_stale events in
                    # six minutes against 45 placements, so nearly every quote
                    # was cancelled about two seconds after being placed and
                    # nothing ever rested. Reward is paid for resting.
                    #
                    # New risk is still refused for as long as the book is
                    # stale: the reconciler returns this critical before it can
                    # plan anything, so no quote is placed on stale data.
                    return
            await self._quarantine_working_orders(slug)
        except RuntimeInvariantViolation:
            raise
        except Exception as exc:
            await self._report_runtime_error(
                f"strategy_state_recovery:{slug}", exc)

    @staticmethod
    def _terminal_exception_reason(component: str, exc: Exception) -> str:
        if isinstance(exc, RuntimeInvariantViolation):
            return f"critical:{exc.code}"
        return f"critical:{component}_{type(exc).__name__}"

    def _recovery_delay(self, component: str, *, base: float = 1.0,
                        cap: float = 30.0) -> float:
        attempt = max(1, self._runtime_error_counts.get(component, 1))
        return min(base * (2 ** min(attempt - 1, 5)), cap)

    def _is_retired(self, slug: str) -> bool:
        """True while a retired market's position is still being worked."""

        return slug in self._retired

    def _barred_slugs(self, now: datetime | None = None) -> set[str]:
        """Slugs that must not be quoted or reselected right now.

        Outlives ``_retired`` but no longer outlives the session. A market is
        barred while its exit is still being worked, while its cooldown runs,
        and permanently if it was bound by recovery -- those were loaded to be
        exited, and their games have already started.
        """

        moment = now or self._now()
        return (set(self._retired)
                | set(self._recovery_slugs)
                | {slug for slug, until in self._retired_until.items()
                   if until > moment})

    def _is_barred_from_quoting(self, slug: str) -> bool:
        """True while a retirement is still in force for this market."""

        if slug in self._barred_slugs():
            return True
        # Serviced but not selected: we hold risk here and are working the
        # exit, but selection did not choose it, so it must not build MORE.
        # Without this the managed set would quietly widen what we quote.
        return slug not in set(self.active_slugs)

    async def _retire_market(
        self, slug: str, reason: str, *, reset_exit_budget: bool = True,
    ) -> str | None:
        """Stop quoting one market without stopping the session.

        Every condition routed here is a statement about one market's
        inventory or exit, not about the account. Ending the session for them
        was a single-market habit: it exits 4, which the supervisor never
        restarts, so one thin book on one slot took down seven healthy markets
        and left the process dead until an operator noticed.

        Retirement is strictly less permissive than quoting. The market keeps
        its streams, book refresh and reconciler so the position is still
        worked toward flat, and the pre-kickoff flatten still applies, but no
        side of it may be quoted while the retirement remains in force. The
        exit budget is reset so the retry happens on a slow cadence rather
        than never.

        Returns a session stop reason only when retirement itself is no longer
        a proportionate response -- a whole slate of failures is systemic.
        """

        state = self.state_for(slug)
        state.clear_economics()
        # A fresh budget, not an unlimited one: the attempt and window caps
        # still bound each round, and max_taker_exit_orders still bounds the
        # session's crossing overall.
        #
        # Not always, though. Clearing the budget also clears the sent marker,
        # which is the only evidence that a confirmation is stuck. Reset it
        # there and the condition stops re-firing, the round counter never
        # advances, and the market never reaches the abandonment bound -- the
        # runner retries forever. Callers whose condition is *detected* by that
        # marker must keep it.
        if reset_exit_budget:
            self.reconciler.restart_hard_exit_budget(slug)
        now = self._now()
        retry_at = now + timedelta(seconds=RETIRED_EXIT_RETRY_SECONDS)
        existing = self._retired.get(slug)
        # Already retired for the same permanent fault? Nothing to escalate.
        # The round counter bounds EXIT ATTEMPTS, not how many times a book
        # failed to parse. The book-refresh error counter never resets, so
        # every later failure re-entered here and incremented the round --
        # 1, 2, 4, 8, then abandonment -- for a market that was already retired
        # and quoting nothing. Measured 2026-08-15: 50 markets retired and 30
        # abandoned in one session, past the max_markets limit that stops the
        # session outright.
        if (existing is not None
                and existing.reason == reason
                and str(reason).startswith(self.PERMANENT_RETIREMENT_PREFIXES)):
            return None
        if existing is None:
            self._retired[slug] = RetiredMarket(
                slug=slug, reason=reason, retired_at=now,
                next_retry_at=retry_at)
        else:
            existing.rounds += 1
            existing.reason = reason
            existing.next_retry_at = retry_at
        self._retired_ever.add(slug)
        # Re-retiring pushes the cooldown out from now, so a market that keeps
        # failing keeps being barred; one that recovers comes back.
        self._retired_until[slug] = now + timedelta(
            seconds=RETIREMENT_COOLDOWN_SECONDS)
        rounds = self._retired[slug].rounds
        position = self.ledger.position(slug)
        print(
            f"[market-retired] at={self._now().isoformat()} market={slug} "
            f"reason={reason} round={rounds} position={position}",
            flush=True,
        )
        if rounds >= MAX_RETIREMENT_ROUNDS:
            print(
                f"[market-retired] at={self._now().isoformat()} market={slug} "
                f"abandoned_after={rounds}_rounds position={position}",
                flush=True,
            )
            await self.notifier.send_async(
                f"🚨 Liquidity Bot abandoned {slug} after {rounds} failed exit "
                f"rounds, position {position}. It will not be planned again "
                "this session; shutdown will make a final attempt. Every other "
                "market keeps running.",
                critical=True,
            )
        elif rounds == 1 or (rounds & (rounds - 1)) == 0:
            if reason == "unresolved_mutation_timeout":
                quote_status = (
                    "It remains barred until the intent has positive terminal "
                    "proof and the market is order-free and flat; it then "
                    "resumes automatically."
                )
            else:
                quote_status = (
                    "It remains barred while retired and during its cooldown."
                )
            await self.notifier.send_async(
                f"⚠️ Liquidity Bot retired {slug}: {reason} (round {rounds}, "
                f"position {position}). {quote_status} "
                "The exit keeps being worked and every other market keeps "
                "running.",
                critical=True,
            )
        # One full slate of retirements AT ONCE is not unlucky books, it is a
        # systemic fault. Stop rather than churn every slot.
        #
        # Two things are excluded, because both produce that shape without any
        # fault at all. Markets bound by recovery share the retired set (they
        # must never quote) but were not retired for a fault. And a market
        # whose game has started is retired by LIFECYCLE: its book stops
        # updating, book_stale exhausts its recoveries, and it retires
        # permanently -- correctly, since a live game must not be quoted.
        #
        # Observed 2026-08-16: the session stopped after 7h09m having retired
        # 31 markets, 30 of them `unrecoverable_book_stale` as their games
        # kicked off. It had sent 466 orders and the account was flat. Nothing
        # was wrong; a cumulative counter had simply outlived twenty markets,
        # and a synchronized morning kickoff retired a slate's worth together.
        horizon = now - timedelta(seconds=RETIREMENT_BURST_WINDOW_SECONDS)
        recent_faults = {
            name for name, entry in self._retired.items()
            if name not in self._recovery_slugs
            and entry.retired_at >= horizon
            and not self._retired_past_kickoff(name, now)}
        if len(recent_faults) > self.config.max_markets:
            return "critical:market_retirement_limit_exceeded"
        return None

    def _retired_past_kickoff(self, slug: str, now: datetime) -> bool:
        """Has this market's game already started?

        Such a retirement is the market ending, not the bot failing. Unknown
        game time counts as a fault: the systemic bound is the last guard
        against churning every slot, so it must not be weakened by a gap.
        """

        state = self._markets.get(slug)
        start = getattr(getattr(state, "market", None), "game_start", None)
        return start is not None and start <= now

    # Retirement reasons that being flat does not resolve. A market retired
    # because its book will not parse is still unquotable once flat, so
    # releasing it just lets the same fault retire it again. Observed
    # 2026-08-15 on chevic-scovla: retired, released=flat, retired again, every
    # ~45 seconds.
    PERMANENT_RETIREMENT_PREFIXES = ("unrecoverable_book", "plan_failed",
                                     "action_failed")

    def _book_refresh_would_be_pointless(self, slug: str, now: datetime) -> bool:
        """Is there anything left that a fresh book for this market could serve?

        Invariant 62 keeps a retired market's book refresh alive on purpose, so
        a retirement that still holds inventory can be priced and exited. That
        reasoning does not reach a market with **no position and no working
        order**: there is nothing to exit, and a retired market may never quote
        again, so the fetch can only produce another failure.

        Composed with the churn guard in `_release_retired_if_flat` -- which
        deliberately refuses to release a BOOK-fault retirement on flatness,
        because releasing on flatness alone let the same fault re-retire the
        market every ~45 seconds -- a finished game with an unparseable book
        refreshed forever. Measured 2026-08-20T10:55Z: two settled setkamecz
        markets at attempt=72 and 70, 144 REST calls in ten minutes, about 6%
        of the 4 req/s governor each, accumulating as more games finish.

        This is strictly narrower than that release: it stops the fetch without
        clearing the retirement, so the market stays barred and cannot churn
        back in. Past kickoff is required as well, so a market retired before
        its game -- which may still recover and be released by its cooldown --
        keeps its refresh.
        """

        if slug not in self._retired:
            return False
        if not self._retired_past_kickoff(slug, now):
            return False
        # The exit test, and the reason this stays safe. A retirement holding
        # anything at all keeps its book.
        if self.ledger.position(slug) != 0:
            return False
        return not self.ledger.working_orders(slug)

    def _release_retired_if_flat(self, slug: str) -> None:
        """Free a retired market's slot once the venue proves it flat.

        Book faults still hold the market until their cooldown expires. Being
        flat does not make an unparseable book parseable, so releasing on flat
        alone just let the same fault retire it again about every 45 seconds
        (chevic-scovla, 2026-08-15). Time is what resolves them, not flatness --
        so they wait out the cooldown here instead of being barred forever.
        """

        retired = self._retired.get(slug)
        if retired is None:
            return
        # One clock read. Calling `_now()` on both sides compares two different
        # instants, so the comparison depended on how far the clock advanced
        # between them rather than on the cooldown.
        moment = self._now()
        if (str(retired.reason).startswith(self.PERMANENT_RETIREMENT_PREFIXES)
                and self._retired_until.get(slug, moment) > moment):
            return
        if self.ledger.position(slug) != 0:
            return
        if self.ledger.working_orders(slug):
            return
        if any(str(row["market_slug"]) == slug
               for row in self.ledger.unresolved_intents()):
            return
        self._retired.pop(slug, None)
        # This retirement exists only to contain ambiguous mutation state.
        # Once the venue has positively resolved the intent and the owner is
        # order-free and flat, carrying the generic thirty-minute cooldown
        # adds no safety: all of the condition's evidence is gone. Other
        # retirement reasons keep their cooldown unchanged.
        if retired.reason == "unresolved_mutation_timeout":
            self._retired_until.pop(slug, None)
        print(
            f"[market-retired] at={self._now().isoformat()} market={slug} "
            "released=flat",
            flush=True,
        )

    async def _handle_strategy_critical(
        self, reason: str, slug: str,
    ) -> str | None:
        """Recover only explicitly enumerated transient strategy conditions."""

        if reason in RETIRING_STRATEGY_CRITICALS:
            return await self._retire_market(slug, reason)
        # A "recoverable" condition that never recovers is not recoverable.
        # Measured 2026-08-14: one market reached 371 consecutive book_stale
        # recoveries, each failing on `book is not two-sided` -- 3,416 of those
        # across the session. Thin books here are routinely one-sided (section
        # 5 records bid 23,388 against ask 1.0) and parse_book_response rejects
        # them outright, so the market can never refresh, never quotes, and
        # holds a slot forever while paging Discord on every attempt.
        if (reason in RECOVERABLE_STRATEGY_CRITICALS
                and self._runtime_error_counts.get(
                    f"strategy_state:{slug}", 0) >= MAX_RECOVERY_ATTEMPTS):
            return await self._retire_market(
                slug, f"unrecoverable_{reason}", reset_exit_budget=False)
        if reason not in RECOVERABLE_STRATEGY_CRITICALS:
            exc = RuntimeInvariantViolation(
                reason, f"terminal strategy condition: {reason}")
            await self._report_runtime_error("strategy_state_terminal", exc)
            return f"critical:{reason}"
        try:
            await self._recover_strategy_state(reason, slug)
        except RuntimeInvariantViolation as invariant:
            await self._report_runtime_error(
                "strategy_state_terminal", invariant)
            return f"critical:{invariant.code}"
        return None

    async def _handle_strategy_plan_failure(self, exc: Exception) -> str:
        """A pure planning exception is a code/state invariant, not transport."""

        await self._report_runtime_error("strategy_plan_terminal", exc)
        return self._terminal_exception_reason("strategy_plan", exc)

    async def _handle_action_failure(
        self, exc: Exception, slug: str,
    ) -> str | None:
        """Retry ambiguous remote outcomes; terminate local invariant failures."""

        await self._report_runtime_error(f"action_driver:{slug}", exc)
        if isinstance(exc, RuntimeInvariantViolation):
            # About the process, not this market. Terminal.
            return self._terminal_exception_reason("action_driver", exc)
        if not isinstance(exc, (AdapterError, OrderOutcomeUncertain)):
            # Some other exception driving this market's action. Retire the
            # market and keep the rest quoting rather than ending the session
            # over one market's mutation. Bounded like every retirement.
            return await self._retire_market(
                slug, f"action_failed_{type(exc).__name__}")
        # Losing a race against our own placement is not a failure worth
        # tearing the book down for. The order we just sent is not yet visible
        # as working, so the next pass tries to place again, place() refuses
        # because that market has an unresolved intent, and quarantining in
        # response cancels the very order that caused the refusal. Measured
        # 2026-08-14: PLACE 2597 against CANCEL 2596, each cancel about 1.5
        # seconds after its place, with no planner ever asking for one.
        #
        # Nothing is unsafe here -- the mutation is durably recorded and will
        # reconcile on its own. Waiting is the whole remedy.
        if "unresolved order intent" in str(exc):
            return None
        try:
            await self._quarantine_working_orders(slug)
        except RuntimeInvariantViolation as invariant:
            await self._report_runtime_error(
                "action_driver_terminal", invariant)
            return f"critical:{invariant.code}"
        except Exception as recovery_exc:
            await self._report_runtime_error(
                f"action_driver_recovery:{slug}", recovery_exc)
        return None

    def managed_slugs(self) -> tuple[str, ...]:
        """Every market to SERVICE, which is not the same as what to quote.

        `active_slugs` answers "what did selection choose"; this answers "what
        still holds risk". They were conflated, so a position lost its manager
        the moment selection moved on: the passive reducer, the inventory
        timer and the pre-kickoff flatten all iterate the serviced set, so a
        dropped market was never visited again and never got an exit order.
        Rotation even cancelled the exit that was already resting, leaving the
        worst pairing -- position kept, exit gone.

        Observed 2026-08-15: `aec-wta-kamrak-marsak-2026-08-14` held 5
        contracts from the previous day, through many restarts, with no exit
        order and no attempt to place one. Nothing was refusing it; nothing was
        looking at it.

        A market here that selection did not choose is exit-only: barred from
        quoting by `_is_barred_from_quoting`, while the reconciler still plans
        its exit every pass.
        """

        return self._shutdown_slugs()

    def _shutdown_slugs(self) -> tuple[str, ...]:
        """Every market that could still hold risk, not just the active set.

        A market dropped by rotation can still own a working order, a nonzero
        position or an unresolved intent. Shutdown must retire all of them, so
        this unions the active set with everything the ledger still shows.
        """

        slugs = set(self.active_slugs)
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.working_orders())
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.unresolved_intents())
        slugs.update(
            str(row["market_slug"]) for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0"))
        slugs.discard("")
        return tuple(sorted(slugs))

    def _safe_to_scan(self) -> bool:
        """True when a universe scan can run without abandoning live risk.

        A scan cancels every resting quote and stops draining both streams for
        its whole duration, so it may only run from a genuinely idle account.
        All three checks are whole-account: with several markets bound, an
        unresolved intent, a position or an authorized side on *any* slot makes
        the scan unsafe, and the primary market is not a proxy for the rest.
        """

        return (not self.ledger.unresolved_intents()
                and not self._has_open_position()
                and self._discovery_is_free())

    def _has_open_position(self) -> bool:
        """True when any market on the account still holds inventory.

        Rotation is gated on this rather than on the primary market: with
        several markets bound, a position on any one of them makes a scan
        unsafe, and checking only the primary would tear both streams down
        while inventory sits unhedged on another slot.
        """

        return bool(self.ledger.query(
            "SELECT 1 FROM execution_positions "
            "WHERE CAST(net_qty AS REAL)!=0 LIMIT 1"))

    def _only_parked_positions_remain(self, parked: list[str]) -> bool:
        """True when every market still holding risk is parked on its exit.

        Parked means: a nonzero position, no unresolved intent, and nothing
        working that could add to it. That is a safe state to stop in -- the
        exit keeps resting at the venue and the next process binds the market
        exit-only through recovery. Any market outside that set still needs
        shutdown's attention, so this refuses.
        """

        parked_set = set(parked)
        for slug in self._shutdown_slugs():
            if slug in parked_set:
                continue
            if self.ledger.position(slug) != 0:
                return False
            if self.ledger.working_orders(slug):
                return False
            if self.ledger.market_has_unresolved_intent(slug):
                return False
        return bool(parked_set)

    def _venue_proves_flat(self) -> bool:
        """True when the last venue snapshot showed no orders and no position.

        Read from the snapshots the shutdown loop already refreshes, so this
        adds no requests. An unresolved create or UNKNOWN_ABSENT order is not
        flat proof: it may have reached the venue and simply not appeared in
        the open-order snapshot yet.
        """

        if self.ledger.unresolved_intents() or self.ledger.absent_orders():
            return False
        if self.ledger.query(
                "SELECT 1 FROM execution_orders WHERE status IN "
                "('OPEN','NEW','PARTIAL','PENDING','SUBMITTED') LIMIT 1"):
            return False
        return not self.ledger.query(
            "SELECT 1 FROM execution_positions "
            "WHERE CAST(net_qty AS REAL)!=0 LIMIT 1")

    def _whole_account_safe_to_stop(self) -> bool:
        """True only when every market is proven flat and state is known."""

        if not self.ledger.risk_state_known():
            return False
        # An empty set is genuinely safe: no market holds risk.
        return all(
            self.kill_switch.status(slug).safe_to_stop
            for slug in self._shutdown_slugs())

    async def _shutdown(self, reason: str) -> None:
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot LIVE shutdown started: {reason}. "
            "New risk is disabled; reconciling to flat.",
            critical=(reason == "EMERGENCY_EQUITY_HARD_STOP"
                      or reason.startswith("critical:")
                      or reason.startswith("risk_stop:")))
        shutdown_failures = 0
        exit_budget_announced = False
        slug = self.market_slug
        started_at = self._now()
        deadline = started_at + timedelta(
            seconds=self.config.max_shutdown_seconds)
        # The start notification is the first alert in the cadence. Without
        # this anchor, a quiet unresolved/working state would send an immediate
        # duplicate and then go silent until the deadline.
        last_alert_at: datetime | None = started_at
        # Shutdown keeps a flatten budget independent of the quoting loop. It
        # prices a freshly read book against the conservative recorded basis,
        # so an exit the runtime could not complete may still complete here.
        # Inheriting an exhausted runtime counter meant the terminal path
        # never attempted a flatten at all and inventory reached settlement.
        exit_attempts = 0

        async def alert(message: str, *, force: bool = False) -> bool:
            """Repeat a stuck-shutdown alert instead of latching it once.

            A position that needs manual intervention must keep announcing
            itself; a single message is too easy to miss overnight.
            """

            nonlocal last_alert_at
            moment = self._now()
            if (not force and last_alert_at is not None
                    and (moment - last_alert_at).total_seconds()
                    < self.config.shutdown_alert_interval_seconds):
                return False
            last_alert_at = moment
            await self.notifier.send_async(message, critical=True)
            return True

        while True:
            delay = 1.0
            now = self._now()
            try:
                await self._rest_reconcile(now)
                # Every market that ever held risk must be retired, not just
                # the primary. A slug the session has already dropped can still
                # own an order or a position, and leaving it behind is exactly
                # how inventory reaches settlement.
                parked: list[str] = []
                for market_slug in self._shutdown_slugs():
                    position = self.ledger.position(market_slug)
                    working = self.ledger.working_orders(market_slug)
                    unresolved = self.ledger.market_has_unresolved_intent(
                        market_slug)
                    if position != 0 and not unresolved:
                        # A position keeps its exit working. Shutdown used to
                        # cancel every order and then cross out at whatever the
                        # book showed, with no price cap anywhere on that path
                        # -- so a redeploy liquidated at the market. Measured
                        # 2026-08-15: short 5 at 0.65 with a break-even reducer
                        # resting, cancelled and crossed at 0.96 on a deploy
                        # restart, -$1.55 on a position that was costing
                        # nothing to hold.
                        #
                        # Leaving the exit resting is strictly better: it may
                        # fill at basis while we are down, and the next process
                        # binds the market exit-only through recovery. Orders
                        # that could ADD to the position are still pulled --
                        # nothing may grow a position nobody is watching.
                        # Kickoff is the one deadline parking may not outlive.
                        # A held position that reaches settlement is unbounded
                        # up to the full contract value, which is how maker-lab
                        # lost $443.88 over four days. Inside the flatten
                        # window we still cross out at whatever it costs.
                        state = self.state_for(market_slug)
                        start = (
                            state.market.game_start
                            if state.market is not None else None)
                        near_kickoff = (
                            start is None
                            or (start - now).total_seconds()
                            <= self.config.force_exit_before_start_seconds)
                        if not near_kickoff:
                            reducing = (
                                "ORDER_SIDE_SELL" if position > 0
                                else "ORDER_SIDE_BUY")
                            if any(str(order["side"]) != reducing
                                   for order in working):
                                self._log_shutdown(
                                    market_slug, "cancel_building",
                                    position=position, working=len(working))
                                await self.kill_switch.initiate(
                                    market_slug, now=now)
                                continue
                            # Parking only means anything if the exit is
                            # actually there. With nothing resting the position
                            # is not parked, it is stranded -- shutdown reports
                            # non-flat, the supervisor refuses to restart, and
                            # the position sits with no way out. Observed
                            # 2026-08-15 on aec-valorant-ff-ep: +5 held, no
                            # order, bot down. Fall through to the flatten
                            # below instead, which is the pre-existing path for
                            # exactly this state.
                            if working:
                                self._log_shutdown(
                                    market_slug, "parked", position=position,
                                    working=len(working),
                                    seconds_to_kickoff=(
                                        None if start is None
                                        else round((start - now).total_seconds())))
                                parked.append(market_slug)
                                continue
                    if working and not unresolved:
                        self._log_shutdown(
                            market_slug, "cancel_working", position=position,
                            working=len(working))
                        await self.kill_switch.initiate(market_slug, now=now)
                        continue
                    if position == 0 or working or unresolved:
                        # Silence here made a stuck shutdown look exactly like
                        # a finished one. A flat market is genuinely nothing to
                        # report; one still holding is waiting on something and
                        # must say so.
                        if position != 0:
                            self._log_shutdown(
                                market_slug, "waiting", position=position,
                                working=len(working), unresolved=unresolved)
                        continue
                    if exit_attempts >= self.config.max_shutdown_exit_attempts:
                        self._log_shutdown(
                            market_slug, "budget_exhausted",
                            position=position, attempts=exit_attempts,
                            budget=self.config.max_shutdown_exit_attempts)
                        await alert(
                            "🚨 Liquidity Bot exhausted its shutdown exit budget. "
                            "Maker quoting remains terminal; manual intervention "
                            f"is required for remaining position {position} in "
                            f"{market_slug}.",
                            force=not exit_budget_announced)
                        exit_budget_announced = True
                        delay = 30.0
                        continue
                    await self._refresh_book(now, market_slug)
                    state = self.state_for(market_slug)
                    if state.book is None or state.market is None:
                        raise RuntimeError("shutdown book is unavailable")
                    basis = self.ledger.inventory_basis(market_slug)
                    entry_side = "bid" if position > 0 else "offer"
                    conservative_entry = (
                        float(basis.average_entry_price) if basis is not None
                        else .99 if position > 0 else .01)
                    exit_result = cross_exit(
                        state.book, entry_side=entry_side,
                        entry_price=conservative_entry,
                        qty=float(abs(position)), depth_haircut=.5)
                    if (not exit_result.complete
                            or exit_result.worst_price is None):
                        # This raise is why a shutdown could burn its whole
                        # budget without ever sending an order. Say so before
                        # raising, or the alert reports a spent budget with no
                        # trace of what refused.
                        self._log_shutdown(
                            market_slug, "depth_refused", position=position,
                            entry_side=entry_side, haircut=0.5,
                            complete=exit_result.complete)
                        raise RuntimeError(
                            "insufficient stressed depth to flatten exact "
                            "position")
                    side = "offer" if position > 0 else "bid"
                    action = PlanAction(
                        ActionKind.PLACE_IOC, market_slug,
                        "commissioning_shutdown_flatten", side=side,
                        price=exit_result.worst_price, qty=abs(position))
                    if self.driver is None:
                        raise RuntimeError("action driver is unavailable")
                    # Count before mutation because a transport failure
                    # after send may still have reached the venue.
                    exit_attempts += 1
                    # Logged before the send, for the same reason the counter
                    # increments before it: a transport failure after the send
                    # may still have reached the venue, and a record written
                    # only on success would omit exactly those cases.
                    self._log_shutdown(
                        market_slug, "flatten", position=position, side=side,
                        price=exit_result.worst_price, qty=abs(position),
                        attempt=exit_attempts,
                        budget=self.config.max_shutdown_exit_attempts)
                    await self.driver.apply_one(
                        [action], market=state.market,
                        position=position, now=now)
                    delay = min(2 ** max(exit_attempts - 1, 0), 5)
                # The venue is the authority on whether we hold risk. Waiting
                # on local bookkeeping keeps a flat process alive for minutes:
                # risk_state_known() is false during any in-flight cancel, and
                # that is 35% of wall-clock time, so shutdown ground through
                # its budget with nothing open. Slow stops are why deploys
                # needed kill -9, and kill -9 is what left the state that
                # produced a recovery run and its alerts on every restart.
                if self._venue_proves_flat() or self._whole_account_safe_to_stop():
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot LIVE stopped flat after {reason}.")
                    return
                if parked and self._only_parked_positions_remain(parked):
                    # Not flat, but deliberately so. Spinning to the 900s
                    # deadline here would make every deploy a fifteen-minute
                    # stop and end it on a critical alert.
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot LIVE stopped after {reason} with "
                        f"{len(parked)} position(s) left resting on their exit: "
                        f"{', '.join(sorted(parked))}. No order can add to them "
                        "and the next process resumes their exit.")
                    return
                shutdown_failures = 0
            except Exception as exc:
                shutdown_failures += 1
                delay = min(2 ** min(shutdown_failures - 1, 5), 30)
                # Recorded unconditionally. The traceback below is gated on
                # `alert`, which is rate-limited by
                # shutdown_alert_interval_seconds -- so inside that window a
                # failing shutdown produced no Discord message AND no local
                # record at all. That combination is why the krovit-sukiho
                # budget exhaustion could not be diagnosed afterwards.
                self._log_shutdown(
                    slug, "failed", error=type(exc).__name__,
                    failures=shutdown_failures)
                if await alert(
                        "🚨 Liquidity Bot cannot yet prove flat during shutdown "
                        f"({type(exc).__name__}). Process remains in recovery."):
                    traceback.print_exc()
            # Checked after the proven-flat return above, so a healthy shutdown
            # always completes. Staying alive forever hid the one state that
            # needs a human, so the window ends in a non-flat exit instead.
            if self._now() >= deadline:
                self._shutdown_proved_flat = False
                await self.notifier.send_async(
                    f"🚨 Liquidity Bot shutdown window expired after {reason} "
                    "without proving flat. Remaining position "
                    f"{self.ledger.position(slug)} in {slug} requires immediate "
                    "operator inspection; the process is exiting non-flat.",
                    critical=True)
                return
            await alert(
                "🚨 Liquidity Bot still cannot prove flat during shutdown. "
                f"Position {self.ledger.position(slug)} in {slug}; maker "
                "quoting remains terminal and reconciliation continues.")
            await self.sleep(delay)

    async def run(self) -> CommissioningResult:
        market_task: asyncio.Task | None = None
        private_task: asyncio.Task | None = None
        queue_measurement_task: asyncio.Task | None = None
        stop_reason = "unknown"
        preflight_complete = False
        preflight_error: Exception | None = None
        try:
            startup_mode = await self.preflight_until_ready()
            preflight_complete = True
            if startup_mode == "RECOVERY_ONLY":
                self.request_stop("startup_recovery_only")
                # Not critical. This is the healthy recovery path, and it
                # fires on every restart that finds leftover state -- roughly
                # fifteen times on 2026-08-14 alone. An alert that cries wolf
                # on a normal state trains the operator to ignore Discord,
                # which is where the real stops and ownership violations go.
                await self.notifier.send_async(
                    "Liquidity Bot found identified one-market state from a "
                    "prior process. New risk remains disabled; recovering flat.")
            now = self._now()
            try:
                self._evaluate_rewards(now)
            except Exception as exc:
                stop_reason = await self._handle_strategy_plan_failure(exc)
                self.request_stop(stop_reason)
            next_balance = now
            next_book_refresh = now + timedelta(seconds=3)
            next_contract_refresh = now + timedelta(minutes=1)
            next_summary = now + timedelta(minutes=5)
            next_reconcile = now
            next_state_reconcile = now + timedelta(seconds=10)
            next_earnings = now + timedelta(
                seconds=self.config.earnings_refresh_seconds)
            next_selection = self._initial_market_selection_due(now)
            market_task = asyncio.create_task(self.market_stream.next_book())
            private_task = asyncio.create_task(self.private_stream.next_event())
            queue_measurement_task = asyncio.create_task(
                self._queue_measurement_loop())
            while True:
                now = self._now()
                stop_reason = self._stop_reason(now) or ""
                await self._announce_loss_stop_pause()
                if stop_reason:
                    break
                if self._streams_need_rebind:
                    # Both `next_*` tasks are reading streams that are already
                    # closed, so waiting on them below can only ever time out.
                    # Drop them before rebinding: the recovery replaces both
                    # stream objects, and a task holding the old one would
                    # otherwise be recreated against a stream nobody owns.
                    stream_tasks = (market_task, private_task)
                    for task in stream_tasks:
                        task.cancel()
                    await asyncio.gather(*stream_tasks, return_exceptions=True)
                    stop_reason = await self._recover_bound_streams(
                        RuntimeError("stream bind left both streams closed")
                    ) or ""
                    if stop_reason:
                        break
                    market_task = asyncio.create_task(
                        self.market_stream.next_book())
                    private_task = asyncio.create_task(
                        self.private_stream.next_event())
                    continue
                await asyncio.wait(
                    {market_task, private_task}, timeout=.5,
                    return_when=asyncio.FIRST_COMPLETED)
                market_task, private_task, stream_stop = (
                    await self._consume_ready_stream_tasks(
                        market_task, private_task))
                if stream_stop:
                    stop_reason = stream_stop
                    break
                now = self._now()
                if now >= next_balance:
                    try:
                        await self._refresh_balance(now)
                    except Exception:
                        # Freshness expires in 15 seconds and then triggers the
                        # normal risk stop. Never substitute buying power.
                        pass
                    next_balance = now + timedelta(seconds=5)
                if now >= next_state_reconcile:
                    try:
                        # Fresh complete snapshots renew account visibility.
                        # Individual ambiguity stays behind its market gate and
                        # is repaired one owner at a time below; reading every
                        # id here made the ten-second freshness task itself a
                        # twenty-slot head-of-line blocker.
                        await self._rest_reconcile(
                            now, strict_order_markets=frozenset())
                    except Exception:
                        # Preserve the last proven state briefly. If both the
                        # private stream and REST remain quiet/unavailable, the
                        # freshness gate expires and shuts down new risk.
                        pass
                    next_state_reconcile = now + timedelta(seconds=10)
                if now >= next_earnings:
                    try:
                        await self._refresh_earnings(now)
                        self._runtime_error_counts.pop(
                            "reward_earnings_refresh", None)
                    except Exception as exc:
                        # Earnings are delayed and never authorize an exit, but
                        # every live run must preserve an auditable venue view.
                        self._earnings_observed_at = None
                        await self._report_runtime_error(
                            "reward_earnings_refresh", exc)
                    next_earnings = now + timedelta(
                        seconds=self.config.earnings_refresh_seconds)
                # A terminal CANCEL is positive proof that this owner may be
                # planned again. Re-service only those owners, then run the
                # same freshness/equity/intent-gated decision path used by the
                # full slate. Measured 2026-08-23: cancel confirmation took a
                # median 1.2s, but the replacement waited another 39.5s
                # (p90 118.6s) behind the shared service/sweep path; 21/80
                # replacements returned at the exact price just cancelled.
                # This removes that scheduler wait without placing before a
                # terminal state or weakening any quote/economic gate.
                priority_replans = self._take_priority_replans()
                if priority_replans:
                    priority_started_at = self._now()
                    stop_reason = await self._service_bound_markets(
                        priority_replans)
                    if stop_reason:
                        break
                    # Priority service can itself yield for REST. Apply any
                    # fill/order/book events that arrived during that work
                    # before deciding, exactly as the full-slate path below
                    # does. Otherwise the fast path could place from a ledger
                    # that predates a fill received while its book refreshed.
                    market_task, private_task, stream_stop = (
                        await self._consume_ready_stream_tasks(
                            market_task, private_task))
                    if stream_stop:
                        stop_reason = stream_stop
                        break
                    # A drained book may have changed the candidate price and
                    # economics after the first service. This second pass is
                    # normally REST-free because the first advanced the
                    # per-market timers; it synchronizes those derived gates
                    # with the just-applied stream state.
                    stop_reason = await self._service_bound_markets(
                        priority_replans)
                    if stop_reason:
                        break
                    stop_reason = await self._decision_sweep(
                        priority_replans) or ""
                    if stop_reason:
                        break
                    priority_finished_at = self._now()
                    print(
                        f"[priority-replan] "
                        f"at={priority_finished_at.isoformat()} "
                        f"elapsed={max(0.0, (priority_finished_at - priority_started_at).total_seconds()):.1f} "
                        f"markets={','.join(priority_replans)}",
                        flush=True,
                    )
                # Service every bound market on its own cadence. Keep this
                # last among periodic REST work so account latency cannot age a
                # book before the decisions below.
                stop_reason = await self._service_bound_markets()
                if stop_reason:
                    break
                # REST service yields to the event loop for every request. In
                # a twenty-slot pass, stream tasks can complete early and hold
                # one event while newer events queue behind them. Consume the
                # held event first, then drain the backlog before freshness and
                # private-state gates authorize any order.
                market_task, private_task, stream_stop = (
                    await self._consume_ready_stream_tasks(
                        market_task, private_task))
                if stream_stop:
                    stop_reason = stream_stop
                    break
                now = self._now()
                stop_reason = self._stop_reason(now) or ""
                await self._announce_loss_stop_pause()
                if stop_reason:
                    break
                # A full rotation needs a whole-account flat state, which with
                # twenty markets almost never happens -- measured 2026-08-16,
                # it ran twice, both at startup, while markets kicked off and
                # were never replaced. When it cannot run, still reclaim the
                # slots held by markets that have kicked off and are flat, or
                # fill capacity that startup validation left free. That path
                # keeps every resting order where it is.
                if self._incremental_top_up_due(now, next_selection):
                    stream_tasks = (market_task, private_task)
                    for task in stream_tasks:
                        task.cancel()
                    await asyncio.gather(
                        *stream_tasks, return_exceptions=True)
                    try:
                        await self._top_up_active_markets(now)
                        self._reward_end_top_up_requested = False
                        next_selection = self._now() + timedelta(
                            seconds=self.config.market_selection_refresh_seconds)
                    except Exception as exc:
                        self._reward_end_top_up_requested = False
                        await self._report_runtime_error(
                            "market_top_up", exc)
                        # Back off exactly as the rotation path does. Without
                        # this `next_selection` stays in the past, the branch
                        # re-enters on the very next iteration, and a failing
                        # scan retries forever -- cancelling and rebuilding
                        # both stream tasks every pass.
                        next_selection = self._now() + timedelta(seconds=60)
                    market_task = asyncio.create_task(
                        self.market_stream.next_book())
                    private_task = asyncio.create_task(
                        self.private_stream.next_event())
                    now = self._now()
                    next_book_refresh = now + timedelta(seconds=3)
                    next_contract_refresh = now + timedelta(minutes=1)
                    continue
                if (self.config.auto_select_reward_markets
                        and now >= next_selection
                        and self._safe_to_scan()):
                    stream_tasks = (market_task, private_task)
                    for task in stream_tasks:
                        task.cancel()
                    await asyncio.gather(
                        *stream_tasks, return_exceptions=True)
                    try:
                        await self._rotate_reward_market(now)
                        self._runtime_error_counts.pop(
                            "reward_market_selection", None)
                        next_selection = self._now() + timedelta(
                            seconds=(
                                self.config.market_selection_refresh_seconds))
                    except RuntimeInvariantViolation as invariant:
                        await self._report_runtime_error(
                            "reward_market_selection_terminal", invariant)
                        stop_reason = f"critical:{invariant.code}"
                        break
                    except Exception as exc:
                        await self._report_runtime_error(
                            "reward_market_selection", exc)
                        next_selection = self._now() + timedelta(seconds=60)
                    market_task = asyncio.create_task(
                        self.market_stream.next_book())
                    private_task = asyncio.create_task(
                        self.private_stream.next_event())
                    now = self._now()
                    next_book_refresh = now + timedelta(seconds=3)
                    next_contract_refresh = now + timedelta(minutes=1)
                    next_state_reconcile = now + timedelta(seconds=10)
                    next_earnings = now + timedelta(
                        seconds=self.config.earnings_refresh_seconds)
                    # Re-enter through every freshness and state gate after a
                    # potentially long, rate-governed universe scan.
                    continue
                if self.ledger.unresolved_intents():
                    if now >= next_reconcile:
                        unresolved_rows = self.ledger.unresolved_intents()
                        # Unattributable ambiguity cannot be scoped. Otherwise
                        # repair a bounded owner batch per pass. Its reads are
                        # independently scheduled through the shared limiter,
                        # so a persistent 404 cannot hide every other owner's
                        # positive terminal answer behind its retry ladder.
                        reconcile_scope = (
                            self._next_unresolved_reconcile_scope(
                                unresolved_rows))
                        try:
                            if reconcile_scope is None:
                                await self._rest_reconcile(now)
                            else:
                                await self._repair_unresolved_orders(
                                    now, markets=reconcile_scope)
                        except Exception as exc:
                            # Ambiguous state remains quarantined. Equity and
                            # stream freshness independently force shutdown if
                            # this cannot be reconciled promptly.
                            #
                            # Reported, not silent. This was a bare `pass`, and
                            # on 2026-08-18 that destroyed the only evidence of
                            # why the bot died: for the two minutes before the
                            # critical stop, every 2s reconcile raised here and
                            # left no trace, so commissioning.log jumps from the
                            # last placement straight to the shutdown and what
                            # `retrieve` actually raised is simply gone. This
                            # loop is counting down to a terminal, account-wide
                            # stop; it must not count down in the dark.
                            await self._report_runtime_error(
                                "unresolved_mutation_reconcile", exc)
                        else:
                            self._runtime_error_counts.pop(
                                "unresolved_mutation_reconcile", None)
                        next_reconcile = self._now() + timedelta(seconds=2)
                    now = self._now()
                    unresolved_age = self._oldest_unresolved_age_seconds(now)
                    if unresolved_age is not None:
                        limit = self.config.unresolved_mutation_timeout_seconds
                        stuck, unattributable = self._stuck_mutations(now)
                        # Retire the market that owns the stuck intent. It
                        # keeps its exit path (invariant 62) and `place()`
                        # already refuses while its own intent is unresolved,
                        # so the market stops building risk without the other
                        # nineteen paying for it. Retirement carries its own
                        # systemic bound, so a fault that really is
                        # account-wide still ends the session through
                        # `market_retirement_limit_exceeded`.
                        for slug in sorted(stuck):
                            if self._is_retired(slug):
                                continue
                            stop = await self._retire_market(
                                slug, "unresolved_mutation_timeout",
                                reset_exit_budget=False)
                            if stop:
                                raise RuntimeInvariantViolation(
                                    "market_retirement_limit_exceeded", stop)
                        # No market to blame: cannot be scoped, so it keeps the
                        # terminal behaviour. Fail closed.
                        if (unattributable is not None
                                and self.risk_guard is not None):
                            self.risk_guard.trip_critical(
                                "order mutation unresolved for more than "
                                f"{limit:.0f} seconds")
                        if now >= next_summary:
                            await self._send_summary(now)
                            next_summary = now + timedelta(minutes=5)
                    # Attributable mutations quarantine only their owner. An
                    # intent without a market cannot be scoped, so retain the
                    # account-wide fail-closed behavior for that case.
                    if any(not str(row["market_slug"] or "").strip()
                           for row in self.ledger.unresolved_intents()):
                        continue
                stop_reason = await self._decision_sweep() or ""
                if stop_reason:
                    break
                if now >= next_summary:
                    await self._send_summary(now)
                    next_summary = now + timedelta(minutes=5)
        except asyncio.CancelledError:
            stop_reason = "process_cancelled"
        except Exception as exc:
            stop_reason = f"runner_failure:{type(exc).__name__}"
            # Say WHERE. This alert used to carry the exception type alone, so
            # when the runner crash-looped on 2026-08-14 Discord reported
            # "IndexError" three times with no message and no location -- true,
            # useless, and impossible to act on without shell access. The last
            # frame is the one that actually failed.
            frame = ""
            tb = exc.__traceback__
            while tb is not None:
                name = tb.tb_frame.f_code.co_filename.rsplit("/", 1)[-1]
                frame = f"{name}:{tb.tb_lineno} in {tb.tb_frame.f_code.co_name}"
                tb = tb.tb_next
            location = f" at {frame}" if frame else ""
            await self.notifier.send_async(
                f"🚨 Liquidity Bot LIVE runner failure: "
                f"{type(exc).__name__}: {str(exc)[:200]}{location}. "
                "The supervisor restarts this if the account proved flat.",
                critical=True)
            if not preflight_complete:
                preflight_error = exc
            else:
                traceback.print_exc()
        finally:
            stream_tasks = tuple(
                task for task in (market_task, private_task) if task is not None)
            for task in stream_tasks:
                task.cancel()
            if stream_tasks:
                await asyncio.gather(*stream_tasks, return_exceptions=True)
            if preflight_complete:
                await self._shutdown(stop_reason)
            if queue_measurement_task is not None:
                queue_measurement_task.cancel()
                await asyncio.gather(
                    queue_measurement_task, return_exceptions=True)
            await self.private_stream.close()
            await self.market_stream.close()
        if preflight_error is not None:
            raise preflight_error
        stopped = self._now()
        creates = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        return CommissioningResult(
            self.started_at or stopped,
            self.cutoff_at,
            stopped,
            stop_reason,
            # Whole-account. This value picks the exit code: a false "flat"
            # reports a clean stop while a contract sits at the venue, and
            # exit 3 is the only signal that says check the exchange now.
            (not self._has_open_position() and self._shutdown_proved_flat),
            creates - self._orders_before,
        )


async def _run(args: argparse.Namespace) -> CommissioningResult:
    now = datetime.now(timezone.utc)
    auto_select = bool(getattr(args, "auto_select_rewards", False))
    config = CommissioningConfig(
        market_slug=(AUTO_REWARD_MARKET if auto_select else args.market_slug),
        economic_risk_accepted=args.accept_one_contract_risk,
        exclusive_api_key_confirmed=args.exclusive_api_key,
        maker_lab_stopped_confirmed=args.maker_lab_stopped,
        arm_live=args.arm_live,
        auto_select_reward_markets=auto_select,
        quote_size=Decimal(args.quote_size),
    )
    # Refuse before opening the credential file, database, client, or network.
    config.assert_authorized(now=now)
    secrets = load_runtime_secrets(require_polymarket=True)
    if not (secrets.discord_configured and secrets.discord_errors_configured):
        raise CommissioningRefused(
            "normal and critical Discord delivery must be configured")
    lease = SingleInstanceLease(args.lock_file)
    lease.acquire()
    client = AsyncPolymarketUS(
        key_id=secrets.key_id, secret_key=secrets.secret_key)
    ledger = ExecutionLedger(args.database)
    runner = CommissioningRunner(
        client, config, ledger, DiscordNotifier(secrets))
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig, runner.request_stop, f"operator_{sig.name.lower()}")
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        return await runner.run()
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        ledger.close()
        await client.close()
        lease.release()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Polymarket liquidity commissioning runner")
    market = parser.add_mutually_exclusive_group(required=True)
    market.add_argument("--market-slug")
    market.add_argument(
        "--auto-select-rewards",
        action="store_true",
        help="join executable sports markets to rewards and rotate one flat executor",
    )
    parser.add_argument("--database", type=Path,
                        default=Path("data/commissioning.db"))
    parser.add_argument("--lock-file", type=Path,
                        default=Path("data/commissioning.lock"))
    parser.add_argument("--arm-live", action="store_true")
    parser.add_argument("--accept-one-contract-risk", action="store_true")
    parser.add_argument("--exclusive-api-key", action="store_true")
    parser.add_argument("--maker-lab-stopped", action="store_true")
    parser.add_argument(
        "--quote-size", type=int, choices=range(5, 11), default=5,
        help="whole contracts per quote; size-coupled risk limits derive automatically",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
    except CommissioningRefused as exc:
        # State we must not trade from: open orders, a position, unresolved
        # intents, ownership we cannot explain. Terminal on purpose -- exit 2
        # is the code the supervisor never restarts, because a human has to
        # look before this account trades again.
        parser.exit(2, f"commissioning refused: {exc}\n")
    except AdapterError as exc:
        # The venue did not answer. That is transport, and it says nothing
        # about whether our state is safe -- so it must NOT be terminal.
        #
        # These shared exit 2 with the refusal above, and a venue blip
        # therefore downed the bot permanently. Three separate outages on
        # 2026-08-16/17, each a different endpoint, each on a flat account
        # with nothing wrong: `GET /v1/incentives/earnings` 500,
        # `GET /v1/orders/open` 500 returning an HTML error page, and a book
        # that came back empty on both sides.
        #
        # Exit 5 is the supervisor's recoverable code: it restarts with
        # backoff and still gives up after MAX_FAST_CRASHES deaths inside
        # FAST_CRASH_SECONDS, so a venue that is genuinely down still stops
        # the session and alerts rather than spinning.
        parser.exit(5, f"commissioning transport failure: {exc}\n")
    print(json.dumps({
        "started_at": result.started_at.isoformat(),
        "cutoff_at": (
            result.cutoff_at.isoformat() if result.cutoff_at is not None else None),
        "stopped_at": result.stopped_at.isoformat(),
        "stop_reason": result.stop_reason,
        "flat": result.flat,
        "orders_sent_to_polymarket": result.orders_sent_to_polymarket,
    }, indent=2, sort_keys=True))
    if not result.flat:
        return 3
    if result.stop_reason.startswith("runner_failure:"):
        # An unexpected loop failure that still proved the whole account flat
        # is recoverable by a fresh process: startup re-runs the full flat and
        # ownership preflight before any order. Treating it as terminal is what
        # left the bot down for hours after a single transient defect. Loss
        # stops, unowned state and every named critical stay at exit 4 and are
        # never restarted. The supervisor still bounds fast restart loops.
        return 5
    if result.stop_reason.startswith("critical:"):
        return 4
    if (result.stop_reason.startswith(
            "risk_stop:critical failure: order mutation unresolved")
            and "," not in result.stop_reason):
        # Narrow by construction, and deliberately so. This is the only risk
        # stop that is a venue-latency artefact rather than a statement about
        # the account: measured 2026-08-18, the venue accepted a place, handed
        # back an exchange id, and then served no state for it for 131 seconds
        # while also omitting it from a complete open-orders snapshot. The
        # intent cannot resolve during that window -- absence is never read as
        # cancellation -- so the guard fires on a healthy, flat account.
        #
        # Raising the timeout is not the answer and has already been tried
        # twice: 10s cost an outage on 2026-08-17, 60s cost another the next
        # day. See FINDINGS.md. What actually costs the hours is that this
        # exits 0, which the supervisor reads as a clean operator stop and
        # never restarts -- three hours down on 2026-08-18 for a stall that
        # cleared on its own in two minutes.
        #
        # Safe to restart for the same reason exit 5 already is: the process
        # proved flat before returning (`result.flat` is checked above and
        # returns 3 otherwise), and a fresh one re-runs the entire flat and
        # ownership preflight before it is allowed to place anything. The
        # supervisor's fast-crash counter still gives up on a genuine loop.
        #
        # The comma test keeps this to the single-blocker case. `blockers()`
        # are joined with "," by `_risk_stop_reason`, so a comma means
        # something else tripped in the same breath -- a loss stop, the session
        # duration limit -- and those must stay terminal. Any unexpected shape
        # falls through to 0 and does not restart, which is the safe direction.
        return 5
    if result.stop_reason == "startup_recovery_only":
        # A recovery run exists only to retire state a previous process left
        # behind. It proved flat and did exactly that, so the work is done and
        # a normal session should follow immediately. Exiting 0 made the
        # supervisor stop instead, leaving the bot down after every recovery
        # until someone restarted it by hand -- observed 2026-08-14T04:38Z.
        # Exit 5 restarts, and the fresh process re-runs the whole flat and
        # ownership preflight before any order.
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
