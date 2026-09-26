"""Read-only backtest of arbitrary exit horizons from scalp raw paths."""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .models import BookLevel, BookSnapshot
from .shadow_math import cross_exit


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _book(row: sqlite3.Row) -> BookSnapshot:
    bids = json.loads(zlib.decompress(row["bids_zlib"]).decode())
    offers = json.loads(zlib.decompress(row["offers_zlib"]).decode())
    return BookSnapshot(
        slug=row["market_slug"],
        bids=tuple(BookLevel(float(price), float(qty))
                   for price, qty in bids),
        offers=tuple(BookLevel(float(price), float(qty))
                     for price, qty in offers),
        state=row["state"],
        transact_time=_at(row["exchange_time"]) if row["exchange_time"] else None,
    )


def _fill_state(connection: sqlite3.Connection, episode_id: int,
                until: datetime | None = None) -> tuple[float, float, float]:
    params: list[Any] = [episode_id]
    where = "episode_id=?"
    if until is not None:
        where += " AND signaled_at<=?"
        params.append(until.isoformat())
    row = connection.execute(
        "SELECT COALESCE(SUM(CASE WHEN side='bid' THEN qty ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN side='offer' THEN qty ELSE 0 END),0),"
        "COALESCE(SUM(maker_rebate_usd),0) FROM scalp_episode_fills WHERE " + where,
        tuple(params)).fetchone()
    return float(row[0]), float(row[1]), float(row[2])


def _position_pnl(episode: sqlite3.Row, bid_qty: float, offer_qty: float,
                  rebates: float, book: BookSnapshot, *, depth_haircut: float,
                  taker_theta: float) -> tuple[bool, float]:
    paired = min(bid_qty, offer_qty)
    pnl = paired * (float(episode["offer_price"]) - float(episode["bid_price"]))
    residual = abs(bid_qty - offer_qty)
    if residual <= 1e-9:
        return True, pnl + rebates
    side = "bid" if bid_qty > offer_qty else "offer"
    price = float(episode["bid_price"] if side == "bid" else episode["offer_price"])
    result = cross_exit(
        book, entry_side=side, entry_price=price, qty=residual,
        depth_haircut=depth_haircut, taker_theta=taker_theta)
    return result.complete, pnl + rebates + result.pnl


