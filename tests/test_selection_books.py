import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway, parse_book_response
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.selection_books import SelectionBookSource, book_scope


def book(slug="one", age=0):
    return replace(parse_book_response({"marketData": {
        "marketSlug": slug, "bids": [{"px": {"value": ".4"}, "qty": "100"}],
        "offers": [{"px": {"value": ".6"}, "qty": "100"}],
        "state": "MARKET_STATE_OPEN"}}),
        received_at=datetime.now(timezone.utc) - timedelta(seconds=age))


class Stream:
    def __init__(self, books=(), error=None):
        self.queue = asyncio.Queue()
        for item in books:
            self.queue.put_nowait(item)
        self.error = error
        self.closed = False
        self.slugs = ()

    async def start(self, slugs):
        self.slugs = tuple(slugs)
        if self.error:
            raise self.error

    async def next_book(self):
        item = await self.queue.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


class SelectionBooksTests(unittest.IsolatedAsyncioTestCase):
    def source(self, stream, **kwargs):
        return SelectionBookSource(object(), stream_factory=lambda _: stream,
                                   wait_seconds=.1, **kwargs)

    async def test_hundred_markets_use_stream_without_rest_and_preserve_receipts(self):
        books = [book(str(i)) for i in range(100)]
        stream = Stream(books)
        gateway = AsyncReadOnlyGateway(object(), limiter=AsyncIntervalLimiter())
        gateway._call = AsyncMock(side_effect=AssertionError("REST must not run"))
        gateway.selection_books = self.source(stream)
        async with book_scope(gateway, [b.slug for b in books]):
            for expected in books:
                self.assertIs(await gateway.fetch_book(expected.slug), expected)
        self.assertTrue(stream.closed)
        self.assertIsNone(await gateway.selection_books.get("0"))
        self.assertEqual(gateway.limiter.telemetry().attempts, 0)

    async def test_missing_stale_future_and_failed_connection_fall_back(self):
        for stream in (Stream(), Stream([book(age=4)]), Stream([book(age=-1)]),
                       Stream(error=AdapterError("connect"))):
            with self.subTest(stream=stream):
                source = self.source(stream)
                async with source.scope(["one"]):
                    self.assertIsNone(await source.get("one"))
                self.assertTrue(stream.closed)

    async def test_disconnect_revokes_previously_received_books(self):
        stream = Stream([book()])
        source = self.source(stream)
        async with source.scope(["one"]):
            self.assertIsNotNone(await source.get("one"))
            stream.queue.put_nowait(AdapterError("disconnected"))
            await asyncio.sleep(0)
            self.assertIsNone(await source.get("one"))

    async def test_scope_error_cannot_supply_another_markets_depth(self):
        stream = Stream([book("foreign")])
        source = self.source(stream)
        async with source.scope(["one"]):
            self.assertIsNone(await source.get("one"))
            self.assertIsNone(await source.get("foreign"))

    async def test_newer_snapshot_supersedes_previous(self):
        first, second = book(), book()
        stream = Stream([first])
        source = self.source(stream)
        async with source.scope(["one"]):
            self.assertIs(await source.get("one"), first)
            stream.queue.put_nowait(second)
            await asyncio.sleep(0)
            self.assertIs(await source.get("one"), second)

    async def test_checkpoint_interrupt_closes_collector_before_more_work(self):
        stream = Stream()
        check = AsyncMock(side_effect=ApiAdmissionDeferred("position priority"))
        source = self.source(stream, checkpoint=check)
        with self.assertRaises(ApiAdmissionDeferred):
            async with source.scope(["one"]):
                await source.get("one")
        self.assertTrue(stream.closed)
        self.assertIsNone(source._session.get())

    async def test_cancellation_joins_reader_and_closes_socket(self):
        stream = Stream()
        source = self.source(stream)
        entered = asyncio.Event()

        async def collect():
            async with source.scope(["one"]):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(collect())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)

    async def test_programming_fault_is_not_silent_rest_fallback(self):
        source = self.source(Stream([RuntimeError("bug")]))
        async with source.scope(["one"]):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                await source.get("one")

    async def test_scope_bound_and_empty_scope(self):
        stream = Stream()
        source = self.source(stream)
        with self.assertRaises(ValueError):
            async with source.scope([str(i) for i in range(101)]):
                pass
        async with source.scope([]):
            self.assertIsNone(await source.get("one"))
        self.assertEqual(stream.slugs, ())

    async def test_without_source_gateway_scope_is_unchanged(self):
        async with book_scope(object(), ["one"]):
            pass

    async def test_selector_and_revalidation_keep_identical_ranking_and_gates(self):
        from tests.test_market_selector import (
            NOW, FakeGateway, FixedFillHazard, book as selector_book, incentive, market)
        from liquidity_bot.market_selector import RewardMarketSelector, RewardSelectionPolicy
        slugs = ("small", "big", "live")
        markets = {s: market(s, starts_in_hours=-1 if s == "live" else 1) for s in slugs}
        incentives = {s: incentive(s, pool=100 if s == "small" else 1000) for s in slugs}
        books = {s: replace(selector_book(s), received_at=NOW) for s in slugs}
        plain = FakeGateway(markets, incentives, books)
        streamed = FakeGateway(markets, incentives, books)
        streams = []

        def factory(_):
            stream = Stream([books[s] for s in ("small", "big")])
            streams.append(stream)
            return stream

        streamed.selection_books = SelectionBookSource(
            object(), stream_factory=factory, clock=lambda: NOW)

        async def fetch(slug):
            result = await streamed.selection_books.get(slug)
            if result is None:
                raise AssertionError("expected subscribed full book")
            return result

        streamed.fetch_book = fetch
        policy = RewardSelectionPolicy(quote_size=1.0)
        before = RewardMarketSelector(plain, policy, fill_hazard_model=FixedFillHazard(.05))
        after = RewardMarketSelector(streamed, policy, fill_hazard_model=FixedFillHazard(.05))
        self.assertEqual(await before.select(now=NOW), await after.select(now=NOW))
        self.assertEqual(await before.revalidate_viable(slugs, now=NOW),
                         await after.revalidate_viable(slugs, now=NOW))
        self.assertTrue(all(s.closed for s in streams))
        self.assertNotIn("live", streams[0].slugs)

    async def test_fallback_uses_existing_gateway_and_propagates_429(self):
        gateway = AsyncReadOnlyGateway(object(), limiter=AsyncIntervalLimiter())
        stream = Stream(error=AdapterError("no stream"))
        gateway.selection_books = self.source(stream)
        gateway._call = AsyncMock(side_effect=AdapterError("limited", status_code=429))
        async with book_scope(gateway, ["one"]):
            with self.assertRaises(AdapterError) as raised:
                await gateway.fetch_book("one")
        self.assertEqual(raised.exception.status_code, 429)
        gateway._call.assert_awaited_once()
        self.assertEqual(gateway._call.call_args.args[0], "markets.book(one)")

    async def test_cancellation_during_connect_closes_socket(self):
        stream = Stream()
        started = asyncio.Event()

        async def connect(slugs):
            started.set()
            await asyncio.Future()

        stream.start = connect
        source = self.source(stream)

        async def collect():
            async with source.scope(["one"]):
                self.fail("connection never completed")

        task = asyncio.create_task(collect())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)

    async def test_concurrent_scopes_do_not_share_books(self):
        streams = []

        def factory(_):
            stream = Stream([book("one")])
            streams.append(stream)
            return stream

        source = SelectionBookSource(object(), stream_factory=factory, wait_seconds=.1)

        async def collect():
            async with source.scope(["one"]):
                return await source.get("one")

        first, second = await asyncio.gather(collect(), collect())
        self.assertIsNot(first, second)
        self.assertTrue(all(s.closed for s in streams))


if __name__ == "__main__":
    unittest.main()
