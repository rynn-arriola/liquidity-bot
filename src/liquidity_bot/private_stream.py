"""Strict decoder for authenticated private WebSocket messages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import AdapterError


class PrivateEventKind(str, Enum):
    ORDER_SNAPSHOT = "order_snapshot"
    ORDER_UPDATE = "order_update"
    POSITION_SNAPSHOT = "position_snapshot"
    POSITION_UPDATE = "position_update"
    ACCOUNT_BALANCE_SNAPSHOT = "account_balance_snapshot"
    ACCOUNT_BALANCE_UPDATE = "account_balance_update"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


@dataclass(frozen=True)
class PrivateEvent:
    kind: PrivateEventKind
    payload: Any
    request_id: str | None = None
    subscription_type: str | None = None
    market_scope: tuple[str, ...] | None = None


_PAYLOADS = (
    (PrivateEventKind.ORDER_SNAPSHOT,
     ("orderSubscriptionSnapshot", "ordersSnapshot",
      "order_subscription_snapshot", "orders_snapshot")),
    (PrivateEventKind.ORDER_UPDATE,
     ("orderSubscriptionUpdate", "orderUpdate",
      "order_subscription_update", "order_update")),
    (PrivateEventKind.POSITION_SNAPSHOT,
     ("positionSubscriptionSnapshot", "positionsSnapshot",
      "position_subscription_snapshot", "positions_snapshot")),
    (PrivateEventKind.POSITION_UPDATE,
     ("positionSubscriptionUpdate", "positionUpdate", "positionSubscription",
      "position_subscription_update", "position_update", "position_subscription")),
    (PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
     ("accountBalanceSubscriptionSnapshot", "accountBalancesSnapshot",
      "account_balance_subscription_snapshot", "account_balances_snapshot")),
    (PrivateEventKind.ACCOUNT_BALANCE_UPDATE,
     ("accountBalanceSubscriptionUpdate", "accountBalanceUpdate",
      "accountBalancesUpdate", "account_balance_subscription_update",
      "account_balance_update", "account_balances_update")),
)


def decode_private_message(message: Any) -> PrivateEvent:
    if not isinstance(message, dict):
        raise AdapterError("private WebSocket message is not an object")
    raw_request_id = message.get("requestId", message.get("request_id"))
    raw_subscription = message.get(
        "subscriptionType", message.get("subscription_type"))
    request_id = str(raw_request_id) if raw_request_id is not None else None
    subscription = (str(raw_subscription)
                    if raw_subscription is not None else None)
    if "error" in message:
        return PrivateEvent(PrivateEventKind.ERROR, message["error"],
                            request_id, subscription)
    if "heartbeat" in message:
        return PrivateEvent(PrivateEventKind.HEARTBEAT, message["heartbeat"],
                            request_id, subscription)
    for kind, keys in _PAYLOADS:
        for key in keys:
            if key in message:
                payload = message[key]
                if not isinstance(payload, (dict, list)):
                    raise AdapterError(f"{key} payload is malformed")
                return PrivateEvent(kind, payload, request_id, subscription)
    raise AdapterError("unrecognized private WebSocket message schema")
