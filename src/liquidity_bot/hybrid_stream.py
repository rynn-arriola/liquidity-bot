"""Optional private notifications over the continuously available REST source.

This reader has no ledger, REST client or trading authority. It never reports
private readiness from a Pong. A transport gap is a typed reconciliation hint,
not a synthetic private event or a reason to cancel healthy orders.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import AdapterError
from .private_stream import PrivateEventKind
from .streams import PrivateStreamPool


@dataclass(frozen=True)
class HybridGap:
    reason: str


@dataclass(frozen=True)
class HybridDelivery:
    number: int
    event: object
    stream: object


class HybridPrivateStream:
    MAX_PENDING_EVENTS = 1024
    HEALTH_SECONDS = 5
    CONNECT_TIMEOUT = 15
    CLOSE_TIMEOUT = 5
    RETRY_INITIAL_SECONDS = 1
    RETRY_MAX_SECONDS = 60
    RETRY_RESET_SECONDS = 60

    def __init__(self, client, *, factory=PrivateStreamPool):
        self.client = client
        self.factory = factory
        self._queue = asyncio.Queue(maxsize=self.MAX_PENDING_EVENTS)
        self._task = None
        self._stream = None
        self._reset = False
        self.pending = 0  # Includes the outer runner's task-held event.
        self.state = "rest_fallback"
        self.gaps = 0
        self._issued = 0
        self._applied = 0
        self._held = None

    async def start(self, market_slugs):
        if self._task is not None:
            raise RuntimeError("hybrid listener is already started")
        # Global order messages have exact market identities. Global position
        # messages are wakeups only; no inferred market/quantity is projected.
        self._task = asyncio.create_task(self._run())

    async def _put(self, event):
        # Backpressure rather than dropping a known fill. The underlying pool
        # is bounded too and explicitly reports overflow if the owner stalls.
        number = self._issued + 1
        await self._queue.put(HybridDelivery(number, event, self))
        self._issued = number
        self.pending += 1

    def already_applied(self, delivery):
        return delivery.stream is self and delivery.number <= self._applied

    def applied(self, delivery):
        if delivery.stream is not self or delivery.number != self._applied + 1:
            raise RuntimeError("hybrid observations applied out of order")
        self._applied = delivery.number
        self.pending -= 1
        if self._held is delivery:
            self._held = None

    def request_reconnect(self):
        self._reset = True
        self.state = "rest_fallback"

    async def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                async with asyncio.timeout(self.CLOSE_TIMEOUT):
                    await stream.close()
            except Exception:
                pass

    async def _run(self):
        delay = self.RETRY_INITIAL_SECONDS
        try:
            while True:
                connected_at = None
                try:
                    self._reset = False
                    self._stream = self.factory(self.client)
                    async with asyncio.timeout(self.CONNECT_TIMEOUT):
                        await self._stream.start(None)
                    # Connected is not evidence that subscriptions work.
                    self.state = "listening"
                    connected_at = asyncio.get_running_loop().time()
                    while True:
                        if self._reset:
                            raise AdapterError("hybrid observation rejected")
                        try:
                            event = await self._stream.next_event(timeout=self.HEALTH_SECONDS)
                        except TimeoutError:
                            self._stream.assert_live(now=datetime.now(timezone.utc))
                            continue
                        if event.kind == PrivateEventKind.ERROR:
                            raise AdapterError("hybrid subscription error")
                        if event.kind in {
                            PrivateEventKind.ORDER_SNAPSHOT, PrivateEventKind.ORDER_UPDATE,
                            PrivateEventKind.POSITION_SNAPSHOT, PrivateEventKind.POSITION_UPDATE,
                        }:
                            await self._put(event)
                            self.state = "receiving"
                        # Balance and heartbeat data never renew REST proof.
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.state = "rest_fallback"
                    self.gaps += 1
                    await self._put(HybridGap("stream_unavailable"))
                finally:
                    await self._close_stream()
                # No REST request or synchronous delay on the trading owner.
                if (connected_at is not None and
                        asyncio.get_running_loop().time() - connected_at >= self.RETRY_RESET_SECONDS):
                    delay = self.RETRY_INITIAL_SECONDS
                await asyncio.sleep(delay)
                delay = min(self.RETRY_MAX_SECONDS, delay * 2)
        finally:
            self.state = "closed"

    async def next_event(self, timeout=None):
        # Do not use wait_for's separate dequeue task: it could remove a frame
        # before this coroutine records the task-held prefix for a REST drain.
        async with asyncio.timeout(timeout):
            delivery = await self._queue.get()
        self._held = delivery
        return delivery

    def drain_events(self, *, limit=None):
        result = []
        # REST service can run while the outer next_event task holds an earlier
        # observation. Include it first; the eventual task result is idempotent.
        if self._held is not None:
            result.append(self._held)
        for _ in range(self._queue.qsize() if limit is None else limit):
            if limit is not None and len(result) >= limit:
                break
            try:
                result.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return tuple(result)

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.state = "closed"

    async def retire_with_events(self):
        await self.close()
        return self.drain_events()
