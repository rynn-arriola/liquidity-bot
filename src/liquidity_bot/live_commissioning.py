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

from . import read_backoff
from .api_diagnostics import api_workflow
from .error_summary import error_summary

import argparse
import asyncio
import hashlib
import json
import math
import signal
import time
import socket
import traceback
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from .sdk_client import new_async_client

from .active_scope_cache import write_active_scope_snapshot
from .account_snapshots import (
    AccountEquitySnapshot, AsyncAccountSnapshotGateway,
    account_equity_snapshot)
from .account_reporting import AccountReportSnapshot
from .bot_era import bot_era_rewards_by_status
from .adapters import (
    AsyncIntervalLimiter, AsyncReadOnlyGateway, REST_REQUEST_TIMEOUT_SECONDS,
    shared_api_limiter)
from .cash_activity import corrected_opening_cash
from .commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    CommissioningUnavailable,
)
from .discord_summary import build_runtime_alert
from .errors import (
    AccountSnapshotConsistencyError, AdapterError, ApiAdmissionDeferred,
    EmptyBookError, MarketStreamUnavailable, RuntimeInvariantViolation)
from .execution_ledger import (
    ACTIVE_ORDER_STATES,
    UNRESOLVED_INTENTS,
    ExecutionLedger,
    LedgerConflict,
    QuoteObservation,
)
from .execution_timing import ExecutionTimings
from .entry_quality import (
    DEFAULT_ENTRY_QUALITY_POLICY_PATH, load_entry_quality_policy)
from .risk_windows import WindowRiskMeter, load_risk_window_policy
from .risk_diagnostics import RiskDiagnostics
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
from .ranker_priority import RankerPriority
from .slot_capacity import MAX_SLOT_CAPACITY, MIN_SLOT_CAPACITY
from .readiness import ReadinessVerdict
from .reward_calendar import (
    NEW_YORK, reward_day, reward_day_bounds, risk_day,
    split_reward_day_interval)
from .runner_summary import SummaryReportingMixin
from .runner_decisions import DecisionMixin
from .runner_shared import (  # noqa: F401 - unchanged values, re-exported
    CONTRACT_REFRESH_BATCH_SIZE,
    CONTRACT_REFRESH_LOOKAHEAD_SECONDS,
    CONTRACT_REFRESH_MAX_BACKOFF_SECONDS,
    CONTRACT_REFRESH_SECONDS,
    DECISION_EQUITY_REFRESH_MARGIN_SECONDS,
    DECISION_STATE_REFRESH_MARGIN_SECONDS,
    MAINTENANCE_FAILURE_THRESHOLD,
    MAINTENANCE_POLL_SECONDS,
    MAINTENANCE_STABLE_READS,
    MAX_BOUND_STREAM_RECOVERY_ATTEMPTS,
    MAX_MARKET_STREAM_RECOVERY_ATTEMPTS,
    MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS,
    MAX_RECOVERY_ATTEMPTS,
    MAX_RETIREMENT_ROUNDS,
    NONTRADING_CASH_MAX_PAGES,
    ORPHAN_PLACE_CLEAR_MIN_SECONDS,
    ORPHAN_PLACE_CLEAR_PROOFS,
    POST_FLAT_SELECTION_CACHE_SECONDS,
    SETTLEMENT_WAIT_BOOK_REFRESH_SECONDS,
    _CancellationEvidencePending,
    _ConfirmedMarketNotOpen,
    _OrphanPlaceEvidencePending,
    _RewardUnavailable,
)
from .runner_shutdown import ShutdownMixin
from .runner_startup import StartupMixin
from .runner_streams import StreamRecoveryMixin
from .runner_rest_proof import RestProofMixin
from .runner_account import AccountRiskMixin
from .runner_rewards import RewardRotationMixin
from .runner_top_up import TopUpSchedulingMixin
from .runner_earnings import EarningsReportingMixin
from .runner_diagnostics import (  # noqa: F401 - constant re-exported unchanged
    DECISION_SCHEDULER_LOG_SECONDS, RunnerDiagnosticsMixin)
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
    daily_reward_evidence_is_current,
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
TOP_UP_FAILURE_RETRY_SECONDS = 60.0

RETIRED_EXIT_RETRY_SECONDS = 60

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


