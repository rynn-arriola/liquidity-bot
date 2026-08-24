"""Polymarket reward-day boundaries.

The earnings API groups rewards by midnight-to-midnight America/New_York,
including daylight-saving changes.  Reporting code must put trading events in
that same calendar before comparing a day's trading with a day's reward.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")


def _aware_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("reward timestamps must be timezone-aware")
    return parsed


def reward_day(value: datetime | str) -> str:
    """Return the venue reward date for a timezone-aware instant."""

    return _aware_datetime(value).astimezone(NEW_YORK).date().isoformat()


def reward_day_bounds(value: date | str) -> tuple[datetime, datetime]:
    """Return one ET reward day's half-open bounds as UTC datetimes."""

    day = value if isinstance(value, date) else date.fromisoformat(str(value))
    start_local = datetime.combine(day, time.min, tzinfo=NEW_YORK)
    end_local = datetime.combine(day + timedelta(days=1), time.min,
                                 tzinfo=NEW_YORK)
    return (start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc))


def split_reward_day_interval(
    start: datetime, end: datetime,
) -> list[tuple[str, float]]:
    """Split a timezone-aware interval across venue ET reward days."""

    start = _aware_datetime(start)
    end = _aware_datetime(end)
    if end < start:
        raise ValueError("reward interval end precedes start")
    if end == start:
        return []
    out: list[tuple[str, float]] = []
    cursor = start
    while cursor < end:
        day = reward_day(cursor)
        _, boundary = reward_day_bounds(day)
        # Preserve the caller's timezone for subtraction and return values.
        boundary = boundary.astimezone(cursor.tzinfo)
        piece_end = min(end, boundary)
        elapsed = (piece_end.astimezone(timezone.utc)
                   - cursor.astimezone(timezone.utc)).total_seconds()
        out.append((day, elapsed))
        cursor = piece_end
    return out
