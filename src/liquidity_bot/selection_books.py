"""Bounded public full-book collection; no account or mutation authority."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext, suppress
from contextvars import ContextVar
from datetime import datetime, timezone

from .errors import AdapterError
from .streams import MarketReadOnlyStream


def book_scope(gateway, slugs):
    source = getattr(gateway, "selection_books", None)
    return source.scope(slugs) if source is not None else nullcontext()


class SelectionBookSource:
    """One disposable socket per scan, capped at 100 full-book markets.

    Snapshots retain their original receipt time. A quiet/missing/failed feed
    falls back through the gateway's normal paced REST path; no BBO or Pong
    can manufacture depth. Background readers never access the ledger.
    """

    def __init__(self, client, *, stream_factory=MarketReadOnlyStream,
                 clock=None, checkpoint=None, wait_seconds=3.0):
        self.client = client
        self.stream_factory = stream_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.checkpoint = checkpoint
        self.wait_seconds = wait_seconds
        self._session = ContextVar(f"selection_books_{id(self)}", default=None)

    async def _check(self):
        if self.checkpoint is not None:
            await self.checkpoint()

    async def _wait_start(self, task):
        deadline = asyncio.get_running_loop().time() + 5.0
        while not task.done():
            await self._check()
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.wait({task}, timeout=.1)
        try:
            await task
        except (AdapterError, TimeoutError, OSError):
            return False
        return True

    @asynccontextmanager
    async def scope(self, slugs):
        clean = tuple(dict.fromkeys(slugs))
        if len(clean) > 100 or any(not isinstance(s, str) or not s.strip() for s in clean):
            raise ValueError("candidate book scope must contain at most 100 nonempty slugs")
        if not clean:
            yield
            return
        if self._session.get() is not None:
            raise RuntimeError("nested candidate book scope")
        stream = self.stream_factory(self.client)
        session = dict(slugs=set(clean), books={}, failed=False,
                       deadline=0.0, hits=0, misses=0)
        token = self._session.set(session)
        starter = asyncio.create_task(stream.start(clean))
        reader = None
        try:
            session["failed"] = not await self._wait_start(starter)
            if session["failed"]:
                starter.cancel()
                await asyncio.gather(starter, return_exceptions=True)
            if not session["failed"]:
                session["deadline"] = asyncio.get_running_loop().time() + self.wait_seconds
                reader = asyncio.create_task(self._read(stream, session))
            yield
        finally:
            self._session.reset(token)
            for task in (starter, reader):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (starter, reader) if t is not None),
                                 return_exceptions=True)
            # Close owns no orders and cannot hold shutdown indefinitely.
            with suppress(AdapterError, TimeoutError, OSError):
                await asyncio.wait_for(stream.close(), 5.0)
            session["books"].clear()
            print(f"[selection-books] markets={len(clean)} "
                  f"stream_hits={session['hits']} rest_fallbacks={session['misses']} "
                  f"stream_failed={int(session['failed'])}", flush=True)

    async def _read(self, stream, session):
        try:
            while True:
                book = await stream.next_book()
                if book.slug not in session["slugs"]:
                    raise AdapterError("candidate book scope mismatch")
                session["books"][book.slug] = book
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session["books"].clear()
            session["failed"] = True
            # Expected stream failures permit REST; programming faults do not.
            if not isinstance(exc, (AdapterError, OSError)):
                session["error"] = exc

    async def get(self, slug):
        session = self._session.get()
        if session is None or slug not in session["slugs"]:
            return None
        while True:
            await self._check()
            if "error" in session:
                raise session["error"]
            book = session["books"].get(slug)
            if not session["failed"] and book is not None and book.received_at is not None:
                age = (self.clock() - book.received_at).total_seconds()
                if 0 <= age < 3.0:
                    session["hits"] += 1
                    return book
            if session["failed"] or asyncio.get_running_loop().time() >= session["deadline"]:
                session["misses"] += 1
                return None
            await asyncio.sleep(.05)