class CommissioningRunner(
    SummaryReportingMixin,
    RunnerDiagnosticsMixin,
    EarningsReportingMixin,
    TopUpSchedulingMixin,
    RewardRotationMixin,
    AccountRiskMixin,
    RestProofMixin,
    StreamRecoveryMixin,
    StartupMixin,
    ShutdownMixin,
    DecisionMixin,
):
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
        # BC-2026-09-27-A: a market whose every new entry is skipped for a thin
        # book is an idle slot like any other economic refusal.
        "thin_book_entry_skip",
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
        quality_sink: Any = None,
        reward_cache_scope: str | None = None,
    ):
        self.client = client
        self.config = config
        self.ledger = ledger
        self.quality_sink = quality_sink
        self._reward_cache_scope = reward_cache_scope
        self._report_rewards_observed_at = None
        self._reward_refresh_failed = False
        if quality_sink is not None:
            from .market_quality_measurement import OrderWitness
            self.ledger.quality_observer = OrderWitness(quality_sink)
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
        self.gateway.entry_admission_check = self._check_entry_admission
        self.fill_hazard_model = fill_hazard_model or MondayFillHazardModel()
        self.reward_selector = reward_selector
        if self.reward_selector is None and config.auto_select_reward_markets:
            self.reward_selector = RewardMarketSelector(
                self.gateway,
                config.reward_selection_policy(),
                fill_hazard_model=self.fill_hazard_model,
                clock=lambda: self._now(),
                quality_sink=quality_sink,
                # Operator 2026-09-26: ranker-listed markets get book reads
                # and free slots first; every live gate still applies.
                priority_source=RankerPriority().ranks,
            )
        self.account = AsyncAccountSnapshotGateway(
            self.gateway, resolve_client_id=ledger.resolve_client_order_id)
        self.executor = LiveOrderExecutor(
            AsyncTradingGateway(self.gateway), ledger)
        self._execution_timings = ExecutionTimings()
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
        # A synchronous consumer may observe shared failure before next_book.
        self._pending_market_stream_error: MarketStreamUnavailable | None = None
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
        self._top_up_retry_selection: RewardMarketSelection | None = None
        from .selection_provenance import SelectionProvenance
        self._selection_provenance = SelectionProvenance()
        self.balance: AccountBalance | None = None
        # Cash-stable fiat + per-position `cashValue` snapshot.  Retail
        # currently returns assetNotional=0 even with open positions, so no
        # safety or reporting consumer may rebuild marked equity from the
        # balance row alone.
        self._equity_snapshot: AccountEquitySnapshot | None = None
        self._account_report: AccountReportSnapshot | None = None
        self._account_report_invalid = False
        self._cash_report_blocked = False
        self._cash_report_attempted_at: datetime | None = None
        self._defer_cash_reporting = False
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
        try:
            self._window_risk = (
                WindowRiskMeter(self.ledger.connection, config.risk_window_policy)
                if config.risk_window_policy is not None else None)
        except ValueError as exc:
            raise CommissioningRefused(str(exc)) from exc
        if self._window_risk is not None:
            if self._window_risk.held:
                self._hard_stop_hold_notice = "held"
            if self._window_risk.provisional:
                self._provisional_hard_stop_notice = "started"
        if self._window_risk is not None and (
                self._hard_stop_hold_day is not None
                or self._provisional_hard_stop_at is not None):
            raise CommissioningRefused(
                "resolve the existing daily HARD hold with fresh flat proof "
                "before migrating to six-hour risk windows")
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
        self._risk_diagnostics = RiskDiagnostics()
        self._risk_refresh_stage = "not_started"
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
        # Optional evidence must never veto a valid scope change.
        self._remember_selection_binding(clean)
        self._remember_warm_restart_scope(clean)
        from .market_quality_measurement import emit, binding_snapshot
        emit(self.quality_sink, "binding", binding_snapshot, self)
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

    @staticmethod
    def _payout_floor_count(selection: RewardMarketSelection | None,
                            floor: float) -> int | None:
        """Optional telemetry must not make scan or summary handling fail."""
        try:
            return selection.payout_floor_market_count(floor)
        except Exception:
            return None

    @api_workflow("contract_refresh")
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
        try:
            market = await self.gateway.fetch_market(slug)
            incentives = await self.gateway.fetch_incentives([slug])
        except Exception:
            state.clear_reward_unavailability()
            raise
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
        """Keep successful reward absence distinct from failed/unknown reads."""

        try:
            self._apply_contract_rows(
                state, market=market, incentive=incentive, now=now,
                for_exit=for_exit,
                accept_bound_lifecycle_end=accept_bound_lifecycle_end)
        except _RewardUnavailable:
            state.incentive = None
            state.clear_economics()
            if (accept_bound_lifecycle_end and not for_exit
                    and not self._is_barred_from_quoting(state.slug)
                    and self._owner_is_flat_and_clear(state.slug)):
                previous = state.reward_unavailable_observed_at
                if (previous is None or not 0 <= (now - previous).total_seconds()
                        <= CONTRACT_REFRESH_MAX_BACKOFF_SECONDS
                        + CONTRACT_REFRESH_LOOKAHEAD_SECONDS):
                    state.reward_unavailable_since = now
                state.reward_unavailable_observed_at = now
            else:
                state.clear_reward_unavailability()
            raise
        except Exception:
            state.clear_reward_unavailability()
            raise
        else:
            state.clear_reward_unavailability()

    def _apply_contract_rows(
        self, state: MarketState, *, market: MarketMetadata,
        incentive: MarketIncentive | None, now: datetime, for_exit: bool,
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
            raise CommissioningUnavailable(
                f"{slug} event pregame state is unavailable "
                f"({state.market.event_entry_block_reason(now)})")
        if state.incentive is None:
            raise _RewardUnavailable(
                f"{slug} has no liquidityProgram incentive")
        try:
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now,
                allow_daily_event=self.config.allow_daily_event_rewards,
                daily_event_horizon_seconds=self.config.daily_event_horizon_seconds,
                daily_event_observed_at=state.incentive.observed_at)
        except AmbiguousIncentiveError as exc:
            raise CommissioningUnavailable(
                f"{slug} has ambiguous incentive periods") from exc
        if period is None:
            if (self.config.allow_daily_event_rewards
                    and self.config.daily_event_horizon_seconds > 21600
                    and any(str(p.period).strip().lower() in {"daily_event", "daily-event"}
                            for p in state.incentive.periods)
                    and not daily_reward_evidence_is_current(
                        state.incentive.observed_at, now)):
                # A scan crossing midnight needs new proof, not retirement of
                # the prior owner based on an unrefreshed reward response.
                raise CommissioningUnavailable(f"{slug} daily reward day is unproven")
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
            raise _RewardUnavailable(
                f"{slug} has no applicable pregame liquidity period")
        state.reward_program_id = period.program_id
        state.reward_program_ends_at = period.ends_at
        state.reward_period_ended_at = None
        state.reward_period_end_confirmed_at = None
        state.reward_authorization_lost_at = None
        state.reward_authorization_loss_reason = None
        state.reward_authorization_loss_confirmed_at = None

    @api_workflow("contract_refresh")
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
                    state.clear_reward_unavailability()
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
                    if not isinstance(exc, _RewardUnavailable):
                        state.clear_reward_unavailability()
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
        state.book_observed_at = state.book.received_at or self._now()
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
        accepts = getattr(self.market_stream, "accepts", None)
        if accepts is not None and not accepts(book):
            return
        observed_at = book.received_at or self._now()
        if state.book_observed_at is not None and observed_at < state.book_observed_at:
            return
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
            self._window_entry_ready(now),
            self._provisional_hard_stop_at is None,
            self._baseline_day == today,
            self._nontrading_cash is not None,
            self._nontrading_cash_day == today,
            not self._cash_report_blocked,
            session_ready,
            not self._entry_pause_active(),
            self._effective_entry_order_cap(now),
        )

    def _entry_pause_active(self) -> bool:
        """Whether new risk is blocked without affecting reducers or exits."""

        return (
            self._manual_entry_paused
            or self._hard_stop_active()
            or (self._window_risk is not None and self._window_risk.provisional)
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
        if self._hard_stop_active():
            return ("window_hard_stop_flatten_and_wait" if self._window_risk
                    else "daily_hard_stop_flatten_and_wait")
        if self._window_risk is not None and self._window_risk.provisional:
            return "window_loss_awaiting_position_close"
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

    async def _fetch_execution_history(self, now: datetime):
        return await self.account.fetch_executions(observed_at=now)

    def _reconciliation_read_endpoints(self) -> tuple[str, ...]:
        return ("open_orders", "positions")

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
                self._record_execution_timing("cancel", slug=slug, intent_id=intent_id)

    async def _observe_private(self, event: Any, now: datetime) -> None:
        unresolved_owners = self._unresolved_intent_owners()
        execution_owners_before = self._execution_owner_slugs()
        affected_slugs = set(self._private_event_market_slugs(event))
        received_at = getattr(event, "received_at", None) or now
        had_near_deadline_position = (
            self._position_near_its_flatten_deadline(now))
        held_before = self._held_refill_owners() if had_near_deadline_position else ()
        self.gate.observe(event, received_at=received_at)
        self.projector.apply(event, received_at=received_at)
        if getattr(event, "kind", None) in {
                "order_snapshot", "order_update", "position_snapshot", "position_update"}:
            self._record_execution_timing("positions")
        if self._settlement_wait_slugs:
            self._sync_settlement_wait_positions(self._now())
        if self._inactive_market_wait_slugs:
            self._sync_inactive_market_wait_positions(self._now())
        self._request_post_flat_top_up_if_needed(
            had_near_deadline_position, self._now(), held_before=held_before)
        self._record_resolved_intent_owners(unresolved_owners)
        affected_slugs.update(
            execution_owners_before.symmetric_difference(
                self._execution_owner_slugs()))
        for slug in affected_slugs:
            self._mark_market_dirty(slug, received_at)

    def _record_execution_timing(self, event, **fields):
        # Local evidence only: no API call, no strategy input, no exception may
        # turn an acknowledged mutation into an apparent execution failure.
        try:
            at = self._now()
            source = getattr(self, "account_state_mode", "websocket")
            if event == "positions":
                quantities = {str(row["market_slug"]): Decimal(row["net_qty"])
                    for row in self.ledger.query("SELECT market_slug,net_qty FROM "
                        "execution_positions WHERE CAST(net_qty AS REAL)!=0")}
                self._execution_timings.observe(quantities, at=at, source=source, complete=True)
            elif event == "cancel":
                rows = self.ledger.query("SELECT state FROM execution_intents WHERE intent_id=?",
                                         (fields["intent_id"],))
                if rows and rows[0]["state"] == "RECONCILED":
                    self._execution_timings.cancel_confirmed(fields["slug"], at=at, source=source)
            elif event == "action":
                deadline = None
                if fields["action"].kind == ActionKind.PLACE_IOC:
                    get_deadline = getattr(self, "_position_exit_deadline", None)
                    if get_deadline is not None:
                        deadline = get_deadline(fields["action"].market_slug)
                self._execution_timings.action(at=at, source=source, deadline=deadline, **fields)
        except Exception:
            # A missing timing row remains missing; never report fabricated 0s.
            pass

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
        # Project one item before requesting the next: a later queued error
        # must not discard the valid prefix of a bulk drain. The pool is
        # bounded; this extra bound also protects injected/custom streams.
        for _ in range(16384):
            events = drain(limit=1)
            if not events:
                return count
            for event in events:
                await self._observe_private(event, self._now())
                count += 1
        raise AdapterError("private event drain exceeded its bounded backlog")

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
                hard_stop_flatten_required=self._hard_stop_active(),
                maintenance_flatten_required=(
                    self._scheduled_maintenance_flatten_required()))
        new_risk_allowed = self._entry_risk_allowed(now, state.market)
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
            book_known=not self._public_book_invalid(state.slug),
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
            hard_stop_flatten_required=self._hard_stop_active(),
            maintenance_flatten_required=(
                self._scheduled_maintenance_flatten_required()),
        )

    def _check_entry_admission(self, payload: Mapping[str, Any]) -> None:
        """Recheck entry authority after governor wait, without network I/O.

        The executor has already reserved THIS create as SUBMITTING. Do not
        call gate.ready(), which would reject that very intent, or count it
        twice against the cap. Source wrappers independently recheck their
        generation and exact proof. Reducers/cancels do not use this gate.
        """
        slug = str(payload.get("marketSlug") or "")
        self._assert_runtime_scope()
        state = self.state_for(slug)
        if state.market is None or state.book is None or state.book_observed_at is None:
            raise RuntimeInvariantViolation(
                "entry_admission_incomplete", "entry market evidence is incomplete")
        if isinstance(self.gateway, EventAwareGateway):
            state.market = self.gateway.apply_event_status(state.market)
        position = self.ledger.position(slug)
        ownership_known = self.ledger.ownership_is_known()
        working, reservations = self._flat_entry_orders_and_reservations()
        # Do every ledger read before sampling send-time authority. Rebuilding
        # a planner frame here also computed unused basis/portfolio/quote data.
        now = self._now()
        market = state.market
        seconds_to_event = (
            (market.game_start - now).total_seconds()
            if market.game_start is not None else None)
        daily_clock_allowed = True
        if (state.incentive is not None and market.game_start is not None
                and any(str(p.period).strip().lower() in {"daily_event", "daily-event"}
                        for p in state.incentive.periods)):
            # Admission can wait across midnight or an explicit reward end.
            # This is a local recheck, never another API call or an exit gate.
            try:
                current_period = select_applicable_period(
                    state.incentive.periods, market.game_start, now,
                    allow_daily_event=self.config.allow_daily_event_rewards,
                    daily_event_horizon_seconds=self.config.daily_event_horizon_seconds,
                    daily_event_observed_at=state.incentive.observed_at)
                daily_clock_allowed = current_period is not None and (
                    str(current_period.period).strip().lower()
                    in {"day_of", "day-of", "dayof", "daily_event", "daily-event"})
            except (AmbiguousIncentiveError, ValueError):
                daily_clock_allowed = False
        allowed = (
            not self._stop_requested
            and daily_clock_allowed
            and not self.config.recovery_only
            and position == 0
            and not self._public_book_invalid(slug)
            and self._entry_risk_allowed(now, market)
            and ownership_known
            and self.gate.session_evidence_ready(now=now)
            and market.active and not market.closed
            and market.is_supported_moneyline
            and seconds_to_event is not None
            and seconds_to_event > self.reconciler.policy.force_exit_before_start_seconds
            and 0 <= (now - state.book_observed_at).total_seconds()
            <= self.reconciler.policy.max_book_age_seconds
            and len(working) + reservations <= self._effective_entry_order_cap(now)
        )
        from .market_quality_measurement import emit
        emit(getattr(self, "quality_sink", None), "entry_admission", lambda: {
            "at": now.isoformat(), "market": slug, "allowed": allowed,
            "client_order_id": payload.get("clientOrderId"),
            "side": payload.get("side"), "intent": payload.get("intent"),
            "flat_entry_orders": len(working), "reservations": reservations,
            "position": str(position), "ownership_known": ownership_known,
            "seconds_to_event": seconds_to_event, "daily_clock_allowed": daily_clock_allowed,
            "book_observed_at": state.book_observed_at.isoformat(),
            "stop_requested": self._stop_requested,
            "basis": "existing_final_send_gate_not_new_authority"})
        if not allowed:
            raise ApiAdmissionDeferred(
                "entry safety evidence expired or changed; request not sent",
                error_code="LOCAL_ENTRY_STATE_CHANGED", retry_after_seconds=1.0)

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

        snapshot = self._reporting_equity(self._now())
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
        snapshot = self._reporting_equity(self._now())
        if (self.balance is None or self._cash_baseline is None
                or snapshot is None):
            return ledger_value
        if (self._nontrading_cash is None
                or self._nontrading_cash_day != self._baseline_day):
            raise RuntimeError("non-trading cash adjustment is unavailable")
        try:
            realised = snapshot.realized_trading_pnl(
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
        if self.risk_guard is not None and self._window_risk is None:
            self.risk_guard.adopt_day_baseline(opening_equity)

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
        if self._maintenance_owner_is_idle(state.slug, now):
            # Keep the due time: the first post-hold pass must obtain fresh
            # depth, not inherit a dead-market or recovery delay.
            return next_refresh
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

    def _public_book_invalid(self, slug: str) -> bool:
        invalid = getattr(self.market_stream, "book_invalid", None)
        return invalid is not None and invalid(slug)

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
        if self._public_book_invalid(slug):
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

    def _entry_observation_pending(self) -> bool:
        return False

    def _position_priority_tokens(self) -> dict[str, tuple[Any, ...]]:
        """Optional account-source scheduling hint, never mutation authority."""

        return {}

    def _position_priority_visit_limit(self, slug: str) -> int:
        """Bound scheduling-only interruptions for one owner per sweep."""

        return 4

    def _position_priority_rank(self, slug: str) -> int:
        """Optional inter-position urgency; normal WebSocket order is unchanged."""

        return 2

    def _position_priority_replan_immediately(
        self, slug: str, before: tuple[Any, ...], after: tuple[Any, ...],
    ) -> bool:
        """Return whether one scheduling-token change needs same-owner service."""

        return False

    def _position_priority_must_yield(self, slug: str) -> bool:
        """Optional bounded-sweep handoff; never authorizes an extra mutation."""
        return False

    async def _periodic_position_checkpoint(self, market_task, private_task):
        """Source-specific serial handoff after a periodic account stage."""
        return market_task, private_task, self._stop_requested or None

    async def _refresh_bound_books(self, now: datetime) -> None:
        from .selection_books import book_scope
        # Recovery needs the same current depth, not a mandatory REST source.
        # A bounded, stop-aware public collector can supply it even while the
        # public REST book endpoint is throttled. This scope discovers nothing
        # and cannot alter account/ownership proof. Missing/failed stream data
        # retains the existing paced REST path, including exact empty-book
        # evidence required for settlement handling; never infer an empty book.
        async with book_scope(self.gateway, self.active_slugs):
            await self._refresh_bound_books_inner(now)

    async def _refresh_bound_books_inner(self, now: datetime) -> None:
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
                scope = getattr(self.gateway, "traffic_class", None)
                # New startup candidates are optional price discovery, not
                # position safety. Recovering owners retain the safety lane.
                with (scope("entry") if callable(scope) and slug not in recovering
                      else nullcontext()):
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
                if isinstance(exc, ApiAdmissionDeferred) or (
                        isinstance(exc, AdapterError) and exc.status_code == 429):
                    # A shared endpoint limit is not evidence that each
                    # remaining market is bad. Preserve scope and yield once.
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

    def _routine_book_scope(self, slug: str):
        """Pace only unambiguous flat quoting, never held/recovery risk."""
        scope = getattr(self.gateway, "optional_book_reads", None)
        state = self.state_for(slug)
        if (scope is not None and not self._stop_requested
                and slug not in self._recovery_slugs and slug not in self._retired
                and state.economically_ineligible_since is None
                and self.ledger.position(slug) == 0
                and not self.ledger.absent_orders(slug)
                and not self.ledger.market_has_unresolved_intent(slug)):
            return scope()
        return nullcontext()

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

    def _redacted_error_detail(self, exc: Exception) -> str:
        detail = str(exc).replace("\n", " ")
        for name in ("key_id", "secret_key"):
            secret = str(getattr(self.client, name, "") or "")
            if secret:
                detail = detail.replace(secret, "<redacted>")
        return error_summary(detail)

    async def _report_runtime_error(
        self, component: str, exc: Exception, attempt: int | None = None, *,
        include_traceback: bool = True,
    ) -> None:
        if attempt is None:
            attempt = self._runtime_error_counts.get(component, 0) + 1
        self._runtime_error_counts[component] = max(
            attempt, self._runtime_error_counts.get(component, 0))
        now = self._now()
        detail = self._redacted_error_detail(exc)
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

    @staticmethod
    def _terminal_exception_reason(component: str, exc: Exception) -> str:
        if isinstance(exc, RuntimeInvariantViolation):
            return f"critical:{exc.code}"
        return f"critical:{component}_{type(exc).__name__}"

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

    def _maintenance_owner_is_idle(self, slug: str, now: datetime) -> bool:
        """Skip price work only for a proven empty owner during a known hold.

        Account reconciliation, stream consumption and lifecycle checks remain
        live. Any newly observed exposure or ambiguity immediately removes this
        exception; a maintenance notice alone never proves the account empty.
        """
        return (
            (self._maintenance_decision.phase != MaintenancePhase.NORMAL
             or self._maintenance_health_required_for is not None)
            and not self._stop_requested
            and self.gate.ready(self.ledger, now=now)
            and self.ledger.position(slug) == 0
            and not self.ledger.working_orders(slug)
            and not self.ledger.absent_orders(slug)
            and not self.ledger.market_has_unresolved_intent(slug)
        )

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
            return await self._recover_strategy_state(reason, slug)
        except RuntimeInvariantViolation as invariant:
            await self._report_runtime_error(
                "strategy_state_terminal", invariant)
            return f"critical:{invariant.code}"
        return None

    async def _handle_strategy_plan_failure(self, exc: Exception) -> str:
        """A pure planning exception is a code/state invariant, not transport."""

        await self._report_runtime_error("strategy_plan_terminal", exc)
        return self._terminal_exception_reason("strategy_plan", exc)

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
            if startup_mode == "HOST_RECOVERY_FLAT":
                stopped = self._now()
                return CommissioningResult(
                    self.started_at or stopped, self.cutoff_at, stopped,
                    "host_recovery_complete", True, 0)
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
                    # Cancellation needs ownership, not a new price. Preserve
                    # the existing fast pause path without adding book reads.
                    stop_reason = await self._decision_sweep(
                        pause_cancel_slugs, service_books=False) or ""
                    if stop_reason:
                        break
                if self._streams_need_rebind:
                    # Both `next_*` tasks are reading streams that are already
                    # closed, so waiting on them below can only ever time out.
                    # Drop them before rebinding: the recovery replaces both
                    # stream objects, and a task holding the old one would
                    # otherwise be recreated against a stream nobody owns.
                    market_task, private_task, stop_reason = (
                        await self._consume_ready_stream_tasks(market_task, private_task))
                    if stop_reason:
                        break
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
                self._sample_fill_markouts(now)
                stop_reason = await self._service_urgent_positions() or ""
                if stop_reason:
                    break
                if self._streams_need_rebind:
                    continue
                now = self._now()
                if now >= next_balance:
                    try:
                        # A decision/exit pass may just have published a full
                        # bracket. Use its ORIGINAL data age, not an unrelated
                        # periodic timer, to avoid an immediate duplicate walk.
                        next_balance = await self._refresh_periodic_balance(now)
                    except Exception:
                        # Freshness expires in 15 seconds and then triggers the
                        # normal risk stop. Never substitute buying power.
                        next_balance = now + timedelta(seconds=5)
                    market_task, private_task, stop_reason = (
                        await self._periodic_position_checkpoint(
                            market_task, private_task))
                    if stop_reason:
                        break
                    if self._streams_need_rebind:
                        continue
                now = self._now()
                if now >= next_state_reconcile:
                    try:
                        # Fresh complete snapshots renew account visibility.
                        # Individual ambiguity stays behind its market gate and
                        # is repaired one owner at a time below; reading every
                        # id here made the ten-second freshness task itself a
                        # twenty-slot head-of-line blocker.
                        await self._rest_reconcile(
                            now, tag="periodic", strict_order_markets=frozenset())
                    except Exception as exc:
                        # Preserve the last proven state briefly. If both the
                        # private stream and REST remain quiet/unavailable, the
                        # freshness gate expires and shuts down new risk.
                        self._note_periodic_reconcile(exc)
                    else:
                        self._note_periodic_reconcile(None)
                    next_state_reconcile = now + timedelta(seconds=10)
                    market_task, private_task, stop_reason = (
                        await self._periodic_position_checkpoint(
                            market_task, private_task))
                    if stop_reason:
                        break
                    if self._streams_need_rebind:
                        continue
                # Periodic account reads may have crossed an exit boundary;
                # use the current clock before optional reporting starts.
                next_earnings = await self._refresh_due_earnings(
                    self._now(), next_earnings)
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
                        priority_replans, refresh_books=False)
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
                        priority_replans, refresh_books=False)
                    if stop_reason:
                        break
                    stop_reason = await self._decision_sweep(
                        priority_replans) or ""
                    if stop_reason:
                        break
                    if self._streams_need_rebind:
                        continue
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
                scheduled = self._scheduled_market_slugs(self._now())
                service_cpu_started = time.process_time()
                stop_reason = await self._service_bound_markets(
                    scheduled, refresh_books=False)
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
                latest_scheduled = self._scheduled_market_slugs(
                    self._now(), record_pass=False)
                resync = tuple(
                    slug for slug in latest_scheduled
                    if (slug not in serviced_generations
                        or self._market_dirty_generation.get(slug, 0)
                        != serviced_generations[slug])
                )
                if resync:
                    service_cpu_started = time.process_time()
                    stop_reason = await self._service_bound_markets(
                        resync, refresh_books=False)
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
                if (self.config.auto_select_reward_markets
                        and now >= next_selection
                        and not self._entry_pause_active()
                        and self._safe_to_scan()):
                    old_market, old_private = market_task, private_task
                    market_task = private_task = None
                    await self._settle_stream_readers(old_market, old_private)
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
                if self._streams_need_rebind:
                    continue
                # Existing owners get their guarded decision pass before any
                # refill, including every interrupted/retried catalog attempt.
                now = self._now()
                if self._incremental_top_up_due(now, next_selection):
                    consumed_post_flat_at = (
                        self._post_flat_top_up_requested_at)
                    post_flat_requested = consumed_post_flat_at is not None
                    normal_scan_due = now >= next_selection
                    old_market, old_private = market_task, private_task
                    market_task = private_task = None
                    await self._settle_stream_readers(old_market, old_private)
                    try:
                        await self._top_up_active_markets(
                            now,
                            # Every urgent refill may reuse a recent complete
                            # scan after bounded live revalidation. At 17:00Z
                            # on 2026-08-30 a lifecycle refill ignored a scan
                            # only 40 seconds old, repeated discovery, then its
                            # critical positions proof hit Cloudflare 1015.
                            allow_cached_selection=(not normal_scan_due and
                                self._urgent_top_up_can_use_cache(
                                    post_flat_requested)),
                            # Optional economic-idle eviction remains on the
                            # operator-approved 900-second cadence. An urgent
                            # lifecycle refill may fill mandatory holes early,
                            # but it cannot pull a merely uneconomic owner early.
                            allow_economic_idle=normal_scan_due,
                        )
                        self._top_up_retry_selection = None
                        self._clear_incremental_top_up_requests(
                            consumed_post_flat_at)
                        self._top_up_retry_not_before = datetime.min.replace(
                            tzinfo=timezone.utc)
                        next_selection = self._now() + timedelta(
                            seconds=self.config.market_selection_refresh_seconds)
                        cache_expiry = getattr(self, "_top_up_cache_expires_at", None)
                        if cache_expiry is not None:
                            next_selection = min(next_selection, cache_expiry)
                    except ApiAdmissionDeferred as exc:
                        self._clear_incremental_top_up_requests(
                            consumed_post_flat_at)
                        delay = self._api_admission_delay(exc)
                        self._log_api_backpressure(
                            "market_top_up", delay, cause=exc.error_code)
                        retry_at = self._now() + timedelta(seconds=delay)
                        self._top_up_retry_not_before = max(
                            self._top_up_retry_not_before, retry_at)
                        next_selection = retry_at
                    except (LedgerConflict, RuntimeInvariantViolation) as exc:
                        self.gate.on_reconciliation_conflict()
                        await self._report_runtime_error("market_top_up_terminal", exc)
                        stop_reason = self._terminal_exception_reason("market_top_up", exc)
                        break
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
            old_market, old_private = market_task, private_task
            market_task = private_task = None
            try:
                await self._settle_stream_readers(old_market, old_private)
            except Exception as exc:
                # An integrity error still requires attempted safe shutdown;
                # never let teardown skip cancels/exits or claim flatness.
                self.gate.on_reconciliation_conflict()
                stop_reason = f"stream_teardown_failure:{type(exc).__name__}"
                await self._report_runtime_error("shutdown_stream_teardown", exc)
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
        risk_window_policy = load_risk_window_policy()
        from .entry_expiry import load_entry_expiry_policy
        entry_expiry_policy = load_entry_expiry_policy()
        from .selection_exclusions import load_selection_exclusions
        selection_exclusions = load_selection_exclusions()
    except (ValueError, OSError) as exc:
        raise CommissioningRefused(str(exc)) from exc
    config = CommissioningConfig(
        allow_daily_event_rewards=not getattr(args, "disable_daily_event_rewards", False),
        daily_event_horizon_seconds=3600 * getattr(args, "daily_event_entry_hours", 24),
        market_slug=(AUTO_REWARD_MARKET if auto_select else args.market_slug),
        economic_risk_accepted=args.accept_one_contract_risk,
        exclusive_api_key_confirmed=args.exclusive_api_key,
        maker_lab_stopped_confirmed=args.maker_lab_stopped,
        arm_live=args.arm_live,
        recovery_only=bool(getattr(args, "recovery_only", False)),
        entry_expiry_policy=entry_expiry_policy,
        selection_exclusions=selection_exclusions,
        auto_select_reward_markets=auto_select,
        quote_size=Decimal(args.quote_size),
        max_markets=args.max_markets,
        entry_order_policy=entry_order_policy,
        entry_quality_policy=entry_quality_policy,
        risk_window_policy=risk_window_policy,
    )
    # Refuse before opening the credential file, database, client, or network.
    config.assert_authorized(now=now)
    print("[selection-exclusions] excluded_leagues="
          + (",".join(sorted(selection_exclusions.excluded_leagues)) or "none"),
          flush=True)
    from .account_source import load_source_policy, startup_mode
    from .account_source_runner import AccountSourceRunner
    from .rest_polling_policy import (
        DEFAULT_REST_POLLING_POLICY_PATH, load_rest_polling_policy)
    try:
        source_policy = load_source_policy(Path(getattr(args, "account_source_policy",
            Path(__file__).resolve().parents[2] / "config/account_source_policy.json")))
        account_state_mode = startup_mode(getattr(args, "account_state_mode", None),
            Path(args.database).parent, default=source_policy.default_mode)
        polling_policy = load_rest_polling_policy(Path(getattr(args, "rest_polling_policy",
            DEFAULT_REST_POLLING_POLICY_PATH)))
    except (ValueError, TypeError, OSError) as exc:
        raise CommissioningRefused(str(exc)) from exc
    from .refreshing_books import RefreshingMarketStream, install_runtime_books
    runner_type = AccountSourceRunner
    runner_options = dict(account_state_mode=account_state_mode,
                          source_policy=source_policy, rest_polling_policy=polling_policy,
                          market_stream_factory=RefreshingMarketStream)
    secrets = load_runtime_secrets(require_polymarket=True)
    if not (secrets.discord_configured and secrets.discord_errors_configured):
        raise CommissioningRefused(
            "normal and critical Discord delivery must be configured")
    lease = SingleInstanceLease(args.lock_file)
    lease.acquire()
    client = new_async_client(
        key_id=secrets.key_id, secret_key=secrets.secret_key,
        timeout=REST_REQUEST_TIMEOUT_SECONDS)
    ledger = ExecutionLedger(args.database)
    quality_sink = None
    if getattr(args, "market_quality_directory", None) is not None:
        try:
            from .market_quality_store import QualityStore
            quality_sink = QualityStore(Path(args.market_quality_directory))
            from .market_quality_measurement import emit, plain
            emit(quality_sink, "run", lambda: {
                "config": plain(config), "account_source": account_state_mode,
                "at": datetime.now(timezone.utc).isoformat()})
        except Exception:
            print("[market-quality] unavailable; trading unchanged", flush=True)
    runner = runner_type(
        client, config, ledger, DiscordNotifier(secrets),
        limiter=shared_api_limiter(float(config.rest_requests_per_second)),
        quality_sink=quality_sink,
        reward_cache_scope=hashlib.sha256(secrets.key_id.encode()).hexdigest(),
        **runner_options)
    from .selection_books import SelectionBookSource

    async def book_checkpoint():
        if runner._stop_requested:
            raise CommissioningRefused("operator stopped during book collection")
        await runner.gateway._checkpoint_before_read()

    runner.gateway.selection_books = SelectionBookSource(
        client, stream_factory=RefreshingMarketStream,
        clock=lambda: runner._now(), checkpoint=book_checkpoint)
    install_runtime_books(runner)
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
        if quality_sink is not None:
            quality_sink.close()
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
    parser.add_argument(
        "--disable-daily-event-rewards", action="store_true",
        help="disable daily-event fallback; retain established day-of selection",
    )
    parser.add_argument(
        "--daily-event-entry-hours", type=int, choices=(6, 24), default=24,
        help="daily-event entry lookahead; 6 restores the previous horizon",
    )
    parser.add_argument("--recovery-only", action="store_true",
                        help="recover identified account exposure, never start entries")
    parser.add_argument("--accept-one-contract-risk", action="store_true")
    parser.add_argument("--exclusive-api-key", action="store_true")
    parser.add_argument("--maker-lab-stopped", action="store_true")
    parser.add_argument(
        "--account-state-mode", choices=("websocket", "rest", "rest-fallback", "hybrid"),
        default=None,
        help="explicit account source; otherwise use the last verified selection",
    )
    parser.add_argument(
        "--rest-polling-policy", type=Path, default=argparse.SUPPRESS,
        help="validated restart-scoped REST optimization JSON",
    )
    parser.add_argument("--market-quality-directory", type=Path, default=None,
                        help="opt-in local-only quality archive; no extra API requests")
    parser.add_argument("--account-source-policy", type=Path, default=argparse.SUPPRESS,
                        help="validated restart-scoped source/handoff configuration")
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
    if args.recovery_only:
        return 0
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
