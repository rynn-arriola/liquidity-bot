import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from liquidity_bot.adapters import AsyncReadOnlyGateway, AsyncIntervalLimiter
from liquidity_bot.errors import AdapterError, EmptyBookError
from liquidity_bot.refreshing_books import RefreshingMarketStream, install_runtime_books
from liquidity_bot.selection_books import SelectionBookSource
from tests.test_streams import FakeClient, FakeSocket


def message(slug, request, *, empty=False):
    return {"requestId": request, "marketData": {
        "marketSlug": slug, "bids": [] if empty else [{"px": {"value": ".4"}, "qty": "100"}],
        "offers": [] if empty else [{"px": {"value": ".6"}, "qty": "100"}]}}


class Socket(FakeSocket):
    def __init__(self):
        super().__init__()
        self.silent = set()
        self.send_snapshots = True

    async def subscribe_market_data(self, request_id, slugs):
        await super().subscribe_market_data(request_id, slugs)
        if self.send_snapshots:
            for slug in slugs:
                if slug not in self.silent:
                    self.emit("market_data", message(slug, request_id))

    async def unsubscribe(self, request_id):
        self.calls.append(("unsubscribe", request_id))


class RefreshingBooksTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_book_blocks_price_actions_but_keeps_pause_cancels(self):
        from decimal import Decimal
        from liquidity_bot.reconciler import ActionKind, DesiredStateReconciler, WorkingOrder
        from tests.test_reconciler import frame, policy
        planner = DesiredStateReconciler(policy())
        current = replace(frame(), book_known=False)
        self.assertEqual(planner.plan(current)[0].reason, "book_stale")
        paused = replace(current, new_risk_allowed=False, hard_stop_flatten_required=True,
            working_orders=(WorkingOrder("owned", "bid", .49, Decimal("2")),))
        self.assertEqual(planner.plan(paused)[0].kind, ActionKind.CANCEL)

    async def make(self, slugs=("one",), *, silent=()):
        client = FakeClient()
        socket = Socket()
        socket.silent = set(silent)
        client.ws.market_socket = socket
        stream = RefreshingMarketStream(client)
        self.addAsyncCleanup(stream.close)
        await stream.start(slugs)
        return stream, socket, client

    def age(self, stream):
        for slug, book in tuple(stream._latest.items()):
            stream._latest[slug] = replace(book, received_at=book.received_at - timedelta(seconds=4))
        stream._last_request_at -= 4

    async def test_32_owners_one_renewal_zero_rest_and_original_receipts(self):
        slugs = tuple(str(i) for i in range(32))
        stream, socket, _ = await self.make(slugs)
        original = await stream.fresh_book("0")
        self.assertIs(await stream.next_book(), original)
        self.assertIs(await stream.fresh_book("0"), original)
        self.age(stream)
        books = await asyncio.gather(*(stream.fresh_book(s) for s in slugs))
        self.assertEqual(len(books), 32)
        self.assertEqual(len([c for c in socket.calls if c[0] == "market_data"]), 2)
        self.assertEqual(len([c for c in socket.calls if c[0] == "unsubscribe"]), 1)
        self.assertGreater(books[0].received_at, original.received_at)
        self.assertEqual({b.slug for b in stream.drain_books()}, set(slugs))
        self.assertEqual(len(stream._latest), 32)

    async def test_late_old_request_missing_id_and_held_book_are_rejected(self):
        stream, socket, _ = await self.make()
        old = stream._request_id
        held = await stream.next_book()
        self.age(stream)
        fresh = await stream.fresh_book("one")
        socket.emit("market_data", message("one", old))
        socket.emit("market_data", message("one", None))
        self.assertIs(await stream.fresh_book("one"), fresh)
        self.assertFalse(stream.accepts(held))
        self.assertTrue(stream.accepts(fresh))

    async def test_busy_same_generation_delivery_is_not_starved_or_retained_forever(self):
        import gc
        stream, socket, _ = await self.make()
        held = await stream.next_book()
        for _ in range(100):
            socket.emit("market_data", message("one", stream._request_id))
        self.assertTrue(stream.accepts(held))
        self.assertIsNot(held, stream._latest["one"])
        gc.collect()
        self.assertEqual(len(stream._generation_books), 2)
        del held
        gc.collect()
        self.assertEqual(len(stream._generation_books), 1)

    async def test_missing_market_is_bounded_without_rest_or_per_owner_storm(self):
        stream, socket, _ = await self.make(("one", "two"), silent=("two",))
        stream.WAIT_SECONDS = .06
        self.age(stream)
        with self.assertRaisesRegex(AdapterError, "unavailable"):
            await stream.fresh_book("two")
        self.assertIsNotNone(await stream.fresh_book("one"))
        with self.assertRaises(AdapterError):
            await stream.fresh_book("two")
        self.assertEqual(len([c for c in socket.calls if c[0] == "market_data"]), 2)

    async def test_empty_and_malformed_are_owner_scoped_positive_errors(self):
        stream, socket, _ = await self.make(("one", "two"))
        socket.emit("market_data", message("one", stream._request_id, empty=True))
        with self.assertRaises(EmptyBookError):
            await stream.fresh_book("one")
        self.assertTrue(stream.book_invalid("one"))
        self.assertFalse(stream.book_invalid("two"))
        self.assertEqual([b.slug for b in stream.drain_books()], ["two"])
        self.assertIsNotNone(await stream.fresh_book("two"))
        bad = message("one", stream._request_id)
        bad["marketData"]["bids"] = "bad"
        socket.emit("market_data", bad)
        with self.assertRaisesRegex(AdapterError, "sides"):
            await stream.fresh_book("one")
        socket.emit("market_data", message("one", stream._request_id))
        self.assertIsNotNone(await stream.fresh_book("one"))
        self.assertFalse(stream.book_invalid("one"))

    async def test_negative_proof_survives_renewal_until_positive_replacement(self):
        stream, socket, _ = await self.make()
        socket.emit("market_data", message("one", stream._request_id, empty=True))
        socket.send_snapshots = False
        await stream._subscribe(stream._request_id)
        self.assertTrue(stream.book_invalid("one"))
        socket.emit("market_data", message("one", stream._request_id))
        self.assertFalse(stream.book_invalid("one"))

    async def test_repeated_bad_good_frames_keep_queue_bounded(self):
        stream, socket, _ = await self.make()
        for _ in range(100):
            socket.emit("market_data", message("one", stream._request_id, empty=True))
            socket.emit("market_data", message("one", stream._request_id))
        self.assertEqual(stream._queue.qsize(), 1)
        self.assertEqual(len(stream.drain_books()), 1)

    async def test_disconnect_and_foreign_scope_revoke_fresh_proof(self):
        for event in ("close", "error", "foreign"):
            stream, socket, _ = await self.make()
            book = await stream.fresh_book("one")
            if event == "foreign":
                socket.emit("market_data", message("foreign", stream._request_id))
            else:
                socket.emit(event, None)
            self.assertFalse(stream.accepts(book))
            with self.assertRaises(AdapterError):
                await stream.fresh_book("one")
            with self.assertRaises(AdapterError):
                stream.drain_books()
            with self.assertRaises(AdapterError):
                await stream.next_book(timeout=.1)

    async def test_future_receipt_and_pong_do_not_manufacture_book(self):
        stream, socket, _ = await self.make()
        stream.WAIT_SECONDS = .05
        stream._latest["one"] = replace(stream._latest["one"],
            received_at=datetime.now(timezone.utc) + timedelta(seconds=10))
        socket.emit("message", {"pong": True})
        with self.assertRaises(AdapterError):
            await stream.fresh_book("one")

    async def test_renewal_timeout_failure_and_close_cancel_owned_worker(self):
        stream, socket, _ = await self.make()
        stream.WAIT_SECONDS = .05
        socket.unsubscribe = AsyncMock(side_effect=OSError("disconnect"))
        self.age(stream)
        with self.assertRaisesRegex(AdapterError, "renewal failed"):
            await stream.fresh_book("one")
        self.assertIsInstance(stream._failure.__cause__, OSError)
        self.assertIn("OSError: disconnect", str(stream._failure))
        await stream.close()
        self.assertIsNone(stream._renew_task)
        self.assertFalse(stream.owns("one"))
        self.assertFalse(stream.book_invalid("one"))

    async def test_cancelled_waiter_does_not_steal_others_and_close_joins(self):
        stream, socket, _ = await self.make()
        gate = asyncio.Event()
        original = socket.unsubscribe
        async def blocked(request):
            await gate.wait()
            await original(request)
        socket.unsubscribe = blocked
        self.age(stream)
        first = asyncio.create_task(stream.fresh_book("one"))
        await asyncio.sleep(.01)
        second = asyncio.create_task(stream.fresh_book("one"))
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        gate.set()
        self.assertIsNotNone(await second)
        self.age(stream)
        gate.clear()
        waiting = asyncio.create_task(stream.fresh_book("one"))
        await asyncio.sleep(.01)
        await stream.close()
        with self.assertRaises(AdapterError):
            await waiting
        self.assertIsNone(stream._renew_task)

    async def test_nested_checkpoint_shares_batch_without_deadlock(self):
        stream, socket, _ = await self.make(("one", "two"))
        self.age(stream)
        async def checkpoint():
            await stream.fresh_book("two")
        self.assertIsNotNone(await asyncio.wait_for(stream.fresh_book("one", checkpoint=checkpoint), .5))
        self.assertEqual(len([c for c in socket.calls if c[0] == "market_data"]), 2)

    async def test_rebind_reconnect_and_gateway_keep_rest_only_for_unsubscribed(self):
        stream, socket, _ = await self.make()
        gateway = AsyncReadOnlyGateway(object(), limiter=AsyncIntervalLimiter())
        gateway._call = AsyncMock(return_value=message("other", None))
        runner = SimpleNamespace(market_stream=stream, gateway=gateway,
                                 _now=lambda: datetime.now(timezone.utc), _stop_requested=True)
        install_runtime_books(runner)
        self.assertIs(await gateway.fetch_book("one"), stream._latest["one"])
        gateway._call.assert_not_awaited()
        self.assertEqual((await gateway.fetch_book("other")).slug, "other")
        gateway._call.assert_awaited_once()
        gateway._call.reset_mock()
        socket.emit("close", None)
        with self.assertRaises(AdapterError):
            await gateway.fetch_book("one")
        gateway._call.assert_not_awaited()
        replacement, _, _ = await self.make()
        runner.market_stream = replacement
        self.assertIs(await gateway.fetch_book("one"), replacement._latest["one"])

    async def test_selection_renews_quiet_startup_books_and_cleans_up(self):
        stream, socket, client = await self.make()
        await stream.close()
        source = SelectionBookSource(client, stream_factory=lambda _: stream)
        async with source.scope(("one",)):
            first = await source.get("one")
            self.age(stream)
            fresh = await source.get("one")
            self.assertGreater(fresh.received_at, first.received_at)
        self.assertFalse(stream.owns("one"))
        self.assertIsNone(stream._renew_task)

    async def test_scope_bounds_and_one_sided_exit_book(self):
        stream, socket, _ = await self.make()
        one = message("one", stream._request_id)
        one["marketData"]["offers"] = []
        socket.emit("market_data", one)
        self.assertEqual(len((await stream.fresh_book("one")).offers), 0)
        await stream.close()
        for slugs in ((), tuple(str(i) for i in range(101)), (" one",)):
            with self.assertRaises(ValueError):
                await stream.start(slugs)

    async def test_start_cancel_closes_socket(self):
        client = FakeClient()
        stream = RefreshingMarketStream(client)
        client.market_socket.connect = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await stream.start(("one",))
        self.assertIn(("close",), client.market_socket.calls)
        self.assertFalse(stream.owns("one"))

    async def test_partial_batch_missing_wait_is_shared_and_does_not_block_healthy(self):
        stream, socket, _ = await self.make(("one", "two", "three"), silent=("two", "three"))
        stream.WAIT_SECONDS = .05
        self.age(stream)
        with self.assertRaises(AdapterError):
            await stream.fresh_book("two")
        self.assertTrue(stream._missing_until > asyncio.get_running_loop().time())
        with self.assertRaisesRegex(AdapterError, "recent batch"):
            await stream.fresh_book("three")
        self.assertIsNotNone(await stream.fresh_book("one"))
        self.assertEqual(len([c for c in socket.calls if c[0] == "market_data"]), 2)

    async def test_selection_does_not_swallow_checkpoint_stop_as_rest_fallback(self):
        stream, socket, client = await self.make()
        await stream.close()
        source = SelectionBookSource(client, stream_factory=lambda _: stream)
        async with source.scope(("one",)):
            source.checkpoint = AsyncMock(side_effect=AdapterError("operator stop"))
            with self.assertRaisesRegex(AdapterError, "operator stop"):
                await source.get("one")

    async def test_rebind_during_checkpoint_refuses_old_owner_book(self):
        stream, socket, _ = await self.make()
        replacement, _, _ = await self.make()
        gateway = AsyncReadOnlyGateway(object(), limiter=AsyncIntervalLimiter())
        gateway._call = AsyncMock(side_effect=AssertionError("no REST"))
        runner = SimpleNamespace(market_stream=stream, gateway=gateway,
                                 _now=lambda: datetime.now(timezone.utc))
        async def rebind():
            runner.market_stream = replacement
        gateway._checkpoint_before_read = rebind
        install_runtime_books(runner)
        self.age(stream)
        with self.assertRaisesRegex(AdapterError, "owner changed"):
            await gateway.fetch_book("one")
        gateway._call.assert_not_awaited()
