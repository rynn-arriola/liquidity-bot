"""Bounded per-endpoint account-read recovery; never mutation authority.

The limiter calls these pure helpers under its local or host-wide lock.
Cash/trade history share a circuit because both use the activities endpoint.
"""
from __future__ import annotations

import math
import socket

from httpx import TransportError
from polymarket_us.errors import APIConnectionError

from .errors import AdapterError, ApiAdmissionDeferred


ENDPOINTS = ("balance", "positions", "activities", "open_orders", "order_retrieve")
LEASE_SECONDS = 20.0  # Beyond the configured 15-second HTTP timeout.
MAX_SECONDS = 60.0
TRANSIENT_STATUSES = (500, 502, 503, 504)


def is_transport_failure(exc):
    """Only typed network failures or explicit adapter causes qualify."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror,
                            TransportError, APIConnectionError)):
            return True
        if not isinstance(exc, AdapterError):
            return False
        exc = exc.__cause__
    return False


def endpoint_for(category):
    if category in {"execution_history", "cash_history"}:
        return "activities"
    return category if category in ENDPOINTS else None


def is_backpressure(exc):
    return (isinstance(exc, ApiAdmissionDeferred)
            and exc.error_code == "LOCAL_ACCOUNT_READ_COOLDOWN"
            or isinstance(exc, AdapterError)
            and exc.status_code in (429, *TRANSIENT_STATUSES)
            or is_transport_failure(exc))


def _stamp(value, now, maximum=MAX_SECONDS):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) and 0 <= value <= now + maximum + 1 else 0.0


def _state(states, endpoint):
    key = "account_read_" + endpoint
    raw = states.get(key)
    if not isinstance(raw, dict):
        raw = {}
        states[key] = raw
    return raw


def remaining(states, endpoint, now):
    raw = states.get("account_read_" + endpoint, {})
    if not isinstance(raw, dict):
        return 0.0
    return max(0.0, _stamp(raw.get("until"), now) - now,
               _stamp(raw.get("lease_until"), now, LEASE_SECONDS) - now)


def admit(states, endpoint, now, token, *, claim):
    delay = remaining(states, endpoint, now)
    if delay > 0:
        raise ApiAdmissionDeferred(
            f"{endpoint} read backoff active; request not sent",
            operation=f"account-read:{endpoint}",
            error_code="LOCAL_ACCOUNT_READ_COOLDOWN", retry_after_seconds=delay)
    if claim:
        state = _state(states, endpoint)
        if state.get("limited") is True:
            state.update(token=token, lease_until=now + LEASE_SECONDS)


def record(states, endpoint, now, *, token, status_code, failed, retry_after_seconds,
           transport_failure=False):
    state = _state(states, endpoint)
    before = dict(state)
    if status_code in (429, *TRANSIENT_STATUSES) or transport_failure:
        # Retain compatibility with the existing shared file on rollback.
        last = _stamp(state.get("last_failure", state.get("last_429")), now)
        try:
            streak = min(5, max(0, int(state.get("streak", 0))))
        except (TypeError, ValueError, OverflowError):
            streak = 0
        if last <= 0 or now - last > 60:
            streak = 0
        streak += 1
        delay = min(30, 2 ** min(streak, 5))
        try:
            requested = float(retry_after_seconds)
        except (TypeError, ValueError, OverflowError):
            requested = 0.0
        if math.isfinite(requested):
            delay = min(MAX_SECONDS, max(delay, requested))
        # An older failure cannot free another in-flight probe. An older
        # success cannot clear this new failure because its token is revoked.
        other_lease = (_stamp(state.get("lease_until"), now, LEASE_SECONDS)
                       if state.get("token") != token else 0.0)
        state.update(limited=True, last_failure=now, streak=streak, token=None,
                     lease_until=other_lease,
                     until=max(_stamp(state.get("until"), now), now + delay))
        if status_code == 429:
            state["last_429"] = now
    elif token is not None and token == state.get("token"):
        state.update(token=None, lease_until=0.0)
        if failed:
            state["until"] = now + 2.0
        else:
            state.update(limited=False, until=0.0)
    return state != before
