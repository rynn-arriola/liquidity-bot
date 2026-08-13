"""Strict normalization and projection of Polymarket private state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .errors import AdapterError
from .execution_ledger import Execution, ExecutionLedger, OrderState, PositionState
from .private_stream import PrivateEvent, PrivateEventKind


ClientIdResolver = Callable[..., str | None]


def _wire(raw: Mapping[str, Any], camel: str, snake: str) -> Any:
    return raw[camel] if camel in raw else raw.get(snake)


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdapterError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, field: str, fallback: datetime | None = None) -> datetime:
    if value in (None, ""):
        if fallback is None:
            raise AdapterError(f"{field} is missing")
        return _aware(fallback, field)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError(f"{field} is not an ISO timestamp") from exc
    return _aware(parsed, field)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AdapterError(f"{field} is not a decimal") from exc
    if not parsed.is_finite():
        raise AdapterError(f"{field} must be finite")
    return parsed


def _identity(exchange_id: str, resolver: ClientIdResolver | None,
              raw_order: Mapping[str, Any]) -> str:
    if resolver is None:
        resolved = None
    else:
        try:
            resolved = resolver(exchange_id, raw_order)
        except TypeError:
            # Preserve compatibility with simple one-argument test resolvers.
            resolved = resolver(exchange_id)
    return str(resolved or f"venue:{exchange_id}")


def _slug(order: Mapping[str, Any]) -> str:
    metadata = _wire(order, "marketMetadata", "market_metadata") or {}
    value = (_wire(order, "marketSlug", "market_slug")
             or (metadata.get("slug") if isinstance(metadata, Mapping) else None))
    if not value:
        raise AdapterError("order.marketSlug is missing")
    return str(value)


def normalize_order(
    raw: Any,
    *,
    observed_at: datetime,
    resolve_client_id: ClientIdResolver | None = None,
) -> OrderState:
    if not isinstance(raw, Mapping):
        raise AdapterError("order is not an object")
    exchange_id = str(raw.get("id") or raw.get("order_id") or "").strip()
    if not exchange_id:
        raise AdapterError("order.id is missing")
    state = str(raw.get("state") or raw.get("order_state") or "").strip().upper()
    state_map = {
        "ORDER_STATE_NEW": "OPEN",
        "ORDER_STATE_PENDING_NEW": "PENDING",
        "ORDER_STATE_PENDING_REPLACE": "PENDING",
        "ORDER_STATE_PENDING_CANCEL": "PENDING",
        "ORDER_STATE_PENDING_RISK": "PENDING",
        "ORDER_STATE_PARTIALLY_FILLED": "PARTIAL",
        "ORDER_STATE_FILLED": "FILLED",
        "ORDER_STATE_CANCELED": "CANCELED",
        "ORDER_STATE_REPLACED": "CANCELED",
        "ORDER_STATE_REJECTED": "REJECTED",
        "ORDER_STATE_EXPIRED": "EXPIRED",
    }
    if state not in state_map:
        raise AdapterError(f"unknown order state {state or '<missing>'}")
    quantity = _decimal(raw.get("quantity"), "order.quantity")
    cumulative_raw = _wire(raw, "cumQuantity", "cum_quantity")
    cumulative = _decimal(
        "0" if cumulative_raw is None else cumulative_raw,
        "order.cumQuantity")
    event_at = _timestamp(
        _wire(raw, "updateTime", "update_time")
        or _wire(raw, "lastTransactTime", "last_transact_time")
        or _wire(raw, "createTime", "create_time")
        or _wire(raw, "insertTime", "insert_time"),
        "order event time", fallback=observed_at)
    try:
        return OrderState(
            client_order_id=_identity(exchange_id, resolve_client_id, raw),
            exchange_order_id=exchange_id,
            market_slug=_slug(raw),
            side=str(raw.get("side") or raw.get("order_side") or ""),
            price=_decimal(raw.get("price"), "order.price"),
            original_qty=quantity,
            filled_qty=cumulative,
            status=state_map[state],
            event_at=event_at,
        )
    except ValueError as exc:
        raise AdapterError(f"order is invalid: {exc}") from exc


def normalize_execution(
    raw: Any,
    *,
    observed_at: datetime,
    resolve_client_id: ClientIdResolver | None = None,
) -> Execution:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("order"), Mapping):
        raise AdapterError("execution.order is missing")
    order = raw["order"]
    exchange_order_id = str(order.get("id") or "").strip()
    execution_id = str(
        raw.get("id") or _wire(raw, "tradeId", "trade_id") or "").strip()
    if not execution_id or not exchange_order_id:
        raise AdapterError("execution identity is incomplete")
    try:
        return Execution(
            execution_id=execution_id,
            client_order_id=_identity(
                exchange_order_id, resolve_client_id, order),
            exchange_order_id=exchange_order_id,
            market_slug=_slug(order),
            side=str(order.get("side") or ""),
            intent=str(order.get("intent") or ""),
            price=_decimal(
                _wire(raw, "lastPx", "last_px"), "execution.lastPx"),
            qty=_decimal(
                _wire(raw, "lastShares", "last_shares"),
                "execution.lastShares"),
            fee=_decimal(
                _wire(raw, "commissionNotionalCollected",
                      "commission_notional_collected")
                or {"value": "0"},
                "execution.commissionNotionalCollected"),
            liquidity="TAKER" if bool(raw.get("aggressor")) else "MAKER",
            event_at=_timestamp(
                                _wire(raw, "transactTime", "transact_time"),
                                "execution.transactTime",
                                fallback=observed_at),
        )
    except ValueError as exc:
        raise AdapterError(f"execution is invalid: {exc}") from exc


def normalize_position(
    slug: Any, raw: Any, *, observed_at: datetime,
) -> PositionState:
    if not isinstance(raw, Mapping):
        raise AdapterError("position is not an object")
    market_slug = str(
        slug or _wire(raw, "marketSlug", "market_slug") or "").strip()
    if not market_slug:
        raise AdapterError("position market slug is missing")
    # Use the exact decimal field. Never infer economic quantity from a
    # rounded integer or cost field.
    value = _wire(raw, "netPositionDecimal", "net_position_decimal")
    if value is None:
        raise AdapterError(
            "position.netPositionDecimal is required; integer netPosition is rounded")
    try:
        return PositionState(
            market_slug=market_slug,
            net_qty=_decimal(value, "position.netPosition"),
            event_at=_timestamp(
                                _wire(raw, "updateTime", "update_time"),
                                "position.updateTime",
                                fallback=observed_at),
        )
    except ValueError as exc:
        raise AdapterError(f"position is invalid: {exc}") from exc


def _event_id(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PrivateLedgerProjector:
    """Make private WebSocket events the primary durable execution feed."""

    def __init__(self, ledger: ExecutionLedger,
                 resolve_client_id: ClientIdResolver | None = None):
        self.ledger = ledger
        self.resolve_client_id = (
            resolve_client_id or ledger.resolve_client_order_id)
        self._order_pages: dict[str, list[OrderState]] = {}
        self._position_pages: dict[str, list[PositionState]] = {}

    def apply(self, event: PrivateEvent, *, received_at: datetime) -> None:
        received_at = _aware(received_at, "received_at")
        if event.kind in {PrivateEventKind.HEARTBEAT,
                          PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
                          PrivateEventKind.ACCOUNT_BALANCE_UPDATE}:
            return
        if event.kind == PrivateEventKind.ERROR:
            raise AdapterError(f"private WebSocket error: {event.payload}")
        request_id = str(event.request_id or "").strip()
        if not request_id:
            raise AdapterError("private state event has no request id")

        if event.kind == PrivateEventKind.ORDER_SNAPSHOT:
            payload = event.payload
            if not isinstance(payload, Mapping) or not isinstance(
                    payload.get("orders"), list) or not isinstance(payload.get("eof"), bool):
                raise AdapterError("order snapshot is incomplete")
            page = self._order_pages.setdefault(request_id, [])
            page.extend(normalize_order(
                row, observed_at=received_at,
                resolve_client_id=self.resolve_client_id)
                for row in payload["orders"])
            if payload["eof"]:
                self.ledger.apply_order_snapshot(
                    request_id, page, as_of=received_at,
                    observed_at=received_at, complete=True)
                del self._order_pages[request_id]
            return

        if event.kind == PrivateEventKind.ORDER_UPDATE:
            payload = event.payload
            raw = payload.get("execution") if isinstance(payload, Mapping) else None
            if not isinstance(raw, Mapping):
                raise AdapterError("order update has no execution")
            order = normalize_order(
                raw.get("order"), observed_at=received_at,
                resolve_client_id=self.resolve_client_id)
            execution_id = str(raw.get("id") or raw.get("tradeId") or "").strip()
            if not execution_id:
                raise AdapterError("order update execution has no id")
            self.ledger.apply_order_event(
                "private_ws", f"order:{execution_id}", order,
                received_at=received_at)
            execution_type = str(raw.get("type") or "").upper()
            if execution_type in {"EXECUTION_TYPE_PARTIAL_FILL", "EXECUTION_TYPE_FILL"}:
                self.ledger.apply_execution(normalize_execution(
                    raw, observed_at=received_at,
                    resolve_client_id=self.resolve_client_id),
                    received_at=received_at)
            return

        if event.kind == PrivateEventKind.POSITION_SNAPSHOT:
            payload = event.payload
            if not isinstance(payload, Mapping) or not isinstance(
                    payload.get("positions"), Mapping) or not isinstance(
                        payload.get("eof"), bool):
                raise AdapterError("position snapshot is incomplete")
            page = self._position_pages.setdefault(request_id, [])
            page.extend(normalize_position(slug, row, observed_at=received_at)
                        for slug, row in payload["positions"].items())
            if payload["eof"]:
                self.ledger.apply_position_snapshot(
                    request_id, page, as_of=received_at,
                    observed_at=received_at, complete_account=True)
                del self._position_pages[request_id]
            return

        if event.kind == PrivateEventKind.POSITION_UPDATE:
            payload = event.payload
            if not isinstance(payload, Mapping):
                raise AdapterError("position update is malformed")
            after = _wire(payload, "afterPosition", "after_position")
            raw = (after if isinstance(after, Mapping)
                   else payload.get("position") if isinstance(
                       payload.get("position"), Mapping) else payload)
            raw = dict(raw)
            update_time = _wire(payload, "updateTime", "update_time")
            if (update_time is not None
                    and _wire(raw, "updateTime", "update_time") is None):
                raw["updateTime"] = update_time
            slug = (_wire(payload, "marketSlug", "market_slug")
                    or _wire(raw, "marketSlug", "market_slug"))
            position = normalize_position(slug, raw, observed_at=received_at)
            self.ledger.apply_position_event(
                "private_ws", f"position:{_event_id(payload)}", position,
                received_at=received_at)
            return
        raise AdapterError(f"unsupported private event kind {event.kind}")
