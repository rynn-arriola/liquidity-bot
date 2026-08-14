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

RECOVERABLE_STRATEGY_CRITICALS = frozenset({
    "book_stale",
    "execution_state_unknown",
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
        self._active_market_slug: str | None = (
            None if config.auto_select_reward_markets else config.market_slug)
        self._market_selection: RewardMarketSelection | None = None
        self._market_selection_observed_at: datetime | None = None
        self.market: MarketMetadata | None = None
        self.incentive: MarketIncentive | None = None
        self.book: BookSnapshot | None = None
        self.book_observed_at: datetime | None = None
        self.balance: AccountBalance | None = None
        self.started_at: datetime | None = None
        self.cutoff_at: datetime | None = None
        self.risk_guard: SessionRiskGuard | None = None
        self.reconciler = DesiredStateReconciler(ReconcilerPolicy(
            quote_size=Decimal("1"),
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
        self._reward_eligible: frozenset[str] = frozenset()
        self._score_by_side: dict[str, float] = {}
        self._expected_net_by_side: dict[str, float] = {}
        self._reward_projection_by_side: dict[str, RewardProjection] = {}
        self._economic_blocker_by_side: dict[str, str] = {}
        self._fill_probability_by_side: dict[str, float] = {}
        self._fill_risk_driver_by_side: dict[str, str] = {}
        self._score_seconds = 0.0
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
    def market_slug(self) -> str:
        slug = str(self._active_market_slug or "").strip()
        if not slug or slug == AUTO_REWARD_MARKET:
            raise RuntimeError("an active reward market has not been selected")
        return slug

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
                self._active_market_slug = selection.selected.market.slug
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

    async def _refresh_contracts(self, now: datetime) -> None:
        slug = self.market_slug
        self.market = await self.gateway.fetch_market(slug)
        incentives = await self.gateway.fetch_incentives([slug])
        self.incentive = incentives.get(slug)
        if self.incentive is None:
            raise CommissioningRefused(
                "selected market has no liquidityProgram incentive")
        if (not self.market.active or self.market.closed
                or self.market.status not in OPEN_MARKET_STATUSES):
            raise CommissioningRefused("selected market is not open")
        if self.market.game_start is None:
            raise CommissioningRefused("market start clock is unavailable")
        if self.market.game_start <= now:
            raise CommissioningRefused("selected market is no longer pregame")
        try:
            period = select_applicable_period(
                self.incentive.periods, self.market.game_start, now)
        except AmbiguousIncentiveError as exc:
            raise CommissioningRefused(
                "selected market has ambiguous incentive periods") from exc
        if period is None or period.period == "live":
            raise CommissioningRefused(
                "selected market has no applicable pregame liquidity period")

    async def _refresh_book(self, now: datetime) -> None:
        self.book = await self.gateway.fetch_book(self.market_slug)
        # Stamp receipt, not request start. REST and limiter latency can be a
        # material fraction of the five-second decision freshness budget.
        self.book_observed_at = self._now()
        if self.market is None:
            raise RuntimeError("market metadata is unavailable")
        # Validate both possible one-contract passive wire payloads before the
        # first private subscription can authorize mutation.
        build_order_payload(OrderRequest(
            self.market.slug, "bid", Decimal(str(self.book.best_bid.price)),
            Decimal("1")), self.market)
        build_order_payload(OrderRequest(
            self.market.slug, "offer", Decimal(str(self.book.best_offer.price)),
            Decimal("1")), self.market)

    async def _refresh_earnings(self, now: datetime) -> None:
        """Persist authenticated venue earnings without treating them as P&L."""

        fetched = await self.gateway.fetch_earnings(
            market_slug=self.market_slug,
            program_type="liquidityProgram",
        )
        # Keep an exact local boundary even if a venue deployment silently
        # ignores query filters. Shared-account historical rewards must never
        # be presented or persisted as this market's earnings.
        rows = tuple(
            row for row in fetched
            if row.market_slug == self.market_slug
            and row.program_type == "liquidityProgram")
        self.ledger.record_reward_earnings(rows, observed_at=now)
        totals: dict[str, Decimal] = {}
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
        raise CommissioningRefused(
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
                self._active_market_slug = recovery_slug
            else:
                await self._select_reward_market(now)
                now = self._now()
        await self._refresh_contracts(now)
        await self._refresh_book(now)
        # A singleton private subscription makes the documented position
        # update (which omits marketSlug) attributable without guessing. Whole-
        # account REST reconciliation remains the authoritative safety check.
        await self.private_stream.start([self.market_slug])
        self.gate.on_connect()
        await self.market_stream.start([self.market_slug])
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
        if self._score_observed_at is not None:
            seconds = (now - self._score_observed_at).total_seconds()
            if seconds < 0:
                raise RuntimeError("commissioning score clock moved backwards")
            self._score_seconds += seconds * sum(self._score_by_side.values())
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

        if (resting_price is None or self.book is None
                or self.market is None or self.market.tick_size is None):
            return touch_score
        if abs(resting_price - touch_price) < 1e-9:
            return touch_score
        drifted = evaluate_order_score(
            side=side, price=resting_price, size=1,
            levels=self.book.levels(side), incentive=period,
            tick_size=self.market.tick_size,
            order_already_resting=True)
        return drifted.score if drifted.eligible else 0.0

    def _evaluate_rewards(self, now: datetime) -> None:
        self._accrue_score(now)
        self._reward_eligible = frozenset()
        self._score_by_side = {}
        self._expected_net_by_side = {}
        self._reward_projection_by_side = {}
        self._economic_blocker_by_side = {}
        self._fill_probability_by_side = {}
        self._fill_risk_driver_by_side = {}
        if self.market is None or self.book is None or self.incentive is None:
            return
        if not self.market.is_supported_moneyline:
            self._economic_blocker_by_side = {
                "bid": "unsupported_market_structure",
                "offer": "unsupported_market_structure",
            }
            return
        try:
            period = select_applicable_period(
                self.incentive.periods, self.market.game_start, now)
        except AmbiguousIncentiveError:
            return
        if period is None or period.period == "live" or self.market.tick_size is None:
            return
        eligible: set[str] = set()
        working_orders = self._working_orders()
        working_sides = {order.side for order in working_orders}
        resting_price_by_side = {
            side: self._furthest_resting_price(working_orders, side)
            for side in ("bid", "offer")}
        spread = self.book.best_offer.price - self.book.best_bid.price
        for side, price in (("bid", self.book.best_bid.price),
                            ("offer", self.book.best_offer.price)):
            already_resting = side in working_sides
            result = evaluate_order_score(
                side=side, price=price, size=1,
                levels=self.book.levels(side), incentive=period,
                tick_size=self.market.tick_size,
                order_already_resting=already_resting)
            if result.eligible:
                eligible.add(side)
            projection = project_order_reward(
                side=side,
                price=price,
                size=1,
                levels=self.book.levels(side),
                incentive=period,
                tick_size=self.market.tick_size,
                event_start=self.market.game_start,
                now=now,
                horizon_seconds=self.config.reward_projection_horizon_seconds,
                haircut=float(self.config.reward_projection_haircut),
                # The venue's $1 threshold is aggregate settlement policy, not
                # a per-market or per-order eligibility threshold.
                minimum_payout=0.0,
                order_already_resting=already_resting,
            )
            self._reward_projection_by_side[side] = projection
            queue_ahead = (
                self.book.best_bid.size
                if side == "bid"
                else self.book.best_offer.size)
            seconds_to_event = max(
                0.0, (self.market.game_start - now).total_seconds())
            fill_hazard = self.fill_hazard_model.estimate(
                slug=self.market.slug,
                side=side,
                price=price,
                spread=spread,
                queue_ahead=queue_ahead,
                seconds_to_event=seconds_to_event,
                horizon_seconds=(
                    self.config.reward_projection_horizon_seconds),
            )
            self._fill_probability_by_side[side] = fill_hazard.probability
            self._fill_risk_driver_by_side[side] = fill_hazard.driver
            spread_threshold = float(self.config.minimum_entry_spread)
            if already_resting:
                spread_threshold -= float(self.config.spread_hysteresis)
            if spread + 1e-9 < spread_threshold:
                self._economic_blocker_by_side[side] = (
                    "spread_below_resting_minimum"
                    if already_resting
                    else "spread_below_entry_minimum")
                continue
            if not projection.eligible:
                self._economic_blocker_by_side[side] = projection.reason
                continue
            exit_result = cross_exit(
                self.book,
                entry_side=side,
                entry_price=price,
                qty=1,
                depth_haircut=.5,
                # If this quote fills, liquidity ahead at its entry level was
                # consumed. Never count that same level as guaranteed exit
                # depth, even while our order is already visible in the book.
                exclude_entry_level=True,
            )
            if not exit_result.complete:
                self._economic_blocker_by_side[side] = (
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
                self._expected_net_by_side[side] = economics.expected_net_value
                # Live score-seconds represent actual attributable resting
                # exposure, not a hypothetical candidate. A newly authorized
                # side begins accruing only after its owned order is observed
                # working; a newly blocked side stops at this evaluation.
                if already_resting:
                    self._score_by_side[side] = self._resting_score(
                        side=side,
                        period=period,
                        touch_price=price,
                        touch_score=result.score,
                        resting_price=resting_price_by_side[side])
            else:
                self._economic_blocker_by_side[side] = (
                    "projected_reward_does_not_cover_probability_"
                    "weighted_exit_cost")
        self._reward_eligible = frozenset(eligible)

    def _working_orders(self) -> tuple[WorkingOrder, ...]:
        result: list[WorkingOrder] = []
        side_by_intent = {
            "ORDER_INTENT_BUY_LONG": "bid",
            "ORDER_INTENT_SELL_SHORT": "bid",
            "ORDER_INTENT_BUY_SHORT": "offer",
            "ORDER_INTENT_SELL_LONG": "offer",
        }
        for row in self.ledger.working_orders(self.market_slug):
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
        """Reject ownership or market identity drift before any mutation."""

        slug = self.market_slug
        working = self.ledger.working_orders()
        if any(str(row["client_order_id"]).startswith("venue:")
               for row in working):
            raise RuntimeInvariantViolation(
                "unowned_working_order",
                "runtime account contains an unowned working order")
        if any(row["market_slug"] != slug for row in working):
            raise RuntimeInvariantViolation(
                "cross_market_working_order",
                "runtime account contains a working order in another market")
        cross_positions = self.ledger.query(
            "SELECT market_slug FROM execution_positions "
            "WHERE market_slug!=? AND CAST(net_qty AS REAL)!=0", (slug,))
        if cross_positions:
            raise RuntimeInvariantViolation(
                "cross_market_position",
                "runtime account contains a position in another market")
        if any(row["market_slug"] != slug
               for row in self.ledger.unresolved_intents()):
            raise RuntimeInvariantViolation(
                "cross_market_unresolved_intent",
                "execution ledger contains an unresolved intent in another market")

    def _frame(self, now: datetime) -> MarketFrame:
        self._assert_runtime_scope()
        if self.market is None or self.book is None or self.book_observed_at is None:
            raise RuntimeError("market frame is incomplete")
        position = self.ledger.position(self.market.slug)
        basis = self.ledger.inventory_basis(self.market.slug)
        if position != 0 and basis is None:
            return MarketFrame(
                self.market, now, self.book, self.book_observed_at, position,
                self._working_orders(), False, self._reward_eligible, {}, 0)
        eligible = self._reward_eligible
        if (position == 0 and self.risk_guard is not None
                and not self.risk_guard.can_add_new_risk(now=now)):
            # A stale/unknown marked balance is recoverable, but existing flat-
            # state quotes would still add risk if filled. Make them undesired
            # so the reconciler cancels them while the process stays alive.
            eligible = frozenset()
        return MarketFrame(
            market=self.market,
            now=now,
            book=self.book,
            book_observed_at=self.book_observed_at,
            position=position,
            working_orders=self._working_orders(),
            state_known=(self.gate.ready(self.ledger, now=now)
                         and self.ledger.risk_state_known()),
            reward_eligible=eligible,
            expected_net_by_side=self._expected_net_by_side,
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
            return "15_USD_HARD_STOP"
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
        self, next_refresh: datetime,
    ) -> datetime:
        # Read time inside this boundary, after every upstream awaited call.
        now = self._now()
        if now < next_refresh:
            return next_refresh
        if self._quiet_book_needs_rest_refresh(now):
            await self._refresh_book(now)
        # Anchor the next check to the actual book receipt. If a due check was
        # skipped because a WebSocket book arrived 2.9 seconds ago, scheduling
        # three seconds from *now* would cross the five-second stale limit.
        anchor = self.book_observed_at or self._now()
        return anchor + timedelta(seconds=3)

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
        slug = self.market_slug
        if self.ledger.market_has_unresolved_intent(slug):
            raise AdapterError("runtime recovery has unresolved order intents")
        if self.ledger.working_orders(slug):
            await self.kill_switch.initiate(slug, now=self._now())
            await self._rest_reconcile(self._now())
            self._assert_runtime_scope()
        if self.ledger.market_has_unresolved_intent(slug):
            raise AdapterError("runtime recovery cancel is not yet reconciled")
        if self.ledger.working_orders(slug):
            raise AdapterError("runtime recovery order is still working")

    def _rotation_blockers(self) -> tuple[str, ...]:
        """Require whole-account flat, owned, resolved state before rebinding."""

        blockers: list[str] = []
        if self.ledger.working_orders():
            blockers.append("working orders remain")
        if self.ledger.unresolved_intents():
            blockers.append("order intents remain unresolved")
        if self.ledger.query(
                "SELECT 1 FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0 LIMIT 1"):
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
            await private.start([self.market_slug])
            self.gate.on_connect()
            await market.start([self.market_slug])
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
        self._score_by_side = {}
        self._reward_eligible = frozenset()
        self._expected_net_by_side = {}
        self._reward_projection_by_side = {}
        self._economic_blocker_by_side = {}
        self._fill_probability_by_side = {}
        self._fill_risk_driver_by_side = {}
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

        if self._expected_net_by_side:
            return False
        return not self.ledger.working_orders(self.market_slug)

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
        old_slug = self.market_slug
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
        selected_slug = selection.selected.market.slug
        if selected_slug == old_slug:
            await self._refresh_contracts(self._now())
            await self._refresh_book(self._now())
            await self._rest_reconcile(self._now())
            return False

        old_private = self.private_stream
        old_market = self.market_stream
        await old_private.close()
        await old_market.close()
        self.gate.on_disconnect()
        self._active_market_slug = selected_slug
        self.market = None
        self.incentive = None
        self.book = None
        self.book_observed_at = None
        self._reset_selected_market_observations(self._now())
        try:
            await self._refresh_contracts(self._now())
            await self._refresh_book(self._now())
            await self._open_streams_for_active_market()
            await self._refresh_earnings(self._now())
        except Exception as selection_error:
            # No mutation occurred after the flat proof. Restore the prior
            # singleton scope so a transient discovery/bind failure leaves the
            # process safely monitoring instead of silently streamless.
            self._active_market_slug = old_slug
            self.market = None
            self.incentive = None
            self.book = None
            self.book_observed_at = None
            try:
                await self._refresh_contracts(self._now())
                await self._refresh_book(self._now())
                await self._open_streams_for_active_market()
                await self._refresh_earnings(self._now())
            except Exception as restore_error:
                raise RuntimeInvariantViolation(
                    "market_rotation_restore_failed",
                    "automatic market rotation could not restore a safe "
                    "singleton stream scope",
                ) from restore_error
            raise AdapterError(
                "selected reward market could not be safely bound: "
                f"{type(selection_error).__name__}") from selection_error
        await self.notifier.send_async(
            "🔄 Liquidity Bot rotated while flat from "
            f"{old_slug} to rewarded market {selected_slug}. "
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
                await stream.start([self.market_slug])
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
                await stream.start([self.market_slug])
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

    async def _reconcile_pending_hard_exit(self) -> str:
        """Confirm a submitted IOC without converting latency into shutdown."""

        slug = self.market_slug
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

    async def _recover_strategy_state(self, reason: str) -> None:
        """Quarantine an unexpected strategy state without ending the process."""

        await self._report_runtime_error(
            "strategy_state", AdapterError(str(reason or "unknown")))
        try:
            if reason in {"book_stale", "book_market_mismatch"}:
                await self._refresh_book(self._now())
            await self._quarantine_working_orders()
        except RuntimeInvariantViolation:
            raise
        except Exception as exc:
            await self._report_runtime_error("strategy_state_recovery", exc)

    @staticmethod
    def _terminal_exception_reason(component: str, exc: Exception) -> str:
        if isinstance(exc, RuntimeInvariantViolation):
            return f"critical:{exc.code}"
        return f"critical:{component}_{type(exc).__name__}"

    def _recovery_delay(self, component: str, *, base: float = 1.0,
                        cap: float = 30.0) -> float:
        attempt = max(1, self._runtime_error_counts.get(component, 1))
        return min(base * (2 ** min(attempt - 1, 5)), cap)

    async def _handle_strategy_critical(self, reason: str) -> str | None:
        """Recover only explicitly enumerated transient strategy conditions."""

        if reason not in RECOVERABLE_STRATEGY_CRITICALS:
            exc = RuntimeInvariantViolation(
                reason, f"terminal strategy condition: {reason}")
            await self._report_runtime_error("strategy_state_terminal", exc)
            return f"critical:{reason}"
        try:
            await self._recover_strategy_state(reason)
        except RuntimeInvariantViolation as invariant:
            await self._report_runtime_error(
                "strategy_state_terminal", invariant)
            return f"critical:{invariant.code}"
        return None

    async def _handle_strategy_plan_failure(self, exc: Exception) -> str:
        """A pure planning exception is a code/state invariant, not transport."""

        await self._report_runtime_error("strategy_plan_terminal", exc)
        return self._terminal_exception_reason("strategy_plan", exc)

    async def _handle_action_failure(self, exc: Exception) -> str | None:
        """Retry ambiguous remote outcomes; terminate local invariant failures."""

        await self._report_runtime_error("action_driver", exc)
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
                "action_driver_recovery", recovery_exc)
        return None

    async def _shutdown(self, reason: str) -> None:
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot LIVE shutdown started: {reason}. "
            "New risk is disabled; reconciling to flat.",
            critical=(reason == "15_USD_HARD_STOP"
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
                position = self.ledger.position(slug)
                working = self.ledger.working_orders(slug)
                unresolved = self.ledger.market_has_unresolved_intent(slug)
                if working and not unresolved:
                    await self.kill_switch.initiate(slug, now=now)
                elif position != 0 and not working and not unresolved:
                    if exit_attempts >= self.config.max_shutdown_exit_attempts:
                        await alert(
                            "🚨 Liquidity Bot exhausted its shutdown exit budget. "
                            "Maker quoting remains terminal; manual intervention "
                            f"is required for remaining position {position} in "
                            f"{slug}.",
                            force=not exit_budget_announced)
                        exit_budget_announced = True
                        delay = 30.0
                    else:
                        await self._refresh_book(now)
                        if self.book is None or self.market is None:
                            raise RuntimeError("shutdown book is unavailable")
                        basis = self.ledger.inventory_basis(slug)
                        entry_side = "bid" if position > 0 else "offer"
                        conservative_entry = (
                            float(basis.average_entry_price) if basis is not None
                            else .99 if position > 0 else .01)
                        exit_result = cross_exit(
                            self.book, entry_side=entry_side,
                            entry_price=conservative_entry,
                            qty=float(abs(position)), depth_haircut=.5)
                        if (not exit_result.complete
                                or exit_result.worst_price is None):
                            raise RuntimeError(
                                "insufficient stressed depth to flatten exact "
                                "position")
                        side = "offer" if position > 0 else "bid"
                        action = PlanAction(
                            ActionKind.PLACE_IOC, slug,
                            "commissioning_shutdown_flatten", side=side,
                            price=exit_result.worst_price, qty=abs(position))
                        if self.driver is None:
                            raise RuntimeError("action driver is unavailable")
                        # Count before mutation because a transport failure
                        # after send may still have reached the venue.
                        exit_attempts += 1
                        await self.driver.apply_one(
                            [action], market=self.market,
                            position=position, now=now)
                        delay = min(2 ** max(exit_attempts - 1, 0), 5)
                status = self.kill_switch.status(slug)
                if status.safe_to_stop and self.ledger.risk_state_known():
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
            startup_mode = await self.preflight()
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
                        self.book = market_task.result()
                        self.book_observed_at = self._now()
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
                if now >= next_contract_refresh:
                    try:
                        await self._refresh_contracts(now)
                        self._runtime_error_counts.pop(
                            "market_incentive_refresh", None)
                    except Exception as exc:
                        self.incentive = None
                        self._reward_eligible = frozenset()
                        if self.config.auto_select_reward_markets:
                            next_selection = now
                        await self._report_runtime_error(
                            "market_incentive_refresh", exc)
                    next_contract_refresh = now + timedelta(minutes=1)
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
                # Keep this last among periodic REST work so account/incentive
                # latency cannot age the book before the decision below.
                try:
                    next_book_refresh = await self._refresh_quiet_book_if_due(
                        next_book_refresh)
                    self._runtime_error_counts.pop(
                        "quiet_book_rest_refresh", None)
                except Exception as exc:
                    await self._report_runtime_error(
                        "quiet_book_rest_refresh", exc)
                    next_book_refresh = self._now() + timedelta(seconds=3)
                now = self._now()
                try:
                    self._evaluate_rewards(now)
                except Exception as exc:
                    stop_reason = await self._handle_strategy_plan_failure(exc)
                    break
                stop_reason = self._stop_reason(now) or ""
                if stop_reason:
                    break
                if (self.config.auto_select_reward_markets
                        and now >= next_selection
                        and not self.ledger.unresolved_intents()
                        and self.ledger.position(self.market_slug) == 0
                        and self._discovery_is_free()):
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
                try:
                    actions = self.reconciler.plan(self._frame(now))
                except Exception as exc:
                    stop_reason = await self._handle_strategy_plan_failure(exc)
                    break
                if actions and actions[0].kind == ActionKind.CRITICAL:
                    reason = actions[0].reason
                    if reason == "hard_exit_sent_but_inventory_remains":
                        delay = 1.0
                        try:
                            outcome = await self._reconcile_pending_hard_exit()
                            print(
                                "[runtime-recovery] "
                                f"at={self._now().isoformat()} "
                                "component=hard_exit_confirmation "
                                f"outcome={outcome}",
                                flush=True,
                            )
                            if (outcome == "pending"
                                    and self.reconciler.hard_exit_confirmation_expired(
                                        self.market_slug, now=self._now())):
                                stop_reason = "critical:hard_exit_confirmation_timeout"
                                await self._report_runtime_error(
                                    "hard_exit_confirmation_terminal",
                                    RuntimeInvariantViolation(
                                        "hard_exit_confirmation_timeout"))
                                break
                            self._runtime_error_counts.pop(
                                "hard_exit_confirmation", None)
                            if outcome == "flat":
                                continue
                            attempts = self.reconciler.hard_exit_attempts(
                                self.market_slug)
                            delay = min(2 ** max(attempts - 1, 0), 5)
                        except Exception as exc:
                            await self._report_runtime_error(
                                "hard_exit_confirmation", exc)
                            if self.reconciler.hard_exit_confirmation_expired(
                                    self.market_slug, now=self._now()):
                                stop_reason = (
                                    "critical:hard_exit_confirmation_timeout")
                                await self._report_runtime_error(
                                    "hard_exit_confirmation_terminal",
                                    RuntimeInvariantViolation(
                                        "hard_exit_confirmation_timeout"))
                                break
                            delay = self._recovery_delay(
                                "hard_exit_confirmation")
                        await self.sleep(delay)
                        continue
                    stop_reason = (
                        await self._handle_strategy_critical(reason) or "")
                    if stop_reason:
                        break
                    await self.sleep(self._recovery_delay("strategy_state"))
                    continue
                self._runtime_error_counts.pop("runtime_state_gate", None)
                self._runtime_error_counts.pop("runtime_state_quarantine", None)
                self._runtime_error_counts.pop("strategy_state", None)
                self._runtime_error_counts.pop("strategy_state_recovery", None)
                self._runtime_error_counts.pop("strategy_plan", None)
                self._runtime_error_counts.pop("strategy_plan_recovery", None)
                self._runtime_error_counts.pop("action_driver", None)
                self._runtime_error_counts.pop("action_driver_recovery", None)
                if self.driver is None or self.market is None:
                    raise RuntimeError("commissioning driver is unavailable")
                try:
                    await self.driver.apply_one(
                        actions, market=self.market,
                        position=self.ledger.position(self.market.slug), now=now)
                except Exception as exc:
                    stop_reason = await self._handle_action_failure(exc) or ""
                    if stop_reason:
                        break
                    await self.sleep(self._recovery_delay("action_driver"))
                    continue
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
