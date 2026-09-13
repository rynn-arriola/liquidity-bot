"""Discovery price reads may slow; safety proof and stop must not queue behind them."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway, fcntl, shared_api_limiter
from liquidity_bot.errors import ApiAdmissionDeferred


class OptionalBookPacingTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(fcntl is None, "production coordinator is POSIX")
    def test_factory_uses_one_second_routine_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            limiter = shared_api_limiter(
                state_path=Path(directory) / "state")
        self.assertEqual(limiter.optional_book_interval_seconds, 1)

    def test_background_books_do_not_use_routine_entry_allowance(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=4)
        state = {"next_optional_book_at": 1004}
        self.assertEqual(limiter._optional_book_due(
            state, 1000, "background"), 0)
        limiter._record_book_claim(state, 1000, "background")
        self.assertEqual(state["next_optional_book_at"], 1004)
        self.assertEqual(limiter._optional_book_due(state, 1000, "entry"), 1004)

    async def test_nonwaiting_flat_books_are_fair_and_do_not_hold_safety(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=4)
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
        call = AsyncMock(return_value="book")
        with gateway.optional_book_reads():
            await gateway._call("markets.book(first)", call)
            for slug in ("second", "third", "first"):
                with self.assertRaises(ApiAdmissionDeferred):
                    await gateway._call(f"markets.book({slug})", call)
            with gateway.traffic_class("safety"):
                await gateway._call("markets.book(position)", call)
                await gateway._call("GET /v1/portfolio/positions", call)
            # The first owner must not monopolize each new four-second slot.
            for slug in ("second", "third", "first"):
                limiter._book_backoff["next_optional_book_at"] = 0
                await gateway._call(f"markets.book({slug})", call)
        self.assertEqual(call.await_count, 6)
        self.assertEqual(limiter.telemetry().waiting_now, 0)
        self.assertEqual(limiter._optional_book_waiters, {})
        self.assertFalse(gateway._defer_optional_book.get())
        self.assertIsNone(gateway._traffic_class_override.get())

    async def test_abandoned_optional_demand_expires_and_queue_is_bounded(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=4)
        limiter._optional_book_waiters = {f"old-{n}": time.monotonic() + 20 for n in range(128)}
        with self.assertRaises(ApiAdmissionDeferred):
            await limiter.wait(operation="markets.book(new)", traffic_class="entry",
                               defer_optional_book=True)
        self.assertEqual(len(limiter._optional_book_waiters), 128)
        limiter._optional_book_waiters = {"abandoned": time.monotonic() - 1}
        await limiter.wait(operation="markets.book(new)", traffic_class="entry",
                           defer_optional_book=True)
        self.assertEqual(limiter._optional_book_waiters, {})

    async def test_full_32_owner_slate_gets_one_optional_book_each_without_starvation(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=4)
        accepted = []
        for tick in range(125):
            with patch("liquidity_bot.adapters.time.monotonic", return_value=1000 + tick):
                for owner in range(32):
                    try:
                        await limiter.wait(operation=f"markets.book({owner})", traffic_class="entry",
                                           defer_optional_book=True)
                    except ApiAdmissionDeferred:
                        continue
                    accepted.append((tick, owner))
                # Position books and account reads proceed even while the
                # entire optional slate is waiting. No real time/network IO.
                await limiter.wait(operation="markets.book(held)", traffic_class="safety")
                await limiter.wait(operation="GET /v1/portfolio/positions", traffic_class="safety")
        self.assertEqual(accepted, [(owner * 4, owner) for owner in range(32)])
        self.assertEqual(limiter.telemetry().attempts, 32 + 250)

    async def test_local_optional_books_are_paced_but_safety_bypasses(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=.12)
        await limiter.wait(operation="markets.book(one)", traffic_class="entry")
        pending = asyncio.create_task(limiter.wait(operation="markets.book(two)", traffic_class="entry"))
        await asyncio.sleep(.01)
        self.assertFalse(pending.done())
        for op, kind in (("GET /v1/orders/open", "read"), ("cancel", "mutation"),
                         ("markets.book(position)", "read")):
            await asyncio.wait_for(limiter.wait(operation=op, request_kind=kind, traffic_class="safety"), .05)
        self.assertFalse(pending.done())
        await asyncio.wait_for(pending, .3)
        self.assertEqual(limiter.telemetry().attempts, 5)

    async def test_wait_checkpoint_can_interrupt_without_sending_or_reserving(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=.5)
        await limiter.wait(operation="markets.book(one)", traffic_class="entry")
        gateway = AsyncReadOnlyGateway(object(), limiter=limiter)
        checkpoints = 0

        async def changed_position():
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 3:
                raise ApiAdmissionDeferred("position changed", retry_after_seconds=1)

        call = AsyncMock()
        with gateway.traffic_class("entry"), gateway.read_checkpoints(changed_position):
            with self.assertRaises(ApiAdmissionDeferred):
                await gateway._call("markets.book(two)", call)
        call.assert_not_awaited()
        self.assertEqual(limiter.telemetry().attempts, 1)
        self.assertEqual(limiter.telemetry().waiting_now, 0)

    async def test_operator_stop_interrupts_price_wait_and_shutdown_remains_available(self):
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=4)
        await limiter.wait(operation="markets.book(one)", traffic_class="entry")
        pending = asyncio.create_task(limiter.wait(operation="markets.book(two)", traffic_class="entry"))
        await asyncio.sleep(.01)
        limiter.interrupt_waits()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await asyncio.wait_for(limiter.wait(operation="cancel", request_kind="mutation"), .1)
        self.assertEqual(limiter.telemetry().waiting_now, 0)

    async def test_one_success_does_not_reset_repeated_rejection_streak(self):
        limiter = AsyncIntervalLimiter()
        for expected in (2, 4, 8):
            await limiter.record_result(operation="markets.book(one)", duration_seconds=0,
                status_code=429, failed=True)
            delay = limiter._book_backoff["book_cooldown_until"] - time.monotonic()
            self.assertAlmostEqual(delay, expected, delta=.1)
            limiter._book_backoff["book_cooldown_until"] = 0
            token = await limiter.wait(operation="markets.book(probe)")
            await limiter.record_result(operation="markets.book(probe)", duration_seconds=0,
                admission_token=token)
        limiter._book_backoff["book_last_429_at"] -= 61
        await limiter.record_result(operation="markets.book(one)", duration_seconds=0,
            status_code=429, failed=True)
        self.assertEqual(limiter._book_backoff["book_429_streak"], 1)

    @unittest.skipIf(fcntl is None, "production coordinator is POSIX")
    async def test_shared_factory_paces_across_peers_and_only_claims_when_due(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            first = shared_api_limiter(state_path=path)
            peer = shared_api_limiter(state_path=path)
            self.assertEqual(first.optional_book_interval_seconds, 1)
            with patch("liquidity_bot.adapters.time.time", return_value=1000):
                self.assertEqual(first._claim_shared_slot(.5, "background", "a"), (0, 0))
            with patch("liquidity_bot.adapters.time.time", return_value=1000.25):
                # Account safety uses only the ordinary aggregate clock.
                self.assertEqual(peer._claim_shared_slot(.25, "safety"), (0, 0))
            with patch("liquidity_bot.adapters.time.time", return_value=1000.5):
                # Background scan books retain their independent 2/s lane and
                # neither wait on nor write the routine-entry book allowance.
                self.assertEqual(peer._claim_shared_slot(.5, "background", "b"), (0, 0))
            with patch("liquidity_bot.adapters.time.time", return_value=1000.75):
                self.assertEqual(peer._claim_shared_slot(.25, "entry", "c"), (0, 0))
                before = path.read_bytes()
                with self.assertRaises(ApiAdmissionDeferred):
                    peer._claim_shared_slot(.25, "entry", "d", defer_optional_book=True)
                self.assertEqual(path.read_bytes(), before)
            with patch("liquidity_bot.adapters.time.time", return_value=1001):
                self.assertEqual(first._claim_shared_slot(.5, "background", "e"), (0, 0))
            with patch("liquidity_bot.adapters.time.time", return_value=1001.25):
                self.assertEqual(peer._claim_shared_slot(.25, "safety", "position"), (0, 0))
            with patch("liquidity_bot.adapters.time.time", return_value=1001.5):
                self.assertAlmostEqual(first._claim_shared_slot(.25, "entry", "f")[0], .25)
            with patch("liquidity_bot.adapters.time.time", return_value=1001.75):
                self.assertEqual(first._claim_shared_slot(.25, "entry", "f"), (0, 0))
            state = json.loads(path.read_text())
            self.assertEqual(state["next_request_at"], 1002)
            self.assertEqual(state["next_optional_book_at"], 1002.75)
            self.assertNotIn("cooldown_until_safety", state)

    def test_invalid_interval_and_corrupt_deadlines_are_bounded(self):
        for value in (-1, 61, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                AsyncIntervalLimiter(optional_book_interval_seconds=value)
        limiter = AsyncIntervalLimiter(optional_book_interval_seconds=4)
        for value in ("bad", float("nan"), float("inf"), 999999):
            self.assertEqual(limiter._optional_book_due({"next_optional_book_at": value}, 1000, "entry"), 0)
        self.assertEqual(limiter._optional_book_due({"next_optional_book_at": 1004}, 1000, "safety"), 0)
