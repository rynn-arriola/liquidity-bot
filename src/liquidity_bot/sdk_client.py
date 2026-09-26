"""One place that constructs the Polymarket US SDK client.

polymarket-us 1.0 added automatic retries for idempotent requests (GET, HEAD,
OPTIONS, DELETE on 408/409/429/5xx and network errors, two by default). The
bot's shared limiter, backoff lanes and 429 handling own retry policy and must
see every request, so SDK retries are always disabled. POST (order create and
cancel) is never retried by the SDK either way. Older SDKs without the
parameter have no automatic retries, so nothing needs disabling there.
"""

from __future__ import annotations

import inspect

from polymarket_us import AsyncPolymarketUS


def new_async_client(*, key_id: str, secret_key: str, timeout: float) -> AsyncPolymarketUS:
    kwargs = dict(key_id=key_id, secret_key=secret_key, timeout=timeout)
    if "max_retries" in inspect.signature(AsyncPolymarketUS.__init__).parameters:
        kwargs["max_retries"] = 0
    return AsyncPolymarketUS(**kwargs)
