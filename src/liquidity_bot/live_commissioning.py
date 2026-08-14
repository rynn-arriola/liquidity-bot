"""Explicitly armed one-contract commissioning runner.

The economic evidence gate remains red. This runner exists only for the
operator-accepted experiment and is mechanically constrained to one selected
rewarded market at a time, one contract per quote, an operator-controlled stop, a $5
normal session stop, and a $15 emergency hard stop.

Importing this module has no side effect. Exchange access is possible only
through ``main()`` with all explicit arming acknowledgements.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from polymarket_us import AsyncPolymarketUS

from .account_snapshots import AsyncAccountSnapshotGateway
from .adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from .commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    CommissioningUnavailable,
)
from .discord_summary import LiquiditySummary, build_liquidity_summary
from .errors import AdapterError, RuntimeInvariantViolation
from .execution_ledger import ExecutionLedger
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
from .market_state import MarketState
from .market_selector import (
    RewardMarketSelection,
    RewardMarketSelector,
    RewardSelectionPolicy,
)
from .models import AccountBalance, BookSnapshot, MarketIncentive, MarketMetadata
from .notifications import DiscordNotifier
from .readiness import ReadinessVerdict
from .reconciler import (
    ActionKind,
    DesiredStateReconciler,
    MarketFrame,
    PlanAction,
    ReconcilerPolicy,
    WorkingOrder,
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
from .streams import MarketReadOnlyStream, PrivateReadOnlyStream
from .venue_state import PrivateLedgerProjector


Clock = Callable[[], datetime]

# Generous: with backoff capped at 60s this is roughly an hour of retrying
# before a transient startup condition is finally treated as a refusal.
MAX_STARTUP_UNAVAILABLE_ATTEMPTS = 60

RECOVERABLE_STRATEGY_CRITICALS = frozenset({
    "book_stale",
    "execution_state_unknown",
    # The exit book was too thin to cross before the kickoff deadline. Holding
    # is the correct response and the pre-kickoff flatten still forces the
    # position out, so ending the session here would trade a bounded
    # one-contract hold for hours of lost quoting across every other market.
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
        private_stream_factory: Callable[[Any], Any] = PrivateReadOnlyStream,
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
                RewardSelectionPolicy(
                    quote_size=float(config.quote_size),
                    projection_horizon_seconds=(
                        config.reward_projection_horizon_seconds),
                    projection_haircut=float(config.reward_projection_haircut),
                    exit_depth_haircut=.5,
                    exit_cost_safety_multiple=float(
                        config.reward_cost_safety_multiple),
                    max_book_candidates=(
                        config.max_selection_book_candidates),
                    minimum_entry_spread=float(
                        config.minimum_entry_spread),
                ),
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
        self.market_stream_factory = market_stream_factory
        self.private_stream_factory = private_stream_factory
        self.market_stream = market_stream_factory(client)
        self.private_stream = private_stream_factory(client)
        self._markets: dict[str, MarketState] = {}
        self._active_market_slugs: tuple[str, ...] = (
            () if config.auto_select_reward_markets
            else (config.market_slug,))
        for slug in self._active_market_slugs:
            self._markets[slug] = MarketState(slug)
        self._market_selection: RewardMarketSelection | None = None
        self._market_selection_observed_at: datetime | None = None
        self.balance: AccountBalance | None = None
        self.started_at: datetime | None = None
        self.cutoff_at: datetime | None = None
        self.risk_guard: SessionRiskGuard | None = None
        self.reconciler = DesiredStateReconciler(ReconcilerPolicy(
            quote_size=config.quote_size,
            max_inventory_age_seconds=config.max_inventory_age_seconds,
            max_market_worst_loss=float(config.max_market_loss_usd),
            max_portfolio_worst_loss=float(config.max_session_loss_usd),
            max_exit_loss=float(config.max_market_loss_usd),
            exit_depth_haircut=.5,
            max_book_age_seconds=5,
            cancel_confirm_grace_seconds=5,
            max_hard_exit_attempts=config.max_hard_exit_attempts,
            max_hard_exit_retry_seconds=config.max_hard_exit_retry_seconds,
            hard_exit_confirmation_timeout_seconds=(
                config.hard_exit_confirmation_timeout_seconds),
            minimum_entry_spread=float(config.minimum_entry_spread),
            spread_hysteresis=float(config.spread_hysteresis),
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
        self._orders_before = 0
        self._runtime_error_counts: dict[str, int] = {}
        self._selection_scan_seconds_last: float | None = None
        self._rotation_transition_seconds_last: float | None = None

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

    def _identified_recovery_market(self) -> str | None:
        """Return the sole ledger market that may still require retirement."""

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
        if len(slugs) > 1:
            raise CommissioningRefused(
                "automatic recovery found state in more than one market")
        return next(iter(slugs), None)

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
                self._bind_markets((selection.selected.market.slug,))
                self._runtime_error_counts.pop("startup_market_wait", None)
                return self.market_slug
            self._market_selection = selection
            self._market_selection_observed_at = self._now()
            if self._stop_requested:
                raise CommissioningRefused(
                    "operator stopped while waiting for a viable reward market")
            await self._report_runtime_error(
                "startup_market_wait",
                AdapterError(
                    "no economically viable pregame reward market yet; "
                    f"scanned {selection.executable_catalog_markets} executable "
                    f"catalog markets and {selection.books_evaluated} books"),
                attempt)
            await self.sleep(
                float(self.config.market_selection_refresh_seconds))

    async def _refresh_contracts(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        state = self.state_for(slug or self.market_slug)
        slug = state.slug
        state.market = await self.gateway.fetch_market(slug)
        incentives = await self.gateway.fetch_incentives([slug])
        state.incentive = incentives.get(slug)
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
        if period is None or period.period == "live":
            raise CommissioningUnavailable(
                f"{slug} has no applicable pregame liquidity period")

    async def _refresh_book(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        state = self.state_for(slug or self.market_slug)
        state.book = await self.gateway.fetch_book(state.slug)
        # Stamp receipt, not request start. REST and limiter latency can be a
        # material fraction of the five-second decision freshness budget.
        state.book_observed_at = self._now()
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
        state.book = book
        state.book_observed_at = self._now()

    async def _refresh_earnings(self, now: datetime) -> None:
        """Persist authenticated venue earnings without treating them as P&L."""

        authorized = set(self.active_slugs)
        if not authorized:
            self._earnings_by_status = {}
            self._earnings_observed_at = now
            return
        totals: dict[str, Decimal] = {}
        collected: list[Any] = []
        for slug in sorted(authorized):
            fetched = await self.gateway.fetch_earnings(
                market_slug=slug,
                program_type="liquidityProgram",
            )
            # Keep an exact local boundary even if a venue deployment silently
            # ignores query filters. Shared-account historical rewards must
            # never be presented or persisted as this session's earnings.
            collected.extend(
                row for row in fetched
                if row.market_slug == slug
                and row.program_type == "liquidityProgram")
        rows = tuple(collected)
        self.ledger.record_reward_earnings(rows, observed_at=now)
        for row in rows:
            status = str(row.status).upper()
            totals[status] = totals.get(status, Decimal("0")) + Decimal(
                str(row.reward))
        self._earnings_by_status = totals
        self._earnings_observed_at = now

    async def _rest_reconcile(self, now: datetime) -> None:
        # First ask for terminal state on every bot-owned working order. A
        # missing order in an open-order snapshot is not cancellation proof.
        exchange_ids = {
            row["exchange_order_id"]
            for row in tuple(self.ledger.working_orders(owned_only=True))
            if row["exchange_order_id"]
        }
        exchange_ids.update(
            row["exchange_order_id"] for row in self.ledger.unresolved_intents()
            if row["exchange_order_id"])
        for exchange_id in sorted(exchange_ids):
            await self.executor.reconcile_order(
                exchange_id, observed_at=now)
        orders = await self.account.fetch_open_orders(observed_at=now)
        positions = await self.account.fetch_positions(observed_at=now)
        stamp = now.isoformat()
        self.ledger.apply_order_snapshot(
            f"commissioning-rest-orders:{stamp}", orders,
            as_of=now, observed_at=now, complete=True)
        self.ledger.apply_position_snapshot(
            f"commissioning-rest-positions:{stamp}", positions,
            as_of=now, observed_at=now, complete_account=True)
        self.gate.on_rest_reconciled(now)

    async def _observe_private(self, event: Any, now: datetime) -> None:
        self.gate.observe(event, received_at=now)
        self.projector.apply(event, received_at=now)

    async def _wait_for_initial_private_state(self, timeout_seconds: float = 15) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            event = await self.private_stream.next_event(timeout=remaining)
            now = self._now()
            await self._observe_private(event, now)
            if self.gate.ready(self.ledger, now=now):
                return
        raise CommissioningUnavailable(
            "private stream did not become reconciled and fresh")

    def _startup_recovery_is_scoped(self, blockers: tuple[str, ...]) -> bool:
        """Allow restart only to retire this bot's identified one-market risk."""

        allowed = {
            "shared account has open orders",
            "shared account has nonzero positions",
            "execution ledger has unresolved intents",
        }
        if not blockers or not set(blockers).issubset(allowed):
            return False
        working = self.ledger.working_orders()
        if any(str(row["client_order_id"]).startswith("venue:")
               or row["market_slug"] != self.market_slug for row in working):
            return False
        nonzero = self.ledger.query(
            "SELECT market_slug FROM execution_positions "
            "WHERE CAST(net_qty AS REAL)!=0")
        if any(row["market_slug"] != self.market_slug for row in nonzero):
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
        if any(row["market_slug"] != self.market_slug
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
        while True:
            attempt += 1
            try:
                mode = await self.preflight()
                self._runtime_error_counts.pop("startup_unavailable", None)
                return mode
            except CommissioningUnavailable as exc:
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
        had_prior_place = bool(self.ledger.query(
            "SELECT 1 FROM execution_intents WHERE operation='PLACE' LIMIT 1"))
        self.config.assert_authorized(now=now)
        self.cutoff_at = None
        if self.config.auto_select_reward_markets:
            recovery_slug = self._identified_recovery_market()
            if recovery_slug is not None:
                self._bind_markets((recovery_slug,))
            else:
                await self._select_reward_market(now)
                now = self._now()
        await self._refresh_contracts(now)
        await self._refresh_book(now)
        # A singleton private subscription makes the documented position
        # update (which omits marketSlug) attributable without guessing. Whole-
        # account REST reconciliation remains the authoritative safety check.
        await self.private_stream.start(list(self.active_slugs))
        self.gate.on_connect()
        await self.market_stream.start(list(self.active_slugs))
        if self.ledger.working_orders(owned_only=True) or self.ledger.unresolved_intents():
            await self._rest_reconcile(now)
        await self._refresh_earnings(now)
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
        self.gate.on_rest_reconciled(now)
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
        self.driver = ControlledActionDriver(
            self.executor, self.ledger, self.risk_guard)
        self._orders_before = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        if recovery_only or had_prior_place:
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
        delivery = await self.notifier.send_async(
            "🟠 Liquidity Bot LIVE commissioning started: one market, "
            "one contract per quote, reward-versus-exit economics enforced, "
            "one taker exit ends the market, $5 soft stop, $15 hard stop, "
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
        self._accrue_score(now)
        state = self.state_for(slug or self.market_slug)
        state.clear_economics()
        if state.market is None or state.book is None or state.incentive is None:
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
            return
        if period is None or period.period == "live" or state.market.tick_size is None:
            return
        eligible: set[str] = set()
        working_orders = self._working_orders(state.slug)
        working_sides = {order.side for order in working_orders}
        resting_price_by_side = {
            side: self._furthest_resting_price(working_orders, side)
            for side in ("bid", "offer")}
        spread = state.book.best_offer.price - state.book.best_bid.price
        for side, price in (("bid", state.book.best_bid.price),
                            ("offer", state.book.best_offer.price)):
            already_resting = side in working_sides
            result = evaluate_order_score(
                side=side, price=price, size=float(self.config.quote_size),
                levels=state.book.levels(side), incentive=period,
                tick_size=state.market.tick_size,
                order_already_resting=already_resting)
            if result.eligible:
                eligible.add(side)
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
            spread_threshold = float(self.config.minimum_entry_spread)
            if already_resting:
                spread_threshold -= float(self.config.spread_hysteresis)
            if spread + 1e-9 < spread_threshold:
                state.economic_blocker_by_side[side] = (
                    "spread_below_resting_minimum"
                    if already_resting
                    else "spread_below_entry_minimum")
                continue
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
            if economics.should_quote:
                state.expected_net_by_side[side] = economics.expected_net_value
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
        state.reward_eligible = frozenset(eligible)

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
            state_known=(self.gate.ready(self.ledger, now=now)
                         and self.ledger.risk_state_known()),
            reward_eligible=eligible,
            expected_net_by_side=state.expected_net_by_side,
            existing_portfolio_worst_loss=0,
            position_opened_at=basis.opened_at if basis else None,
            entry_price=float(basis.average_entry_price) if basis else None,
        )

    async def _refresh_balance(self, now: datetime) -> None:
        if self.risk_guard is None:
            raise RuntimeError("risk guard is not initialized")
        self.balance = await self.account.fetch_balance(observed_at=now)
        self.risk_guard.observe_equity(
            self.balance.marked_equity, observed_at=now)
        change = self.balance.marked_equity - self.risk_guard.starting_equity
        self.risk_guard.observe_market_realized({
            self.market_slug: min(change, Decimal("0"))})

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
        # risk disabled. A confirmed loss, clock failure, or explicit critical
        # condition still enters terminal flatten-and-stop handling.
        transient = {
            "marked account equity is unknown",
            "marked account equity is stale",
        }
        terminal = tuple(item for item in blockers if item not in transient)
        if terminal:
            return "risk_stop:" + ",".join(terminal)
        return None

    def _oldest_unresolved_age_seconds(self, now: datetime) -> float | None:
        rows = self.ledger.unresolved_intents()
        if not rows:
            return None
        oldest = min(datetime.fromisoformat(row["updated_at"]) for row in rows)
        return (now - oldest).total_seconds()

    def _quiet_book_needs_rest_refresh(self, now: datetime) -> bool:
        if self.book_observed_at is None:
            return True
        age = (now - self.book_observed_at).total_seconds()
        return age < 0 or age >= 3

    async def _refresh_quiet_book_if_due(
        self, next_refresh: datetime, slug: str | None = None,
    ) -> datetime:
        # Read time inside this boundary, after every upstream awaited call.
        state = self.state_for(slug or self.market_slug)
        now = self._now()
        if now < next_refresh:
            return next_refresh
        if state.book_is_quiet(now, max_age_seconds=3):
            await self._refresh_book(now, state.slug)
        # Anchor the next check to the actual book receipt. If a due check was
        # skipped because a WebSocket book arrived 2.9 seconds ago, scheduling
        # three seconds from *now* would cross the five-second stale limit.
        anchor = state.book_observed_at or self._now()
        return anchor + timedelta(seconds=3)

    async def _decide_for_market(
        self, slug: str, now: datetime,
    ) -> tuple[str | None, float]:
        """Plan and apply at most one mutation for one market.

        Returns ``(stop_reason, backoff_seconds)``. A stop reason is terminal
        for the whole session; a backoff is this market asking the sweep to
        pause. Recoverable conditions are handled here so one market's stale
        book or pending exit cannot stall the other seven.

        The clock is read per market rather than once per sweep: issuing eight
        mutations through a 4 requests/second governor takes real time, and a
        market whose book aged meanwhile must report ``book_stale`` and be
        skipped rather than quote on data that is no longer fresh.
        """

        state = self.state_for(slug)
        try:
            actions = self.reconciler.plan(self._frame(now, slug))
        except Exception as exc:
            return await self._handle_strategy_plan_failure(exc), 0.0
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
                        await self._report_runtime_error(
                            "hard_exit_confirmation_terminal",
                            RuntimeInvariantViolation(
                                "hard_exit_confirmation_timeout"))
                        return "critical:hard_exit_confirmation_timeout", 0.0
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
                        await self._report_runtime_error(
                            "hard_exit_confirmation_terminal",
                            RuntimeInvariantViolation(
                                "hard_exit_confirmation_timeout"))
                        return "critical:hard_exit_confirmation_timeout", 0.0
                    return None, self._recovery_delay(
                        f"hard_exit_confirmation:{slug}")
            stop = await self._handle_strategy_critical(reason, slug)
            if stop:
                return stop, 0.0
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
        try:
            await self.driver.apply_one(
                actions, market=state.market,
                position=self.ledger.position(slug), now=now)
        except Exception as exc:
            stop = await self._handle_action_failure(exc, slug)
            if stop:
                return stop, 0.0
            return None, self._recovery_delay(f"action_driver:{slug}")
        return None, 0.0

    async def _service_market_state(self, slug: str, now: datetime) -> None:
        """Bring one market's contracts, book and economics up to date.

        Each market carries its own REST cadence. A shared schedule would let a
        single quiet book consume the governor on behalf of all of them, and a
        single failing market would reset everyone else's timers.
        """

        state = self.state_for(slug)
        if state.next_contract_refresh is None or now >= state.next_contract_refresh:
            try:
                await self._refresh_contracts(now, slug)
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
            self._runtime_error_counts.pop(
                f"quiet_book_rest_refresh:{slug}", None)
        except Exception as exc:
            await self._report_runtime_error(
                f"quiet_book_rest_refresh:{slug}", exc)
            state.next_book_refresh = self._now() + timedelta(seconds=3)
        self._evaluate_rewards(self._now(), slug)

    async def _send_summary(self, now: datetime, *, critical: bool = False,
                            problems: tuple[str, ...] = ()) -> None:
        position = self.ledger.position(self.market_slug)
        basis = self.ledger.inventory_basis(self.market_slug)
        working = self.ledger.working_orders(self.market_slug)
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
        market_age = ((now - self.book_observed_at).total_seconds()
                      if self.book_observed_at else None)
        private_age = ((now - self.gate.last_message_at).total_seconds()
                       if self.gate.last_message_at else None)
        payload = build_liquidity_summary(LiquiditySummary(
            mode="LIVE",
            readiness_status="COMMISSIONING_REWARD_ECONOMICS_ENFORCED",
            observed_at=now,
            session_started_at=self.started_at or now,
            active_markets=1,
            reward_eligible_sides=len(self._reward_eligible),
            score_seconds=self._score_seconds,
            entry_fills=entry_fills,
            exit_samples=exit_fills,
            exit_completion_rate=(
                1.0 if position == 0 and entry_fills
                else min(exit_fills / entry_fills, 1.0) if entry_fills else 0.0),
            open_positions=int(position != 0),
            working_orders=len(working),
            max_inventory_age_seconds=(
                max(0, (now - basis.opened_at).total_seconds()) if basis else 0),
            unresolved_intents=len(unresolved),
            unknown_orders=unknown,
            trading_realized_usd=equity_change,
            open_marked_usd=Decimal("0"),
            session_loss_limit_usd=self.config.max_session_loss_usd,
            hard_stop_loss_usd=self.config.hard_stop_loss_usd,
            pnl_label="marked account equity Δ",
            rest_requests_per_second=float(self.config.rest_requests_per_second),
            market_stream_age_seconds=market_age,
            private_stream_age_seconds=private_age,
            integrity_problems=problems,
        ))
        # Expose actual create count only in the description, never imply that
        # shadow observations were venue orders.
        economically_allowed = sorted(self._expected_net_by_side)
        maximum_remaining = max(
            (item.maximum_remaining_reward
             for item in self._reward_projection_by_side.values()),
            default=0.0,
        )
        paid_observed = self._earnings_by_status.get("PAID", Decimal("0"))
        pending_observed = self._earnings_by_status.get("PENDING", Decimal("0"))
        maximum_fill_probability = max(
            self._fill_probability_by_side.values(), default=0.0)
        fill_driver = "none"
        if self._fill_probability_by_side:
            riskiest_side = max(
                self._fill_probability_by_side,
                key=self._fill_probability_by_side.__getitem__)
            fill_driver = self._fill_risk_driver_by_side.get(
                riskiest_side, "unknown")
        payload["embeds"][0]["footer"]["text"] += (
            f" • session creates {place_count - self._orders_before}"
            f" • economic sides {','.join(economically_allowed) or 'none'}"
            f" • max remaining reward ${maximum_remaining:.4f}"
            f" • 30s fill risk {maximum_fill_probability:.2%} ({fill_driver})"
            f" • current-market venue earnings paid ${paid_observed:.2f}"
            f" pending ${pending_observed:.2f}"
            f" • venue aggregate payout floor "
            f"${self.config.aggregate_reward_payout_floor_usd:.2f} reporting-only")
        if self._market_selection is not None:
            payload["embeds"][0]["footer"]["text"] += (
                f" • catalog {self._market_selection.catalog_markets}"
                f" • executable {self._market_selection.executable_catalog_markets}"
                f" • rewarded {self._market_selection.rewarded_executable_markets}"
                f" • unsupported {self._market_selection.unsupported_programs}"
                f" • books ranked {self._market_selection.books_evaluated}"
                f" • catalog tight skipped "
                f"{self._market_selection.catalog_spread_filtered_markets}"
                f" • viable markets {self._market_selection.viable_markets}")
        if self._selection_scan_seconds_last is not None:
            payload["embeds"][0]["footer"]["text"] += (
                f" • selection scan {self._selection_scan_seconds_last:.1f}s")
        if self._rotation_transition_seconds_last is not None:
            payload["embeds"][0]["footer"]["text"] += (
                " • last flat rotation transition "
                f"{self._rotation_transition_seconds_last:.1f}s")
        await self.notifier.send_payload_async(payload, critical=critical)

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
        if attempt <= 2 or (attempt & (attempt - 1)) == 0:
            await self.notifier.send_async(
                "⚠️ Liquidity Bot runtime recovery: "
                f"{component} attempt {attempt}, {type(exc).__name__}: "
                f"{detail[:400]}. "
                "New risk is disabled until reconnect and reconciliation pass.",
                critical=True,
            )

    async def _quarantine_working_orders(self) -> None:
        """Cancel known quotes and prove their terminal state before resume."""

        now = self._now()
        await self._rest_reconcile(now)
        self._assert_runtime_scope()
        # Retire quotes in every market that could hold them, not just the
        # primary. Resuming with one market's order still working would let it
        # fill unobserved while the others rebind.
        # Nothing bound and nothing in the ledger means nothing to retire.
        slugs = self._shutdown_slugs()
        for slug in slugs:
            if self.ledger.market_has_unresolved_intent(slug):
                raise AdapterError(
                    f"runtime recovery has unresolved order intents in {slug}")
            if self.ledger.working_orders(slug):
                await self.kill_switch.initiate(slug, now=self._now())
        await self._rest_reconcile(self._now())
        self._assert_runtime_scope()
        for slug in slugs:
            if self.ledger.market_has_unresolved_intent(slug):
                raise AdapterError(
                    f"runtime recovery cancel is not yet reconciled in {slug}")
            if self.ledger.working_orders(slug):
                raise AdapterError(
                    f"runtime recovery order is still working in {slug}")

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

    async def _open_streams_for_active_market(self) -> None:
        """Bind fresh public/private streams and re-prove account state."""

        private = self.private_stream_factory(self.client)
        market = self.market_stream_factory(self.client)
        self.private_stream = private
        self.market_stream = market
        try:
            await private.start(list(self.active_slugs))
            self.gate.on_connect()
            await market.start(list(self.active_slugs))
            await self._rest_reconcile(self._now())
            await self._wait_for_initial_private_state()
        except Exception:
            self.gate.on_disconnect()
            await private.close()
            await market.close()
            raise

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
        chosen = tuple(
            item.market.slug
            for item in selection.top_viable(self.config.max_markets))
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
                await self._quarantine_working_orders()
                stream = self.private_stream_factory(self.client)
                self.private_stream = stream
                await stream.start(list(self.active_slugs))
                self.gate.on_connect()
                await self._rest_reconcile(self._now())
                await self._wait_for_initial_private_state()
                await self.notifier.send_async(
                    "✅ Liquidity Bot private stream recovered; REST and fresh "
                    "private state passed. Quoting may resume.")
                self._runtime_error_counts.pop("private_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "private_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                self.gate.on_disconnect()
                exc = recovery_exc
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    async def _recover_market_stream(self, initial: Exception) -> str | None:
        """Reconnect market data after canceling quotes made on the old book."""

        exc = initial
        attempt = 0
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
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

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
            await self._quarantine_working_orders()
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

    async def _handle_strategy_critical(
        self, reason: str, slug: str,
    ) -> str | None:
        """Recover only explicitly enumerated transient strategy conditions."""

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
        if not isinstance(exc, (AdapterError, OrderOutcomeUncertain)):
            return self._terminal_exception_reason("action_driver", exc)
        try:
            await self._quarantine_working_orders()
        except RuntimeInvariantViolation as invariant:
            await self._report_runtime_error(
                "action_driver_terminal", invariant)
            return f"critical:{invariant.code}"
        except Exception as recovery_exc:
            await self._report_runtime_error(
                f"action_driver_recovery:{slug}", recovery_exc)
        return None

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
                for market_slug in self._shutdown_slugs():
                    position = self.ledger.position(market_slug)
                    working = self.ledger.working_orders(market_slug)
                    unresolved = self.ledger.market_has_unresolved_intent(
                        market_slug)
                    if working and not unresolved:
                        await self.kill_switch.initiate(market_slug, now=now)
                        continue
                    if position == 0 or working or unresolved:
                        continue
                    if exit_attempts >= self.config.max_shutdown_exit_attempts:
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
                    await self.driver.apply_one(
                        [action], market=state.market,
                        position=position, now=now)
                    delay = min(2 ** max(exit_attempts - 1, 0), 5)
                if self._whole_account_safe_to_stop():
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot LIVE stopped flat after {reason}.")
                    return
                shutdown_failures = 0
            except Exception as exc:
                shutdown_failures += 1
                delay = min(2 ** min(shutdown_failures - 1, 5), 30)
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
        stop_reason = "unknown"
        preflight_complete = False
        preflight_error: Exception | None = None
        try:
            startup_mode = await self.preflight_until_ready()
            preflight_complete = True
            if startup_mode == "RECOVERY_ONLY":
                self.request_stop("startup_recovery_only")
                await self.notifier.send_async(
                    "Liquidity Bot found identified one-market state from a "
                    "prior process. New risk remains disabled; recovering flat.",
                    critical=True)
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
            next_selection = (
                now + timedelta(
                    seconds=self.config.market_selection_refresh_seconds)
                if self.config.auto_select_reward_markets
                else datetime.max.replace(tzinfo=timezone.utc))
            market_task = asyncio.create_task(self.market_stream.next_book())
            private_task = asyncio.create_task(self.private_stream.next_event())
            while True:
                now = self._now()
                stop_reason = self._stop_reason(now) or ""
                if stop_reason:
                    break
                done, _ = await asyncio.wait(
                    {market_task, private_task}, timeout=.5,
                    return_when=asyncio.FIRST_COMPLETED)
                if market_task in done:
                    try:
                        self._accept_book(market_task.result())
                    except Exception as exc:
                        stop_reason = await self._recover_market_stream(exc) or ""
                        if stop_reason:
                            break
                    market_task = asyncio.create_task(
                        self.market_stream.next_book())
                if private_task in done:
                    try:
                        await self._observe_private(
                            private_task.result(), self._now())
                    except Exception as exc:
                        stop_reason = await self._recover_private_stream(exc) or ""
                        if stop_reason:
                            break
                    private_task = asyncio.create_task(
                        self.private_stream.next_event())
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
                        await self._rest_reconcile(now)
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
                # Service every bound market on its own cadence. Keep this
                # last among periodic REST work so account latency cannot age a
                # book before the decisions below.
                try:
                    for slug in self.active_slugs:
                        await self._service_market_state(slug, self._now())
                except RuntimeInvariantViolation as invariant:
                    stop_reason = await self._handle_strategy_plan_failure(
                        invariant)
                    break
                now = self._now()
                stop_reason = self._stop_reason(now) or ""
                if stop_reason:
                    break
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
                        try:
                            await self._rest_reconcile(now)
                        except Exception:
                            # Ambiguous state remains quarantined. Equity and
                            # stream freshness independently force shutdown if
                            # this cannot be reconciled promptly.
                            pass
                        next_reconcile = now + timedelta(seconds=2)
                    now = self._now()
                    unresolved_age = self._oldest_unresolved_age_seconds(now)
                    if unresolved_age is not None:
                        if (unresolved_age > 10 and self.risk_guard is not None):
                            self.risk_guard.trip_critical(
                                "order mutation unresolved for more than 10 seconds")
                        if now >= next_summary:
                            await self._send_summary(now)
                            next_summary = now + timedelta(minutes=5)
                    # A reconciliation pass may consume most of the book-age
                    # budget. Re-enter the loop so book/state freshness is
                    # rechecked before any new decision, even if it resolved.
                    continue
                sweep_delay = 0.0
                for slug in self.active_slugs:
                    outcome, delay = await self._decide_for_market(
                        slug, self._now())
                    if outcome:
                        stop_reason = outcome
                        break
                    sweep_delay = max(sweep_delay, delay)
                if stop_reason:
                    break
                if sweep_delay:
                    await self.sleep(sweep_delay)
                if now >= next_summary:
                    await self._send_summary(now)
                    next_summary = now + timedelta(minutes=5)
        except asyncio.CancelledError:
            stop_reason = "process_cancelled"
        except Exception as exc:
            stop_reason = f"runner_failure:{type(exc).__name__}"
            await self.notifier.send_async(
                f"🚨 Liquidity Bot LIVE runner failure: {type(exc).__name__}.",
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
            (self.ledger.position(self.market_slug) == 0
             and self._shutdown_proved_flat),
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
        description="One-contract Polymarket liquidity commissioning runner")
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
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
    except (CommissioningRefused, AdapterError) as exc:
        parser.exit(2, f"commissioning refused: {exc}\n")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
