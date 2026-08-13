"""Controlled-trial startup, stream-health, action, and shutdown gates.

There is intentionally no command-line entry point that can arm live trading.
The objects here are composable safety boundaries for a later supervised
runner, and all exchange mutations still flow through ``LiveOrderExecutor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from .account_snapshots import AsyncAccountSnapshotGateway
from .execution_ledger import ExecutionLedger
from .errors import RuntimeInvariantViolation
from .live_orders import LiveOrderExecutor, OrderRequest, SubmittedOrder
from .models import MarketMetadata
from .private_stream import PrivateEvent, PrivateEventKind
from .readiness import ReadinessVerdict
from .reconciler import ActionKind, PlanAction
from .runtime_safety import SessionRiskGuard


@dataclass(frozen=True)
class StartupReport:
    ready: bool
    blockers: tuple[str, ...]
    open_orders: int
    nonzero_positions: int


class ControlledTrialStartup:
    """Reconcile the entire shared account before allowing one new order."""

    def __init__(self, account: AsyncAccountSnapshotGateway,
                 ledger: ExecutionLedger):
        self.account = account
        self.ledger = ledger
        if getattr(self.account, "resolve_client_id", None) is None:
            self.account.resolve_client_id = ledger.resolve_client_order_id

    async def reconcile(self, verdict: ReadinessVerdict, *,
                        observed_at: datetime) -> StartupReport:
        if not verdict.trial_ready:
            return StartupReport(
                False, tuple(verdict.trial_blockers), 0, 0)
        orders = await self.account.fetch_open_orders(observed_at=observed_at)
        positions = await self.account.fetch_positions(observed_at=observed_at)
        stamp = observed_at.isoformat()
        self.ledger.apply_order_snapshot(
            f"startup-orders:{stamp}", orders, as_of=observed_at,
            observed_at=observed_at, complete=True)
        self.ledger.apply_position_snapshot(
            f"startup-positions:{stamp}", positions, as_of=observed_at,
            observed_at=observed_at, complete_account=True)
        blockers: list[str] = []
        if orders:
            blockers.append("shared account has open orders")
        nonzero = sum(1 for item in positions if item.net_qty != 0)
        if nonzero:
            blockers.append("shared account has nonzero positions")
        if self.ledger.unresolved_intents():
            blockers.append("execution ledger has unresolved intents")
        if not self.ledger.risk_state_known():
            blockers.append("exchange order ownership or state is unknown")
        return StartupReport(
            not blockers, tuple(blockers), len(orders), nonzero)


@dataclass
class PrivateStateGate:
    """Require REST reconciliation plus current private-stream snapshots."""

    max_silence_seconds: float = 30.0
    connected: bool = False
    rest_reconciled: bool = False
    order_snapshot_seen: bool = False
    order_snapshot_in_progress: bool = False
    balance_snapshot_seen: bool = False
    last_message_at: datetime | None = None
    last_reconcile_at: datetime | None = None

    def on_connect(self) -> None:
        self.connected = True
        self.rest_reconciled = False
        self.order_snapshot_seen = False
        self.order_snapshot_in_progress = False
        self.balance_snapshot_seen = False
        self.last_message_at = None
        self.last_reconcile_at = None

    def on_rest_reconciled(self, at: datetime) -> None:
        self.rest_reconciled = True
        self.last_reconcile_at = at

    def on_disconnect(self) -> None:
        self.connected = False
        self.rest_reconciled = False
        self.order_snapshot_seen = False
        self.order_snapshot_in_progress = False
        self.balance_snapshot_seen = False

    def observe(self, event: PrivateEvent, *, received_at: datetime) -> None:
        self.last_message_at = received_at
        if event.kind == PrivateEventKind.ERROR:
            self.on_disconnect()
            return
        if event.kind == PrivateEventKind.ORDER_SNAPSHOT:
            payload = event.payload
            self.order_snapshot_seen = (
                isinstance(payload, dict) and payload.get("eof") is True)
            self.order_snapshot_in_progress = not self.order_snapshot_seen
        elif event.kind == PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT:
            self.balance_snapshot_seen = True

    def ready(self, ledger: ExecutionLedger, *, now: datetime) -> bool:
        if not (self.connected and self.rest_reconciled
                and not self.order_snapshot_in_progress
                and self.balance_snapshot_seen
                and ledger.risk_state_known() and self.last_message_at
                and self.last_reconcile_at):
            return False
        freshest_state = max(self.last_message_at, self.last_reconcile_at)
        silence = (now - freshest_state).total_seconds()
        return 0 <= silence <= self.max_silence_seconds


@dataclass(frozen=True)
class ActionResult:
    action: PlanAction
    submitted: SubmittedOrder | None = None
    cancel_intent_id: str | None = None


class ControlledActionDriver:
    """Execute at most one reconciler mutation per pass."""

    def __init__(self, executor: LiveOrderExecutor, ledger: ExecutionLedger,
                 risk_guard: SessionRiskGuard | None = None):
        self.executor = executor
        self.ledger = ledger
        self.risk_guard = risk_guard

    def _owned_order(self, exchange_order_id: str):
        rows = self.ledger.query(
            "SELECT * FROM execution_orders WHERE exchange_order_id=? "
            "AND client_order_id NOT LIKE 'venue:%'",
            (exchange_order_id,))
        return rows[0] if rows else None

    async def apply_one(self, actions: Iterable[PlanAction], *,
                        market: MarketMetadata, position: Decimal,
                        now: datetime) -> ActionResult | None:
        action = next(iter(actions), None)
        if action is None:
            return None
        if action.kind in {ActionKind.CRITICAL, ActionKind.HALT_NEW_RISK}:
            return ActionResult(action)
        if action.market_slug != market.slug:
            raise RuntimeInvariantViolation(
                "action_market_mismatch",
                "action market does not match metadata")
        if action.kind == ActionKind.CANCEL:
            if not action.order_id:
                raise RuntimeInvariantViolation(
                    "cancel_order_id_missing",
                    "cancel action has no exchange order id")
            row = self._owned_order(action.order_id)
            if row is None:
                raise RuntimeInvariantViolation(
                    "cancel_order_not_owned",
                    "refusing to cancel an unowned or unknown order")
            intent = await self.executor.cancel(
                client_order_id=row["client_order_id"],
                exchange_order_id=row["exchange_order_id"],
                market_slug=row["market_slug"], now=now)
            return ActionResult(action, cancel_intent_id=intent)
        if action.price is None or action.qty is None or action.side is None:
            raise RuntimeInvariantViolation(
                "place_action_incomplete", "place action is incomplete")
        if action.kind == ActionKind.PLACE_IOC:
            if position == 0 or action.qty != abs(position):
                raise RuntimeInvariantViolation(
                    "ioc_not_exact_position",
                    "IOC must close the entire exact position")
            if self.ledger.working_orders(market.slug):
                raise RuntimeInvariantViolation(
                    "ioc_working_order_not_terminal",
                    "IOC requires every working order to be terminal")
            request = OrderRequest(
                market.slug, action.side, Decimal(str(action.price)), action.qty,
                reducing=True,
                time_in_force="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                post_only=False)
        elif action.kind == ActionKind.PLACE_POST_ONLY:
            reducing = position != 0
            if (not reducing and self.risk_guard is not None
                    and not self.risk_guard.can_add_new_risk(now=now)):
                return ActionResult(PlanAction(
                    ActionKind.HALT_NEW_RISK, market.slug,
                    "runtime_session_or_market_loss_limit"))
            if reducing:
                expected = "offer" if position > 0 else "bid"
                if action.side != expected or action.qty != abs(position):
                    raise RuntimeInvariantViolation(
                        "reducing_quote_not_exact_position",
                        "inventory quote must reduce the entire exact position")
            request = OrderRequest(
                market.slug, action.side, Decimal(str(action.price)), action.qty,
                reducing=reducing)
        else:
            raise RuntimeInvariantViolation(
                "unsupported_action", f"unsupported action {action.kind}")
        submitted = await self.executor.place(request, market, now=now)
        return ActionResult(action, submitted=submitted)


@dataclass(frozen=True)
class KillSwitchReport:
    market_slug: str
    cancel_requests: int
    position: Decimal
    safe_to_stop: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)


class MarketKillSwitch:
    """Cancel bot-owned orders in one market; never call account cancel-all."""

    def __init__(self, executor: LiveOrderExecutor, ledger: ExecutionLedger):
        self.executor = executor
        self.ledger = ledger

    async def initiate(self, market_slug: str, *, now: datetime) -> KillSwitchReport:
        rows = self.ledger.working_orders(market_slug)
        unowned = [row for row in rows
                   if str(row["client_order_id"]).startswith("venue:")]
        if unowned:
            return KillSwitchReport(
                market_slug, 0, self.ledger.position(market_slug), False,
                ("market contains an unowned working order",))
        sent = 0
        for row in rows:
            await self.executor.cancel(
                client_order_id=row["client_order_id"],
                exchange_order_id=row["exchange_order_id"],
                market_slug=market_slug, now=now)
            sent += 1
        position = self.ledger.position(market_slug)
        blockers: list[str] = []
        if sent:
            blockers.append("cancel requests await terminal exchange confirmation")
        if position != 0:
            blockers.append("position must be flattened before process exit")
        return KillSwitchReport(
            market_slug, sent, position, not blockers, tuple(blockers))

    def status(self, market_slug: str) -> KillSwitchReport:
        all_working = self.ledger.working_orders(market_slug)
        working = [row for row in all_working
                   if not str(row["client_order_id"]).startswith("venue:")]
        unowned = [row for row in all_working
                   if str(row["client_order_id"]).startswith("venue:")]
        position = self.ledger.position(market_slug)
        blockers: list[str] = []
        if working:
            blockers.append("bot-owned orders are still working")
        if unowned:
            blockers.append("market contains unowned working orders")
        if self.ledger.market_has_unresolved_intent(market_slug):
            blockers.append("market has unresolved order intents")
        if position != 0:
            blockers.append("position is not flat")
        return KillSwitchReport(
            market_slug, 0, position, not blockers, tuple(blockers))
