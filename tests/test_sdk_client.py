import asyncio
import unittest
from unittest.mock import patch

from liquidity_bot import sdk_client
from liquidity_bot.adapters import REST_REQUEST_TIMEOUT_SECONDS


class NewSdk:
    def __init__(self, *, key_id, secret_key, timeout, max_retries=2):
        self.kwargs = dict(key_id=key_id, secret_key=secret_key, timeout=timeout,
                           max_retries=max_retries)


class OldSdk:
    def __init__(self, *, key_id, secret_key, timeout):
        self.kwargs = dict(key_id=key_id, secret_key=secret_key, timeout=timeout)


class SdkClientTests(unittest.TestCase):
    def build(self, cls):
        with patch.object(sdk_client, "AsyncPolymarketUS", cls):
            return sdk_client.new_async_client(key_id="k", secret_key="s", timeout=7).kwargs

    def test_sdk_retries_are_disabled_when_the_sdk_has_them(self):
        self.assertEqual(self.build(NewSdk),
                         dict(key_id="k", secret_key="s", timeout=7, max_retries=0))

    def test_older_sdk_without_retries_is_constructed_unchanged(self):
        self.assertEqual(self.build(OldSdk), dict(key_id="k", secret_key="s", timeout=7))

    def test_installed_sdk_ends_with_no_automatic_retries(self):
        client = sdk_client.new_async_client(
            key_id="k", secret_key="s", timeout=REST_REQUEST_TIMEOUT_SECONDS)
        try:
            self.assertEqual(getattr(client, "max_retries", 0), 0)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
