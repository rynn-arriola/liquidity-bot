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


class SnapshotStream(FakeStream):
    """A shard that supplies both messages even though only balance is required."""

    async def start(self, slugs):
        await super().start(slugs)
        scope = None if slugs is None else tuple(slugs)
        self.queue.put_nowait(PrivateEvent(
            PrivateEventKind.ORDER_SNAPSHOT,
            {"orders": [], "eof": True}, request_id="orders",
            market_scope=scope, received_at=datetime.now(timezone.utc)))
        self.queue.put_nowait(PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
            {"balances": []}, request_id="balance",
            received_at=datetime.now(timezone.utc)))


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

    async def test_silent_shard_is_overlap_replaced_without_closing_healthy(self):
        pool = PrivateStreamPool(object(), factory=SnapshotStream)
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)
        old = tuple(pool._streams)
        now = datetime.now(timezone.utc)
        pool._shard_last_message_at[1] = now - timedelta(seconds=181)

        scopes = await pool.repair_stale_shards(now=now)

        self.assertEqual(scopes, (tuple(f"market-{i}" for i in range(8, 16)),))
        self.assertIs(pool._streams[0], old[0])
        self.assertIsNot(pool._streams[1], old[1])
        self.assertFalse(old[0].closed)
        self.assertTrue(old[1].closed)
        pool.assert_live(now=datetime.now(timezone.utc))

    async def test_failed_overlap_repair_leaves_every_old_shard_intact(self):
        starts = 0

        class FailsReplacement(SnapshotStream):
            async def start(self, slugs):
                nonlocal starts
                starts += 1
                if starts > 2:
                    raise AdapterError("replacement refused")
                await super().start(slugs)

        pool = PrivateStreamPool(object(), factory=FailsReplacement)
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)
        old = tuple(pool._streams)
        now = datetime.now(timezone.utc)
        pool._shard_last_message_at[1] = now - timedelta(seconds=181)

        with self.assertRaisesRegex(AdapterError, "overlap repair failed"):
            await pool.repair_stale_shards(now=now)

        self.assertEqual(tuple(pool._streams), old)
        self.assertFalse(any(stream.closed for stream in old))

    async def test_initial_state_waits_for_each_shards_balance_delivery(self):
        pool = PrivateStreamPool(object(), factory=SnapshotStream)
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)

        # Socket receipt alone is insufficient: the runner must consume the
        # private-state proof from every connection before mutation.
        self.assertFalse(pool.initial_state_ready)
        balances = 0
        while balances < 2:
            item = await pool.next_event(timeout=1)
            if item.kind == PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT:
                balances += 1
                if balances < 2:
                    self.assertFalse(pool.initial_state_ready)
        self.assertTrue(pool.initial_state_ready)

    async def test_one_balance_snapshot_cannot_prove_three_connections(self):
        starts = 0

        class OneBalanceStream(SnapshotStream):
            async def start(self, slugs):
                nonlocal starts
                starts += 1
                await FakeStream.start(self, slugs)
                scope = None if slugs is None else tuple(slugs)
                self.queue.put_nowait(PrivateEvent(
                    PrivateEventKind.ORDER_SNAPSHOT,
                    {"orders": [], "eof": True}, request_id=f"orders-{starts}",
                    market_scope=scope, received_at=datetime.now(timezone.utc)))
                if starts == 1:
                    self.queue.put_nowait(PrivateEvent(
                        PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
                        {"balances": []}, request_id="balance",
                        received_at=datetime.now(timezone.utc)))

        pool = PrivateStreamPool(object(), factory=OneBalanceStream)
        await pool.start([f"market-{i}" for i in range(20)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)
        for _ in range(4):
            await pool.next_event(timeout=1)
        self.assertFalse(pool.initial_state_ready)
        self.assertEqual(
            pool.initial_state_status,
            "order_eof=3/3 account_balance=1/3")

    async def test_empty_order_subscriptions_need_no_synthetic_order_eof(self):
        class BalanceOnlyStream(FakeStream):
            async def start(self, slugs):
                await super().start(slugs)
                self.queue.put_nowait(PrivateEvent(
                    PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
                    {"balances": []}, request_id="balance",
                    received_at=datetime.now(timezone.utc)))

        pool = PrivateStreamPool(object(), factory=BalanceOnlyStream)
        await pool.start([f"market-{i}" for i in range(20)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)
        for _ in range(3):
            await pool.next_event(timeout=1)

        self.assertTrue(pool.initial_state_ready)
        self.assertEqual(
            pool.initial_state_status,
            "order_eof=0/3 account_balance=3/3")

    async def test_all_stale_shards_can_overlap_recover_with_balance_proof(self):
        pool = PrivateStreamPool(object(), factory=SnapshotStream)
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        old = tuple(pool._streams)
        now = datetime.now(timezone.utc)
        pool._shard_last_message_at = [
            now - timedelta(seconds=181) for _ in old]

        scopes = await pool.repair_stale_shards(now=now)

        self.assertEqual(len(scopes), 2)
        self.assertTrue(all(stream.closed for stream in old))
        self.assertTrue(all(
            replacement is not prior for replacement, prior
            in zip(pool._streams, old, strict=True)))

    async def test_silent_empty_order_shard_repairs_from_balance_delivery(self):
        class BalanceOnlyStream(FakeStream):
            async def start(self, slugs):
                await super().start(slugs)
                self.queue.put_nowait(PrivateEvent(
                    PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
                    {"balances": []}, request_id="balance",
                    received_at=datetime.now(timezone.utc)))

        pool = PrivateStreamPool(object(), factory=BalanceOnlyStream)
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)
        old = tuple(pool._streams)
        now = datetime.now(timezone.utc)
        pool._shard_last_message_at[1] = now - timedelta(seconds=181)

        scopes = await pool.repair_stale_shards(
            now=now, snapshot_timeout_seconds=0.1)

        self.assertEqual(scopes, (tuple(f"market-{i}" for i in range(8, 16)),))
        self.assertIs(pool._streams[0], old[0])
        self.assertIsNot(pool._streams[1], old[1])
        self.assertFalse(old[0].closed)
        self.assertTrue(old[1].closed)

    async def test_repair_rejects_wrong_scope_on_nonterminal_order_page(self):
        starts = 0

        class WrongScopePageStream(FakeStream):
            async def start(self, slugs):
                nonlocal starts
                starts += 1
                await super().start(slugs)
                scope = None if slugs is None else tuple(slugs)
                if starts <= 2:
                    self.queue.put_nowait(PrivateEvent(
                        PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
                        {"balances": []}, request_id=f"balance-{starts}",
                        received_at=datetime.now(timezone.utc)))
                    return
                self.queue.put_nowait(PrivateEvent(
                    PrivateEventKind.ORDER_SNAPSHOT,
                    {"orders": [], "eof": False}, request_id="orders-page",
                    market_scope=("wrong-market",),
                    received_at=datetime.now(timezone.utc)))
                self.queue.put_nowait(PrivateEvent(
                    PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
                    {"balances": []}, request_id="replacement-balance",
                    market_scope=scope,
                    received_at=datetime.now(timezone.utc)))

        pool = PrivateStreamPool(object(), factory=WrongScopePageStream)
        await pool.start([f"market-{i}" for i in range(16)])
        self.addAsyncCleanup(pool.close)
        await asyncio.sleep(0)
        old = tuple(pool._streams)
        now = datetime.now(timezone.utc)
        pool._shard_last_message_at[1] = now - timedelta(seconds=181)

        with self.assertRaisesRegex(AdapterError, "overlap repair failed"):
            await pool.repair_stale_shards(now=now)

        self.assertEqual(tuple(pool._streams), old)
        self.assertFalse(any(stream.closed for stream in old))

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
