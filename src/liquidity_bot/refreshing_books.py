"""Demand-driven public book renewal; never owns account state or mutations."""
from __future__ import annotations

import asyncio
import uuid
from weakref import WeakValueDictionary
from dataclasses import replace
from datetime import datetime, timezone

from .adapters import parse_book_response
from .errors import AdapterError, MarketStreamUnavailable
from .streams import MarketReadOnlyStream


class RefreshingMarketStream(MarketReadOnlyStream):
    """One full-book subscription, shared by events and direct freshness reads.

    Quiet books require a new venue snapshot, not a renewed local timestamp.
    At most one batched renewal every three seconds serves the entire scope.
    Transport workers only send subscription controls; checkpoints run on the
    caller. Failure never silently turns this into whole-slate REST polling.
    """

    RENEW_INTERVAL = 3.0
    WAIT_SECONDS = 3.0

    def __init__(self, client):
        super().__init__(client)
        self._request_id = None
        self._latest = {}
        self._book_errors = {}
        self._failure = None
        self._renew_task = None
        self._last_request_at = 0.0
        self._missing_until = 0.0
        self._closing = False
        self._queued_slugs = set()
        self._invalid_slugs = set()
        self._generation_books = WeakValueDictionary()

    def owns(self, slug):
        return self._started and slug in self._slugs

    def accepts(self, book):
        # Also fences a book already held by the consumer during a renewal.
        return (self._failure is None and book.slug not in self._invalid_slugs
                and self._generation_books.get(id(book)) is book)

    def book_invalid(self, slug):
        # Keep explicit negative evidence across renewals until a valid frame
        # replaces it. Resubscribing alone cannot resurrect the previous book.
        # After teardown this socket is no longer the book authority; the
        # existing bounded recovery REST proof must not inherit its failure.
        return self.owns(slug) and (self._failure is not None or slug in self._invalid_slugs)

    def _clear_books(self):
        self._generation_books.clear()
        self._latest.clear()
        self._book_errors.clear()
        self._pending_books.clear()
        self._queued_slugs.clear()
        while not self._queue.empty():
            self._queue.get_nowait()

    def _fail(self, error):
        if self._closing or self._failure is not None:
            return
        self._failure = error
        self._request_id = None
        self._clear_books()
        self._queue.put_nowait(error)

    def _on_error(self, _error):
        error = AdapterError("public book subscription failed")
        if isinstance(_error, Exception):
            error.__cause__ = _error
        self._fail(error)

    def _raise_if_failed(self):
        if self._failure is not None:
            # A fresh exception avoids accumulating a traceback for every
            # consumer/owner on the one permanently stored failure object.
            raise MarketStreamUnavailable(str(self._failure)) from self._failure

    def _on_close(self, *_):
        self._fail(AdapterError("market WebSocket disconnected"))

    def _on_market_data(self, message):
        if self._closing or self._failure is not None:
            return
        if (not isinstance(message, dict) or self._request_id is None
                or message.get("requestId") != self._request_id):
            return
        data = message.get("marketData", message.get("market_data"))
        slug = (data.get("marketSlug", data.get("market_slug"))
                if isinstance(data, dict) else None)
        if not isinstance(slug, str) or slug not in self._slugs:
            self._fail(AdapterError("public book subscription scope mismatch"))
            return
        received = datetime.now(timezone.utc)
        self._last_message_at = received
        try:
            book = replace(parse_book_response(message, expected_slug=slug),
                           received_at=received)
        except AdapterError as exc:
            self._invalid_slugs.add(slug)
            self._latest.pop(slug, None)
            self._book_errors[slug] = (exc, received)
            self._dropped_books += 1
            self._last_drop = str(exc)
            # Keep any queue token, but readers skip its now-invalid value.
            self._pending_books.pop(slug, None)
            return
        except Exception:
            self._fail(AdapterError("public book decode failed"))
            return
        self._book_errors.pop(slug, None)
        self._invalid_slugs.discard(slug)
        self._latest[slug] = book
        self._generation_books[id(book)] = book
        if slug not in self._queued_slugs:
            self._queued_slugs.add(slug)
            self._queue.put_nowait(slug)
        self._pending_books[slug] = book

    async def start(self, slugs):
        if self._started:
            raise RuntimeError("market stream is already started")
        clean = tuple(dict.fromkeys(slugs))
        if not clean or len(clean) > 100 or any(
                not isinstance(s, str) or not s.strip() or s != s.strip() for s in clean):
            raise ValueError("public book scope requires 1-100 exact market slugs")
        self._closing = False
        self._failure = None
        self._invalid_slugs.clear()
        self._clear_books()
        self._slugs = set(clean)
        try:
            await asyncio.wait_for(self._socket.connect(), 5.0)
            await self._subscribe(None)
            self._raise_if_failed()
            self._started = True
            self._last_message_at = datetime.now(timezone.utc)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise AdapterError("public book startup failed") from exc

    async def _subscribe(self, previous):
        self._request_id = None
        self._clear_books()
        self._last_request_at = asyncio.get_running_loop().time()
        self._missing_until = 0.0
        async with asyncio.timeout(self.WAIT_SECONDS):
            if previous is not None:
                await self._socket.unsubscribe(previous)
            if self._failure is not None or self._closing:
                raise AdapterError("public book renewal disconnected")
            self._request_id = "liquidity-books-" + uuid.uuid4().hex
            await self._socket.subscribe_market_data(self._request_id, sorted(self._slugs))

    async def _renew(self):
        try:
            await self._subscribe(self._request_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = str(exc).strip()
            error = AdapterError(
                "public book renewal failed: "
                f"{type(exc).__name__}{f': {detail}' if detail else ''}")
            error.__cause__ = exc
            self._fail(error)

    async def fresh_book(self, slug, *, checkpoint=None, clock=None):
        if not self.owns(slug):
            raise AdapterError("public book owner is not subscribed")
        clock = clock or (lambda: datetime.now(timezone.utc))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.WAIT_SECONDS
        while True:
            self._raise_if_failed()
            if self._closing or not self.owns(slug):
                raise AdapterError("public book stream closed during refresh")
            book = self._latest.get(slug)
            if book is not None and 0 <= (clock() - book.received_at).total_seconds() < 3:
                return book
            error = self._book_errors.get(slug)
            if error is not None and 0 <= (clock() - error[1]).total_seconds() < 3:
                raise error[0]
            now = loop.time()
            if now < self._missing_until:
                raise AdapterError("public book snapshot missing in recent batch")
            if now >= deadline:
                # A partial round is shared evidence. Do not spend another
                # three-second wait/renewal for each missing owner in a sweep.
                self._missing_until = now + self.RENEW_INTERVAL
                raise AdapterError("public book snapshot unavailable within refresh deadline")
            if (now - self._last_request_at >= self.RENEW_INTERVAL
                    and (self._renew_task is None or self._renew_task.done())):
                self._renew_task = asyncio.create_task(self._renew())
                # Reserve admission before yielding, including nested checkpoints.
                self._last_request_at = now
            if checkpoint is not None:
                await checkpoint()
            await asyncio.sleep(min(.02, max(0, deadline - loop.time())))

    async def next_book(self, *, timeout=None):
        async def read():
            while True:
                self._raise_if_failed()
                if self._closing:
                    raise AdapterError("public book stream closed")
                item = await self._queue.get()
                if isinstance(item, Exception):
                    self._raise_if_failed()
                    raise MarketStreamUnavailable(str(item)) from item
                self._queued_slugs.discard(item)
                book = self._pending_books.pop(item, None)
                if book is not None and self.accepts(book):
                    return book
        return await read() if timeout is None else await asyncio.wait_for(read(), timeout)

    def drain_books(self, *, limit=None):
        self._raise_if_failed()
        books = []
        for _ in range(self._queue.qsize() if limit is None else max(0, limit)):
            if self._queue.empty():
                break
            item = self._queue.get_nowait()
            if isinstance(item, Exception):
                self._raise_if_failed()
                raise MarketStreamUnavailable(str(item)) from item
            self._queued_slugs.discard(item)
            book = self._pending_books.pop(item, None)
            if book is not None and self.accepts(book):
                books.append(book)
        return tuple(books)

    async def close(self):
        self._closing = True
        self._request_id = None
        if self._renew_task is not None:
            self._renew_task.cancel()
            await asyncio.gather(self._renew_task, return_exceptions=True)
            self._renew_task = None
        try:
            await asyncio.wait_for(self._socket.close(), 5.0)
        finally:
            self._started = False
            self._slugs.clear()
            self._invalid_slugs.clear()
            self._clear_books()
            self._queue.put_nowait(AdapterError("public book stream closed"))
            self._last_message_at = None


def install_runtime_books(runner):
    """Resolve the current stream at every read, including rebind and shutdown."""
    async def get(slug):
        stream = runner.market_stream
        if not stream.owns(slug):
            # Before startup/after teardown, retain existing bounded REST proof.
            return None
        await runner.gateway._checkpoint_before_read()
        book = await stream.fresh_book(slug, clock=runner._now,
                                      checkpoint=runner.gateway._checkpoint_before_read)
        if runner.market_stream is not stream or not stream.accepts(book):
            raise AdapterError("public book owner changed during refresh")
        return book
    runner.gateway.runtime_book = get
