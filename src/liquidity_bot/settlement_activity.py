"""Read-only venue market settlements for reporting.

A market that resolves while the account holds inventory closes that inventory
without a fill, so the fill ledger never sees the outcome. The venue records
each such close as an ``ACTIVITY_TYPE_POSITION_RESOLUTION`` activity. This
module reads those activities (bounded, report lane) and keeps a small local
cache so each hourly report only reads the newest page.

Reporting only: nothing here is used by trading, risk or selection.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

RESOLUTION_TYPE = "ACTIVITY_TYPE_POSITION_RESOLUTION"
CACHE_FILENAME = "settlement-resolutions.json"
CACHE_SCHEMA = 1
# Activities are sorted by creation time, which can trail the resolution
# time. Re-read this much history on every incremental refresh.
INCREMENTAL_OVERLAP = timedelta(days=2)
INITIAL_MAX_PAGES = 12
INCREMENTAL_MAX_PAGES = 3
PAGE_LIMIT = 100


class SettlementUnavailable(ValueError):
    """The settlement history cannot be proved from bounded reads."""


@dataclass(frozen=True)
class Resolution:
    market_slug: str
    resolved_at: datetime
    long_price: Decimal
    venue_net_before: Decimal

    @property
    def key(self) -> tuple[str, str]:
        return self.market_slug, self.resolved_at.isoformat()


@dataclass(frozen=True)
class ResolutionHistory:
    resolutions: tuple[Resolution, ...]
    # Every resolution at or before this instant is known. None means no
    # settlement history is available at all.
    complete_through: datetime | None
    error: str | None = None


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SettlementUnavailable(f"{field} is invalid") from exc
    if not result.is_finite():
        raise SettlementUnavailable(f"{field} is not finite")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # The venue sends nanoseconds; keep microseconds.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = len(tail) - len(tail.lstrip("0123456789"))
        text = f"{head}.{tail[:min(digits, 6)]}{tail[digits:]}"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SettlementUnavailable(f"{field} is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise SettlementUnavailable(f"{field} is timezone-naive")
    return result.astimezone(timezone.utc)


def parse_resolution(activity: Any) -> Resolution | None:
    """Return the settlement, or None when no position was held."""

    if not isinstance(activity, dict):
        raise SettlementUnavailable("resolution activity is not an object")
    if str(activity.get("type") or "").upper() != RESOLUTION_TYPE:
        raise SettlementUnavailable("unexpected activity type")
    body = activity.get("positionResolution")
    if not isinstance(body, dict):
        raise SettlementUnavailable("resolution body is missing")
    slug = str(body.get("marketSlug") or "").strip()
    if not slug:
        raise SettlementUnavailable("resolution market is missing")
    before = body.get("beforePosition") or {}
    if not isinstance(before, dict):
        raise SettlementUnavailable("resolution position is invalid")
    net = _decimal(
        before.get("netPositionDecimal", before.get("netPosition", 0)),
        "resolution position")
    if net == 0:
        return None
    resolved_at = _timestamp(body.get("updateTime"), "resolution time")
    market = body.get("market")
    sides = market.get("marketSides") if isinstance(market, dict) else None
    if not isinstance(sides, list):
        raise SettlementUnavailable(f"{slug}: resolution sides are missing")
    long_sides = [side for side in sides
                  if isinstance(side, dict) and side.get("long") is True]
    if len(long_sides) != 1:
        raise SettlementUnavailable(f"{slug}: resolution long side is ambiguous")
    price = _decimal(long_sides[0].get("price"), f"{slug}: resolution price")
    if not Decimal(0) <= price <= Decimal(1):
        raise SettlementUnavailable(f"{slug}: resolution price is out of range")
    return Resolution(slug, resolved_at, price, net)


async def fetch_resolutions(
    gateway: Any, *, stop_before: datetime | None, max_pages: int,
) -> tuple[Resolution, ...]:
    """Read resolutions newest first until ``stop_before`` or the end.

    Fails closed when the page bound is reached first, so a partial history
    is never presented as complete.
    """

    cursor: str | None = None
    seen: set[str] = set()
    found: list[Resolution] = []
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "limit": PAGE_LIMIT,
            "types": [RESOLUTION_TYPE],
            "sortOrder": "SORT_ORDER_DESCENDING",
        }
        if cursor:
            params["cursor"] = cursor
        response = await gateway._call(
            "GET /v1/portfolio/activities (resolutions)",
            lambda params=params: gateway.client.portfolio.activities(params))
        if not isinstance(response, dict) or not isinstance(
                response.get("activities"), list):
            raise SettlementUnavailable(
                "resolution response has no activities list")
        reached = False
        for activity in response["activities"]:
            body = activity.get("positionResolution") if isinstance(
                activity, dict) else None
            if isinstance(body, dict) and stop_before is not None:
                if _timestamp(body.get("updateTime"),
                              "resolution time") < stop_before:
                    reached = True
            parsed = parse_resolution(activity)
            if parsed is not None:
                found.append(parsed)
        eof = response.get("eof")
        if eof is True or reached:
            return tuple(found)
        if eof is not False:
            raise SettlementUnavailable(
                "resolution pagination has no explicit eof")
        next_cursor = response.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor.strip():
            raise SettlementUnavailable(
                "resolution pagination ended without eof")
        if next_cursor in seen:
            raise SettlementUnavailable("resolution pagination cursor repeated")
        seen.add(next_cursor)
        cursor = next_cursor
    raise SettlementUnavailable("resolution scan exceeded its page bound")


def _encode(item: Resolution) -> dict[str, str]:
    return {
        "market_slug": item.market_slug,
        "resolved_at": item.resolved_at.isoformat(),
        "long_price": format(item.long_price, "f"),
        "venue_net_before": format(item.venue_net_before, "f"),
    }


def load_cache(path: Path) -> ResolutionHistory:
    """Missing or unreadable cache means no history; never raises."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
            raise SettlementUnavailable("settlement cache schema is unsupported")
        complete = _timestamp(payload.get("complete_through"),
                              "settlement cache time")
        items = tuple(
            Resolution(
                str(row["market_slug"]),
                _timestamp(row["resolved_at"], "cached resolution time"),
                _decimal(row["long_price"], "cached resolution price"),
                _decimal(row["venue_net_before"], "cached resolution position"),
            ) for row in payload.get("resolutions") or ())
    except FileNotFoundError:
        return ResolutionHistory((), None, "settlement cache is missing")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return ResolutionHistory((), None, f"settlement cache is invalid: {exc}")
    return ResolutionHistory(items, complete)


