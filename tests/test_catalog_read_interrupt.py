"""Optional discovery IO can yield; execution and proof remain serial."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred


class CatalogReadInterruptTests(IsolatedAsyncioTestCase):
    def gateway(self):
        limiter = AsyncIntervalLimiter()
        limiter.wait = AsyncMock(return_value=None)
        limiter.record_result = AsyncMock()
        return AsyncReadOnlyGateway(object(), limiter=limiter), limiter

    async def test_checkpoint_runs_on_owner_read_worker_has_no_callback(self):
        gateway, limiter = self.gateway()
        owner = asyncio.current_task()
        entered, finished = asyncio.Event(), asyncio.Event()
        calls = 0
        error = ApiAdmissionDeferred("urgent position", retry_after_seconds=1)

        async def checkpoint():
            self.assertIs(asyncio.current_task(), owner)
            self.assertIsNone(gateway._read_checkpoint.get())
            if entered.is_set():
                raise error

        async def read():
            nonlocal calls
            calls += 1
            self.assertIsNot(asyncio.current_task(), owner)
            self.assertIsNone(gateway._read_checkpoint.get())
            self.assertEqual(gateway._traffic_class_override.get(), "background")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                # The owner cannot proceed until worker cleanup finishes.
                await asyncio.sleep(0)
                finished.set()

        with gateway.traffic_class("background"), gateway.read_checkpoints(checkpoint):
            async with asyncio.timeout(2):
                with self.assertRaises(ApiAdmissionDeferred) as caught:
                    await gateway._call("events.list", read)
        self.assertIs(caught.exception, error)
        self.assertTrue(finished.is_set())
        self.assertEqual(calls, 1)
        limiter.wait.assert_awaited_once()
        limiter.record_result.assert_not_awaited()
        self.assertIsNone(gateway._read_checkpoint.get())

    async def test_unscoped_read_stays_on_serial_owner(self):
        gateway, limiter = self.gateway()
        owner = asyncio.current_task()
        async def read():
            self.assertIs(asyncio.current_task(), owner)
            return {"ok": True}
        self.assertEqual(await gateway._call("positions", read), {"ok": True})
        limiter.record_result.assert_awaited_once()

    async def test_success_retains_one_request_and_result(self):
        gateway, limiter = self.gateway()
        result = object()
        read = AsyncMock(return_value=result)
        check = AsyncMock()
        with gateway.read_checkpoints(check):
            self.assertIs(await gateway._call("events.list", read), result)
        read.assert_awaited_once()
        limiter.wait.assert_awaited_once()
        limiter.record_result.assert_awaited_once()
        self.assertFalse(limiter.record_result.call_args.kwargs.get("failed", False))

    async def test_real_429_is_still_recorded_without_retry(self):
        gateway, limiter = self.gateway()
        error = AdapterError("rate limited", status_code=429)
        read = AsyncMock(side_effect=error)
        with gateway.read_checkpoints(AsyncMock()):
            with self.assertRaises(AdapterError) as caught:
                await gateway._call("events.list", read)
        self.assertIs(caught.exception, error)
        read.assert_awaited_once()
        limiter.record_result.assert_awaited_once()
        self.assertEqual(limiter.record_result.call_args.kwargs["status_code"], 429)

    async def test_checkpoint_programming_failure_is_not_adapter_failure(self):
        gateway, limiter = self.gateway()
        entered, finished = asyncio.Event(), asyncio.Event()
        error = RuntimeError("owner invariant")
        async def check():
            if entered.is_set():
                raise error
        async def read():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        with gateway.read_checkpoints(check):
            with self.assertRaises(RuntimeError) as caught:
                await gateway._call("events.list", read)
        self.assertIs(caught.exception, error)
        self.assertTrue(finished.is_set())
        limiter.record_result.assert_not_awaited()

    async def test_caller_cancellation_joins_read_without_retry(self):
        gateway, limiter = self.gateway()
        entered, finished = asyncio.Event(), asyncio.Event()
        async def read():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finished.set()
        with gateway.read_checkpoints(AsyncMock()):
            task = asyncio.create_task(gateway._call("events.list", read))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())
        limiter.wait.assert_awaited_once()
        limiter.record_result.assert_not_awaited()

    async def test_late_result_after_cancel_cannot_authorize_selection(self):
        gateway, limiter = self.gateway()
        entered = asyncio.Event()
        async def read():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"obsolete": True}
        async def check():
            if entered.is_set():
                raise ApiAdmissionDeferred("stop", retry_after_seconds=1)
        with gateway.read_checkpoints(check):
            with self.assertRaises(ApiAdmissionDeferred):
                await gateway._call("events.list", read)
        limiter.record_result.assert_not_awaited()