def build_scalp_report(path: Path, horizons: Iterable[int], *,
                       depth_haircut: float = 0.5,
                       taker_theta: float = 0.06,
                       maximum_book_age_seconds: float = 30.0) -> dict[str, Any]:
    clean_horizons = tuple(sorted(set(int(value) for value in horizons)))
    if not clean_horizons or clean_horizons[0] <= 0:
        raise ValueError("exit horizons must be positive")
    if maximum_book_age_seconds <= 0:
        raise ValueError("maximum book age must be positive")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        episodes = list(connection.execute(
            "SELECT * FROM scalp_episodes ORDER BY started_at,id"))
        by_cohort: dict[str, list[sqlite3.Row]] = defaultdict(list)
        by_spread: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in episodes:
            by_cohort[row["cohort"]].append(row)
            bucket = f"{float(row['initial_spread']):.3f}"
            by_spread[(row["cohort"], bucket)].append(row)

        cohort_rows = []
        for cohort, rows in sorted(by_cohort.items()):
            completed = [row for row in rows if row["status"] == "BOTH_FILLED"]
            resolved = [row for row in rows
                        if row["status"] not in {"OPEN", "RUN_ENDED_CENSORED"}]
            second_leg = [
                (_at(row["completed_at"]) - _at(row["first_fill_at"])).total_seconds()
                for row in completed if row["first_fill_at"] and row["completed_at"]]
            cohort_rows.append({
                "cohort": cohort,
                "started_two_sided_episodes": len(rows),
                "resolved_episodes": len(resolved),
                "pending_or_censored": len(rows) - len(resolved),
                "both_sides_filled": len(completed),
                "both_side_fill_rate": (
                    len(completed) / len(resolved) if resolved else None),
                "one_side_or_partial": sum(
                    row["first_fill_at"] is not None
                    and row["status"] != "BOTH_FILLED" for row in resolved),
                "no_fill": sum(
                    row["first_fill_at"] is None for row in resolved),
                "median_second_leg_seconds": median(second_leg) if second_leg else None,
                "completed_pair_net_pnl_usd": sum(
                    float(row["estimated_net_pair_pnl_usd"] or 0) for row in completed),
            })

        spread_rows = []
        for (cohort, bucket), rows in sorted(by_spread.items()):
            completed = [row for row in rows if row["status"] == "BOTH_FILLED"]
            resolved = [row for row in rows
                        if row["status"] not in {"OPEN", "RUN_ENDED_CENSORED"}]
            spread_rows.append({
                "cohort": cohort,
                "spread": float(bucket),
                "episodes": len(rows),
                "resolved_episodes": len(resolved),
                "both_filled": len(completed),
                "both_side_fill_rate": (
                    len(completed) / len(resolved) if resolved else None),
                "net_pnl_usd": sum(
                    float(row["estimated_net_pair_pnl_usd"] or 0) for row in completed),
            })

        now = datetime.now(timezone.utc)
        run_cutoffs = {
            row["run_id"]: (_at(row["ended_at"]) if row["ended_at"] else now)
            for row in connection.execute("SELECT run_id,ended_at FROM scalp_runs")
        }
        replay_rows = []
        for horizon in clean_horizons:
            grouped: dict[str, list[tuple[bool, float, float]]] = defaultdict(list)
            missing: dict[str, int] = defaultdict(int)
            pending: dict[str, int] = defaultdict(int)
            for episode in episodes:
                if not episode["first_fill_at"]:
                    continue
                due = _at(episode["first_fill_at"]) + timedelta(seconds=horizon)
                if due > run_cutoffs[episode["run_id"]]:
                    pending[episode["cohort"]] += 1
                    continue
                bid_qty, offer_qty, rebates = _fill_state(
                    connection, int(episode["id"]), due)
                if (bid_qty >= float(episode["requested_qty"]) - 1e-9
                        and offer_qty >= float(episode["requested_qty"]) - 1e-9):
                    pnl = (float(episode["requested_qty"])
                           * (float(episode["offer_price"])
                              - float(episode["bid_price"])) + rebates)
                    grouped[episode["cohort"]].append((True, pnl, 0.0))
                    continue
                book_row = connection.execute(
                    "SELECT * FROM scalp_books WHERE run_id=? AND market_slug=? "
                    "AND observed_at<=? ORDER BY observed_at DESC LIMIT 1",
                    (episode["run_id"], episode["market_slug"], due.isoformat())
                ).fetchone()
                if book_row is None:
                    missing[episode["cohort"]] += 1
                    continue
                age = (due - _at(book_row["observed_at"])).total_seconds()
                if age < -1e-9 or age > maximum_book_age_seconds:
                    missing[episode["cohort"]] += 1
                    continue
                complete, pnl = _position_pnl(
                    episode, bid_qty, offer_qty, rebates, _book(book_row),
                    depth_haircut=depth_haircut, taker_theta=taker_theta)
                grouped[episode["cohort"]].append((complete, pnl, age))
            for cohort in sorted(set(grouped) | set(missing) | set(pending)):
                values = grouped[cohort]
                complete = [pnl for ok, pnl, _age in values if ok]
                replay_rows.append({
                    "horizon_seconds": horizon,
                    "cohort": cohort,
                    "samples": len(values),
                    "complete_exits": len(complete),
                    "exit_completion_rate": (
                        len(complete) / len(values) if values else None),
                    "missing_fresh_book": missing[cohort],
                    "pending_horizon": pending[cohort],
                    "maximum_book_age_seconds": maximum_book_age_seconds,
                    "mean_book_age_seconds": (
                        sum(age for _ok, _pnl, age in values) / len(values)
                        if values else None),
                    "net_pnl_usd": sum(complete),
                    "mean_net_pnl_usd": sum(complete) / len(complete) if complete else None,
                })

        settlement_rows = []
        for episode in episodes:
            settlement = connection.execute(
                "SELECT settlement_price FROM scalp_settlements WHERE run_id=? "
                "AND market_slug=?",
                (episode["run_id"], episode["market_slug"])).fetchone()
            if settlement is None or not episode["first_fill_at"]:
                continue
            bid_qty, offer_qty, rebates = _fill_state(connection, int(episode["id"]))
            paired = min(bid_qty, offer_qty)
            long_only = max(0.0, bid_qty - paired)
            short_only = max(0.0, offer_qty - paired)
            px = float(settlement[0])
            pnl = (
                paired * (float(episode["offer_price"])
                          - float(episode["bid_price"]))
                + long_only * (px - float(episode["bid_price"]))
                + short_only * (float(episode["offer_price"]) - px)
                + rebates)
            settlement_rows.append({
                "episode_id": int(episode["id"]),
                "cohort": episode["cohort"],
                "market_slug": episode["market_slug"],
                "settlement_price": px,
                "pnl_usd": pnl,
            })

        counts = dict(connection.execute(
            "SELECT (SELECT COUNT(*) FROM scalp_runs) runs,"
            "(SELECT COUNT(*) FROM scalp_markets) markets,"
            "(SELECT COUNT(*) FROM scalp_books) books,"
            "(SELECT COUNT(*) FROM scalp_trades) trades,"
            "(SELECT COUNT(*) FROM scalp_episodes) episodes,"
            "(SELECT COUNT(*) FROM scalp_settlements) settlements,"
            "(SELECT COUNT(*) FROM scalp_stream_sessions WHERE status='ERROR') "
            "stream_errors").fetchone())
        now = datetime.now(timezone.utc)
        run_seconds = reconnects = gaps = 0.0
        for row in connection.execute(
                "SELECT started_at,ended_at,reconnects,coverage_gaps FROM scalp_runs"):
            run_seconds += ((_at(row["ended_at"]) if row["ended_at"] else now)
                            - _at(row["started_at"])).total_seconds()
            reconnects += int(row["reconnects"])
            gaps += int(row["coverage_gaps"])
        stream_seconds = 0.0
        for row in connection.execute(
                "SELECT started_at,ended_at FROM scalp_stream_sessions"):
            stream_seconds += ((_at(row["ended_at"]) if row["ended_at"] else now)
                               - _at(row["started_at"])).total_seconds()
        return {
            "database": str(path.resolve()),
            "counts": counts,
            "coverage": {
                "run_seconds": run_seconds,
                "stream_seconds": stream_seconds,
                "stream_fraction": (
                    min(1.0, stream_seconds / run_seconds) if run_seconds else None),
                "reconnects": int(reconnects),
                "decode_or_drop_gaps": int(gaps),
            },
            "cohorts": cohort_rows,
            "by_initial_spread": spread_rows,
            "early_exit_replays": replay_rows,
            "settlement_replays": settlement_rows,
            "interpretation": (
                "Fill signals are conservative public-trade/queue counterfactuals, "
                "not venue executions. A pair succeeds only when the full requested "
                "quantity fills on both original fixed-price sides."),
        }
    finally:
        connection.close()
