"""Report-only reward-period attribution of local resting side-hours.

Uses the existing market/side union (including exit-order coverage), excluding
rejected orders, which never supplied liquidity.
Missing or conflicting order-level evidence stays unallocated; never infer
historical reward types from today's market settings. No venue requests.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from liquidity_bot.resting_time import WORKING_ORDER_STATES, _day_bounds, _parse
from liquidity_bot.reward_calendar import NEW_YORK


def resting_period_hours_by_day(
    db: str, days: list[date], *, now: datetime,
) -> dict[str, dict[str, float]]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not days:
        return {}
    first, _ = _day_bounds(min(days))
    _, last = _day_bounds(max(days))
    last = min(last, now.astimezone(timezone.utc))
    result = {day.isoformat(): dict(day_of=0.0, daily_event=0.0, unallocated=0.0)
              for day in days}
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    events = defaultdict(list)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        has_observations = "execution_quote_observations" in tables
        observation_columns = (
            "q.market_slug AS q_market,q.side AS q_side,q.observation_json"
            if has_observations else
            "NULL AS q_market,NULL AS q_side,NULL AS observation_json")
        observation_join = (
            "LEFT JOIN execution_quote_observations q ON q.client_order_id=o.client_order_id "
            if has_observations else "")
        # One short consistent read, one grouped intent scan, no N+1 reads.
        query = (
            "WITH placed AS (SELECT client_order_id,"
            "MIN(CASE WHEN operation='PLACE' THEN created_at END) AS placed_at,"
            "MAX(CASE WHEN operation='MODIFY' THEN 1 ELSE 0 END) AS modified "
            "FROM execution_intents WHERE client_order_id IS NOT NULL GROUP BY client_order_id) "
            "SELECT o.*,p.placed_at,p.modified," + observation_columns + " "
            "FROM execution_orders o JOIN placed p ON p.client_order_id=o.client_order_id "
            + observation_join)
        for row in conn.execute(query):
            if row["status"] == "REJECTED":
                continue
            placed = _parse(row["placed_at"])
            end = (now if row["status"] in WORKING_ORDER_STATES else _parse(row["event_at"]))
            if placed is None or end is None:
                continue
            start, end = max(placed, first), min(end, last)
            if end <= start:
                continue
            side = "bid" if "BUY" in str(row["side"]).upper() else "offer"
            period = "unallocated"
            try:
                doc = json.loads(row["observation_json"])
                observed = _parse(doc.get("observed_at"))
                raw = str(doc["reward_period"]).lower().replace("-", "_")
                raw = {"dayof": "day_of"}.get(raw, raw)
                if (not row["modified"]
                        and doc["market_slug"] == row["market_slug"] == row["q_market"]
                        and doc["side"] == side == row["q_side"]
                        # Observation and intent are written atomically, but
                        # the observer clock normally follows created_at by
                        # milliseconds. Same-order identity is the provenance;
                        # reject evidence after the inspected order interval.
                        and observed is not None and observed <= end
                        and raw in {"day_of", "daily_event"}):
                    period = raw
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
            key = (row["market_slug"], side)
            events[key].extend(((start, period, 1), (end, period, -1)))
    finally:
        conn.close()
    # Sweep each market/side once. Conflicting overlapping types are unknown,
    # not double-counted or arbitrarily assigned to the newest order.
    for changes in events.values():
        active = Counter()
        previous = None
        for at, period, delta in sorted(changes):
            kinds = {kind for kind, count in active.items() if count > 0}
            if previous is not None and at > previous and kinds:
                owner = next(iter(kinds)) if len(kinds) == 1 else "unallocated"
                cursor = previous
                while cursor < at:
                    day = cursor.astimezone(NEW_YORK).date()
                    stop = min(at, _day_bounds(day)[1])
                    if day.isoformat() in result:
                        result[day.isoformat()][owner] += (stop-cursor).total_seconds()/3600
                    cursor = stop
            active[period] += delta
            previous = at
    return result
