"""Bounded request provenance; diagnostic only, never API authority."""
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import math
import os
import re

_workflow = ContextVar("api_workflow", default=())
# Process-local attribution of exact order reads (API_TESTS_2026-09-26: about
# 7 GET /v1/order/{id} per placed order, source unknown). Diagnostic only.
_order_reads = Counter()


def note_order_read(status_code=None):
    """Count one exact order read by innermost workflow label and outcome."""
    try:
        stack = _workflow.get()
        label = stack[-1] if stack else "unattributed"
        outcome = "ok" if status_code is None else str(status_code)
        _order_reads[f"{label}@{outcome}"] += 1
    except Exception:
        pass


def take_order_read_counts():
    """Return and reset the counts, most frequent first."""
    counts = _order_reads.most_common()
    _order_reads.clear()
    return counts
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
        try:
            retry = None if retry_after_seconds is None else float(retry_after_seconds)
        except (TypeError, ValueError):
            retry = None  # e.g. an HTTP-date Retry-After; keep the evidence line
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
        }, separators=(",", ":"), default=str), flush=True)
    except Exception as exc:
        # 2026-09-26: 266 runner 429s but only 4 [api-429] lines; never lose
        # the evidence line silently again.
        try:
            print(f"[api-429] category={category} lane={traffic_class} "
                  f"log_error={type(exc).__name__}", flush=True)
        except Exception:
            pass
