from types import SimpleNamespace
from unittest import TestCase

from liquidity_bot.adapters import AsyncReadOnlyGateway


class ErrorSummaryTests(TestCase):
    def test_html_is_omitted_but_status_and_trace_survive(self):
        error = RuntimeError('<!DOCTYPE html><html>IP 192.0.2.1 private-token</html>')
        error.status_code = 429
        error.response = SimpleNamespace(headers={'cf-ray': 'trace-EWR', 'retry-after': '10'})
        result = AsyncReadOnlyGateway._adapter_error('GET /v1/portfolio/activities', error)
        self.assertNotIn('192.0.2.1', str(result))
        self.assertNotIn('private-token', str(result))
        self.assertNotIn('<html>', str(result))
        self.assertIn('omitted', str(result))
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.trace_id, 'trace-EWR')
        self.assertEqual(result.retry_after_seconds, 10)

    def test_plain_error_stays_useful_and_bounded(self):
        error = RuntimeError('NotFound: Order not found')
        result = AsyncReadOnlyGateway._adapter_error('GET /v1/order/example', error)
        self.assertIn('NotFound: Order not found', str(result))
        result = AsyncReadOnlyGateway._adapter_error('GET /test', RuntimeError('x' * 10000))
        self.assertLess(len(str(result)), 800)

    def test_sensitive_headers_and_injected_diagnostic_fields_are_not_echoed(self):
        error = RuntimeError('X-PM-Access-Key: private-token')
        error.response = SimpleNamespace(headers={'cf-ray': '<html>private-token</html>'})
        error.body = {'code': 'bad\nprivate-token'}
        result = AsyncReadOnlyGateway._adapter_error('GET /test', error)
        self.assertNotIn('private-token', str(result))
        self.assertIsNone(result.trace_id)
        self.assertIsNone(result.error_code)