def write_cache(path: Path, history: ResolutionHistory) -> None:
    if history.complete_through is None:
        raise ValueError("only a complete history is cached")
    payload = {
        "schema": CACHE_SCHEMA,
        "complete_through": history.complete_through.isoformat(),
        "resolutions": [_encode(item) for item in history.resolutions],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def refresh_resolutions(
    gateway: Any, path: Path, *, now: datetime,
) -> ResolutionHistory:
    """Merge the newest venue page(s) into the cache; never raises.

    On a read failure the cached history is returned with its older
    ``complete_through``, so callers can decline days it does not cover.
    """

    cached = load_cache(path)
    incremental = cached.complete_through is not None
    stop_before = (cached.complete_through - INCREMENTAL_OVERLAP
                   if incremental else None)
    try:
        fresh = await fetch_resolutions(
            gateway, stop_before=stop_before,
            max_pages=INCREMENTAL_MAX_PAGES if incremental else INITIAL_MAX_PAGES)
    except Exception as exc:  # reporting must degrade, not crash
        return ResolutionHistory(
            cached.resolutions, cached.complete_through,
            f"{type(exc).__name__}: {exc}")
    merged = {item.key: item for item in cached.resolutions}
    merged.update((item.key, item) for item in fresh)
    history = ResolutionHistory(
        tuple(sorted(merged.values(), key=lambda item: item.resolved_at)), now)
    try:
        write_cache(path, history)
    except OSError as exc:
        return ResolutionHistory(
            history.resolutions, history.complete_through,
            f"settlement cache write failed: {exc}")
    return history
