"""Fail-closed Retail API order entry for a controlled liquidity trial.

This module deliberately exposes only single-order create, retrieve, and
market-scoped cancel operations. It has no modify, cancel-all, or account-wide
close method. Every state-changing call is preceded by a durable ledger intent
and is never retried automatically.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from .api_diagnostics import response_ids
from .errors import AdapterError, ApiAdmissionDeferred, RuntimeInvariantViolation
from .execution_ledger import ExecutionLedger, QuoteObservation
from .models import MarketMetadata
from .venue_state import normalize_execution, normalize_order


class OrderValidationError(ValueError):
    """An order fails local market-metadata validation."""


class OrderOutcomeUncertain(RuntimeError):
    """The remote result is ambiguous and the market remains quarantined."""


class OrderAdmissionFailed(RuntimeInvariantViolation):
    """Critical local failure with positive proof the request was NOT sent."""

    def __init__(self, cause: Exception):
        code = (cause.code if isinstance(cause, RuntimeInvariantViolation)
                else "order_admission_failed")
        super().__init__(
            code, f"local order admission failed ({type(cause).__name__}); request not sent")


class OrderMutationRejected(AdapterError):
    """The venue positively rejected a mutation without accepting it.

    This is deliberately separate from ``OrderOutcomeUncertain``. A signed
    HTTP rejection is terminal proof about this request; a timeout, dropped
    connection, malformed success response, or 5xx remains ambiguous.
    """


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
    # Local-only measurement payload.  It is deliberately omitted from the
    # venue wire body and has no role in order validation or strategy policy.
    quote_observation: QuoteObservation | None = None
    good_till_time: datetime | None = None


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
            "TIME_IN_FORCE_GOOD_TILL_DATE",
            "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}:
        raise OrderValidationError("controlled trial supports only GTC, GTD and IOC")
    if request.post_only and request.time_in_force not in {
            "TIME_IN_FORCE_GOOD_TILL_CANCEL", "TIME_IN_FORCE_GOOD_TILL_DATE"}:
        raise OrderValidationError("post-only orders must be GTC or GTD")
    if (not request.post_only
            and request.time_in_force != "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"):
        raise OrderValidationError("aggressive trial orders must be IOC")
    if request.time_in_force == "TIME_IN_FORCE_GOOD_TILL_DATE":
        if (request.reducing or not request.post_only
                or not isinstance(request.good_till_time, datetime)
                or request.good_till_time.utcoffset() is None):
            raise OrderValidationError("GTD requires an aware expiry on a passive entry")
    elif request.good_till_time is not None:
        raise OrderValidationError("expiry is only valid for GTD entries")
    payload = {
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
    if request.good_till_time is not None:
        payload["goodTillTime"] = request.good_till_time.astimezone(
            timezone.utc).isoformat().replace("+00:00", "Z")
    return payload


class AsyncTradingGateway:
    """Mutations sharing the same limiter as every REST read."""

    def __init__(self, gateway: AsyncReadOnlyGateway):
        self.gateway = gateway
        self.client = gateway.client

    async def _mutation(
        self, operation: str, call: Any, *, traffic_class: str = "safety",
        admission_check=None,
    ) -> Any:
        # Never use AsyncReadOnlyGateway._call here: its confirmed-429 retry is
        # correct for reads but can duplicate an order after an ambiguous POST.
        if isinstance(self.gateway.limiter, AsyncIntervalLimiter):
            try:
                await self.gateway.limiter.wait(
                    operation=operation, request_kind="mutation",
                    traffic_class=traffic_class)
            except asyncio.CancelledError:
                # Operator stop interrupted admission before `call()` began.
                # Convert that local fact into an exact failure so the durable
                # SUBMITTING intent cannot survive shutdown as false ambiguity.
                raise ApiAdmissionDeferred(
                    f"{operation} admission interrupted; request not sent",
                    operation=operation,
                    error_code="LOCAL_API_INTERRUPTED",
                    retry_after_seconds=0.0,
                ) from None
        else:
            # Preserve the long-standing zero-argument protocol for injected
            # deterministic gates used by tests and dry-run composition.
            await self.gateway.limiter.wait()
        if admission_check is not None:
            # A local veto before network I/O is exact, never UNCERTAIN.
            try:
                admission_check()
            except ApiAdmissionDeferred:
                raise
            except Exception as exc:
                # Preserve critical escalation separately from normal stale
                # proof/rate deferral, without inventing a remotely sent order.
                raise OrderAdmissionFailed(exc) from exc
        started_at = time.monotonic()
        try:
            result = await call()
            if isinstance(self.gateway.limiter, AsyncIntervalLimiter):
                await self.gateway.limiter.record_result(
                    duration_seconds=time.monotonic() - started_at,
                    traffic_class=traffic_class,
                    operation=operation, request_kind="mutation")
            return result
        except Exception as exc:
            if isinstance(self.gateway.limiter, AsyncIntervalLimiter):
                await self.gateway.limiter.record_result(
                    duration_seconds=time.monotonic() - started_at,
                    status_code=self.gateway._status_code(exc),
                    failed=True,
                    retry_after_seconds=(
                        getattr(exc, "retry_after_seconds", None)
                        if isinstance(exc, AdapterError)
                        else self.gateway._retry_after_seconds(exc)),
                    traffic_class=traffic_class,
                    operation=operation, request_kind="mutation",
                    server_ids=response_ids(self.gateway._response_headers(exc),
                                            getattr(exc, "trace_id", None)),
                )
            # Preserve structured metadata if the SDK boundary has already
            # normalized the exception. Re-normalizing an AdapterError would
            # discard its code because it no longer carries the raw body.
            detail = (exc if isinstance(exc, AdapterError)
                      else self.gateway._adapter_error(operation, exc))
            # A structured 403 response is positive proof that the venue
            # refused this mutation. Keeping it UNCERTAIN invents an order
            # that the response says was not accepted, and an account-wide
            # permission/location denial then poisons every market slot.
            if detail.status_code == 403:
                raise OrderMutationRejected(
                    f"{operation} rejected: {detail}",
                    operation=operation,
                    status_code=detail.status_code,
                    error_code=detail.error_code,
                    trace_id=detail.trace_id,
                    retry_after_seconds=detail.retry_after_seconds,
                ) from exc
            raise AdapterError(
                f"{operation} outcome uncertain: {detail}",
                operation=operation,
                status_code=detail.status_code,
                error_code=detail.error_code,
                trace_id=detail.trace_id,
                retry_after_seconds=detail.retry_after_seconds,
            ) from exc

    async def create(
        self, payload: Mapping[str, Any], *, reducing: bool = False,
    ) -> Mapping[str, Any]:
        def check_event():
            if payload.get("tif") == "TIME_IN_FORCE_GOOD_TILL_DATE":
                deadline = datetime.fromisoformat(
                    str(payload["goodTillTime"]).replace("Z", "+00:00"))
                if deadline <= datetime.now(timezone.utc):
                    raise ApiAdmissionDeferred(
                        "entry expired during admission; request not sent",
                        error_code="LOCAL_ENTRY_EXPIRED", retry_after_seconds=1.0)
            check = getattr(self.gateway, "event_entry_allowed", None)
            if (not reducing and callable(check)
                    and not check(str(payload.get("marketSlug", "")))):
                raise ApiAdmissionDeferred(
                    "event pregame evidence expired; request not sent",
                    error_code="LOCAL_EVENT_ENTRY_BLOCKED", retry_after_seconds=1.0)
            check_entry = getattr(self.gateway, "entry_admission_check", None)
            if not reducing and callable(check_entry):
                check_entry(payload)

        traffic_class = (
            "safety"
            if (reducing
                or payload.get("tif") == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
            else "entry")
        response = await self._mutation(
            "POST /v1/orders",
            lambda: self.client.orders.create(dict(payload)),
            traffic_class=traffic_class, admission_check=check_event)
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

    def resolve_recorded_permission_denials(self, *, now: datetime) -> tuple[str, ...]:
        """Close legacy uncertain intents that contain exact rejection proof.

        Runners before the response-backed rejection classifier persisted a
        structured location denial as free-form error text and marked the
        intent uncertain. The three required tokens below are the durable
        response metadata recorded by that old path. A timeout, 5xx, generic
        403, missing id, or merely absent order cannot pass this repair.
        """

        repaired: list[str] = []
        for row in self.ledger.unresolved_intents():
            if str(row["state"]).upper() != "UNCERTAIN":
                continue
            error = str(row["error"] or "")
            if not all(token in error for token in (
                    "PermissionDeniedError", "status=403", "code=7")):
                continue
            intent_id = str(row["intent_id"])
            self.ledger.transition_intent(
                intent_id, "FAILED", updated_at=now,
                exchange_order_id=row["exchange_order_id"],
                error=f"definitive_permission_rejection: {error}")
            repaired.append(intent_id)
        return tuple(repaired)

    async def place(self, request: OrderRequest, market: MarketMetadata, *,
                    now: datetime, intent_id: str | None = None,
                    client_order_id: str | None = None) -> SubmittedOrder:
        payload = build_order_payload(request, market)
        if request.good_till_time is not None and request.good_till_time <= now:
            raise OrderValidationError("entry expiry must be in the future")
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
            payload, created_at=now,
            quote_observation=(
                request.quote_observation if not request.reducing else None),
            quote_observation_required=(
                request.post_only and not request.reducing))
        self.ledger.transition_intent(intent_id, "SUBMITTING", updated_at=now)
        try:
            response = await self.gateway.create(
                payload, reducing=request.reducing)
            exchange_id = str(response.get("id") or "").strip()
            if not exchange_id:
                raise AdapterError("create-order response has no exchange order id")
        except (ApiAdmissionDeferred, OrderAdmissionFailed) as exc:
            # The limiter declined this entry before gateway.create performed
            # any network IO.  FAILED is exact; UNCERTAIN would falsely claim
            # that an order might exist and unnecessarily quarantine market
            # state during a background/entry cooldown.
            self.ledger.transition_intent(
                intent_id, "FAILED", updated_at=now,
                error=f"{type(exc).__name__}: {exc}")
            raise
        except OrderMutationRejected as exc:
            self.ledger.transition_intent(
                intent_id, "FAILED", updated_at=now,
                error=f"{type(exc).__name__}: {exc}")
            raise
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
        except (ApiAdmissionDeferred, OrderAdmissionFailed) as exc:
            self.ledger.transition_intent(
                intent_id, "FAILED", updated_at=now,
                exchange_order_id=exchange_order_id,
                error=f"{type(exc).__name__}: {exc}")
            raise
        except OrderMutationRejected as exc:
            self.ledger.transition_intent(
                intent_id, "FAILED", updated_at=now,
                exchange_order_id=exchange_order_id,
                error=f"{type(exc).__name__}: {exc}")
            raise
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

    async def reconcile_absent_order(self, exchange_order_id: str, *,
                                     observed_at: datetime) -> None:
        """Re-read an order we have lost sight of, and always apply the answer.

        `reconcile_order` keys its event on the venue's own event time and
        status, which is right for the normal sweep -- the same venue event
        must not be applied twice. It is wrong here.

        `UNKNOWN_ABSENT` is a status **we invented locally**, from an order
        missing out of a complete snapshot. The venue never emits a new event
        for it, so once its terminal state has been recorded the re-ask
        produces an identical (id, event_at, status) triple, `_accept_event`
        rejects it as a replay, `_upsert_order` never runs, and the row is
        stuck absent forever. Measured 2026-08-19: `BZ9PPEV4RDK0` sat absent
        while the venue plainly said CANCELED at a timestamp already on file,
        and `risk_state_known()` counts absences account-wide, so rotation was
        blocked for hours with fifteen slots bound and two quoting.

        Keying on the OBSERVATION time instead means this always writes. That
        is safe: `_upsert_order` is an upsert with its own ordering and
        terminal guards -- including `resolves_absence`, which exists so a
        terminal state can outrank an absence and was unreachable from here.
        """

        response = await self.gateway.retrieve(exchange_order_id)
        order = normalize_order(
            response["order"], observed_at=observed_at,
            resolve_client_id=self.ledger.resolve_client_order_id)
        self.ledger.apply_order_event(
            "absent_repair",
            f"absent:{exchange_order_id}:{observed_at.isoformat()}",
            order, received_at=observed_at)

    async def reconcile_order(self, exchange_order_id: str, *,
                              observed_at: datetime) -> None:
        order = await self.read_order_state(exchange_order_id, observed_at=observed_at)
        self.apply_order_state(exchange_order_id, order, observed_at=observed_at)

    async def read_order_state(self, exchange_order_id: str, *, observed_at: datetime,
                               resolve_client_id=None):
        """Concurrent callers must supply a deferred, non-writing resolver."""
        response = await self.gateway.retrieve(exchange_order_id)
        order = normalize_order(
            response["order"], observed_at=observed_at,
            resolve_client_id=(resolve_client_id if resolve_client_id is not None
                               else self.ledger.resolve_client_order_id))
        if order.exchange_order_id != exchange_order_id:
            raise OrderValidationError("retrieved order identity does not match request")
        return order

    def apply_order_state(self, exchange_order_id: str, order, *, observed_at: datetime):
        # The status is part of the event identity, not just the timestamp.
        # The venue can change an order's STATE without advancing its event
        # time: measured 2026-08-18, BYTCVSJ4EDJT was read as PARTIAL at
        # lastTransactTime 18:08:27.222776Z, and after being cancelled it still
        # reported 18:08:27.222776Z -- now as CANCELED. Keyed on time alone
        # that is a replay, so `_accept_event` rejected it, `_upsert_order`
        # never ran, and the row could never leave `UNKNOWN_ABSENT`. The
        # ledger already knows how to handle this (`resolves_absence` lets a
        # terminal state outrank an absence) but that code was unreachable.
        # Rotation is blocked account-wide while any absence stands, so one
        # order stuck this way stops the bot rebinding at all.
        #
        # Safe to make the key finer: each (id, time, status) still applies at
        # most once, and `_upsert_order` keeps its own ordering and terminal
        # guards, so a stale or backwards transition is still refused there.
        self.ledger.apply_order_event(
            "rest_reconcile",
            f"order:{exchange_order_id}:{order.event_at.isoformat()}"
            f":{order.status}",
            order, received_at=observed_at)
