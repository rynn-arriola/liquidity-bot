"""Queue-based, read-only WebSocket adapters.

The adapters subscribe to market data and private account state. They expose
normalized events to the application but contain no order creation, mutation,
or cancellation method.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Any, Iterable

from .adapters import parse_book_response, parse_trade_response
from .errors import AdapterError
from .models import BookSnapshot, TradePrint
from .private_stream import PrivateEvent, decode_private_message


class MarketReadOnlyStream:
    """Full-book snapshots for an explicit set of subscribed markets."""

    def __init__(self, client: Any):
        self._socket = client.ws.markets()
        self._queue: asyncio.Queue[BookSnapshot | Exception] = asyncio.Queue()
        self._slugs: set[str] = set()
        self._started = False
        self._socket.on("market_data", self._on_market_data)
        self._socket.on("error", self._on_error)
        self._socket.on("close", self._on_close)

    def _on_market_data(self, message: Any) -> None:
        try:
            book = parse_book_response(message)
            if book.slug not in self._slugs:
                raise AdapterError(
                    f"received market data for unsubscribed slug {book.slug}")
            self._queue.put_nowait(book)
        except Exception as exc:
            self._queue.put_nowait(
                exc if isinstance(exc, AdapterError)
                else AdapterError(f"market stream decode failed: {exc}"))

    def _on_error(self, error: Any) -> None:
        self._queue.put_nowait(AdapterError(f"market WebSocket error: {error}"))

    def _on_close(self, *_: Any) -> None:
        self._queue.put_nowait(AdapterError("market WebSocket disconnected"))

    async def start(self, slugs: Iterable[str]) -> None:
        if self._started:
            raise RuntimeError("market stream is already started")
        clean = list(dict.fromkeys(str(slug).strip() for slug in slugs
                                   if str(slug).strip()))
        if not clean:
            raise ValueError("at least one market slug is required")
        self._slugs = set(clean)
        try:
            await self._socket.connect()
            request_id = f"liquidity-books-{uuid.uuid4().hex}"
            await self._socket.subscribe_market_data(request_id, clean)
        except Exception as exc:
            self._slugs.clear()
            try:
                await self._socket.close()
            except Exception:
                pass
            raise AdapterError(f"market WebSocket startup failed: {exc}") from exc
        self._started = True

    async def next_book(self, *, timeout: float | None = None) -> BookSnapshot:
        item = (await self._queue.get() if timeout is None
                else await asyncio.wait_for(self._queue.get(), timeout))
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        try:
            await self._socket.close()
        finally:
            self._started = False
            self._slugs.clear()


class PrivateReadOnlyStream:
    """Authenticated order, position, and balance state events."""

    def __init__(self, client: Any):
        self._socket = client.ws.private()
        self._queue: asyncio.Queue[PrivateEvent | Exception] = asyncio.Queue()
        self._started = False
        self._market_scope_by_request_id: dict[str, tuple[str, ...] | None] = {}
        # The installed SDK emits every decoded JSON object on `message`
        # before its convenience event. Register once to avoid duplicates.
        self._socket.on("message", self._on_message)
        self._socket.on("error", self._on_error)
        self._socket.on("close", self._on_close)

    def _on_message(self, message: Any) -> None:
        try:
            event = decode_private_message(message)
            if event.request_id in self._market_scope_by_request_id:
                event = replace(
                    event,
                    market_scope=self._market_scope_by_request_id[event.request_id],
                )
            self._queue.put_nowait(event)
        except Exception as exc:
            self._queue.put_nowait(
                exc if isinstance(exc, AdapterError)
                else AdapterError(f"private stream decode failed: {exc}"))

    def _on_error(self, error: Any) -> None:
        self._queue.put_nowait(AdapterError(f"private WebSocket error: {error}"))

    def _on_close(self, *_: Any) -> None:
        self._queue.put_nowait(AdapterError("private WebSocket disconnected"))

    async def start(self, slugs: Iterable[str] | None = None) -> None:
        if self._started:
            raise RuntimeError("private stream is already started")
        clean = None
        if slugs is not None:
            clean = list(dict.fromkeys(str(slug).strip() for slug in slugs
                                       if str(slug).strip()))
            if not clean:
                raise ValueError("an explicit market filter cannot be empty")
        base = uuid.uuid4().hex
        balance_request_id = f"liquidity-balance-{base}"
        scopes: dict[str, tuple[str, ...] | None] = {balance_request_id: None}
        planned: list[tuple[str, str, list[str] | None]] = []
        if clean is None:
            order_request_id = f"liquidity-orders-{base}"
            position_request_id = f"liquidity-positions-{base}"
            scopes[order_request_id] = None
            scopes[position_request_id] = None
            planned.append(("orders", order_request_id, None))
            planned.append(("positions", position_request_id, None))
        else:
            # One subscription per market, never one covering several. The
            # documented positionSubscription update omits marketSlug, and the
            # projector may only infer it when the request itself covers
            # exactly one market. A single multi-slug subscription would make
            # every such update ambiguous again, which is the defect that
            # ended the 2026-08-13T05:39Z session on its first fill.
            for index, slug in enumerate(clean):
                order_request_id = f"liquidity-orders-{base}-{index}"
                position_request_id = f"liquidity-positions-{base}-{index}"
                scopes[order_request_id] = (slug,)
                scopes[position_request_id] = (slug,)
                planned.append(("orders", order_request_id, [slug]))
                planned.append(("positions", position_request_id, [slug]))
        # Install request metadata before subscribing because an initial
        # snapshot can arrive as soon as the subscribe call is awaited.
        self._market_scope_by_request_id = scopes
        try:
            await self._socket.connect()
            for kind, request_id, market_filter in planned:
                if kind == "orders":
                    await self._socket.subscribe_orders(
                        request_id, market_filter)
                else:
                    await self._socket.subscribe_positions(
                        request_id, market_filter)
            await self._socket.subscribe_account_balance(balance_request_id)
        except Exception as exc:
            self._market_scope_by_request_id.clear()
            try:
                await self._socket.close()
            except Exception:
                pass
            raise AdapterError(f"private WebSocket startup failed: {exc}") from exc
        self._started = True

    async def next_event(self, *, timeout: float | None = None) -> PrivateEvent:
        item = (await self._queue.get() if timeout is None
                else await asyncio.wait_for(self._queue.get(), timeout))
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        try:
            await self._socket.close()
        finally:
            self._started = False
            self._market_scope_by_request_id.clear()


class ShadowMarketStream:
    """Ordered full-book and public-trade events for shadow evaluation."""

    def __init__(self, client: Any):
        self._socket = client.ws.markets()
        self._queue: asyncio.Queue[BookSnapshot | TradePrint | Exception] = asyncio.Queue()
        self._slugs: set[str] = set()
        self._started = False
        self._socket.on("market_data", self._on_book)
        self._socket.on("trade", self._on_trade)
        self._socket.on("error", self._on_error)
        self._socket.on("close", self._on_close)

    def _put(self, item: BookSnapshot | TradePrint | Exception) -> None:
        self._queue.put_nowait(item)

    def _on_book(self, message: Any) -> None:
        try:
            item = parse_book_response(message)
            if item.slug not in self._slugs:
                raise AdapterError(f"received book for unsubscribed slug {item.slug}")
            self._put(item)
        except Exception as exc:
            self._put(exc if isinstance(exc, AdapterError)
                      else AdapterError(f"shadow book decode failed: {exc}"))

    def _on_trade(self, message: Any) -> None:
        try:
            item = parse_trade_response(message)
            if item.slug not in self._slugs:
                raise AdapterError(f"received trade for unsubscribed slug {item.slug}")
            self._put(item)
        except Exception as exc:
            self._put(exc if isinstance(exc, AdapterError)
                      else AdapterError(f"shadow trade decode failed: {exc}"))

    def _on_error(self, error: Any) -> None:
        self._put(AdapterError(f"shadow market WebSocket error: {error}"))

    def _on_close(self, *_: Any) -> None:
        self._put(AdapterError("shadow market WebSocket disconnected"))

    async def start(self, slugs: Iterable[str]) -> None:
        if self._started:
            raise RuntimeError("shadow market stream is already started")
        clean = list(dict.fromkeys(str(slug).strip() for slug in slugs
                                   if str(slug).strip()))
        if not clean:
            raise ValueError("at least one market slug is required")
        self._slugs = set(clean)
        base = uuid.uuid4().hex
        try:
            await self._socket.connect()
            await self._socket.subscribe_market_data(f"shadow-books-{base}", clean)
            await self._socket.subscribe_trades(f"shadow-trades-{base}", clean)
        except Exception as exc:
            self._slugs.clear()
            try:
                await self._socket.close()
            except Exception:
                pass
            raise AdapterError(f"shadow market WebSocket startup failed: {exc}") from exc
        self._started = True

    async def next_event(
        self, *, timeout: float | None = None
    ) -> BookSnapshot | TradePrint:
        item = (await self._queue.get() if timeout is None
                else await asyncio.wait_for(self._queue.get(), timeout))
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        try:
            await self._socket.close()
        finally:
            self._started = False
            self._slugs.clear()
