#!/usr/bin/env python3
"""Read-only report for the live almost-front queue measurement.

No venue calls are made. The report does not cancel, replace, rank, or filter
orders and never groups by league. A ``READY_FOR_REVIEW`` verdict authorizes
analysis only; it does not enable automatic queue resets.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from liquidity_bot.sqlite_compat import active_fills_predicate


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def render_report(
    connection: sqlite3.Connection,
    *,
    recent_hours: float | None = None,
) -> str:
    if not _table_exists(connection, "execution_queue_measurements"):
        print("queue measurement is not installed in this ledger")
        return "NOT_INSTALLED"

    where = ""
    params: tuple[str, ...] = ()
    if recent_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
        where = "WHERE qm.started_at>=?"
        params = (cutoff.isoformat(),)
    active_fills = active_fills_predicate(connection)
    rows = list(connection.execute(
        f"""
        WITH first_fill AS (
            SELECT client_order_id,MIN(event_at) AS first_fill_at,
                   SUM(CAST(qty AS REAL)) AS filled_qty,
                   MAX(CASE WHEN UPPER(COALESCE(liquidity,''))='MAKER'
                            THEN 1 ELSE 0 END) AS has_maker_fill
            FROM execution_fills
            WHERE client_order_id IS NOT NULL AND {active_fills}
            GROUP BY client_order_id
        )
        SELECT qm.*,f.first_fill_at,f.filled_qty,f.has_maker_fill
        FROM execution_queue_measurements qm
        LEFT JOIN first_fill f USING (client_order_id)
        {where}
        ORDER BY qm.started_at
        """, params).fetchall())

    print("Liquidity queue measurement")
    print("  mode                            : measurement only; no cancel/repost")
    print("  shadow trigger                  : upper bound <= 1.0x order quantity after 60s")
    if not rows:
        print("  measured orders                 : 0")
        print("\nverdict: COLLECTING")
        return "COLLECTING"

    first = datetime.fromisoformat(str(rows[0]["started_at"]))
    last_candidates = [
        datetime.fromisoformat(str(row["closed_at"] or row["last_persisted_at"]))
        for row in rows]
    last = max(last_candidates)
    span_days = max(0.0, (last - first).total_seconds() / 86400)
    active = sum(row["status"] == "ACTIVE" for row in rows)
    closed = len(rows) - active
    gap_orders = sum(int(row["coverage_gaps"]) > 0 for row in rows)
    baseline_orders = sum(bool(row["baseline_book_seen"]) for row in rows)
    filled = [row for row in rows
              if row["first_fill_at"] is not None or row["last_filled_qty"] > 0]
    maker_filled = sum(
        bool(row["has_maker_fill"]) and bool(row["baseline_book_seen"])
        for row in rows)
    signaled = [
        row for row in rows
        if (row["would_reset_at"] is not None
            and int(row["coverage_gaps"]) == 0
            and bool(row["baseline_book_seen"]))]
    signaled_fills = []
    lead_seconds = []
    for row in signaled:
        if row["first_fill_at"] is None:
            continue
        signal_at = datetime.fromisoformat(str(row["would_reset_at"]))
        fill_at = datetime.fromisoformat(str(row["first_fill_at"]))
        if signal_at < fill_at:
            signaled_fills.append(row)
            lead_seconds.append((fill_at - signal_at).total_seconds())

    print(f"  measured orders                 : {len(rows)}")
    print(f"  active / closed                 : {active} / {closed}")
    print(f"  observed span                   : {span_days:.2f} days")
    print(f"  orders with any fill            : {len(filled)}")
    print(f"  post-ACK queue baselines        : {baseline_orders}/{len(rows)}")
    print(f"  maker-filled with baseline      : {maker_filled}")
    print(f"  clean / coverage-gap orders     : {len(rows) - gap_orders} / {gap_orders}")
    print(f"  clean shadow reset signals      : {len(signaled)}")
    print(f"  signals preceding a later fill  : {len(signaled_fills)}")
    if lead_seconds:
        print(f"  median signal-to-fill lead      : {median(lead_seconds):.1f}s")
    print(f"  definite at-price trade volume  : {sum(row['trade_at_price_qty'] for row in rows):.2f}")
    print(f"  trade-through signals           : {sum(row['trade_through_qty'] > 0 for row in rows)}")
    print(f"  ambiguous depth removed         : {sum(row['ambiguous_removed_qty'] for row in rows):.2f}")

    session_where = ""
    session_params: tuple[str, ...] = ()
    if recent_hours is not None:
        session_where = "WHERE started_at>=?"
        session_params = params
    sessions = list(connection.execute(
        "SELECT * FROM execution_queue_stream_sessions " + session_where
        + " ORDER BY started_at", session_params).fetchall())
    trades_received = sum(int(row["trades_received"]) for row in sessions)
    dropped = sum(int(row["dropped_trades"]) for row in sessions)
    decoded = sum(int(row["decode_failures"]) for row in sessions)
    errors = sum(row["status"] == "ERROR" for row in sessions)
    collecting = sum(row["status"] == "COLLECTING" for row in sessions)
    print("\nPublic-trade stream coverage")
    print(f"  sessions / currently collecting : {len(sessions)} / {collecting}")
    print(f"  trades received                 : {trades_received}")
    print(f"  dropped / decode failures       : {dropped} / {decoded}")
    print(f"  errored sessions                : {errors}")
    if sessions:
        print(f"  latest health update            : {sessions[-1]['last_updated_at']}")

    complete = (
        span_days >= 3
        and maker_filled >= 300
        and gap_orders == 0
        and bool(sessions)
        and trades_received > 0
        and dropped == 0
        and decoded == 0
    )
    verdict = "READY_FOR_REVIEW" if complete else "COLLECTING"
    print(f"\nverdict: {verdict}")
    if complete:
        print("Review held-out avoided-fill value versus estimated reward dark time; do not enable automatically.")
    else:
        print("Keep measuring. Target is 3 full days, ~300 maker fills, and zero coverage gaps.")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", default="data/commissioning.db", type=Path,
        help="execution ledger (opened read-only)")
    parser.add_argument(
        "--recent-hours", type=float,
        help="limit measurements and stream sessions to a recent UTC window")
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
