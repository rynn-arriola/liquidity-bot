"""Explicitly armed liquidity commissioning runner.

The economic evidence gate remains red. This runner exists only for the
operator-accepted experiment and is mechanically constrained to 20 selected
rewarded markets, a restart-scoped whole quote size from 5 to 30 contracts, an
operator-controlled stop and an $80 emergency hard
stop.

Importing this module has no side effect. Exchange access is possible only
through ``main()`` with all explicit arming acknowledgements.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
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

from .active_scope_cache import write_active_scope_snapshot
from .account_snapshots import (
    AccountEquitySnapshot, AsyncAccountSnapshotGateway,
    account_equity_snapshot)
from .bot_era import bot_era_rewards_by_status
from .adapters import (
    AsyncIntervalLimiter, AsyncReadOnlyGateway, REST_REQUEST_TIMEOUT_SECONDS,
    shared_api_limiter)
from .cash_activity import corrected_opening_cash, fetch_nontrading_cash
from .commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    CommissioningUnavailable,
)
from .discord_summary import (
    LiquiditySummary, build_liquidity_summary, build_runtime_alert)
from .errors import (
    AdapterError, ApiAdmissionDeferred, EmptyBookError,
    RuntimeInvariantViolation)
from .execution_ledger import (
    ACTIVE_ORDER_STATES,
    UNRESOLVED_INTENTS,
    ExecutionLedger,
    LedgerConflict,
    QuoteObservation,
)
from .entry_quality import (
    DEFAULT_ENTRY_QUALITY_POLICY_PATH, load_entry_quality_policy)
from .entry_order_policy import (
    DEFAULT_ENTRY_ORDER_POLICY_PATH,
    EntryOrderPolicyError,
    load_entry_order_policy,
)
from .entry_pause import (
    OPERATOR_ENTRY_PAUSE_PATH,
    UiTradingProbe,
    UiTradingState,
    operator_entry_pause_requested,
    probe_polymarket_us_homepage,
)
from .fill_hazard import MondayFillHazardModel
from .live_orders import (
    AsyncTradingGateway,
    LiveOrderExecutor,
    OPEN_MARKET_STATUSES,
    OrderMutationRejected,
    OrderOutcomeUncertain,
    OrderRequest,
    OrderValidationError,
    build_order_payload,
)
from .live_pnl_cache import write_live_pnl_snapshot
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
from .maintenance_schedule import (
    MAINTENANCE_CACHE_PATH,
    MAINTENANCE_SCHEDULE_POLL_SECONDS,
    MaintenanceDecision,
    MaintenanceFeedState,
    MaintenancePhase,
    MaintenanceScheduleProbe,
    MaintenanceWindow,
    load_maintenance_cache,
    maintenance_decision,
    probe_polymarket_us_maintenance_schedule,
    write_maintenance_cache,
)
from .models import AccountBalance, BookSnapshot, MarketIncentive, MarketMetadata
from .event_guard import EventAwareGateway
from .notifications import DiscordNotifier, RuntimeNotificationDispatcher
from .quote_size import MAX_QUOTE_SIZE, MIN_QUOTE_SIZE
from .slot_capacity import MAX_SLOT_CAPACITY, MIN_SLOT_CAPACITY
from .readiness import ReadinessVerdict
from .reward_calendar import (
    NEW_YORK, reward_day, reward_day_bounds, risk_day,
    split_reward_day_interval)
from .resting_time import resting_side_hours_by_day
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
from .streams import (
    MarketReadOnlyStream,
    PrivateStreamPool,
    STREAM_SILENCE_TIMEOUT_SECONDS,
)
from .venue_state import PrivateLedgerProjector


Clock = Callable[[], datetime]
LIVE_PNL_CACHE_PATH = Path("data/tv-live-pnl.json")
ACTIVE_SCOPE_CACHE_PATH = Path("data/tv-active-scope.json")

# The runtime loss sensor needs the same positive cash classification as the
# read-only reward report. One cash-only page currently covers the account;
# four is the proven bounded ceiling. The marked-equity snapshot itself is a
# balance/positions/balance bracket. A due cash-activity scan adds its page(s)
# and one final equal-cash proof; all requests still share the 4/s governor.
NONTRADING_CASH_MAX_PAGES = 4

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


class _ConfirmedMarketNotOpen(CommissioningUnavailable):
    """Exact fetched metadata says the owner cannot currently accept orders."""

# Ordinary reconnects are bounded so a local/schema defect cannot hold a live
# process dormant forever. Positively classified venue transport/5xx outages
# cross into the maintenance coordinator below instead. Neither path infers
# flatness; shutdown remains the only authority for that proof.
MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS = 12
MAX_MARKET_STREAM_RECOVERY_ATTEMPTS = 12
MAX_BOUND_STREAM_RECOVERY_ATTEMPTS = 12

# A PLACE whose response carried no exchange order id must never be resent: the
# venue may have accepted it even though the acknowledgement was lost.  It can,
# however, stop being live risk once several complete account snapshots agree
# that its market has neither an open order nor a position.  Requiring both
# repetition and elapsed time protects against an eventually-consistent first
# snapshot after a venue outage while keeping recovery in-process.
ORPHAN_PLACE_CLEAR_PROOFS = 3
ORPHAN_PLACE_CLEAR_MIN_SECONDS = 10.0


class _OrphanPlaceEvidencePending(CommissioningUnavailable):
    """A no-id PLACE remains dormant while repeated venue proof accumulates."""


class _CancellationEvidencePending(CommissioningUnavailable):
    """A requested cancel still lacks authoritative terminal order proof."""

# The ordinary read gateway already retries a confirmed 429 three times. A
# Cloudflare 1015 can outlive those short retries, and immediately converting
# that temporary read throttle into global quarantine erased 34 healthy orders
# on 2026-08-28. During an overlap handoff the decision coroutine is frozen and
# the replacement private sockets remain live, so use a second, slower bounded
# read-only retry tier before taking the destructive fallback. Mutations never
# use this path.
PRIVATE_HANDOFF_RATE_LIMIT_ATTEMPTS = 4
PRIVATE_HANDOFF_RATE_LIMIT_BASE_SECONDS = 15.0
PRIVATE_HANDOFF_RATE_LIMIT_MAX_SECONDS = 60.0

# A venue outage is different from an unsafe account.  After the ordinary
# bounded reconnect has proved that the failure persists, keep this process
# dormant and probe only complete authenticated reads.  Three passes one
# minute apart means "back" is sustained for about two minutes, rather than a
# single lucky 200 during a rolling maintenance restart.
MAINTENANCE_FAILURE_THRESHOLD = 3
MAINTENANCE_STABLE_READS = 3
MAINTENANCE_POLL_SECONDS = 60.0

# The public US application can pause customer participation while authenticated
# API reads and mutations still succeed.  This independent signal controls only
# new risk.  One exact banner pauses immediately; recovery requires three
# consecutive healthy observations so a rolling frontend deploy cannot flap
# entries.  It makes no venue API request and carries no credentials.
UI_AVAILABILITY_POLL_SECONDS = 60.0
UI_AVAILABILITY_STABLE_READS = 3

# A ready stream task and a no-op repair coroutine can both complete without
# suspending. Yield on every outer pass so Unix signal callbacks, the isolated
# telemetry task and socket readers cannot be starved by an otherwise fully
# synchronous iteration.
RUN_LOOP_COOPERATIVE_YIELD_SECONDS = 0.01
NO_ID_REPAIR_IDLE_SECONDS = 0.25
API_BUDGET_LOG_SECONDS = 60.0
DECISION_SCHEDULER_LOG_SECONDS = 60.0
TOP_UP_FAILURE_RETRY_SECONDS = 60.0

# Bound-market metadata and incentive rows are both bulk-readable. Refresh one
# private-shard-sized scope at a time: this replaces sixteen per-market calls
# with two while ensuring one malformed/failed response can affect at most the
# same eight owners as one private shard. A short look-ahead coalesces timers
# that were previously staggered by serial REST completion.
CONTRACT_REFRESH_SECONDS = 60.0
CONTRACT_REFRESH_MAX_BACKOFF_SECONDS = 900.0
SETTLEMENT_WAIT_BOOK_REFRESH_SECONDS = 60.0
CONTRACT_REFRESH_BATCH_SIZE = 8
CONTRACT_REFRESH_LOOKAHEAD_SECONDS = 5.0

RETIRED_EXIT_RETRY_SECONDS = 60

# Empty, order-free, ambiguity-free owners may become dormant after this many
# failed rounds. Any remaining position/order keeps recovery alive, with the
# per-round exit budget and 60-second cooldown unchanged.
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

# A recent full-universe selection can seed an urgent post-flat refill, but it
# is never trusted directly. Within this small window the runner bulk-refreshes
# metadata/incentives and re-reads every candidate book; older evidence falls
# back to the normal complete scan. The value is operational latency, not a
# strategy horizon or reward authorization lifetime.
POST_FLAT_SELECTION_CACHE_SECONDS = 120.0

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

    ACCOUNT_STATE_DESCRIPTION = "REST and fresh private state"
    SOURCE_REBOUND_DESCRIPTION = "rebound both streams"
    REQUIRE_EXECUTION_BACKFILL = False
    PENDING_ENTRY_ORDER_CEILING: int | None = None

    # How long "up but resting nothing" may last before it is worth waking
    # someone. Long enough that a rotation, a top-up rebind or a slate between
    # kickoffs passes quietly; short enough that neither 2026-08-16 outage
    # could have run its full length unannounced. The summary that drives it
    # runs every 5 minutes, so this is effectively three summaries of silence.
    QUIET_ALARM_SECONDS = 900.0

    # A five-minute report can land in the few seconds between an owned cancel
    # being submitted and its positive terminal proof arriving. Publishing
    # `UNAVAILABLE` for that ordinary in-flight interval makes capital
    # guidance look permanently broken even though the next account state is
    # clean. Delay only that scheduled report, never the safety gate or the
    # withdrawal calculation. Persistent ambiguity still becomes visible at
    # this bound, and the recheck is ledger-only so reporting cannot consume
    # venue capacity needed by trading.
    SUMMARY_PROOF_DEFERRAL_SECONDS = 60.0
    SUMMARY_PROOF_RECHECK_SECONDS = 2.0

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
    CANCEL_RETRY_SECONDS = 10.0

    # Async rejection is a terminal state, but immediately recreating the same
    # invalid order turns a useful structured reason into hot-loop churn.
    # Post-only is an ordinary book race and gets only a one-second local wait;
    # balance/risk rejects wait for multiple five-second balance refreshes;
    # an unfamiliar validation reject is isolated to that market for a minute.
    POST_ONLY_REJECTION_COOLDOWN_SECONDS = 1.0
    BALANCE_REJECTION_COOLDOWN_SECONDS = 15.0
    VALIDATION_REJECTION_COOLDOWN_SECONDS = 60.0
    BUYING_POWER_RECHECK_SECONDS = 5.0

    # A response-backed permission denial blocks new exposure account-wide
    # until a later entry proves otherwise. One entry probe per minute avoids
    # turning a twenty-market sweep into twenty deterministic failures.
    # Cancels and reducers bypass that entry circuit: preserving the ability
    # to reduce risk is more important than assuming an entry rejection also
    # rejected safety mutations. If a safety mutation itself receives the
    # same response-backed rejection, retry it on the normal cancel cadence.
    ENTRY_MUTATION_ACCESS_RECHECK_SECONDS = 60.0
    RISK_MUTATION_ACCESS_RECHECK_SECONDS = 10.0

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

    # These are current-book economic refusals which selection can improve by
    # choosing another market.  Missing/stale data, lifecycle ambiguity,
    # retirement and execution-state blockers are deliberately absent: those
    # have their own fail-closed recovery paths and cannot prove a continuous
    # economic comparison.
    REPLACEABLE_ECONOMIC_BLOCKERS = frozenset({
        "target_not_met",
        "outside_reward_cutoff",
        "book_sides_overlap",
        "price_too_extreme",
        "stressed_exit_incomplete",
        "entry_quality_below_minimum",
        "projected_reward_does_not_cover_probability_weighted_exit_cost",
    })

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
        reward_selector: RewardMarketSelector | None = None,
        fill_hazard_model: MondayFillHazardModel | None = None,
        limiter: AsyncIntervalLimiter | None = None,
        ui_availability_probe: Callable[[], UiTradingProbe] | None = None,
        operator_entry_pause_path: Path = OPERATOR_ENTRY_PAUSE_PATH,
        maintenance_schedule_probe: Callable[..., MaintenanceScheduleProbe]
        | None = None,
        maintenance_cache_path: Path = MAINTENANCE_CACHE_PATH,
    ):
        self.client = client
        self.config = config
        self.ledger = ledger
        self.notifier = notifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self._ui_availability_probe = (
            ui_availability_probe or probe_polymarket_us_homepage)
        self._operator_entry_pause_path = operator_entry_pause_path
        self._manual_entry_paused = False
        self._ui_entry_paused = False
        self._ui_availability_initialized = False
        self._ui_availability_state = UiTradingState.UNKNOWN
        self._ui_availability_detail = "not_checked"
        self._ui_healthy_streak = 0
        self._next_ui_availability_check_at: datetime | None = None
        self._ui_availability_task: asyncio.Task | None = None
        self._entry_pause_reason: str | None = None
        self._entry_pause_cancel_requested = False
        self._maintenance_schedule_probe = (
            maintenance_schedule_probe
            or probe_polymarket_us_maintenance_schedule)
        self._maintenance_cache_path = maintenance_cache_path
        self._maintenance_windows: tuple[MaintenanceWindow, ...] = (
            load_maintenance_cache(maintenance_cache_path))
        self._maintenance_decision = MaintenanceDecision(
            MaintenancePhase.NORMAL)
        self._maintenance_feed_initialized = False
        self._maintenance_feed_state = MaintenanceFeedState.UNKNOWN
        self._maintenance_feed_detail = "not_checked"
        self._maintenance_feed_observed_at: datetime | None = None
        self._next_maintenance_schedule_check_at: datetime | None = None
        self._maintenance_schedule_task: asyncio.Task | None = None
        self._maintenance_health_required_for: str | None = None
        self._maintenance_schedule_signature: tuple[object, ...] | None = None
        limiter = limiter or AsyncIntervalLimiter.from_rate(
            float(config.rest_requests_per_second))
        self.gateway = EventAwareGateway(
            client, limiter=limiter, ledger=ledger, now=lambda: self._now())
        self.fill_hazard_model = fill_hazard_model or MondayFillHazardModel()
        self.reward_selector = reward_selector
        if self.reward_selector is None and config.auto_select_reward_markets:
            self.reward_selector = RewardMarketSelector(
                self.gateway,
                config.reward_selection_policy(),
                fill_hazard_model=self.fill_hazard_model,
                clock=lambda: self._now(),
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
        # A bound pregame market reaching its positively observed kickoff is a
        # normal lifecycle transition, not a recoverable transport failure.
        # Request one incremental top-up as soon as ownership is flat and clear;
        # the existing near-deadline guard still keeps discovery behind exits.
        self._lifecycle_top_up_requested_at: datetime | None = None
        # A near-deadline position can correctly defer discovery while several
        # finished owners hold slots. Once the final such position becomes
        # flat, waiting behind another serial twenty-market service pass adds
        # avoidable reward downtime. This one-shot request preempts only that
        # ordinary pass; the near-deadline guard and all top-up ownership proof
        # remain unchanged.
        self._post_flat_top_up_requested_at: datetime | None = None
        self._post_flat_underfilled_slots = 0
        # A failed discovery must leave real service time before any lifecycle
        # or post-flat signal can start another scan.  `next_selection` alone
        # is insufficient because event-driven requests deliberately bypass
        # that ordinary cadence; when a catalog scan itself took 60-90 seconds,
        # they restarted it almost immediately and starved order decisions.
        self._top_up_retry_not_before = datetime.min.replace(
            tzinfo=timezone.utc)
        # "Up, healthy, and resting nothing" -- see `_alarm_if_quoting_nothing`.
        self._quiet_since: datetime | None = None
        self._quiet_alarm_sent = False
        # exchange_order_id -> when we last asked the venue about an absence.
        self._absent_asked_at: dict[str, datetime] = {}
        # exchange_order_id -> when we last re-issued a cancel the venue had
        # confirmed did not land. See `_retry_uncertain_cancels`.
        self._cancel_retried_at: dict[str, datetime] = {}
        # intent id -> (consecutive clean account proofs, first proof time).
        # This is deliberately process-local. A restart must collect fresh
        # proofs rather than inheriting a confidence claim from a dead process.
        self._orphan_place_clear_proofs: dict[str, tuple[int, datetime]] = {}
        # market -> when it last placed or cancelled. A market between a
        # cancel and its re-place holds no order for a few seconds and is not
        # stuck; without this it was counted as stuck and the alarm fired on
        # ordinary churn. See `_cycling_markets`.
        self._last_action_at: dict[str, datetime] = {}
        self._market_action_generation: dict[str, int] = {}
        # Recovery delay belongs to the market that requested it. The old
        # sweep let one unhealthy slot pause the other nineteen.
        self._next_decision_at: dict[str, datetime] = {}
        # Event-driven scheduler state.  This changes only which markets enter
        # the existing service/plan/apply functions; it never changes a frame,
        # action, price, quantity, risk gate or mutation path.  Positions stay
        # outside this set because they are selected unconditionally every
        # pass.  Flat markets enter after an exact input change or a due timer.
        self._dirty_market_slugs: set[str] = set()
        self._market_dirty_generation: dict[str, int] = {}
        self._dirty_market_since: dict[str, datetime] = {}
        self._market_last_input_at: dict[str, datetime] = {}
        self._scheduler_last_log_at: datetime | None = None
        self._scheduler_passes = 0
        self._scheduler_markets_seen = 0
        self._scheduler_markets_evaluated = 0
        self._scheduler_markets_skipped = 0
        self._scheduler_position_evaluations = 0
        self._scheduler_service_cpu_seconds = 0.0
        self._scheduler_decision_cpu_seconds = 0.0
        self._scheduler_process_cpu_at_last_log: float | None = None
        self._scheduler_event_loop_delays: list[float] = []
        self._scheduler_decision_latencies: list[float] = []
        self._scheduler_action_latencies: list[float] = []
        self._scheduler_entry_signature: tuple[bool | int, ...] | None = None
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
        # Exact bot-owned post-kickoff positions whose venue book was
        # positively returned empty on both sides. They stay inside the
        # authorized/private-stream scope, count against capacity and
        # portfolio risk, and are never quotable. Isolating the known empty
        # owner prevents it from disabling unrelated reward work without ever
        # pretending the position is flat.
        self._settlement_wait_slugs: set[str] = set()
        # Exact bot-owned positions whose freshly fetched market metadata says
        # closed, inactive or another explicit non-open state. The venue cannot
        # accept even a reducing payload in that state. Keep the owner inside
        # account/private monitoring and configured risk, but do not repeatedly
        # manufacture locally rejected IOC intents while waiting for either an
        # authoritative flat snapshot or fresh open metadata.
        self._inactive_market_wait_slugs: set[str] = set()
        self._active_market_slugs: tuple[str, ...] = (
            () if config.auto_select_reward_markets
            else (config.market_slug,))
        for slug in self._active_market_slugs:
            self._markets[slug] = MarketState(slug)
        for slug in self._markets:
            self._mark_market_dirty(slug)
        self._market_selection: RewardMarketSelection | None = None
        self._market_selection_observed_at: datetime | None = None
        self.balance: AccountBalance | None = None
        # Cash-stable fiat + per-position `cashValue` snapshot.  Retail
        # currently returns assetNotional=0 even with open positions, so no
        # safety or reporting consumer may rebuild marked equity from the
        # balance row alone.
        self._equity_snapshot: AccountEquitySnapshot | None = None
        self._live_pnl_cache_error = False
        self._active_scope_cache_error = False
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
        # Settled cash at the ET-day baseline. Reporting measures the change
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
        # Completed rewards, deposits, withdrawals and transfers since the
        # durable cash baseline. Raw cash change is not trading P&L when one
        # of these lands: 2026-08-25 paid $353.28 while trading lost $44.52,
        # making the old session sensor read a fictitious +$308.76. The scan
        # is bounded, cash-only and bracketed by equal balance reads.
        self._nontrading_cash: Decimal | None = None
        self._nontrading_cash_observed_at: datetime | None = None
        self._nontrading_cash_day: date | None = None
        self._cash_activity_boundary: datetime | None = None
        self._day_opening_equity: Decimal | None = None
        # Gross taker fees remain ET-day telemetry. The operator removed fee
        # enforcement after the $10 reference canceled an otherwise healthy
        # 24-slot slate on 2026-08-31. Durable fills reconstruct the notice
        # after restart, but only the loss guards authorize a trading
        # pause/stop.
        self._execution_fee_observation_day: str | None = None
        self._execution_fee_observation_pending: (
            tuple[str, Decimal] | None
        ) = None
        self._execution_fee_observation_announced_day: str | None = None
        # A marked-equity breach may be only a temporary mark on open
        # inventory. It blocks new entries immediately, then waits until at
        # least one position that was open during the breach is positively
        # closed before accepting a recovered mark. The venue exposes only one
        # account-wide equity number, so it cannot name one culprit position.
        # A confirmed HARD breach latches the day and evacuates every remaining
        # position; complete account flatness is required before next-day entry.
        self._provisional_hard_stop_at: datetime | None = None
        self._provisional_hard_stop_markets: set[str] = set()
        self._provisional_hard_stop_notice: str | None = None
        self._provisional_hard_stop_announced = False
        self._provisional_hard_stop_day: date | None = None
        provisional = self.ledger.query(
            "SELECT risk_date,first_tripped_at,markets_json "
            "FROM execution_provisional_hard_stop WHERE singleton=1")
        if provisional:
            row = provisional[0]
            self._provisional_hard_stop_day = date.fromisoformat(row["risk_date"])
            self._provisional_hard_stop_at = datetime.fromisoformat(row["first_tripped_at"])
            markets = json.loads(row["markets_json"])
            if (not isinstance(markets, list) or not markets
                    or any(not isinstance(s, str) or not s for s in markets)
                    or self._provisional_hard_stop_at.tzinfo is None):
                raise RuntimeInvariantViolation(
                    "provisional_hard_stop_invalid", "durable HARD cohort is malformed")
            self._provisional_hard_stop_markets = set(markets)
            self._provisional_hard_stop_notice = "started"
        holds = self.ledger.query(
            "SELECT risk_date,first_tripped_at FROM execution_hard_stop_holds "
            "WHERE released_at IS NULL ORDER BY risk_date LIMIT 1")
        self._hard_stop_hold_day = (
            date.fromisoformat(str(holds[0]["risk_date"])) if holds else None)
        self._hard_stop_hold_at = (
            datetime.fromisoformat(str(holds[0]["first_tripped_at"]))
            if holds else None)
        self._hard_stop_hold_notice: str | None = "held" if holds else None
        self._transport_recovery_component: str | None = None
        self._market_fault_pause_until: datetime | None = None
        # Footer identity, so an alert says which box and which build produced
        # it without the operator having to ask.
        self._host_name = socket.gethostname()
        try:
            self._deployed_commit = (
                Path("DEPLOYED_COMMIT").read_text(
                    encoding="utf-8").splitlines()[0].strip() or None)
        except Exception:
            self._deployed_commit = None
        self.cutoff_at: datetime | None = None
        self.risk_guard: SessionRiskGuard | None = None
        # The operator wants progressively fewer flat-market entry orders as
        # the ET-day marked loss consumes HARD. This ratchets only downward
        # within one risk day; reducers and all position-owned orders are
        # deliberately outside the count.
        self._loss_entry_order_cap_day: date | None = None
        self._loss_entry_order_cap = config.full_entry_order_capacity
        self._loss_entry_order_cap_notice: (
            tuple[date, int, int, Decimal] | None
        ) = None
        # Independent 00:00-09:00 ET ceiling. It is recomputed from the
        # timezone-aware clock (rather than persisted) and combines with the
        # durable loss ratchet by taking the smaller cap.
        self._overnight_entry_order_cap_active: bool | None = None
        self._overnight_entry_order_cap_notice: (
            tuple[bool, int, int, int] | None
        ) = None
        self.reconciler = DesiredStateReconciler(ReconcilerPolicy(
            quote_size=config.quote_size,
            max_inventory_age_seconds=config.max_inventory_age_seconds,
            # Exposure budgets, not loss stops. These bound how much open worst
            # case may exist at once; the stressed-exit value is a pre-entry
            # economics check and HARD remains in SessionRiskGuard.
            max_market_worst_loss=float(config.max_market_exposure_usd),
            max_portfolio_worst_loss=float(config.max_portfolio_exposure_usd),
            max_exit_loss=float(config.max_stressed_exit_loss_usd),
            entry_quality_policy=config.entry_quality_policy,
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
            small_residual_min_qty=config.small_residual_min_qty,
            small_residual_max_qty=config.small_residual_max_qty,
            small_residual_timeout_seconds=float(
                config.small_residual_timeout_seconds),
        ))
        self._stop_requested: str | None = None
        # Cleared only when shutdown gives up before proving flat, so the
        # process can report exit 3 instead of silently looking clean.
        self._shutdown_proved_flat = True
        self._score_seconds = 0.0
        self._score_seconds_by_market: dict[str, float] = {}
        # Unflushed ET-day increments. Unlike `_score_seconds`, which remains
        # the historical session diagnostic, the Discord value is persisted
        # by reward date so a restart cannot reset "today" to zero.
        self._daily_score_pending: dict[str, float] = {}
        self._daily_score_committed: dict[str, float] = {}
        self._score_observed_at: datetime | None = None
        # Diagnostic only. Wall clocks can step backwards when the host
        # corrects time. Score accrual treats the last accepted timestamp as a
        # high-water mark; this counter makes that containment visible without
        # feeding the condition into market retirement or order decisions.
        self._score_clock_reversals = 0
        self._earnings_observed_at: datetime | None = None
        self._earnings_by_status: dict[str, Decimal] = {}
        # Account-wide but date-scoped to this bot; see `bot_era.py`.
        self._bot_era_rewards: dict[str, Decimal] = {}
        self._orders_before = 0
        self._runtime_error_counts: dict[str, int] = {}
        # First scheduled-summary instant whose otherwise-fresh capital proof
        # was blocked only by current order ambiguity. Shutdown and critical
        # summaries bypass this reporting-only delay.
        self._summary_proof_wait_started_at: datetime | None = None
        self._entry_mutation_blocked_until: datetime | None = None
        self._risk_mutation_blocked_until: datetime | None = None
        self._mutation_access_denial_announced = False
        self._selection_scan_seconds_last: float | None = None
        self._rotation_transition_seconds_last: float | None = None
        # (market, side) -> (label, since). Drives `[eligibility]`. Pruned in
        # `_bind_markets` alongside `_markets`: a market that is dropped and
        # later re-selected must not report the interval it spent UNBOUND as
        # though it had held that state, which would put hour-long `held`
        # values into the one number the log exists to measure.
        self._eligibility_state: dict[tuple[str, str], tuple[str, datetime]] = {}

    def request_stop(self, reason: str = "operator_requested") -> None:
        first_request = self._stop_requested is None
        self._stop_requested = str(reason or "operator_requested")
        if first_request:
            # A shared cooldown used to sleep inside a worker thread for as
            # long as 60 seconds. SIGTERM could set this flag but the run loop
            # could not reach shutdown until that sleep ended. Interrupt only
            # waits that already began; shutdown captures a fresh generation
            # and retains full access to cancels, proof reads and IOC exits.
            interrupt = getattr(self.gateway.limiter, "interrupt_waits", None)
            if callable(interrupt):
                interrupt()

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
        stranded = sorted(
            slug for slug in self._isolated_unexecutable_slugs()
            if self.ledger.position(slug) != 0 and slug not in clean)
        if stranded:
            raise RuntimeInvariantViolation(
                "unexecutable_wait_owner_dropped",
                "active scope cannot drop a nonzero unexecutable owner: "
                + ", ".join(stranded))
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
        self._market_action_generation = {
            slug: value
            for slug, value in self._market_action_generation.items()
            if slug in self._markets}
        self._priority_replan_slugs.intersection_update(self._markets)
        self._dirty_market_slugs.intersection_update(self._markets)
        self._market_dirty_generation = {
            slug: value
            for slug, value in self._market_dirty_generation.items()
            if slug in self._markets}
        for slug in self._markets:
            self._mark_market_dirty(slug)
        self._dirty_market_since = {
            slug: value for slug, value in self._dirty_market_since.items()
            if slug in self._markets}
        self._market_last_input_at = {
            slug: value for slug, value in self._market_last_input_at.items()
            if slug in self._markets}
        self._publish_active_scope_snapshot()

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

    def _unresolved_places_without_exchange_id(self) -> tuple[Any, ...]:
        """Return creates that have no safe order-specific recovery path."""

        return tuple(
            row for row in self.ledger.unresolved_intents()
            if str(row["operation"]).upper() == "PLACE"
            and str(row["state"]).upper() == "UNCERTAIN"
            and not str(row["exchange_order_id"] or "").strip()
        )

    async def _advance_orphan_place_clear_proofs(
        self,
        *,
        observed_at: datetime,
        open_orders: tuple[Any, ...],
        positions: tuple[Any, ...],
    ) -> tuple[str, ...]:
        """Terminalize no-id PLACE ambiguity only after repeated flat proof.

        The retail create endpoint does not receive our local client id, so an
        uncertain create has no order-specific lookup key and may never be
        replayed.  Complete open-order and position snapshots can nevertheless
        prove that no *live risk* survives in its market.  We require three
        consecutive proofs spanning ten seconds, no local order row, and no
        locally observed execution at or after the intent.  Any contrary fact
        resets that intent's proof.  The terminal FAILED label means the
        mutation is abandoned, not that its original HTTP outcome is known.
        """

        candidates = self._unresolved_places_without_exchange_id()
        candidate_ids = {str(row["intent_id"]) for row in candidates}
        self._orphan_place_clear_proofs = {
            intent_id: proof
            for intent_id, proof in self._orphan_place_clear_proofs.items()
            if intent_id in candidate_ids
        }
        if not candidates:
            return ()

        live_order_markets = {
            str(order.market_slug) for order in open_orders
            if str(order.market_slug).strip()
        }
        held_markets = {
            str(position.market_slug) for position in positions
            if position.net_qty != 0
        }
        resolved: list[str] = []
        for row in candidates:
            intent_id = str(row["intent_id"])
            market = str(row["market_slug"] or "").strip()
            client_order_id = str(row["client_order_id"] or "").strip()
            created_at = datetime.fromisoformat(str(row["created_at"]))
            local_order = self.ledger.query(
                "SELECT 1 FROM execution_orders WHERE client_order_id=? LIMIT 1",
                (client_order_id,),
            )
            later_execution = self.ledger.query(
                "SELECT 1 FROM execution_fills WHERE market_slug=? "
                "AND event_at>=? LIMIT 1",
                (market, created_at.isoformat()),
            )
            blocked = (
                not market
                or bool(local_order)
                or market in live_order_markets
                or market in held_markets
                or bool(later_execution)
            )
            if blocked:
                self._orphan_place_clear_proofs.pop(intent_id, None)
                continue

            count, first_at = self._orphan_place_clear_proofs.get(
                intent_id, (0, observed_at))
            count += 1
            self._orphan_place_clear_proofs[intent_id] = (count, first_at)
            if (count < ORPHAN_PLACE_CLEAR_PROOFS
                    or (observed_at - first_at).total_seconds()
                    < ORPHAN_PLACE_CLEAR_MIN_SECONDS):
                continue

            self.ledger.transition_intent(
                intent_id,
                "FAILED",
                updated_at=observed_at,
                error=(
                    "automatic recovery abandoned no-id PLACE after repeated "
                    "complete account proofs found no live order or position; "
                    "the original create outcome remains unknown"),
            )
            self._orphan_place_clear_proofs.pop(intent_id, None)
            resolved.append(intent_id)
            self._mark_market_dirty(market, observed_at)
            await self.notifier.send_async(
                "Liquidity Bot safely retired an unresolved PLACE without an "
                f"exchange order id in {market} after repeated complete "
                "order/position proofs found no surviving venue risk. The "
                "uncertain create was not retried."
            )
        return tuple(resolved)

    def _log_selection_scan(self, selection: RewardMarketSelection) -> None:
        """Expose discovery headroom once for every successful full scan."""

        elapsed = (
            f"{self._selection_scan_seconds_last:.1f}"
            if self._selection_scan_seconds_last is not None else "unknown")
        print(
            f"[selection-scan] at={self._now().isoformat()} "
            f"event_pages={getattr(selection, 'event_pages_used', 0)} "
            f"event_page_ceiling={getattr(self.gateway, 'event_max_pages', 0)} "
            f"event_markets={getattr(selection, 'catalog_markets', 0)} "
            f"executable={getattr(selection, 'executable_catalog_markets', 0)} "
            f"rewarded={getattr(selection, 'rewarded_executable_markets', 0)} "
            f"books_ranked={getattr(selection, 'books_evaluated', 0)} "
            f"viable={getattr(selection, 'viable_markets', 0)} "
            f"scan_seconds={elapsed}",
            flush=True,
        )

    async def _select_reward_market(self, now: datetime) -> str:
        """Wait for a viable reward market rather than refusing to start.

        A universe with nothing viable right now is the normal case, not a
        misconfiguration: economically authorized windows are short and
        infrequent. Refusing startup returns exit 2, which the supervisor
        treats as terminal, so the process would stay down until an operator
        noticed. Waiting costs nothing, because a runner with no bound market
        holds no orders, no position and no stream subscription.

        Only an operator stop, HARD, or a genuine adapter failure ends
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
            self._log_selection_scan(selection)
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
        accept_bound_lifecycle_end: bool = False,
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
        market = await self.gateway.fetch_market(slug)
        incentives = await self.gateway.fetch_incentives([slug])
        self._apply_contracts(
            state,
            market=market,
            incentive=incentives.get(slug),
            now=self._now(),
            for_exit=for_exit,
            accept_bound_lifecycle_end=accept_bound_lifecycle_end,
        )

    @staticmethod
    def _market_metadata_proves_not_open(market: MarketMetadata | None) -> bool:
        """Return true only for an explicit response-backed lifecycle state."""

        if market is None:
            return False
        status = getattr(market, "status", None)
        return (
            getattr(market, "closed", False) is True
            or getattr(market, "active", True) is False
            or (bool(status) and status not in OPEN_MARKET_STATUSES)
        )

    def _request_lifecycle_top_up(self, now: datetime) -> None:
        """Schedule one incremental scan without authorizing a slot release."""

        if self._lifecycle_top_up_requested_at is not None:
            return
        self._lifecycle_top_up_requested_at = now
        print(
            f"[top-up-request] at={now.isoformat()} reason=lifecycle",
            flush=True,
        )

    def _contract_refresh_delay(self, slug: str) -> float:
        """Back off repeated unknown refresh failures without inferring closure."""

        attempt = self._runtime_error_counts.get(
            f"market_incentive_refresh:{slug}", 0)
        if attempt < MAX_RECOVERY_ATTEMPTS:
            return CONTRACT_REFRESH_SECONDS
        exponent = min(attempt - MAX_RECOVERY_ATTEMPTS + 1, 4)
        return min(
            CONTRACT_REFRESH_SECONDS * (2 ** exponent),
            CONTRACT_REFRESH_MAX_BACKOFF_SECONDS,
        )

    def _apply_contracts(
        self,
        state: MarketState,
        *,
        market: MarketMetadata,
        incentive: MarketIncentive | None,
        now: datetime,
        for_exit: bool,
        accept_bound_lifecycle_end: bool = False,
    ) -> None:
        """Apply already-fetched rows with the original per-market gates."""

        slug = state.slug
        if market.slug != slug:
            raise CommissioningUnavailable(
                f"contract batch returned {market.slug} for {slug}")
        state.market = market
        state.incentive = incentive
        if self._market_metadata_proves_not_open(market):
            state.incentive = None
            state.reward_eligible = frozenset()
            state.clear_economics()
            # A positioned owner cannot release its slot while the contract is
            # non-open.  Isolate that exact risk before requesting replacement
            # work; otherwise every scheduled metadata probe starts another
            # full discovery scan even though capacity cannot change.  Flat
            # owners still request the immediate lifecycle top-up below, and
            # _sync_inactive_market_wait_positions requests it when positive
            # account evidence later proves this position flat.
            if self._enter_inactive_market_wait(slug, now):
                return
            if accept_bound_lifecycle_end:
                self._request_lifecycle_top_up(now)
            if for_exit:
                return
            raise _ConfirmedMarketNotOpen(f"{slug} is not open")
        self._leave_inactive_market_wait(slug, now, "market_open")
        if for_exit:
            return
        if state.incentive is None:
            raise CommissioningUnavailable(
                f"{slug} has no liquidityProgram incentive")
        if state.market.game_start is None:
            raise CommissioningUnavailable(
                f"{slug} start clock is unavailable")
        if state.market.game_start <= now:
            if accept_bound_lifecycle_end:
                state.incentive = None
                state.reward_eligible = frozenset()
                state.clear_economics()
                self._request_lifecycle_top_up(now)
                return
            raise CommissioningUnavailable(f"{slug} is no longer pregame")
        if not state.market.event_allows_entry(now):
            state.incentive = None
            state.reward_eligible = frozenset()
            state.clear_economics()
            raise CommissioningUnavailable(f"{slug} event pregame state is unavailable")
        try:
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now)
        except AmbiguousIncentiveError as exc:
            raise CommissioningUnavailable(
                f"{slug} has ambiguous incentive periods") from exc
        if period is None:
            prior = next((
                item for item in state.incentive.periods
                if item.program_id == state.reward_program_id
            ), None)
            if prior is not None:
                explicitly_ended = (
                    prior.ends_at is not None and prior.ends_at <= now)
                newly_confirmed = (
                    state.reward_authorization_loss_confirmed_at is None
                    or state.reward_authorization_loss_reason
                    != ("program_ended" if explicitly_ended
                        else "no_longer_applicable"))
                confirmed_at = self._now()
                state.reward_authorization_lost_at = (
                    prior.ends_at if explicitly_ended else now)
                state.reward_authorization_loss_reason = (
                    "program_ended" if explicitly_ended
                    else "no_longer_applicable")
                state.reward_authorization_loss_confirmed_at = confirmed_at
                if explicitly_ended:
                    state.reward_period_ended_at = prior.ends_at
                    state.reward_period_end_confirmed_at = confirmed_at
                if newly_confirmed:
                    self._reward_end_top_up_requested = True
                # Preserve the exact row above as proof, but never leave it in
                # the strategy field: it does not authorize an entry now.
                state.incentive = None
                state.clear_economics()
                return
        if period is None or period.period == "live":
            raise CommissioningUnavailable(
                f"{slug} has no applicable pregame liquidity period")
        state.reward_program_id = period.program_id
        state.reward_program_ends_at = period.ends_at
        state.reward_period_ended_at = None
        state.reward_period_end_confirmed_at = None
        state.reward_authorization_lost_at = None
        state.reward_authorization_loss_reason = None
        state.reward_authorization_loss_confirmed_at = None

    async def _refresh_due_contract_batches(
        self, now: datetime, slugs: tuple[str, ...],
    ) -> None:
        """Bulk-refresh due owners without widening one failure past 8 slots."""

        due_before = now + timedelta(
            seconds=CONTRACT_REFRESH_LOOKAHEAD_SECONDS)
        due = [
            slug for slug in slugs
            if slug in self._markets
            and (
                self.state_for(slug).next_contract_refresh is None
                or self.state_for(slug).next_contract_refresh <= due_before
            )
        ]
        for offset in range(0, len(due), CONTRACT_REFRESH_BATCH_SIZE):
            if self._stop_requested:
                return
            chunk = tuple(due[offset:offset + CONTRACT_REFRESH_BATCH_SIZE])
            try:
                markets = await self.gateway.fetch_markets_by_slugs(chunk)
                incentives = await self.gateway.fetch_incentives(chunk)
            except Exception as exc:
                for slug in chunk:
                    state = self.state_for(slug)
                    state.incentive = None
                    state.reward_eligible = frozenset()
                    await self._report_runtime_error(
                        f"market_incentive_refresh:{slug}", exc)
                    state.next_contract_refresh = self._now() + timedelta(
                        seconds=self._contract_refresh_delay(slug))
                continue
            completed_at = self._now()
            for slug in chunk:
                state = self.state_for(slug)
                try:
                    market = markets.get(slug)
                    if market is None:
                        raise CommissioningUnavailable(
                            f"{slug} is absent from the complete contract batch")
                    self._apply_contracts(
                        state,
                        market=market,
                        incentive=incentives.get(slug),
                        now=completed_at,
                        for_exit=self._is_barred_from_quoting(slug),
                        accept_bound_lifecycle_end=True,
                    )
                    self._runtime_error_counts.pop(
                        f"market_incentive_refresh:{slug}", None)
                except _ConfirmedMarketNotOpen:
                    # Positive lifecycle evidence schedules a top-up above. It
                    # is not a failed API read and must not consume the error
                    # budget or be labeled as a Discord runtime failure.
                    state.incentive = None
                    state.reward_eligible = frozenset()
                    self._runtime_error_counts.pop(
                        f"market_incentive_refresh:{slug}", None)
                except Exception as exc:
                    state.incentive = None
                    state.reward_eligible = frozenset()
                    await self._report_runtime_error(
                        f"market_incentive_refresh:{slug}", exc)
                state.next_contract_refresh = self._now() + timedelta(
                    seconds=(
                        CONTRACT_REFRESH_SECONDS
                        if f"market_incentive_refresh:{slug}"
                        not in self._runtime_error_counts
                        else self._contract_refresh_delay(slug)))

    async def _refresh_book(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        """Refresh market data without imposing entry-only requirements.

        Book acquisition is shared by entry, reduction, recovery and shutdown.
        A one-sided book can still price the side needed to reduce inventory,
        and a closed market can still need servicing while its position is
        unsettled.  Entry payload validation therefore belongs at the startup
        entry boundary, not in this generic refresh path.
        """

        state = self.state_for(slug or self.market_slug)
        state.book = await self.gateway.fetch_book(state.slug)
        # Stamp receipt, not request start. REST and limiter latency can be a
        # material fraction of the five-second decision freshness budget.
        state.book_observed_at = self._now()
        state.observe_book_touch(
            state.book, observed_at=state.book_observed_at)
        self._leave_settlement_wait(
            state.slug, state.book_observed_at, "book_available")
        self._mark_market_dirty(state.slug, state.book_observed_at)
        if state.market is None:
            # Entry validation is separate, but a book without matching
            # contract identity is still not usable by any decision path.
            raise RuntimeError("market metadata is unavailable")

    def _validate_entry_book(self, slug: str) -> None:
        """Validate every priceable entry side before startup authorization.

        Missing sides are not fabricated and are not dereferenced.  The
        reconciler already refuses building risk until a book is two-sided,
        while recovery and exits may legitimately use the one side present.
        """

        state = self.state_for(slug)
        if state.market is None:
            raise RuntimeError("market metadata is unavailable")
        if state.book is None:
            raise RuntimeError("market book is unavailable")
        if not state.book.bids and not state.book.offers:
            raise OrderValidationError("book is empty on both sides")
        # Validate both possible passive wire payloads before the first
        # private subscription can authorize mutation, but only when that side
        # exists. One-sided books are valid recovery inputs and cannot safely
        # be dereferenced through min()/max() on an empty collection.
        size = self.config.quote_size
        if state.book.bids:
            build_order_payload(OrderRequest(
                state.market.slug, "bid",
                Decimal(str(state.book.best_bid.price)), size), state.market)
        if state.book.offers:
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
        self._leave_settlement_wait(
            book.slug, observed_at, "stream_book_available")
        self._mark_market_dirty(book.slug, observed_at)

    def _mark_market_dirty(
        self, slug: str, observed_at: datetime | None = None,
    ) -> None:
        """Schedule one owner after a decision input changed.

        Timestamps are telemetry only. They never decide whether an owner is
        authorized, and malformed/backward observations are simply excluded
        from latency measurement rather than from trading.
        """

        slug = str(slug or "").strip()
        if not slug:
            return
        self._dirty_market_slugs.add(slug)
        self._market_dirty_generation[slug] = (
            self._market_dirty_generation.get(slug, 0) + 1)
        if observed_at is None:
            return
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return
        previous = self._dirty_market_since.get(slug)
        if previous is None or observed_at < previous:
            self._dirty_market_since[slug] = observed_at
        latest = self._market_last_input_at.get(slug)
        if latest is None or observed_at > latest:
            self._market_last_input_at[slug] = observed_at

    @staticmethod
    def _private_event_market_slugs(event: Any) -> frozenset[str]:
        """Extract exact owners named by one already-decoded private event."""

        slugs = {
            str(slug).strip() for slug in (getattr(event, "market_scope", None)
                                           or ())
            if str(slug).strip()
        }
        payload = getattr(event, "payload", None)

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key in ("marketSlug", "market_slug"):
                    slug = str(value.get(key) or "").strip()
                    if slug:
                        slugs.add(slug)
                positions = value.get("positions")
                if isinstance(positions, dict):
                    slugs.update(
                        str(slug).strip() for slug in positions
                        if str(slug).strip())
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(payload)
        return frozenset(slugs)

    def _execution_owner_slugs(self) -> frozenset[str]:
        slugs = {
            str(row["market_slug"])
            for row in self.ledger.working_orders()
        }
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        )
        slugs.discard("")
        return frozenset(slugs)

    def _entry_scheduler_signature(
        self, now: datetime,
    ) -> tuple[bool | int, ...]:
        """Global entry authority, deliberately excluding buying power."""

        today = date.fromisoformat(risk_day(now))
        risk_allowed = False
        if self.risk_guard is not None:
            try:
                risk_allowed = self.risk_guard.can_add_new_risk(now=now)
            except Exception:
                risk_allowed = False
        try:
            session_ready = self.gate.session_ready(self.ledger, now=now)
        except Exception:
            session_ready = False
        return (
            risk_allowed,
            self._provisional_hard_stop_at is None,
            self._baseline_day == today,
            self._nontrading_cash is not None,
            self._nontrading_cash_day == today,
            session_ready,
            not self._entry_pause_active(),
            self._effective_entry_order_cap(now),
        )

    def _entry_pause_active(self) -> bool:
        """Whether new risk is blocked without affecting reducers or exits."""

        return (
            self._manual_entry_paused
            or self._hard_stop_hold_day is not None
            or self._transport_recovery_component is not None
            or self._market_fault_pause_until is not None
            or self._scheduled_maintenance_pause_active()
            or (self._ui_availability_initialized and self._ui_entry_paused)
        )

    def _scheduled_maintenance_pause_active(self) -> bool:
        """Fail closed until the official schedule has initialized."""

        return (
            not self._maintenance_feed_initialized
            or self._maintenance_decision.paused
            or self._maintenance_health_required_for is not None
        )

    def _scheduled_maintenance_flatten_required(self) -> bool:
        """Flatten only for a positively identified current window."""

        return (
            self._maintenance_decision.paused
            or self._maintenance_health_required_for is not None
        )

    def _maintenance_resume_feed_is_fresh(self) -> bool:
        """Require a successful schedule read at or after the hold boundary."""

        if (self._maintenance_feed_state is not MaintenanceFeedState.HEALTHY
                or self._maintenance_feed_observed_at is None
                or self._maintenance_health_required_for is None):
            return False
        window = next((
            item for item in self._maintenance_windows
            if item.maintenance_id == self._maintenance_health_required_for
        ), None)
        resume_at = window.resume_at if window is not None else None
        return (
            resume_at is not None
            and self._maintenance_feed_observed_at >= resume_at
        )

    def _current_entry_pause_reason(self) -> str | None:
        if self._hard_stop_hold_day is not None:
            return "daily_hard_stop_flatten_and_wait"
        if self._transport_recovery_component is not None:
            return f"transport_recovery:{self._transport_recovery_component}"
        if self._market_fault_pause_until is not None:
            return "market_fault_cascade_recovery"
        if self._manual_entry_paused:
            return "operator"
        if self._maintenance_decision.phase == MaintenancePhase.EVACUATE:
            return "scheduled_maintenance_evacuation"
        if self._maintenance_decision.phase == MaintenancePhase.MAINTENANCE:
            return "scheduled_maintenance"
        if self._maintenance_decision.phase == MaintenancePhase.RESUME_HOLD:
            return "scheduled_maintenance_resume_hold"
        if self._maintenance_health_required_for is not None:
            return "scheduled_maintenance_health_gate"
        if not self._maintenance_feed_initialized:
            return "polymarket_status_schedule_unavailable"
        if self._ui_availability_initialized and self._ui_entry_paused:
            if self._ui_availability_state == UiTradingState.MAINTENANCE:
                return "polymarket_us_ui_maintenance"
            if self._ui_availability_state == UiTradingState.HEALTHY:
                return "polymarket_us_ui_recovery_pending"
            return "polymarket_us_ui_unavailable"
        return None

    def _apply_maintenance_schedule(
        self, result: MaintenanceScheduleProbe, now: datetime,
    ) -> None:
        """Apply only a complete official-feed observation."""

        if not isinstance(result, MaintenanceScheduleProbe):
            result = MaintenanceScheduleProbe(
                MaintenanceFeedState.UNKNOWN, self._maintenance_windows,
                "invalid_probe_result", now)
        self._maintenance_feed_state = result.state
        self._maintenance_feed_detail = result.detail
        self._maintenance_feed_observed_at = result.observed_at
        if result.state is not MaintenanceFeedState.HEALTHY:
            return
        self._maintenance_feed_initialized = True
        self._maintenance_windows = result.windows
        self._maintenance_decision = maintenance_decision(
            result.windows, now)
        window = self._maintenance_decision.window
        if self._maintenance_decision.paused and window is not None:
            self._maintenance_health_required_for = window.maintenance_id

    def _log_maintenance_schedule_state(self, now: datetime) -> None:
        window = self._maintenance_decision.window
        signature: tuple[object, ...] = (
            self._maintenance_feed_state,
            self._maintenance_decision.phase,
            window.maintenance_id if window else None,
            window.status if window else None,
            self._maintenance_health_required_for,
        )
        if signature == self._maintenance_schedule_signature:
            return
        self._maintenance_schedule_signature = signature
        fields = [
            f"[maintenance-schedule] at={now.isoformat()}",
            f"feed={self._maintenance_feed_state.value}",
            f"phase={self._maintenance_decision.phase.value}",
            f"detail={self._maintenance_feed_detail}",
        ]
        if window is not None:
            fields.extend((
                f"id={window.maintenance_id}",
                f"status={window.status}",
                f"evacuate_at={window.evacuation_at.isoformat()}",
                f"scheduled_for={window.scheduled_for.isoformat()}",
                f"scheduled_until={window.scheduled_until.isoformat()}",
                f"resume_at={window.resume_at.isoformat() if window.resume_at else 'unknown'}",
            ))
        if self._maintenance_health_required_for is not None:
            fields.append(
                f"health_required_for={self._maintenance_health_required_for}")
        print(" ".join(fields), flush=True)

    async def _refresh_maintenance_schedule(
        self, now: datetime, *, startup: bool = False,
    ) -> None:
        """Poll Statuspage off-loop and hold until post-window health proof."""

        cache_result: MaintenanceScheduleProbe | None = None
        previous_reason = self._current_entry_pause_reason()
        if (startup
                and previous_reason == "polymarket_status_schedule_unavailable"):
            # The uninitialized constructor state is a mutation guard, not a
            # real transition. A healthy no-window startup should be silent;
            # a known window or failed first read still enters pause from None
            # and requests prompt quote withdrawal.
            previous_reason = None
        # Time crossing a cached boundary is sufficient to tighten behavior;
        # it must not wait for another successful network response. A failed
        # poll can never postpone a known evacuation or clear a known hold.
        cached = maintenance_decision(self._maintenance_windows, now)
        self._maintenance_decision = cached
        if cached.paused and cached.window is not None:
            self._maintenance_health_required_for = (
                cached.window.maintenance_id)
        if startup:
            try:
                result = await asyncio.to_thread(
                    self._maintenance_schedule_probe,
                    self._maintenance_windows,
                    include_history=True,
                    now=now,
                )
            except Exception as exc:
                result = MaintenanceScheduleProbe(
                    MaintenanceFeedState.UNKNOWN, self._maintenance_windows,
                    type(exc).__name__, now)
            self._apply_maintenance_schedule(result, now)
            if result.state is MaintenanceFeedState.HEALTHY:
                cache_result = result
            self._next_maintenance_schedule_check_at = now + timedelta(
                seconds=MAINTENANCE_SCHEDULE_POLL_SECONDS)
        else:
            task = self._maintenance_schedule_task
            if task is not None and task.done():
                self._maintenance_schedule_task = None
                try:
                    result = task.result()
                except Exception as exc:
                    result = MaintenanceScheduleProbe(
                        MaintenanceFeedState.UNKNOWN,
                        self._maintenance_windows,
                        type(exc).__name__, now)
                self._apply_maintenance_schedule(result, now)
                if result.state is MaintenanceFeedState.HEALTHY:
                    cache_result = result
                self._next_maintenance_schedule_check_at = now + timedelta(
                    seconds=MAINTENANCE_SCHEDULE_POLL_SECONDS)
            due = self._next_maintenance_schedule_check_at
            if self._maintenance_schedule_task is None and (
                    due is None or now >= due):
                self._maintenance_schedule_task = asyncio.create_task(
                    asyncio.to_thread(
                        self._maintenance_schedule_probe,
                        self._maintenance_windows,
                        include_history=False,
                        now=now,
                    ))

        if cache_result is not None:
            try:
                await asyncio.to_thread(
                    write_maintenance_cache,
                    cache_result.windows,
                    cache_result.observed_at,
                    self._maintenance_cache_path,
                )
            except Exception as exc:
                # The in-memory official observation remains valid. Cache
                # failure reduces restart resilience but authorizes nothing.
                print(
                    f"[maintenance-schedule] at={now.isoformat()} "
                    f"cache_write=failed error={type(exc).__name__}",
                    flush=True,
                )

        # A live process that entered any phase of a known window requires a
        # fresh complete schedule observation after the hold, then the same
        # three authenticated account reads used for venue-outage recovery.
        if (self._maintenance_decision.phase is MaintenancePhase.NORMAL
                and self._maintenance_health_required_for is not None):
            if self._maintenance_resume_feed_is_fresh():
                stop = await self._wait_for_venue_recovery(
                    "scheduled_maintenance_resume",
                    RuntimeError("scheduled maintenance hold completed"),
                    planned=True,
                )
                if stop is None:
                    self._maintenance_health_required_for = None
        self._log_maintenance_schedule_state(now)
        await self._publish_entry_pause_transition(previous_reason, now)

    async def _publish_entry_pause_transition(
        self, previous_reason: str | None, now: datetime,
    ) -> None:
        reason = self._current_entry_pause_reason()
        self._entry_pause_reason = reason
        if reason == previous_reason:
            return
        if reason is not None and previous_reason is not None:
            self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
            print(
                f"[entry-pause] at={now.isoformat()} state=paused "
                f"reason={reason} ui={self._ui_availability_state.value} "
                f"detail={self._ui_availability_detail}",
                flush=True,
            )
            return
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, now)
        paused = reason is not None
        if paused:
            self._entry_pause_cancel_requested = True
        self._publish_active_scope_snapshot(
            status="ENTRY_PAUSED" if paused else "RUNNING")
        if paused:
            print(
                f"[entry-pause] at={now.isoformat()} state=paused "
                f"reason={reason} ui={self._ui_availability_state.value} "
                f"detail={self._ui_availability_detail}",
                flush=True,
            )
            await self.notifier.send_async(
                "⏸️ Liquidity Bot paused NEW ENTRIES while keeping cancels "
                f"and position exits live. Reason: {reason}.")
        else:
            print(
                f"[entry-pause] at={now.isoformat()} state=resumed "
                f"ui={self._ui_availability_state.value} "
                f"healthy_streak={self._ui_healthy_streak}",
                flush=True,
            )
            await self.notifier.send_async(
                "▶️ Liquidity Bot new entries resumed after the entry-pause "
                "gate returned healthy.")

    def _apply_ui_availability(self, result: UiTradingProbe) -> None:
        if not isinstance(result, UiTradingProbe):
            result = UiTradingProbe(
                UiTradingState.UNKNOWN, "invalid_probe_result")
        was_initialized = self._ui_availability_initialized
        self._ui_availability_initialized = True
        self._ui_availability_state = result.state
        self._ui_availability_detail = result.detail
        if result.state == UiTradingState.MAINTENANCE:
            self._ui_entry_paused = True
            self._ui_healthy_streak = 0
        elif result.state == UiTradingState.HEALTHY:
            if self._ui_entry_paused:
                self._ui_healthy_streak += 1
                if self._ui_healthy_streak >= UI_AVAILABILITY_STABLE_READS:
                    self._ui_entry_paused = False
            else:
                # A clean first startup proof need not impose an artificial
                # two-minute delay.  The three-read rule applies after a pause.
                self._ui_healthy_streak = max(1, self._ui_healthy_streak + 1)
        else:
            # Unknown can never clear an existing pause.  At startup it fails
            # closed because no positive UI participation proof exists yet.
            if not was_initialized:
                self._ui_entry_paused = True
            self._ui_healthy_streak = 0

    async def _refresh_entry_pause(
        self, now: datetime, *, startup: bool = False,
    ) -> None:
        """Poll the local latch promptly and the public UI off the event loop."""

        previous_reason = self._current_entry_pause_reason()
        self._manual_entry_paused = operator_entry_pause_requested(
            self._operator_entry_pause_path)

        if startup:
            try:
                result = await asyncio.to_thread(self._ui_availability_probe)
            except Exception as exc:
                result = UiTradingProbe(
                    UiTradingState.UNKNOWN, type(exc).__name__)
            self._apply_ui_availability(result)
            self._next_ui_availability_check_at = now + timedelta(
                seconds=UI_AVAILABILITY_POLL_SECONDS)
        else:
            task = self._ui_availability_task
            if task is not None and task.done():
                self._ui_availability_task = None
                try:
                    result = task.result()
                except Exception as exc:
                    result = UiTradingProbe(
                        UiTradingState.UNKNOWN, type(exc).__name__)
                self._apply_ui_availability(result)
                self._next_ui_availability_check_at = now + timedelta(
                    seconds=UI_AVAILABILITY_POLL_SECONDS)
            due = self._next_ui_availability_check_at
            if self._ui_availability_task is None and (
                    due is None or now >= due):
                self._ui_availability_task = asyncio.create_task(
                    asyncio.to_thread(self._ui_availability_probe))
        await self._publish_entry_pause_transition(previous_reason, now)

    def _take_entry_pause_cancel_slugs(self) -> tuple[str, ...]:
        """Return exact flat owners whose known quotes need prompt withdrawal."""

        if not self._entry_pause_cancel_requested:
            return ()
        self._entry_pause_cancel_requested = False
        working = {
            str(row["market_slug"]) for row in self.ledger.working_orders()
            if row["market_slug"]
        }
        return tuple(
            slug for slug in self.active_slugs
            if (slug in working
                and slug in self._markets
                and self.ledger.position(slug) == 0)
        )

    def _scheduler_owner_sets(
        self,
    ) -> tuple[tuple[str, ...], frozenset[str], frozenset[str]]:
        """Read all non-selection owners once, preserving selection order."""

        active_order_states = tuple(sorted(ACTIVE_ORDER_STATES))
        unresolved_states = tuple(sorted(UNRESOLVED_INTENTS))
        active_marks = ",".join("?" for _ in active_order_states)
        unresolved_marks = ",".join("?" for _ in unresolved_states)
        rows = self.ledger.query(
            "SELECT market_slug,'working' owner_kind "
            "FROM execution_orders WHERE status IN "
            f"({active_marks}) UNION ALL "
            "SELECT market_slug,'unresolved' owner_kind "
            "FROM execution_intents WHERE state IN "
            f"({unresolved_marks}) UNION ALL "
            "SELECT market_slug,'position' owner_kind "
            "FROM execution_positions WHERE CAST(net_qty AS REAL)!=0",
            (*active_order_states, *unresolved_states),
        )
        working = frozenset(
            str(row["market_slug"]) for row in rows
            if row["owner_kind"] == "working" and row["market_slug"])
        positions = frozenset(
            str(row["market_slug"]) for row in rows
            if row["owner_kind"] == "position" and row["market_slug"])
        nonselection = {
            str(row["market_slug"]) for row in rows if row["market_slug"]}
        active = self.active_slugs
        extras = tuple(sorted(nonselection.difference(active)))
        return (*active, *extras), positions, working

    def _mark_global_scheduler_transitions(self, now: datetime) -> None:
        """Wake flat owners only when global authorization changes state."""

        signature = self._entry_scheduler_signature(now)
        previous = self._scheduler_entry_signature
        self._scheduler_entry_signature = signature
        if previous is None or previous == signature:
            return
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, now)

    def _scheduled_market_slugs(
        self, now: datetime, *, record_pass: bool = True,
    ) -> tuple[str, ...]:
        """Return owners whose existing decision logic must run this pass."""

        self._mark_global_scheduler_transitions(now)
        managed, positions, working = self._scheduler_owner_sets()
        due_before = now + timedelta(
            seconds=CONTRACT_REFRESH_LOOKAHEAD_SECONDS)
        selected: list[str] = []
        isolated = self._isolated_unexecutable_slugs()
        for slug in managed:
            if slug not in self._markets:
                # Risk discovered outside selection is exit-only, but it still
                # needs metadata, books and the unchanged reconciler path.
                self.state_for(slug)
            state = self._markets[slug]
            due = slug in self._dirty_market_slugs or (
                slug in positions and slug not in isolated)
            decision_at = self._next_decision_at.get(slug)
            due = due or (decision_at is not None and now >= decision_at)
            due = due or (
                state.next_contract_refresh is None
                or state.next_contract_refresh <= due_before)
            if (slug not in isolated and state.market is not None
                    and state.market.event_guard_required
                    and state.market.event_started_at is None):
                due = due or not state.market.event_allows_entry(now)
            if slug not in self._inactive_market_wait_slugs:
                due = due or (
                    state.next_book_refresh is None
                    or now >= state.next_book_refresh)
            retired = self._retired.get(slug)
            due = due or bool(
                retired is not None
                and (retired.rounds < MAX_RETIREMENT_ROUNDS
                     or slug in positions or slug in working)
                and (retired.next_retry_at is None
                     or now >= retired.next_retry_at))
            if slug in working:
                game_start = getattr(state.market, "game_start", None)
                if game_start is not None:
                    flatten_at = game_start - timedelta(
                        seconds=(
                            self.reconciler.policy
                            .force_exit_before_start_seconds))
                    due = due or now >= flatten_at
            if due:
                selected.append(slug)
        if record_pass:
            self._scheduler_passes += 1
            self._scheduler_markets_seen += len(managed)
            self._scheduler_markets_skipped += len(managed) - len(selected)
            self._scheduler_position_evaluations += sum(
                slug in positions and slug not in isolated
                for slug in selected)
        return tuple(selected)

    @staticmethod
    def _scheduler_percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(
            len(ordered) - 1,
            max(0, math.ceil(len(ordered) * fraction) - 1),
        )
        return ordered[index]

    def _record_scheduler_decision(
        self, slug: str, *, finished_at: datetime, action_at: datetime | None,
    ) -> None:
        started_at = self._dirty_market_since.pop(slug, None)
        if started_at is not None:
            latency = (finished_at - started_at).total_seconds()
            if latency >= 0:
                self._scheduler_decision_latencies.append(latency)
        if action_at is not None:
            input_at = self._market_last_input_at.get(slug)
            if input_at is not None:
                latency = (action_at - input_at).total_seconds()
                if latency >= 0:
                    self._scheduler_action_latencies.append(latency)
        # A later timer-driven action must not be attributed to an old input
        # that was already evaluated. A backoff path never reaches this method,
        # so it deliberately keeps the triggering input until its retry.
        self._market_last_input_at.pop(slug, None)
        for values in (
                self._scheduler_decision_latencies,
                self._scheduler_action_latencies):
            if len(values) > 512:
                del values[:-512]

    def _log_decision_scheduler(self, now: datetime) -> None:
        last = self._scheduler_last_log_at
        if (last is not None
                and (now - last).total_seconds()
                < DECISION_SCHEDULER_LOG_SECONDS):
            return
        process_cpu_now = time.process_time()
        total_cpu_seconds = (
            max(0.0, process_cpu_now - self._scheduler_process_cpu_at_last_log)
            if self._scheduler_process_cpu_at_last_log is not None else 0.0)
        window_seconds = (
            max(0.0, (now - last).total_seconds())
            if last is not None else 0.0)
        cpu_cores = (
            total_cpu_seconds / window_seconds if window_seconds > 0 else 0.0)
        print(
            f"[decision-scheduler] at={now.isoformat()} "
            f"passes={self._scheduler_passes} "
            f"markets_seen={self._scheduler_markets_seen} "
            f"evaluated={self._scheduler_markets_evaluated} "
            f"skipped={self._scheduler_markets_skipped} "
            f"position_evaluated={self._scheduler_position_evaluations} "
            f"service_cpu_ms={self._scheduler_service_cpu_seconds * 1000:.1f} "
            f"decision_cpu_ms={self._scheduler_decision_cpu_seconds * 1000:.1f} "
            f"total_cpu_ms={total_cpu_seconds * 1000:.1f} "
            f"cpu_cores={cpu_cores:.3f} "
            f"event_loop_delay_p50={self._scheduler_percentile(self._scheduler_event_loop_delays, .50):.4f} "
            f"event_loop_delay_p95={self._scheduler_percentile(self._scheduler_event_loop_delays, .95):.4f} "
            f"decision_latency_p50={self._scheduler_percentile(self._scheduler_decision_latencies, .50):.3f} "
            f"decision_latency_p95={self._scheduler_percentile(self._scheduler_decision_latencies, .95):.3f} "
            f"action_latency_p50={self._scheduler_percentile(self._scheduler_action_latencies, .50):.3f} "
            f"action_latency_p95={self._scheduler_percentile(self._scheduler_action_latencies, .95):.3f}",
            flush=True,
        )
        self._scheduler_last_log_at = now
        self._scheduler_process_cpu_at_last_log = process_cpu_now
        self._scheduler_passes = 0
        self._scheduler_markets_seen = 0
        self._scheduler_markets_evaluated = 0
        self._scheduler_markets_skipped = 0
        self._scheduler_position_evaluations = 0
        self._scheduler_service_cpu_seconds = 0.0
        self._scheduler_decision_cpu_seconds = 0.0
        self._scheduler_event_loop_delays.clear()
        self._scheduler_decision_latencies.clear()
        self._scheduler_action_latencies.clear()

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

        `cancel` moves its intent to `UNCERTAIN` when the request fails and to
        `ACKNOWLEDGED` when the request returns, while either CANCEL intent
        resolves only on a terminal order state -- correctly, since even an
        accepted HTTP response is not cancellation confirmation. Re-issue
        either form only after a later positive OPEN read proves that terminal
        cancellation still has not happened.

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
            if str(row["state"]).upper() not in {
                    "ACKNOWLEDGED", "UNCERTAIN"}:
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

    async def _read_owned_order_states(
        self, exchange_ids: list[str], now: datetime,
    ) -> list[tuple[str, str]]:
        failures: list[tuple[str, str]] = []
        for exchange_id in exchange_ids:
            try:
                await self.executor.reconcile_order(exchange_id, observed_at=now)
            except RuntimeInvariantViolation:
                raise
            except Exception as exc:
                failures.append((exchange_id, f"{type(exc).__name__}: {exc}"))
        return failures

    async def _fetch_execution_history(self, now: datetime):
        return await self.account.fetch_executions(observed_at=now)

    async def _rest_reconcile(
        self, now: datetime, *, tag: str = "",
        strict_order_markets: frozenset[str] | None = None,
        advance_gate: bool = True,
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

        ``advance_gate=False`` is for a risk-reducing, market-scoped proof. It
        may update positive account facts, but cannot certify that every owned
        order was re-read and therefore cannot renew account-wide entry
        authority.
        """

        unresolved_owners = self._unresolved_intent_owners()
        execution_owners_before = self._execution_owner_slugs()
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
        read_ids: list[str] = []
        for exchange_id in sorted(exchange_ids):
            markets = exchange_markets.get(exchange_id, set())
            if strict_order_markets is not None and len(markets) == 1:
                market = next(iter(markets))
                if market not in strict_order_markets:
                    skipped_order_ids.add(exchange_id)
                    skipped_order_markets.add(market)
                    continue
            read_ids.append(exchange_id)
        reconcile_failures.extend(await self._read_owned_order_states(read_ids, now))
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
        # Recent venue-retained fills backfill gaps while available. The local
        # ledger is the only execution history this project controls across a
        # restart plus a venue-side history reset. Without this sweep the
        # $40 stop reads one-sided round trips: measured 2026-08-15, a $1.55
        # loss was recorded as a $3.26 gain because only the opening fill was
        # ever written. Failing this must not fail the whole reconcile -- the
        # order and position snapshots above are what gate new risk.
        try:
            execution_batch = await self._fetch_execution_history(now)
            for execution in execution_batch:
                try:
                    self.ledger.apply_execution(execution, received_at=now)
                except LedgerConflict:
                    if self.REQUIRE_EXECUTION_BACKFILL:
                        raise
                    # Already recorded. The dedupe hashes the whole payload, so
                    # adding a field to Execution -- `outcome` did exactly this
                    # -- makes a replay of an existing fill look like a
                    # conflicting rewrite. It is not: the execution id is the
                    # identity and we already hold it. Aborting the sweep here
                    # left the position unprovable and the runner exited
                    # non-flat, refusing to restart. Observed 2026-08-15T21:24Z.
                    continue
            # Trade lifecycle is independent of execution delivery. In
            # particular, BUSTED can appear after a fill was already recorded;
            # apply it after linking all execution legs in this batch.
            for trade_state in getattr(execution_batch, "trade_states", ()):
                self.ledger.apply_trade_state(trade_state, received_at=now)
            self._runtime_error_counts.pop("execution_backfill", None)
        except LedgerConflict:
            # A malformed/unavailable history page is optional positive
            # backfill and can be retried. Contradictory trade identity is not:
            # continuing to treat the previous REST proof as fresh could build
            # risk while fill reversal and P&L are disputed.
            self.gate.on_reconciliation_conflict()
            raise
        except Exception as exc:
            await self._report_runtime_error("execution_backfill", exc)
            if self.REQUIRE_EXECUTION_BACKFILL:
                raise
        stamp = f"{tag}:{now.isoformat()}" if tag else now.isoformat()
        self.ledger.apply_order_snapshot(
            f"commissioning-rest-orders:{stamp}", orders,
            as_of=now, observed_at=now, complete=True)
        had_near_deadline_position = (
            self._position_near_its_flatten_deadline(self._now()))
        self.ledger.apply_position_snapshot(
            f"commissioning-rest-positions:{stamp}", positions,
            as_of=now, observed_at=now, complete_account=True)
        orphan_places_resolved = await self._advance_orphan_place_clear_proofs(
            observed_at=now,
            open_orders=orders,
            positions=positions,
        )
        if orphan_places_resolved:
            print(
                f"[orphan-place-recovery] at={now.isoformat()} "
                f"resolved={len(orphan_places_resolved)} "
                f"intent_ids={','.join(orphan_places_resolved)}",
                flush=True,
            )
        if self._settlement_wait_slugs:
            self._sync_settlement_wait_positions(self._now())
        if self._inactive_market_wait_slugs:
            self._sync_inactive_market_wait_positions(self._now())
        self._request_post_flat_top_up_if_needed(
            had_near_deadline_position, self._now())
        self._record_resolved_intent_owners(unresolved_owners)
        for slug in execution_owners_before.symmetric_difference(
                self._execution_owner_slugs()):
            self._mark_market_dirty(slug, self._now())
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
        if advance_gate:
            self.gate.on_rest_reconciled(self._now())

    def _unresolved_cancel_owners(self) -> dict[str, str]:
        """Map unresolved CANCEL intents to their positively-known owner."""

        return {
            intent_id: detail[1]
            for intent_id, detail in self._unresolved_intent_owners().items()
            if detail[0] == "CANCEL"
        }

    def _unresolved_intent_owners(
        self,
    ) -> dict[str, tuple[str, str, str]]:
        """Map exact unresolved intents to operation, owner and order id."""

        return {
            str(row["intent_id"]): (
                str(row["operation"]).upper(),
                str(row["market_slug"] or "").strip(),
                str(row["exchange_order_id"] or "").strip(),
            )
            for row in self.ledger.unresolved_intents()
        }

    def _record_resolved_intent_owners(
        self, unresolved: dict[str, tuple[str, str, str]],
    ) -> None:
        """Wake the exact owner after an intent gains terminal proof.

        A CANCEL receives the existing priority path only when that exact
        intent disappears from the unresolved set. A PLACE resolving OPEN,
        rejected or terminal also wakes its owner so dirty scheduling cannot
        leave the market idle until an unrelated book message.
        """

        if not unresolved:
            return
        still_unresolved = {
            str(row["intent_id"])
            for row in self.ledger.unresolved_intents()
        }
        for intent_id, (operation, slug, _order_id) in unresolved.items():
            if intent_id in still_unresolved or not slug:
                continue
            self._mark_market_dirty(slug, self._now())
            if operation == "CANCEL":
                self._priority_replan_slugs.add(slug)

    def _record_resolved_cancel_owners(
        self, unresolved_cancels: dict[str, str],
    ) -> None:
        """Prioritize owners whose CANCEL gained terminal proof."""

        self._record_resolved_intent_owners({
            intent_id: ("CANCEL", slug, "")
            for intent_id, slug in unresolved_cancels.items()
        })

    async def _observe_private(self, event: Any, now: datetime) -> None:
        unresolved_owners = self._unresolved_intent_owners()
        execution_owners_before = self._execution_owner_slugs()
        affected_slugs = set(self._private_event_market_slugs(event))
        received_at = getattr(event, "received_at", None) or now
        had_near_deadline_position = (
            self._position_near_its_flatten_deadline(now))
        self.gate.observe(event, received_at=received_at)
        self.projector.apply(event, received_at=received_at)
        if self._settlement_wait_slugs:
            self._sync_settlement_wait_positions(self._now())
        if self._inactive_market_wait_slugs:
            self._sync_inactive_market_wait_positions(self._now())
        self._request_post_flat_top_up_if_needed(
            had_near_deadline_position, self._now())
        self._record_resolved_intent_owners(unresolved_owners)
        affected_slugs.update(
            execution_owners_before.symmetric_difference(
                self._execution_owner_slugs()))
        for slug in affected_slugs:
            self._mark_market_dirty(slug, received_at)

    async def _drain_private_events(self) -> int:
        """Apply private events already buffered by a connected stream.

        The normal run loop drains after its pending ``next_event`` result.
        Recovery and shutdown run outside that loop, but the stream remains
        connected and can already hold the terminal cancel proof those paths
        need.  Leaving it unread forced them onto a GET-by-id endpoint that can
        answer NotFound for minutes after a successful cancel.
        """

        drain = getattr(self.private_stream, "drain_events", None)
        if not callable(drain):
            return 0
        count = 0
        for event in drain():
            await self._observe_private(event, self._now())
            count += 1
        return count

    def _take_priority_replans(self) -> tuple[str, ...]:
        """Take exact cancel-confirmed owners in stable managed order."""

        selected = tuple(
            slug for slug in self.managed_slugs()
            if slug in self._priority_replan_slugs)
        self._priority_replan_slugs.difference_update(selected)
        return selected

    async def _wait_for_initial_private_state(self, timeout_seconds: float = 15) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                event = await self.private_stream.next_event(timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                status = getattr(
                    self.private_stream, "initial_state_status", "unavailable")
                raise CommissioningUnavailable(
                    "private stream did not become reconciled and fresh; "
                    f"initial_state={status}") from exc
            now = self._now()
            await self._observe_private(event, now)
            # `session_ready`, not `ready`: a bind proves we can SEE the venue.
            # Asking the account-wide `ready` here meant one market's unsettled
            # bookkeeping could fail the bind for all twenty -- and a bind that
            # fails closes both streams, so the markets that were perfectly
            # fine stopped quoting too. Whether any given market may quote is
            # asked per market, every pass, in `_frame`.
            pool_ready = getattr(
                self.private_stream, "initial_state_ready", True)
            if (self.gate.session_ready(self.ledger, now=now)
                    and bool(pool_ready)):
                return
        raise CommissioningUnavailable(
            "private stream did not become reconciled and fresh; "
            "initial_state="
            f"{getattr(self.private_stream, 'initial_state_status', 'unavailable')}")

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

    def _isolated_unexecutable_slugs(self) -> set[str]:
        """Exact nonzero owners that currently have no legal mutation path."""

        return set(self._settlement_wait_slugs) | set(
            self._inactive_market_wait_slugs)

    def _can_wait_for_inactive_market(self, slug: str) -> bool:
        """Whether exact metadata and ownership justify mutation-free waiting."""

        if slug not in set(self.active_slugs) or not self.ledger.risk_state_known():
            return False
        position = self.ledger.position(slug)
        if position == 0:
            return False
        if (self.ledger.working_orders(slug)
                or self.ledger.absent_orders(slug)
                or self.ledger.market_has_unresolved_intent(slug)):
            return False
        basis = self.ledger.inventory_basis(slug)
        if basis is None or basis.net_qty != position:
            return False
        state = self._markets.get(slug)
        return self._market_metadata_proves_not_open(
            getattr(state, "market", None))

    def _enter_inactive_market_wait(self, slug: str, now: datetime) -> bool:
        """Isolate one exact position while fresh metadata refuses all orders."""

        if not self._can_wait_for_inactive_market(slug):
            return False
        state = self.state_for(slug)
        state.incentive = None
        state.reward_eligible = frozenset()
        state.clear_economics()
        first = slug not in self._inactive_market_wait_slugs
        self._inactive_market_wait_slugs.add(slug)
        if first:
            print(
                f"[inactive-market-wait] at={now.isoformat()} market={slug} "
                f"position={self.ledger.position(slug)} state=entered "
                "metadata=not_open capacity_reserved=1",
                flush=True,
            )
        return True

    def _leave_inactive_market_wait(
        self, slug: str, now: datetime, reason: str,
    ) -> None:
        if slug not in self._inactive_market_wait_slugs:
            return
        self._inactive_market_wait_slugs.remove(slug)
        self._mark_market_dirty(slug, now)
        print(
            f"[inactive-market-wait] at={now.isoformat()} market={slug} "
            f"position={self.ledger.position(slug)} state=left reason={reason}",
            flush=True,
        )

    def _can_wait_for_settlement(self, slug: str, now: datetime) -> bool:
        """Whether one exact owner may be isolated without claiming flat.

        A valid empty-book response is checked by the caller; transport,
        schema and missing-market failures never reach this predicate. The
        position itself must be attributable, past kickoff, order-free and
        ambiguity-free. Any later drift returns the owner to ordinary
        fail-closed recovery.
        """

        if (slug not in self._recovery_slugs
                or slug not in set(self.active_slugs)):
            return False
        position = self.ledger.position(slug)
        if position == 0:
            return False
        if (self.ledger.working_orders(slug)
                or self.ledger.absent_orders(slug)
                or self.ledger.market_has_unresolved_intent(slug)):
            return False
        basis = self.ledger.inventory_basis(slug)
        if basis is None or basis.net_qty != position:
            return False
        market = self.state_for(slug).market
        if market is None or market.game_start is None:
            return False
        return market.game_start <= now

    def _enter_settlement_wait(self, slug: str, now: datetime) -> bool:
        """Quarantine one response-backed empty post-kickoff recovery book."""

        if not self._can_wait_for_settlement(slug, now):
            return False
        state = self.state_for(slug)
        state.book = None
        state.book_observed_at = None
        state.clear_economics()
        state.next_book_refresh = now + timedelta(
            seconds=SETTLEMENT_WAIT_BOOK_REFRESH_SECONDS)
        first = slug not in self._settlement_wait_slugs
        self._settlement_wait_slugs.add(slug)
        if first:
            print(
                f"[settlement-wait] at={now.isoformat()} market={slug} "
                f"position={self.ledger.position(slug)} state=entered "
                "book=empty capacity_reserved=1",
                flush=True,
            )
        return True

    def _leave_settlement_wait(self, slug: str, now: datetime, reason: str) -> None:
        if slug not in self._settlement_wait_slugs:
            return
        self._settlement_wait_slugs.remove(slug)
        self._mark_market_dirty(slug, now)
        print(
            f"[settlement-wait] at={now.isoformat()} market={slug} "
            f"position={self.ledger.position(slug)} state=left reason={reason}",
            flush=True,
        )

    def _sync_settlement_wait_positions(self, now: datetime) -> None:
        """Release only owners whose authoritative position became zero."""

        for slug in tuple(self._settlement_wait_slugs):
            if self.ledger.position(slug) != 0:
                continue
            self._leave_settlement_wait(slug, now, "position_flat")
            if self._lifecycle_top_up_requested_at is None:
                self._lifecycle_top_up_requested_at = now

    def _sync_inactive_market_wait_positions(self, now: datetime) -> None:
        """Release only owners whose authoritative position became zero."""

        for slug in tuple(self._inactive_market_wait_slugs):
            if self.ledger.position(slug) != 0:
                continue
            self._leave_inactive_market_wait(slug, now, "position_flat")
            self._request_lifecycle_top_up(now)

    def _can_wait_unexecutable(self, slug: str, now: datetime) -> bool:
        if slug in self._settlement_wait_slugs:
            return self._can_wait_for_settlement(slug, now)
        if slug in self._inactive_market_wait_slugs:
            return self._can_wait_for_inactive_market(slug)
        return False

    def _startup_settlement_wait_is_scoped(
        self, blockers: tuple[str, ...], now: datetime,
    ) -> bool:
        """Authorize unrelated entry while every nonflat owner is isolated."""

        if set(blockers) != {"shared account has nonzero positions"}:
            return False
        nonzero = {
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        }
        return (
            bool(nonzero)
            and nonzero == self._isolated_unexecutable_slugs()
            and self.ledger.risk_state_known()
            and all(self._can_wait_unexecutable(slug, now)
                    for slug in nonzero)
        )

    def _startup_managed_position_is_scoped(
        self, blockers: tuple[str, ...] | None, now: datetime,
    ) -> bool:
        """Allow other slots around one exact post-kickoff recovery owner."""

        if (blockers is not None
                and set(blockers) != {"shared account has nonzero positions"}):
            return False
        nonzero = tuple(
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0"))
        if len(nonzero) != 1:
            return False
        slug = nonzero[0]
        if (slug not in self._recovery_slugs
                or slug not in set(self.active_slugs)
                or slug in self._isolated_unexecutable_slugs()
                or not self.ledger.risk_state_known()
                or self.ledger.working_orders()
                or self.ledger.absent_orders()
                or self.ledger.unresolved_intents()):
            return False
        try:
            basis = self.ledger.inventory_basis(slug)
        except Exception:
            return False
        if basis is None or basis.net_qty != self.ledger.position(slug):
            return False
        state = self.state_for(slug)
        market = state.market
        return bool(
            market is not None
            and market.game_start is not None
            and market.game_start <= now
            and state.book is not None
            and state.book_observed_at is not None)

    async def _add_startup_markets_around_reserved_position(
        self, now: datetime,
    ) -> None:
        """Use remaining configured slots without delaying risk monitoring."""

        if (not self.config.auto_select_reward_markets
                or self.reward_selector is None
                or not (
                    self._isolated_unexecutable_slugs()
                    or self._startup_managed_position_is_scoped(None, now))):
            return
        available = self.config.max_markets - len(self.active_slugs)
        if available <= 0:
            return
        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=now)
        except (ApiAdmissionDeferred, AdapterError) as exc:
            # Discovery cannot improve or exit the quarantined position. A
            # failed scan therefore leaves its monitor scope intact, and the
            # ordinary bounded runtime top-up path can retry later.
            self._selection_scan_seconds_last = time.monotonic() - scan_started
            await self._report_runtime_error(
                "startup_reserved_position_selection", exc)
            return
        self._selection_scan_seconds_last = time.monotonic() - scan_started
        self._log_selection_scan(selection)
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        if selection.selected is None:
            return
        chosen = tuple(
            item.market.slug for item in selection.top_viable(
                available, exclude=self._barred_slugs()))
        if chosen:
            self._bind_markets((*self.active_slugs, *chosen))

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
            except ApiAdmissionDeferred as exc:
                # A cooperating process published a host cooldown before this
                # read crossed the network boundary. Startup is still flat and
                # safe: discard partial bindings, wait once, and re-prove the
                # complete preflight without consuming an outage/error budget.
                attempt = 0
                venue_failure_streak = 0
                await self._release_partial_startup()
                if self._stop_requested:
                    raise CommissioningRefused(
                        "operator stopped during API admission backpressure"
                    ) from exc
                delay = self._api_admission_delay(exc)
                self._log_api_backpressure("startup", delay)
                await self.sleep(delay)
            except AdapterError as exc:
                # Do not let maintenance consume the supervisor's three-fast-
                # crash allowance. Only classified transport/5xx/throttle
                # failures enter the long-lived pause; malformed responses,
                # validation failures and ownership/state defects still leave
                # this process and surface normally.
                if not self._is_transient_recovery_error(exc):
                    raise
                venue_failure_streak += 1
                await self._report_runtime_error(
                    "startup_venue_unavailable", exc, venue_failure_streak)
                await self._release_partial_startup()
                if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                    stop = await self._wait_for_venue_recovery("startup", exc)
                    if stop:
                        raise CommissioningRefused(
                            f"operator stopped during venue recovery pause: {stop}"
                        ) from exc
                    venue_failure_streak = 0
                    attempt = 0
                    continue
                await self.sleep(self._recovery_delay(
                    "startup_venue_unavailable", base=5.0, cap=30.0))
            except _OrphanPlaceEvidencePending as exc:
                # This is local ambiguity, not a stream/venue failure and not a
                # condition that improves by restarting. Stay flat, repeat the
                # complete account proof, and remain operator-interruptible.
                attempt = 0
                venue_failure_streak = 0
                await self._report_runtime_error(
                    "startup_orphan_place_evidence", exc)
                await self._release_partial_startup()
                if self._stop_requested:
                    raise CommissioningRefused(
                        f"operator stopped during orphan PLACE recovery: {exc}"
                    ) from exc
                await self.sleep(self._recovery_delay(
                    "startup_orphan_place_evidence", base=5.0, cap=60.0))
            except CommissioningUnavailable as exc:
                venue_failure_streak = 0
                if self._stop_requested:
                    raise CommissioningRefused(
                        f"operator stopped during startup: {exc}") from exc
                await self._report_runtime_error(
                    "startup_unavailable", exc, attempt,
                    include_traceback=(attempt == 1 or attempt % 30 == 0))
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
        # A retry can fail before automatic selection binds its first market
        # (notably while proving a startup orphan). The compatibility
        # properties require a primary slug, so clear them only when one exists;
        # `_bind_markets(())` below discards every per-market state either way.
        if self.active_slugs:
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
        repaired_denials = self.executor.resolve_recorded_permission_denials(
            now=now)
        if repaired_denials:
            print(
                f"[startup-intent-repair] at={now.isoformat()} "
                "reason=definitive_permission_rejection "
                f"intents={len(repaired_denials)}",
                flush=True,
            )
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
        if reconciled_at_startup:
            orphan_places = self._unresolved_places_without_exchange_id()
            if orphan_places:
                # A create that returned no exchange id cannot be retrieved by
                # id, and the local client id was never sent to the venue. Do
                # not bind its (possibly finished) market as if it held a
                # position: an empty book then becomes a retryable transport
                # failure and the supervisor performs three pointless fast
                # restarts. It is also unsafe to clear the intent from one
                # absence snapshot. Keep this process alive and flat while the
                # repeated complete-account proof in `_rest_reconcile`
                # accumulates; the uncertain create is never retried.
                markets = sorted({
                    str(row["market_slug"] or "").strip()
                    for row in orphan_places
                    if str(row["market_slug"] or "").strip()
                })
                raise _OrphanPlaceEvidencePending(
                    "startup found "
                    f"{len(orphan_places)} unresolved PLACE intent(s) without "
                    "an exchange order id; waiting in-process for repeated "
                    "complete account proof with no surviving venue risk"
                    + (f"; markets={','.join(markets)}" if markets else ""))
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
        if (self._isolated_unexecutable_slugs()
                or self._startup_managed_position_is_scoped(None, self._now())):
            # One exact recovery owner reserves its slot. Empty/non-open
            # owners remain mutation-free; a readable post-kickoff owner keeps
            # the unchanged exit planner. Use only the remaining configured
            # slots, and never let a failed scan drop position monitoring.
            await self._add_startup_markets_around_reserved_position(
                self._now())
            await self._refresh_bound_books(self._now())
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
        settlement_wait_trade = (
            not report.ready
            and self._startup_settlement_wait_is_scoped(
                report.blockers, self._now()))
        managed_position_trade = (
            not report.ready
            and not settlement_wait_trade
            and self._startup_managed_position_is_scoped(
                report.blockers, self._now()))
        recovery_only = (
            not report.ready
            and not settlement_wait_trade
            and not managed_position_trade
            and self._startup_recovery_is_scoped(report.blockers))
        if (not report.ready and not recovery_only
                and not settlement_wait_trade and not managed_position_trade):
            raise CommissioningRefused("; ".join(report.blockers))
        # The startup proof can span several paginated REST calls. Start its
        # freshness window when that proof completed, just like periodic
        # reconciliation, or a slow preflight can arrive at the loop stale.
        self.gate.on_rest_reconciled(self._now())
        self._equity_snapshot = await self.account.fetch_equity_snapshot(
            observed_at=now)
        self.balance = self._equity_snapshot.balance
        self.risk_guard = SessionRiskGuard(
            started_at=now,
            starting_equity=self._equity_snapshot.marked_equity,
            max_duration_seconds=float("inf"),
            max_equity_age_seconds=15,
            hard_stop_loss=self.config.hard_stop_loss_usd,
        )
        # No raw-cash HARD sample: a failed cash-activity proof during startup
        # must not durably latch a withdrawal as trading loss in recovery.
        # Before any order can be placed. `SessionRiskGuard` stamped
        # `starting_equity` from this instant, which measures the process; this
        # re-points it at the ET day's opening equity from the ledger, so a
        # restart inherits the day's drawdown instead of resetting it.
        self._roll_realized_baseline()
        # Raw cash can include a reward or deposit. Prove the non-trading
        # adjustment before the driver exists, so startup can never arm new
        # risk with a reward-masked session-loss sensor.
        await self._refresh_balance(self._now())
        self.driver = ControlledActionDriver(
            self.executor, self.ledger, self.risk_guard,
            new_entry_allowed=lambda: (
                not self._entry_pause_active()
                and not self._loss_cap_blocks_new_entry(self._now())))
        self._orders_before = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        hard_recovery = recovery_only and self._hard_stop_hold_day is not None
        if recovery_only and not hard_recovery:
            return "RECOVERY_ONLY"
        await self._wait_for_initial_private_state()
        selection_note = ""
        if self._market_selection is not None:
            selection_note = (
                f" Auto-selected {self.market_slug} from "
                f"{self._market_selection.rewarded_executable_markets} rewarded "
                f"executable markets joined from "
                f"{self._market_selection.catalog_markets} buffered event-market "
                "rows after evaluating "
                f"{self._market_selection.books_evaluated} books; "
                f"{self._market_selection.unsupported_programs} programs used "
                "an unsupported or undocumented lifecycle and remained read-only.")
        if settlement_wait_trade:
            empty_count = len(self._settlement_wait_slugs)
            inactive_count = len(self._inactive_market_wait_slugs)
            if inactive_count == 0:
                selection_note += (
                    f" {empty_count} exact post-kickoff position(s) have no "
                    "executable book and remain quarantined for "
                    "settlement/book recovery; each reserves one configured "
                    "slot and its full portfolio exposure.")
            else:
                selection_note += (
                    f" {empty_count + inactive_count} exact position(s) remain "
                    "mutation-free while unexecutable "
                    f"(empty_book={empty_count}, not_open={inactive_count}); "
                    "each reserves one configured slot and its full portfolio "
                    "exposure.")
        elif managed_position_trade:
            selection_note += (
                " One exact post-kickoff recovery position remains barred "
                "from building and under the unchanged exit manager; it "
                "reserves one configured slot and its full portfolio exposure.")
        if hard_recovery:
            selection_note += (
                " Persisted HARD recovery: entries remain paused while owned "
                "orders/positions evacuate. Next-day entry requires fresh flat proof.")
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
            f"${self.config.hard_stop_loss_usd} hard stop, "
            f"${self.config.execution_fee_observation_usd} ET-day "
            "execution-fee telemetry reference, "
            "operator-controlled stop; pregame and loss gates remain active."
            + selection_note)
        if not getattr(delivery, "sent", False):
            raise CommissioningRefused(
                "Discord startup alert delivery failed before order entry")
        return "HARD_RECOVERY" if hard_recovery else "TRADE"

    def _accrue_score(self, now: datetime) -> None:
        """Accrue resting score across every bound market.

        Score is a time integral over orders actually resting, so with several
        markets the session total is the sum of their rates. Per-market totals
        are kept alongside it: a session number alone cannot tell you which
        market earned the exposure the phase is judged on.
        """

        if self._score_observed_at is not None:
            started_at = self._score_observed_at
            seconds = (now - started_at).total_seconds()
            if seconds < 0:
                # Score is observation-only (safety invariant 72). A host
                # wall-clock correction must not escape into
                # `_service_bound_markets`, where a generic service exception
                # is deliberately interpreted as a market-local trading
                # failure and retires the slot. Keep the accepted high-water
                # mark so the reversed interval is neither double-counted nor
                # assigned to the wrong ET reward day. Once wall time catches
                # up, accrual resumes from that mark.
                self._score_clock_reversals += 1
                count = self._score_clock_reversals
                if count == 1 or (count & (count - 1)) == 0:
                    try:
                        print(
                            f"[score-clock] at={now.isoformat()} "
                            f"high_water={started_at.isoformat()} "
                            f"reversals={count} action=ignored_stale_sample",
                            flush=True,
                        )
                    except Exception:
                        # Even diagnostics about observational containment are
                        # not allowed to affect market availability.
                        pass
                return
            day_pieces = split_reward_day_interval(started_at, now)
            for slug, state in self._markets.items():
                rate = sum(state.score_by_side.values())
                earned = seconds * rate
                if earned:
                    self._score_seconds += earned
                    self._score_seconds_by_market[slug] = (
                        self._score_seconds_by_market.get(slug, 0.0) + earned)
                    for day, day_seconds in day_pieces:
                        day_earned = day_seconds * rate
                        self._daily_score_pending[day] = (
                            self._daily_score_pending.get(day, 0.0)
                            + day_earned)
        self._score_observed_at = now

    def _flush_daily_score(self, now: datetime) -> float:
        """Persist pending ET-day score and return today's complete total.

        Flushing at the five-minute summary boundary keeps score persistence
        out of the order-decision hot path. Graceful shutdown also sends a
        summary, so the normal restart path loses no score. A hard process
        kill can lose only the increment since the previous summary; it can
        never erase the value a previous process already committed.
        """

        self._accrue_score(now)
        for day, pending in tuple(self._daily_score_pending.items()):
            if pending > 0:
                total = self.ledger.add_score_seconds(
                    day, pending, observed_at=now)
                self._daily_score_committed[day] = float(total)
            del self._daily_score_pending[day]
        today = reward_day(now)
        total = float(self.ledger.score_seconds_for_day(today))
        self._daily_score_committed[today] = total
        return total

    def _daily_score_fallback(self, now: datetime) -> float:
        """Best available daily score after reporting persistence fails.

        This function never raises. Pending increments remain buffered for the
        next summary; a reporting-only write fault cannot become a runner or
        shutdown fault.
        """

        today = reward_day(now)
        try:
            committed = float(self.ledger.score_seconds_for_day(today))
            self._daily_score_committed[today] = committed
        except Exception:
            committed = self._daily_score_committed.get(today, 0.0)
        return committed + self._daily_score_pending.get(today, 0.0)

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
        try:
            self._evaluate_rewards_inner(now, slug)
        except Exception:
            # A failed evaluation interrupts continuous economic proof.  It
            # must not leave an old refusal clock aging through unknown state.
            state.economically_ineligible_since = None
            state.economic_ineligibility_reasons = ()
            raise
        self._record_economic_ineligibility(state, self._now())
        self._record_eligibility(state, self._now())

    def _record_economic_ineligibility(
        self, state: MarketState, now: datetime,
    ) -> None:
        """Maintain a continuous dwell only for a flat, proven economic no.

        The timer is intentionally stricter than quote eligibility.  Both
        sides must have completed evaluation with a replaceable economic
        blocker, and the owner must already be position/order/ambiguity free.
        Any authorized side or execution state resets the dwell immediately.
        """

        reasons = tuple(sorted(set(state.economic_blocker_by_side.values())))
        replaceable = (
            set(state.economic_blocker_by_side) == {"bid", "offer"}
            and bool(reasons)
            and set(reasons).issubset(self.REPLACEABLE_ECONOMIC_BLOCKERS)
            and not any(
                value > 0 for value in state.expected_net_by_side.values())
            and self._owner_is_flat_and_clear(state.slug)
        )
        if not replaceable:
            state.economically_ineligible_since = None
            state.economic_ineligibility_reasons = ()
            return
        if state.economically_ineligible_since is None:
            state.economically_ineligible_since = now
        state.economic_ineligibility_reasons = reasons

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
            # Score the price we will actually rest at. An exact one-tick book
            # joins both touches; a locked book is priced back to a two-cent
            # gap. The economics and planner must use the same price.
            price = entry_quote_price(
                state.book, side,
                float(self.config.minimum_entry_spread),
                state.market.tick_size)
            if price is None:
                state.economic_blocker_by_side[side] = "book_sides_overlap"
                continue
            if min(price, 1.0 - price) < (
                    float(self.config.minimum_entry_price) - 1e-9):
                # The reconciler already refuses the same candidate.  Record
                # it here as economics as well so a flat market that drifts to
                # an untradeable extreme cannot silently own a slot forever.
                state.economic_blocker_by_side[side] = "price_too_extreme"
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
            quality_blocker = self.config.entry_quality_policy.blocker(exit_result)
            if quality_blocker:
                state.economic_blocker_by_side[side] = quality_blocker
                state.expected_net_by_side[side] = 0.0
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
                unit_loss = (1.0 if basis is None else
                             float(basis.average_entry_price) if position > 0
                             else 1.0 - float(basis.average_entry_price))
                total += float(abs(position)) * unit_loss
            for order in self._working_orders(slug, include_absent=True):
                total += float(order.remaining_qty) * (
                    order.price if order.side == "bid" else 1.0 - order.price)
            # PLACE may be live before any order row arrives. Row-backed
            # intents are already counted above (or positively terminal).
            for intent in self.ledger.unresolved_intents(slug):
                if intent["operation"] != "PLACE":
                    continue
                if self.ledger.query(
                        "SELECT 1 FROM execution_orders WHERE client_order_id=?",
                        (intent["client_order_id"],)):
                    continue
                payload = json.loads(intent["payload_json"])
                price = Decimal(str(payload["price"]["value"]))
                qty = Decimal(str(payload["quantity"]))
                fills = self.ledger.query(
                    "SELECT qty FROM execution_fills WHERE client_order_id=? AND reversed=0",
                    (intent["client_order_id"],))
                remaining = max(Decimal(0), qty - sum(
                    (Decimal(str(row["qty"])) for row in fills), Decimal(0)))
                side = str(payload.get("intent", ""))
                if (not price.is_finite() or not qty.is_finite()
                        or not 0 <= price <= 1 or qty <= 0
                        or side not in {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_SHORT",
                                        "ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_LONG"}):
                    raise RuntimeInvariantViolation(
                        "reserved_order_invalid", "unresolved order risk is malformed")
                total += float(remaining * (
                    price if side in {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_SHORT"}
                    else 1 - price))
        return total

    def _working_orders(
        self, slug: str | None = None, *, include_absent: bool = False,
    ) -> tuple[WorkingOrder, ...]:
        result: list[WorkingOrder] = []
        side_by_intent = {
            "ORDER_INTENT_BUY_LONG": "bid",
            "ORDER_INTENT_SELL_SHORT": "bid",
            "ORDER_INTENT_BUY_SHORT": "offer",
            "ORDER_INTENT_SELL_LONG": "offer",
        }
        rows = self.ledger.working_orders(slug or self.market_slug)
        if include_absent:
            rows += self.ledger.absent_orders(slug or self.market_slug)
        for row in rows:
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
        if isinstance(self.gateway, EventAwareGateway):
            state.market = self.gateway.apply_event_status(state.market)
        position = self.ledger.position(state.market.slug)
        basis = self.ledger.inventory_basis(state.market.slug)
        if position != 0 and basis is None:
            return MarketFrame(
                state.market, now, state.book, state.book_observed_at, position,
                self._working_orders(state.slug), False,
                state.reward_eligible, {}, 0,
                new_risk_allowed=False,
                hard_stop_flatten_required=self._hard_stop_hold_day is not None,
                maintenance_flatten_required=(
                    self._scheduled_maintenance_flatten_required()))
        today = date.fromisoformat(risk_day(now))
        new_risk_allowed = (
            self.risk_guard is not None
            and self.risk_guard.can_add_new_risk(now=now)
            and self._provisional_hard_stop_at is None
            and self._baseline_day == today
            and self._nontrading_cash is not None
            and self._nontrading_cash_day == today
            and not self._entry_pause_active()
            and state.market.event_allows_entry(now)
        )
        eligible = state.reward_eligible
        if position == 0 and not new_risk_allowed:
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
            new_risk_allowed=new_risk_allowed,
            hard_stop_flatten_required=self._hard_stop_hold_day is not None,
            maintenance_flatten_required=(
                self._scheduled_maintenance_flatten_required()),
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
            if (state.book.slug != action.market_slug
                    or state.market.slug != action.market_slug):
                raise RuntimeError("market observation identity is inconsistent")
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now)
            if period is None:
                raise RuntimeError("applicable reward period is unavailable")
            side = action.side
            score = state.reward_score_by_side[side]
            projection = state.reward_projection_by_side[side]
            hazard = state.fill_probability_by_side[side]
            driver = state.fill_risk_driver_by_side[side]
            # The cached economics may describe an earlier touch. Bind these
            # telemetry fields to this exact action and current local book.
            exit_result = cross_exit(
                state.book, entry_side=side, entry_price=float(action.price),
                qty=float(action.qty), depth_haircut=.5,
                exclude_entry_level=True)
            economics = state.opportunity_economics_by_side[side]
            model_queue = state.model_queue_ahead_by_side[side]

            entry_stake = exit_result.entry_cost
            retained_value = (
                entry_stake + exit_result.pnl
                if exit_result.complete and entry_stake > 0 else None)
            retained_ratio = (
                retained_value / entry_stake
                if retained_value is not None else None)
            replacement = self._retained_value_replacement_snapshot(
                now, current_projected_reward=projection.projected_reward)

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
                retained_value_ratio=retained_ratio,
                stressed_exit_value=retained_value,
                would_refuse_retained_60=(
                    retained_ratio < .60
                    if retained_ratio is not None else None),
                would_refuse_retained_70=(
                    retained_ratio < .70
                    if retained_ratio is not None else None),
                would_refuse_retained_80=(
                    retained_ratio < .80
                    if retained_ratio is not None else None),
                replacement_market_slug=replacement[0],
                replacement_projected_reward=replacement[1],
                replacement_expected_net=replacement[2],
                replacement_reward_delta=replacement[3],
                replacement_selection_age_seconds=replacement[4],
                replacement_reason=replacement[5],
                slot_would_go_dark=replacement[6],
                selection_rank=self._selection_rank_for_market(now, state.slug),
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

    def _selection_rank_for_market(
        self, now: datetime, market_slug: str,
    ) -> int | None:
        """Return evidence-only rank from the recent in-memory selector slate.

        This helper is synchronous, makes no venue call and cannot alter the
        selected market set. Missing, future-dated or stale evidence remains
        NULL rather than assigning a market a guessed cohort.
        """

        selection = self._market_selection
        observed_at = self._market_selection_observed_at
        if selection is None or observed_at is None:
            return None
        age = (now - observed_at).total_seconds()
        if age < 0 or age > self.config.market_selection_refresh_seconds:
            return None
        candidates = selection.top_viable(
            max(1, len(selection.opportunities)))
        for rank, candidate in enumerate(candidates, start=1):
            if candidate.market.slug == market_slug:
                return rank
        return None

    def _retained_value_replacement_snapshot(
        self, now: datetime, *, current_projected_reward: float,
    ) -> tuple[
        str | None, float | None, float | None, float | None,
        float | None, str, bool | None,
    ]:
        """Describe the best spare slot from already-fetched selection data.

        This is deliberately synchronous and local. It cannot refresh a book,
        scan the catalog, release a market or feed the planner. A stale or
        absent selector snapshot is recorded as missing evidence rather than
        promoted into a pretend replacement.
        """

        selection = self._market_selection
        observed_at = self._market_selection_observed_at
        if selection is None or observed_at is None:
            return (None, None, None, None, None,
                    "selection_unavailable", None)
        age = (now - observed_at).total_seconds()
        if age < 0:
            return (None, None, None, None, age,
                    "selection_clock_invalid", None)
        if age > self.config.market_selection_refresh_seconds:
            return (None, None, None, None, age,
                    "selection_stale", None)
        excluded = set(self.active_slugs) | self._barred_slugs(now)
        candidates = selection.top_viable(
            max(1, len(selection.opportunities)), exclude=excluded)
        if not candidates:
            return (None, None, None, None, age,
                    "no_viable_spare", True)
        candidate = candidates[0]
        reward = candidate.projected_reward_total
        expected = candidate.best_expected_net_value
        return (
            candidate.market.slug,
            reward,
            expected,
            reward - current_projected_reward,
            age,
            "recent_selector_spare",
            False,
        )

    async def _refresh_balance(self, now: datetime) -> None:
        if self.risk_guard is None:
            raise RuntimeError("risk guard is not initialized")
        snapshot = await self.account.fetch_equity_snapshot(observed_at=now)

        observed_at = self._now()
        today = date.fromisoformat(risk_day(observed_at))
        if (self._baseline_day != today
                and self._provisional_hard_stop_at is None):
            snapshot_day = date.fromisoformat(risk_day(snapshot.observed_at))
            if snapshot_day != today:
                # The bracketed account read itself crossed midnight. It is
                # not a defensible opening mark for either side of the
                # boundary; leave entry blocked and take another snapshot.
                return
            # Roll the durable ET-day marks before asking cash history to
            # classify activity. The scan is bounded by the baseline's cash
            # boundary and verifies that the current risk day matches
            # ``_baseline_day``. The opposite order deadlocks at midnight:
            # yesterday remains active, the check fails, and balance/cash
            # reads retry forever while entry stays blocked.
            #
            # Publish only the raw candidate needed to create the baseline;
            # ``_frame`` separately requires a current-day cash adjustment,
            # so entry remains fail-closed until the work below succeeds.
            self._equity_snapshot = snapshot
            self.balance = snapshot.balance
            self._roll_realized_baseline()
        # Every published mark needs cash activity from the same bracket.
        # A timer-cached adjustment can misclassify a newly completed transfer
        # (even if an offsetting fill leaves the raw cash balance unchanged).
        if self._cash_activity_boundary is None:
            raise RuntimeError("cash-activity boundary is unavailable")
        nontrading = await fetch_nontrading_cash(
            self.gateway,
            boundary=self._cash_activity_boundary,
            through=observed_at,
            max_pages=NONTRADING_CASH_MAX_PAGES,
        )
        # Cash activity can complete while its pages are being read. The
        # snapshot already bracketed positions with equal cash reads; one
        # final equal cash read proves the activity adjustment belongs to
        # that same account state.
        second = await self.account.fetch_balance(observed_at=self._now())
        if snapshot.balance.current_balance != second.current_balance:
            raise AdapterError(
                "settled cash changed during non-trading activity snapshot")
        if date.fromisoformat(risk_day(self._now())) != today:
            raise AdapterError(
                "ET risk day changed during non-trading activity snapshot")
        # Keep every consumer on the final bracketed balance row.  Do not
        # publish any part of the candidate snapshot until all reads have
        # succeeded; a failed activity bracket must leave the previous
        # proven mark intact and merely let its freshness expire.
        snapshot = account_equity_snapshot(
            second, snapshot.positions, observed_at=snapshot.observed_at)
        self._nontrading_cash = nontrading
        self._nontrading_cash_observed_at = self._now()
        self._nontrading_cash_day = self._baseline_day

        self._equity_snapshot = snapshot
        self.balance = snapshot.balance
        # Realised P&L from the fill ledger, per market and in total. This
        # used to attribute the whole account's marked-equity change to the
        # primary market, which was wrong twice over: marked equity is not
        # realised, and one market is not all of them.
        self._roll_realized_baseline()

        # Freshness begins only after every cash input is proven, not when the
        # first balance request entered the shared governor. A failed activity
        # scan therefore lets the existing 15-second risk freshness expire and
        # disables new risk instead of silently falling back to masked cash.
        observed_at = self._now()
        # Fiat cash plus each open position's documented cashValue is the
        # account mark.  Retail's balance-row assetNotional has been observed
        # as zero with nonzero positions and therefore cannot protect this
        # stop. Rewards, deposits and withdrawals are then removed exactly as
        # they are from the realised sensor.
        adjusted_equity = snapshot.marked_equity - self._nontrading_cash
        self.risk_guard.observe_equity(
            adjusted_equity, observed_at=observed_at)
        self._refresh_loss_entry_order_cap(observed_at)
        self._refresh_overnight_entry_order_cap(observed_at)
        self._publish_live_pnl_snapshot()

    def _publish_active_scope_snapshot(
        self, *, status: str | None = None,
    ) -> None:
        """Refresh the TV slot scope without giving the dashboard bot access."""

        if status is None:
            status = (
                "ENTRY_PAUSED" if self._entry_pause_active() else "RUNNING")
        try:
            write_active_scope_snapshot(
                ACTIVE_SCOPE_CACHE_PATH,
                observed_at=self._now(),
                markets=self.active_slugs,
                capacity=self.config.max_markets,
                status=status,
            )
            if self._active_scope_cache_error:
                print(
                    f"[active-scope-cache] at={self._now().isoformat()} "
                    "state=recovered",
                    flush=True,
                )
            self._active_scope_cache_error = False
        except (OSError, TypeError, ValueError) as exc:
            # Display state cannot authorize, cancel or place an order. Keep a
            # cache fault local and rate-limit its log to state transitions.
            if not self._active_scope_cache_error:
                print(
                    f"[active-scope-cache] at={self._now().isoformat()} "
                    f"state=failed reason={type(exc).__name__}:{exc}",
                    flush=True,
                )
            self._active_scope_cache_error = True

    def _publish_live_pnl_snapshot(self) -> None:
        """Refresh the dashboard mark without giving it venue access."""

        snapshot = self._equity_snapshot
        if (snapshot is None or self._baseline_day is None
                or self._day_opening_equity is None
                or self._nontrading_cash is None):
            return
        marked = snapshot.marked_trading_pnl(
            opening_equity=self._day_opening_equity,
            nontrading_cash=self._nontrading_cash)
        realized = unrealized = None
        if (self._cash_baseline is not None
                and self._day_opening_equity == self._cash_baseline):
            realized = snapshot.realized_trading_pnl(
                opening_cash=self._cash_baseline,
                nontrading_cash=self._nontrading_cash)
            unrealized = snapshot.open_unrealized_pnl()
            if realized is None or unrealized is None:
                realized = unrealized = None
        try:
            write_live_pnl_snapshot(
                LIVE_PNL_CACHE_PATH,
                observed_at=snapshot.observed_at,
                risk_date=self._baseline_day,
                marked_pnl=marked,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                nontrading_cash=self._nontrading_cash,
            )
            if self._live_pnl_cache_error:
                print(
                    f"[live-pnl-cache] at={self._now().isoformat()} "
                    "state=recovered",
                    flush=True,
                )
            self._live_pnl_cache_error = False
        except (OSError, TypeError, ValueError) as exc:
            # Local display is observation only. Log only the transition so a
            # read-only disk fault cannot flood the trading log every sweep.
            if not self._live_pnl_cache_error:
                print(
                    f"[live-pnl-cache] at={self._now().isoformat()} "
                    f"state=failed reason={type(exc).__name__}:{exc}",
                    flush=True,
                )
            self._live_pnl_cache_error = True

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

    def _refresh_loss_entry_order_cap(self, observed_at: datetime) -> None:
        """Tighten the durable ET-day entry-order budget from marked HARD use."""

        guard = self.risk_guard
        day = self._baseline_day
        if (guard is None or day is None or guard.current_equity is None
                or guard.hard_stop_loss is None):
            return
        if date.fromisoformat(risk_day(observed_at)) != day:
            return
        loss = max(
            Decimal("0"), guard.starting_equity - guard.current_equity)
        fraction = loss / guard.hard_stop_loss
        full_cap = self.config.full_entry_order_capacity
        candidate = self.config.entry_order_cap_for_loss(loss)
        stored_before = self.ledger.entry_order_cap_for_day(
            day.isoformat(), full_cap=full_cap)
        effective = self.ledger.ratchet_entry_order_cap(
            day.isoformat(), candidate_cap=candidate, full_cap=full_cap,
            loss_fraction=fraction, observed_at=observed_at)

        previous_day = self._loss_entry_order_cap_day
        previous = self._loss_entry_order_cap
        transition_from = (
            stored_before if previous_day is None
            else previous)
        self._loss_entry_order_cap_day = day
        self._loss_entry_order_cap = effective
        if transition_from == effective:
            return

        action = "et_day_reset" if effective > transition_from else "reduce"
        print(
            f"[loss-entry-order-cap] at={observed_at.isoformat()} "
            f"day={day.isoformat()} marked_loss={loss} "
            f"hard={guard.hard_stop_loss} "
            f"utilization={fraction:.4f} from={transition_from} "
            f"to={effective} action={action} "
            "positions_and_reducers=excluded",
            flush=True,
        )
        self._loss_entry_order_cap_notice = (
            day, transition_from, effective, fraction)
        # Wake every flat owner so an over-cap slate starts draining without
        # waiting for a book/timer event. The existing single-mutation driver
        # keeps cancellation bounded and serial.
        self._dirty_market_slugs.update(
            slug for slug in self.active_slugs
            if self.ledger.position(slug) == 0)

    def _effective_entry_order_cap(self, observed_at: datetime) -> int:
        """Return the tighter of the durable loss and clock-based ceilings."""

        policy_cap = min(
            self._loss_entry_order_cap,
            self.config.entry_order_cap_for_time(observed_at),
        )
        ceiling = self.PENDING_ENTRY_ORDER_CEILING
        return policy_cap if ceiling is None else min(policy_cap, ceiling)

    def _refresh_overnight_entry_order_cap(
        self, observed_at: datetime,
    ) -> None:
        """Record midnight/09:00 ET transitions without changing loss state."""

        time_cap = self.config.entry_order_cap_for_time(observed_at)
        active = self.config.entry_order_cap_is_overnight(observed_at)
        previous = self._overnight_entry_order_cap_active
        self._overnight_entry_order_cap_active = active
        if previous is active:
            return
        # An initial daytime observation merely establishes state. Starting
        # inside the overnight window is material and should be announced.
        if previous is None and not active:
            return

        effective = self._effective_entry_order_cap(observed_at)
        print(
            f"[overnight-entry-order-cap] at={observed_at.isoformat()} "
            "window=00:00-09:00_ET "
            f"active={str(active).lower()} time_cap={time_cap} "
            f"loss_cap={self._loss_entry_order_cap} effective={effective} "
            "positions_and_reducers=excluded",
            flush=True,
        )
        self._overnight_entry_order_cap_notice = (
            active, effective, self._loss_entry_order_cap, time_cap)
        self._dirty_market_slugs.update(
            slug for slug in self.active_slugs
            if self.ledger.position(slug) == 0)

    def _flat_entry_orders_and_reservations(
        self,
    ) -> tuple[tuple[Any, ...], int]:
        """Return working flat entry orders and unmatched PLACE reservations."""

        positioned = {
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        }
        working = tuple(
            row for row in self.ledger.working_orders(owned_only=True)
            if str(row["market_slug"]) not in positioned)
        active_clients = {str(row["client_order_id"]) for row in working}
        reservations = {
            str(row["client_order_id"])
            for row in self.ledger.unresolved_intents()
            if str(row["operation"]) == "PLACE"
            and str(row["market_slug"]) not in positioned
            and str(row["client_order_id"]) not in active_clients
        }
        reservations.update(
            str(row["client_order_id"]) for row in self.ledger.absent_orders()
            if str(row["market_slug"]) not in positioned
            and str(row["client_order_id"]) not in active_clients)
        return working, len(reservations)

    def _loss_cap_overflow_order_ids(
        self, observed_at: datetime | None = None,
    ) -> frozenset[str]:
        """Choose lowest-ranked flat entry orders beyond the effective cap."""

        working, reservations = self._flat_entry_orders_and_reservations()
        now = self._now() if observed_at is None else observed_at
        keep_count = max(
            0, self._effective_entry_order_cap(now) - reservations)
        if len(working) <= keep_count:
            return frozenset()
        rank = {slug: index for index, slug in enumerate(self.active_slugs)}

        def priority(row: Any) -> tuple[int, float, str, str]:
            slug = str(row["market_slug"])
            raw_side = str(row["side"] or "").lower()
            side = "bid" if raw_side in {"buy", "bid"} else "offer"
            expected = self.state_for(slug).expected_net_by_side.get(
                side, float("-inf"))
            return (
                rank.get(slug, len(rank)),
                -expected,
                str(row["event_at"]),
                str(row["exchange_order_id"]),
            )

        ordered = sorted(working, key=priority)

        # Allocate complete market pairs before one-sided leftovers. This
        # avoids keeping one higher-ranked orphan and breaking a complete pair
        # immediately below it when an even cap can retain the pair instead.
        # Within each class, active selection rank and expected value still
        # decide priority.
        by_market: dict[str, list[Any]] = {}
        for row in ordered:
            by_market.setdefault(str(row["market_slug"]), []).append(row)
        retained_ids: set[str] = set()
        remaining = keep_count
        ranked_slugs = tuple(dict.fromkeys(
            (*self.active_slugs, *(str(row["market_slug"]) for row in ordered))))
        for slug in ranked_slugs:
            rows = by_market.get(slug, [])
            bids = [row for row in rows
                    if str(row["side"] or "").lower() in {"buy", "bid"}]
            offers = [row for row in rows
                      if str(row["side"] or "").lower()
                      not in {"buy", "bid"}]
            if not bids or not offers:
                continue
            pair = (min(bids, key=priority), min(offers, key=priority))
            pair_ids = {
                str(row["exchange_order_id"]) for row in pair
                if row["exchange_order_id"]
            }
            if remaining >= len(pair_ids):
                retained_ids.update(pair_ids)
                remaining -= len(pair_ids)
        for row in ordered:
            order_id = str(row["exchange_order_id"] or "")
            if (remaining <= 0 or not order_id
                    or order_id in retained_ids):
                continue
            retained_ids.add(order_id)
            remaining -= 1
        return frozenset(
            str(row["exchange_order_id"])
            for row in ordered
            if row["exchange_order_id"]
            and str(row["exchange_order_id"]) not in retained_ids)

    def _loss_cap_cancel_action(
        self, frame: MarketFrame,
    ) -> PlanAction | None:
        """Return one owned overflow cancel without touching position markets."""

        if frame.position != 0 or not frame.state_known:
            return None
        overflow = self._loss_cap_overflow_order_ids(frame.now)
        for order in reversed(frame.working_orders):
            if order.order_id in overflow:
                time_cap = self.config.entry_order_cap_for_time(frame.now)
                reason = (
                    "overnight_entry_order_cap"
                    if (time_cap < self._loss_entry_order_cap
                        and self.config.entry_order_cap_is_overnight(frame.now))
                    else "daytime_entry_order_cap"
                    if time_cap < self._loss_entry_order_cap
                    else "loss_scaled_entry_order_cap"
                )
                if self._effective_entry_order_cap(frame.now) < min(
                        time_cap, self._loss_entry_order_cap):
                    reason = "account_source_entry_order_cap"
                return PlanAction(
                    ActionKind.CANCEL,
                    frame.market.slug,
                    reason,
                    side=order.side,
                    order_id=order.order_id,
                )
        return None

    def _loss_cap_blocks_new_entry(
        self, observed_at: datetime | None = None,
    ) -> bool:
        working, reservations = self._flat_entry_orders_and_reservations()
        now = self._now() if observed_at is None else observed_at
        return (
            len(working) + reservations
            >= self._effective_entry_order_cap(now)
        )

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

            realised = cash delta + economic cost of open inventory

        Open inventory has already spent cash, so it is added back at its own
        basis -- the same shape realized_pnl uses, but with cash taken from the
        venue instead of a total rebuilt from fills.

        Falls back to the ledger whenever cash or a basis is unavailable. A
        stop that raises is worse than one computed the old way.
        """

        ledger_value = (
            self.ledger.realized_pnl(since=self._cash_activity_boundary)
            if self._cash_activity_boundary is not None
            else self.ledger.realized_pnl() - self._realized_baseline
        )
        if (self.balance is None or self._cash_baseline is None
                or self._equity_snapshot is None):
            return ledger_value
        if (self._nontrading_cash is None
                or self._nontrading_cash_day != self._baseline_day):
            raise RuntimeError("non-trading cash adjustment is unavailable")
        try:
            realised = self._equity_snapshot.realized_trading_pnl(
                opening_cash=self._cash_baseline,
                nontrading_cash=self._nontrading_cash)
            # Cannot value what we hold at basis, so cash alone would read the
            # entry cost as a loss. Use the fill-ledger fallback.
            return ledger_value if realised is None else realised
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

        The former session stop and HARD used to baseline per PROCESS, which is
        what "a restart forgives the day's drawdown" means. Ten sessions ran on
        2026-08-18, so the $40 budget was handed back ten times; the $45 HARD
        was worse,
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
        if self._provisional_hard_stop_at is not None:
            # Preserve the breached day's baseline until its position cohort
            # closes. Resetting at ET midnight while it remains open would
            # forgive the breach before the required confirmation.
            if self._baseline_day is not None:
                return
            today = self._provisional_hard_stop_day
            if today is None or not self.ledger.query(
                    "SELECT 1 FROM execution_risk_day_baselines WHERE risk_date=?",
                    (today.isoformat(),)):
                raise RuntimeInvariantViolation(
                    "provisional_hard_baseline_missing", "breached baseline is unavailable")
        if self._baseline_day == today:
            return
        if self.balance is None or self._equity_snapshot is None:
            # Never write a baseline we cannot measure. `INSERT OR IGNORE`
            # makes the first write of a risk date permanent, so a zero stored
            # here could not be corrected for the rest of that day -- and a
            # zero opening equity makes `starting_equity - current_equity`
            # negative, which disables the emergency stop silently until
            # midnight. Both call sites fetch the balance immediately before
            # this, so skipping simply defers to the next pass.
            return
        equity = self._equity_snapshot.marked_equity
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
        baseline_rows = self.ledger.query(
            "SELECT opening_cash,created_at,source "
            "FROM execution_risk_day_baselines WHERE risk_date=?",
            (today.isoformat(),),
        )
        if len(baseline_rows) != 1:
            raise RuntimeError("durable ET cash baseline is unavailable")
        baseline_row = baseline_rows[0]
        source = str(baseline_row["source"] or "")
        if opening_cash is not None:
            opening_cash = corrected_opening_cash(
                today.isoformat(), opening_cash, source)
        if source == "legacy_utc_cutover":
            cash_boundary = datetime.combine(
                today, datetime.min.time(), tzinfo=NEW_YORK).astimezone(
                    timezone.utc)
        elif source == "fresh_et_day":
            cash_boundary = datetime.fromisoformat(
                str(baseline_row["created_at"]))
            if (cash_boundary.tzinfo is None
                    or cash_boundary.utcoffset() is None):
                raise RuntimeError("durable cash baseline time is timezone-naive")
            # `fetch_nontrading_cash` treats this lower boundary as exclusive:
            # opening cash already includes activity completed at this exact
            # response receipt. Do not move the boundary into the future; the
            # following snapshot can legitimately share the same clock tick.
            cash_boundary = cash_boundary.astimezone(timezone.utc)
        else:  # corrected_opening_cash handles this when cash is present.
            raise RuntimeError(f"unknown ET baseline source: {source}")
        self._baseline_day = today
        self._realized_baseline = opening_realized
        self._cash_baseline = opening_cash
        self._cash_activity_boundary = cash_boundary
        self._nontrading_cash = None
        self._nontrading_cash_observed_at = None
        self._nontrading_cash_day = None
        self._day_opening_equity = opening_equity
        # The hard stop measures against this. Without it the emergency
        # ceiling stays pinned to whatever equity this process happened to
        # start at, which is the ratchet above.
        if self.risk_guard is not None:
            self.risk_guard.adopt_day_baseline(opening_equity)

    def _latch_hard_stop(self, now: datetime) -> None:
        """Commit before cancelling/flattening; preserve the breached meter day."""

        day = self._baseline_day or date.fromisoformat(risk_day(now))
        self.ledger.record_hard_stop_hold(day.isoformat(), at=now)
        self._hard_stop_hold_day = day
        self._hard_stop_hold_at = now
        self._hard_stop_hold_notice = "held"
        self._provisional_hard_stop_at = None
        self._provisional_hard_stop_markets.clear()
        self._provisional_hard_stop_notice = None
        self._entry_pause_cancel_requested = True
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, now)
            if slug in self._retired:
                self._retired[slug].next_retry_at = now
        self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
        print(f"[daily-hard-stop] at={now.isoformat()} day={day} "
              "state=held action=cancel_entries_and_flatten", flush=True)

    def _maybe_release_hard_stop(self, now: datetime) -> None:
        """Next ET day is necessary, not sufficient: fresh flat proof first."""

        day = self._hard_stop_hold_day
        today = date.fromisoformat(risk_day(now))
        if (day is None or today <= day or self._baseline_day != today
                or self._nontrading_cash_day != today
                or self._nontrading_cash is None or self.risk_guard is None
                or not self.risk_guard.can_add_new_risk(now=now)
                or self._streams_need_rebind
                or not self.gate.ready(self.ledger, now=now)):
            return
        reconciled = self.gate.last_reconcile_at
        equity_at = self.risk_guard.equity_observed_at
        if (reconciled is None or equity_at is None
                or self._hard_stop_hold_at is None
                or reconciled < self._hard_stop_hold_at
                or equity_at < reconciled
                or risk_day(reconciled) != today.isoformat()
                or self._equity_snapshot is None
                or any(position.net_qty != 0
                       for position in self._equity_snapshot.positions)
                or self.ledger.working_orders() or self.ledger.absent_orders()
                or self.ledger.unresolved_intents()
                or self.ledger.query("SELECT 1 FROM execution_positions "
                                     "WHERE CAST(net_qty AS REAL)!=0 LIMIT 1")):
            return
        self._assert_runtime_scope()
        self.ledger.release_hard_stop_hold(day.isoformat(), at=now)
        self._hard_stop_hold_day = None
        self._hard_stop_hold_at = None
        self._hard_stop_hold_notice = "released"
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, now)
        self._publish_active_scope_snapshot(
            status="ENTRY_PAUSED" if self._entry_pause_active() else "RUNNING")
        print(f"[daily-hard-stop] at={now.isoformat()} day={day} "
              "state=released proof=fresh_next_day_flat_account", flush=True)

    def _stop_reason(self, now: datetime) -> str | None:
        if self._stop_requested:
            return self._stop_requested
        if self.risk_guard is None:
            return "risk_guard_missing"
        if (self._market_fault_pause_until is not None
                and now >= self._market_fault_pause_until
                and not self._streams_need_rebind
                and self.gate.ready(self.ledger, now=now)
                and self.gate.last_reconcile_at is not None
                and self.gate.last_reconcile_at >= self._market_fault_pause_until
                and self.risk_guard.can_add_new_risk(now=now)):
            self._market_fault_pause_until = None
            # Retired owners and their ambiguity/cooldowns remain barred. Only
            # normal discovery may find healthy replacements after this hold.
            self._publish_active_scope_snapshot()
            print(f"[market-fault-recovery] at={now.isoformat()} "
                  "state=released proof=fresh_account_after_cooldown", flush=True)
        self._maybe_release_hard_stop(now)
        open_position_slugs = {
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        }
        hard_hit = (self._hard_stop_hold_day is None
                    and self.risk_guard.hard_stop_reached(now=now))
        if hard_hit and open_position_slugs:
            if self._provisional_hard_stop_at is None:
                day = self._baseline_day or date.fromisoformat(risk_day(now))
                self.ledger.record_provisional_hard_stop(
                    day.isoformat(), at=now, markets=open_position_slugs)
                self._provisional_hard_stop_day = day
                self._provisional_hard_stop_at = now
                self._provisional_hard_stop_markets = set(open_position_slugs)
                self._provisional_hard_stop_notice = "started"
                print(
                    f"[hard-stop-provisional] at={now.isoformat()} "
                    f"markets={','.join(sorted(open_position_slugs))} "
                    "action=block_new_risk_and_wait_for_closed_position",
                    flush=True,
                )
            else:
                # An already-resting entry can fill while its cancellation is
                # in flight. It is exposed to the same marked breach and must
                # join the set whose closure can settle the provisional state.
                if not open_position_slugs <= self._provisional_hard_stop_markets:
                    self.ledger.record_provisional_hard_stop(
                        self._provisional_hard_stop_day.isoformat(),
                        at=self._provisional_hard_stop_at,
                        markets=self._provisional_hard_stop_markets | open_position_slugs)
                    self._provisional_hard_stop_markets.update(open_position_slugs)
        elif hard_hit and self._provisional_hard_stop_at is None:
            # No open inventory can explain this as temporary paper movement.
            self._latch_hard_stop(now)

        if self._provisional_hard_stop_at is not None:
            reconcile_at = self.gate.last_reconcile_at
            equity_at = self.risk_guard.equity_observed_at
            closed: set[str] = set()
            if (reconcile_at is not None and equity_at is not None
                    and equity_at >= reconcile_at >= self._provisional_hard_stop_at):
                for slug in self._provisional_hard_stop_markets:
                    rows = self.ledger.query(
                        "SELECT net_qty,received_at FROM execution_positions "
                        "WHERE market_slug=?",
                        (slug,),
                    )
                    if len(rows) != 1 or Decimal(str(rows[0]["net_qty"])) != 0:
                        continue
                    position_at = datetime.fromisoformat(
                        str(rows[0]["received_at"]))
                    if (position_at.tzinfo is not None
                            and position_at.utcoffset() is not None
                            and reconcile_at >= position_at >= self._provisional_hard_stop_at):
                        closed.add(slug)
            # A recovered mark by itself is not enough: the same open trade can
            # bounce above and below the line. Require a breach-time position
            # to have actually closed, then accept the fresh adjusted balance.
            if hard_hit and closed:
                self._latch_hard_stop(now)
            if not hard_hit and closed:
                self.ledger.clear_provisional_hard_stop()
                self._provisional_hard_stop_at = None
                self._provisional_hard_stop_markets.clear()
                self._provisional_hard_stop_notice = "cleared"
                print(
                    f"[hard-stop-cleared] at={now.isoformat()} "
                    f"closed={','.join(sorted(closed))} "
                    "reason=adjusted_equity_recovered_after_position_close",
                    flush=True,
                )
        today = risk_day(now)
        if (self._execution_fee_observation_day is not None
                and self._execution_fee_observation_day != today):
            self._execution_fee_observation_day = None
        day_start, _ = reward_day_bounds(today)
        daily_rows = self.ledger.query(
            "SELECT f.client_order_id,f.fee FROM execution_fills f "
            "WHERE f.received_at>=? AND f.received_at<=? "
            "AND f.reversed=0 AND f.liquidity='TAKER' "
            "AND EXISTS (SELECT 1 FROM execution_intents i "
            "WHERE i.operation='PLACE' "
            "AND i.client_order_id=f.client_order_id)",
            (day_start.isoformat(), now.astimezone(timezone.utc).isoformat()),
        )
        positive_daily_fees = sum(
            (max(Decimal("0"), Decimal(str(row["fee"])))
             for row in daily_rows),
            Decimal("0"),
        )
        if positive_daily_fees >= self.config.execution_fee_observation_usd:
            if self._execution_fee_observation_day != today:
                self._execution_fee_observation_pending = (
                    today, positive_daily_fees)
                print(
                    f"[execution-fee-observation] at={now.isoformat()} "
                    f"day={today} "
                    f"gross_taker_fees={positive_daily_fees} "
                    f"reference={self.config.execution_fee_observation_usd} "
                    "action=observe_only",
                    flush=True,
                )
            self._execution_fee_observation_day = today
        blockers = self.risk_guard.blockers(now=now)
        # Missing/stale equity is recoverable only by staying alive with new
        # risk disabled. A clock failure or explicit critical condition still
        # enters terminal flatten-and-stop handling.
        transient = {
            "marked account equity is unknown",
            "marked account equity is stale",
        }
        terminal = tuple(item for item in blockers if item not in transient)
        if terminal:
            return "risk_stop:" + ",".join(terminal)
        return None

    async def _announce_execution_fee_observation(self) -> None:
        """Tell Discord once per ET day that fees crossed the reference."""

        pending, self._execution_fee_observation_pending = (
            self._execution_fee_observation_pending, None)
        if pending is None:
            return
        day, fees = pending
        if self._execution_fee_observation_announced_day == day:
            return
        self._execution_fee_observation_announced_day = day
        await self.notifier.send_async(
            f"ℹ️ Liquidity Bot gross taker fees reached ${fees} for {day}, "
            f"above the ${self.config.execution_fee_observation_usd} "
            "telemetry reference. This does not pause trading; the hard-stop "
            "protection remains enforced.")

    async def _announce_loss_entry_order_cap(self) -> None:
        """Notify once for each in-process ET-day order-cap transition."""

        notice, self._loss_entry_order_cap_notice = (
            self._loss_entry_order_cap_notice, None)
        if notice is None:
            return
        day, previous, current, fraction = notice
        if current < previous:
            detail = (
                f"reduced from {previous} to {current} after marked loss "
                f"reached {fraction:.1%} of HARD. This cap remains through "
                "the ET day")
        else:
            detail = f"reset from {previous} to {current} for the new ET day"
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot entry-order cap {detail} ({day}). "
            "Open-position reducers and exits are excluded.")

    async def _announce_overnight_entry_order_cap(self) -> None:
        """Notify on each midnight/09:00 ET ceiling transition."""

        notice, self._overnight_entry_order_cap_notice = (
            self._overnight_entry_order_cap_notice, None)
        if notice is None:
            return
        active, effective, loss_cap, time_cap = notice
        if active:
            detail = (
                "is active until 9:00 AM ET; pending flat-entry orders are "
                f"limited to {effective}"
            )
        else:
            detail = (
                f"ended at 9:00 AM ET; the daytime cap is {time_cap} and "
                f"the effective cap is {effective} after the remaining "
                f"loss-based cap of {loss_cap}"
            )
        await self.notifier.send_async(
            f"ℹ️ Liquidity Bot midnight-9:00 AM ET order cap {detail}. "
            "Open positions, reducers, and exits are excluded.")

    async def _announce_provisional_hard_stop(self) -> None:
        """Announce a paper breach once, then its position-close recovery."""

        hold_notice, self._hard_stop_hold_notice = self._hard_stop_hold_notice, None
        if hold_notice == "held":
            await self.notifier.send_async(
                "⏸️ HARD loss confirmed. Cancelling pending entries and exiting "
                "all positions with terminal confirmation. Trading stays paused "
                "for the ET day, including across restarts. Next-day entry "
                "requires a fresh flat account and healthy trading gates.",
                critical=True)
        elif hold_notice == "released":
            await self.notifier.send_async(
                "✅ Daily HARD pause cleared for the new ET day after fresh "
                "flat-account proof. Other entry and maintenance gates still apply.")
        notice, self._provisional_hard_stop_notice = (
            self._provisional_hard_stop_notice, None)
        if notice == "started" and not self._provisional_hard_stop_announced:
            self._provisional_hard_stop_announced = True
            await self.notifier.send_async(
                "⚠️ Liquidity Bot marked equity crossed the hard-stop limit "
                "with open inventory. New entries are blocked immediately; "
                "existing positions keep their normal exits. The breach is "
                "provisional until a breach-time position closes and the "
                "adjusted account loss is measured again.",
                critical=True,
            )
        elif notice == "cleared":
            self._provisional_hard_stop_announced = False
            await self.notifier.send_async(
                "✅ Liquidity Bot cleared the provisional hard stop after a "
                "position closed. The fresh adjusted account loss was below "
                "the hard-stop limit, so quoting may resume.")

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

    def _all_active_markets_blocked_by_no_id_intents(self) -> bool:
        """True when no active owner can produce id-based repair or mutate."""

        active = set(self.active_slugs)
        if not active:
            return False
        blocked = {
            str(row["market_slug"] or "").strip()
            for row in self.ledger.unresolved_intents()
            if not str(row["exchange_order_id"] or "").strip()
        }
        return active.issubset(blocked)

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
        unresolved_owners = self._unresolved_intent_owners()
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
        self._record_resolved_intent_owners(unresolved_owners)
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

    async def _log_api_budget(self, now: datetime) -> None:
        """Emit limiter evidence without making a diagnostic API request."""

        read = getattr(self.gateway.limiter, "shared_telemetry", None)
        if callable(read):
            evidence = await read(reset=True)
        else:
            read = getattr(self.gateway.limiter, "telemetry", None)
            if not callable(read):
                return
            evidence = read(reset=True)
        categories = ",".join(
            f"{name}:{count}"
            for name, count in evidence.attempts_by_category
        ) or "none"
        print(
            f"[api-budget] at={now.isoformat()} "
            f"slots={self.config.max_markets} "
            f"seconds={evidence.elapsed_seconds:.1f} "
            f"attempts={evidence.attempts} "
            f"rps={evidence.attempted_requests_per_second:.3f} "
            f"reads={evidence.read_attempts} "
            f"mutations={evidence.mutation_attempts} "
            f"failures={evidence.failed_responses} "
            f"rate_limited={evidence.rate_limited_responses} "
            f"wait_p95={evidence.wait_p95_seconds:.3f} "
            f"wait_p99={evidence.wait_p99_seconds:.3f} "
            f"wait_max={evidence.max_wait_seconds:.3f} "
            f"response_p95={evidence.response_p95_seconds:.3f} "
            f"response_p99={evidence.response_p99_seconds:.3f} "
            f"response_max={evidence.max_response_seconds:.3f} "
            f"waiting={evidence.waiting_now} "
            f"max_waiting={evidence.max_waiting} "
            f"cooldown={evidence.cooldown_remaining_seconds:.1f} "
            f"cooldown_safety={evidence.safety_cooldown_remaining_seconds:.1f} "
            f"cooldown_entry={evidence.entry_cooldown_remaining_seconds:.1f} "
            f"cooldown_background={evidence.background_cooldown_remaining_seconds:.1f} "
            f"cooldown_report={evidence.report_cooldown_remaining_seconds:.1f} "
            f"shared={int(evidence.shared_across_processes)} "
            f"categories={categories}",
            flush=True,
        )

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

    @classmethod
    def _rejection_cooldown_seconds(cls, reason: str | None) -> float:
        """Map a structured venue rejection code to a market-local delay."""

        code = str(reason or "").strip().upper()
        if "POST_ONLY" in code:
            return cls.POST_ONLY_REJECTION_COOLDOWN_SECONDS
        if any(token in code for token in (
                "INSUFFICIENT", "BALANCE", "BUYING_POWER", "RISK_LIMIT")):
            return cls.BALANCE_REJECTION_COOLDOWN_SECONDS
        return cls.VALIDATION_REJECTION_COOLDOWN_SECONDS

    def _recent_rejection_delay(self, slug: str, now: datetime) -> float:
        """Return the unexpired local cooldown for the latest rejection."""

        row = self.ledger.latest_rejection(slug)
        if row is None:
            return 0.0
        try:
            event_at = datetime.fromisoformat(str(row["event_at"]))
            age = (now - event_at).total_seconds()
        except (TypeError, ValueError):
            return self.VALIDATION_REJECTION_COOLDOWN_SECONDS
        cooldown = self._rejection_cooldown_seconds(row["reject_reason"])
        if age < 0:
            return cooldown
        return max(0.0, cooldown - age)

    def _entry_buying_power(
        self, action: PlanAction, *, position: Decimal,
    ) -> tuple[Decimal, Decimal] | None:
        """Required/available buying power for a quote that adds new risk."""

        if (action.kind != ActionKind.PLACE_POST_ONLY or position != 0
                or action.price is None or action.qty is None):
            return None
        required = Decimal(str(action.price)) * action.qty
        available = (
            self.balance.buying_power if self.balance is not None
            else Decimal("-1"))
        return required, available

    @staticmethod
    def _is_mutation_action(action: PlanAction) -> bool:
        return action.kind in {
            ActionKind.CANCEL,
            ActionKind.PLACE_POST_ONLY,
            ActionKind.PLACE_IOC,
        }

    @staticmethod
    def _is_risk_reducing_mutation(
        action: PlanAction, position: Decimal,
    ) -> bool:
        """Return whether the mutation removes orders or existing exposure."""

        return (
            action.kind in {ActionKind.CANCEL, ActionKind.PLACE_IOC}
            or (action.kind == ActionKind.PLACE_POST_ONLY and position != 0)
        )

    def _mutation_access_delay(
        self, actions: list[PlanAction], now: datetime, position: Decimal,
    ) -> float:
        """Return the applicable entry or risk-mutation cooldown."""

        if not actions or not self._is_mutation_action(actions[0]):
            return 0.0
        blocked_until = (
            self._risk_mutation_blocked_until
            if self._is_risk_reducing_mutation(actions[0], position)
            else self._entry_mutation_blocked_until
        )
        if blocked_until is None:
            return 0.0
        return max(
            0.0,
            (blocked_until - now).total_seconds())

    def _clear_mutation_access_denial(
        self, action: PlanAction, position: Decimal,
    ) -> None:
        """A successful later mutation is positive proof access recovered."""

        if not self._is_mutation_action(action):
            return
        risk_reducing = self._is_risk_reducing_mutation(action, position)
        blocked_until = (
            self._risk_mutation_blocked_until
            if risk_reducing else self._entry_mutation_blocked_until)
        if blocked_until is None:
            return
        if self._mutation_access_denial_announced:
            print(
                f"[mutation-access-restored] at={self._now().isoformat()} "
                f"kind={action.kind.name} "
                f"scope={'risk' if risk_reducing else 'entry'}",
                flush=True,
            )
        if risk_reducing:
            self._risk_mutation_blocked_until = None
        else:
            self._entry_mutation_blocked_until = None
            self._mutation_access_denial_announced = False
            self._runtime_error_counts.pop("mutation_access_denied", None)

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

    async def _refresh_event_guard(self) -> None:
        if not isinstance(self.gateway, EventAwareGateway):
            return
        try:
            await self.gateway.refresh_event_states(self.managed_slugs())
        except AdapterError as exc:
            # Invalidated freshness cancels/blocks entries, not reductions.
            await self._report_runtime_error("event_lifecycle", exc)
        for owner in self._markets.values():
            if owner.market is None:
                continue
            previous = owner.market.event_started_at
            owner.market = self.gateway.apply_event_status(owner.market)
            if owner.market.event_started_at is not None and previous is None:
                self._mark_market_dirty(owner.slug, self._now())
                if owner.slug in self._retired:
                    self._retired[owner.slug].next_retry_at = self._now()
                self._request_lifecycle_top_up(self._now())
                print(f"[event-started] at={self._now().isoformat()} "
                      f"market={owner.slug} action=cancel_entries_and_exit", flush=True)

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
        if (retired is not None and retired.rounds >= MAX_RETIREMENT_ROUNDS
                and self.ledger.position(slug) == 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.absent_orders(slug)
                and not self.ledger.unresolved_intents(slug)):
            # Only positively empty local owners may become dormant. Held or
            # ambiguous owners keep their normal proof and slow exit service.
            return None, 0.0
        if (retired is not None and retired.next_retry_at is not None
                and now < retired.next_retry_at):
            # Inside the cooldown the position simply rests. Its reducing quote
            # is already working and the pre-kickoff flatten is unaffected;
            # replanning here would re-fire the same condition immediately.
            return None, 0.0
        try:
            await self._refresh_event_guard()
            if self._stop_requested:
                return None, 0.0
            now = self._now()
            frame = self._frame(now, slug)
            overflow_cancel = self._loss_cap_cancel_action(frame)
            actions = (
                [overflow_cancel] if overflow_cancel is not None
                else self.reconciler.plan(frame))
            if (actions
                    and actions[0].kind == ActionKind.PLACE_POST_ONLY
                    and frame.position == 0
                    and self._loss_cap_blocks_new_entry(frame.now)):
                # A cap is a ceiling, not a target. Existing normal cancels
                # still flow through the reconciler; only a building PLACE is
                # suppressed. The next ledger/private input dirties the owner
                # and retries after real capacity exists.
                actions = []
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
                    return None, self._hard_exit_retry_delay(slug)
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
                fresh_enough = self._book_is_fresh_enough_to_plan(slug)
                if not fresh_enough:
                    print(
                        f"[book-stale-why] at={now_at.isoformat()} market={slug} "
                        f"age={'none' if age is None else round(age, 1)} "
                        f"refresh_due_in="
                        f"{'none' if due is None else round((due - now_at).total_seconds(), 1)} "
                        f"fresh_enough={fresh_enough} replanned={replanned}",
                        flush=True,
                    )
            if (not replanned and reason == "book_stale"
                    and fresh_enough):
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
        # One snapshot is reused by rejection/buying-power protection,
        # observation classification and the action driver. A nonzero position
        # makes PLACE_POST_ONLY reducing; no entry rejection cooldown or buying
        # power gate may delay that exit path.
        position = self.ledger.position(slug)
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
            mutation_delay = self._mutation_access_delay(
                actions, now, position=position)
            if mutation_delay > 0:
                print(
                    f"[mutation-access-backoff] at={now.isoformat()} "
                    f"market={slug} kind={chosen.kind.name} "
                    f"seconds={mutation_delay:.1f}",
                    flush=True,
                )
                return None, mutation_delay
            if (chosen.kind == ActionKind.PLACE_POST_ONLY and position == 0):
                if not state.market.event_allows_entry(self._now()):
                    return None, 1.0
                if self.config.entry_quality_policy.enabled:
                    # Recheck the actual action after planning; do not consume
                    # cached shadow labels or add any venue request. Safety
                    # actions bypass this entry-only boundary completely.
                    book_age = state.book_age_seconds(self._now())
                    if (state.book is None or state.book.slug != slug
                            or book_age is None or not 0 <= book_age <= 5
                            or chosen.side not in {"bid", "offer"}
                            or chosen.price is None or chosen.qty is None):
                        return None, 1.0
                    entry_exit = cross_exit(
                        state.book, entry_side=chosen.side,
                        entry_price=float(chosen.price), qty=float(chosen.qty),
                        depth_haircut=.5, exclude_entry_level=True)
                    quality_blocker = self.config.entry_quality_policy.blocker(entry_exit)
                    if quality_blocker:
                        print(f"[entry-quality] at={now.isoformat()} market={slug} "
                              f"side={chosen.side} reason={quality_blocker}", flush=True)
                        return None, 1.0
                rejection_delay = self._recent_rejection_delay(slug, now)
                if rejection_delay > 0:
                    latest = self.ledger.latest_rejection(slug)
                    print(
                        f"[order-rejection-backoff] at={now.isoformat()} "
                        f"market={slug} reason="
                        f"{latest['reject_reason'] if latest else 'unknown'} "
                        f"seconds={rejection_delay:.1f}",
                        flush=True,
                    )
                    return None, rejection_delay
        try:
            # Reuse one ledger snapshot for both observation classification
            # and the action driver.  Observation must not add another query
            # to each placement or see a different position than the driver.
            power = self._entry_buying_power(actions[0], position=position) \
                if actions else None
            if power is not None and power[1] < power[0]:
                print(
                    f"[buying-power-block] at={now.isoformat()} market={slug} "
                    f"required={power[0]} available={power[1]}",
                    flush=True,
                )
                return None, self.BUYING_POWER_RECHECK_SECONDS
            quote_observation = (
                self._quote_observation_for_action(
                    state, actions[0], self._now(), position)
                if actions else None)
            result = await self.driver.apply_one(
                actions, market=state.market,
                position=position, now=now,
                quote_observation=quote_observation)
            if (actions and result is not None
                    and getattr(result, "cancel_intent_id", None) is not None
                    and actions[0].reason
                    == "hard_timeout_cancel_confirm_first"):
                # The grace belongs to the exact request that crossed the
                # mutation boundary, not to a plan that local admission may
                # have deferred. The reconciler will wait for terminal proof
                # of this order before selecting the next hard-exit cancel.
                self.reconciler.note_hard_exit_cancel_submitted(
                    slug,
                    actions[0].order_id or "",
                    submitted_at=self._now(),
                )
            if actions and result is not None:
                self._clear_mutation_access_denial(
                    actions[0], position=position)
            # The venue balance refresh is periodic. Reserve successful new
            # quote notional locally so later slots in this same sweep cannot
            # all spend the same stale buying-power observation. A rejection or
            # cancel is restored by the next authenticated five-second read.
            if (power is not None and result is not None
                    and result.submitted is not None
                    and self.balance is not None):
                self.balance = AccountBalance(
                    current_balance=self.balance.current_balance,
                    asset_notional=self.balance.asset_notional,
                    buying_power=max(Decimal("0"), power[1] - power[0]),
                    observed_at=self.balance.observed_at,
                )
        except Exception as exc:
            stop = await self._handle_action_failure(
                exc,
                slug,
                risk_reducing=(
                    bool(actions)
                    and self._is_risk_reducing_mutation(
                        actions[0], position)),
            )
            if stop:
                return stop, 0.0
            return None, self._recovery_delay(f"action_driver:{slug}")
        if actions:
            # Stamp a successful mutation, not an empty healthy plan.
            self._last_action_at[slug] = self._now()
            self._market_action_generation[slug] = (
                self._market_action_generation.get(slug, 0) + 1)
        return None, 0.0

    def _position_priority_tokens(self) -> dict[str, tuple[Any, ...]]:
        """Optional account-source scheduling hint, never mutation authority."""

        return {}

    def _position_priority_visit_limit(self, slug: str) -> int:
        """Bound scheduling-only interruptions for one owner per sweep."""

        return 4

    def _position_priority_replan_immediately(
        self, slug: str, before: tuple[Any, ...], after: tuple[Any, ...],
    ) -> bool:
        """Return whether one scheduling-token change needs same-owner service."""

        return False

    def _hard_exit_retry_delay(self, slug: str) -> float:
        """Back off a confirmed terminal IOC before its bounded retry."""

        attempts = self.reconciler.hard_exit_attempts(slug)
        return float(min(2 ** max(attempts - 1, 0), 5))

    async def _decision_sweep(
        self, slugs: tuple[str, ...] | None = None,
    ) -> str | None:
        """Plan every due market without cross-slot intent/backoff blocking."""

        state_refresh_failed = False
        planned_slugs = self.active_slugs if slugs is None else slugs
        pending_slugs = list(planned_slugs)
        priority_seen: dict[str, tuple[Any, ...]] = {}
        priority_visits: dict[str, int] = {}
        priority_dispatches: set[str] = set()
        while pending_slugs:
            slug = pending_slugs.pop(0)
            priority_dispatch = slug in priority_dispatches
            priority_dispatches.discard(slug)
            # SIGTERM is delivered by an event-loop callback.  The outer run
            # loop cannot see it while this twenty-market sweep is already in
            # progress, so without this boundary the remaining slots can place
            # fresh risk after an operator has asked the bot to stop.  Observed
            # live 2026-08-25.  Return to the shutdown path; it alone owns the
            # cancellation and flattening work that follows a stop.
            if self._stop_requested:
                return self._stop_requested
            if slug not in self._markets:
                continue
            if self.ledger.market_has_unresolved_intent(slug):
                # The exact intent-resolution path re-dirties this owner. Do
                # not spend every outer pass rediscovering the same deliberate
                # quarantine while its venue outcome is still unknown.
                self._dirty_market_slugs.discard(slug)
                self._dirty_market_since.pop(slug, None)
                continue
            if slug in self._isolated_unexecutable_slugs():
                now = self._now()
                if self._can_wait_unexecutable(slug, now):
                    # No valid mutation path exists. The owner is still
                    # serviced by complete account snapshots, its private
                    # position subscription and one bounded recovery timer;
                    # planning would only manufacture a recurring failure.
                    self._dirty_market_slugs.discard(slug)
                    self._dirty_market_since.pop(slug, None)
                    self._scheduler_markets_skipped += 1
                    continue
                self._leave_settlement_wait(
                    slug, now, "isolation_preconditions_changed")
                self._leave_inactive_market_wait(
                    slug, now, "isolation_preconditions_changed")
            now = self._now()
            due_at = self._next_decision_at.get(slug)
            if due_at is not None and now < due_at:
                # Preserve the deadline rather than the dirty bit. Any newer
                # input is still represented by ``_market_last_input_at`` for
                # telemetry, while the reviewed market-local backoff remains
                # authoritative for behavior.
                self._dirty_market_slugs.discard(slug)
                self._dirty_market_since.pop(slug, None)
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
            # REST can discover a fill while proving a different, flat owner.
            # Re-enter the SAME guarded path for that position before another
            # entry decision. A changed quantity/order state can wake an owner
            # already visited (notably cancel-confirm -> exact reducer).
            # Bound extra visits so changing inventory cannot starve the slate.
            priorities = self._position_priority_tokens()
            if priority_dispatch and slug not in priorities:
                # Inventory can flatten during the proof above. A position
                # interruption must not turn into an extra flat entry pass.
                self._mark_market_dirty(slug, self._now())
                continue
            if slug in priorities:
                priority_seen[slug] = priorities[slug]
            else:
                urgent = next((owner for owner, token in priorities.items()
                               if priority_seen.get(owner) != token
                               and priority_visits.get(owner, 0)
                               < self._position_priority_visit_limit(owner)), None)
                if urgent is not None:
                    priority_seen[urgent] = priorities[urgent]
                    priority_visits[urgent] = priority_visits.get(urgent, 0) + 1
                    priority_dispatches.add(urgent)
                    pending_slugs = [urgent, slug, *(
                        owner for owner in pending_slugs if owner != urgent)]
                    # No guard, backoff, proof or stop check is bypassed.
                    continue
            try:
                # Account reconciliation can take seconds. Its pre-await time
                # must not make an expired equity mark appear fresh.
                await self._refresh_equity_for_decision(self._now())
            except Exception:
                # Preserve the last observation until its 15-second bound;
                # once stale, `_frame` removes entry eligibility fail-closed.
                pass
            previous_action_generation = self._market_action_generation.get(
                slug, 0)
            outcome, delay = await self._decide_for_market(slug, self._now())
            finished_at = self._now()
            action_at = (
                self._last_action_at.get(slug)
                if self._market_action_generation.get(slug, 0)
                != previous_action_generation else None)
            self._scheduler_markets_evaluated += 1
            self._record_scheduler_decision(
                slug, finished_at=finished_at, action_at=action_at)
            self._dirty_market_slugs.discard(slug)
            if action_at is not None:
                # A create response can carry an embedded OPEN/FILL and make
                # its PLACE intent terminal before any later private event.
                # The mutation itself is therefore a decision input: revisit
                # this exact owner so the second side, reducer or next cancel
                # is not forced to wait for an unrelated book/timer event.
                self._dirty_market_slugs.add(slug)
                self._market_dirty_generation[slug] = (
                    self._market_dirty_generation.get(slug, 0) + 1)
                self._dirty_market_since[slug] = action_at
                self._market_last_input_at[slug] = action_at
            # A REST IOC may be submitted and become terminal between account
            # snapshots. Its position and working-order token can therefore
            # look unchanged unless the reconciler's bounded exit state is
            # included by the account-source subclass. Re-read that token
            # after the decision and immediately re-enter this same guarded
            # path when it changed. Unknown intents are still rejected at the
            # top of the loop, and the per-owner limit prevents starvation.
            priority_after = self._position_priority_tokens()
            if (slug in priority_seen and slug in priority_after
                    and priority_seen[slug] != priority_after[slug]
                    and self._position_priority_replan_immediately(
                        slug, priority_seen[slug], priority_after[slug])
                    and priority_visits.get(slug, 0)
                    < self._position_priority_visit_limit(slug)):
                priority_seen[slug] = priority_after[slug]
                priority_visits[slug] = priority_visits.get(slug, 0) + 1
                priority_dispatches.add(slug)
                pending_slugs = [slug, *(
                    owner for owner in pending_slugs if owner != slug)]
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
                resting_sides = frozenset(
                    order.side for order in self._working_orders(slug))
                eligibility_at = (
                    self.reconciler.next_reward_eligibility_deadline(
                        slug, resting_sides=resting_sides))
                if (eligibility_at is not None
                        and eligibility_at > self._now()):
                    self._next_decision_at[slug] = eligibility_at
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
                stop = await self._recover_market_stream(
                    exc, private_task=private_task)
                if stop:
                    return market_task, private_task, stop
                private_task = asyncio.create_task(self.private_stream.next_event())
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

        # A complete REST reconcile can keep the execution gate fresh while a
        # WebSocket shard has silently died. Check transport liveness
        # independently so REST is only a short backstop, never proof that all
        # three private shards and the market connection still deliver data.
        now = self._now()
        liveness_errors: dict[str, Exception] = {}
        for name, stream in (
                ("market", self.market_stream),
                ("private", self.private_stream)):
            assert_live = getattr(stream, "assert_live", None)
            if not callable(assert_live):
                continue
            try:
                assert_live(
                    now=now,
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
            except Exception as exc:
                liveness_errors[name] = exc
        if len(liveness_errors) == 2:
            for task in (market_task, private_task):
                task.cancel()
            await asyncio.gather(
                market_task, private_task, return_exceptions=True)
            combined = AdapterError(
                "both WebSockets exceeded the independent silence limit: "
                f"market={liveness_errors['market']}; "
                f"private={liveness_errors['private']}")
            stop = await self._recover_bound_streams(combined)
            if stop:
                return market_task, private_task, stop
            market_task = asyncio.create_task(self.market_stream.next_book())
            private_task = asyncio.create_task(self.private_stream.next_event())
        elif "market" in liveness_errors:
            market_task.cancel()
            await asyncio.gather(market_task, return_exceptions=True)
            stop = await self._recover_market_stream(
                liveness_errors["market"], private_task=private_task)
            if stop:
                return market_task, private_task, stop
            private_task = asyncio.create_task(self.private_stream.next_event())
            market_task = asyncio.create_task(self.market_stream.next_book())
        elif "private" in liveness_errors:
            private_task.cancel()
            await asyncio.gather(private_task, return_exceptions=True)
            stop = await self._recover_private_stream(liveness_errors["private"])
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
        transient_failure: Exception | None = None
        for slug in self.active_slugs:
            if slug in self._settlement_wait_slugs:
                # The first pass already proved valid metadata plus an exact
                # empty-book response. Preserve that evidence and avoid a
                # duplicate request when normal startup candidates are added.
                healthy.append(slug)
                continue
            try:
                await self._refresh_contracts(
                    now, slug, for_exit=slug in recovering)
                if slug in self._inactive_market_wait_slugs:
                    # Fresh metadata already proved that no order payload can
                    # be accepted. A book cannot change that authority; the
                    # bounded contract refresh is the recovery probe.
                    healthy.append(slug)
                    continue
                await self._refresh_book(now, slug)
                if slug not in recovering:
                    self._validate_entry_book(slug)
                healthy.append(slug)
            except EmptyBookError as exc:
                if slug in recovering and self._enter_settlement_wait(
                        slug, self._now()):
                    healthy.append(slug)
                    continue
                if slug in recovering:
                    raise
                await self._report_runtime_error(
                    f"startup_market_dropped:{slug}", exc)
            except Exception as exc:
                if slug in recovering:
                    raise
                if self._is_transient_recovery_error(exc):
                    transient_failure = exc
                await self._report_runtime_error(
                    f"startup_market_dropped:{slug}", exc)
        if not healthy:
            if transient_failure is not None:
                # Preserve the typed 5xx/transport/429 cause for startup's
                # retry path. Genuine invalid/unsafe candidates still refuse.
                raise AdapterError("startup market reads temporarily unavailable") from transient_failure
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
        if slugs is None and self._post_flat_top_up_can_preempt(self._now()):
            return ""
        await self._refresh_event_guard()
        if self._stop_requested:
            return ""
        await self._refresh_due_contract_batches(
            self._now(), serviced_slugs)
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
            if (slugs is None
                    and self._post_flat_top_up_can_preempt(self._now())):
                return ""
        return ""

    async def _service_market_state(self, slug: str, now: datetime) -> None:
        """Bring one market's contracts, book and economics up to date.

        Each market carries its own REST cadence. A shared schedule would let a
        single quiet book consume the governor on behalf of all of them, and a
        single failing market would reset everyone else's timers.
        """

        state = self.state_for(slug)
        if (state.next_contract_refresh is None
                or now >= state.next_contract_refresh):
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
                    now,
                    slug,
                    for_exit=self._is_barred_from_quoting(slug),
                    accept_bound_lifecycle_end=True,
                )
                self._runtime_error_counts.pop(
                    f"market_incentive_refresh:{slug}", None)
            except _ConfirmedMarketNotOpen:
                state.incentive = None
                state.reward_eligible = frozenset()
                self._runtime_error_counts.pop(
                    f"market_incentive_refresh:{slug}", None)
            except Exception as exc:
                # Only this market stops quoting. The others are unaffected.
                state.incentive = None
                state.reward_eligible = frozenset()
                await self._report_runtime_error(
                    f"market_incentive_refresh:{slug}", exc)
            state.next_contract_refresh = self._now() + timedelta(
                seconds=(
                    CONTRACT_REFRESH_SECONDS
                    if f"market_incentive_refresh:{slug}"
                    not in self._runtime_error_counts
                    else self._contract_refresh_delay(slug)))
        if (slug in self._inactive_market_wait_slugs
                or (self._market_metadata_proves_not_open(state.market)
                    and self._owner_is_flat_and_clear(slug))):
            # Contract metadata, not book depth, is the missing authority. A
            # flat clear owner is already queued for replacement; an exact
            # positioned owner is isolated above. The next scheduled contract
            # refresh is the only useful recovery read for either state.
            return
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
        except EmptyBookError as exc:
            if self._enter_settlement_wait(slug, self._now()):
                self._runtime_error_counts.pop(
                    f"quiet_book_rest_refresh:{slug}", None)
                return
            await self._report_runtime_error(
                f"quiet_book_rest_refresh:{slug}", exc)
            state.next_book_refresh = self._now() + timedelta(seconds=3)
            if (self._runtime_error_counts.get(
                    f"quiet_book_rest_refresh:{slug}", 0)
                    >= MAX_RECOVERY_ATTEMPTS):
                stop = await self._retire_market(
                    slug, "unrecoverable_book_EmptyBookError",
                    reset_exit_budget=False)
                if stop:
                    raise RuntimeInvariantViolation(
                        "market_retirement_limit_exceeded", stop)
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
        try:
            score_seconds_today = self._flush_daily_score(now)
            self._runtime_error_counts.pop("daily_score_persistence", None)
        except Exception as exc:
            # This metric is observation only. In particular, `_send_summary`
            # also runs inside shutdown, where escaping would be misread as a
            # failure to prove flat. Retain pending score for a later retry,
            # use the best available display value and report the fault on the
            # normal bounded recovery channel.
            score_seconds_today = self._daily_score_fallback(now)
            await self._report_runtime_error("daily_score_persistence", exc)
        today = date.fromisoformat(reward_day(now))
        try:
            resting_hours, _ = await asyncio.to_thread(
                resting_side_hours_by_day, str(self.ledger.path), [today],
                now=now)
            resting_side_hours_today = resting_hours.get(today.isoformat(), 0.0)
            self._runtime_error_counts.pop("resting_time_summary", None)
        except Exception as exc:
            # This is the same read-only ledger calculation used by the
            # seven-day reward table. It must never delay order work on the
            # event loop or turn an observational failure into a bot stop.
            resting_side_hours_today = 0.0
            await self._report_runtime_error("resting_time_summary", exc)
        # The caller's clock predates a potentially long decision sweep and
        # the awaited report query. Balances can refresh during either. Judge
        # freshness at rendering time, not against that obsolete timestamp.
        now = max(now, self._now())
        today = date.fromisoformat(reward_day(now))
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
        unknown = self.ledger.account_unknown_order_count()
        equity_change = Decimal("0")
        if self._equity_snapshot is not None and self.risk_guard is not None:
            equity_change = (
                self._equity_snapshot.marked_equity
                - self.risk_guard.starting_equity)
        place_count = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        entry_fills, exit_fills = self.ledger.fill_role_counts(
            since=self.started_at or now)
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
        capital_reason = None
        if self.balance is None or self.risk_guard is None:
            capital_reason = "balance or freshness guard unavailable"
        else:
            balance_age = (now - self.balance.observed_at).total_seconds()
            if balance_age < 0:
                capital_reason = "balance timestamp is in the future"
            elif balance_age > self.risk_guard.max_equity_age_seconds:
                capital_reason = f"balance stale ({balance_age:.0f}s old)"
        if capital_reason is None and (unresolved or unknown or problems):
            capital_reason = (
                "account integrity problem" if problems else
                f"order proof pending ({len(unresolved)} intents, {unknown} unknown)")
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
            score_seconds=score_seconds_today,
            resting_side_hours_today=resting_side_hours_today,
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
            hard_stop_loss_usd=self.config.hard_stop_loss_usd,
            # Supply, straight off the last selection scan. These counters
            # already exist on the selection object and were never reported
            # anywhere -- which is why "is it quoting 3 because 3 is all
            # there is, or because it is broken?" needed a 50-call script on
            # the droplet to answer.
            slot_capacity=self.config.max_markets,
            awaiting_rotation_markets=self._awaiting_rotation(),
            cycling_markets=self._cycling_markets(),
            quote_size=self.config.quote_size,
            portfolio_exposure_cap_usd=(
                self.config.max_portfolio_exposure_usd),
            # Reward cash can arrive on later days, so withdrawal guidance
            # keeps seven full daily hard-stop budgets as operating runway.
            # Pending rewards are not cash and cannot reduce this reserve.
            # This is guidance only, not a new stop or trading rule.
            weekly_reserve_usd=self.config.weekly_operating_reserve_usd,
            settled_cash_usd=(
                self.balance.current_balance
                if self.balance is not None else None),
            buying_power_usd=(
                self.balance.buying_power
                if self.balance is not None else None),
            # A withdrawal number is more consequential than an ordinary
            # status metric. Fail closed on stale balance evidence or any
            # unresolved/unknown ownership state. Working owned orders are
            # safe: the calculation separately caps free cash by venue buying
            # power, so their reserved funds cannot be called withdrawable.
            capital_state_proven=capital_reason is None,
            capital_unavailable_reason=capital_reason,
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
            # Marked trading P&L against the DAY's opening equity, from the
            # ledger so a restart cannot reset it. Account-wide by nature:
            # equity carries no per-system attribution. Proven rewards,
            # deposits and other non-trading cash are removed from this
            # operator-facing headline just as they are from the hard guard.
            today_pnl_usd=(
                self._equity_snapshot.marked_trading_pnl(
                    opening_equity=self._day_opening_equity,
                    nontrading_cash=self._nontrading_cash)
                if self._equity_snapshot is not None
                and self._day_opening_equity is not None
                and self._nontrading_cash is not None
                and self._baseline_day == today
                and self._nontrading_cash_day == today else None),
            today_realized_pnl_usd=(
                self._equity_snapshot.realized_trading_pnl(
                    opening_cash=self._cash_baseline,
                    nontrading_cash=self._nontrading_cash)
                if self._equity_snapshot is not None
                and self._cash_baseline is not None
                and self._nontrading_cash is not None
                and self._baseline_day == today
                and self._nontrading_cash_day == today
                # A carried position needs an opening basis to decompose the
                # day's mark. The durable baseline currently stores opening
                # cash and equity, so publish only the still-provable total.
                and self._day_opening_equity == self._cash_baseline
                else None),
            today_unrealized_pnl_usd=(
                self._equity_snapshot.open_unrealized_pnl()
                if self._equity_snapshot is not None
                and self._day_opening_equity == self._cash_baseline
                and self._baseline_day == today
                and self._nontrading_cash_day == today
                and self._nontrading_cash is not None
                else None),
            today_pnl_as_of=(
                self._equity_snapshot.observed_at
                if self._equity_snapshot is not None else None),
            # Exact delta observed by `hard_stop_reached`. `_refresh_balance`
            # removes proven non-trading cash before updating the guard, so
            # the meter and marked-trading headline use the same accounting
            # scope even when a reward or deposit lands.
            hard_stop_pnl_usd=(
                self.risk_guard.current_equity
                - self.risk_guard.starting_equity
                if self.risk_guard is not None
                and self.risk_guard.current_equity is not None
                else None),
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
            f"capital_proven={capital_reason is None} "
            f"capital_reason={capital_reason or 'ok'} "
            f"retired={len(self._retired_ever)}"
            + (f" event_pages={getattr(self._market_selection, 'event_pages_used', 0)}"
               f" event_markets={self._market_selection.catalog_markets}"
               f" executable={self._market_selection.executable_catalog_markets}"
               f" rewarded={self._market_selection.rewarded_executable_markets}"
               f" books_ranked={self._market_selection.books_evaluated}"
               f" viable={self._market_selection.viable_markets}"
               if self._market_selection is not None else "")
            + (f" scan_seconds={self._selection_scan_seconds_last:.1f}"
               if self._selection_scan_seconds_last is not None else ""),
            flush=True,
        )
        await self.notifier.send_payload_async(
            payload, critical=critical, channel="summary")
        await self._alarm_if_quoting_nothing(
            now, resting=resting_markets, reasons=idle_reasons)

    def _scheduled_summary_has_transient_capital_ambiguity(
        self, now: datetime,
    ) -> bool:
        """Return whether a routine summary may briefly wait for order proof.

        Only current order ambiguity is deferrable. Missing/stale balance
        evidence is reported immediately as unavailable, because waiting must
        not hide an account-data problem. This helper performs local reads
        only and cannot alter ownership or ask the venue for fresher state.
        """

        if self.balance is None or self.risk_guard is None:
            return False
        balance_age = (now - self.balance.observed_at).total_seconds()
        if not 0 <= balance_age <= self.risk_guard.max_equity_age_seconds:
            return False
        return bool(
            self.ledger.unresolved_intents()
            or self.ledger.account_unknown_order_count())

    async def _send_scheduled_summary(self, now: datetime) -> datetime:
        """Send a routine summary or return its next bounded proof recheck.

        The capital calculation remains fail-closed. A clean state sends now;
        an ordinary in-flight order waits at most 60 seconds; persistent
        ambiguity then sends the unchanged `UNAVAILABLE` representation.
        """

        now = max(now, self._now())
        if not self._scheduled_summary_has_transient_capital_ambiguity(now):
            self._summary_proof_wait_started_at = None
            await self._send_summary(now)
            return now + timedelta(minutes=5)

        started = self._summary_proof_wait_started_at
        if started is None or now < started:
            self._summary_proof_wait_started_at = now
            return now + timedelta(
                seconds=self.SUMMARY_PROOF_RECHECK_SECONDS)

        if ((now - started).total_seconds()
                >= self.SUMMARY_PROOF_DEFERRAL_SECONDS):
            self._summary_proof_wait_started_at = None
            await self._send_summary(now)
            return now + timedelta(minutes=5)

        return min(
            started + timedelta(seconds=self.SUMMARY_PROOF_DEFERRAL_SECONDS),
            now + timedelta(seconds=self.SUMMARY_PROOF_RECHECK_SECONDS),
        )

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
        self, component: str, exc: Exception, attempt: int | None = None, *,
        include_traceback: bool = True,
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
        if include_traceback and exc.__traceback__ is not None:
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
        # market retired or abandoned, an ownership violation, HARD, or
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
        drain_private_events: bool = False,
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
        # Only the recovery owner that has suspended the normal next_event
        # consumer may drain here; otherwise a task-held older event could
        # be applied after a newer queued event.
        if drain_private_events:
            await self._drain_private_events()
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
        if drain_private_events:
            await self._drain_private_events()
        await self._rest_reconcile(
            self._now(), strict_order_markets=terminal_order_markets)
        self._assert_runtime_scope()
        if not require_terminal:
            return
        for market in slugs:
            unresolved = self.ledger.unresolved_intents(market)
            if unresolved and all(
                    str(row["operation"]).upper() == "PLACE"
                    and str(row["state"]).upper() == "UNCERTAIN"
                    and not str(row["exchange_order_id"] or "").strip()
                    for row in unresolved):
                raise _OrphanPlaceEvidencePending(
                    "runtime recovery is waiting for repeated complete account "
                    f"proof for a no-id PLACE in {market}")
            if unresolved:
                operation = str(unresolved[0]["operation"]).upper()
                if all(
                        str(row["operation"]).upper() == "CANCEL"
                        for row in unresolved):
                    raise _CancellationEvidencePending(
                        "runtime recovery is waiting for terminal cancel "
                        f"proof in {market}")
                raise AdapterError(
                    "runtime recovery "
                    f"{operation.lower()} is not yet reconciled in {market}")
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
        Incremental top-up is the other bounded exception: it first completes
        an account-wide strict proof while the old scope and streams remain
        intact, then uses this scoped read only for the socket handoff.
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
        return self._owner_is_flat_and_clear(slug)

    def _owner_is_flat_and_clear(self, slug: str) -> bool:
        """Whether releasing this owner would strand no execution state."""

        return (self.ledger.position(slug) == 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.absent_orders(slug)
                and not any(str(row["market_slug"]) == slug
                            for row in self.ledger.unresolved_intents()))

    def _closed_and_flat(self, slug: str) -> bool:
        """True only when exact market metadata says this owner cannot quote.

        A failed metadata read is not proof, and a missing status field is not
        proof. The top-up transaction refreshes this exact market again before
        committing a release; this cached predicate only schedules the scan.
        """

        state = self._markets.get(slug)
        market = getattr(state, "market", None)
        if market is None:
            return False
        return (self._market_metadata_proves_not_open(market)
                and self._owner_is_flat_and_clear(slug))

    def _reward_authorization_lost_and_flat(
        self, slug: str, now: datetime,
    ) -> bool:
        """True only after fresh metadata proves this slot is no longer paid.

        Missing reward rows are not proof: a partial/transient incentive
        response must make the market idle, never release its ownership scope.
        `_refresh_contracts` records confirmation only when a later complete
        response still contains the exact program that previously authorized
        entry and that row no longer applies to the freshly read event clock.
        This covers both an explicit elapsed ``ends_at`` and a postponed event
        moving back outside the six-hour day-of phase.
        """

        state = self._markets.get(slug)
        if state is None:
            return False
        confirmed_at = (state.reward_authorization_loss_confirmed_at
                        or state.reward_period_end_confirmed_at)
        lost_at = (state.reward_authorization_lost_at
                   or state.reward_period_ended_at)
        if (confirmed_at is None or lost_at is None or lost_at > now):
            return False
        market = state.market
        if (market is None or market.game_start is None
                or market.game_start <= now):
            # Past kickoff belongs to `_expired_and_flat`, whose separate
            # lifecycle rule is already covered and logged.
            return False
        return self._owner_is_flat_and_clear(slug)

    def _reward_ended_and_flat(self, slug: str, now: datetime) -> bool:
        """Compatibility name for the broader authorization-loss predicate."""

        return self._reward_authorization_lost_and_flat(slug, now)

    def _economically_idle_and_flat(self, slug: str, now: datetime) -> bool:
        """True after a continuous economic refusal and complete flat proof."""

        state = self._markets.get(slug)
        if state is None or state.economically_ineligible_since is None:
            return False
        if not state.economic_ineligibility_reasons:
            return False
        current_reasons = tuple(sorted(set(
            state.economic_blocker_by_side.values())))
        if (set(state.economic_blocker_by_side) != {"bid", "offer"}
                or current_reasons != state.economic_ineligibility_reasons
                or any(value > 0
                       for value in state.expected_net_by_side.values())):
            return False
        if not set(state.economic_ineligibility_reasons).issubset(
                self.REPLACEABLE_ECONOMIC_BLOCKERS):
            return False
        held = (now - state.economically_ineligible_since).total_seconds()
        return (
            held >= self.config.economic_idle_replacement_seconds
            and self._owner_is_flat_and_clear(slug)
        )

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
            if slug in self._isolated_unexecutable_slugs():
                # There is no legal mutation to miss. The top-up path keeps
                # the old streams open through discovery, then performs its
                # normal complete account proof and fresh-stream handoff. A
                # later book/open status removes this exception before exit
                # planning.
                continue
            state = self._markets.get(slug)
            start = getattr(getattr(state, "market", None), "game_start", None)
            if start is None:
                return True
            if (start - now).total_seconds() <= margin:
                return True
        return False

    def _mandatory_top_up_gap(self, now: datetime) -> int:
        """Slots free or held by owners with mandatory release evidence.

        Economic-idle replacement deliberately stays on its operator-approved
        900-second cadence. It must not become urgent merely because an
        unrelated position closed. Lifecycle, reward-end and genuinely empty
        capacity are different: every one is already unable to produce useful
        pregame reward work and can request the post-flat fast path.
        """

        mandatory = {
            slug for slug in self.active_slugs
            if (self._expired_and_flat(slug, now)
                or self._closed_and_flat(slug)
                or self._reward_authorization_lost_and_flat(slug, now))
        }
        retained = len(self.active_slugs) - len(mandatory)
        return max(0, self.config.max_markets - retained)

    def _request_post_flat_top_up_if_needed(
        self, had_near_deadline_position: bool, now: datetime,
    ) -> None:
        """Request one immediate refill when the final urgent position clears."""

        if (not had_near_deadline_position
                or self._position_near_its_flatten_deadline(now)):
            return
        underfilled = self._mandatory_top_up_gap(now)
        if underfilled <= 0:
            return
        if self._post_flat_top_up_requested_at is None:
            self._post_flat_top_up_requested_at = now
            self._post_flat_underfilled_slots = underfilled
            print(
                f"[top-up-request] at={now.isoformat()} reason=post_flat "
                f"underfilled_slots={underfilled}",
                flush=True,
            )
        else:
            self._post_flat_underfilled_slots = max(
                self._post_flat_underfilled_slots, underfilled)

    def _post_flat_top_up_can_preempt(self, now: datetime) -> bool:
        """Whether ordinary market service may yield to the one-shot refill."""

        if self._post_flat_top_up_requested_at is None:
            return False
        if self._position_near_its_flatten_deadline(now):
            return False
        if self._mandatory_top_up_gap(now) <= 0:
            # The evidence that created the request changed before scheduling.
            # Do not let a stale optimization label or preempt a later scan.
            self._post_flat_top_up_requested_at = None
            self._post_flat_underfilled_slots = 0
            return False
        return True

    def _clear_incremental_top_up_requests(
        self, consumed_post_flat_at: datetime | None,
    ) -> None:
        self._reward_end_top_up_requested = False
        self._lifecycle_top_up_requested_at = None
        # The socket handoff performs another complete position snapshot. If
        # that discovers a different final urgent position becoming flat, it
        # creates a new request during this top-up. Clear only the request this
        # attempt actually consumed; never erase the newer evidence.
        if self._post_flat_top_up_requested_at == consumed_post_flat_at:
            self._post_flat_top_up_requested_at = None
            self._post_flat_underfilled_slots = 0

    def _urgent_top_up_can_use_cache(self, post_flat_requested: bool) -> bool:
        """Whether an event-driven refill may revalidate recent candidates."""

        return (
            post_flat_requested
            or self._lifecycle_top_up_requested_at is not None
            or self._reward_end_top_up_requested
        )

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
            or any(self._closed_and_flat(slug)
                   for slug in self.active_slugs)
            or any(self._reward_authorization_lost_and_flat(slug, now)
                   for slug in self.active_slugs)
            or any(self._economically_idle_and_flat(slug, now)
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
            and not self._entry_pause_active()
            and now >= self._top_up_retry_not_before
            and (now >= next_selection
                 or self._reward_end_top_up_requested
                 or self._lifecycle_top_up_requested_at is not None
                 or self._post_flat_top_up_requested_at is not None)
            and (self._reward_end_top_up_requested
                 or self._lifecycle_top_up_requested_at is not None
                 or self._post_flat_top_up_requested_at is not None
                 or not self._safe_to_scan())
            and not self._position_near_its_flatten_deadline(now)
            and self._active_set_needs_top_up(now)
        )

    async def _replacement_candidates_for_top_up(
        self,
        now: datetime,
        *,
        exclude: set[str],
        wanted: int,
        allow_cached_selection: bool,
    ) -> tuple[tuple[str, ...], str, float | None]:
        """Return freshly authorized replacement slugs and their evidence path."""

        if self.reward_selector is None:
            return (), "unavailable", None
        observed = self._market_selection_observed_at
        cached = self._market_selection
        if allow_cached_selection and cached is not None and observed is not None:
            cache_age = (now - observed).total_seconds()
            if 0 <= cache_age <= POST_FLAT_SELECTION_CACHE_SECONDS:
                # Overfetch a small bounded set so one changed book does not
                # force a full catalog scan. The revalidator still spends no
                # more than the selector's existing twenty-book live budget.
                candidate_limit = min(
                    self.config.max_markets, max(wanted, wanted * 2))
                candidates = tuple(
                    item.market.slug for item in cached.top_viable(
                        candidate_limit, exclude=exclude))
                revalidate = getattr(
                    self.reward_selector, "revalidate_viable", None)
                if candidates and callable(revalidate):
                    validation_started = time.monotonic()
                    try:
                        refreshed = await revalidate(
                            candidates, now=self._now())
                    except ApiAdmissionDeferred:
                        # Backpressure is not evidence that the cache failed.
                        # Do not immediately replace bounded entry revalidation
                        # with a much larger background discovery scan.
                        raise
                    except Exception as exc:
                        print(
                            f"[top-up-cache] at={self._now().isoformat()} "
                            f"outcome=fallback error={type(exc).__name__}",
                            flush=True,
                        )
                    else:
                        candidate_set = set(candidates)
                        viable = tuple(
                            item.market.slug for item in refreshed
                            if (item.viable_sides
                                and item.market.slug in candidate_set
                                and item.market.slug not in exclude))
                        elapsed = time.monotonic() - validation_started
                        if len(viable) >= wanted:
                            print(
                                f"[top-up-cache] at={self._now().isoformat()} "
                                f"outcome=validated age_seconds={cache_age:.1f} "
                                f"checked={len(candidates)} viable={len(viable)} "
                                f"validation_seconds={elapsed:.1f}",
                                flush=True,
                            )
                            return viable, "cached_revalidated", cache_age
                        print(
                            f"[top-up-cache] at={self._now().isoformat()} "
                            f"outcome=fallback age_seconds={cache_age:.1f} "
                            f"checked={len(candidates)} viable={len(viable)} "
                            f"wanted={wanted} validation_seconds={elapsed:.1f}",
                            flush=True,
                        )

        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=self._now())
        finally:
            self._selection_scan_seconds_last = time.monotonic() - scan_started
        self._log_selection_scan(selection)
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        return (
            tuple(item.market.slug for item in selection.top_viable(
                self.config.max_markets, exclude=exclude)),
            "fresh_scan",
            None,
        )

    def _top_up_timing_fields(
        self, now: datetime, selection_source: str,
        cache_age: float | None,
    ) -> str:
        triggers: list[str] = []
        if self._post_flat_top_up_requested_at is not None:
            triggers.append("post_flat")
        if self._reward_end_top_up_requested:
            triggers.append("reward_end")
        if self._lifecycle_top_up_requested_at is not None:
            triggers.append("lifecycle")
        trigger = "+".join(triggers) if triggers else "scheduled"
        fields = f"trigger={trigger} selection_source={selection_source}"
        if cache_age is not None:
            fields += f" cache_age_seconds={cache_age:.1f}"
        if self._lifecycle_top_up_requested_at is not None:
            wait = max(0.0, (
                now - self._lifecycle_top_up_requested_at).total_seconds())
            fields += f" lifecycle_wait_seconds={wait:.1f}"
        if self._post_flat_top_up_requested_at is not None:
            wait = max(0.0, (
                now - self._post_flat_top_up_requested_at).total_seconds())
            slot_minutes = (
                wait * self._post_flat_underfilled_slots / 60.0)
            fields += (
                f" post_flat_wait_seconds={wait:.1f}"
                f" post_flat_underfilled_slots="
                f"{self._post_flat_underfilled_slots}"
                f" post_flat_underfilled_slot_minutes={slot_minutes:.2f}")
        return fields

    async def _top_up_active_markets(
        self,
        now: datetime,
        *,
        allow_cached_selection: bool = False,
        allow_economic_idle: bool = True,
    ) -> bool:
        """Fill free slots without disturbing markets that are still resting.

        Full rotation requires a whole-account flat state (`_rotation_blockers`,
        `_discovery_is_free`) because it quarantines every resting order and
        rebinds from scratch. With twenty markets something is almost always
        resting, so that condition is essentially never met: measured
        2026-08-16, rotation ran exactly twice, both at startup, and the active
        set stayed frozen while markets kicked off one by one. Coverage decayed
        19 -> 11 -> 8 -> 7 -> 6 across the previous session for this reason as
        much as for the retirement ban.

        This path is deliberately narrower. It releases markets that are
        past kickoff and flat, are positively reported closed/not-open and
        flat, or whose exact reward program is positively confirmed ended and
        which are flat with no order ownership left.  It may also replace a
        flat, clear pregame owner after ten continuous minutes of a named
        current-book economic refusal, but only when the same scan supplies an
        actual viable replacement. It keeps every other market and its resting
        orders exactly where they are, fills any capacity startup validation
        left free, and never quarantines. Streams are restarted because they
        cannot subscribe incrementally, but a stream restart does not touch
        resting orders -- those live on the venue and keep earning through it.
        That is the whole difference from full rotation, which cancels them.
        """

        if not self.config.auto_select_reward_markets:
            return False
        if self.reward_selector is None:
            return False
        if self._position_near_its_flatten_deadline(now):
            return False
        expired = [slug for slug in self.active_slugs
                   if self._expired_and_flat(slug, now)]
        closed = [slug for slug in self.active_slugs
                  if slug not in expired and self._closed_and_flat(slug)]
        reward_ended = [
            slug for slug in self.active_slugs
            if (slug not in expired and slug not in closed
                and self._reward_authorization_lost_and_flat(slug, now)
                and self.state_for(slug).reward_period_end_confirmed_at
                is not None)]
        reward_inactive = [
            slug for slug in self.active_slugs
            if (slug not in expired and slug not in closed
                and slug not in reward_ended
                and self._reward_authorization_lost_and_flat(slug, now))]
        mandatory_released = expired + closed + reward_ended + reward_inactive
        economic_idle_candidates = sorted(
            (slug for slug in self.active_slugs
             if slug not in mandatory_released
             and allow_economic_idle
             and self._economically_idle_and_flat(slug, now)),
            key=lambda slug: (
                self.state_for(slug).economically_ineligible_since
                or datetime.max.replace(tzinfo=timezone.utc),
                slug,
            ),
        )
        base_keep = tuple(slug for slug in self.active_slugs
                          if slug not in mandatory_released)
        capacity_without_optional_release = max(
            0, self.config.max_markets - len(base_keep))
        if (not mandatory_released and not economic_idle_candidates
                and capacity_without_optional_release == 0):
            return False
        # Exclude every current owner, including optional idle candidates.  An
        # idle owner may be retained if supply is short, but it must not count
        # as its own replacement and manufacture a no-op stream rebind.
        exclude = (self._barred_slugs() | set(base_keep)
                   | set(mandatory_released)
                   | set(economic_idle_candidates))
        wanted = max(
            1,
            capacity_without_optional_release + len(economic_idle_candidates),
        )
        replacement_candidates, selection_source, cache_age = (
            await self._replacement_candidates_for_top_up(
                now,
                exclude=exclude,
                wanted=wanted,
                allow_cached_selection=allow_cached_selection,
            ))
        optional_capacity = max(
            0, len(replacement_candidates) - capacity_without_optional_release)
        economic_idle = economic_idle_candidates[:optional_capacity]
        released = mandatory_released + economic_idle
        keep = tuple(slug for slug in self.active_slugs
                     if slug not in released)
        free = max(0, self.config.max_markets - len(keep))
        replacements = replacement_candidates[:free]
        plan_at = self._now()
        timing_fields = self._top_up_timing_fields(
            plan_at, selection_source, cache_age)
        print(
            f"[top-up-plan] at={plan_at.isoformat()} {timing_fields} "
            f"released={len(released)} kept={len(keep)} "
            f"added={len(replacements)} free_slots={free} "
            f"expired={len(expired)} closed={len(closed)} "
            f"reward_ended={len(reward_ended)} "
            f"reward_inactive={len(reward_inactive)} "
            f"economic_idle={len(economic_idle)} "
            f"economic_idle_candidates={len(economic_idle_candidates)} "
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
                f"expired={len(expired)} closed={len(closed)} "
                f"reward_ended={len(reward_ended)} "
                f"economic_idle={len(economic_idle)}",
                flush=True,
            )
            return False
        if not replacements and not released:
            # Capacity is free, but this scan found nothing safe to add. Keep
            # the current subscriptions intact and retry only on the normal
            # bounded selection cadence.
            return False
        # Transaction boundary: take complete account snapshots and strictly
        # prove every owner being released while the old active set and both
        # healthy streams are still intact. Retained owners stay retained and
        # individually gated if one of their GET-by-ID views lags. On
        # 2026-08-25 this proof happened only after `_bind_markets` and after both
        # streams had been closed. Three attributable GET-by-ID NotFound reads
        # therefore turned a harmless one-slot top-up refusal into global
        # recovery, which cancelled 32 orders and retired 18 owners. Failing
        # here leaves the caller able to resume its old stream tasks without a
        # scope change, cancellation or rebind.
        await self._rest_reconcile(
            self._now(), strict_order_markets=frozenset(released))
        # Closed/not-open is positive lifecycle proof, not an inference from a
        # missing selector row. Re-read each exact owner at the transaction
        # boundary; a failed read or a market that reopened aborts before the
        # active tuple or either healthy stream changes.
        for slug in closed:
            self.state_for(slug).market = await self.gateway.fetch_market(slug)
        self._assert_runtime_scope()
        # The complete snapshot above can discover a fill, a newly absent
        # order, or another unresolved mutation. Recheck the release predicate
        # at the transaction boundary instead of committing a decision made
        # before that evidence arrived.
        no_longer_releasable = [
            slug for slug in released
            if not (self._expired_and_flat(slug, self._now())
                    or self._closed_and_flat(slug)
                    or self._reward_authorization_lost_and_flat(
                        slug, self._now())
                    or self._economically_idle_and_flat(
                        slug, self._now()))]
        if no_longer_releasable:
            raise AdapterError(
                "top-up release proof changed during preflight: "
                + ", ".join(sorted(no_longer_releasable)))
        if not replacements:
            # Still worth releasing: a dead market costs a REST refresh every
            # service cycle and reports `book_stale` every time.
            self._bind_markets(keep)
        else:
            self._bind_markets(keep + replacements)
        # Strictness was established immediately before committing the new
        # scope, with no order mutation in between. During the short socket
        # handoff a resting order can still fill or disappear. The complete
        # post-bind snapshots and fresh private state capture that change;
        # any positively-owned ambiguity then closes only its market's gate.
        # Unowned, conflicting or out-of-scope state remains terminal through
        # `_assert_runtime_scope`. Requiring every attributed GET again would
        # recreate the same account-wide failure after the safe preflight.
        await self._open_streams_for_active_market(
            strict_order_markets=frozenset())
        committed_at = self._now()
        timing_fields = self._top_up_timing_fields(
            committed_at, selection_source, cache_age)
        print(
            f"[top-up] at={committed_at.isoformat()} committed=yes "
            f"{timing_fields} "
            f"released={len(released)} kept={len(keep)} "
            f"added={len(replacements)} active={len(self.active_slugs)} "
            f"expired={len(expired)} closed={len(closed)} "
            f"reward_ended={len(reward_ended)} "
            f"reward_inactive={len(reward_inactive)} "
            f"economic_idle={len(economic_idle)} "
            f"released_slugs={','.join(sorted(released))}",
            flush=True,
        )
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
        self._log_selection_scan(selection)
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
            f"Joined {selection.catalog_markets} buffered event-market rows to "
            f"{selection.rewarded_executable_markets} executable reward programs, "
            f"evaluated {selection.books_evaluated} books, and "
            f"found {selection.viable_markets} economically viable markets; "
            f"selection scan {self._selection_scan_seconds_last:.1f}s; "
            f"{selection.unsupported_programs} unsupported lifecycle programs "
            "remained read-only.")
        return True

    async def _retry_private_handoff_read(
        self, name: str, read: Callable[[], Any],
    ) -> Any:
        """Retry only confirmed 429 reads while a shard handoff is frozen.

        Existing resting orders remain visible through the replacement private
        sockets, and no decision can run from this coroutine. Each wait drains
        authoritative private events and re-proves socket liveness. Any other
        failure, stale replacement, operator stop, or exhausted retry budget
        returns to the existing global quarantine path.
        """

        for attempt in range(1, PRIVATE_HANDOFF_RATE_LIMIT_ATTEMPTS + 1):
            try:
                return await read()
            except AdapterError as exc:
                if (exc.status_code != 429
                        or attempt >= PRIVATE_HANDOFF_RATE_LIMIT_ATTEMPTS):
                    raise
                await self._drain_private_events()
                self.private_stream.assert_live(
                    now=self._now(),
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
                if self._stop_requested:
                    raise CommissioningUnavailable(
                        "operator stopped during private shard handoff") from exc
                retry_after = exc.retry_after_seconds
                delay = (
                    min(max(float(retry_after), 0.0),
                        PRIVATE_HANDOFF_RATE_LIMIT_MAX_SECONDS)
                    if retry_after is not None
                    else min(
                        PRIVATE_HANDOFF_RATE_LIMIT_BASE_SECONDS
                        * (2 ** (attempt - 1)),
                        PRIVATE_HANDOFF_RATE_LIMIT_MAX_SECONDS,
                    )
                )
                print(
                    f"[private-shard-handoff-rate-limit] "
                    f"at={self._now().isoformat()} read={name} "
                    f"attempt={attempt}/{PRIVATE_HANDOFF_RATE_LIMIT_ATTEMPTS} "
                    f"delay={delay:.1f}s cancels=0",
                    flush=True,
                )
                await self.sleep(delay)
        raise AssertionError("unreachable private handoff retry loop")

    async def _recover_private_stream(self, initial: Exception) -> str | None:
        """Stay alive but dormant until private state is proven again."""

        # A heartbeat-silent shard can be replaced under an overlapping
        # listener without sacrificing every resting order. The decision
        # coroutine owns the loop here, so no new mutation can occur. Every
        # replacement first proves that it is receiving private state
        # with its account-balance delivery. The venue can omit an order EOF
        # for an empty scoped subscription, so buffered order pages are useful
        # but optional. A complete REST balance/order/position proof bridges
        # the old connection's gap. Confirmed read throttles receive a slower,
        # bounded retry while orders and the decision freeze are preserved.
        # Any other or exhausted proof falls through to the older global
        # cancel/quarantine recovery below.
        repair_stale = getattr(self.private_stream, "repair_stale_shards", None)
        if (callable(repair_stale)
                and "private WebSocket shard silence:" in str(initial)):
            await self._report_runtime_error("private_stream", initial, 1)
            try:
                self.gate.on_reconciliation_conflict()
                scopes = await repair_stale(
                    now=self._now(),
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
                # A heartbeat may arrive after assert_live raises but before
                # this frozen recovery coroutine re-checks the pool. An empty
                # repair set means the shard recovered; it does not justify
                # canceling the whole slate. The account-wide REST proof and
                # final liveness gate below are still mandatory.
                await self._drain_private_events()
                await self._retry_private_handoff_read(
                    "account_state", lambda: self._rest_reconcile(
                        self._now(), tag="private-shard-handoff",
                        strict_order_markets=frozenset()))
                # The WebSocket balance deliveries above prove each new socket
                # is live; this final account-global REST read proves a current
                # decision balance after any slower account-state retry.
                await self._retry_private_handoff_read(
                    "balance", lambda: self._refresh_balance(self._now()))
                await self._drain_private_events()
                self.private_stream.assert_live(
                    now=self._now(),
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
                if not self.gate.session_ready(
                        self.ledger, now=self._now()):
                    raise CommissioningUnavailable(
                        "private shard handoff did not restore fresh account state")
                repaired_markets = sum(
                    len(scope) for scope in scopes if scope is not None)
                print(
                    f"[private-shard-recovery] at={self._now().isoformat()} "
                    f"resumed=yes shards={len(scopes)} "
                    f"markets={repaired_markets} cancels=0 "
                    f"working={len(self.ledger.working_orders(owned_only=True))}",
                    flush=True,
                )
                await self.notifier.send_async(
                    "✅ Liquidity Bot recovered the silent private WebSocket "
                    f"shard(s), re-proved the account, and preserved "
                    f"resting orders across {repaired_markets} market(s).",
                    channel="errors",
                )
                self._runtime_error_counts.pop("private_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "private_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as repair_exc:
                print(
                    "[private-shard-recovery] overlap repair failed; "
                    "falling back to global quarantine: "
                    f"{type(repair_exc).__name__}: {repair_exc}",
                    flush=True,
                )
                initial = repair_exc

        exc = initial
        attempt = 0
        venue_failure_streak = 0
        self.gate.on_disconnect()
        while True:
            attempt += 1
            stream_listening = False
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
                stream = self.private_stream_factory(self.client)
                self.private_stream = stream
                await stream.start(list(self.active_slugs))
                stream_listening = True
                self.gate.on_connect()
                # Listen before canceling. The private order stream is the
                # venue's authoritative terminal-outcome channel; canceling the
                # whole slate before this start created every terminal event in
                # a visibility gap and forced repair onto GET-by-id, which can
                # return NotFound for minutes. No decision can run while this
                # recovery coroutine owns the loop, so starting the listener
                # first cannot authorize new risk.
                await self._quarantine_working_orders(require_terminal=False)
                await self._wait_for_initial_private_state()
                await self._drain_private_events()
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
                # If the replacement stream itself could not start, quotes are
                # still blind. Preserve the old fail-safe response: make one
                # best-effort cancellation pass before backing off. This branch
                # may miss terminal events, but it is reached only when there is
                # no working private listener to receive them.
                if not stream_listening:
                    try:
                        await self._quarantine_working_orders(
                            require_terminal=False)
                    except Exception:
                        pass
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_transient_recovery_error(recovery_exc) else 0)
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

    async def _recover_market_stream(
        self, initial: Exception, *, private_task: asyncio.Task[Any] | None = None,
    ) -> str | None:
        """Reconnect market data after canceling quotes made on the old book."""

        # Transfer private consumption before waiting outside the main loop.
        # A completed task owns the oldest event; apply it before draining the
        # socket. A pending read is cancelled/awaited before taking over, so it
        # cannot steal the confirmation the recovery needs. The caller starts
        # one replacement reader only after this coroutine returns.
        if private_task is not None:
            if not private_task.done():
                private_task.cancel()
            try:
                event = await private_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                pass
            except Exception as private_exc:
                # Both transports failed. Re-establish private visibility first
                # through the existing strict account/scope recovery path.
                return await self._recover_bound_streams(private_exc)
            else:
                await self._observe_private(event, self._now())
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
                await self._quarantine_working_orders(drain_private_events=True)
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
            except _OrphanPlaceEvidencePending as pending:
                # An uncertain no-id create is neither a failed stream attempt
                # nor permission to resend. Complete reconciliation above keeps
                # proving account state; wait in-process until the conservative
                # proof terminalizes it. HARD/operator stop checks still run on
                # every pass, and this wait never creates venue risk.
                exc = pending
                attempt = 0
                venue_failure_streak = 0
                await self._report_runtime_error(
                    "market_stream_orphan_place_evidence", pending)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self.sleep(self._recovery_delay(
                    "market_stream_orphan_place_evidence",
                    base=5.0,
                    cap=60.0,
                ))
            except _CancellationEvidencePending as pending:
                # During a Retail maintenance episode the cancel endpoint can
                # acknowledge a request while later authoritative order reads
                # still report OPEN.  That is neither permission to resume nor
                # a failed market-stream reconnect.  Consuming the bounded
                # reconnect budget here exited the runner non-flat after 12
                # attempts and abandoned every resting order.  Stay in the
                # same no-entry recovery loop until a terminal order state (or
                # an operator/risk stop) arrives; each pass still performs the
                # existing positive reads and durable cancel retry protocol.
                exc = pending
                attempt = 0
                venue_failure_streak = 0
                await self._report_runtime_error(
                    "market_stream_cancel_evidence", pending)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self.sleep(self._recovery_delay(
                    "market_stream_cancel_evidence",
                    base=5.0,
                    cap=60.0,
                ))
            except Exception as recovery_exc:
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_transient_recovery_error(recovery_exc) else 0)
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

        Rebinds private visibility before quarantine. A top-up deliberately
        leaves resting orders alone, so cancelling here gives up the reward
        they were still accruing -- but the private stream is down, which means
        a fill can land unseen, and quoting blind is the one thing the gate
        exists to prevent. Listening first preserves terminal cancel proof;
        the recovery coroutine still owns the loop, so no new-risk decision can
        run before quarantine and fresh-state proof finish.
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
                # Restore private visibility before creating cancellation
                # events, for the same reason as `_recover_private_stream`.
                # `_open_streams_for_active_market` cannot return control to a
                # decision loop, so this order does not create a quote window.
                await self._open_streams_for_active_market(
                    strict_order_markets=frozenset())
                await self._quarantine_working_orders(require_terminal=False)
                await self._drain_private_events()
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
                    f"✅ Liquidity Bot {self.SOURCE_REBOUND_DESCRIPTION}; "
                    f"{self.ACCOUNT_STATE_DESCRIPTION} passed. "
                    + (
                        "Quoting may resume on unaffected markets; "
                        f"{len(ambiguous_markets)} market(s) remain scoped "
                        "until terminal order proof."
                        if ambiguous_markets else "Quoting may resume."
                    ),
                    channel="errors",
                )
                self._runtime_error_counts.pop("bound_streams", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "bound_streams_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                if not self.gate.connected:
                    try:
                        await self._quarantine_working_orders(
                            require_terminal=False)
                    except Exception:
                        pass
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_transient_recovery_error(recovery_exc) else 0)
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

    @classmethod
    def _is_transient_recovery_error(cls, exc: Exception) -> bool:
        """Typed throttling is recoverable, but is not a maintenance claim."""

        current: BaseException | None = exc
        seen: set[int] = set()
        throttled = False
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, RuntimeInvariantViolation):
                return False
            if isinstance(current, ApiAdmissionDeferred):
                throttled = True
            if (isinstance(current, AdapterError)
                    and current.status_code == 429):
                throttled = True
            current = current.__cause__
        return throttled or cls._is_venue_unavailable(exc)

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
            # Only explicit adapter wrapping is causal evidence. Python's
            # implicit context may be the old disconnect currently being
            # recovered, not the new schema/ownership failure's cause.
            current = current.__cause__

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
        self, component: str, initial: Exception, *, planned: bool = False,
    ) -> str | None:
        """Hold dormant until authenticated venue reads are stably healthy.

        Probes are intentionally read-only and are not applied as ledger
        snapshots.  In particular, an order missing from these observations is
        never interpreted as cancelled.  The component-specific recovery then
        performs the normal quarantine, reconciliation and fresh-stream gates
        before the run loop can quote again.
        """

        self._transport_recovery_component = component
        self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
        pause_tag = "maintenance-pause" if planned else "venue-recovery-pause"
        health_label = "maintenance" if planned else "venue recovery"
        print(
            f"[{pause_tag}] at={self._now().isoformat()} "
            f"component={component} state=entered "
            f"reason={type(initial).__name__}:{initial}",
            flush=True,
        )
        if planned:
            await self.notifier.send_async(
                "🛠️ Liquidity Bot completed the scheduled maintenance delay. "
                "New quoting remains paused while read-only account health "
                "passes three times over about two minutes; no order absence "
                "is treated as cancellation.")
        else:
            await self.notifier.send_async(
                "🛠️ Liquidity Bot venue recovery pause: waiting for API/stream recovery "
                f"in {component}. New quoting is paused; no "
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
                    f"[{pause_tag}] at={self._now().isoformat()} "
                    f"component={component} health=success "
                    f"stable={stable}/{MAINTENANCE_STABLE_READS} "
                    f"open_orders={len(orders)} positions={len(positions)} "
                    f"cash={balance.current_balance} "
                    f"reported_asset_notional={balance.asset_notional}",
                    flush=True,
                )
            except RuntimeInvariantViolation:
                raise
            except Exception as probe_exc:
                # A 429 is not evidence of maintenance, but inside an existing
                # no-entry hold it is a failed health sample, not permission to
                # terminate and abandon recovery. Never count it as success.
                if not self._is_transient_recovery_error(probe_exc):
                    await self._report_runtime_error(
                        "maintenance_probe_terminal", probe_exc)
                    raise
                stable = 0
                attempt = self._runtime_error_counts.get(
                    "maintenance_probe", 0) + 1
                await self._report_runtime_error(
                    "maintenance_probe", probe_exc, attempt=attempt,
                    # Preserve the first diagnosis and a periodic full sample,
                    # but do not print the identical stack every minute of a
                    # long venue outage. The concise attempt line and health
                    # marker remain present for every probe.
                    include_traceback=(attempt == 1 or attempt % 30 == 0))
                print(
                    f"[{pause_tag}] at={self._now().isoformat()} "
                    f"component={component} health=failed stable=0/"
                    f"{MAINTENANCE_STABLE_READS} "
                    f"reason={type(probe_exc).__name__}:{probe_exc}",
                    flush=True,
                )
            if stable < MAINTENANCE_STABLE_READS:
                print(
                    f"[recovery-wait] component={component} "
                    f"next_probe_at={(self._now() + timedelta(seconds=MAINTENANCE_POLL_SECONDS)).isoformat()} "
                    f"stable={stable}/{MAINTENANCE_STABLE_READS}", flush=True)
                stop = await self._maintenance_sleep(MAINTENANCE_POLL_SECONDS)
                if stop:
                    return stop
        await self.notifier.send_async(
            f"✅ Liquidity Bot {health_label} health gate passed three complete "
            "authenticated account reads. Normal stream recovery and exact "
            "order/position reconciliation are resuming; quoting remains "
            "blocked until those existing gates pass.")
        print(
            f"[{pause_tag}] at={self._now().isoformat()} "
            f"component={component} state=health_gate_passed",
            flush=True,
        )
        self._runtime_error_counts.pop("maintenance_probe", None)
        self._transport_recovery_component = None
        return None

    async def _reconcile_pending_hard_exit(
        self, slug: str | None = None,
    ) -> str:
        """Confirm a submitted IOC without converting latency into shutdown."""

        slug = slug or self.market_slug
        await self._reconcile_hard_exit_state(slug)
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

    async def _reconcile_hard_exit_state(self, slug: str) -> None:
        """Refresh the evidence required to classify one submitted IOC."""

        await self._rest_reconcile(self._now())

    async def _recover_strategy_state(self, reason: str, slug: str) -> None:
        """Quarantine an unexpected strategy state without ending the process.

        The refresh must target the market that actually went stale. Refreshing
        the primary would leave a stale book on any other slot untouched, so it
        would report the same condition forever and never quote again.
        """

        component = f"strategy_state:{slug}"
        # A stale read is a decision refusal, but if the immediate REST refresh
        # succeeds it is not an operational error. Reporting before the refresh
        # produced 8,722 attempt=1 "errors" in one healthy day even though the
        # recursive plan used a fresh book milliseconds later. Count and report
        # only a stale condition that the recovery itself cannot clear.
        if reason != "book_stale":
            await self._report_runtime_error(
                component, AdapterError(str(reason or "unknown")))
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
            if reason == "book_stale":
                await self._report_runtime_error(
                    component, AdapterError(str(reason or "unknown")))
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

    @staticmethod
    def _api_admission_delay(exc: ApiAdmissionDeferred) -> float:
        """Clamp an untrusted shared-cooldown hint to one bounded retry wait."""

        try:
            delay = float(exc.retry_after_seconds or 1.0)
        except (TypeError, ValueError, OverflowError):
            delay = 1.0
        if not math.isfinite(delay):
            delay = 1.0
        return min(max(delay, 1.0), 60.0)

    def _log_api_backpressure(self, component: str, delay: float) -> None:
        """Record designed local deferral without calling it a venue error."""

        print(
            f"[api-backpressure] at={self._now().isoformat()} "
            f"component={component} delay={delay:.1f}s requests_sent=0",
            flush=True,
        )

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
                | set(self._inactive_market_wait_slugs)
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

        Returns a terminal reason only when retirement itself is no longer
        a proportionate response -- a whole slate of failures is systemic.
        """

        state = self.state_for(slug)
        state.clear_economics()
        # A fresh budget, not an unlimited one: the attempt and window caps
        # still bound each round. The marked-equity HARD guard bounds aggregate
        # financial harm without turning a routine exit count into a
        # process-lifetime limit.
        #
        # Pending-confirmation callers retain even the round budget. A normal
        # re-arm resets its rate bounds, but never clears the sent IOC marker;
        # only _reconcile_pending_hard_exit may clear that after fresh proof.
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
        existing_family = self._permanent_retirement_family(
            existing.reason) if existing is not None else None
        new_family = self._permanent_retirement_family(reason)
        if (existing is not None and existing_family is not None
                and existing_family == new_family):
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
        if (rounds == MAX_RETIREMENT_ROUNDS and position == 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.absent_orders(slug)
                and not self.ledger.unresolved_intents(slug)):
            print(
                f"[market-retired] at={self._now().isoformat()} market={slug} "
                f"abandoned_after={rounds}_rounds position={position}",
                flush=True,
            )
            await self.notifier.send_async(
                f"🚨 Liquidity Bot abandoned {slug} after {rounds} failed exit "
                f"rounds, position {position}. It will not be planned again "
                "while empty; any position or pending order reactivates recovery. Every other "
                "market keeps running.",
                critical=True,
            )
        elif (rounds == 1 or rounds == MAX_RETIREMENT_ROUNDS
              or (rounds & (rounds - 1)) == 0):
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
        # One full slate of retirements AT ONCE is a systemic problem. Named
        # recoverable faults pause entry/selection; unknown defects still stop.
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
            if all(self._retired[name].reason in {
                    "unrecoverable_book_stale",
                    "unrecoverable_execution_state_unknown",
            } for name in recent_faults):
                if self._market_fault_pause_until is None:
                    self._market_fault_pause_until = now + timedelta(
                        seconds=RETIREMENT_COOLDOWN_SECONDS)
                    self._entry_pause_cancel_requested = True
                    for name in self.active_slugs:
                        self._mark_market_dirty(name, now)
                    self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
                    await self.notifier.send_async(
                        "⏸️ Widespread stale market/account state. Entries paused; "
                        "position recovery continues. Selection will retry after "
                        "the 30-minute cooldown and fresh account/stream proof.",
                        critical=True)
                return None
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

    @classmethod
    def _permanent_retirement_family(cls, reason: str) -> str | None:
        """Group equivalent permanent failures without escalating rounds."""

        text = str(reason)
        return next((prefix for prefix in cls.PERMANENT_RETIREMENT_PREFIXES
                     if text.startswith(prefix)), None)

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

        retired = self._retired.get(slug)
        if retired is None:
            return False
        abandoned = retired.rounds >= MAX_RETIREMENT_ROUNDS
        if not abandoned and not self._retired_past_kickoff(slug, now):
            return False
        # The exit test, and the reason this stays safe. A retirement holding
        # anything at all keeps its book.
        if self.ledger.position(slug) != 0:
            return False
        if self.ledger.working_orders(slug) or self.ledger.absent_orders(slug):
            return False
        return not self.ledger.market_has_unresolved_intent(slug)

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
        self, exc: Exception, slug: str, *, risk_reducing: bool = False,
    ) -> str | None:
        """Retry ambiguous remote outcomes; terminate local invariant failures."""

        if isinstance(exc, ApiAdmissionDeferred):
            # No request crossed the network boundary.  The existing desired
            # state remains authoritative. Apply the same account-wide entry
            # admission circuit before later plans reach durable intent
            # creation; otherwise every market pass would create another exact
            # FAILED row during one shared cooldown. Reporting or quarantining
            # here would turn harmless backpressure into order churn.
            if exc.error_code != "LOCAL_API_INTERRUPTED":
                delay = self._api_admission_delay(exc)
                deferred_until = self._now() + timedelta(
                    seconds=delay)
                if (self._entry_mutation_blocked_until is None
                        or self._entry_mutation_blocked_until < deferred_until):
                    self._entry_mutation_blocked_until = deferred_until
            return None
        if isinstance(exc, OrderMutationRejected):
            await self._report_runtime_error(
                "mutation_access_denied", exc, include_traceback=False)
            self._entry_mutation_blocked_until = (
                self._now()
                + timedelta(
                    seconds=self.ENTRY_MUTATION_ACCESS_RECHECK_SECONDS))
            if risk_reducing:
                self._risk_mutation_blocked_until = (
                    self._now()
                    + timedelta(
                        seconds=self.RISK_MUTATION_ACCESS_RECHECK_SECONDS))
            if not self._mutation_access_denial_announced:
                self._mutation_access_denial_announced = True
                await self.notifier.send_async(
                    "⛔ Liquidity Bot mutation access was rejected by "
                    "Polymarket. New entries pause for one minute, then one "
                    "entry will probe recovery. Cancels and exits remain "
                    "available unless their own request is rejected. "
                    "Existing venue state remains tracked; no cancellation "
                    "or flatness was inferred.",
                    critical=True,
                    channel="errors",
                )
            # The request is positively FAILED, not ambiguous. Quarantining
            # would cancel healthy resting quotes and convert one account-wide
            # venue denial into twenty market retirements.
            return None
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
        slugs.update(str(row["market_slug"]) for row in self.ledger.absent_orders())
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

    def _only_settlement_wait_positions_remain(self, now: datetime) -> bool:
        """Whether shutdown has isolated every remaining nonflat owner."""

        waiting = self._isolated_unexecutable_slugs()
        if (not waiting
                or self.ledger.unresolved_intents()
                or self.ledger.absent_orders()):
            return False
        for slug in self._shutdown_slugs():
            if self.ledger.working_orders(slug):
                return False
            position = self.ledger.position(slug)
            if slug in waiting:
                if position == 0 or not self._can_wait_unexecutable(slug, now):
                    return False
            elif position != 0:
                return False
        return True

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
        exit_attempts: dict[str, int] = {}

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
                # Stream tasks are paused while shutdown owns the loop, but the
                # connected private socket still buffers terminal cancel/fill
                # events. Apply those before falling back to REST reads.
                await self._drain_private_events()
                await self._rest_reconcile(now)
                await self._refresh_event_guard()
                if (self._isolated_unexecutable_slugs()
                        and self._only_settlement_wait_positions_remain(
                            self._now())):
                    # All mutable state is gone and the remaining positions
                    # have no venue price. Retrying the same empty book for 15
                    # minutes cannot improve safety; stop promptly while
                    # reporting nonflat truth. A later explicitly authorized
                    # start reconstructs and monitors the same exact owners.
                    waiting = sorted(self._isolated_unexecutable_slugs())
                    self._shutdown_proved_flat = False
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"⚠️ Liquidity Bot LIVE stopped after {reason} with "
                        f"{len(waiting)} exact position(s) quarantined for "
                        "market reopening, book recovery or settlement: "
                        f"{', '.join(waiting)}. The account is not flat; no "
                        "order remains and no position was ignored.",
                        critical=True)
                    return
                # Every market that ever held risk must be retired, not just
                # the primary. A slug the session has already dropped can still
                # own an order or a position, and leaving it behind is exactly
                # how inventory reaches settlement.
                parked: list[str] = []
                # Cancel known working owners before any book/depth read.
                owners = sorted(
                    self._shutdown_slugs(),
                    key=lambda owner: (
                        not bool(self.ledger.working_orders(owner)), owner))
                for market_slug in owners:
                    now = self._now()
                    if now >= deadline:
                        break
                    try:
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
                                or (state.market is not None
                                    and not state.market.event_allows_entry(self._now()))
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
                        attempts = exit_attempts.get(market_slug, 0)
                        if attempts >= self.config.max_shutdown_exit_attempts:
                            self._log_shutdown(
                                market_slug, "budget_exhausted",
                                position=position, attempts=attempts,
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
                            raise AdapterError("shutdown book is unavailable")
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
                            raise AdapterError(
                                "insufficient stressed depth to flatten exact "
                                "position")
                        side = "offer" if position > 0 else "bid"
                        action = PlanAction(
                            ActionKind.PLACE_IOC, market_slug,
                            "commissioning_shutdown_flatten", side=side,
                            price=exit_result.worst_price, qty=abs(position))
                        if self.driver is None:
                            raise RuntimeError("action driver is unavailable")
                        if self._now() >= deadline:
                            break
                        # Count before mutation because a transport failure
                        # after send may still have reached the venue.
                        attempts += 1
                        exit_attempts[market_slug] = attempts
                        # Logged before the send, for the same reason the counter
                        # increments before it: a transport failure after the send
                        # may still have reached the venue, and a record written
                        # only on success would omit exactly those cases.
                        self._log_shutdown(
                            market_slug, "flatten", position=position, side=side,
                            price=exit_result.worst_price, qty=abs(position),
                            attempt=attempts,
                            budget=self.config.max_shutdown_exit_attempts)
                        await self.driver.apply_one(
                            [action], market=state.market,
                            position=position, now=now)
                        delay = max(delay, min(2 ** max(attempts - 1, 0), 5))
                    except AdapterError as exc:
                        # A scoped read/mutation failure cannot starve another
                        # identified owner's cancel or exit. Ownership and
                        # invariant failures still reach the account handler.
                        self._log_shutdown(
                            market_slug, "owner_failed", error=type(exc).__name__)
                        delay = max(delay, 5.0)
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
        direct_notifier = self.notifier
        notifications: RuntimeNotificationDispatcher | None = None
        market_task: asyncio.Task | None = None
        private_task: asyncio.Task | None = None
        stop_reason = "unknown"
        preflight_complete = False
        preflight_error: Exception | None = None
        try:
            startup_mode = await self.preflight_until_ready()
            preflight_complete = True
            notifications = RuntimeNotificationDispatcher(direct_notifier)
            self.notifier = notifications
            # Establish scheduled-maintenance and public participation state
            # before any frame can authorize new exposure. Existing positions
            # still use the same bounded reducer/hard-exit machinery.
            await self._refresh_maintenance_schedule(
                self._now(), startup=True)
            await self._refresh_entry_pause(self._now(), startup=True)
            self._publish_active_scope_snapshot()
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
            next_api_budget_log = now + timedelta(
                seconds=API_BUDGET_LOG_SECONDS)
            # Start the first scheduler window after preflight so startup CPU
            # and latency do not contaminate the 24-slot runtime baseline.
            self._scheduler_last_log_at = now
            self._scheduler_process_cpu_at_last_log = time.process_time()
            market_task = asyncio.create_task(self.market_stream.next_book())
            private_task = asyncio.create_task(self.private_stream.next_event())
            while True:
                # Awaiting an already-ready task does not necessarily hand
                # control back to the event loop. During the 2026-08-28
                # twenty-owner no-ID incident every repair gather was empty;
                # the loop reached 67% CPU and did not run its SIGTERM callback
                # inside the full 300-second graceful-stop window.
                loop_yield_started = time.perf_counter()
                await self.sleep(RUN_LOOP_COOPERATIVE_YIELD_SECONDS)
                loop_delay = max(
                    0.0,
                    time.perf_counter() - loop_yield_started
                    - RUN_LOOP_COOPERATIVE_YIELD_SECONDS,
                )
                self._scheduler_event_loop_delays.append(loop_delay)
                if len(self._scheduler_event_loop_delays) > 8192:
                    del self._scheduler_event_loop_delays[:-8192]
                now = self._now()
                await self._refresh_maintenance_schedule(now)
                await self._refresh_entry_pause(now)
                stop_reason = self._stop_reason(now) or ""
                await self._announce_execution_fee_observation()
                await self._announce_loss_entry_order_cap()
                await self._announce_overnight_entry_order_cap()
                await self._announce_provisional_hard_stop()
                if stop_reason:
                    break
                pause_cancel_slugs = self._take_entry_pause_cancel_slugs()
                if pause_cancel_slugs:
                    stop_reason = await self._decision_sweep(
                        pause_cancel_slugs) or ""
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
                if now >= next_api_budget_log:
                    await self._log_api_budget(now)
                    next_api_budget_log = now + timedelta(
                        seconds=API_BUDGET_LOG_SECONDS)
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
                # Select only owners whose inputs changed or whose own timer is
                # due. Nonzero positions are included unconditionally; flat
                # unchanged markets skip the existing service/decision work.
                # The reconciler and action driver below are unchanged.
                scheduler_preempted = self._post_flat_top_up_can_preempt(
                    self._now())
                scheduled = (
                    () if scheduler_preempted
                    else self._scheduled_market_slugs(self._now()))
                service_cpu_started = time.process_time()
                stop_reason = await self._service_bound_markets(scheduled)
                self._scheduler_service_cpu_seconds += max(
                    0.0, time.process_time() - service_cpu_started)
                if stop_reason:
                    break
                serviced_generations = {
                    slug: self._market_dirty_generation.get(slug, 0)
                    for slug in scheduled
                }
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
                # If service yielded while a book/fill/order event arrived,
                # synchronize the exact affected owner once more before its
                # decision. Existing book coalescing and private wire ordering
                # remain the authority; this only notices their applied state.
                latest_scheduled = (
                    () if scheduler_preempted
                    else self._scheduled_market_slugs(
                        self._now(), record_pass=False))
                resync = tuple(
                    slug for slug in latest_scheduled
                    if (slug not in serviced_generations
                        or self._market_dirty_generation.get(slug, 0)
                        != serviced_generations[slug])
                )
                if resync:
                    service_cpu_started = time.process_time()
                    stop_reason = await self._service_bound_markets(resync)
                    self._scheduler_service_cpu_seconds += max(
                        0.0, time.process_time() - service_cpu_started)
                    if stop_reason:
                        break
                decision_slugs = tuple(dict.fromkeys(
                    (*scheduled, *latest_scheduled)))
                now = self._now()
                stop_reason = self._stop_reason(now) or ""
                await self._announce_execution_fee_observation()
                await self._announce_loss_entry_order_cap()
                await self._announce_overnight_entry_order_cap()
                await self._announce_provisional_hard_stop()
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
                    consumed_post_flat_at = (
                        self._post_flat_top_up_requested_at)
                    post_flat_requested = consumed_post_flat_at is not None
                    normal_scan_due = now >= next_selection
                    stream_tasks = (market_task, private_task)
                    for task in stream_tasks:
                        task.cancel()
                    await asyncio.gather(
                        *stream_tasks, return_exceptions=True)
                    try:
                        await self._top_up_active_markets(
                            now,
                            # Every urgent refill may reuse a recent complete
                            # scan after bounded live revalidation. At 17:00Z
                            # on 2026-08-30 a lifecycle refill ignored a scan
                            # only 40 seconds old, repeated discovery, then its
                            # critical positions proof hit Cloudflare 1015.
                            allow_cached_selection=(
                                self._urgent_top_up_can_use_cache(
                                    post_flat_requested)),
                            # Optional economic-idle eviction remains on the
                            # operator-approved 900-second cadence. An urgent
                            # lifecycle refill may fill mandatory holes early,
                            # but it cannot pull a merely uneconomic owner early.
                            allow_economic_idle=normal_scan_due,
                        )
                        self._clear_incremental_top_up_requests(
                            consumed_post_flat_at)
                        self._top_up_retry_not_before = datetime.min.replace(
                            tzinfo=timezone.utc)
                        next_selection = self._now() + timedelta(
                            seconds=self.config.market_selection_refresh_seconds)
                    except ApiAdmissionDeferred as exc:
                        self._clear_incremental_top_up_requests(
                            consumed_post_flat_at)
                        delay = self._api_admission_delay(exc)
                        self._log_api_backpressure("market_top_up", delay)
                        retry_at = self._now() + timedelta(seconds=delay)
                        self._top_up_retry_not_before = max(
                            self._top_up_retry_not_before, retry_at)
                        next_selection = retry_at
                    except Exception as exc:
                        self._clear_incremental_top_up_requests(
                            consumed_post_flat_at)
                        await self._report_runtime_error(
                            "market_top_up", exc)
                        # Event-driven lifecycle/post-flat requests bypass the
                        # ordinary selection time, so guard their admission too.
                        # On 2026-09-02 each failed 100-page catalog scan took
                        # about a minute and the next owner signal restarted it
                        # seconds later, leaving no useful decision time.
                        retry_at = self._now() + timedelta(
                            seconds=TOP_UP_FAILURE_RETRY_SECONDS)
                        self._top_up_retry_not_before = max(
                            self._top_up_retry_not_before, retry_at)
                        next_selection = retry_at
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
                        and not self._entry_pause_active()
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
                    except ApiAdmissionDeferred as exc:
                        delay = self._api_admission_delay(exc)
                        self._log_api_backpressure(
                            "reward_market_selection", delay)
                        next_selection = self._now() + timedelta(seconds=delay)
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
                            next_summary = await self._send_scheduled_summary(
                                now)
                    # Attributable mutations quarantine only their owner. An
                    # intent without a market cannot be scoped, so retain the
                    # account-wide fail-closed behavior for that case.
                    if any(not str(row["market_slug"] or "").strip()
                           for row in self.ledger.unresolved_intents()):
                        continue
                    if self._all_active_markets_blocked_by_no_id_intents():
                        # No exchange id means the focused repair has no REST
                        # call to await, and place() correctly bars every one
                        # of these owners. Avoid a CPU spin while retaining
                        # normal stream/account service and the two-second
                        # repair cadence on the next pass.
                        await self.sleep(NO_ID_REPAIR_IDLE_SECONDS)
                        continue
                decision_cpu_started = time.process_time()
                stop_reason = await self._decision_sweep(
                    decision_slugs) or ""
                self._scheduler_decision_cpu_seconds += max(
                    0.0, time.process_time() - decision_cpu_started)
                self._log_decision_scheduler(self._now())
                if stop_reason:
                    break
                if now >= next_summary:
                    next_summary = await self._send_scheduled_summary(now)
        except asyncio.CancelledError:
            stop_reason = self._stop_requested or "process_cancelled"
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
            if self._maintenance_schedule_task is not None:
                self._maintenance_schedule_task.cancel()
                await asyncio.gather(
                    self._maintenance_schedule_task,
                    return_exceptions=True)
                self._maintenance_schedule_task = None
            if self._ui_availability_task is not None:
                self._ui_availability_task.cancel()
                await asyncio.gather(
                    self._ui_availability_task, return_exceptions=True)
                self._ui_availability_task = None
            stream_tasks = tuple(
                task for task in (market_task, private_task) if task is not None)
            for task in stream_tasks:
                task.cancel()
            if stream_tasks:
                await asyncio.gather(*stream_tasks, return_exceptions=True)
            try:
                if preflight_complete:
                    await self._shutdown(stop_reason)
                self._publish_active_scope_snapshot(status="STOPPED")
                await self.private_stream.close()
                await self.market_stream.close()
            finally:
                if notifications is not None:
                    await notifications.close()
                self.notifier = direct_notifier
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
    policy_path = Path(getattr(
        args, "entry_order_policy", DEFAULT_ENTRY_ORDER_POLICY_PATH))
    try:
        entry_order_policy = load_entry_order_policy(policy_path)
    except EntryOrderPolicyError as exc:
        raise CommissioningRefused(str(exc)) from exc
    try:
        entry_quality_policy = load_entry_quality_policy(Path(getattr(
            args, "entry_quality_policy", DEFAULT_ENTRY_QUALITY_POLICY_PATH)))
    except ValueError as exc:
        raise CommissioningRefused(str(exc)) from exc
    config = CommissioningConfig(
        market_slug=(AUTO_REWARD_MARKET if auto_select else args.market_slug),
        economic_risk_accepted=args.accept_one_contract_risk,
        exclusive_api_key_confirmed=args.exclusive_api_key,
        maker_lab_stopped_confirmed=args.maker_lab_stopped,
        arm_live=args.arm_live,
        auto_select_reward_markets=auto_select,
        quote_size=Decimal(args.quote_size),
        max_markets=args.max_markets,
        entry_order_policy=entry_order_policy,
        entry_quality_policy=entry_quality_policy,
    )
    # Refuse before opening the credential file, database, client, or network.
    config.assert_authorized(now=now)
    account_state_mode = getattr(args, "account_state_mode", "websocket")
    if account_state_mode not in {"websocket", "rest-fallback"}:
        raise CommissioningRefused("invalid account-state mode")
    runner_type = CommissioningRunner
    if account_state_mode == "rest-fallback":
        from .rest_fallback import RestFallbackRunner
        runner_type = RestFallbackRunner
    secrets = load_runtime_secrets(require_polymarket=True)
    if not (secrets.discord_configured and secrets.discord_errors_configured):
        raise CommissioningRefused(
            "normal and critical Discord delivery must be configured")
    lease = SingleInstanceLease(args.lock_file)
    lease.acquire()
    client = AsyncPolymarketUS(
        key_id=secrets.key_id, secret_key=secrets.secret_key,
        timeout=REST_REQUEST_TIMEOUT_SECONDS)
    ledger = ExecutionLedger(args.database)
    runner = runner_type(
        client, config, ledger, DiscordNotifier(secrets),
        limiter=shared_api_limiter(float(config.rest_requests_per_second)))
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
        "--account-state-mode", choices=("websocket", "rest-fallback"),
        default="websocket",
        help="restart-scoped account source; REST fallback is experimental",
    )
    parser.add_argument(
        "--quote-size", type=int,
        choices=range(int(MIN_QUOTE_SIZE), int(MAX_QUOTE_SIZE) + 1),
        default=int(MIN_QUOTE_SIZE),
        help="whole contracts per quote; size-coupled risk limits derive automatically",
    )
    parser.add_argument(
        "--max-markets", type=int,
        choices=range(MIN_SLOT_CAPACITY, MAX_SLOT_CAPACITY + 1),
        default=MIN_SLOT_CAPACITY,
        help="restart-scoped market capacity; 21-22 are measurement canaries",
    )
    parser.add_argument(
        "--entry-order-policy", type=Path,
        default=DEFAULT_ENTRY_ORDER_POLICY_PATH,
        help="validated restart-scoped pending-entry-order policy JSON",
    )
    parser.add_argument(
        "--entry-quality-policy", type=Path,
        default=DEFAULT_ENTRY_QUALITY_POLICY_PATH,
        help="validated restart-scoped entry-only exit quality policy JSON",
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
        # something else tripped in the same breath -- HARD, the session
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
