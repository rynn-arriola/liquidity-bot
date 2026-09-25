from unittest import TestCase

import httpx
from polymarket_us.errors import (
    AuthenticationError, InternalServerError, NotFoundError, RateLimitError)

from liquidity_bot.adapters import AsyncReadOnlyGateway
from liquidity_bot.errors import AdapterError, is_not_found


def _sdk(error_type, status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://example.invalid/v1/order/x"))
    return error_type("Order not found" if status == 404 else "failure", response=response)


class NotFoundClassificationTests(TestCase):
    def test_real_sdk_not_found_is_classified_by_status_and_text(self):
        wrapped = AsyncReadOnlyGateway._adapter_error("GET /v1/order/x", _sdk(NotFoundError, 404))
        self.assertEqual(wrapped.status_code, 404)
        self.assertIn("NotFound", str(wrapped))
        self.assertTrue(is_not_found(wrapped))

    def test_other_sdk_statuses_are_not_not_found(self):
        for error_type, status in ((InternalServerError, 500), (AuthenticationError, 401),
                                   (RateLimitError, 429)):
            with self.subTest(status=status):
                wrapped = AsyncReadOnlyGateway._adapter_error("GET /v1/order/x", _sdk(error_type, status))
                self.assertFalse(is_not_found(wrapped))

    def test_status_wins_over_wording_and_legacy_text_is_retained(self):
        self.assertTrue(is_not_found(AdapterError("resource gone", status_code=404)))
        self.assertTrue(is_not_found(AdapterError("GET /v1/order/x failed: NotFoundError")))
        self.assertFalse(is_not_found(AdapterError("timeout", status_code=None)))
        self.assertFalse(is_not_found(RuntimeError("NotFound")))
