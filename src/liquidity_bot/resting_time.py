"""Read-only resting-time accounting shared by reports and live telemetry."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .reward_calendar import NEW_YORK


WORKING_ORDER_STATES = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, NEW_YORK)
    end = datetime.combine(day + timedelta(days=1), time.min, NEW_YORK)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def resting_side_hours_by_day(
    db: str, days: list[date], *, now: datetime,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return merged resting side-hours and elapsed hours for ET days.

    This is intentionally a read-only database calculation. Replacement
    acknowledgements can briefly leave overlapping orders for one market side,
    so their intervals are merged before they are summed.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not days:
        return {}, {}
    ordered_days = sorted(days)
    window_start, _ = _day_bounds(ordered_days[0])
    _, final_end = _day_bounds(ordered_days[-1])
    window_end = min(now.astimezone(timezone.utc), final_end)

    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        placed: dict[str, datetime] = {}
        for row in connection.execute(
                "SELECT client_order_id, created_at FROM execution_intents "
                "WHERE operation='PLACE' AND client_order_id IS NOT NULL"):
            at = _parse(row["created_at"])
            key = str(row["client_order_id"])
            if at is not None and (key not in placed or at < placed[key]):
                placed[key] = at

        intervals: dict[
            tuple[str, str], list[tuple[datetime, datetime]]
        ] = defaultdict(list)
        for row in connection.execute(
                "SELECT client_order_id, market_slug, side, status, event_at "
                "FROM execution_orders"):
            start = placed.get(str(row["client_order_id"]))
            if start is None:
                continue
            end = (now if str(row["status"]) in WORKING_ORDER_STATES
                   else _parse(row["event_at"]))
            if end is None:
                continue
            start = max(start.astimezone(timezone.utc), window_start)
            end = min(end.astimezone(timezone.utc), window_end)
            if end <= start:
                continue
            side = ("bid" if "BUY" in str(row["side"]).upper()
                    else "offer")
            intervals[(str(row["market_slug"]), side)].append((start, end))
    finally:
        connection.close()

    merged: list[tuple[datetime, datetime]] = []
    for spans in intervals.values():
        spans.sort()
        current_start, current_end = spans[0]
        for start, end in spans[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = start, end
        merged.append((current_start, current_end))

    wanted = set(ordered_days)
    hours: dict[str, float] = defaultdict(float)
    for start, end in merged:
        cursor = start
        while cursor < end:
            local_day = cursor.astimezone(NEW_YORK).date()
            _, next_midnight = _day_bounds(local_day)
            stop = min(end, next_midnight)
            if local_day in wanted:
                hours[local_day.isoformat()] += (
                    stop - cursor).total_seconds() / 3600
            cursor = stop

    elapsed: dict[str, float] = {}
    for day in ordered_days:
        start, end = _day_bounds(day)
        elapsed[day.isoformat()] = max(
            0.0, (min(window_end, end) - start).total_seconds() / 3600)
    return dict(hours), elapsed
