"""Fail-closed Retail API order entry for a controlled liquidity trial.

This module deliberately exposes only single-order create, retrieve, and
market-scoped cancel operations. It has no modify, cancel-all, or account-wide
close method. Every state-changing call is preceded by a durable ledger intent
and is never retried automatically.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError
from .execution_ledger import ExecutionLedger
from .models import MarketMetadata
from .venue_state import normalize_execution, normalize_order


class OrderValidationError(ValueError):
    """An order fails local market-metadata validation."""


class OrderOutcomeUncertain(RuntimeError):
    """The remote result is ambiguous and the market remains quarantined."""


# The market metadata endpoint currently emits MARKET_STATUS_OPEN while the
# full-book endpoint emits MARKET_STATE_OPEN. Accept only these two observed
# documented-open families; every other value remains fail-closed.
OPEN_MARKET_STATUSES = frozenset({
    None,
    "MARKET_STATE_OPEN",
    "MARKET_STATUS_OPEN",
})


@dataclass(frozen=True)
class OrderRequest:
    market_slug: str
    strategy_side: str
    price: Decimal
    quantity: Decimal
    reducing: bool = False
    time_in_force: str = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    post_only: bool = True


@dataclass(frozen=True)
class SubmittedOrder:
    intent_id: str
    client_order_id: str
    exchange_order_id: str


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OrderValidationError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise OrderValidationError(f"{field} must be finite")
    return result


def _wire_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _aligned(value: Decimal, increment: Decimal) -> bool:
    return value % increment == 0


def _intent(strategy_side: str, reducing: bool) -> str:
    """Map a book side to a venue intent. Two intents only, never four.

    ``reducing`` is accepted and deliberately ignored. It used to select
    ``SELL_SHORT`` / ``SELL_LONG``, and those are the exact two intents
    maker-lab never sends -- because the venue reads the price in the opposite
    frame for them.

    Confirmed live 2026-08-14: holding a short of 5 entered at 0.36, the
    reducing quote went out as SELL_SHORT at 0.36 and the venue rested it at
    **0.64**, which is 1 - 0.36. At that price it adds to the short instead of
    closing it. maker-lab hit the same thing and recorded it in their
    `to_intent` docstring: "the exchange read our 0.29 as the YES price and
    charged us the NO side of it", roughly 41 cents per contract given away,
    after "three wrong diagnoses before the arithmetic gave it away".

    Their resolution, running for days across 201 fills, is the mapping below:
    both sides are BUY intents and the price is sent unchanged. The venue nets
    a BUY on the opposite outcome against an open position, so this closes as
    well as opens -- reduction needs no separate intent.

    ``preview`` does not catch the inversion. It echoes which OUTCOME an order
    takes and says nothing about which FRAME its price is in.
    """

    side = strategy_side.lower()
    if side == "bid":
        return "ORDER_INTENT_BUY_LONG"
    if side == "offer":
        return "ORDER_INTENT_BUY_SHORT"
    raise OrderValidationError("strategy_side must be 'bid' or 'offer'")


def build_order_payload(request: OrderRequest,
                        market: MarketMetadata) -> dict[str, Any]:
    """Validate against current venue metadata and produce the SDK payload."""

    if request.market_slug != market.slug:
        raise OrderValidationError("order market does not match metadata")
    if not market.active or market.closed:
        raise OrderValidationError("market is not active for order entry")
    if market.status not in OPEN_MARKET_STATUSES:
        raise OrderValidationError(f"market state {market.status} is not open")
    if market.tick_size is None or market.minimum_trade_qty is None:
        raise OrderValidationError(
            "tick size and minimumTradeQty are required for live orders")
    price = _decimal(request.price, "price")
    quantity = _decimal(request.quantity, "quantity")
    tick = _decimal(market.tick_size, "tick_size")
    minimum = _decimal(market.minimum_trade_qty, "minimum_trade_qty")
    if not Decimal("0.01") <= price <= Decimal("0.99"):
        raise OrderValidationError("price must be within exchange bounds [0.01, 0.99]")
    if quantity < minimum:
        raise OrderValidationError("quantity is below minimumTradeQty")
    if not _aligned(price, tick):
        raise OrderValidationError("price is not aligned to orderPriceMinTickSize")
    if not _aligned(quantity, minimum):
        raise OrderValidationError("quantity is not aligned to minimumTradeQty")
    if request.time_in_force not in {
            "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}:
        raise OrderValidationError("controlled trial supports only GTC and IOC")
    if request.post_only and request.time_in_force != "TIME_IN_FORCE_GOOD_TILL_CANCEL":
        raise OrderValidationError("post-only orders must be GTC")
    if (not request.post_only
            and request.time_in_force != "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"):
        raise OrderValidationError("aggressive trial orders must be IOC")
    return {
        "marketSlug": request.market_slug,
        "intent": _intent(request.strategy_side, request.reducing),
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": format(price, "f"), "currency": "USD"},
        "quantity": _wire_number(quantity),
        "tif": request.time_in_force,
        "participateDontInitiate": request.post_only,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": False,
    }


class AsyncTradingGateway:
    """Mutations sharing the same limiter as every REST read."""

    def __init__(self, gateway: AsyncReadOnlyGateway):
        self.gateway = gateway
        self.client = gateway.client

    async def _mutation(self, operation: str, call: Any) -> Any:
        # Never use AsyncReadOnlyGateway._call here: its confirmed-429 retry is
        # correct for reads but can duplicate an order after an ambiguous POST.
        await self.gateway.limiter.wait()
        try:
            return await call()
        except Exception as exc:
            raise AdapterError(
                f"{operation} outcome uncertain: {type(exc).__name__}: {exc}") from exc

    async def create(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        response = await self._mutation(
            "POST /v1/orders",
            lambda: self.client.orders.create(dict(payload)))
        if not isinstance(response, Mapping):
            raise AdapterError("create-order response is not an object")
        return response

    async def cancel(self, exchange_order_id: str, market_slug: str) -> None:
        await self._mutation(
            f"POST /v1/order/{exchange_order_id}/cancel",
            lambda: self.client.orders.cancel(
                exchange_order_id, {"marketSlug": market_slug}))

    # A cancelled order is briefly unreadable. Measured 2026-08-14: 246 of
    # these in 5.7 hours, and every id that 404'd was retrievable later with a
    # proper terminal state. The generic retry in `_call` only covers HTTP 429,
    # so the 404 escalated: the intent stayed unresolved, risk_state_known went
    # false, and the runner quarantined every working order -- 539 recoveries,
    # each one cancelling resting quotes. Reward pays for resting, so that
    # churn was expensive. We hold this id from an acknowledged mutation, so
    # the order exists; a 404 is consistency lag, not absence. Waiting assumes
    # nothing about its state, unlike treating absence as cancellation.
    RETRIEVE_NOT_FOUND_ATTEMPTS = 4
    RETRIEVE_NOT_FOUND_BASE_SECONDS = 0.5

    async def retrieve(self, exchange_order_id: str) -> Mapping[str, Any]:
        attempts = self.RETRIEVE_NOT_FOUND_ATTEMPTS
        for attempt in range(attempts):
            try:
                response = await self.gateway._call(
                    f"GET /v1/order/{exchange_order_id}",
                    lambda: self.client.orders.retrieve(exchange_order_id))
            except AdapterError as exc:
                if "NotFound" not in str(exc) or attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(
                    self.RETRIEVE_NOT_FOUND_BASE_SECONDS * (2 ** attempt))
                continue
            if not isinstance(response, Mapping) or not isinstance(
                    response.get("order"), Mapping):
                raise AdapterError("get-order response has no order")
            return response
        raise AssertionError("unreachable retrieve retry loop")


class LiveOrderExecutor:
    """Durable single-order state transitions; no strategy decisions."""

    def __init__(self, gateway: AsyncTradingGateway, ledger: ExecutionLedger):
        self.gateway = gateway
        self.ledger = ledger

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    async def place(self, request: OrderRequest, market: MarketMetadata, *,
                    now: datetime, intent_id: str | None = None,
                    client_order_id: str | None = None) -> SubmittedOrder:
        payload = build_order_payload(request, market)
        # Name the ordinary same-market race before the broader state gate so
        # the runner can wait for reconciliation without quarantining (and
        # cancelling) the order that is still becoming visible at the venue.
        if self.ledger.market_has_unresolved_intent(request.market_slug):
            raise OrderOutcomeUncertain("market has an unresolved order intent")
        # A mutation in another market does not make this market's durable
        # execution state unknown. Ownership violations remain account-wide
        # inside risk_state_known(), while intents and briefly absent orders
        # are deliberately scoped to the market being mutated.
        if not self.ledger.risk_state_known(request.market_slug):
            raise OrderOutcomeUncertain("execution state is not known")
        intent_id = intent_id or self._id("intent")
        client_order_id = client_order_id or self._id("liquidity")
        self.ledger.prepare_intent(
            intent_id, "PLACE", request.market_slug, client_order_id,
            payload, created_at=now)
        self.ledger.transition_intent(intent_id, "SUBMITTING", updated_at=now)
        try:
            response = await self.gateway.create(payload)
            exchange_id = str(response.get("id") or "").strip()
            if not exchange_id:
                raise AdapterError("create-order response has no exchange order id")
        except Exception as exc:
            self.ledger.transition_intent(
                intent_id, "UNCERTAIN", updated_at=now,
                error=f"{type(exc).__name__}: {exc}")
            raise OrderOutcomeUncertain(
                f"place outcome uncertain for {request.market_slug}") from exc
        self.ledger.transition_intent(
            intent_id, "ACKNOWLEDGED", updated_at=now,
            exchange_order_id=exchange_id)
        for index, execution in enumerate(response.get("executions") or []):
            if not isinstance(execution, Mapping) or not isinstance(
                    execution.get("order"), Mapping):
                raise OrderOutcomeUncertain("embedded execution is malformed")
            resolver = lambda value, _raw=None, expected=exchange_id, owner=client_order_id: (
                owner if value == expected else None)
            order = normalize_order(
                execution["order"], observed_at=now,
                resolve_client_id=resolver)
            self.ledger.apply_order_event(
                "create_response", f"{intent_id}:order:{index}", order,
                received_at=now)
            if str(execution.get("type") or "").upper() in {
                    "EXECUTION_TYPE_PARTIAL_FILL", "EXECUTION_TYPE_FILL"}:
                self.ledger.apply_execution(normalize_execution(
                    execution, observed_at=now, resolve_client_id=resolver),
                    received_at=now)
        return SubmittedOrder(
            intent_id, client_order_id, exchange_id)

    async def cancel(self, *, client_order_id: str, exchange_order_id: str,
                     market_slug: str, now: datetime,
                     intent_id: str | None = None) -> str:
        intent_id = intent_id or self._id("cancel")
        payload = {"orderId": exchange_order_id, "marketSlug": market_slug}
        self.ledger.prepare_intent(
            intent_id, "CANCEL", market_slug, client_order_id,
            payload, created_at=now)
        self.ledger.transition_intent(
            intent_id, "SUBMITTING", updated_at=now)
        try:
            await self.gateway.cancel(exchange_order_id, market_slug)
        except Exception as exc:
            self.ledger.transition_intent(
                intent_id, "UNCERTAIN", updated_at=now,
                exchange_order_id=exchange_order_id,
                error=f"{type(exc).__name__}: {exc}")
            raise OrderOutcomeUncertain(
                f"cancel outcome uncertain for {exchange_order_id}") from exc
        self.ledger.transition_intent(
            intent_id, "ACKNOWLEDGED", updated_at=now,
            exchange_order_id=exchange_order_id)
        return intent_id

    async def reconcile_order(self, exchange_order_id: str, *,
                              observed_at: datetime) -> None:
        response = await self.gateway.retrieve(exchange_order_id)
        order = normalize_order(
            response["order"], observed_at=observed_at,
            resolve_client_id=self.ledger.resolve_client_order_id)
        self.ledger.apply_order_event(
            "rest_reconcile", f"order:{exchange_order_id}:{order.event_at.isoformat()}",
            order, received_at=observed_at)
