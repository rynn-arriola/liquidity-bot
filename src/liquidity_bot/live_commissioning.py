"""Explicitly armed one-contract commissioning runner.

The economic evidence gate remains red. This runner exists only for the
operator-accepted experiment and is mechanically constrained to one named
rewarded market, one contract per quote, a 06:00 New York risk cutoff, a $5
normal session stop, and a $15 emergency hard stop.

Importing this module has no side effect. Exchange access is possible only
through ``main()`` with all explicit arming acknowledgements.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from polymarket_us import AsyncPolymarketUS

from .account_snapshots import AsyncAccountSnapshotGateway
from .adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from .commissioning import CommissioningConfig, CommissioningRefused
from .discord_summary import LiquiditySummary, build_liquidity_summary
from .errors import AdapterError
from .execution_ledger import ExecutionLedger
from .live_orders import (
    AsyncTradingGateway,
    LiveOrderExecutor,
    OPEN_MARKET_STATUSES,
    OrderRequest,
    build_order_payload,
)
from .live_session import (
    ControlledActionDriver,
    ControlledTrialStartup,
    MarketKillSwitch,
    PrivateStateGate,
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
from .rewards import AmbiguousIncentiveError, evaluate_order_score, select_applicable_period
from .runtime_safety import SessionRiskGuard, SingleInstanceLease
from .runtime_secrets import load_runtime_secrets
from .shadow_math import cross_exit
from .streams import MarketReadOnlyStream, PrivateReadOnlyStream
from .venue_state import PrivateLedgerProjector


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class CommissioningResult:
    started_at: datetime
    cutoff_at: datetime
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
        self.account = AsyncAccountSnapshotGateway(
            self.gateway, resolve_client_id=ledger.resolve_client_order_id)
        self.executor = LiveOrderExecutor(
            AsyncTradingGateway(self.gateway), ledger)
        self.driver: ControlledActionDriver | None = None
        self.kill_switch = MarketKillSwitch(self.executor, ledger)
        self.projector = PrivateLedgerProjector(ledger)
        self.gate = PrivateStateGate(max_silence_seconds=30)
        self.market_stream = market_stream_factory(client)
        self.private_stream = private_stream_factory(client)
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
        ))
        self._stop_requested: str | None = None
        self._reward_eligible: frozenset[str] = frozenset()
        self._score_by_side: dict[str, float] = {}
        self._score_seconds = 0.0
        self._score_observed_at: datetime | None = None
        self._orders_before = 0

    def request_stop(self, reason: str = "operator_requested") -> None:
        self._stop_requested = str(reason or "operator_requested")

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("runner clock must be timezone-aware")
        return value

    async def _refresh_contracts(self, now: datetime) -> None:
        slug = self.config.market_slug
        self.market = await self.gateway.fetch_market(slug)
        incentives = await self.gateway.fetch_incentives([slug])
        self.incentive = incentives.get(slug)
        if self.incentive is None:
            raise CommissioningRefused(
                "selected market has no liquidityProgram incentive")
        if (not self.market.active or self.market.closed
                or self.market.status not in OPEN_MARKET_STATUSES):
            raise CommissioningRefused("selected market is not open")
        if self.market.game_start is None or self.cutoff_at is None:
            raise CommissioningRefused("market or cutoff clock is unavailable")
        if self.market.game_start <= self.cutoff_at:
            raise CommissioningRefused(
                "selected market starts before the 06:00 New York shutdown completes")
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
        self.book = await self.gateway.fetch_book(self.config.market_slug)
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
               or row["market_slug"] != self.config.market_slug for row in working):
            return False
        nonzero = self.ledger.query(
            "SELECT market_slug FROM execution_positions "
            "WHERE CAST(net_qty AS REAL)!=0")
        if any(row["market_slug"] != self.config.market_slug for row in nonzero):
            return False
        unresolved = self.ledger.unresolved_intents()
        if any(row["market_slug"] != self.config.market_slug
               or not row["exchange_order_id"] for row in unresolved):
            return False
        return bool(working or nonzero)

    async def preflight(self) -> str:
        now = self._now()
        self.started_at = now
        had_prior_place = bool(self.ledger.query(
            "SELECT 1 FROM execution_intents WHERE operation='PLACE' LIMIT 1"))
        self.cutoff_at = self.config.assert_authorized(now=now)
        await self._refresh_contracts(now)
        await self._refresh_book(now)
        await self.private_stream.start(None)
        self.gate.on_connect()
        await self.market_stream.start([self.config.market_slug])
        if self.ledger.working_orders(owned_only=True) or self.ledger.unresolved_intents():
            await self._rest_reconcile(now)
        accepted = ReadinessVerdict(
            "COMMISSIONING_ECONOMIC_RISK_ACCEPTED", True, False, (),
            ("profitability evidence waived only for one-contract commissioning",))
        report = await ControlledTrialStartup(
            self.account, self.ledger).reconcile(accepted, observed_at=now)
        recovery_only = (
            not report.ready
            and self._startup_recovery_is_scoped(report.blockers))
        if not report.ready and not recovery_only:
            raise CommissioningRefused("; ".join(report.blockers))
        self.gate.on_rest_reconciled(now)
        self.balance = await self.account.fetch_balance(observed_at=now)
        duration = (self.cutoff_at - now).total_seconds()
        self.risk_guard = SessionRiskGuard(
            started_at=now,
            starting_equity=self.balance.marked_equity,
            max_session_loss=self.config.max_session_loss_usd,
            max_market_loss=self.config.max_market_loss_usd,
            max_duration_seconds=duration,
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
        delivery = await self.notifier.send_async(
            "🟠 Liquidity Bot LIVE commissioning started: one market, "
            "one contract per quote, $5 soft stop, $15 hard stop, "
            "06:00 America/New_York cutoff.")
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

    def _evaluate_rewards(self, now: datetime) -> None:
        self._accrue_score(now)
        self._reward_eligible = frozenset()
        self._score_by_side = {}
        if self.market is None or self.book is None or self.incentive is None:
            return
        try:
            period = select_applicable_period(
                self.incentive.periods, self.market.game_start, now)
        except AmbiguousIncentiveError:
            return
        if period is None or period.period == "live" or self.market.tick_size is None:
            return
        eligible: set[str] = set()
        for side, price in (("bid", self.book.best_bid.price),
                            ("offer", self.book.best_offer.price)):
            result = evaluate_order_score(
                side=side, price=price, size=1,
                levels=self.book.levels(side), incentive=period,
                tick_size=self.market.tick_size)
            if result.eligible:
                eligible.add(side)
                self._score_by_side[side] = result.score
        self._reward_eligible = frozenset(eligible)

    def _working_orders(self) -> tuple[WorkingOrder, ...]:
        result: list[WorkingOrder] = []
        side_by_intent = {
            "ORDER_INTENT_BUY_LONG": "bid",
            "ORDER_INTENT_SELL_SHORT": "bid",
            "ORDER_INTENT_BUY_SHORT": "offer",
            "ORDER_INTENT_SELL_LONG": "offer",
        }
        for row in self.ledger.working_orders(self.config.market_slug):
            intent_row = self.ledger.query(
                "SELECT payload_json FROM execution_intents "
                "WHERE operation='PLACE' AND client_order_id=? "
                "ORDER BY created_at DESC LIMIT 1", (row["client_order_id"],))
            if not intent_row:
                raise RuntimeError("working order has no owned place intent")
            payload = json.loads(intent_row[0]["payload_json"])
            strategy_side = side_by_intent.get(str(payload.get("intent") or ""))
            if strategy_side is None:
                raise RuntimeError("working order intent cannot be mapped")
            remaining = Decimal(row["original_qty"]) - Decimal(row["filled_qty"])
            if remaining > 0:
                result.append(WorkingOrder(
                    row["exchange_order_id"], strategy_side,
                    float(Decimal(row["price"])), remaining))
        return tuple(result)

    def _frame(self, now: datetime) -> MarketFrame:
        if self.market is None or self.book is None or self.book_observed_at is None:
            raise RuntimeError("market frame is incomplete")
        position = self.ledger.position(self.market.slug)
        basis = self.ledger.inventory_basis(self.market.slug)
        if position != 0 and basis is None:
            return MarketFrame(
                self.market, now, self.book, self.book_observed_at, position,
                self._working_orders(), False, self._reward_eligible, {}, 0)
        expected = {side: 1e-9 for side in self._reward_eligible}
        return MarketFrame(
            market=self.market,
            now=now,
            book=self.book,
            book_observed_at=self.book_observed_at,
            position=position,
            working_orders=self._working_orders(),
            state_known=(self.gate.ready(self.ledger, now=now)
                         and self.ledger.risk_state_known()),
            reward_eligible=self._reward_eligible,
            # The operator accepted missing profitability evidence. A tiny
            # positive sentinel permits only venue-eligible sides; it is not
            # stored or reported as estimated profit.
            expected_net_by_side=expected,
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
            self.config.market_slug: min(change, Decimal("0"))})

    def _stop_reason(self, now: datetime) -> str | None:
        if self._stop_requested:
            return self._stop_requested
        if self.cutoff_at is not None and now >= self.cutoff_at:
            return "06:00_America_New_York_cutoff"
        if self.risk_guard is None:
            return "risk_guard_missing"
        if self.risk_guard.hard_stop_reached(now=now):
            return "15_USD_HARD_STOP"
        blockers = self.risk_guard.blockers(now=now)
        if blockers:
            return "risk_stop:" + ",".join(blockers)
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
        position = self.ledger.position(self.config.market_slug)
        basis = self.ledger.inventory_basis(self.config.market_slug)
        working = self.ledger.working_orders(self.config.market_slug)
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
            readiness_status="COMMISSIONING_ECONOMIC_RISK_ACCEPTED",
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
        payload["embeds"][0]["footer"]["text"] += (
            f" • session creates {place_count - self._orders_before}")
        await self.notifier.send_payload_async(payload, critical=critical)

    async def _shutdown(self, reason: str) -> None:
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot LIVE shutdown started: {reason}. "
            "New risk is disabled; reconciling to flat.",
            critical=reason == "15_USD_HARD_STOP")
        alerted = False
        while True:
            now = self._now()
            try:
                await self._rest_reconcile(now)
                position = self.ledger.position(self.config.market_slug)
                working = self.ledger.working_orders(self.config.market_slug)
                unresolved = self.ledger.market_has_unresolved_intent(
                    self.config.market_slug)
                if working and not unresolved:
                    await self.kill_switch.initiate(
                        self.config.market_slug, now=now)
                elif position != 0 and not working and not unresolved:
                    await self._refresh_book(now)
                    if self.book is None or self.market is None:
                        raise RuntimeError("shutdown book is unavailable")
                    basis = self.ledger.inventory_basis(self.config.market_slug)
                    entry_side = "bid" if position > 0 else "offer"
                    conservative_entry = (
                        float(basis.average_entry_price) if basis is not None
                        else .99 if position > 0 else .01)
                    exit_result = cross_exit(
                        self.book, entry_side=entry_side,
                        entry_price=conservative_entry, qty=float(abs(position)),
                        depth_haircut=.5)
                    if not exit_result.complete or exit_result.worst_price is None:
                        raise RuntimeError(
                            "insufficient stressed depth to flatten exact position")
                    side = "offer" if position > 0 else "bid"
                    action = PlanAction(
                        ActionKind.PLACE_IOC, self.config.market_slug,
                        "commissioning_shutdown_flatten", side=side,
                        price=exit_result.worst_price, qty=abs(position))
                    if self.driver is None:
                        raise RuntimeError("action driver is unavailable")
                    await self.driver.apply_one(
                        [action], market=self.market,
                        position=position, now=now)
                status = self.kill_switch.status(self.config.market_slug)
                if status.safe_to_stop and self.ledger.risk_state_known():
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot LIVE stopped flat after {reason}.")
                    return
            except Exception as exc:
                if not alerted:
                    traceback.print_exc()
                    await self.notifier.send_async(
                        "🚨 Liquidity Bot cannot yet prove flat during shutdown "
                        f"({type(exc).__name__}). Process remains in recovery.",
                        critical=True)
                    alerted = True
            await self.sleep(1)

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
            self._evaluate_rewards(now)
            next_balance = now
            next_book_refresh = now + timedelta(seconds=3)
            next_contract_refresh = now + timedelta(minutes=1)
            next_summary = now + timedelta(minutes=5)
            next_reconcile = now
            next_state_reconcile = now + timedelta(seconds=10)
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
                        if self.risk_guard:
                            self.risk_guard.trip_critical(
                                f"market stream: {type(exc).__name__}")
                    market_task = asyncio.create_task(
                        self.market_stream.next_book())
                if private_task in done:
                    try:
                        await self._observe_private(
                            private_task.result(), self._now())
                    except Exception as exc:
                        self.gate.on_disconnect()
                        if self.risk_guard:
                            self.risk_guard.trip_critical(
                                f"private stream: {type(exc).__name__}")
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
                    except Exception as exc:
                        if self.risk_guard:
                            self.risk_guard.trip_critical(
                                f"market/incentive refresh: {type(exc).__name__}")
                    next_contract_refresh = now + timedelta(minutes=1)
                # Keep this last among periodic REST work so account/incentive
                # latency cannot age the book before the decision below.
                try:
                    next_book_refresh = await self._refresh_quiet_book_if_due(
                        next_book_refresh)
                except Exception as exc:
                    if self.risk_guard:
                        self.risk_guard.trip_critical(
                            f"quiet-book REST refresh: {type(exc).__name__}")
                    next_book_refresh = self._now() + timedelta(seconds=3)
                now = self._now()
                self._evaluate_rewards(now)
                stop_reason = self._stop_reason(now) or ""
                if stop_reason:
                    break
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
                actions = self.reconciler.plan(self._frame(now))
                if actions and actions[0].kind == ActionKind.CRITICAL:
                    stop_reason = f"critical:{actions[0].reason}"
                    break
                if self.driver is None or self.market is None:
                    raise RuntimeError("commissioning driver is unavailable")
                await self.driver.apply_one(
                    actions, market=self.market,
                    position=self.ledger.position(self.market.slug), now=now)
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
            self.cutoff_at or stopped,
            stopped,
            stop_reason,
            self.ledger.position(self.config.market_slug) == 0,
            creates - self._orders_before,
        )


async def _run(args: argparse.Namespace) -> CommissioningResult:
    now = datetime.now(timezone.utc)
    config = CommissioningConfig(
        market_slug=args.market_slug,
        economic_risk_accepted=args.accept_one_contract_risk,
        exclusive_api_key_confirmed=args.exclusive_api_key,
        maker_lab_stopped_confirmed=args.maker_lab_stopped,
        arm_live=args.arm_live,
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
    try:
        return await CommissioningRunner(
            client, config, ledger, DiscordNotifier(secrets)).run()
    finally:
        ledger.close()
        await client.close()
        lease.release()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-contract Polymarket liquidity commissioning runner")
    parser.add_argument("--market-slug", required=True)
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
        "cutoff_at": result.cutoff_at.isoformat(),
        "stopped_at": result.stopped_at.isoformat(),
        "stop_reason": result.stop_reason,
        "flat": result.flat,
        "orders_sent_to_polymarket": result.orders_sent_to_polymarket,
    }, indent=2, sort_keys=True))
    if not result.flat:
        return 3
    if (result.stop_reason.startswith("runner_failure:")
            or result.stop_reason.startswith("critical:")):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
