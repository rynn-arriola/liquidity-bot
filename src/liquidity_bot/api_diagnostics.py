"""Bounded request provenance; diagnostic only, never API authority."""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import math
import os
import re

_workflow = ContextVar("api_workflow", default=())
_REASONS = frozenset({"startup", "periodic", "rest-source-decision",
    "rest-source-bind", "operator-source-handoff", "private-shard-handoff",
    "strategy-recovery-start", "strategy-recovery-terminal", "hard-exit",
    "position-transition", "hybrid-preplan"})
_ENDPOINTS = {
    "balance": "GET /v1/account/balances", "positions": "GET /v1/portfolio/positions",
    "open_orders": "GET /v1/orders/open", "order_retrieve": "GET /v1/order/{id}",
    "execution_history": "GET /v1/portfolio/activities",
    "cash_history": "GET /v1/portfolio/activities",
    "market_book": "GET /v1/markets/{slug}/book",
    "mutation_create": "POST /v1/orders", "mutation_cancel": "POST /v1/order/{id}/cancel",
    "earnings": "GET /v1/incentives/earnings",
    "incentive_contract_batch": "GET /v1/incentives",
}


def api_workflow(name, *, reason_kw=None):
    """Static code-owned labels propagate into read workers, never arguments."""
    if not re.fullmatch(r"[a-z_]{1,48}", name):
        raise ValueError("invalid workflow label")
    def decorate(method):
        @wraps(method)
        async def wrapped(*args, **kwargs):
            reason = kwargs.get(reason_kw) if reason_kw else None
            label = name + ":" + reason if isinstance(reason, str) and reason in _REASONS else name
            token = _workflow.set((*_workflow.get(), label)[-8:])
            try:
                return await method(*args, **kwargs)
            finally:
                _workflow.reset(token)
        return wrapped
    return decorate


def response_ids(headers, trace_id=None):
    """Whitelist opaque request IDs; never print bodies, URLs or auth headers."""
    allowed = {"cf-ray", "x-trace-id", "trace-id", "x-request-id"}
    result = {}
    for key, value in headers.items():
        key = str(key).lower()
        value = str(value)
        if key in allowed and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            result[key] = value
    if trace_id and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", str(trace_id)):
        result.setdefault("trace-id", str(trace_id))
    return result


def log_rate_limit(*, category, request_kind, traffic_class, duration_seconds,
                   retry_after_seconds, server_ids=None):
    # Logging must not turn a response-backed rejection into an unknown result
    # or prevent the limiter from publishing the original cooldown.
    try:
        retry = None if retry_after_seconds is None else float(retry_after_seconds)
        if retry is not None and not math.isfinite(retry):
            retry = None
        print("[api-429] " + json.dumps({
            "at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
            "status": 429, "category": category, "kind": request_kind,
            "endpoint": _ENDPOINTS.get(category, category),
            "lane": traffic_class, "workflow": list(_workflow.get()) or ["unattributed"],
            "response_seconds": round(duration_seconds, 3),
            "retry_after_seconds": retry,
            "server_ids": response_ids(server_ids or {}),
        }, separators=(",", ":")), flush=True)
    except Exception:
        pass
