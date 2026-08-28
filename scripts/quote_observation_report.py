#!/usr/bin/env python3
"""Read-only coverage and fill-label report for building quote observations.

This script opens only the local execution ledger.  It makes no venue calls,
does not group or filter by league, and does not recommend a strategy gate.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Callable, Iterable

from liquidity_bot.sqlite_compat import active_fills_predicate


TERMINAL = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(
    connection: sqlite3.Connection, *, recent_hours: float | None,
) -> list[sqlite3.Row]:
    where = ""
    params: tuple[str, ...] = ()
    if recent_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
        where = "WHERE q.observed_at>=?"
        params = (cutoff.isoformat(),)
    active_fills = active_fills_predicate(connection)
    return list(connection.execute(
        f"""
        WITH first_fill AS (
            SELECT client_order_id,
                   MIN(event_at) AS first_fill_at,
                   SUM(CAST(qty AS REAL)) AS filled_qty,
                   MAX(CASE WHEN UPPER(COALESCE(liquidity,''))='MAKER'
                            THEN 1 ELSE 0 END) AS has_maker_fill
            FROM execution_fills
            WHERE client_order_id IS NOT NULL AND {active_fills}
            GROUP BY client_order_id
        )
        SELECT q.*, o.status AS order_status, o.reject_reason,
               f.first_fill_at, f.filled_qty, f.has_maker_fill
        FROM execution_quote_observations q
        LEFT JOIN execution_orders o USING (client_order_id)
        LEFT JOIN first_fill f USING (client_order_id)
        {where}
        ORDER BY q.observed_at
        """, params).fetchall())


def _label(row: sqlite3.Row) -> str:
    if row["first_fill_at"] is not None:
        return "maker_filled" if row["has_maker_fill"] else "filled_unknown"
    status = str(row["order_status"] or "").upper()
    if status == "FILLED":
        return "filled_state_missing_execution"
    if status in TERMINAL:
        return "terminal_unfilled"
    return "open_or_unresolved"


def _bucket_report(
    rows: Iterable[sqlite3.Row],
    title: str,
    key: Callable[[sqlite3.Row], str],
) -> None:
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(_label(row))
    print(f"\n{title}")
    for name in sorted(groups):
        labels = groups[name]
        resolved = sum(value != "open_or_unresolved" for value in labels)
        filled = sum(value in {
            "maker_filled", "filled_unknown", "filled_state_missing_execution"
        } for value in labels)
        rate = 100.0 * filled / resolved if resolved else 0.0
        print(
            f"  {name:24s} observations={len(labels):5d} "
            f"resolved={resolved:5d} filled={filled:5d} fill_rate={rate:6.2f}%"
        )


def render_report(
    connection: sqlite3.Connection, *, recent_hours: float | None = None,
) -> str:
    """Print the report and return the collection-readiness verdict."""

    if not _table_exists(connection, "execution_quote_observations"):
        print("quote observation table is not installed in this ledger")
        return "NOT_INSTALLED"
    rows = _rows(connection, recent_hours=recent_hours)
    required_where = "WHERE operation='PLACE' AND quote_observation_required=1"
    required_params: tuple[str, ...] = ()
    if recent_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
        required_where += " AND created_at>=?"
        required_params = (cutoff.isoformat(),)
    required = int(connection.execute(
        f"SELECT COUNT(*) FROM execution_intents {required_where}",
        required_params).fetchone()[0])
    captured = len(rows)
    missing = max(0, required - captured)
    print("Liquidity quote observation coverage")
    print(f"  required building PLACE intents : {required}")
    print(f"  observations captured           : {captured}")
    print(f"  missing observations            : {missing}")
    if not rows:
        print("  no observations collected yet")
        return "COLLECTING"

    labels = [_label(row) for row in rows]
    maker_filled = labels.count("maker_filled")
    filled_unknown = labels.count("filled_unknown")
    filled_state_missing = labels.count("filled_state_missing_execution")
    terminal_unfilled = labels.count("terminal_unfilled")
    open_or_unresolved = labels.count("open_or_unresolved")
    first = datetime.fromisoformat(rows[0]["observed_at"])
    last = datetime.fromisoformat(rows[-1]["observed_at"])
    span_days = max(0.0, (last - first).total_seconds() / 86400.0)
    latencies = []
    for row in rows:
        if row["first_fill_at"] is None:
            continue
        latency = (
            datetime.fromisoformat(row["first_fill_at"])
            - datetime.fromisoformat(row["observed_at"])
        ).total_seconds()
        if latency >= 0:
            latencies.append(latency)
    print(f"  observed span                   : {span_days:.2f} days")
    print(f"  maker-filled                    : {maker_filled}")
    print(f"  filled, liquidity unknown       : {filled_unknown}")
    print(f"  FILLED state, execution missing : {filled_state_missing}")
    print(f"  terminal without fill           : {terminal_unfilled}")
    print(f"  still open/unresolved            : {open_or_unresolved}")
    if latencies:
        print(f"  median placement-to-fill        : {median(latencies):.1f}s")
    trade_rows = sum(
        row["trade_volume_30s"] is not None
        or row["trade_volume_120s"] is not None for row in rows)
    print(
        "  public trade-volume coverage    : "
        f"{trade_rows}/{captured} (book stream currently does not subscribe trades)"
    )

    _bucket_report(
        rows,
        "Fill labels by model driver (diagnostic only)",
        lambda row: str(row["fill_risk_driver"] or "unknown"),
    )
    _bucket_report(
        rows,
        "Fill labels by BBO changes in prior 120 seconds",
        lambda row: (
            "0" if int(row["touch_changes_120s"]) == 0
            else "1-2" if int(row["touch_changes_120s"]) <= 2
            else "3+"
        ),
    )
    _bucket_report(
        rows,
        "Fill labels by observed queue / Target Size",
        lambda row: (
            "unknown" if not row["target_size"]
            else "<0.25" if row["observed_queue_ahead"] / row["target_size"] < .25
            else "0.25-0.99" if row["observed_queue_ahead"] / row["target_size"] < 1
            else ">=1.00"
        ),
    )

    verdict = (
        "READY_FOR_HELD_OUT_SPLIT"
        if missing == 0 and span_days >= 3.0 and maker_filled >= 300
        else "COLLECTING"
    )
    print(f"\nverdict: {verdict}")
    if verdict == "COLLECTING":
        print("Do not enforce a refusal rule yet; target is 3 full UTC days and ~300 maker fills.")
    else:
        print("Freeze the first two UTC days as training and evaluate the third held out.")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", default="data/commissioning.db", type=Path,
        help="local execution ledger (opened read-only)")
    parser.add_argument(
        "--recent-hours", type=float,
        help="limit coverage and labels to a recent UTC window")
    args = parser.parse_args()
    if args.recent_hours is not None and args.recent_hours <= 0:
        parser.error("--recent-hours must be positive")
    connection = _connect(args.database)
    try:
        render_report(connection, recent_hours=args.recent_hours)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
