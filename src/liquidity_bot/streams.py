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


# Measured 2026-08-14 by adding subscriptions one at a time: the 11th is
# rejected with "max subscriptions per connection reached". The rejection
# arrives asynchronously on the socket, not from the subscribe call.
MAX_PRIVATE_SUBSCRIPTIONS = 10
# 1 orders + N positions + 1 balance <= 10. Positions must be scoped one market
# per subscription, so this is forced, and it is measured rather than assumed:
# adding subscriptions one at a time, the eleventh returns `max subscriptions
# per connection reached`.
MARKETS_PER_PRIVATE_CONNECTION = 8


class MarketReadOnlyStream:
    """Full-book snapshots for an explicit set of subscribed markets."""

    def __init__(self, client: Any):
        self._socket = client.ws.markets()
        self._queue: asyncio.Queue[BookSnapshot | Exception] = asyncio.Queue()
        self._slugs: set[str] = set()
        self._started = False
        # Books the venue sent that no consumer can use. Counted, never
        # raised, so a rising number is visible without stopping the feed.
        self._dropped_books = 0
        self._last_drop: str | None = None
        self._socket.on("market_data", self._on_market_data)
        self._socket.on("error", self._on_error)
        self._socket.on("close", self._on_close)

    def _on_market_data(self, message: Any) -> None:
        try:
            book = parse_book_response(message)
        except AdapterError as exc:
            # ONE market's unusable book must not end the feed for all of
            # them. Queuing this raised it out of next_book, which the runner
            # treats as the socket failing. Measured 2026-08-15: subscribing 28
            # slugs delivered 20 messages and then silence after a `book is not
            # two-sided` arrived early, while a later run of 29 slugs delivered
            # 415 messages and stayed healthy. It was never a width cap -- it
            # is whether a malformed book happens to arrive, and about one
            # market in four publishes one.
            #
            # Skipping is safe: that market simply has no fresh book, its data
            # ages, and book_stale already refuses to quote on stale data.
            self._dropped_books += 1
            self._last_drop = str(exc)
            return
        except Exception as exc:
            # Not a book we failed to parse -- something structural. Loud.
            self._queue.put_nowait(
                AdapterError(f"market stream decode failed: {exc}"))
            return
        if book.slug not in self._slugs:
            # A scope violation is our bug, not the venue's. Stays fatal.
            self._queue.put_nowait(AdapterError(
                f"received market data for unsubscribed slug {book.slug}"))
            return
        self._queue.put_nowait(book)

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
            # Orders ride a single subscription carrying every slug. Order
            # events always include marketSlug, so `market_scope` is never
            # consulted to attribute them (see venue_state), and subscriptions
            # are the scarce resource here.
            order_request_id = f"liquidity-orders-{base}"
            scopes[order_request_id] = tuple(clean)
            planned.append(("orders", order_request_id, list(clean)))
            # Positions get one subscription per market, never one covering
            # several. The documented positionSubscription update omits
            # marketSlug, and the projector may only infer it when the request
            # itself covers exactly one market. A single multi-slug
            # subscription would make every such update ambiguous again, which
            # is the defect that ended the 2026-08-13T05:39Z session on its
            # first fill.
            for index, slug in enumerate(clean):
                position_request_id = f"liquidity-positions-{base}-{index}"
                scopes[position_request_id] = (slug,)
                planned.append(("positions", position_request_id, [slug]))
        # The venue accepts 10 subscriptions per private connection and
        # reports the 11th asynchronously on the socket -- the subscribe call
        # itself succeeds. Nothing downstream would notice the missing feed,
        # so refuse here rather than run half-blind to our own fills.
        if len(planned) + 1 > MAX_PRIVATE_SUBSCRIPTIONS:
            raise ValueError(
                f"{len(planned) + 1} private subscriptions exceed the "
                f"{MAX_PRIVATE_SUBSCRIPTIONS} the venue accepts per connection")
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


class PrivateStreamPool:
    """One private feed spread across several connections.

    The subscription cap is per connection, not per account -- measured
    2026-08-14: `ws.private()` returns a fresh socket per call, and two sockets
    each held ten subscriptions simultaneously with zero errors. So more than
    eight markets is reachable by opening more sockets, which is the only lever
    that raises deployed capital: reward pools are per market, so more markets
    reaches more pools, while more size only buys a larger share of the same
    one.

    Presents exactly the single-connection interface -- start, next_event,
    close -- so nothing downstream knows there is a pool.

    **Any socket's failure surfaces as this pool's failure.** That is
    deliberate. The runner's recovery then rebuilds every connection and
    re-subscribes every market. Tearing down healthy sockets to repair one is
    wasteful, but the alternative is worse: one socket dying quietly while its
    markets keep showing resting orders and stop reporting their own fills.
    Losing the feed loudly is the safe direction.
    """

    def __init__(self, client: Any, *, factory: Any = None,
                 markets_per_connection: int = MARKETS_PER_PRIVATE_CONNECTION):
        if markets_per_connection <= 0:
            raise ValueError("markets_per_connection must be positive")
        self._client = client
        self._factory = factory or PrivateReadOnlyStream
        self._markets_per_connection = markets_per_connection
        self._streams: list[Any] = []
        self._readers: list[asyncio.Task[None]] = []
        self._queue: asyncio.Queue[PrivateEvent | Exception] = asyncio.Queue()
        self._started = False

    @property
    def connection_count(self) -> int:
        return len(self._streams)

    async def _pump(self, stream: Any) -> None:
        """Forward one connection's events onto the shared queue."""
        while True:
            try:
                self._queue.put_nowait(await stream.next_event())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._queue.put_nowait(exc)
                return

    async def start(self, slugs: Iterable[str] | None = None) -> None:
        if self._started:
            raise RuntimeError("private stream is already started")
        clean = None
        if slugs is not None:
            clean = list(dict.fromkeys(str(slug).strip() for slug in slugs
                                       if str(slug).strip()))
            if not clean:
                raise ValueError("an explicit market filter cannot be empty")
        shards: list[list[str] | None]
        if clean is None:
            shards = [None]
        else:
            size = self._markets_per_connection
            shards = [clean[index:index + size]
                      for index in range(0, len(clean), size)]
        try:
            for shard in shards:
                stream = self._factory(self._client)
                self._streams.append(stream)
                await stream.start(shard)
        except Exception:
            await self.close()
            raise
        for stream in self._streams:
            self._readers.append(asyncio.create_task(self._pump(stream)))
        self._started = True

    async def next_event(self, *, timeout: float | None = None) -> PrivateEvent:
        item = (await self._queue.get() if timeout is None
                else await asyncio.wait_for(self._queue.get(), timeout))
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        for reader in self._readers:
            reader.cancel()
        for reader in self._readers:
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
        self._readers.clear()
        for stream in self._streams:
            try:
                await stream.close()
            except Exception:
                pass
        self._streams.clear()
        self._started = False
