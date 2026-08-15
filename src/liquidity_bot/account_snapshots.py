"""Read-only startup/reconnect snapshots sharing the global REST governor."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError
from .execution_ledger import OrderState, PositionState
from .models import AccountBalance
from .execution_ledger import Execution
from .venue_state import (
    ClientIdResolver, normalize_execution, normalize_order, normalize_position)


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

    async def fetch_executions(
        self, *, observed_at: datetime,
    ) -> tuple[Execution, ...]:
        """Every fill the venue has recorded for this account.

        A third source for fills, and the only one that survives a restart.
        The other two are the order-create response, which carries executions
        only when the match completes before it returns, and the private
        WebSocket. A shutdown IOC satisfies neither: it returns before its
        match completes, and the fill then arrives on a socket that shutdown
        is closing. Measured 2026-08-15: three closing fills were never
        recorded, so realized_pnl read a $1.55 loss as a $3.26 gain and the $40
        stop could not see it.

        Replaying is safe. apply_execution dedupes on execution_id through
        _accept_event, so re-applying a fill already recorded is a no-op.
        """

        rows: dict[str, Execution] = {}
        cursor: str | None = None
        for _ in range(self.gateway.max_pages):
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            response = await self.gateway._call(
                "GET /v1/portfolio/activities",
                lambda params=params: self.client.portfolio.activities(params),
            )
            if not isinstance(response, dict) or not isinstance(
                    response.get("activities"), list):
                raise AdapterError("activity response has no activities list")
            for activity in response["activities"]:
                if not isinstance(activity, dict):
                    continue
                trade = activity.get("trade")
                if not isinstance(trade, dict):
                    continue
                legs = [trade.get("aggressorExecution")]
                maker = trade.get("makerExecutions")
                if isinstance(maker, list):
                    legs.extend(maker)
                for leg in legs:
                    if not isinstance(leg, dict):
                        continue
                    if str(leg.get("type") or "").upper() not in {
                            "EXECUTION_TYPE_FILL",
                            "EXECUTION_TYPE_PARTIAL_FILL"}:
                        continue
                    try:
                        execution = normalize_execution(
                            leg, observed_at=observed_at,
                            resolve_client_id=self.resolve_client_id)
                    except AdapterError:
                        # A leg belonging to the counterparty carries no order
                        # we can identify. Skipping it is correct; failing the
                        # whole sweep over it would lose our own fills too.
                        continue
                    rows[execution.execution_id] = execution
            cursor = str(response.get("nextCursor") or "").strip() or None
            if not cursor or response.get("eof"):
                break
        return tuple(rows.values())

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
