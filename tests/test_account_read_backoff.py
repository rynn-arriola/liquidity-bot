"""Account throttling yields the owner and cannot fan out into retries."""
import asyncio
import json
import tempfile
import unittest
import httpx
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway, fcntl
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot import read_backoff


BALANCE = "GET /v1/account/balances"
POSITIONS = "GET /v1/portfolio/positions"
HISTORY = "GET /v1/portfolio/activities"
CASH = HISTORY + " (cash only)"


class RateLimited(Exception):
    status_code = 429
    response = SimpleNamespace(headers={"Retry-After": "10"})


class AccountReadBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_outages_back_off_without_mutation_or_endpoint_cooldown(self):
        for status in read_backoff.TRANSIENT_STATUSES:
            with self.subTest(status=status):
                limiter = AsyncIntervalLimiter()
                gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
                failure = AdapterError("unavailable", status_code=status)
                call = AsyncMock(side_effect=failure)
                with self.assertRaises(AdapterError):
                    await gateway._call(BALANCE, call)
                self.assertTrue(read_backoff.is_backpressure(failure))
                self.assertEqual(call.await_count, 1)
                deferred = (await asyncio.gather(gateway._call(BALANCE, call),
                                                return_exceptions=True))[0]
                self.assertIsInstance(deferred, ApiAdmissionDeferred)
                for lane in ("entry", "safety", "background", "report"):
                    with self.assertRaises(ApiAdmissionDeferred):
                        await limiter.wait(operation=BALANCE, traffic_class=lane)
                for operation, kind in ((POSITIONS, "read"), ("cancel", "mutation"),
                                         ("reducer", "mutation")):
                    await limiter.wait(operation=operation, request_kind=kind)
                self.assertEqual(limiter.telemetry().rate_limited_responses, 0)
                self.assertEqual(call.await_count, 1)

    async def test_network_failure_backoff_but_schema_and_auth_are_not_outages(self):
        for error in (TimeoutError(), ConnectionResetError(), httpx.ReadTimeout("timeout")):
            limiter = AsyncIntervalLimiter()
            gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
            with self.assertRaises(AdapterError) as caught:
                await gateway._call(POSITIONS, AsyncMock(side_effect=error))
            self.assertTrue(read_backoff.is_backpressure(caught.exception))
            with self.assertRaises(ApiAdmissionDeferred):
                await limiter.wait(operation=POSITIONS)
        for error in (ValueError("schema"), AdapterError("bad schema"),
                      AdapterError("not found", status_code=404),
                      AdapterError("denied", status_code=403), PermissionError()):
            limiter = AsyncIntervalLimiter()
            gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
            with self.assertRaises(AdapterError) as caught:
                await gateway._call(POSITIONS, AsyncMock(side_effect=error))
            self.assertFalse(read_backoff.is_backpressure(caught.exception))
            await limiter.wait(operation=POSITIONS)

    async def test_mixed_outage_probe_is_single_and_recovers_with_positive_success(self):
        limiter = AsyncIntervalLimiter()
        for expected, status in zip((2, 4, 8, 16, 30, 30), (503, 500, 429, 502, 504, 503)):
            with patch("liquidity_bot.adapters.time.monotonic", return_value=100):
                await limiter.record_result(operation=BALANCE, duration_seconds=.1,
                                            status_code=status, failed=True)
                self.assertEqual(dict(limiter.telemetry().account_read_cooldowns)["balance"], expected)
        state = limiter._account_read_backoff["account_read_balance"]
        state["until"] = 0
        results = await asyncio.gather(*(limiter.wait(operation=BALANCE)
                                        for _ in range(32)), return_exceptions=True)
        tokens = [r for r in results if isinstance(r, str)]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(sum(isinstance(r, ApiAdmissionDeferred) for r in results), 31)
        await limiter.record_result(operation=BALANCE, duration_seconds=.1,
                                    admission_token=tokens[0])
        await limiter.wait(operation=BALANCE)

    @unittest.skipUnless(fcntl is not None, "requires POSIX shared coordination")
    async def test_server_outage_is_shared_across_process_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            first = AsyncIntervalLimiter(shared_state_path=path)
            second = AsyncIntervalLimiter(shared_state_path=path)
            await first.record_result(operation=HISTORY, duration_seconds=.1,
                                      failed=True, status_code=503, retry_after_seconds=10)
            with self.assertRaises(ApiAdmissionDeferred):
                await second.wait(operation=CASH, traffic_class="report")
            await second.wait(operation="cancel", request_kind="mutation")
            self.assertEqual((await second.shared_telemetry()).rate_limited_responses, 0)

    async def fault(self, limiter, operation=BALANCE, token=None, retry_after=10):
        await limiter.record_result(operation=operation, duration_seconds=.1,
            status_code=429, failed=True, admission_token=token,
            retry_after_seconds=retry_after)

    async def test_first_429_stops_inline_retries_and_leaves_other_work_available(self):
        limiter = AsyncIntervalLimiter()
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
        call = AsyncMock(side_effect=RateLimited())
        with self.assertRaises(AdapterError) as caught:
            await gateway._call(BALANCE, call)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(call.await_count, 1)
        for lane in ("safety", "entry", "background", "report"):
            with self.assertRaises(ApiAdmissionDeferred):
                await limiter.wait(operation=BALANCE, traffic_class=lane)
        for op, kind in ((POSITIONS, "read"), (HISTORY, "read"),
                         ("cancel", "mutation"), ("reducer", "mutation")):
            await asyncio.wait_for(limiter.wait(operation=op, request_kind=kind), .2)
        evidence = limiter.telemetry()
        self.assertEqual(evidence.rate_limited_responses, 1)
        self.assertEqual(evidence.safety_cooldown_remaining_seconds, 0)
        self.assertGreater(dict(evidence.account_read_cooldowns)["balance"], 9)

    async def test_cash_and_trade_queries_share_actual_endpoint_cooldown(self):
        limiter = AsyncIntervalLimiter()
        await self.fault(limiter, HISTORY)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation=CASH)
        await limiter.wait(operation=POSITIONS)

    async def test_one_recovery_probe_across_32_consumers_and_stale_success(self):
        limiter = AsyncIntervalLimiter()
        old = await limiter.wait(operation=BALANCE)
        await self.fault(limiter)
        await limiter.record_result(operation=BALANCE, duration_seconds=.1, admission_token=old)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation=BALANCE)
        limiter._account_read_backoff["account_read_balance"]["until"] = 0
        results = await asyncio.gather(*[limiter.wait(operation=BALANCE)
                                        for _ in range(32)], return_exceptions=True)
        tokens = [r for r in results if isinstance(r, str)]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(sum(isinstance(r, ApiAdmissionDeferred) for r in results), 31)
        await limiter.record_result(operation=BALANCE, duration_seconds=.1,
                                    admission_token=tokens[0])
        await limiter.wait(operation=BALANCE)

    async def test_failure_during_probe_cannot_be_erased_by_older_success(self):
        limiter = AsyncIntervalLimiter()
        await self.fault(limiter)
        state = limiter._account_read_backoff["account_read_balance"]
        state["until"] = 0
        token = await limiter.wait(operation=BALANCE)
        await self.fault(limiter, token="older")
        await limiter.record_result(operation=BALANCE, duration_seconds=.1, admission_token=token)
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation=BALANCE)

    async def test_cancelled_probe_expires_and_no_mutation_retry(self):
        limiter = AsyncIntervalLimiter()
        with patch("liquidity_bot.adapters.time.monotonic", return_value=100):
            await self.fault(limiter)
        with patch("liquidity_bot.adapters.time.monotonic", return_value=111):
            gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
            call = AsyncMock(side_effect=asyncio.CancelledError)
            with self.assertRaises(asyncio.CancelledError):
                await gateway._call(BALANCE, call)
            self.assertEqual(call.await_count, 1)
            with self.assertRaises(ApiAdmissionDeferred):
                await limiter.wait(operation=BALANCE)
            await limiter.wait(operation="cancel", request_kind="mutation")
        with patch("liquidity_bot.adapters.time.monotonic", return_value=132):
            await limiter.wait(operation=BALANCE)

    async def test_typed_429_and_failed_probe_also_preserve_backoff(self):
        limiter = AsyncIntervalLimiter()
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
        with self.assertRaises(AdapterError):
            await gateway._call(BALANCE, AsyncMock(side_effect=AdapterError(
                "limited", status_code=429, retry_after_seconds=10)))
        state = limiter._account_read_backoff["account_read_balance"]
        state["until"] = 0
        token = await limiter.wait(operation=BALANCE)
        await limiter.record_result(operation=BALANCE, duration_seconds=.1,
            admission_token=token, status_code=503, failed=True)
        self.assertTrue(state["limited"])
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation=BALANCE)

    @unittest.skipUnless(fcntl is not None, "requires POSIX shared coordination")
    async def test_shared_processes_cannot_bypass_endpoint_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            first = AsyncIntervalLimiter(shared_state_path=path)
            second = AsyncIntervalLimiter(shared_state_path=path)
            await self.fault(first)
            with self.assertRaises(ApiAdmissionDeferred):
                await second.wait(operation=BALANCE)
            await second.wait(operation="cancel", request_kind="mutation")
            evidence = await second.shared_telemetry()
            self.assertGreater(dict(evidence.account_read_cooldowns)["balance"], 9)
            state = json.loads(path.read_text())
            state["account_read_balance"]["until"] = 0
            path.write_text(json.dumps(state))
            results = await asyncio.gather(first.wait(operation=BALANCE),
                second.wait(operation=BALANCE), return_exceptions=True)
            self.assertEqual(sum(isinstance(r, str) for r in results), 1)
            self.assertEqual(sum(isinstance(r, ApiAdmissionDeferred) for r in results), 1)

    def test_bounded_exponential_backoff_and_malformed_state(self):
        states = {}
        for expected in (2, 4, 8, 16, 30, 30):
            read_backoff.record(states, "balance", 100, token=None,
                status_code=429, failed=True, retry_after_seconds=float("nan"))
            self.assertEqual(read_backoff.remaining(states, "balance", 100), expected)
        read_backoff.record(states, "balance", 100, token=None,
            status_code=429, failed=True, retry_after_seconds=9999)
        self.assertEqual(read_backoff.remaining(states, "balance", 100), 60)
        states["account_read_balance"] = {"until": "bad", "lease_until": float("inf")}
        self.assertEqual(read_backoff.remaining(states, "balance", 100), 0)
