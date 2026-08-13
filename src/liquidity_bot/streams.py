"""Queue-based, read-only WebSocket adapters.

The adapters subscribe to market data and private account state. They expose
normalized events to the application but contain no order creation, mutation,
or cancellation method.
"""

from __future__ import annotations

import asyncio
import uuid
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
        # The installed SDK emits every decoded JSON object on `message`
        # before its convenience event. Register once to avoid duplicates.
        self._socket.on("message", self._on_message)
        self._socket.on("error", self._on_error)
        self._socket.on("close", self._on_close)

    def _on_message(self, message: Any) -> None:
        try:
            self._queue.put_nowait(decode_private_message(message))
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
        try:
            await self._socket.connect()
            await self._socket.subscribe_orders(f"liquidity-orders-{base}", clean)
            await self._socket.subscribe_positions(f"liquidity-positions-{base}", clean)
            await self._socket.subscribe_account_balance(f"liquidity-balance-{base}")
        except Exception as exc:
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
