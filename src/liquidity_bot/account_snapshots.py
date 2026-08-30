"""Read-only startup/reconnect snapshots sharing the global REST governor."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError
from .execution_ledger import OrderState, PositionState, TradeStateObservation
from .models import AccountBalance
from .execution_ledger import Execution
from .venue_state import (
    ClientIdResolver, normalize_execution, normalize_order, normalize_position)


@dataclass(frozen=True)
class ExecutionHistoryBatch:
    """Positive fill legs plus independent lifecycle state observations."""

    executions: tuple[Execution, ...]
    trade_states: tuple[TradeStateObservation, ...]

    def __iter__(self) -> Iterator[Execution]:
        return iter(self.executions)

    def __len__(self) -> int:
        return len(self.executions)


@dataclass(frozen=True)
class AccountEquitySnapshot:
    """One cash-stable account mark built from documented Retail fields.

    Authenticated production evidence on 2026-08-30 showed
    ``assetNotional == 0`` while `/v1/portfolio/positions` simultaneously
    returned nonzero positions with nonzero ``cashValue``.  Therefore the
    account mark is fiat cash plus the sum of each open position's cash value,
    not ``currentBalance + assetNotional``.

    ``position_basis`` is optional because marked equity does not need basis.
    Realised/open-unrealised decomposition does, so those consumers fail
    closed when any open position omits cost.
    """

    balance: AccountBalance
    positions: tuple[PositionState, ...]
    position_value: Decimal
    position_basis: Decimal | None
    observed_at: datetime

    @property
    def marked_equity(self) -> Decimal:
        return self.balance.current_balance + self.position_value

    def marked_trading_pnl(
        self, *, opening_equity: Decimal, nontrading_cash: Decimal,
    ) -> Decimal:
        return (self.marked_equity - opening_equity - nontrading_cash)

    def realized_trading_pnl(
        self, *, opening_cash: Decimal, nontrading_cash: Decimal,
    ) -> Decimal | None:
        if self.position_basis is None:
            return None
        return (self.balance.current_balance - opening_cash
                - nontrading_cash + self.position_basis)

    def open_unrealized_pnl(self) -> Decimal | None:
        if self.position_basis is None:
            return None
        return self.position_value - self.position_basis


def account_equity_snapshot(
    balance: AccountBalance,
    positions: tuple[PositionState, ...],
    *,
    observed_at: datetime,
) -> AccountEquitySnapshot:
    """Validate and compose one already cash-bracketed account snapshot."""

    value = Decimal("0")
    basis = Decimal("0")
    basis_known = True
    for position in positions:
        if position.net_qty == 0:
            continue
        if position.cash_value is None:
            raise AdapterError(
                "open position has no venue cashValue: "
                f"{position.market_slug}")
        if position.cash_value < 0:
            raise AdapterError(
                "open position cashValue is negative: "
                f"{position.market_slug}")
        value += position.cash_value
        if position.cost is None:
            basis_known = False
        elif position.cost < 0:
            raise AdapterError(
                "open position basis is negative: "
                f"{position.market_slug}")
        else:
            basis += position.cost
    return AccountEquitySnapshot(
        balance=balance,
        positions=positions,
        position_value=value,
        position_basis=basis if basis_known else None,
        observed_at=observed_at,
    )


def _activity_time(value: Any, *, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError("trade activity updateTime is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError("trade activity updateTime must be timezone-aware")
    return parsed.astimezone(timezone.utc)


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
        self, *, observed_at: datetime, max_pages: int = 2,
    ) -> ExecutionHistoryBatch:
        """Recent fills still retained by the venue for this account.

        A third source for fills, and a restart backfill only while the venue
        still retains the applicable execution history.  Polymarket may prune
        or explicitly reset that history (changelog v0.0.79 did so during the
        2026-08-27 maintenance window).  The durable local execution ledger is
        the only history this project controls across both process restarts and
        venue-history resets.  An empty activity response is therefore never
        proof that no fill occurred, that an order was canceled, or that the
        account is flat.

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
        trade_states: dict[str, TradeStateObservation] = {}
        cursor: str | None = None
        # Deliberately shallow. This runs on every reconcile and shares the one
        # 4 req/s governor with book refreshes and order placement, so walking
        # the whole history would spend the budget quotes need -- and there is
        # no point during normal operation: anything older that we observed is
        # already recorded locally, and apply_execution dedupes. Two pages is
        # recent activity with headroom for a burst. A venue reset can make
        # this legitimately empty; callers may use it only as positive fill
        # evidence, never as a complete negative snapshot.
        for _ in range(max(1, min(max_pages, self.gateway.max_pages))):
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
                trade_id = str(trade.get("id") or "").strip()
                trade_state = str(trade.get("state") or "").strip().upper()
                if trade_state and not trade_state.startswith("TRADE_STATE_"):
                    trade_state = "TRADE_STATE_" + trade_state
                if trade_id and trade_state:
                    market_slug = str(
                        trade.get("marketSlug")
                        or trade.get("market_slug") or "").strip()
                    if market_slug:
                        update_at = _activity_time(
                            trade.get("updateTime", trade.get("update_time")),
                            fallback=observed_at)
                        prior = trade_states.get(trade_id)
                        if prior is None or update_at >= prior.update_at:
                            trade_states[trade_id] = TradeStateObservation(
                                trade_id=trade_id,
                                market_slug=market_slug,
                                state=trade_state,
                                update_at=update_at)
                # A trade has two legs and the venue reports BOTH -- ours and
                # the counterparty's. `isAggressor` says which one is ours,
                # and it is exact: checked against our own order ids over 25
                # consecutive trades on 2026-08-17, True matched the aggressor
                # leg 10/10 and False matched the passive leg 15/15.
                #
                # This used to read `aggressorExecution` plus `makerExecutions`
                # -- a key the venue does not publish; the passive leg is
                # `passiveExecution`. So whenever WE were the passive side, our
                # execution was never read at all and the counterparty's
                # aggressor leg was read instead, then discarded for not
                # matching one of our orders. **60% of fills are passive** (15
                # of those same 25), and passive is the normal case for a
                # market maker, so most fills reached the ledger only if the
                # private WebSocket happened to deliver them -- which is
                # exactly what a restart or a socket gap loses.
                #
                # That single defect is why realized_pnl read -$143.59 against
                # a real -$48.69, why 27 markets held an entry and no exit, and
                # why markets showed a TAKER first fill that a post-only entry
                # cannot produce. See NEXT item 1b.
                leg = (trade.get("aggressorExecution")
                       if trade.get("isAggressor")
                       else trade.get("passiveExecution"))
                if isinstance(leg, dict):
                    if str(leg.get("type") or "").upper() in {
                            "EXECUTION_TYPE_FILL",
                            "EXECUTION_TYPE_PARTIAL_FILL"}:
                        try:
                            execution = normalize_execution(
                                leg, observed_at=observed_at,
                                resolve_client_id=self.resolve_client_id)
                            execution = replace(
                                execution,
                                trade_id=trade_id or execution.trade_id,
                                trade_state=trade_state or None)
                        except AdapterError:
                            # Malformed rather than foreign. Failing the whole
                            # sweep over one leg would lose our own fills too.
                            execution = None
                        if execution is not None:
                            # Deliberately NOT filtered on `venue:` any more.
                            # Ownership is now established by `isAggressor`,
                            # which is a fact about the trade; `venue:` only
                            # means we hold no order record, which is also true
                            # of an operator's manual close and of any order
                            # whose record was lost. Dropping those discarded
                            # our own fills -- the original double-counting bug
                            # is prevented by taking exactly one leg here.
                            rows[execution.execution_id] = execution
            cursor = str(response.get("nextCursor") or "").strip() or None
            if not cursor or response.get("eof"):
                break
        return ExecutionHistoryBatch(
            tuple(rows.values()), tuple(trade_states.values()))

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

    async def fetch_equity_snapshot(
        self, *, observed_at: datetime,
    ) -> AccountEquitySnapshot:
        """Bracket a complete position mark with equal fiat-cash reads.

        A fill between the three reads changes fiat cash.  Mixing the older
        position page with the newer balance would manufacture P&L, so the
        snapshot is rejected.  Market-price movement may legitimately change
        ``cashValue`` after the position response; ``observed_at`` states the
        mark's age and normal risk freshness bounds it.
        """

        first = await self.fetch_balance(observed_at=observed_at)
        positions = await self.fetch_positions(observed_at=observed_at)
        second = await self.fetch_balance(observed_at=observed_at)
        if first.current_balance != second.current_balance:
            raise AdapterError(
                "settled cash changed during account equity snapshot")

        return account_equity_snapshot(
            second, positions, observed_at=observed_at)
