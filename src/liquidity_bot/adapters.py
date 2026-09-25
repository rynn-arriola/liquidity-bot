"""Strict asynchronous adapters for read-only Polymarket US resources.

No method in this module references the SDK's order resource. The injected
client only needs ``get`` and ``markets`` methods, which makes every network
path replaceable with deterministic fixtures.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

try:  # Production runs in WSL; tests/imports remain portable on Windows.
    import fcntl
except ImportError:  # pragma: no cover - exercised only by native Windows
    fcntl = None

from .errors import AdapterError, ApiAdmissionDeferred, EmptyBookError
from .api_diagnostics import log_rate_limit, response_ids
from . import read_backoff
from .models import (
    BookLevel,
    BookSnapshot,
    MarketIncentive,
    MarketMetadata,
    RewardEarning,
    TradePrint,
)
from .rewards import IncentivePeriod

__all__ = [
    "AdapterError",
    "ApiAdmissionDeferred",
    "EmptyBookError",
    "AsyncIntervalLimiter",
    "AsyncReadOnlyGateway",
    "LimiterTelemetry",
    "OPPORTUNISTIC_REPORT_RATE_PER_SECOND",
    "parse_book_response",
    "parse_trade_response",
    "REST_REQUEST_TIMEOUT_SECONDS",
    "shared_api_limiter",
]


# Official connection guidance calls for client-side timeout detection after
# 15 seconds. The SDK otherwise defaults to 30 seconds, long enough for one
# hung request to consume most of the private-state freshness window.
REST_REQUEST_TIMEOUT_SECONDS = 15.0
OPPORTUNISTIC_REPORT_RATE_PER_SECOND = 0.25


_QUESTION_SCHEDULED_UTC = re.compile(
    r"\bscheduled\s+for\s+"
    r"(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>\d{4})\s+at\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+"
    r"(?P<meridiem>AM|PM)\s+UTC\b",
    re.IGNORECASE,
)
_MONTH_NUMBER = {
    name.lower(): number
    for number, name in enumerate((
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ), start=1)
}


def _amount(value: Any, *, field: str) -> float:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool):
        raise AdapterError(f"{field} is not numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"{field} is not numeric") from exc
    if not math.isfinite(parsed):
        raise AdapterError(f"{field} is not finite")
    return parsed


def _timestamp(value: Any, *, field: str, required: bool = False) -> datetime | None:
    if value is None or str(value).strip() == "":
        if required:
            raise AdapterError(f"{field} is missing")
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AdapterError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(f"{field} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _question_scheduled_start(value: Any) -> datetime | None:
    """Extract the venue's explicit UTC schedule embedded in a question.

    Retail primary moneylines can temporarily expose a later
    ``gameStartTime`` than the same row's generated question.  Using the later
    value can authorize an in-play entry.  A recognized question timestamp is
    therefore an additional upper bound on pregame time, never a replacement
    for missing structured metadata.
    """

    match = _QUESTION_SCHEDULED_UTC.search(str(value or ""))
    if match is None:
        return None
    try:
        hour = int(match.group("hour"))
        if not 1 <= hour <= 12:
            raise ValueError("12-hour clock is outside 1-12")
        if match.group("meridiem").upper() == "AM":
            hour = 0 if hour == 12 else hour
        elif hour != 12:
            hour += 12
        return datetime(
            int(match.group("year")),
            _MONTH_NUMBER[match.group("month").lower()],
            int(match.group("day")),
            hour,
            int(match.group("minute")),
            tzinfo=timezone.utc,
        )
    except (KeyError, ValueError) as exc:
        raise AdapterError(
            "market.question scheduled UTC time is invalid") from exc


def _period(row: Any) -> IncentivePeriod:
    if not isinstance(row, dict):
        raise AdapterError("incentive time period is not an object")
    program_id = str(row.get("programId") or "").strip()
    program_type = str(row.get("programType") or "").strip()
    period = str(row.get("period") or "").strip()
    status = str(row.get("status") or "").strip()
    if not program_id or not program_type or not period or not status:
        raise AdapterError("incentive period identity is incomplete")
    if program_type != "liquidityProgram":
        raise AdapterError(
            f"incentive period {program_id} is not a liquidity program")
    reward_pool = _amount(row.get("rewardPool"), field="rewardPool")
    discount = _amount(row.get("discountFactor"), field="discountFactor")
    target = _amount(row.get("targetSize"), field="targetSize")
    if reward_pool < 0 or not 0 < discount <= 1 or target <= 0:
        raise AdapterError("incentive period contains invalid economics")
    return IncentivePeriod(
        program_id=program_id,
        period=period,
        status=status,
        reward_pool=reward_pool,
        discount_factor=discount,
        target_size=target,
        starts_at=_timestamp(row.get("start"), field="period.start"),
        ends_at=_timestamp(row.get("end"), field="period.end"),
    )


def _market(row: Any, *, expected_slug: str | None = None,
            require_clock: bool = False) -> MarketMetadata:
    if not isinstance(row, dict):
        raise AdapterError("market is not an object")
    slug = str(row.get("slug") or "").strip()
    if not slug:
        raise AdapterError("market slug is missing")
    if expected_slug is not None and slug != expected_slug:
        raise AdapterError(f"market slug mismatch: expected {expected_slug}, got {slug}")
    clock = _timestamp(
        row.get("gameStartTime"), field="market.gameStartTime",
        required=require_clock)
    question_clock = _question_scheduled_start(row.get("question"))
    if clock is not None and question_clock is not None:
        clock = min(clock, question_clock)
    raw_tick = row.get("orderPriceMinTickSize")
    tick = None if raw_tick is None or str(raw_tick).strip() == "" \
        else _amount(raw_tick, field="market.orderPriceMinTickSize")
    if tick is not None and tick <= 0:
        raise AdapterError("market tick size must be positive")
    raw_minimum = row.get("minimumTradeQty")
    minimum = None if raw_minimum is None or str(raw_minimum).strip() == "" \
        else _amount(raw_minimum, field="market.minimumTradeQty")
    if minimum is not None and minimum <= 0:
        raise AdapterError("market minimum trade quantity must be positive")
    raw_best_bid = row.get("bestBidQuote")
    raw_best_offer = row.get("bestAskQuote")
    if raw_best_offer is None:
        raw_best_offer = row.get("bestOfferQuote")
    try:
        best_bid = (
            None
            if raw_best_bid is None or str(raw_best_bid).strip() == ""
            else _amount(raw_best_bid, field="market.bestBidQuote"))
        best_offer = (
            None
            if raw_best_offer is None or str(raw_best_offer).strip() == ""
            else _amount(raw_best_offer, field="market.bestAskQuote"))
        if best_bid is not None and not 0 < best_bid < 1:
            raise AdapterError("market best bid must be in (0, 1)")
        if best_offer is not None and not 0 < best_offer < 1:
            raise AdapterError("market best offer must be in (0, 1)")
        if (best_bid is not None and best_offer is not None
                and best_bid >= best_offer):
            raise AdapterError("market catalog quotes are crossed or locked")
    except AdapterError:
        # Catalog BBO is optional and advisory. It may only save a later book
        # read; it never authorizes entry. Transient locked/crossed/malformed
        # quotes therefore become unavailable metadata while the authoritative
        # full book remains responsible for the trading decision.
        best_bid = None
        best_offer = None

    def optional_nonnegative(*fields: str) -> float | None:
        for field in fields:
            raw = row.get(field)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                value = _amount(raw, field=f"market.{field}")
            except AdapterError:
                continue
            return value if value >= 0 else None
        return None

    return MarketMetadata(
        slug=slug,
        game_start=clock,
        tick_size=tick,
        active=bool(row.get("active", False)),
        closed=bool(row.get("closed", False)),
        status=str(row.get("status")) if row.get("status") is not None else None,
        sports_market_type=(str(row.get("sportsMarketType"))
                            if row.get("sportsMarketType") is not None else None),
        question=str(row.get("question")) if row.get("question") is not None else None,
        minimum_trade_qty=minimum,
        sports_market_type_v2=(
            str(row.get("sportsMarketTypeV2")).strip()
            if row.get("sportsMarketTypeV2") is not None else None),
        catalog_best_bid=best_bid,
        catalog_best_offer=best_offer,
        catalog_volume_usd=optional_nonnegative("volumeNum", "volume"),
        catalog_volume_24h_usd=optional_nonnegative("volume24hr"),
        catalog_liquidity_usd=optional_nonnegative("liquidityNum", "liquidity"),
    )


def _book_level(row: Any, *, side: str, index: int) -> BookLevel:
    if not isinstance(row, dict):
        raise AdapterError(f"{side}[{index}] is not an object")
    price = _amount(row.get("px"), field=f"{side}[{index}].px")
    size = _amount(row.get("qty"), field=f"{side}[{index}].qty")
    if not 0 < price < 1 or size <= 0:
        raise AdapterError(f"{side}[{index}] has an invalid price or size")
    return BookLevel(price=price, size=size)


def _wire_value(row: dict[str, Any], camel: str, snake: str) -> Any:
    """Read one documented camelCase/snake_case wire-field variant."""

    return row[camel] if camel in row else row.get(snake)


def parse_book_response(response: Any, *, expected_slug: str | None = None) -> BookSnapshot:
    """Normalize the REST or WebSocket full-book wrapper."""

    if not isinstance(response, dict):
        raise AdapterError("book response is not an object")
    raw_data = _wire_value(response, "marketData", "market_data")
    if not isinstance(raw_data, dict):
        raise AdapterError("book response has no marketData wrapper")
    data = raw_data
    actual_slug = str(_wire_value(data, "marketSlug", "market_slug") or "").strip()
    if not actual_slug:
        raise AdapterError("book market slug is missing")
    if expected_slug is not None and actual_slug != expected_slug:
        raise AdapterError(
            f"book slug mismatch: expected {expected_slug}, got {actual_slug}")
    if not isinstance(data.get("bids"), list) or not isinstance(data.get("offers"), list):
        raise AdapterError("book sides are not lists")
    bids = tuple(_book_level(row, side="bids", index=index)
                 for index, row in enumerate(data["bids"]))
    offers = tuple(_book_level(row, side="offers", index=index)
                   for index, row in enumerate(data["offers"]))
    # One side is enough to return a book. Requiring both is a QUOTING rule --
    # you need two touches to keep two cents in the middle -- and it was
    # gating everything, including exits. A position in a market whose book
    # went one-sided could never be exited at all: the parse failed, the book
    # went stale, the reconciler returned book_stale before planning, and the
    # passive reducer, the hard exit AND the pre-kickoff flatten all live
    # behind that gate. Measured 2026-08-15 on chevic-scovla -- 254 log lines,
    # one order action, position rode to settlement. About one market in four
    # publishes a one-sided book.
    #
    # Selling a long only needs the bid side; buying back a short only needs
    # the offer side. Callers that need both check `has_side` themselves.
    if not bids and not offers:
        raise EmptyBookError("book is empty on both sides")
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}

    def optional_stat(field: str) -> float | None:
        raw = stats.get(field)
        if raw is None or str(raw).strip() == "":
            return None
        try:
            value = _amount(raw, field=f"book.stats.{field}")
        except AdapterError:
            return None
        return value if value >= 0 else None

    return BookSnapshot(
        slug=actual_slug,
        bids=bids,
        offers=offers,
        state=str(data.get("state")) if data.get("state") is not None else None,
        transact_time=_timestamp(
            _wire_value(data, "transactTime", "transact_time"),
            field="book.transactTime"),
        shares_traded=optional_stat("sharesTraded"),
        open_interest=optional_stat("openInterest"),
        notional_traded_usd=optional_stat("notionalTraded"),
    )


def parse_trade_response(response: Any, *, expected_slug: str | None = None) -> TradePrint:
    """Normalize the public trade WebSocket wrapper.

    A trade is an observed print, not proof that a nonexistent shadow order
    filled. Phase 2 consumes it only as a conservative fill-model signal.
    """

    if not isinstance(response, dict) or not isinstance(response.get("trade"), dict):
        raise AdapterError("trade response has no trade wrapper")
    data = response["trade"]
    slug = str(_wire_value(data, "marketSlug", "market_slug") or "").strip()
    if not slug:
        raise AdapterError("trade market slug is missing")
    if expected_slug is not None and slug != expected_slug:
        raise AdapterError(f"trade slug mismatch: expected {expected_slug}, got {slug}")
    price = _amount(data.get("price"), field="trade.price")
    quantity = _amount(data.get("quantity"), field="trade.quantity")
    maker = data.get("maker")
    taker = data.get("taker")
    if not isinstance(maker, dict) or not isinstance(taker, dict):
        raise AdapterError("trade sides are missing")
    maker_side = str(maker.get("side") or "").strip()
    if maker_side not in {"ORDER_SIDE_BUY", "ORDER_SIDE_SELL"}:
        raise AdapterError("trade maker side is invalid")
    if not 0 < price < 1 or quantity <= 0:
        raise AdapterError("trade price or quantity is invalid")
    return TradePrint(
        slug=slug,
        price=price,
        quantity=quantity,
        maker_side=maker_side,
        trade_time=_timestamp(
            _wire_value(data, "tradeTime", "trade_time"),
            field="trade.tradeTime", required=True),
        request_id=(str(_wire_value(response, "requestId", "request_id"))
                    if _wire_value(response, "requestId", "request_id")
                    is not None else None),
        maker_intent=(str(maker.get("intent"))
                      if maker.get("intent") is not None else None),
        taker_side=(str(taker.get("side")) if taker.get("side") is not None else None),
        taker_intent=(str(taker.get("intent"))
                      if taker.get("intent") is not None else None),
    )


@dataclass(frozen=True)
class LimiterTelemetry:
    """Bounded, secret-free request-attempt evidence from one limiter.

    ``max_waiting`` includes deliberately concurrent read admission, including
    the caller currently claiming a slot. It is not a sustained-backlog or
    deadline-miss measurement. Interpret it with configured concurrency,
    waiting_now, actual wait times and completed account/exit proof.
    """

    elapsed_seconds: float
    attempts: int
    read_attempts: int
    mutation_attempts: int
    successful_responses: int
    failed_responses: int
    rate_limited_responses: int
    attempts_by_category: tuple[tuple[str, int], ...]
    failed_responses_by_category_status: tuple[tuple[str, int], ...]
    attempted_requests_per_second: float
    wait_p50_seconds: float
    wait_p95_seconds: float
    wait_p99_seconds: float
    max_wait_seconds: float
    response_p50_seconds: float
    response_p95_seconds: float
    response_p99_seconds: float
    max_response_seconds: float
    waiting_now: int
    max_waiting: int
    cooldown_remaining_seconds: float
    safety_cooldown_remaining_seconds: float
    entry_cooldown_remaining_seconds: float
    background_cooldown_remaining_seconds: float
    report_cooldown_remaining_seconds: float
    shared_across_processes: bool
    account_read_cooldowns: tuple[tuple[str, float], ...] = ()


def _percentile(values: tuple[float, ...], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(
        0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _request_category(operation: str, request_kind: str) -> str:
    """Collapse variable IDs/slugs so telemetry cannot leak or grow forever."""

    clean = str(operation or "").strip()
    if request_kind == "mutation":
        if clean == "POST /v1/orders":
            return "mutation_create"
        if clean.startswith("POST /v1/order/") and clean.endswith("/cancel"):
            return "mutation_cancel"
        return "mutation_other"
    if clean.startswith("markets.book("):
        return "market_book"
    if clean.startswith("markets.retrieve_by_slug("):
        return "market_contract"
    if clean.startswith("markets.settlement("):
        return "market_settlement"
    if clean == "markets.list(slug-filtered)":
        return "market_contract_batch"
    if clean == "events.list(lifecycle-batch)":
        return "event_contract_batch"
    if clean.startswith("markets.list(") or clean.startswith("events.list("):
        return "market_discovery"
    if clean == "GET /v1/incentives (symbol-filtered)":
        return "incentive_contract_batch"
    if clean == "GET /v1/incentives/earnings":
        return "earnings"
    if clean.startswith("GET /v1/incentives"):
        return "incentive_discovery"
    if clean == "GET /v1/orders/open":
        return "open_orders"
    if clean == "GET /v1/portfolio/positions":
        return "positions"
    if clean == "GET /v1/portfolio/activities":
        return "execution_history"
    if clean == "GET /v1/portfolio/activities (cash only)":
        return "cash_history"
    if clean == "GET /v1/account/balances":
        return "balance"
    if clean.startswith("GET /v1/order/"):
        return "order_retrieve"
    return "read_other"


class AsyncIntervalLimiter:
    """A paced request gate with optional host-wide coordination.

    The original gate serialized only one Python process.  A dashboard or
    report could therefore add another independent four requests/second on the
    same API key and public IP.  ``shared_state_path`` turns the final claim
    into a small POSIX-file-locked lease, so every cooperating WSL process
    shares one next-request clock. Confirmed-429 cooldowns remain scoped to the
    traffic class that received the response, so discovery pressure cannot
    suspend a cancel or time-critical exit.

    Background catalog discovery deliberately uses half the configured peak
    rate. Maker entry has an independent configured-rate lane, while safety
    reads, cancels, passive reducers and IOC exits use the safety lane.
    Read-only reports use a fourth, opportunistic lane: it waits for an unused
    aggregate interval and is capped at one request every four seconds. All
    classes still consume the same global clock, so their aggregate cannot
    exceed the configured host rate.
    """

    RATE_LIMIT_STREAK_RESET_SECONDS = 60.0
    RATE_LIMIT_BASE_COOLDOWN_SECONDS = 2.0
    RATE_LIMIT_MAX_ADAPTIVE_COOLDOWN_SECONDS = 30.0
    RATE_LIMIT_MAX_COOLDOWN_SECONDS = 60.0
    RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS = 1.0
    BACKGROUND_MINIMUM_INTERVAL_SECONDS = 0.5
    REPORT_MINIMUM_INTERVAL_SECONDS = (
        1.0 / OPPORTUNISTIC_REPORT_RATE_PER_SECOND)
    REPORT_IDLE_INTERVAL_SECONDS = 0.30
    SHARED_CLOCK_SKEW_TOLERANCE_SECONDS = 1.0
    BOOK_PROBE_LEASE_SECONDS = 20.0  # Longer than the owned 15s HTTP timeout.
    # Public full-book WebSocket collection handles normal selection. Missing
    # books and routine flat-owner REST reads share one conservative allowance;
    # discovery's non-book requests retain their independent 2/s lane. This
    # is local load control, not a claim about the venue's unpublished limit.
    OPTIONAL_BOOK_INTERVAL_SECONDS = 4.0

    def __init__(
        self,
        minimum_interval_seconds: float = 0.0,
        *,
        shared_state_path: str | os.PathLike[str] | None = None,
        shared_global_interval_seconds: float | None = None,
        optional_book_interval_seconds: float = 0.0,
    ):
        if minimum_interval_seconds < 0:
            raise ValueError("minimum interval cannot be negative")
        if not math.isfinite(optional_book_interval_seconds) or not 0 <= optional_book_interval_seconds <= 60:
            raise ValueError("optional book interval must be finite and in [0, 60]")
        self.optional_book_interval_seconds = float(optional_book_interval_seconds)
        if (shared_global_interval_seconds is not None
                and shared_global_interval_seconds < 0):
            raise ValueError("shared global interval cannot be negative")
        self.minimum_interval_seconds = float(minimum_interval_seconds)
        self.shared_global_interval_seconds = float(
            minimum_interval_seconds
            if shared_global_interval_seconds is None
            else shared_global_interval_seconds)
        self.shared_state_path = (
            Path(shared_state_path) if shared_state_path is not None else None)
        if self.shared_state_path is not None and fcntl is None:
            raise RuntimeError(
                "shared_state_path requires POSIX flock; authenticated executable "
                "clients must run inside WSL")
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self._next_entry_at = 0.0
        self._next_background_at = 0.0
        self._next_report_at = 0.0
        self._telemetry_started_at = time.monotonic()
        self._attempts = 0
        self._read_attempts = 0
        self._mutation_attempts = 0
        self._successful_responses = 0
        self._failed_responses = 0
        self._rate_limited_responses = 0
        self._attempts_by_category: Counter[str] = Counter()
        self._failed_responses_by_category_status: Counter[str] = Counter()
        self._wait_samples: deque[float] = deque(maxlen=4096)
        self._response_samples: deque[float] = deque(maxlen=4096)
        self._waiting = 0
        self._max_waiting = 0
        self._cooldown_until_by_class = {
            "safety": 0.0,
            "entry": 0.0,
            "background": 0.0,
            "report": 0.0,
        }
        self._rate_limit_streak_by_class = Counter()
        self._last_rate_limit_at_by_class = {
            "safety": 0.0,
            "entry": 0.0,
            "background": 0.0,
            "report": 0.0,
        }
        self._interrupt_generation = 0
        self._interrupt_event = asyncio.Event()
        self._book_backoff: dict[str, Any] = {}
        self._account_read_backoff: dict[str, Any] = {}
        # Fairness for serial, nonwaiting flat-owner callers only. Expiring
        # demand is not a reservation of a host slot or a safety request.
        self._optional_book_waiters: dict[str, float] = {}

    @classmethod
    def from_rate(
        cls,
        requests_per_second: float,
        *,
        shared_state_path: str | os.PathLike[str] | None = None,
        shared_global_interval_seconds: float | None = None,
        optional_book_interval_seconds: float = 0.0,
    ) -> "AsyncIntervalLimiter":
        rate = float(requests_per_second)
        # The current documented retail ceiling is 20/s. Reject a higher
        # configured rate here instead of relying on the venue to police us.
        if not 0 < rate <= 20:
            raise ValueError("requests_per_second must be in (0, 20]")
        return cls(
            1.0 / rate,
            shared_state_path=shared_state_path,
            shared_global_interval_seconds=shared_global_interval_seconds,
            optional_book_interval_seconds=optional_book_interval_seconds,
        )

    @staticmethod
    def _traffic_class(operation: str, request_kind: str) -> str:
        category = _request_category(operation, request_kind)
        if category in {
            "market_discovery", "incentive_discovery", "earnings",
        }:
            return "background"
        if category == "mutation_create":
            return "entry"
        return "safety"

    def _request_interval(
        self, operation: str, request_kind: str, traffic_class: str | None,
    ) -> float:
        chosen = traffic_class or self._traffic_class(operation, request_kind)
        if chosen not in {"background", "entry", "report", "safety"}:
            raise ValueError(
                "traffic_class must be background, entry, report, or safety")
        if chosen == "background":
            return max(
                self.minimum_interval_seconds,
                self.BACKGROUND_MINIMUM_INTERVAL_SECONDS,
            )
        if chosen == "report":
            return max(
                self.minimum_interval_seconds,
                self.REPORT_MINIMUM_INTERVAL_SECONDS,
            )
        return self.minimum_interval_seconds

    def traffic_class_for(
        self, operation: str, request_kind: str,
        traffic_class: str | None = None,
    ) -> str:
        """Return the validated class used by both admission and its result."""

        chosen = traffic_class or self._traffic_class(operation, request_kind)
        if chosen not in {"background", "entry", "report", "safety"}:
            raise ValueError(
                "traffic_class must be background, entry, report, or safety")
        return chosen

    @staticmethod
    def _read_shared_state(handle: Any) -> dict[str, Any]:
        handle.seek(0)
        try:
            raw = json.load(handle)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _write_shared_state(
        handle: Any, state: Mapping[str, Any],
    ) -> None:
        handle.seek(0)
        json.dump(dict(state), handle, separators=(",", ":"), sort_keys=True)
        handle.truncate()
        handle.flush()

    @staticmethod
    def _open_shared_state(path: Path) -> Any:
        """Open a private, seekable state file without append semantics."""

        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, "r+", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            raise

    @classmethod
    def _shared_timestamp(
        cls,
        value: Any,
        now: float,
        *,
        maximum_future_seconds: float | None = None,
    ) -> float:
        """Return a bounded shared deadline or treat it as stale/corrupt."""

        try:
            timestamp = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not math.isfinite(timestamp):
            return 0.0
        # The longest background/entry deadline this class writes is 60 seconds.
        # Safety deadlines are independently bounded to one second. A larger
        # future value can only be stale state after a wall-clock correction
        # or manual/corrupt data; never let it wedge account-safety traffic.
        maximum = (
            cls.RATE_LIMIT_MAX_COOLDOWN_SECONDS
            if maximum_future_seconds is None
            else max(0.0, float(maximum_future_seconds)))
        if timestamp > (
            now + maximum
            + cls.SHARED_CLOCK_SKEW_TOLERANCE_SECONDS
        ):
            return 0.0
        return timestamp

    @classmethod
    def _cooldown_delay(
        cls, retry_after_seconds: float | None, streak: int, *,
        maximum_seconds: float | None = None,
    ) -> float:
        maximum = (
            cls.RATE_LIMIT_MAX_COOLDOWN_SECONDS
            if maximum_seconds is None else max(0.0, float(maximum_seconds)))
        fallback = min(
            cls.RATE_LIMIT_MAX_ADAPTIVE_COOLDOWN_SECONDS,
            cls.RATE_LIMIT_BASE_COOLDOWN_SECONDS
            * (2 ** min(max(0, streak - 1), 4)),
        )
        fallback = min(maximum, fallback)
        if retry_after_seconds is None:
            return fallback
        try:
            requested = float(retry_after_seconds)
        except (TypeError, ValueError, OverflowError):
            return fallback
        if not math.isfinite(requested):
            return fallback
        return min(
            maximum,
            max(fallback, max(0.0, requested)),
        )

    @staticmethod
    def _lane_clock_name(traffic_class: str) -> str | None:
        if traffic_class == "entry":
            return "next_entry_at"
        if traffic_class == "background":
            return "next_background_at"
        if traffic_class == "report":
            return "next_report_at"
        return None

    @staticmethod
    def _lane_cooldown_name(traffic_class: str) -> str:
        return f"cooldown_until_{traffic_class}"

    def _book_admission(
        self, state: dict[str, Any], now: float, token: str, *, claim: bool,
    ) -> None:
        """Endpoint backoff, independent of account/mutation traffic lanes.

        A post-429 probe is leased atomically across all slugs and cooperating
        processes. Crashed/cancelled probes expire; only their own successful
        response can reopen the endpoint. An older in-flight success cannot
        erase a newer 429 or another probe's lease.
        """
        cooldown = self._shared_timestamp(state.get("book_cooldown_until"), now)
        probe = self._shared_timestamp(
            state.get("book_probe_until"), now,
            maximum_future_seconds=self.BOOK_PROBE_LEASE_SECONDS)
        delay = max(cooldown, probe) - now
        if delay > 0:
            raise ApiAdmissionDeferred(
                "market-book backoff active; request not sent",
                operation="markets.book", error_code="LOCAL_BOOK_COOLDOWN",
                retry_after_seconds=delay)
        if claim and state.get("book_rate_limited") is True:
            state["book_probe_token"] = token
            state["book_probe_until"] = now + self.BOOK_PROBE_LEASE_SECONDS

    def _book_result(
        self, state: dict[str, Any], now: float, *, token: str | None,
        status_code: int | None, failed: bool, retry_after_seconds: float | None,
    ) -> None:
        if status_code == 429:
            last = self._shared_timestamp(state.get("book_last_429_at"), now)
            try:
                streak = min(5, max(0, int(state.get("book_429_streak", 0))))
            except (TypeError, ValueError, OverflowError):
                streak = 0
            if last <= 0 or now - last > self.RATE_LIMIT_STREAK_RESET_SECONDS:
                streak = 0
            streak += 1
            other_probe_until = (
                self._shared_timestamp(
                    state.get("book_probe_until"), now,
                    maximum_future_seconds=self.BOOK_PROBE_LEASE_SECONDS)
                if state.get("book_probe_token") != token else 0.0)
            state.update(book_rate_limited=True, book_429_streak=streak,
                         book_last_429_at=now, book_probe_token="",
                         book_probe_until=other_probe_until)
            state["book_cooldown_until"] = max(
                self._shared_timestamp(state.get("book_cooldown_until"), now),
                now + self._cooldown_delay(retry_after_seconds, streak))
        elif token is not None and token == state.get("book_probe_token"):
            state["book_probe_token"] = ""
            state["book_probe_until"] = 0.0
            if failed:
                # A transport/5xx failure is not recovery from the earlier 429.
                state["book_cooldown_until"] = now + self._cooldown_delay(None, 1)
            else:
                # One cached success is not sustained recovery. Keep the last
                # rejection/streak until the existing quiet-time reset so
                # alternating success/429 cannot reset every delay to base.
                state.update(book_rate_limited=False, book_cooldown_until=0.0)

    def _optional_book_due(self, state: Mapping[str, Any], now: float,
                           traffic_class: str) -> float:
        if traffic_class not in {"entry", "background"} or self.optional_book_interval_seconds == 0:
            return 0.0
        return self._shared_timestamp(
            state.get("next_optional_book_at"), now,
            maximum_future_seconds=self.optional_book_interval_seconds)

    def _record_book_claim(self, state: dict[str, Any], now: float,
                           traffic_class: str) -> None:
        if self.optional_book_interval_seconds and traffic_class in {"entry", "background"}:
            # Reserve only optional book allowance, never a future global or
            # safety slot. Safety reads must not repeatedly move this clock:
            # active positions could otherwise starve refill indefinitely.
            state["next_optional_book_at"] = now + self.optional_book_interval_seconds

    def _record_shared_book_result(self, **result: Any) -> None:
        path = self.shared_state_path
        assert path is not None and fcntl is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_shared_state(path) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self._read_shared_state(handle)
                before = dict(state)
                self._book_result(state, time.time(), **result)
                if state != before:
                    self._write_shared_state(handle, state)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _claim_shared_slot(
        self, interval: float, traffic_class: str, book_token: str | None = None,
        *, defer_optional_book: bool = False, account_endpoint=None, read_token=None,
    ) -> tuple[float, float]:
        """Try one host claim; return total delay and its cooldown portion."""

        path = self.shared_state_path
        if path is None or fcntl is None:
            return 0.0, 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_shared_state(path) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self._read_shared_state(handle)
                now = time.time()
                if book_token is not None:
                    self._book_admission(state, now, book_token, claim=False)
                if account_endpoint is not None:
                    read_backoff.admit(state, account_endpoint, now, read_token, claim=False)
                aggregate_due = self._shared_timestamp(
                    state.get("next_request_at", 0.0), now,
                    maximum_future_seconds=(
                        self.shared_global_interval_seconds))
                cooldown_due = self._shared_timestamp(
                    state.get(self._lane_cooldown_name(traffic_class), 0.0),
                    now,
                    maximum_future_seconds=(
                        self.RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS
                        if traffic_class == "safety" else
                        self.RATE_LIMIT_MAX_COOLDOWN_SECONDS))
                due = max(now, aggregate_due, cooldown_due)
                if book_token is not None:
                    optional_due = self._optional_book_due(state, now, traffic_class)
                    if defer_optional_book and optional_due > now:
                        raise ApiAdmissionDeferred(
                            "optional book pacing; request not sent",
                            error_code="LOCAL_OPTIONAL_BOOK_PACING",
                            retry_after_seconds=optional_due - now)
                    due = max(due, optional_due)
                if traffic_class == "report" and aggregate_due > 0.0:
                    # ``aggregate_due`` is written by old and new runners.
                    # Waiting beyond it proves one aggregate slot stayed
                    # unused without requiring a new shared-state field or a
                    # simultaneous runner upgrade.
                    due = max(
                        due,
                        aggregate_due + max(
                            0.0,
                            self.REPORT_IDLE_INTERVAL_SECONDS
                            - self.shared_global_interval_seconds,
                        ),
                    )
                lane_clock = self._lane_clock_name(traffic_class)
                if lane_clock is not None:
                    due = max(
                        due,
                        self._shared_timestamp(
                            state.get(lane_clock, 0.0), now,
                            maximum_future_seconds=interval),
                    )
                delay = max(0.0, due - now)
                if delay > 0.0:
                    return delay, max(0.0, cooldown_due - now)
                if book_token is not None:
                    self._book_admission(state, now, book_token, claim=True)
                    self._record_book_claim(state, now, traffic_class)
                if account_endpoint is not None:
                    read_backoff.admit(state, account_endpoint, now, read_token, claim=True)
                state["next_request_at"] = (
                    now + self.shared_global_interval_seconds)
                if lane_clock is not None:
                    state[lane_clock] = now + interval
                self._write_shared_state(handle, state)
                return 0.0, 0.0
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _record_shared_account_read_result(self, endpoint, **result):
        path = self.shared_state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_shared_state(path) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self._read_shared_state(handle)
                if read_backoff.record(state, endpoint, time.time(), **result):
                    self._write_shared_state(handle, state)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _register_shared_rate_limit(
        self, retry_after_seconds: float | None, traffic_class: str,
    ) -> None:
        path = self.shared_state_path
        if path is None or fcntl is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_shared_state(path) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self._read_shared_state(handle)
                now = time.time()
                last_name = f"last_rate_limit_at_{traffic_class}"
                streak_name = f"rate_limit_streak_{traffic_class}"
                last = self._shared_timestamp(
                    state.get(last_name, 0.0), now)
                try:
                    streak = min(
                        5, max(0, int(state.get(streak_name, 0))))
                except (TypeError, ValueError, OverflowError):
                    streak = 0
                if (
                    last <= 0.0
                    or now - last > self.RATE_LIMIT_STREAK_RESET_SECONDS
                ):
                    streak = 0
                streak += 1
                maximum = (
                    self.RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS
                    if traffic_class == "safety"
                    else self.RATE_LIMIT_MAX_COOLDOWN_SECONDS)
                delay = self._cooldown_delay(
                    retry_after_seconds, streak, maximum_seconds=maximum)
                state[last_name] = now
                state[streak_name] = streak
                cooldown_name = self._lane_cooldown_name(traffic_class)
                state[cooldown_name] = max(
                    self._shared_timestamp(
                        state.get(cooldown_name, 0.0), now,
                        maximum_future_seconds=maximum),
                    now + delay,
                )
                self._write_shared_state(handle, state)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_shared_cooldowns(self) -> dict[str, float]:
        """Return bounded host cooldowns without mutating shared state.

        This performs file IO and locking, so callers must run it in a worker.
        A missing state file simply means no peer has published a cooldown yet.
        """

        path = self.shared_state_path
        if path is None or fcntl is None or not path.is_file():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                # Telemetry is evidence-only and must never queue trading
                # behind a wedged diagnostic lock. The next 60-second window
                # can report the peer deadline if this instant is contended.
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                try:
                    state = self._read_shared_state(handle)
                    now = time.time()
                    result = {
                        traffic_class: max(
                            0.0,
                            self._shared_timestamp(
                                state.get(
                                    self._lane_cooldown_name(traffic_class),
                                    0.0,
                                ),
                                now,
                                maximum_future_seconds=(
                                    self.RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS
                                    if traffic_class == "safety" else
                                    self.RATE_LIMIT_MAX_COOLDOWN_SECONDS
                                ),
                            ) - now,
                        )
                        for traffic_class in (
                            "background", "entry", "report", "safety")
                    }
                    result.update({"read:" + endpoint: read_backoff.remaining(
                        state, endpoint, now) for endpoint in read_backoff.ENDPOINTS})
                    return result
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            # Another process may rotate/remove an unused state file between
            # the existence check and open. Admission will recreate it safely.
            return {}

    async def wait(
        self, *, operation: str = "", request_kind: str = "read",
        traffic_class: str | None = None,
        checkpoint: Callable[[], Awaitable[Any]] | None = None,
        defer_optional_book: bool = False,
    ) -> str | None:
        if request_kind not in {"read", "mutation"}:
            raise ValueError("request_kind must be read or mutation")
        queued_at = time.monotonic()
        self._waiting += 1
        self._max_waiting = max(self._max_waiting, self._waiting)
        chosen_class = self.traffic_class_for(
            operation, request_kind, traffic_class)
        interval = self._request_interval(
            operation, request_kind, chosen_class)
        interrupt_generation = self._interrupt_generation
        interrupt_event = self._interrupt_event
        book_token = (uuid4().hex if request_kind == "read"
                      and _request_category(operation, request_kind) == "market_book"
                      else None)
        account_endpoint = (read_backoff.endpoint_for(_request_category(operation, request_kind))
                            if request_kind == "read" else None)
        read_token = uuid4().hex if account_endpoint is not None else None
        try:
            while True:
                if self._interrupt_generation != interrupt_generation:
                    raise asyncio.CancelledError
                if checkpoint is not None:
                    # No lock/lease is held. A scan can yield to a changed
                    # position or urgent deadline while it is being paced.
                    await checkpoint()
                if self._interrupt_generation != interrupt_generation:
                    raise asyncio.CancelledError
                nonwaiting_book = (defer_optional_book and book_token is not None
                                   and chosen_class != "safety"
                                   and self.optional_book_interval_seconds > 0)
                if nonwaiting_book:
                    now = time.monotonic()
                    self._optional_book_waiters = {
                        key: deadline for key, deadline in self._optional_book_waiters.items()
                        if deadline > now}
                    if operation in self._optional_book_waiters or len(self._optional_book_waiters) < 128:
                        self._optional_book_waiters[operation] = now + max(
                            5.0, self.optional_book_interval_seconds + 1.0)
                    if next(iter(self._optional_book_waiters), None) != operation:
                        raise ApiAdmissionDeferred(
                            "another flat book is due first; request not sent",
                            error_code="LOCAL_OPTIONAL_BOOK_PACING",
                            retry_after_seconds=1)
                if self.shared_state_path is not None:
                    # The POSIX transaction is already the atomic claim across
                    # both processes and coroutines. Do not hold the local
                    # asyncio lock while a worker waits for that file lock.
                    delay, cooldown_delay = await asyncio.to_thread(
                        self._claim_shared_slot, interval, chosen_class, book_token,
                        **({"account_endpoint": account_endpoint, "read_token": read_token}
                           if account_endpoint is not None else {}),
                        **({"defer_optional_book": True} if nonwaiting_book else {}))
                else:
                    async with self._lock:
                        now = time.monotonic()
                        if book_token is not None:
                            self._book_admission(
                                self._book_backoff, now, book_token, claim=False)
                        if account_endpoint is not None:
                            read_backoff.admit(self._account_read_backoff, account_endpoint,
                                               now, read_token, claim=False)
                        lane_due = 0.0
                        if chosen_class == "entry":
                            lane_due = self._next_entry_at
                        elif chosen_class == "background":
                            lane_due = self._next_background_at
                        elif chosen_class == "report":
                            lane_due = self._next_report_at
                        cooldown_due = self._cooldown_until_by_class[chosen_class]
                        due = max(
                            now,
                            self._next_at,
                            lane_due,
                            cooldown_due,
                        )
                        if book_token is not None:
                            optional_due = self._optional_book_due(
                                self._book_backoff, now, chosen_class)
                            if nonwaiting_book and optional_due > now:
                                raise ApiAdmissionDeferred(
                                    "optional book pacing; request not sent",
                                    error_code="LOCAL_OPTIONAL_BOOK_PACING",
                                    retry_after_seconds=optional_due - now)
                            due = max(due, optional_due)
                        if chosen_class == "report" and self._next_at > 0.0:
                            due = max(
                                due,
                                self._next_at + max(
                                    0.0,
                                    self.REPORT_IDLE_INTERVAL_SECONDS
                                    - self.shared_global_interval_seconds,
                                ),
                            )
                        delay = max(0.0, due - now)
                        cooldown_delay = max(0.0, cooldown_due - now)
                        if delay == 0.0:
                            if account_endpoint is not None:
                                read_backoff.admit(self._account_read_backoff, account_endpoint,
                                                   now, read_token, claim=True)
                            if book_token is not None:
                                self._book_admission(
                                    self._book_backoff, now, book_token, claim=True)
                                self._record_book_claim(self._book_backoff, now, chosen_class)
                            self._next_at = now + self.shared_global_interval_seconds
                            if chosen_class == "entry":
                                self._next_entry_at = now + interval
                            elif chosen_class == "background":
                                self._next_background_at = now + interval
                            elif chosen_class == "report":
                                self._next_report_at = now + interval
                if self._interrupt_generation != interrupt_generation:
                    raise asyncio.CancelledError
                if delay <= 0.0:
                    if nonwaiting_book:
                        self._optional_book_waiters.pop(operation, None)
                    break
                # This runner is intentionally serial.  Sleeping here for a
                # background/entry 429 cooldown would prevent it from reaching
                # an unrelated cancel or exit even though safety has its own
                # healthy lane. Fail before the network call so the caller can
                # defer this
                # optional work without inventing an uncertain mutation. Use
                # the explicit cooldown component rather than total delay:
                # a deliberately configured sub-1-RPS lane may have ordinary
                # pacing over one second and must still make progress.
                if chosen_class != "safety" and cooldown_delay > 1.0:
                    raise ApiAdmissionDeferred(
                        f"{chosen_class} API cooldown active; request not sent",
                        operation=operation,
                        error_code="LOCAL_API_COOLDOWN",
                        retry_after_seconds=cooldown_delay,
                    )
                try:
                    await asyncio.wait_for(
                        interrupt_event.wait(), timeout=(
                            min(delay, 0.25) if checkpoint is not None else delay))
                except TimeoutError:
                    continue
                raise asyncio.CancelledError
        finally:
            self._waiting -= 1
        waited = max(0.0, time.monotonic() - queued_at)
        self._attempts += 1
        if request_kind == "mutation":
            self._mutation_attempts += 1
        else:
            self._read_attempts += 1
        self._attempts_by_category[
            _request_category(operation, request_kind)
        ] += 1
        self._wait_samples.append(waited)
        return book_token or read_token

    async def ensure_account_reads_available(self, *endpoints: str) -> None:
        """Reject a doomed compound read before spending unrelated API slots.

        Advisory only: no lease, pacing slot or freshness is granted here.
        Every actual request still performs normal atomic admission. In
        particular, another caller can claim a probe after this check.
        """
        endpoints = tuple(dict.fromkeys(endpoints))
        if any(endpoint not in read_backoff.ENDPOINTS for endpoint in endpoints):
            raise ValueError("unknown account-read endpoint")
        if self.shared_state_path is not None:
            await asyncio.to_thread(self._check_shared_account_reads, endpoints)
        else:
            async with self._lock:
                now = time.monotonic()
                for endpoint in endpoints:
                    read_backoff.admit(self._account_read_backoff, endpoint,
                                       now, None, claim=False)

    def _check_shared_account_reads(self, endpoints: tuple[str, ...]) -> None:
        path = self.shared_state_path
        assert path is not None and fcntl is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_shared_state(path) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                state = self._read_shared_state(handle)
                now = time.time()
                for endpoint in endpoints:
                    read_backoff.admit(state, endpoint, now, None, claim=False)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def interrupt_waits(self) -> None:
        """Wake waits that began before an operator stop boundary.

        Future shutdown safety calls capture a fresh event and remain usable.
        """

        waiting = self._interrupt_event
        self._interrupt_generation += 1
        self._interrupt_event = asyncio.Event()
        waiting.set()

    async def sleep_interruptibly(
        self, seconds: float,
        *,
        sleeper: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        """Wait for read retry backoff while preserving the stop boundary."""

        delay = max(0.0, float(seconds))
        if delay == 0.0:
            return
        generation = self._interrupt_generation
        interrupt = asyncio.create_task(self._interrupt_event.wait())
        sleeping = asyncio.ensure_future(sleeper(delay))
        try:
            done, _ = await asyncio.wait(
                (interrupt, sleeping), return_when=asyncio.FIRST_COMPLETED)
            if (interrupt in done
                    or self._interrupt_generation != generation):
                raise asyncio.CancelledError
            await sleeping
        finally:
            for task in (interrupt, sleeping):
                if not task.done():
                    task.cancel()
            await asyncio.gather(interrupt, sleeping, return_exceptions=True)

    async def record_result(
        self, *, duration_seconds: float, status_code: int | None = None,
        failed: bool = False, retry_after_seconds: float | None = None,
        traffic_class: str = "safety",
        operation: str = "", request_kind: str = "read",
        admission_token: str | None = None,
        transport_failure: bool = False,
        server_ids: Mapping[str, str] | None = None,
    ) -> None:
        """Record the outcome of one already-counted attempt."""

        chosen_class = self.traffic_class_for("", "read", traffic_class)
        duration = max(0.0, float(duration_seconds))
        if status_code == 429:
            log_rate_limit(category=_request_category(operation, request_kind),
                           request_kind=request_kind, traffic_class=chosen_class,
                           duration_seconds=duration,
                           retry_after_seconds=retry_after_seconds,
                           server_ids=server_ids)
        self._response_samples.append(duration)
        if failed:
            self._failed_responses += 1
            category = _request_category(operation, request_kind)
            status = str(status_code) if status_code is not None else "unknown"
            self._failed_responses_by_category_status[
                f"{category}@{status}"] += 1
        else:
            self._successful_responses += 1
        account_endpoint = (read_backoff.endpoint_for(_request_category(operation, request_kind))
                            if request_kind == "read" else None)
        if account_endpoint is not None:
            result = dict(token=admission_token, status_code=status_code,
                          failed=failed, retry_after_seconds=retry_after_seconds,
                          transport_failure=transport_failure)
            if self.shared_state_path is not None:
                await asyncio.to_thread(self._record_shared_account_read_result,
                                        account_endpoint, **result)
            else:
                async with self._lock:
                    read_backoff.record(self._account_read_backoff, account_endpoint,
                                        time.monotonic(), **result)
            if status_code == 429:
                self._rate_limited_responses += 1
            # A read rejection cannot impose cooldown on cancel/reducer sends.
            return
        if (request_kind == "read"
                and _request_category(operation, request_kind) == "market_book"):
            result = dict(token=admission_token, status_code=status_code,
                          failed=failed, retry_after_seconds=retry_after_seconds)
            if self.shared_state_path is not None:
                await asyncio.to_thread(self._record_shared_book_result, **result)
            else:
                async with self._lock:
                    self._book_result(self._book_backoff, time.monotonic(), **result)
            if status_code == 429:
                self._rate_limited_responses += 1
            # Book throttling must not cool the account/cancel/reducer lane.
            return
        if status_code == 429:
            self._rate_limited_responses += 1
            now = time.monotonic()
            if (now - self._last_rate_limit_at_by_class[chosen_class]
                    > self.RATE_LIMIT_STREAK_RESET_SECONDS):
                self._rate_limit_streak_by_class[chosen_class] = 0
            self._rate_limit_streak_by_class[chosen_class] += 1
            self._last_rate_limit_at_by_class[chosen_class] = now
            maximum = (
                self.RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS
                if chosen_class == "safety"
                else self.RATE_LIMIT_MAX_COOLDOWN_SECONDS)
            delay = self._cooldown_delay(
                retry_after_seconds,
                self._rate_limit_streak_by_class[chosen_class],
                maximum_seconds=maximum)
            self._cooldown_until_by_class[chosen_class] = max(
                self._cooldown_until_by_class[chosen_class], now + delay)
            if self.shared_state_path is not None:
                await asyncio.to_thread(
                    self._register_shared_rate_limit,
                    retry_after_seconds,
                    chosen_class,
                )

    def _telemetry(
        self, *, reset: bool,
        shared_cooldowns: Mapping[str, float],
    ) -> LimiterTelemetry:
        """Build one evidence window, merging any worker-read host state."""

        now = time.monotonic()
        elapsed = max(0.0, now - self._telemetry_started_at)
        samples = tuple(self._wait_samples)
        responses = tuple(self._response_samples)
        class_cooldowns = {
            traffic_class: max(
                0.0,
                deadline - now,
                float(shared_cooldowns.get(traffic_class, 0.0)),
            )
            for traffic_class, deadline in self._cooldown_until_by_class.items()
        }
        result = LimiterTelemetry(
            elapsed_seconds=elapsed,
            attempts=self._attempts,
            read_attempts=self._read_attempts,
            mutation_attempts=self._mutation_attempts,
            successful_responses=self._successful_responses,
            failed_responses=self._failed_responses,
            rate_limited_responses=self._rate_limited_responses,
            attempts_by_category=tuple(sorted(self._attempts_by_category.items())),
            failed_responses_by_category_status=tuple(sorted(
                self._failed_responses_by_category_status.items())),
            attempted_requests_per_second=(
                self._attempts / elapsed if elapsed > 0 else 0.0
            ),
            wait_p50_seconds=_percentile(samples, 0.50),
            wait_p95_seconds=_percentile(samples, 0.95),
            wait_p99_seconds=_percentile(samples, 0.99),
            max_wait_seconds=max(samples, default=0.0),
            response_p50_seconds=_percentile(responses, 0.50),
            response_p95_seconds=_percentile(responses, 0.95),
            response_p99_seconds=_percentile(responses, 0.99),
            max_response_seconds=max(responses, default=0.0),
            waiting_now=self._waiting,
            max_waiting=self._max_waiting,
            cooldown_remaining_seconds=max(class_cooldowns.values(), default=0.0),
            safety_cooldown_remaining_seconds=class_cooldowns["safety"],
            entry_cooldown_remaining_seconds=class_cooldowns["entry"],
            background_cooldown_remaining_seconds=class_cooldowns["background"],
            report_cooldown_remaining_seconds=class_cooldowns["report"],
            shared_across_processes=(
                self.shared_state_path is not None and fcntl is not None),
            account_read_cooldowns=tuple((endpoint, max(
                read_backoff.remaining(self._account_read_backoff, endpoint, now),
                float(shared_cooldowns.get("read:" + endpoint, 0))))
                for endpoint in read_backoff.ENDPOINTS),
        )
        if reset:
            self._telemetry_started_at = now
            self._attempts = 0
            self._read_attempts = 0
            self._mutation_attempts = 0
            self._successful_responses = 0
            self._failed_responses = 0
            self._rate_limited_responses = 0
            self._attempts_by_category.clear()
            self._failed_responses_by_category_status.clear()
            self._wait_samples.clear()
            self._response_samples.clear()
            self._max_waiting = self._waiting
        return result

    def telemetry(self, *, reset: bool = False) -> LimiterTelemetry:
        """Return process-local limiter evidence without file or API IO."""

        return self._telemetry(reset=reset, shared_cooldowns={})

    async def shared_telemetry(self, *, reset: bool = False) -> LimiterTelemetry:
        """Return local evidence plus host cooldowns, reading off the loop."""

        shared_cooldowns: Mapping[str, float] = {}
        if self.shared_state_path is not None:
            try:
                shared_cooldowns = await asyncio.to_thread(
                    self._read_shared_cooldowns)
            except OSError:
                # Reporting is operational evidence, never trading authority.
                # Admission still fails normally if its required file access
                # later fails; an evidence read must not stop the runner.
                shared_cooldowns = {}
        return self._telemetry(
            reset=reset, shared_cooldowns=shared_cooldowns)


def shared_api_limiter(
    requests_per_second: float = 4.0,
    *,
    state_path: str | os.PathLike[str] | None = None,
) -> AsyncIntervalLimiter:
    """Build the one host-wide limiter used by production API entrypoints.

    Tests and composed library callers may still inject an isolated limiter.
    Executable tools must come through this factory so adding a new report
    cannot silently add another independent request budget beside the bot.
    """

    if fcntl is None:
        raise RuntimeError(
            "host-wide API coordination requires POSIX flock; run authenticated "
            "executable clients inside WSL")
    if state_path is None:
        configured = os.environ.get("LIQUIDITY_BOT_API_LIMITER_STATE", "").strip()
        state_path = configured or (
            Path(__file__).resolve().parents[2]
            / "data"
            / "api-rate-limiter.state"
        )
    return AsyncIntervalLimiter.from_rate(
        requests_per_second,
        shared_state_path=state_path,
        shared_global_interval_seconds=0.25,
        optional_book_interval_seconds=AsyncIntervalLimiter.OPTIONAL_BOOK_INTERVAL_SECONDS,
    )


class AsyncReadOnlyGateway:
    """Validated access to catalog, market, book, incentive, and earnings data."""

    def __init__(
        self,
        client: Any,
        *,
        limiter: AsyncIntervalLimiter | None = None,
        incentive_batch_size: int = 40,
        catalog_page_size: int = 500,
        max_pages: int = 100,
        event_max_pages: int = 10,
        incentive_max_pages: int = 200,
        retry_attempts: int = 3,
        retry_base_seconds: float = 1.0,
        max_retry_after_seconds: float = 60.0,
        default_traffic_class: str | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        if not 1 <= incentive_batch_size <= 100:
            raise ValueError("incentive_batch_size must be in [1, 100]")
        if not 1 <= catalog_page_size <= 500:
            raise ValueError("catalog_page_size must be in [1, 500]")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if event_max_pages <= 0:
            raise ValueError("event_max_pages must be positive")
        if incentive_max_pages <= 0:
            raise ValueError("incentive_max_pages must be positive")
        if (retry_attempts <= 0 or retry_base_seconds < 0
                or max_retry_after_seconds < 0):
            raise ValueError("retry settings are invalid")
        if default_traffic_class not in {
            None, "background", "entry", "report", "safety",
        }:
            raise ValueError(
                "default_traffic_class must be background, entry, report, "
                "safety, or None")
        self.client = client
        self.limiter = limiter or AsyncIntervalLimiter()
        self.incentive_batch_size = incentive_batch_size
        self.catalog_page_size = catalog_page_size
        self.max_pages = max_pages
        self.event_max_pages = event_max_pages
        self.incentive_max_pages = incentive_max_pages
        self.retry_attempts = retry_attempts
        self.retry_base_seconds = retry_base_seconds
        self.max_retry_after_seconds = max_retry_after_seconds
        self.default_traffic_class = default_traffic_class
        self.last_upcoming_event_pages = 0
        self._traffic_class_override: ContextVar[str | None] = ContextVar(
            f"api_traffic_class_{id(self)}", default=None)
        self._read_checkpoint = ContextVar(f"api_read_checkpoint_{id(self)}", default=None)
        self._defer_optional_book = ContextVar(f"api_defer_book_{id(self)}", default=False)
        self._sleep = sleep

    @contextmanager
    def optional_book_reads(self):
        """Yield flat price work instead of sleeping through the owner sweep."""
        token = self._defer_optional_book.set(True)
        try:
            with self.traffic_class("entry"):
                yield
        finally:
            self._defer_optional_book.reset(token)

    @contextmanager
    def read_checkpoints(self, callback):
        """Yield composed read work to its serial owner between requests.

        The checkpoint runs outside request retry/error accounting. Nested
        safety reads cannot recursively invoke it or inherit the scan lane.
        """
        token = self._read_checkpoint.set(callback)
        try:
            yield
        finally:
            self._read_checkpoint.reset(token)

    async def _checkpoint_before_read(self):
        callback = self._read_checkpoint.get()
        if callback is not None:
            with self.read_checkpoints(None), self.traffic_class("safety"):
                await callback()

    @contextmanager
    def traffic_class(self, value: str):
        """Classify a composed operation without leaking across async tasks."""

        if value not in {"background", "entry", "safety"}:
            raise ValueError("traffic class must be background, entry, or safety")
        token = self._traffic_class_override.set(value)
        try:
            yield
        finally:
            self._traffic_class_override.reset(token)

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        raw = getattr(exc, "status_code", None)
        if raw is None:
            raw = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _response_headers(exc: Exception) -> Mapping[str, Any]:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        return headers if isinstance(headers, Mapping) else {}

    @classmethod
    def _retry_after_seconds(cls, exc: Exception) -> float | None:
        raw = next((value for name, value in cls._response_headers(exc).items()
                    if str(name).lower() == "retry-after"), None)
        if raw in (None, ""):
            return None
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                until = parsedate_to_datetime(str(raw))
                if until.tzinfo is None or until.utcoffset() is None:
                    return None
                seconds = (until - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        return max(0.0, seconds) if math.isfinite(seconds) else None

    @classmethod
    def _error_code(cls, exc: Exception) -> str | None:
        body = getattr(exc, "body", None)
        if not isinstance(body, Mapping):
            return None
        for key in ("code", "errorCode", "error_code"):
            value = body.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @classmethod
    def _trace_id(cls, exc: Exception) -> str | None:
        headers = cls._response_headers(exc)
        for wanted in ("x-trace-id", "trace-id", "x-request-id", "cf-ray"):
            for name, value in headers.items():
                if str(name).lower() == wanted and value not in (None, ""):
                    return str(value)
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            for key in ("traceId", "trace_id", "requestId", "request_id"):
                if body.get(key) not in (None, ""):
                    return str(body[key])
        return None

    @classmethod
    def _adapter_error(cls, operation: str, exc: Exception) -> AdapterError:
        status = cls._status_code(exc)
        code = cls._error_code(exc)
        trace = cls._trace_id(exc)
        retry_after = cls._retry_after_seconds(exc)
        detail = [f"status={status}"] if status is not None else []
        if code:
            detail.append(f"code={code}")
        if trace:
            detail.append(f"trace={trace}")
        suffix = f" ({', '.join(detail)})" if detail else ""
        return AdapterError(
            f"{operation} failed: {type(exc).__name__}: {exc}{suffix}",
            operation=operation,
            status_code=status,
            error_code=code,
            trace_id=trace,
            retry_after_seconds=retry_after,
        )

    async def _await_catalog_read(self, call, checkpoint):
        """Keep the serial owner's checkpoints live during optional network IO.

        Only a read inside an explicit checkpoint scope gets a worker. It has
        no inherited callback/ledger authority and must be joined before the
        caller resumes account proof or execution. Ordinary safety reads and
        every mutation retain their original path.
        """
        with self.read_checkpoints(None):
            task = asyncio.create_task(call())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.25)
                if not task.done():
                    await checkpoint()
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def ensure_account_reads_available(self, *endpoints: str) -> None:
        # Match _call's supported lightweight test/custom limiter protocol.
        if isinstance(self.limiter, AsyncIntervalLimiter):
            await self.limiter.ensure_account_reads_available(*endpoints)

    async def _call(self, operation: str, call: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(self.retry_attempts):
            admission_token = None
            await self._checkpoint_before_read()
            traffic_class = (
                self._traffic_class_override.get()
                or self.default_traffic_class)
            if isinstance(self.limiter, AsyncIntervalLimiter):
                traffic_class = self.limiter.traffic_class_for(
                    operation, "read", traffic_class)
                admission_token = await self.limiter.wait(
                    operation=operation,
                    request_kind="read",
                    traffic_class=traffic_class,
                    **({"checkpoint": self._checkpoint_before_read}
                       if self._read_checkpoint.get() is not None else {}),
                    **({"defer_optional_book": True}
                       if self._defer_optional_book.get() else {}),
                )
            else:
                # Test doubles and externally supplied compatible gates retain
                # the original zero-argument protocol.
                await self.limiter.wait()
            started_at = time.monotonic()
            checkpoint_error = None

            async def checkpoint():
                nonlocal checkpoint_error
                try:
                    await self._checkpoint_before_read()
                except BaseException as exc:
                    checkpoint_error = exc
                    raise

            try:
                result = (await call() if self._read_checkpoint.get() is None
                          else await self._await_catalog_read(call, checkpoint))
                if isinstance(self.limiter, AsyncIntervalLimiter):
                    await self.limiter.record_result(
                        duration_seconds=time.monotonic() - started_at,
                        traffic_class=traffic_class,
                        operation=operation, request_kind="read",
                        admission_token=admission_token)
                return result
            except AdapterError as exc:
                if exc is checkpoint_error:
                    # Local scheduling is not a failed venue request and must
                    # not publish a cooldown or enter an API retry ladder.
                    raise
                if isinstance(self.limiter, AsyncIntervalLimiter):
                    await self.limiter.record_result(
                        duration_seconds=time.monotonic() - started_at,
                        status_code=self._status_code(exc),
                        failed=True,
                        retry_after_seconds=getattr(
                            exc, "retry_after_seconds", None),
                        traffic_class=traffic_class,
                        operation=operation, request_kind="read",
                        admission_token=admission_token,
                        transport_failure=read_backoff.is_transport_failure(exc),
                        server_ids=response_ids(self._response_headers(exc),
                                                getattr(exc, "trace_id", None)),
                    )
                raise
            except Exception as exc:
                if exc is checkpoint_error:
                    raise
                if isinstance(self.limiter, AsyncIntervalLimiter):
                    await self.limiter.record_result(
                        duration_seconds=time.monotonic() - started_at,
                        status_code=self._status_code(exc),
                        failed=True,
                        retry_after_seconds=self._retry_after_seconds(exc),
                        traffic_class=traffic_class,
                        operation=operation, request_kind="read",
                        admission_token=admission_token,
                        transport_failure=read_backoff.is_transport_failure(exc),
                        server_ids=response_ids(self._response_headers(exc),
                                                getattr(exc, "trace_id", None)),
                    )
                # Retry only a confirmed HTTP 429. Polymarket documents that
                # requests delayed over five seconds can return the text
                # "Global Rate Limit Exceeded" even though it is not a rate
                # limit; slowing down in response makes that failure worse.
                if self._status_code(exc) != 429 or attempt + 1 >= self.retry_attempts:
                    raise self._adapter_error(operation, exc) from exc
                if (isinstance(self.limiter, AsyncIntervalLimiter)
                        and read_backoff.endpoint_for(_request_category(operation, "read"))
                        is not None):
                    # Fail this proof promptly. The next ordinary caller may
                    # claim one endpoint recovery probe after bounded backoff.
                    # Never hold the serial exit owner in a read retry ladder.
                    raise self._adapter_error(operation, exc) from exc
                if _request_category(operation, "read") == "market_book":
                    # Let the next ordinary caller claim the single recovery
                    # probe; never hold the decision loop in book retry sleep.
                    raise self._adapter_error(operation, exc) from exc
                # A selection/report retry is awaited by its caller and can
                # therefore serialize the live runner even though its limiter
                # lock is free. Publish the lane cooldown, then fail promptly;
                # the caller's normal later cadence is the retry. Safety proof
                # may retry inline, but never sleeps longer than its own short
                # lane bound.
                if (isinstance(self.limiter, AsyncIntervalLimiter)
                        and traffic_class != "safety"):
                    raise self._adapter_error(operation, exc) from exc
                retry_after = self._retry_after_seconds(exc)
                delay = (self.retry_base_seconds * (2 ** attempt)
                         if retry_after is None else min(
                             retry_after, self.max_retry_after_seconds))
                if isinstance(self.limiter, AsyncIntervalLimiter):
                    delay = min(
                        delay,
                        self.limiter.RATE_LIMIT_MAX_SAFETY_COOLDOWN_SECONDS)
                    await self.limiter.sleep_interruptibly(
                        delay, sleeper=self._sleep)
                else:
                    await self._sleep(delay)
        raise AssertionError("unreachable retry loop")

    async def fetch_incentives(
        self, symbols: Iterable[str]
    ) -> dict[str, MarketIncentive]:
        observed_at = datetime.now(timezone.utc)
        clean = list(dict.fromkeys(str(symbol).strip() for symbol in symbols
                                   if str(symbol).strip()))
        if not clean:
            return {}
        found_periods: dict[str, list[IncentivePeriod]] = {}
        reported_clocks: dict[str, datetime | None] = {}
        for offset in range(0, len(clean), self.incentive_batch_size):
            chunk = clean[offset:offset + self.incentive_batch_size]
            await self._collect_incentive_pages(
                found_periods,
                reported_clocks,
                symbols=chunk,
            )
        return self._incentive_models(found_periods, reported_clocks, observed_at)

    async def list_incentives(
        self,
        *,
        statuses: Iterable[str] = ("active",),
    ) -> dict[str, MarketIncentive]:
        """Enumerate liquidity programs for bounded diagnostics only.

        The official incentive endpoint permits an omitted ``symbols`` filter
        and paginates the complete program universe. On 2026-09-02,
        ``statuses=active`` still exceeded 20,000 returned market groups and
        the live startup safety ceiling. The live selector therefore must use
        event-bounded discovery plus :meth:`fetch_incentives` with exact
        symbols; this method is not a live-selection source. One shared REST
        governor still owns every request.
        """

        clean_statuses = list(dict.fromkeys(
            str(status).strip().lower() for status in statuses
            if str(status).strip()))
        allowed = {"active", "closed", "pending"}
        if not clean_statuses or any(item not in allowed for item in clean_statuses):
            raise ValueError(
                "incentive statuses must be active, closed, or pending")
        found_periods: dict[str, list[IncentivePeriod]] = {}
        reported_clocks: dict[str, datetime | None] = {}
        await self._collect_incentive_pages(
            found_periods,
            reported_clocks,
            statuses=clean_statuses,
        )
        return self._incentive_models(found_periods, reported_clocks)

    @staticmethod
    def _incentive_models(
        found_periods: dict[str, list[IncentivePeriod]],
        reported_clocks: dict[str, datetime | None],
        observed_at: datetime | None = None,
    ) -> dict[str, MarketIncentive]:
        return {
            slug: MarketIncentive(
                slug=slug,
                periods=tuple(periods),
                reported_event_start=reported_clocks.get(slug),
                observed_at=observed_at,
            )
            for slug, periods in found_periods.items()
        }

    async def _collect_incentive_pages(
        self,
        found_periods: dict[str, list[IncentivePeriod]],
        reported_clocks: dict[str, datetime | None],
        *,
        symbols: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> None:
        token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(self.incentive_max_pages):
            # The deployed API was verified with snake_case pagination/filter
            # keys.  ``symbols`` is intentionally omitted for universe-wide
            # discovery, matching the official unfiltered endpoint contract.
            query: dict[str, Any] = {
                "page_size": 100,
                "program_type": "liquidityProgram",
            }
            if symbols is not None:
                query["symbols"] = symbols
            if statuses is not None:
                query["statuses"] = statuses
            if token:
                query["page_token"] = token
            operation = (
                "GET /v1/incentives (symbol-filtered)"
                if symbols is not None
                else "GET /v1/incentives (unfiltered)"
            )
            response = await self._call(
                operation,
                lambda query=query: self.client.get(
                    "/v1/incentives", query=query, authenticated=True),
            )
            if not isinstance(response, dict) or not isinstance(
                    response.get("programs"), list):
                raise AdapterError("incentives response has no programs list")
            for row in response["programs"]:
                if not isinstance(row, dict):
                    raise AdapterError("incentive program is not an object")
                slug = str(row.get("marketSlug") or "").strip()
                periods = row.get("timePeriods")
                if not slug or not isinstance(periods, list):
                    raise AdapterError("incentive program identity is incomplete")
                liquidity_periods = []
                for item in periods:
                    if not isinstance(item, dict):
                        raise AdapterError("incentive time period is not an object")
                    program_type = str(item.get("programType") or "").strip()
                    if not program_type:
                        raise AdapterError("incentive period programType is missing")
                    if program_type == "liquidityProgram":
                        liquidity_periods.append(_period(item))
                if liquidity_periods:
                    found_periods.setdefault(slug, []).extend(liquidity_periods)
                reported_clocks[slug] = _timestamp(
                    row.get("eventStartTime"), field="incentive.eventStartTime")
            next_token = response.get("nextPageToken")
            if next_token is None or str(next_token).strip() == "":
                return
            next_token = str(next_token)
            if next_token in seen_tokens:
                raise AdapterError("incentives pagination repeated a page token")
            seen_tokens.add(next_token)
            token = next_token
        raise AdapterError("incentives pagination exceeded incentive_max_pages")

    async def fetch_earnings(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        market_slug: str | None = None,
        program_type: str | None = None,
    ) -> tuple[RewardEarning, ...]:
        query = {
            key: value for key, value in {
                # The live Retail API currently honors snake_case query keys;
                # its published camelCase examples are silently ignored.
                "start_date": start_date,
                "end_date": end_date,
                "market_slug": market_slug,
                "program_type": program_type,
            }.items() if value is not None and str(value).strip()
        }
        response = await self._call(
            "GET /v1/incentives/earnings",
            lambda: self.client.get(
                "/v1/incentives/earnings",
                query=query or None,
                authenticated=True),
        )
        if not isinstance(response, dict) or not isinstance(response.get("rewards"), list):
            raise AdapterError("earnings response has no rewards list")
        out: list[RewardEarning] = []
        for row in response["rewards"]:
            if not isinstance(row, dict):
                raise AdapterError("earnings row is not an object")
            date = str(row.get("date") or "").strip()
            slug = str(row.get("marketSlug") or "").strip()
            status = str(row.get("status") or "").strip().upper()
            program_type = str(row.get("programType") or "").strip()
            reward = _amount(row.get("reward"), field="earning.reward")
            if not date or not slug or not status or not program_type or reward < 0:
                raise AdapterError("earnings row is incomplete")
            out.append(RewardEarning(date, slug, program_type, reward, status))
        return tuple(out)

    async def fetch_market(self, slug: str) -> MarketMetadata:
        response = await self._call(
            f"markets.retrieve_by_slug({slug})",
            lambda: self.client.markets.retrieve_by_slug(slug),
        )
        if not isinstance(response, dict) or not isinstance(response.get("market"), dict):
            raise AdapterError("market response has no market wrapper")
        return _market(response["market"], expected_slug=slug, require_clock=True)

    async def fetch_book(self, slug: str) -> BookSnapshot:
        runtime_book = getattr(self, "runtime_book", None)
        if runtime_book is not None:
            book = await runtime_book(slug)
            if book is not None:
                return book
        source = getattr(self, "selection_books", None)
        if source is not None:
            book = await source.get(slug)
            if book is not None:
                return book
        response = await self._call(
            f"markets.book({slug})", lambda: self.client.markets.book(slug))
        return parse_book_response(response, expected_slug=slug)

    async def fetch_settlement(self, slug: str) -> float:
        response = await self._call(
            f"markets.settlement({slug})",
            lambda: self.client.markets.settlement(slug),
        )
        if not isinstance(response, dict):
            raise AdapterError("market settlement response is not an object")
        actual = str(_wire_value(response, "marketSlug", "slug") or slug).strip()
        if actual != slug:
            raise AdapterError(
                f"settlement slug mismatch: expected {slug}, got {actual}")
        value = _amount(
            _wire_value(response, "settlementPrice", "settlement"),
            field="market.settlementPrice")
        if not 0 <= value <= 1:
            raise AdapterError("market settlement must be within [0, 1]")
        return value

    async def list_sports_markets(self) -> tuple[MarketMetadata, ...]:
        out: list[MarketMetadata] = []
        seen: set[str] = set()
        for page_number in range(self.max_pages):
            offset = page_number * self.catalog_page_size
            params = {
                "categories": ["sports"],
                "active": True,
                "closed": False,
                "limit": self.catalog_page_size,
                "offset": offset,
            }
            response = await self._call(
                f"markets.list(offset={offset})",
                lambda params=params: self.client.markets.list(params),
            )
            if not isinstance(response, dict) or not isinstance(response.get("markets"), list):
                raise AdapterError("market catalog response has no markets list")
            page = response["markets"]
            for row in page:
                try:
                    item = _market(row, require_clock=False)
                except AdapterError:
                    # One malformed candidate cannot authorize risk and is
                    # excluded. The page itself remains usable and complete.
                    continue
                if item.slug not in seen:
                    seen.add(item.slug)
                    out.append(item)
            if len(page) < self.catalog_page_size:
                return tuple(out)
        raise AdapterError("market catalog pagination exceeded max_pages")

    async def list_upcoming_sports_markets(
        self, *, start: datetime, end: datetime, limit: int = 500,
        require_complete: bool = False,
    ) -> tuple[MarketMetadata, ...]:
        """Bounded event pages flattened to upcoming market metadata.

        The observed 2026-08-28 Retail market/event list wire shape omits the
        documented volume fields even when ordered by volume.  This method is
        therefore only a low-cost discovery read.  The scalp collector ranks
        candidates later from book ``sharesTraded``/``notionalTraded`` stats.

        The stopped-only scalp measurement deliberately retains its original
        one-page bound by default.  Live selection passes
        ``require_complete=True``: a full page is not complete evidence, so
        that path keeps paging until a short page and fails closed at the
        explicit event ceiling instead of silently selecting from a truncated
        window.
        """

        if not 1 <= int(limit) <= 500:
            raise ValueError("upcoming event limit must be within [1, 500]")
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("upcoming event bounds must be aware and increasing")
        self.last_upcoming_event_pages = 0
        out: list[MarketMetadata] = []
        seen: set[str] = set()
        page_size = int(limit)
        page_ceiling = self.event_max_pages if require_complete else 1
        for page_number in range(page_ceiling):
            params = {
                "categories": ["sports"],
                "active": True,
                "closed": False,
                "startTimeMin": start.isoformat(),
                "startTimeMax": end.isoformat(),
                "limit": page_size,
                "offset": page_number * page_size,
            }
            response = await self._call(
                "events.list(upcoming-sports)",
                lambda params=params: self.client.events.list(params),
            )
            if (not isinstance(response, dict)
                    or not isinstance(response.get("events"), list)):
                raise AdapterError("upcoming sports response has no events list")
            self.last_upcoming_event_pages = page_number + 1
            page = response["events"]
            for event in page:
                if (not isinstance(event, dict)
                        or not isinstance(event.get("markets"), list)):
                    continue
                for row in event["markets"]:
                    try:
                        market = _market(row, require_clock=False)
                    except AdapterError:
                        continue
                    if market.slug not in seen:
                        seen.add(market.slug)
                        out.append(market)
            if len(page) < page_size or not require_complete:
                return tuple(out)
        raise AdapterError(
            "upcoming sports pagination exceeded event_max_pages")

    async def fetch_markets_by_slugs(
        self, slugs: Iterable[str],
    ) -> dict[str, MarketMetadata]:
        clean = list(dict.fromkeys(str(slug).strip() for slug in slugs
                                   if str(slug).strip()))
        if not clean:
            return {}
        if len(clean) > 100:
            raise ValueError("slug-filtered market read supports at most 100 markets")
        params = {"slug": clean, "limit": len(clean), "offset": 0}
        response = await self._call(
            "markets.list(slug-filtered)",
            lambda: self.client.markets.list(params),
        )
        if not isinstance(response, dict) or not isinstance(response.get("markets"), list):
            raise AdapterError("slug-filtered market response has no markets list")
        out: dict[str, MarketMetadata] = {}
        expected = set(clean)
        for row in response["markets"]:
            try:
                market = _market(row, require_clock=False)
            except AdapterError:
                continue
            if market.slug in expected:
                out[market.slug] = market
        return out
