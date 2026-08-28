"""The private feed spread across several connections."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.errors import AdapterError
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.streams import (
    MARKETS_PER_PRIVATE_CONNECTION, PrivateStreamPool)


class FakeStream:
    """One connection. Refuses more markets than the venue accepts."""

    instances: list["FakeStream"] = []

    def __init__(self, _client):
        self.started_with: list[str] | None = None
        self.closed = False
        self.queue: asyncio.Queue = asyncio.Queue()
        FakeStream.instances.append(self)

    async def start(self, slugs):
        if slugs is not None and len(slugs) > MARKETS_PER_PRIVATE_CONNECTION:
            raise ValueError("too many subscriptions for one connection")
        self.started_with = list(slugs) if slugs is not None else None

    async def next_event(self, *, timeout=None):
        item = await self.queue.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


def event(slug: str) -> PrivateEvent:
    return PrivateEvent(
        kind=PrivateEventKind.ORDER_UPDATE, request_id="r",
        payload={"slug": slug}, market_scope=(slug,))


class PrivateStreamPoolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeStream.instances = []

    def pool(self, **kwargs):
        return PrivateStreamPool(object(), factory=FakeStream, **kwargs)

    async def test_sixteen_markets_open_two_connections(self):
        """Eight per connection is the venue's cap, so sixteen is two sockets."""
        pool = self.pool()
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)

        self.assertEqual(pool.connection_count, 2)
        self.assertEqual(
            [len(s.started_with) for s in FakeStream.instances], [8, 8])
        # Every market is covered exactly once.
        covered = [slug for s in FakeStream.instances for slug in s.started_with]
        self.assertEqual(sorted(covered), sorted(f"market-{i}" for i in range(16)))

    async def test_a_partial_shard_does_not_open_an_empty_connection(self):
        pool = self.pool()
        await pool.start([f"market-{i}" for i in range(9)])
        self.addAsyncCleanup(pool.close)

        self.assertEqual(pool.connection_count, 2)
        self.assertEqual(
            [len(s.started_with) for s in FakeStream.instances], [8, 1])

    async def test_events_from_every_connection_reach_one_queue(self):
        pool = self.pool()
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)

        FakeStream.instances[0].queue.put_nowait(event("market-0"))
        FakeStream.instances[1].queue.put_nowait(event("market-9"))

        seen = {
            (await pool.next_event(timeout=1)).market_scope[0],
            (await pool.next_event(timeout=1)).market_scope[0],
        }
        self.assertEqual(seen, {"market-0", "market-9"})

    async def test_one_dead_socket_surfaces_as_a_pool_failure(self):
        """A quiet socket is the dangerous failure, so fail loudly instead.

        If one connection died silently its markets would keep showing resting
        orders while no longer reporting their own fills. Raising here makes
        the runner rebuild and re-subscribe every connection.
        """
        pool = self.pool()
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)

        FakeStream.instances[1].queue.put_nowait(
            AdapterError("private WebSocket disconnected"))

        with self.assertRaises(AdapterError):
            await pool.next_event(timeout=1)

    async def test_one_silent_shard_is_detected_independently(self):
        pool = self.pool()
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        now = datetime.now(timezone.utc)
        pool._shard_last_message_at[1] = now - timedelta(seconds=181)

        with self.assertRaisesRegex(
                AdapterError, r"shard silence: 2\(8 markets\)"):
            pool.assert_live(now=now)

        # Traffic on shard one cannot hide that shard two is stale.
        FakeStream.instances[0].queue.put_nowait(event("market-0"))
        await pool.next_event(timeout=1)
        with self.assertRaisesRegex(AdapterError, r"shard silence: 2"):
            pool.assert_live(now=datetime.now(timezone.utc))

        # Its own heartbeat repairs only its own liveness clock.
        FakeStream.instances[1].queue.put_nowait(PrivateEvent(
            PrivateEventKind.HEARTBEAT, {}, request_id="heartbeat"))
        await pool.next_event(timeout=1)
        pool.assert_live(now=datetime.now(timezone.utc))

    async def test_close_closes_every_connection(self):
        pool = self.pool()
        await pool.start([f"market-{i}" for i in range(16)])

        await pool.close()

        self.assertTrue(all(s.closed for s in FakeStream.instances))
        self.assertEqual(pool.connection_count, 0)

    async def test_a_failed_connection_during_startup_closes_the_rest(self):
        """No half-open pool: either every socket is up or none are."""
        pool = self.pool(markets_per_connection=100)

        with self.assertRaises(ValueError):
            await pool.start([f"market-{i}" for i in range(16)])

        self.assertTrue(all(s.closed for s in FakeStream.instances))


if __name__ == "__main__":
    unittest.main()
