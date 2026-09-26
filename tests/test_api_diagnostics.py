"""429 provenance uses fake responses, not additional network requests."""
import asyncio
import io
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from liquidity_bot.adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from liquidity_bot.api_diagnostics import api_workflow
from liquidity_bot.errors import AdapterError, ApiAdmissionDeferred
from liquidity_bot.live_orders import AsyncTradingGateway


class DiagnosticsTests(IsolatedAsyncioTestCase):
    async def test_every_endpoint_and_mutation_429_has_one_redacted_record(self):
        cases = (
            ('GET /v1/account/balances', 'read', 'balance'),
            ('GET /v1/portfolio/positions', 'read', 'positions'),
            ('GET /v1/portfolio/activities', 'read', 'execution_history'),
            ('GET /v1/portfolio/activities (cash only)', 'read', 'cash_history'),
            ('GET /v1/orders/open', 'read', 'open_orders'),
            ('GET /v1/order/secret-value', 'read', 'order_retrieve'),
            ('markets.book(secret-value)', 'read', 'market_book'),
            ('GET /v1/incentives (symbol-filtered)', 'read', 'incentive_contract_batch'),
            ('GET /v1/incentives/earnings', 'read', 'earnings'),
            ('POST /v1/orders', 'mutation', 'mutation_create'),
            ('POST /v1/order/secret-value/cancel', 'mutation', 'mutation_cancel'),
        )
        for operation, kind, category in cases:
            limiter = AsyncIntervalLimiter()
            output = io.StringIO()
            with patch('sys.stdout', output):
                await limiter.record_result(duration_seconds=.1, status_code=429,
                    failed=True, operation=operation, request_kind=kind,
                    traffic_class='safety', retry_after_seconds=10,
                    server_ids={'cf-ray': 'abc-EWR', 'authorization': 'secret-value'})
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0].split(' ', 1)[1])
            self.assertEqual(row['category'], category)
            self.assertEqual(row['status'], 429)
            self.assertEqual(row['server_ids'], {'cf-ray': 'abc-EWR'})
            self.assertNotIn('secret-value', output.getvalue())
            self.assertEqual(row['workflow'], ['unattributed'])
            self.assertEqual(limiter.telemetry().rate_limited_responses, 1)

    async def test_nested_workflow_propagates_to_workers_and_resets(self):
        output = io.StringIO()
        limiter = AsyncIntervalLimiter()
        async def emit():
            await limiter.record_result(duration_seconds=.1, status_code=429,
                failed=True, operation='GET /v1/account/balances')
        @api_workflow('account_reconcile')
        async def read():
            await asyncio.create_task(emit())
        @api_workflow('market_top_up')
        async def scan():
            await read()
            raise RuntimeError('test')
        with patch('sys.stdout', output):
            with self.assertRaises(RuntimeError):
                await scan()
            await emit()
        rows = [json.loads(line.split(' ', 1)[1]) for line in output.getvalue().splitlines()]
        self.assertEqual(rows[0]['workflow'], ['market_top_up', 'account_reconcile'])
        self.assertEqual(rows[1]['workflow'], ['unattributed'])

    async def test_read_header_ids_and_no_inline_account_retry(self):
        class Limited(Exception):
            status_code = 429
            response = SimpleNamespace(headers={'CF-Ray': 'abc-EWR',
                'X-Request-ID': '123-abc', 'Retry-After': '10',
                'Authorization': 'secret-value'})
        gateway = AsyncReadOnlyGateway(None, limiter=AsyncIntervalLimiter())
        calls = 0
        async def read():
            nonlocal calls
            calls += 1
            raise Limited()
        output = io.StringIO()
        with patch('sys.stdout', output):
            with self.assertRaises(AdapterError):
                await gateway._call('GET /v1/account/balances', read)
        row = json.loads(output.getvalue().split(' ', 1)[1])
        self.assertEqual(calls, 1)
        self.assertEqual(row['retry_after_seconds'], 10)
        self.assertEqual(row['server_ids'], {'cf-ray': 'abc-EWR', 'x-request-id': '123-abc'})

    async def test_other_errors_and_local_deferral_are_not_429(self):
        gateway = AsyncReadOnlyGateway(None, limiter=AsyncIntervalLimiter())
        output = io.StringIO()
        async def checkpoint():
            raise ApiAdmissionDeferred('not sent', error_code='LOCAL_CATALOG_ACCOUNT_CHANGED')
        async def read():
            self.fail('must not send')
        with patch('sys.stdout', output):
            with gateway.read_checkpoints(checkpoint):
                with self.assertRaises(ApiAdmissionDeferred):
                    await gateway._call('GET /v1/account/balances', read)
            await gateway.limiter.record_result(duration_seconds=.1, status_code=503,
                failed=True, operation='GET /v1/portfolio/positions')
        self.assertEqual(output.getvalue(), '')

    async def test_logging_failure_cannot_prevent_rate_limit_accounting(self):
        limiter = AsyncIntervalLimiter()
        with patch('builtins.print', side_effect=OSError('closed pipe')):
            await limiter.record_result(duration_seconds=.1, status_code=429,
                failed=True, operation='GET /v1/account/balances')
        self.assertEqual(limiter.telemetry().rate_limited_responses, 1)

    async def test_mutation_success_and_429_preserve_single_send_and_trace(self):
        gateway = AsyncTradingGateway(AsyncReadOnlyGateway(None, limiter=AsyncIntervalLimiter()))
        calls = 0
        async def send():
            nonlocal calls
            calls += 1
            return {'ok': True}
        self.assertEqual(await gateway._mutation('POST /v1/orders', send), {'ok': True})
        async def reject():
            nonlocal calls
            calls += 1
            raise AdapterError('limited', status_code=429, trace_id='abc-EWR')
        output = io.StringIO()
        with patch('sys.stdout', output):
            with self.assertRaises(AdapterError) as caught:
                await gateway._mutation('POST /v1/orders', reject)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(calls, 2)
        row = json.loads(output.getvalue().split(' ', 1)[1])
        self.assertEqual(row['server_ids'], {'trace-id': 'abc-EWR'})
        self.assertEqual(row['category'], 'mutation_create')

    async def test_only_code_owned_reasons_enter_trace(self):
        limiter = AsyncIntervalLimiter()
        @api_workflow('account_reconcile', reason_kw='tag')
        async def read(*, tag):
            await limiter.record_result(duration_seconds=.1, status_code=429,
                failed=True, operation='GET /v1/portfolio/positions')
        output = io.StringIO()
        with patch('sys.stdout', output):
            await read(tag='periodic')
            await read(tag='secret-value')
        rows = [json.loads(line.split(' ', 1)[1]) for line in output.getvalue().splitlines()]
        self.assertEqual(rows[0]['workflow'], ['account_reconcile:periodic'])
        self.assertEqual(rows[1]['workflow'], ['account_reconcile'])
        self.assertNotIn('secret-value', output.getvalue())


