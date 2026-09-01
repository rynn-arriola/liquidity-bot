from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from liquidity_bot.adapters import (
    AdapterError,
    ApiAdmissionDeferred,
    AsyncIntervalLimiter,
    AsyncReadOnlyGateway,
    parse_book_response,
    parse_trade_response,
)
from liquidity_bot.errors import EmptyBookError
from liquidity_bot.private_stream import PrivateEventKind, decode_private_message


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeMarkets:
    def __init__(self):
        self.list_responses = []
        self.market_response = None
        self.book_response = None
        self.settlement_response = None
        self.list_calls = []

    async def list(self, params):
        self.list_calls.append(params)
        return self.list_responses.pop(0)

    async def retrieve_by_slug(self, slug):
        self.market_slug = slug
        return self.market_response

    async def book(self, slug):
        self.book_slug = slug
        return self.book_response

    async def settlement(self, slug):
        self.settlement_slug = slug
        return self.settlement_response


class FakeEvents:
    def __init__(self):
        self.list_responses = []
        self.list_calls = []

    async def list(self, params):
        self.list_calls.append(params)
        return self.list_responses.pop(0)


class FakeClient:
    def __init__(self):
        self.markets = FakeMarkets()
        self.events = FakeEvents()
        self.get_responses = []
        self.get_calls = []

    async def get(self, path, *, query=None, authenticated=False):
        self.get_calls.append((path, query, authenticated))
        return self.get_responses.pop(0)


class CountingLimiter:
    def __init__(self):
        self.calls = 0

    async def wait(self):
        self.calls += 1


class RateLimited(Exception):
    status_code = 429


class StructuredHTTPError(Exception):
    def __init__(self, status_code, *, headers=None, body=None):
        super().__init__("structured failure")
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code, headers=headers or {})
        self.body = body


class GatewayTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_incentives_follow_next_page_token_and_preserve_all_periods(self):
        client = FakeClient()
        client.get_responses = [fixture("incentives_page_1.json"),
                                fixture("incentives_page_2.json")]
        gateway = AsyncReadOnlyGateway(client)
        found = self.run_async(gateway.fetch_incentives(["market-one", "market-two"]))

        self.assertEqual(set(found), {"market-one", "market-two"})
        self.assertEqual([p.period for p in found["market-one"].periods],
                         ["day_of", "live"])
        self.assertIsNone(found["market-one"].reported_event_start)
        self.assertEqual(client.get_calls[0][1]["symbols"],
                         ["market-one", "market-two"])
        self.assertEqual(client.get_calls[0][1]["page_size"], 100)
        self.assertEqual(
            client.get_calls[0][1]["program_type"], "liquidityProgram")
        self.assertEqual(client.get_calls[1][1]["page_token"], "page-two")
        self.assertNotIn("pageSize", client.get_calls[0][1])
        self.assertTrue(all(call[2] for call in client.get_calls))

    def test_unfiltered_incentive_discovery_omits_symbols_and_paginates(self):
        client = FakeClient()
        client.get_responses = [fixture("incentives_page_1.json"),
                                fixture("incentives_page_2.json")]

        found = self.run_async(
            AsyncReadOnlyGateway(client).list_incentives(statuses=("active",)))

        self.assertEqual(set(found), {"market-one", "market-two"})
        self.assertNotIn("symbols", client.get_calls[0][1])
        self.assertEqual(client.get_calls[0][1]["statuses"], ["active"])
        self.assertEqual(client.get_calls[0][1]["page_size"], 100)
        self.assertEqual(client.get_calls[1][1]["page_token"], "page-two")
        self.assertTrue(all(call[2] for call in client.get_calls))

    def test_unfiltered_incentive_discovery_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            self.run_async(AsyncReadOnlyGateway(FakeClient()).list_incentives(
                statuses=("mystery",)))

    def test_incentive_universe_has_a_separate_larger_page_ceiling(self):
        client = FakeClient()
        first = fixture("incentives_page_1.json")
        first["nextPageToken"] = "page-two"
        second = fixture("incentives_page_2.json")
        second["nextPageToken"] = None
        client.get_responses = [first, second]

        found = self.run_async(AsyncReadOnlyGateway(
            client,
            max_pages=1,
            incentive_max_pages=2,
        ).list_incentives())

        self.assertEqual(set(found), {"market-one", "market-two"})
        self.assertEqual(len(client.get_calls), 2)

    def test_incentive_page_ceiling_still_fails_closed(self):
        client = FakeClient()
        page = fixture("incentives_page_1.json")
        page["nextPageToken"] = "more"
        client.get_responses = [page]

        with self.assertRaisesRegex(
                AdapterError, "incentive_max_pages"):
            self.run_async(AsyncReadOnlyGateway(
                client, incentive_max_pages=1).list_incentives())

    def test_repeated_pagination_token_fails_closed(self):
        page = fixture("incentives_page_1.json")
        client = FakeClient()
        client.get_responses = [page, page]
        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(client).fetch_incentives(["market-one"]))

    def test_each_attempt_passes_one_and_only_one_governor_gate(self):
        client = FakeClient()
        client.get_responses = [fixture("earnings.json")]
        limiter = CountingLimiter()
        self.run_async(AsyncReadOnlyGateway(client, limiter=limiter).fetch_earnings())
        self.assertEqual(limiter.calls, 1)

    def test_confirmed_429_retries_with_bounded_backoff(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                self.get_calls.append((path, query, authenticated))
                response = self.get_responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        client = Client()
        client.get_responses = [RateLimited("slow down"), fixture("earnings.json")]
        limiter = CountingLimiter()
        rows = self.run_async(AsyncReadOnlyGateway(
            client, limiter=limiter, retry_attempts=2,
            retry_base_seconds=0.25, sleep=fake_sleep).fetch_earnings())
        self.assertEqual(len(rows), 3)
        self.assertEqual(limiter.calls, 2)
        self.assertEqual(sleeps, [0.25])

    def test_limiter_telemetry_counts_each_429_attempt_and_response(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                response = self.get_responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        async def no_sleep(_seconds):
            return None

        client = Client()
        client.get_responses = [RateLimited("slow down"), fixture("earnings.json")]
        limiter = AsyncIntervalLimiter()

        with patch.object(
                AsyncIntervalLimiter,
                "RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS", 0.0):
            self.run_async(AsyncReadOnlyGateway(
                client,
                limiter=limiter,
                retry_attempts=2,
                default_traffic_class="safety",
                sleep=no_sleep,
            ).fetch_earnings())
        evidence = limiter.telemetry()

        self.assertEqual(evidence.attempts, 2)
        self.assertEqual(evidence.failed_responses, 1)
        self.assertEqual(evidence.successful_responses, 1)
        self.assertEqual(evidence.rate_limited_responses, 1)
        self.assertEqual(evidence.attempts_by_category, (("earnings", 2),))

    def test_429_honors_retry_after_and_preserves_diagnostics(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                response = self.get_responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        client = Client()
        client.get_responses = [StructuredHTTPError(
            429,
            headers={"Retry-After": "2.5", "X-Trace-Id": "trace-429"},
            body={"code": "RATE_LIMITED"}), fixture("earnings.json")]
        rows = self.run_async(AsyncReadOnlyGateway(
            client, limiter=CountingLimiter(), retry_attempts=2,
            retry_base_seconds=.25,
            sleep=fake_sleep).fetch_earnings())

        self.assertEqual(len(rows), 3)
        self.assertEqual(sleeps, [2.5])

        client.get_responses = [StructuredHTTPError(
            422, headers={"X-Trace-Id": "trace-422"},
            body={"errorCode": "INVALID_QUANTITY"})]
        with self.assertRaises(AdapterError) as raised:
            self.run_async(AsyncReadOnlyGateway(client).fetch_earnings())
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.error_code, "INVALID_QUANTITY")
        self.assertEqual(raised.exception.trace_id, "trace-422")
        self.assertIn("trace=trace-422", str(raised.exception))

    def test_unconfirmed_global_limit_text_is_not_treated_as_429(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                raise RuntimeError("Global Rate Limit Exceeded")

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(
                Client(), retry_attempts=3, sleep=fake_sleep).fetch_earnings())
        self.assertEqual(sleeps, [])

    def test_rate_constructor_stays_below_official_limit(self):
        limiter = AsyncIntervalLimiter.from_rate(4)
        self.assertAlmostEqual(limiter.minimum_interval_seconds, 0.25)
        with self.assertRaises(ValueError):
            AsyncIntervalLimiter.from_rate(21)

    def test_background_is_slow_while_entry_and_safety_keep_configured_rate(self):
        limiter = AsyncIntervalLimiter.from_rate(4)
        self.assertEqual(
            limiter._request_interval("markets.list(page)", "read", None), .5)
        self.assertEqual(
            limiter._request_interval("POST /v1/orders", "mutation", "entry"), .25)
        self.assertEqual(
            limiter._request_interval("GET /v1/orders/open", "read", "safety"),
            .25)

    def test_confirmed_429_opens_adaptive_gate_cooldown(self):
        limiter = AsyncIntervalLimiter()

        async def exercise():
            await limiter.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=7.0, traffic_class="background")

        self.run_async(exercise())
        evidence = limiter.telemetry()
        self.assertEqual(evidence.rate_limited_responses, 1)
        self.assertGreater(evidence.cooldown_remaining_seconds, 6.9)
        self.assertGreater(evidence.background_cooldown_remaining_seconds, 6.9)
        self.assertEqual(evidence.safety_cooldown_remaining_seconds, 0.0)

    def test_gateway_context_classifies_composed_background_work(self):
        class RecordingLimiter(AsyncIntervalLimiter):
            def __init__(self):
                super().__init__()
                self.classes = []

            async def wait(self, **kwargs):
                self.classes.append(kwargs.get("traffic_class"))

        limiter = RecordingLimiter()
        gateway = AsyncReadOnlyGateway(
            FakeClient(), limiter=limiter,
            default_traffic_class="background")

        async def exercise():
            await gateway._call("background", lambda: asyncio.sleep(0))
            with gateway.traffic_class("safety"):
                await gateway._call("safety", lambda: asyncio.sleep(0))
            await gateway._call("background-again", lambda: asyncio.sleep(0))

        self.run_async(exercise())
        self.assertEqual(
            limiter.classes, ["background", "safety", "background"])

    def test_shared_gate_serializes_independent_process_limiters(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            first = AsyncIntervalLimiter.from_rate(
                4, shared_state_path=state)
            second = AsyncIntervalLimiter.from_rate(
                4, shared_state_path=state)
            self.assertEqual(
                first._claim_shared_slot(0.02, "safety"), (0.0, 0.0))
            started = time.monotonic()
            delay, cooldown = second._claim_shared_slot(0.02, "safety")
            self.assertGreaterEqual(delay, 0.015)
            self.assertEqual(cooldown, 0.0)
            self.assertLess(time.monotonic() - started, 0.05)

    def test_429_cooldown_is_visible_to_an_independent_limiter(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            first = AsyncIntervalLimiter(
                shared_state_path=state)
            second = AsyncIntervalLimiter(
                shared_state_path=state)
            with patch.object(
                    AsyncIntervalLimiter,
                    "RATE_LIMIT_BASE_COOLDOWN_SECONDS", 0.02):
                self.run_async(first.record_result(
                    duration_seconds=0.1, status_code=429, failed=True,
                    retry_after_seconds=0.02,
                    traffic_class="background"))
            started = time.monotonic()
            delay, cooldown = second._claim_shared_slot(0.0, "background")
            self.assertGreaterEqual(delay, 0.015)
            self.assertGreaterEqual(cooldown, 0.015)
            self.assertLess(time.monotonic() - started, 0.05)

    def test_shared_background_cooldown_does_not_cross_into_safety_or_entry(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            background = AsyncIntervalLimiter(shared_state_path=state)
            safety = AsyncIntervalLimiter(shared_state_path=state)
            self.run_async(background.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=30.0, traffic_class="background"))

            delay, cooldown = safety._claim_shared_slot(0.0, "safety")
            entry_delay, entry_cooldown = safety._claim_shared_slot(
                0.0, "entry")

            self.assertEqual(delay, 0.0)
            self.assertEqual(cooldown, 0.0)
            self.assertEqual(entry_delay, 0.0)
            self.assertEqual(entry_cooldown, 0.0)

    def test_shared_telemetry_reports_a_peer_process_cooldown(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            publisher = AsyncIntervalLimiter(shared_state_path=state)
            observer = AsyncIntervalLimiter(shared_state_path=state)
            self.run_async(publisher.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=30.0, traffic_class="background"))

            local_only = observer.telemetry()
            shared = self.run_async(observer.shared_telemetry())

            self.assertEqual(
                local_only.background_cooldown_remaining_seconds, 0.0)
            self.assertGreater(
                shared.background_cooldown_remaining_seconds, 25.0)
            self.assertEqual(shared.entry_cooldown_remaining_seconds, 0.0)
            self.assertEqual(shared.safety_cooldown_remaining_seconds, 0.0)
            self.assertEqual(shared.rate_limited_responses, 0)

    def test_shared_telemetry_reads_state_off_the_event_loop(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            limiter = AsyncIntervalLimiter(
                shared_state_path=Path(directory) / "api-rate-limiter.state")
            event_loop_thread = threading.get_ident()
            worker_threads = []

            def read_cooldowns():
                worker_threads.append(threading.get_ident())
                return {"background": 4.0}

            with patch.object(
                    limiter, "_read_shared_cooldowns",
                    side_effect=read_cooldowns):
                evidence = self.run_async(limiter.shared_telemetry())

            self.assertEqual(len(worker_threads), 1)
            self.assertNotEqual(worker_threads[0], event_loop_thread)
            self.assertEqual(
                evidence.background_cooldown_remaining_seconds, 4.0)

    def test_shared_telemetry_file_failure_is_reporting_only(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            limiter = AsyncIntervalLimiter(
                shared_state_path=Path(directory) / "api-rate-limiter.state")
            with patch.object(
                    limiter, "_read_shared_cooldowns",
                    side_effect=OSError("temporary evidence read failure")):
                evidence = self.run_async(limiter.shared_telemetry())

            self.assertEqual(evidence.cooldown_remaining_seconds, 0.0)

    def test_background_429_does_not_cool_safety_or_entry(self):
        limiter = AsyncIntervalLimiter()

        async def exercise():
            await limiter.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=30.0, traffic_class="background")
            await asyncio.wait_for(limiter.wait(
                operation="GET /v1/orders/open", request_kind="read",
                traffic_class="safety"), timeout=0.1)
            # Reset only the aggregate clock so this assertion isolates the
            # independent entry lane rather than measuring normal 4/s pacing.
            limiter._next_at = 0.0
            await asyncio.wait_for(limiter.wait(
                operation="POST /v1/orders", request_kind="mutation",
                traffic_class="entry"), timeout=0.1)

        self.run_async(exercise())

    def test_long_non_safety_cooldown_defers_before_network_queue(self):
        limiter = AsyncIntervalLimiter()

        async def exercise():
            await limiter.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=30.0, traffic_class="background")
            with self.assertRaises(ApiAdmissionDeferred) as raised:
                await asyncio.wait_for(limiter.wait(
                    operation="markets.list(page)", request_kind="read",
                    traffic_class="background"), timeout=0.1)
            self.assertEqual(
                raised.exception.error_code, "LOCAL_API_COOLDOWN")
            self.assertGreater(raised.exception.retry_after_seconds, 1.0)

        self.run_async(exercise())

    def test_slow_configured_non_safety_pacing_is_not_mistaken_for_cooldown(self):
        limiter = AsyncIntervalLimiter(1.01)

        async def exercise():
            await limiter.wait(
                operation="markets.list(page)", request_kind="read",
                traffic_class="background")
            await asyncio.wait_for(limiter.wait(
                operation="markets.list(page)", request_kind="read",
                traffic_class="background"), timeout=1.1)

        self.run_async(exercise())
        self.assertEqual(limiter.telemetry().attempts, 2)

    def test_safety_429_cooldown_is_independent_and_bounded_to_one_second(self):
        limiter = AsyncIntervalLimiter()
        self.run_async(limiter.record_result(
            duration_seconds=0.1, status_code=429, failed=True,
            retry_after_seconds=60.0, traffic_class="safety"))

        evidence = limiter.telemetry()
        self.assertGreater(evidence.safety_cooldown_remaining_seconds, 0.8)
        self.assertLessEqual(evidence.safety_cooldown_remaining_seconds, 1.0)
        self.assertEqual(evidence.entry_cooldown_remaining_seconds, 0.0)
        self.assertEqual(evidence.background_cooldown_remaining_seconds, 0.0)

    def test_entry_lane_does_not_queue_behind_background_lane(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            limiter = adapters.shared_api_limiter(4, state_path=state)
            self.assertEqual(
                limiter._claim_shared_slot(0.5, "background"), (0.0, 0.0))
            entry_delay, entry_cooldown = limiter._claim_shared_slot(
                0.25, "entry")
            background_delay, background_cooldown = limiter._claim_shared_slot(
                0.5, "background")
            self.assertGreaterEqual(entry_delay, 0.20)
            self.assertLess(entry_delay, 0.35)
            self.assertGreaterEqual(background_delay, 0.45)
            self.assertLess(background_delay, 0.60)
            self.assertEqual(entry_cooldown, 0.0)
            self.assertEqual(background_cooldown, 0.0)

    def test_report_lane_waits_for_idle_capacity_without_throttling_background(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            limiter = adapters.shared_api_limiter(4, state_path=state)
            self.assertEqual(
                limiter._claim_shared_slot(4.0, "report"), (0.0, 0.0))
            shared = json.loads(state.read_text(encoding="utf-8"))
            self.assertIn("next_report_at", shared)
            self.assertNotIn("next_background_at", shared)
            self.assertNotIn("next_entry_at", shared)

            background_delay, background_cooldown = limiter._claim_shared_slot(
                0.5, "background")
            report_delay, report_cooldown = limiter._claim_shared_slot(
                4.0, "report")

            self.assertGreaterEqual(background_delay, 0.20)
            self.assertLess(background_delay, 0.70)
            self.assertGreaterEqual(report_delay, 3.90)
            self.assertLess(report_delay, 4.20)
            self.assertEqual(background_cooldown, 0.0)
            self.assertEqual(report_cooldown, 0.0)

    def test_report_lane_requires_an_unused_aggregate_interval(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            limiter = adapters.shared_api_limiter(4, state_path=state)
            self.assertEqual(
                limiter._claim_shared_slot(0.25, "safety"), (0.0, 0.0))

            report_delay, cooldown = limiter._claim_shared_slot(4.0, "report")

            self.assertGreaterEqual(report_delay, 0.25)
            self.assertLess(report_delay, 0.70)
            self.assertEqual(cooldown, 0.0)

    def test_report_rate_limit_isolated_from_trading_classes(self):
        limiter = AsyncIntervalLimiter()
        self.run_async(limiter.record_result(
            duration_seconds=0.1, status_code=429, failed=True,
            retry_after_seconds=30.0, traffic_class="report"))

        evidence = limiter.telemetry()
        self.assertGreater(evidence.report_cooldown_remaining_seconds, 1.8)
        self.assertEqual(evidence.background_cooldown_remaining_seconds, 0.0)
        self.assertEqual(evidence.entry_cooldown_remaining_seconds, 0.0)
        self.assertEqual(evidence.safety_cooldown_remaining_seconds, 0.0)

    def test_operator_interrupt_preempts_existing_cooldown_but_future_safety_works(self):
        limiter = AsyncIntervalLimiter()

        async def exercise():
            # Safety cooldown remains a short real wait; long non-safety
            # cooldowns now defer immediately and are covered separately.
            await limiter.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=60.0, traffic_class="safety")
            waiting = asyncio.create_task(limiter.wait(
                operation="GET /v1/orders/open", request_kind="read",
                traffic_class="safety"))
            await asyncio.sleep(0.01)
            limiter.interrupt_waits()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(waiting, timeout=0.1)
            await asyncio.wait_for(limiter.wait(
                operation="GET /v1/orders/open", request_kind="read",
                traffic_class="safety"), timeout=1.1)

        self.run_async(exercise())

    def test_operator_interrupt_preempts_read_retry_sleep(self):
        class Client(FakeClient):
            async def get(self, path, *, query=None, authenticated=False):
                raise RateLimited("confirmed 429")

        limiter = AsyncIntervalLimiter()

        async def exercise():
            retry_started = asyncio.Event()

            async def long_retry(_seconds):
                retry_started.set()
                await asyncio.sleep(3600)

            gateway = AsyncReadOnlyGateway(
                Client(), limiter=limiter, retry_attempts=2,
                max_retry_after_seconds=60.0,
                default_traffic_class="safety", sleep=long_retry)
            request = asyncio.create_task(gateway.fetch_earnings())
            await asyncio.wait_for(retry_started.wait(), timeout=0.1)
            limiter.interrupt_waits()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(request, timeout=0.1)

        self.run_async(exercise())

    def test_background_429_publishes_cooldown_without_inline_retry(self):
        class Client(FakeClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            async def get(self, path, *, query=None, authenticated=False):
                self.calls += 1
                raise RateLimited("confirmed 429")

        client = Client()
        limiter = AsyncIntervalLimiter()
        sleeps = []

        async def no_sleep(seconds):
            sleeps.append(seconds)

        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(
                client, limiter=limiter, retry_attempts=3,
                default_traffic_class="background",
                sleep=no_sleep).fetch_earnings())

        self.assertEqual(client.calls, 1)
        self.assertEqual(sleeps, [])
        self.assertGreater(
            limiter.telemetry().background_cooldown_remaining_seconds, 1.8)

    def test_shared_429_registration_does_not_block_event_loop(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            limiter = AsyncIntervalLimiter(
                shared_state_path=Path(directory) / "api-rate-limiter.state")
            entered = threading.Event()
            release = threading.Event()

            def blocked_registration(*_args):
                entered.set()
                release.wait(timeout=1.0)

            async def exercise():
                with patch.object(
                        limiter, "_register_shared_rate_limit",
                        side_effect=blocked_registration):
                    recording = asyncio.create_task(limiter.record_result(
                        duration_seconds=0.1, status_code=429, failed=True,
                        traffic_class="background"))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 0.2))
                    await asyncio.sleep(0.01)
                    self.assertFalse(recording.done())
                    release.set()
                    await recording

            self.run_async(exercise())

    def test_shared_file_wait_does_not_hold_process_asyncio_lock(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            limiter = AsyncIntervalLimiter(
                shared_state_path=Path(directory) / "api-rate-limiter.state")
            first_entered = threading.Event()
            second_entered = threading.Event()
            release = threading.Event()
            counter_lock = threading.Lock()
            calls = 0

            def blocked_first_claim(*_args):
                nonlocal calls
                with counter_lock:
                    calls += 1
                    current = calls
                if current == 1:
                    first_entered.set()
                    release.wait(timeout=1.0)
                else:
                    second_entered.set()
                return 0.0, 0.0

            async def exercise():
                with patch.object(
                        limiter, "_claim_shared_slot",
                        side_effect=blocked_first_claim):
                    background = asyncio.create_task(limiter.wait(
                        operation="markets.list(page)", request_kind="read",
                        traffic_class="background"))
                    self.assertTrue(
                        await asyncio.to_thread(first_entered.wait, 0.2))
                    safety = asyncio.create_task(limiter.wait(
                        operation="GET /v1/orders/open", request_kind="read",
                        traffic_class="safety"))
                    self.assertTrue(
                        await asyncio.to_thread(second_entered.wait, 0.2))
                    release.set()
                    await asyncio.gather(background, safety)

            self.run_async(exercise())

    def test_shared_factory_fails_closed_without_posix_locking(self):
        import liquidity_bot.adapters as adapters

        with patch.object(adapters, "fcntl", None):
            with self.assertRaisesRegex(RuntimeError, "inside WSL"):
                adapters.shared_api_limiter()
            with self.assertRaisesRegex(RuntimeError, "inside WSL"):
                AsyncIntervalLimiter(shared_state_path="ignored.state")

    def test_shared_gate_repairs_corrupt_or_stale_deadlines(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            state.write_text(json.dumps({
                "next_request_at": "not-a-number",
                "cooldown_until_safety": time.time() + 3600,
                "rate_limit_streak_background": "also-invalid",
            }), encoding="utf-8")
            limiter = AsyncIntervalLimiter(shared_state_path=state)

            started = time.monotonic()
            self.assertEqual(
                limiter._claim_shared_slot(0.0, "safety"), (0.0, 0.0))
            self.assertLess(time.monotonic() - started, 0.1)
            self.run_async(limiter.record_result(
                duration_seconds=0.1, status_code=429, failed=True,
                retry_after_seconds=float("nan"),
                traffic_class="background"))

            repaired = json.loads(state.read_text(encoding="utf-8"))
            self.assertLessEqual(
                repaired["cooldown_until_background"] - time.time(), 2.1)

    def test_slow_background_clock_does_not_throttle_shared_safety_lane(self):
        import liquidity_bot.adapters as adapters

        if adapters.fcntl is None:
            self.skipTest("shared limiter uses POSIX flock in production WSL")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "api-rate-limiter.state"
            background = adapters.shared_api_limiter(
                1, state_path=state)
            safety = adapters.shared_api_limiter(
                4, state_path=state)
            self.assertEqual(
                background._claim_shared_slot(1.0, "background"), (0.0, 0.0))

            started = time.monotonic()
            delay, cooldown = safety._claim_shared_slot(0.25, "safety")
            elapsed = time.monotonic() - started

            self.assertGreaterEqual(delay, 0.20)
            self.assertLess(delay, 0.70)
            self.assertEqual(cooldown, 0.0)
            self.assertLess(elapsed, 0.05)

    def test_limiter_telemetry_classifies_reads_without_variable_identity(self):
        client = FakeClient()
        client.market_response = None
        client.markets.market_response = fixture("market.json")
        limiter = AsyncIntervalLimiter()
        gateway = AsyncReadOnlyGateway(client, limiter=limiter)

        self.run_async(gateway.fetch_market("market-one"))
        evidence = limiter.telemetry()

        self.assertEqual(evidence.attempts, 1)
        self.assertEqual(evidence.read_attempts, 1)
        self.assertEqual(evidence.mutation_attempts, 0)
        self.assertEqual(
            evidence.attempts_by_category, (("market_contract", 1),))
        self.assertNotIn("market-one", repr(evidence))

    def test_limiter_telemetry_reset_starts_a_new_local_window(self):
        limiter = AsyncIntervalLimiter()

        self.run_async(limiter.wait(operation="GET /v1/account/balances"))
        first = limiter.telemetry(reset=True)
        second = limiter.telemetry()

        self.assertEqual(first.attempts, 1)
        self.assertEqual(first.attempts_by_category, (("balance", 1),))
        self.assertEqual(second.attempts, 0)
        self.assertEqual(second.attempts_by_category, ())

    def test_malformed_incentive_collection_is_not_empty_success(self):
        client = FakeClient()
        client.get_responses = [{"programs": {}}]
        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(client).fetch_incentives(["market-one"]))

    def test_non_liquidity_period_is_not_treated_as_book_scoring(self):
        client = FakeClient()
        page = fixture("incentives_page_1.json")
        page["programs"][0]["timePeriods"].append({
            "programId": "volume-only",
            "programType": "volumeProgram",
            "period": "day_of",
            "status": "active",
            "rewardPool": 100,
            "minTakerNotional": 10,
        })
        page["nextPageToken"] = None
        client.get_responses = [page]
        found = self.run_async(
            AsyncReadOnlyGateway(client).fetch_incentives(["market-one"]))
        self.assertEqual(
            [item.program_id for item in found["market-one"].periods],
            ["day-one", "live-one"])

    def test_earnings_preserve_paid_pending_and_skipped(self):
        client = FakeClient()
        client.get_responses = [fixture("earnings.json")]
        rows = self.run_async(AsyncReadOnlyGateway(client).fetch_earnings())
        self.assertEqual([row.status for row in rows], ["PAID", "PENDING", "SKIPPED"])
        self.assertEqual(rows[0].reward, 0.30)
        self.assertTrue(rows[0].is_paid)
        self.assertFalse(rows[1].is_paid)

    def test_earnings_can_be_scoped_to_one_liquidity_market(self):
        client = FakeClient()
        client.get_responses = [fixture("earnings.json")]
        self.run_async(AsyncReadOnlyGateway(client).fetch_earnings(
            start_date="2026-08-13",
            market_slug="market-one",
            program_type="liquidityProgram"))
        self.assertEqual(client.get_calls, [(
            "/v1/incentives/earnings",
            {
                "start_date": "2026-08-13",
                "market_slug": "market-one",
                "program_type": "liquidityProgram",
            },
            True,
        )])

    def test_market_uses_nested_wrapper_clock_and_tick(self):
        client = FakeClient()
        client.markets.market_response = fixture("market.json")
        market = self.run_async(AsyncReadOnlyGateway(client).fetch_market("market-one"))
        self.assertEqual(market.slug, "market-one")
        self.assertEqual(market.game_start.isoformat(), "2026-08-13T03:25:00+00:00")
        self.assertEqual(market.tick_size, 0.005)
        self.assertEqual(market.minimum_trade_qty, 0.25)
        self.assertEqual(
            market.sports_market_type_v2,
            "SPORTS_MARKET_TYPE_MONEYLINE",
        )
        self.assertTrue(market.is_supported_moneyline)
        self.assertEqual(market.catalog_best_bid, 0.49)
        self.assertEqual(market.catalog_best_offer, 0.51)

    def test_missing_market_clock_fails_closed(self):
        client = FakeClient()
        response = fixture("market.json")
        response["market"]["gameStartTime"] = ""
        client.markets.market_response = response
        with self.assertRaises(AdapterError):
            self.run_async(AsyncReadOnlyGateway(client).fetch_market("market-one"))

    def test_crossed_advisory_catalog_bbo_does_not_reject_active_metadata(self):
        client = FakeClient()
        response = fixture("market.json")
        response["market"]["bestBidQuote"] = "0.51"
        response["market"]["bestAskQuote"] = "0.50"
        client.markets.market_response = response

        market = self.run_async(
            AsyncReadOnlyGateway(client).fetch_market("market-one"))
        client.markets.list_responses = [{"markets": [response["market"]]}]
        catalog = self.run_async(
            AsyncReadOnlyGateway(client).list_sports_markets())

        self.assertEqual(market.slug, "market-one")
        self.assertIsNone(market.catalog_best_bid)
        self.assertIsNone(market.catalog_best_offer)
        self.assertEqual([item.slug for item in catalog], ["market-one"])
        self.assertIsNone(catalog[0].catalog_best_bid)
        self.assertIsNone(catalog[0].catalog_best_offer)

    def test_book_uses_market_data_wrapper_and_full_depth(self):
        client = FakeClient()
        client.markets.book_response = fixture("book.json")
        book = self.run_async(AsyncReadOnlyGateway(client).fetch_book("market-one"))
        self.assertEqual(book.slug, "market-one")
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(len(book.offers), 2)
        self.assertEqual(book.best_bid.price, 0.50)
        self.assertEqual(book.best_offer.price, 0.505)
        self.assertEqual(book.bid_depth, 5100)
        self.assertEqual(book.offer_depth, 5100)

    def test_empty_or_unwrapped_book_fails_closed(self):
        client = FakeClient()
        for response in ({}, {"bids": [], "offers": []},
                         {"marketData": {"marketSlug": "market-one", "bids": [], "offers": []}}):
            client.markets.book_response = response
            with self.assertRaises(AdapterError):
                self.run_async(AsyncReadOnlyGateway(client).fetch_book("market-one"))

    def test_catalog_paginates_by_offset_without_requiring_every_clock(self):
        client = FakeClient()
        one = fixture("market.json")["market"]
        two = dict(one, slug="market-two", gameStartTime="")
        three = dict(one, slug="market-three")
        client.markets.list_responses = [
            {"markets": [one, two]},
            {"markets": [three]},
        ]
        gateway = AsyncReadOnlyGateway(client, catalog_page_size=2)
        rows = self.run_async(gateway.list_sports_markets())
        self.assertEqual([row.slug for row in rows],
                         ["market-one", "market-two", "market-three"])
        self.assertEqual(rows[0].tick_size, 0.005)
        self.assertEqual(rows[0].minimum_trade_qty, 0.25)
        self.assertEqual(rows[0].status, "MARKET_STATE_OPEN")
        self.assertIsNone(rows[1].game_start)
        self.assertEqual([call["offset"] for call in client.markets.list_calls], [0, 2])

    def test_measurement_catalog_uses_one_bounded_upcoming_event_page(self):
        client = FakeClient()
        row = dict(fixture("market.json")["market"], volume="123.5",
                   liquidity="80")
        client.events.list_responses = [{"events": [{"markets": [row]}]}]
        start = datetime(2026, 8, 28, tzinfo=timezone.utc)
        end = datetime(2026, 8, 29, tzinfo=timezone.utc)

        markets = self.run_async(
            AsyncReadOnlyGateway(client).list_upcoming_sports_markets(
                start=start, end=end, limit=50))

        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].catalog_volume_usd, 123.5)
        self.assertEqual(markets[0].catalog_liquidity_usd, 80)
        self.assertEqual(client.events.list_calls, [{
            "categories": ["sports"], "active": True, "closed": False,
            "startTimeMin": start.isoformat(), "startTimeMax": end.isoformat(),
            "limit": 50, "offset": 0,
        }])

    def test_measurement_can_batch_fetch_reward_markets_by_slug(self):
        client = FakeClient()
        one = fixture("market.json")["market"]
        client.markets.list_responses = [{"markets": [
            one, dict(one, slug="not-requested")]}]

        result = self.run_async(
            AsyncReadOnlyGateway(client).fetch_markets_by_slugs(["market-one"]))

        self.assertEqual(set(result), {"market-one"})
        self.assertEqual(client.markets.list_calls[0], {
            "slug": ["market-one"], "limit": 1, "offset": 0})

    def test_bulk_contract_telemetry_is_separate_from_discovery(self):
        client = FakeClient()
        client.markets.list_responses = [{"markets": [
            fixture("market.json")["market"]]}]
        incentive_page = fixture("incentives_page_1.json")
        incentive_page["nextPageToken"] = ""
        client.get_responses = [incentive_page]
        limiter = AsyncIntervalLimiter()
        gateway = AsyncReadOnlyGateway(client, limiter=limiter)

        self.run_async(gateway.fetch_markets_by_slugs(["market-one"]))
        self.run_async(gateway.fetch_incentives(["market-one"]))

        self.assertEqual(limiter.telemetry().attempts_by_category, (
            ("incentive_contract_batch", 1),
            ("market_contract_batch", 1),
        ))

    def test_measurement_settlement_requires_matching_bounded_value(self):
        client = FakeClient()
        client.markets.settlement_response = {
            "marketSlug": "market-one",
            "settlementPrice": {"value": "1", "currency": "USD"}}

        value = self.run_async(
            AsyncReadOnlyGateway(client).fetch_settlement("market-one"))

        self.assertEqual(value, 1)
        self.assertEqual(client.markets.settlement_slug, "market-one")

    def test_trade_wrapper_preserves_contract_quantity_and_maker_side(self):
        row = parse_trade_response(fixture("trade.json"), expected_slug="market-one")
        self.assertEqual(row.slug, "market-one")
        self.assertEqual(row.price, 0.50)
        self.assertEqual(row.quantity, 4)
        self.assertEqual(row.maker_side, "ORDER_SIDE_BUY")
        self.assertEqual(row.trade_time.isoformat(), "2026-08-13T02:30:01+00:00")

    def test_malformed_trade_fails_closed(self):
        with self.assertRaises(AdapterError):
            parse_trade_response({"trade": {"marketSlug": "market-one"}})


class PrivateMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.messages = fixture("private_messages.json")

    def test_empty_order_snapshot_is_still_a_snapshot(self):
        event = decode_private_message(self.messages["orders_snapshot"])
        self.assertEqual(event.kind, PrivateEventKind.ORDER_SNAPSHOT)
        self.assertEqual(event.payload["orders"], [])

    def test_balance_snapshot_decodes(self):
        event = decode_private_message(self.messages["balance_snapshot"])
        self.assertEqual(event.kind, PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT)

    def test_decimal_position_update_is_preserved(self):
        event = decode_private_message(self.messages["position_update"])
        self.assertEqual(event.kind, PrivateEventKind.POSITION_UPDATE)
        self.assertEqual(event.payload["netPositionDecimal"], "1.750000")

    def test_documented_snake_case_order_snapshot_decodes(self):
        event = decode_private_message({
            "request_id": "orders-snake",
            "subscription_type": 1,
            "order_subscription_snapshot": {"orders": [], "eof": True},
        })
        self.assertEqual(event.kind, PrivateEventKind.ORDER_SNAPSHOT)
        self.assertEqual(event.request_id, "orders-snake")
        self.assertEqual(event.subscription_type, "1")

    def test_documented_position_and_balance_update_envelopes_decode(self):
        position = decode_private_message({
            "requestId": "position-doc",
            "positionSubscription": {"afterPosition": {
                "marketSlug": "market-one",
                "netPositionDecimal": "1.25"}},
        })
        balance = decode_private_message({
            "requestId": "balance-doc",
            "accountBalancesUpdate": {"balanceChange": {}},
        })
        self.assertEqual(position.kind, PrivateEventKind.POSITION_UPDATE)
        self.assertEqual(balance.kind, PrivateEventKind.ACCOUNT_BALANCE_UPDATE)

    def test_heartbeat_and_error_are_explicit(self):
        self.assertEqual(decode_private_message(self.messages["heartbeat"]).kind,
                         PrivateEventKind.HEARTBEAT)
        self.assertEqual(decode_private_message(self.messages["error"]).kind,
                         PrivateEventKind.ERROR)

    def test_unknown_message_fails_closed(self):
        with self.assertRaises(AdapterError):
            decode_private_message({"requestId": "unknown", "newSchema": {}})


if __name__ == "__main__":
    unittest.main()


class OneSidedBookTests(unittest.TestCase):
    """A one-sided book must parse. Requiring both sides stranded positions."""

    def payload(self, *, bids, offers):
        return {"marketData": {
            "marketSlug": "market-one",
            "state": "MARKET_STATE_OPEN",
            "transactTime": "2026-08-15T12:00:00Z",
            "bids": bids,
            "offers": offers,
        }}

    def level(self, price, size):
        return {"px": {"value": str(price), "currency": "USD"},
                "qty": str(size)}

    def test_a_bids_only_book_parses(self):
        """About one market in four publishes one.

        Requiring both sides meant the book never refreshed, went stale, and
        the reconciler returned book_stale before planning -- losing the
        passive reducer, the hard exit and the pre-kickoff flatten together.
        Measured 2026-08-15 on chevic-scovla: the position rode to settlement.
        """
        book = parse_book_response(
            self.payload(bids=[self.level(0.45, 100)], offers=[]))

        self.assertTrue(book.has_side("bid"))
        self.assertFalse(book.has_side("offer"))
        self.assertFalse(book.is_two_sided)
        self.assertEqual(book.best_bid.price, 0.45)

    def test_an_offers_only_book_parses(self):
        book = parse_book_response(
            self.payload(bids=[], offers=[self.level(0.55, 100)]))

        self.assertTrue(book.has_side("offer"))
        self.assertFalse(book.has_side("bid"))
        self.assertEqual(book.best_offer.price, 0.55)

    def test_a_book_empty_on_both_sides_is_still_refused(self):
        with self.assertRaises(EmptyBookError):
            parse_book_response(self.payload(bids=[], offers=[]))
