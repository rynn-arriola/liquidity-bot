"""Strict asynchronous adapters for read-only Polymarket US resources.

No method in this module references the SDK's order resource. The injected
client only needs ``get`` and ``markets`` methods, which makes every network
path replaceable with deterministic fixtures.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from .errors import AdapterError
from .models import (
    BookLevel,
    BookSnapshot,
    MarketIncentive,
    MarketMetadata,
    RewardEarning,
    TradePrint,
)
from .rewards import IncentivePeriod

__all__ = [
    "AdapterError",
    "AsyncIntervalLimiter",
    "AsyncReadOnlyGateway",
    "parse_book_response",
    "parse_trade_response",
]


def _amount(value: Any, *, field: str) -> float:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool):
        raise AdapterError(f"{field} is not numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"{field} is not numeric") from exc
    if not math.isfinite(parsed):
        raise AdapterError(f"{field} is not finite")
    return parsed


def _timestamp(value: Any, *, field: str, required: bool = False) -> datetime | None:
    if value is None or str(value).strip() == "":
        if required:
            raise AdapterError(f"{field} is missing")
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AdapterError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(f"{field} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _period(row: Any) -> IncentivePeriod:
    if not isinstance(row, dict):
        raise AdapterError("incentive time period is not an object")
    program_id = str(row.get("programId") or "").strip()
    program_type = str(row.get("programType") or "").strip()
    period = str(row.get("period") or "").strip()
    status = str(row.get("status") or "").strip()
    if not program_id or not program_type or not period or not status:
        raise AdapterError("incentive period identity is incomplete")
    if program_type != "liquidityProgram":
        raise AdapterError(
            f"incentive period {program_id} is not a liquidity program")
    reward_pool = _amount(row.get("rewardPool"), field="rewardPool")
    discount = _amount(row.get("discountFactor"), field="discountFactor")
    target = _amount(row.get("targetSize"), field="targetSize")
    if reward_pool < 0 or not 0 < discount <= 1 or target <= 0:
        raise AdapterError("incentive period contains invalid economics")
    return IncentivePeriod(
        program_id=program_id,
        period=period,
        status=status,
        reward_pool=reward_pool,
        discount_factor=discount,
        target_size=target,
        starts_at=_timestamp(row.get("start"), field="period.start"),
        ends_at=_timestamp(row.get("end"), field="period.end"),
    )


def _market(row: Any, *, expected_slug: str | None = None,
            require_clock: bool = False) -> MarketMetadata:
    if not isinstance(row, dict):
        raise AdapterError("market is not an object")
    slug = str(row.get("slug") or "").strip()
    if not slug:
        raise AdapterError("market slug is missing")
    if expected_slug is not None and slug != expected_slug:
        raise AdapterError(f"market slug mismatch: expected {expected_slug}, got {slug}")
    clock = _timestamp(
        row.get("gameStartTime"), field="market.gameStartTime",
        required=require_clock)
    raw_tick = row.get("orderPriceMinTickSize")
    tick = None if raw_tick is None or str(raw_tick).strip() == "" \
        else _amount(raw_tick, field="market.orderPriceMinTickSize")
    if tick is not None and tick <= 0:
        raise AdapterError("market tick size must be positive")
    raw_minimum = row.get("minimumTradeQty")
    minimum = None if raw_minimum is None or str(raw_minimum).strip() == "" \
        else _amount(raw_minimum, field="market.minimumTradeQty")
    if minimum is not None and minimum <= 0:
        raise AdapterError("market minimum trade quantity must be positive")
    return MarketMetadata(
        slug=slug,
        game_start=clock,
        tick_size=tick,
        active=bool(row.get("active", False)),
        closed=bool(row.get("closed", False)),
        status=str(row.get("status")) if row.get("status") is not None else None,
        sports_market_type=(str(row.get("sportsMarketType"))
                            if row.get("sportsMarketType") is not None else None),
        question=str(row.get("question")) if row.get("question") is not None else None,
        minimum_trade_qty=minimum,
    )


def _book_level(row: Any, *, side: str, index: int) -> BookLevel:
    if not isinstance(row, dict):
        raise AdapterError(f"{side}[{index}] is not an object")
    price = _amount(row.get("px"), field=f"{side}[{index}].px")
    size = _amount(row.get("qty"), field=f"{side}[{index}].qty")
    if not 0 < price < 1 or size <= 0:
        raise AdapterError(f"{side}[{index}] has an invalid price or size")
    return BookLevel(price=price, size=size)


def _wire_value(row: dict[str, Any], camel: str, snake: str) -> Any:
    """Read one documented camelCase/snake_case wire-field variant."""

    return row[camel] if camel in row else row.get(snake)


def parse_book_response(response: Any, *, expected_slug: str | None = None) -> BookSnapshot:
    """Normalize the REST or WebSocket full-book wrapper."""

    if not isinstance(response, dict):
        raise AdapterError("book response is not an object")
    raw_data = _wire_value(response, "marketData", "market_data")
    if not isinstance(raw_data, dict):
        raise AdapterError("book response has no marketData wrapper")
    data = raw_data
    actual_slug = str(_wire_value(data, "marketSlug", "market_slug") or "").strip()
    if not actual_slug:
        raise AdapterError("book market slug is missing")
    if expected_slug is not None and actual_slug != expected_slug:
        raise AdapterError(
            f"book slug mismatch: expected {expected_slug}, got {actual_slug}")
    if not isinstance(data.get("bids"), list) or not isinstance(data.get("offers"), list):
        raise AdapterError("book sides are not lists")
    bids = tuple(_book_level(row, side="bids", index=index)
                 for index, row in enumerate(data["bids"]))
    offers = tuple(_book_level(row, side="offers", index=index)
                   for index, row in enumerate(data["offers"]))
    if not bids or not offers:
        raise AdapterError("book is not two-sided")
    return BookSnapshot(
        slug=actual_slug,
        bids=bids,
        offers=offers,
        state=str(data.get("state")) if data.get("state") is not None else None,
        transact_time=_timestamp(
            _wire_value(data, "transactTime", "transact_time"),
            field="book.transactTime"),
    )


def parse_trade_response(response: Any, *, expected_slug: str | None = None) -> TradePrint:
    """Normalize the public trade WebSocket wrapper.

    A trade is an observed print, not proof that a nonexistent shadow order
    filled. Phase 2 consumes it only as a conservative fill-model signal.
    """

    if not isinstance(response, dict) or not isinstance(response.get("trade"), dict):
        raise AdapterError("trade response has no trade wrapper")
    data = response["trade"]
    slug = str(_wire_value(data, "marketSlug", "market_slug") or "").strip()
    if not slug:
        raise AdapterError("trade market slug is missing")
    if expected_slug is not None and slug != expected_slug:
        raise AdapterError(f"trade slug mismatch: expected {expected_slug}, got {slug}")
    price = _amount(data.get("price"), field="trade.price")
    quantity = _amount(data.get("quantity"), field="trade.quantity")
    maker = data.get("maker")
    taker = data.get("taker")
    if not isinstance(maker, dict) or not isinstance(taker, dict):
        raise AdapterError("trade sides are missing")
    maker_side = str(maker.get("side") or "").strip()
    if maker_side not in {"ORDER_SIDE_BUY", "ORDER_SIDE_SELL"}:
        raise AdapterError("trade maker side is invalid")
    if not 0 < price < 1 or quantity <= 0:
        raise AdapterError("trade price or quantity is invalid")
    return TradePrint(
        slug=slug,
        price=price,
        quantity=quantity,
        maker_side=maker_side,
        trade_time=_timestamp(
            _wire_value(data, "tradeTime", "trade_time"),
            field="trade.tradeTime", required=True),
        request_id=(str(_wire_value(response, "requestId", "request_id"))
                    if _wire_value(response, "requestId", "request_id")
                    is not None else None),
        maker_intent=(str(maker.get("intent"))
                      if maker.get("intent") is not None else None),
        taker_side=(str(taker.get("side")) if taker.get("side") is not None else None),
        taker_intent=(str(taker.get("intent"))
                      if taker.get("intent") is not None else None),
    )


class AsyncIntervalLimiter:
    """One non-blocking request gate shared by a gateway instance."""

    def __init__(self, minimum_interval_seconds: float = 0.0):
        if minimum_interval_seconds < 0:
            raise ValueError("minimum interval cannot be negative")
        self.minimum_interval_seconds = float(minimum_interval_seconds)
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    @classmethod
    def from_rate(cls, requests_per_second: float) -> "AsyncIntervalLimiter":
        rate = float(requests_per_second)
        # The current documented retail ceiling is 20/s. Reject a higher
        # configured rate here instead of relying on the venue to police us.
        if not 0 < rate <= 20:
            raise ValueError("requests_per_second must be in (0, 20]")
        return cls(1.0 / rate)

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                await asyncio.sleep(self._next_at - now)
                now = time.monotonic()
            self._next_at = now + self.minimum_interval_seconds


class AsyncReadOnlyGateway:
    """Validated access to catalog, market, book, incentive, and earnings data."""

    def __init__(
        self,
        client: Any,
        *,
        limiter: AsyncIntervalLimiter | None = None,
        incentive_batch_size: int = 40,
        catalog_page_size: int = 500,
        max_pages: int = 100,
        retry_attempts: int = 3,
        retry_base_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        if not 1 <= incentive_batch_size <= 100:
            raise ValueError("incentive_batch_size must be in [1, 100]")
        if not 1 <= catalog_page_size <= 500:
            raise ValueError("catalog_page_size must be in [1, 500]")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if retry_attempts <= 0 or retry_base_seconds < 0:
            raise ValueError("retry settings are invalid")
        self.client = client
        self.limiter = limiter or AsyncIntervalLimiter()
        self.incentive_batch_size = incentive_batch_size
        self.catalog_page_size = catalog_page_size
        self.max_pages = max_pages
        self.retry_attempts = retry_attempts
        self.retry_base_seconds = retry_base_seconds
        self._sleep = sleep

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        raw = getattr(exc, "status_code", None)
        if raw is None:
            raw = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    async def _call(self, operation: str, call: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(self.retry_attempts):
            await self.limiter.wait()
            try:
                return await call()
            except AdapterError:
                raise
            except Exception as exc:
                # Retry only a confirmed HTTP 429. Polymarket documents that
                # requests delayed over five seconds can return the text
                # "Global Rate Limit Exceeded" even though it is not a rate
                # limit; slowing down in response makes that failure worse.
                if self._status_code(exc) != 429 or attempt + 1 >= self.retry_attempts:
                    raise AdapterError(
                        f"{operation} failed: {type(exc).__name__}: {exc}") from exc
                await self._sleep(self.retry_base_seconds * (2 ** attempt))
        raise AssertionError("unreachable retry loop")

    async def fetch_incentives(
        self, symbols: Iterable[str]
    ) -> dict[str, MarketIncentive]:
        clean = list(dict.fromkeys(str(symbol).strip() for symbol in symbols
                                   if str(symbol).strip()))
        found_periods: dict[str, list[IncentivePeriod]] = {}
        reported_clocks: dict[str, datetime | None] = {}
        for offset in range(0, len(clean), self.incentive_batch_size):
            chunk = clean[offset:offset + self.incentive_batch_size]
            token: str | None = None
            seen_tokens: set[str] = set()
            for _ in range(self.max_pages):
                # Incentive query parameters are snake_case. The API silently
                # ignores camelCase query keys, which previously made our
                # requested page size ineffective.
                query: dict[str, Any] = {
                    "symbols": chunk,
                    "page_size": 100,
                    "program_type": "liquidityProgram",
                }
                if token:
                    query["page_token"] = token
                response = await self._call(
                    "GET /v1/incentives",
                    lambda query=query: self.client.get(
                        "/v1/incentives", query=query, authenticated=True),
                )
                if not isinstance(response, dict) or not isinstance(
                        response.get("programs"), list):
                    raise AdapterError("incentives response has no programs list")
                for row in response["programs"]:
                    if not isinstance(row, dict):
                        raise AdapterError("incentive program is not an object")
                    slug = str(row.get("marketSlug") or "").strip()
                    periods = row.get("timePeriods")
                    if not slug or not isinstance(periods, list):
                        raise AdapterError("incentive program identity is incomplete")
                    liquidity_periods = []
                    for item in periods:
                        if not isinstance(item, dict):
                            raise AdapterError("incentive time period is not an object")
                        program_type = str(item.get("programType") or "").strip()
                        if not program_type:
                            raise AdapterError("incentive period programType is missing")
                        if program_type == "liquidityProgram":
                            liquidity_periods.append(_period(item))
                    if liquidity_periods:
                        found_periods.setdefault(slug, []).extend(liquidity_periods)
                    reported_clocks[slug] = _timestamp(
                        row.get("eventStartTime"), field="incentive.eventStartTime")
                next_token = response.get("nextPageToken")
                if next_token is None or str(next_token).strip() == "":
                    break
                next_token = str(next_token)
                if next_token in seen_tokens:
                    raise AdapterError("incentives pagination repeated a page token")
                seen_tokens.add(next_token)
                token = next_token
            else:
                raise AdapterError("incentives pagination exceeded max_pages")

        return {
            slug: MarketIncentive(
                slug=slug,
                periods=tuple(periods),
                reported_event_start=reported_clocks.get(slug),
            )
            for slug, periods in found_periods.items()
        }

    async def fetch_earnings(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        market_slug: str | None = None,
        program_type: str | None = None,
    ) -> tuple[RewardEarning, ...]:
        query = {
            key: value for key, value in {
                # The live Retail API currently honors snake_case query keys;
                # its published camelCase examples are silently ignored.
                "start_date": start_date,
                "end_date": end_date,
                "market_slug": market_slug,
                "program_type": program_type,
            }.items() if value is not None and str(value).strip()
        }
        response = await self._call(
            "GET /v1/incentives/earnings",
            lambda: self.client.get(
                "/v1/incentives/earnings",
                query=query or None,
                authenticated=True),
        )
        if not isinstance(response, dict) or not isinstance(response.get("rewards"), list):
            raise AdapterError("earnings response has no rewards list")
        out: list[RewardEarning] = []
        for row in response["rewards"]:
            if not isinstance(row, dict):
                raise AdapterError("earnings row is not an object")
            date = str(row.get("date") or "").strip()
            slug = str(row.get("marketSlug") or "").strip()
            status = str(row.get("status") or "").strip().upper()
            program_type = str(row.get("programType") or "").strip()
            reward = _amount(row.get("reward"), field="earning.reward")
            if not date or not slug or not status or not program_type or reward < 0:
                raise AdapterError("earnings row is incomplete")
            out.append(RewardEarning(date, slug, program_type, reward, status))
        return tuple(out)

    async def fetch_market(self, slug: str) -> MarketMetadata:
        response = await self._call(
            f"markets.retrieve_by_slug({slug})",
            lambda: self.client.markets.retrieve_by_slug(slug),
        )
        if not isinstance(response, dict) or not isinstance(response.get("market"), dict):
            raise AdapterError("market response has no market wrapper")
        return _market(response["market"], expected_slug=slug, require_clock=True)

    async def fetch_book(self, slug: str) -> BookSnapshot:
        response = await self._call(
            f"markets.book({slug})", lambda: self.client.markets.book(slug))
        return parse_book_response(response, expected_slug=slug)

    async def list_sports_markets(self) -> tuple[MarketMetadata, ...]:
        out: list[MarketMetadata] = []
        seen: set[str] = set()
        for page_number in range(self.max_pages):
            offset = page_number * self.catalog_page_size
            params = {
                "categories": ["sports"],
                "active": True,
                "closed": False,
                "limit": self.catalog_page_size,
                "offset": offset,
            }
            response = await self._call(
                f"markets.list(offset={offset})",
                lambda params=params: self.client.markets.list(params),
            )
            if not isinstance(response, dict) or not isinstance(response.get("markets"), list):
                raise AdapterError("market catalog response has no markets list")
            page = response["markets"]
            for row in page:
                try:
                    item = _market(row, require_clock=False)
                except AdapterError:
                    # One malformed candidate cannot authorize risk and is
                    # excluded. The page itself remains usable and complete.
                    continue
                if item.slug not in seen:
                    seen.add(item.slug)
                    out.append(item)
            if len(page) < self.catalog_page_size:
                return tuple(out)
        raise AdapterError("market catalog pagination exceeded max_pages")