class EvidenceNeverLostTests(IsolatedAsyncioTestCase):
    """2026-09-26: 266 runner 429s produced only 4 [api-429] lines."""

    async def test_http_date_retry_after_still_logs_the_429(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            await AsyncIntervalLimiter().record_result(
                duration_seconds=.1, status_code=429, failed=True,
                operation='GET /v1/account/balances', request_kind='read',
                retry_after_seconds='Sat, 26 Sep 2026 21:00:00 GMT')
        row = json.loads(output.getvalue().splitlines()[0].split(' ', 1)[1])
        self.assertEqual((row['category'], row['retry_after_seconds']), ('balance', None))

    async def test_a_logging_failure_prints_a_fallback_line(self):
        output = io.StringIO()
        with patch('sys.stdout', output), \
                patch('liquidity_bot.api_diagnostics.json.dumps', side_effect=RuntimeError('x')):
            await AsyncIntervalLimiter().record_result(
                duration_seconds=.1, status_code=429, failed=True,
                operation='GET /v1/portfolio/positions', request_kind='read')
        self.assertIn('[api-429] category=positions lane=safety log_error=RuntimeError',
                      output.getvalue())

    async def test_exact_order_reads_are_counted_by_workflow_and_reset(self):
        from liquidity_bot.api_diagnostics import take_order_read_counts
        take_order_read_counts()
        limiter = AsyncIntervalLimiter()

        @api_workflow('absent_repair')
        async def repair():
            await limiter.record_result(duration_seconds=.1, operation='GET /v1/order/a')
            await limiter.record_result(duration_seconds=.1, status_code=404, failed=True,
                                        operation='GET /v1/order/b')

        await repair()
        await limiter.record_result(duration_seconds=.1, operation='GET /v1/order/c')
        await limiter.record_result(duration_seconds=.1, operation='GET /v1/orders/open')
        self.assertEqual(dict(take_order_read_counts()),
                         {'absent_repair@ok': 1, 'absent_repair@404': 1, 'unattributed@ok': 1})
        self.assertEqual(take_order_read_counts(), [])
