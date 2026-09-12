"""Public-book 429 recovery must not consume account/exit request capacity."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway, fcntl
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred


class RateLimited(Exception):
    status_code = 429


class BookBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def fault(self, limiter, *, token=None, retry_after=None):
        await limiter.record_result(
            operation="markets.book(one)", duration_seconds=0.1,
            status_code=429, failed=True, admission_token=token,
            retry_after_seconds=retry_after)

    async def success(self, limiter, token):
        await limiter.record_result(
            operation="markets.book(one)", duration_seconds=0.1,
            admission_token=token)

    async def test_book_429_does_not_retry_inline_or_cool_account_lane(self):
        limiter = AsyncIntervalLimiter()
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter, retry_attempts=3)
        call = AsyncMock(side_effect=RateLimited("confirmed"))
        with self.assertRaises(AdapterError) as caught:
            await gateway._call("markets.book(one)", call)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(call.await_count, 1)
        for lane in ("background", "entry", "safety", "report"):
            with self.assertRaises(ApiAdmissionDeferred):
                await limiter.wait(operation="markets.book(two)", traffic_class=lane)
        self.assertEqual(limiter.telemetry().attempts, 1)
        self.assertEqual(limiter.telemetry().cooldown_remaining_seconds, 0)
        for op, kind in (("GET /v1/orders/open", "read"),
                         ("GET /v1/portfolio/positions", "read"),
                         ("cancel", "mutation"), ("reducer", "mutation")):
            await asyncio.wait_for(limiter.wait(operation=op, request_kind=kind), 0.2)

    async def test_only_one_probe_after_deadline_and_success_restores_books(self):
        limiter = AsyncIntervalLimiter()
        await self.fault(limiter)
        limiter._book_backoff["book_cooldown_until"] = 0
        results = await asyncio.gather(*[
            limiter.wait(operation=f"markets.book({n})") for n in range(32)
        ], return_exceptions=True)
        probes = [r for r in results if isinstance(r, str)]
        self.assertEqual(len(probes), 1)
        self.assertEqual(sum(isinstance(r, ApiAdmissionDeferred) for r in results), 31)
        await self.success(limiter, probes[0])
        for n in range(32):
            await limiter.wait(operation=f"markets.book({n})")

    async def test_old_success_cannot_erase_new_429(self):
        limiter = AsyncIntervalLimiter()
        old = await limiter.wait(operation="markets.book(old)")
        await self.fault(limiter)
        await self.success(limiter, old)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation="markets.book(next)")
        limiter._book_backoff["book_cooldown_until"] = 0
        probe = await limiter.wait(operation="markets.book(probe)")
        await self.fault(limiter)  # Another request already on the wire fails.
        await self.success(limiter, probe)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation="markets.book(next)")

    async def test_failed_probe_releases_lease_without_claiming_recovery(self):
        limiter = AsyncIntervalLimiter()
        await self.fault(limiter)
        limiter._book_backoff["book_cooldown_until"] = 0
        probe = await limiter.wait(operation="markets.book(probe)")
        await limiter.record_result(
            operation="markets.book(probe)", duration_seconds=0.1,
            failed=True, status_code=503, admission_token=probe)
        self.assertTrue(limiter._book_backoff["book_rate_limited"])
        self.assertEqual(limiter._book_backoff["book_probe_until"], 0)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation="markets.book(next)")

    async def test_cancelled_probe_has_bounded_lease_and_no_extra_network_retry(self):
        limiter = AsyncIntervalLimiter()
        await self.fault(limiter)
        limiter._book_backoff["book_cooldown_until"] = 0
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
        call = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await gateway._call("markets.book(probe)", call)
        self.assertEqual(call.await_count, 1)
        remaining = limiter._book_backoff["book_probe_until"] - time.monotonic()
        self.assertTrue(0 < remaining <= limiter.BOOK_PROBE_LEASE_SECONDS)
        await limiter.wait(operation="cancel", request_kind="mutation")
        limiter._book_backoff["book_probe_until"] = 0
        await limiter.wait(operation="markets.book(new-probe)")

    async def test_retry_after_is_bounded_and_repeated_429_backoff_increases(self):
        limiter = AsyncIntervalLimiter()
        for expected in (2, 4, 8, 16, 30, 30):
            await self.fault(limiter)
            delay = limiter._book_backoff["book_cooldown_until"] - time.monotonic()
            self.assertAlmostEqual(delay, expected, delta=0.1)
        await self.fault(limiter, retry_after=999999)
        delay = limiter._book_backoff["book_cooldown_until"] - time.monotonic()
        self.assertAlmostEqual(delay, 60, delta=0.1)

    async def test_other_endpoint_success_and_failure_do_not_change_book_circuit(self):
        limiter = AsyncIntervalLimiter()
        await self.fault(limiter)
        before = dict(limiter._book_backoff)
        await limiter.record_result(operation="GET /v1/orders/open", duration_seconds=0.1)
        self.assertEqual(before, limiter._book_backoff)
        clean = AsyncIntervalLimiter()
        await clean.record_result(operation="GET /v1/orders/open", duration_seconds=0.1,
                                  failed=True, status_code=429)
        self.assertEqual(clean._book_backoff, {})

    @unittest.skipIf(fcntl is None, "production shared coordinator is POSIX")
    async def test_restart_peer_and_cross_lane_share_cooldown_and_single_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.state"
            first = AsyncIntervalLimiter(shared_state_path=path)
            peer = AsyncIntervalLimiter(shared_state_path=path)
            with patch("liquidity_bot.adapters.time.time", return_value=1000):
                await self.fault(first)
                for lane in ("entry", "safety", "report", "background"):
                    with self.assertRaises(ApiAdmissionDeferred):
                        await peer.wait(operation="markets.book(peer)", traffic_class=lane)
                await peer.wait(operation="cancel", request_kind="mutation")
            with patch("liquidity_bot.adapters.time.time", return_value=1002):
                token = await peer.wait(operation="markets.book(peer)")
                with self.assertRaises(ApiAdmissionDeferred):
                    await first.wait(operation="markets.book(first)")
                await self.success(peer, token)
                await first.wait(operation="markets.book(recovered)")

    async def test_corrupt_or_backward_clock_deadlines_cannot_wedge_book_probe(self):
        limiter = AsyncIntervalLimiter()
        for value in (float("nan"), float("inf"), "bad", 999999):
            state = {"book_rate_limited": True, "book_probe_until": value,
                     "book_cooldown_until": value}
            limiter._book_admission(state, 1000, "next", claim=True)
            self.assertEqual(state["book_probe_token"], "next")
            self.assertEqual(state["book_probe_until"], 1020)

    async def test_gateway_probe_success_reopens_and_non_429_never_opens_circuit(self):
        limiter = AsyncIntervalLimiter()
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
        with self.assertRaises(AdapterError):
            await gateway._call("markets.book(one)", AsyncMock(side_effect=ValueError("schema")))
        self.assertFalse(limiter._book_backoff.get("book_rate_limited", False))
        await self.fault(limiter)
        limiter._book_backoff["book_cooldown_until"] = 0
        self.assertEqual(await gateway._call("markets.book(one)", AsyncMock(return_value={})), {})
        self.assertFalse(limiter._book_backoff["book_rate_limited"])
        await gateway._call("markets.book(two)", AsyncMock(return_value={}))
