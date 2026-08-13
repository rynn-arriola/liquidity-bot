"""Read-only startup/reconnect snapshots sharing the global REST governor."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError
from .execution_ledger import OrderState, PositionState
from .models import AccountBalance
from .venue_state import ClientIdResolver, normalize_order, normalize_position


class AsyncAccountSnapshotGateway:
    """Order/position reads only; no mutation method exists in this class."""

    def __init__(self, gateway: AsyncReadOnlyGateway,
                 resolve_client_id: ClientIdResolver | None = None):
        self.gateway = gateway
        self.client = gateway.client
        self.resolve_client_id = resolve_client_id

    async def fetch_open_orders(
        self, *, slugs: Iterable[str] = (), observed_at: datetime,
    ) -> tuple[OrderState, ...]:
        clean = list(dict.fromkeys(str(slug).strip() for slug in slugs
                                   if str(slug).strip()))
        params = {"slugs": clean} if clean else None
        response = await self.gateway._call(
            "GET /v1/orders/open",
            lambda: self.client.orders.list(params),
        )
        if not isinstance(response, dict) or not isinstance(
                response.get("orders"), list):
            raise AdapterError("open-order response has no orders list")
        return tuple(normalize_order(
            row, observed_at=observed_at,
            resolve_client_id=self.resolve_client_id)
            for row in response["orders"])

    async def fetch_positions(
        self, *, observed_at: datetime,
    ) -> tuple[PositionState, ...]:
        rows: dict[str, PositionState] = {}
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self.gateway.max_pages):
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            response = await self.gateway._call(
                "GET /v1/portfolio/positions",
                lambda params=params: self.client.portfolio.positions(params),
            )
            if not isinstance(response, dict) or not isinstance(
                    response.get("positions"), dict):
                raise AdapterError("position response has no positions map")
            for slug, raw in response["positions"].items():
                rows[str(slug)] = normalize_position(
                    slug, raw, observed_at=observed_at)
            eof = response.get("eof")
            next_cursor = response.get("nextCursor")
            if eof is True:
                return tuple(rows.values())
            if eof is not False:
                raise AdapterError("position pagination has no explicit eof")
            if next_cursor in (None, ""):
                raise AdapterError("position pagination ended without eof")
            if not isinstance(next_cursor, str) or next_cursor in seen:
                raise AdapterError("position pagination cursor is invalid or repeated")
            seen.add(next_cursor)
            cursor = next_cursor
        raise AdapterError("position pagination exceeded max_pages")

    async def fetch_balance(self, *, observed_at: datetime) -> AccountBalance:
        response = await self.gateway._call(
            "GET /v1/account/balances", self.client.account.balances)
        if not isinstance(response, dict) or not isinstance(
                response.get("balances"), list):
            raise AdapterError("account balance response has no balances list")
        usd = [row for row in response["balances"]
               if isinstance(row, dict)
               and str(row.get("currency") or "").upper() == "USD"]
        if len(usd) != 1:
            raise AdapterError("account balance must contain exactly one USD row")

        def amount(name: str) -> Decimal:
            try:
                value = Decimal(str(usd[0][name]))
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise AdapterError(f"account balance {name} is invalid") from exc
            if not value.is_finite():
                raise AdapterError(f"account balance {name} is not finite")
            return value

        return AccountBalance(
            current_balance=amount("currentBalance"),
            asset_notional=amount("assetNotional"),
            buying_power=amount("buyingPower"),
            observed_at=observed_at,
        )
