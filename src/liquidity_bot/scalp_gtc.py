"""Read-only fixed-GTC spread-scalp replay from the isolated raw database.

This module has no client, REST, WebSocket, order, or commissioning imports.  It
reconstructs one hypothetical pair per selected market/cohort and deliberately
keeps the original prices and queue-ahead quantities until both sides fill or
the market settles.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .models import BookSnapshot, TradePrint
from .scalp_engine import ScalpMeasurementEngine, maker_rebate
from .scalp_report import _at, _book, _position_pnl, build_scalp_report


EPSILON = 1e-9


@dataclass(frozen=True)
class GtcReplayConfig:
    quantity: float = 10.0
    minimum_spread: float = 0.005
    feature_lookback_seconds: int = 300
    minimum_feature_coverage_seconds: int = 60
    early_exit_seconds: int = 3600
    depth_haircut: float = 0.5
    taker_theta: float = 0.06
    maker_theta: float = -0.0125
    maximum_book_age_seconds: float = 30.0
    cohorts: tuple[str, ...] = ("PURE_VOLUME_GTC",)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if not 0 <= self.minimum_spread < 1:
            raise ValueError("minimum spread must be within [0, 1)")
        if self.feature_lookback_seconds <= 0:
            raise ValueError("feature lookback must be positive")
        if not 0 <= self.minimum_feature_coverage_seconds <= self.feature_lookback_seconds:
            raise ValueError("minimum feature coverage is invalid")
        if self.early_exit_seconds <= 0:
            raise ValueError("early exit must be positive")
        if not 0 < self.depth_haircut <= 1:
            raise ValueError("depth haircut must be within (0, 1]")
        if self.taker_theta < 0 or self.maker_theta > 0:
            raise ValueError("fee theta signs are invalid")
        if self.maximum_book_age_seconds <= 0:
            raise ValueError("maximum book age must be positive")
        if not self.cohorts or any(not str(value).strip() for value in self.cohorts):
            raise ValueError("at least one named cohort is required")


@dataclass
class _Replay:
    run_id: str
    slug: str
    cohort: str
    started_at: datetime
    selection_rank: int
    rank_metric: str | None
    rank_value: float | None
    quantity: float
    bid_price: float
    offer_price: float
    bid_queue: float
    offer_queue: float
    features: dict[str, float | int | None]
    coverage_clean: bool
    bid_filled: float = 0.0
    offer_filled: float = 0.0
    rebates: float = 0.0
    first_fill_at: datetime | None = None
    completed_at: datetime | None = None
    settlement_at: datetime | None = None
    settlement_price: float | None = None
    data_cutoff: datetime | None = None
    fills: list[tuple[datetime, str, float, float]] | None = None

    @property
    def spread(self) -> float:
        return self.offer_price - self.bid_price

    @property
    def both_filled(self) -> bool:
        return (self.bid_filled >= self.quantity - EPSILON
                and self.offer_filled >= self.quantity - EPSILON)

    @property
    def resolved(self) -> bool:
        return self.both_filled or self.settlement_price is not None

    @property
    def end_at(self) -> datetime:
        if self.completed_at is not None:
            return self.completed_at
        if self.settlement_at is not None:
            return self.settlement_at
        return self.data_cutoff or self.started_at


def _queue_at(book: BookSnapshot, side: str, price: float) -> float:
    return sum(size for level_price, size in book.levels(side)
               if abs(level_price - price) < EPSILON)


def _run_cutoffs(connection: sqlite3.Connection) -> dict[str, datetime]:
    now = datetime.now(timezone.utc)
    result: dict[str, datetime] = {}
    for row in connection.execute("SELECT run_id,started_at,ended_at FROM scalp_runs"):
        if row["ended_at"]:
            result[str(row["run_id"])] = _at(row["ended_at"])
            continue
        values = connection.execute(
            "SELECT MAX(value) FROM ("
            "SELECT MAX(observed_at) value FROM scalp_books WHERE run_id=? UNION ALL "
            "SELECT MAX(received_at) value FROM scalp_trades WHERE run_id=? UNION ALL "
            "SELECT MAX(observed_at) value FROM scalp_selections WHERE run_id=? UNION ALL "
            "SELECT MAX(observed_at) value FROM scalp_settlements WHERE run_id=?)",
            (row["run_id"],) * 4).fetchone()
        result[str(row["run_id"])] = (
            _at(values[0]) if values and values[0] else _at(row["started_at"]))
    return result or {"": now}


def _selection_intervals(
        connection: sqlite3.Connection,
        cutoffs: dict[str, datetime],
) -> Iterable[tuple[sqlite3.Row, datetime, datetime]]:
    """Yield contiguous selected intervals with the first row as entry metadata."""

    rows = list(connection.execute(
        "SELECT * FROM scalp_selections ORDER BY run_id,observed_at,cohort,rank,id"))
    by_run_cohort_time: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    snapshots: dict[tuple[str, str, datetime], dict[str, sqlite3.Row]] = {}
    cohorts_by_run: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        run_id = str(row["run_id"])
        at = _at(row["observed_at"])
        cohort = str(row["cohort"])
        by_run_cohort_time[(run_id, cohort)].append(at)
        cohorts_by_run[run_id].add(cohort)
        snapshots.setdefault((run_id, cohort, at), {})[str(row["market_slug"])] = row

    for run_id, cohorts in cohorts_by_run.items():
        for cohort in sorted(cohorts):
            times = sorted(set(by_run_cohort_time[(run_id, cohort)]))
            active: dict[str, tuple[sqlite3.Row, datetime]] = {}
            for at in times:
                current = snapshots.get((run_id, cohort, at), {})
                for slug in tuple(active):
                    if slug not in current:
                        first, started = active.pop(slug)
                        yield first, started, at
                for slug, row in current.items():
                    active.setdefault(slug, (row, at))
            for first, started in active.values():
                yield first, started, cutoffs[run_id]


def _mid_reversals(mids: list[float]) -> int:
    previous_sign = 0
    reversals = 0
    for before, after in zip(mids, mids[1:]):
        delta = after - before
        sign = 1 if delta > EPSILON else -1 if delta < -EPSILON else 0
        if sign and previous_sign and sign != previous_sign:
            reversals += 1
        if sign:
            previous_sign = sign
    return reversals


def _features(connection: sqlite3.Connection, row: sqlite3.Row,
              book_row: sqlite3.Row, started_at: datetime,
              config: GtcReplayConfig, bid_queue: float,
              offer_queue: float) -> dict[str, float | int | None]:
    since = started_at - timedelta(seconds=config.feature_lookback_seconds)
    books = list(connection.execute(
        "SELECT observed_at,best_bid,best_offer FROM scalp_books "
        "WHERE run_id=? AND market_slug=? AND observed_at>=? AND observed_at<=? "
        "AND best_bid IS NOT NULL AND best_offer IS NOT NULL ORDER BY observed_at,id",
        (row["run_id"], row["market_slug"], since.isoformat(),
         started_at.isoformat())))
    trades = list(connection.execute(
        "SELECT maker_side,qty FROM scalp_trades WHERE run_id=? AND market_slug=? "
        "AND received_at>=? AND received_at<? ORDER BY received_at,id",
        (row["run_id"], row["market_slug"], since.isoformat(),
         started_at.isoformat())))
    mids = [(float(item["best_bid"]) + float(item["best_offer"])) / 2
            for item in books]
    earliest = _at(books[0]["observed_at"]) if books else started_at
    observed = min(float(config.feature_lookback_seconds),
                   max(0.0, (started_at - earliest).total_seconds()))
    bid_flow = sum(float(item["qty"]) for item in trades
                   if item["maker_side"] == "ORDER_SIDE_BUY")
    offer_flow = sum(float(item["qty"]) for item in trades
                     if item["maker_side"] == "ORDER_SIDE_SELL")
    total_flow = bid_flow + offer_flow
    balance = (2 * min(bid_flow, offer_flow) / total_flow
               if total_flow > EPSILON else 0.0)
    bottleneck = min(
        bid_flow / max(EPSILON, bid_queue + config.quantity),
        offer_flow / max(EPSILON, offer_queue + config.quantity),
    )
    score = (bottleneck
             if observed + EPSILON >= config.minimum_feature_coverage_seconds
             else None)
    mid_range = max(mids) - min(mids) if mids else 0.0
    initial_depth_total = float(book_row["bid_depth"] + book_row["offer_depth"])
    return {
        "lookback_observed_seconds": observed,
        "trade_qty_total": total_flow,
        "bid_consuming_qty": bid_flow,
        "offer_consuming_qty": offer_flow,
        "two_way_flow_balance": balance,
        "mid_range": mid_range,
        "mid_range_in_initial_spreads": (
            mid_range / max(EPSILON, float(book_row["spread"]))),
        "mid_reversals": _mid_reversals(mids),
        "initial_bid_depth": float(book_row["bid_depth"]),
        "initial_offer_depth": float(book_row["offer_depth"]),
        "initial_depth_balance": (
            2 * min(float(book_row["bid_depth"]),
                    float(book_row["offer_depth"])) / initial_depth_total
            if initial_depth_total > EPSILON else 0.0),
        "initial_shares_traded": (
            float(book_row["shares_traded"])
            if book_row["shares_traded"] is not None else None),
        "two_side_queue_clearance_score": score,
    }


def _has_gap(connection: sqlite3.Connection, run_id: str, slug: str,
             started_at: datetime, ended_at: datetime) -> bool:
    run = connection.execute(
        "SELECT coverage_gaps FROM scalp_runs WHERE run_id=?", (run_id,)).fetchone()
    if run is not None and int(run[0]) > 0:
        return True
    sessions = list(connection.execute(
        "SELECT started_at,ended_at,scope_json,status FROM scalp_stream_sessions "
        "WHERE run_id=? ORDER BY started_at,id", (run_id,)))
    for row in sessions:
        scope = set(json.loads(row["scope_json"]))
        gap_start = _at(row["started_at"])
        gap_end = _at(row["ended_at"]) if row["ended_at"] else ended_at
        overlaps = gap_start <= ended_at and gap_end >= started_at
        if overlaps and (slug not in scope
                         or row["status"] in {"ERROR", "INTERRUPTED"}):
            return True
    return False


def _build_replays(connection: sqlite3.Connection,
                   config: GtcReplayConfig) -> list[_Replay]:
    cutoffs = _run_cutoffs(connection)
    created: set[tuple[str, str, str]] = set()
    result: list[_Replay] = []
    for selection, interval_start, interval_end in sorted(
            _selection_intervals(connection, cutoffs),
            key=lambda item: (item[1], item[0]["run_id"], item[0]["cohort"],
                              item[0]["market_slug"])):
        key = (str(selection["run_id"]), str(selection["cohort"]),
               str(selection["market_slug"]))
        if key[1] not in config.cohorts:
            continue
        if key in created:
            continue
        book_row = connection.execute(
            "SELECT * FROM scalp_books WHERE run_id=? AND market_slug=? "
            "AND observed_at>=? AND observed_at<? AND best_bid IS NOT NULL "
            "AND best_offer IS NOT NULL AND spread>=? "
            "AND (state IS NULL OR state='MARKET_STATE_OPEN') "
            "ORDER BY observed_at,id LIMIT 1",
            (selection["run_id"], selection["market_slug"],
             interval_start.isoformat(), interval_end.isoformat(),
             config.minimum_spread - EPSILON)).fetchone()
        if book_row is None:
            continue
        started_at = _at(book_row["observed_at"])
        initial_book = _book(book_row)
        bid_price = float(book_row["best_bid"])
        offer_price = float(book_row["best_offer"])
        bid_queue = _queue_at(initial_book, "bid", bid_price)
        offer_queue = _queue_at(initial_book, "offer", offer_price)
        settlement = connection.execute(
            "SELECT observed_at,settlement_price FROM scalp_settlements "
            "WHERE run_id=? AND market_slug=?",
            (selection["run_id"], selection["market_slug"])).fetchone()
        end_at = (_at(settlement["observed_at"])
                  if settlement is not None else cutoffs[key[0]])
        replay = _Replay(
            run_id=key[0], slug=key[2], cohort=key[1], started_at=started_at,
            selection_rank=int(selection["rank"]),
            rank_metric=selection["rank_metric"],
            rank_value=(float(selection["rank_value"])
                        if selection["rank_value"] is not None else None),
            quantity=config.quantity, bid_price=bid_price, offer_price=offer_price,
            bid_queue=bid_queue, offer_queue=offer_queue,
            features=_features(connection, selection, book_row, started_at, config,
                               bid_queue, offer_queue),
            coverage_clean=True,
            settlement_at=(_at(settlement["observed_at"])
                           if settlement is not None else None),
            settlement_price=(float(settlement["settlement_price"])
                              if settlement is not None else None),
            data_cutoff=cutoffs[key[0]], fills=[])
        replay.coverage_clean = not _has_gap(
            connection, replay.run_id, replay.slug, replay.started_at, end_at)
        bid_queue_left = bid_queue
        offer_queue_left = offer_queue
        for trade_row in connection.execute(
                "SELECT * FROM scalp_trades WHERE run_id=? AND market_slug=? "
                "AND received_at>? AND received_at<=? ORDER BY received_at,id",
                (replay.run_id, replay.slug, replay.started_at.isoformat(),
                 end_at.isoformat())):
            at = _at(trade_row["received_at"])
            maker_side = str(trade_row["maker_side"])
            side = "bid" if maker_side == "ORDER_SIDE_BUY" else "offer"
            remaining = (replay.quantity - replay.bid_filled if side == "bid"
                         else replay.quantity - replay.offer_filled)
            quote_price = replay.bid_price if side == "bid" else replay.offer_price
            queue = bid_queue_left if side == "bid" else offer_queue_left
            model_trade = TradePrint(
                slug=replay.slug, price=float(trade_row["price"]),
                quantity=float(trade_row["qty"]), maker_side=maker_side,
                maker_intent=trade_row["maker_intent"],
                taker_side=trade_row["taker_side"],
                taker_intent=trade_row["taker_intent"],
                trade_time=_at(trade_row["trade_time"]),
                request_id=trade_row["request_id"])
            fill, queue_after, _signal = ScalpMeasurementEngine._consume(
                side, quote_price, queue, remaining, model_trade)
            if side == "bid":
                bid_queue_left = queue_after
            else:
                offer_queue_left = queue_after
            if fill <= EPSILON:
                continue
            economic_price = quote_price if side == "bid" else 1.0 - quote_price
            rebate = maker_rebate(economic_price, fill, config.maker_theta)
            if side == "bid":
                replay.bid_filled += fill
            else:
                replay.offer_filled += fill
            replay.rebates += rebate
            replay.fills.append((at, side, fill, rebate))
            replay.first_fill_at = replay.first_fill_at or at
            if replay.both_filled:
                replay.completed_at = at
                break
        created.add(key)
        result.append(replay)
    return result


def _settlement_pnl(replay: _Replay, bid_qty: float | None = None,
                    offer_qty: float | None = None,
                    rebates: float | None = None) -> float:
    if replay.settlement_price is None:
        raise ValueError("settlement P&L requires settlement")
    bid = replay.bid_filled if bid_qty is None else bid_qty
    offer = replay.offer_filled if offer_qty is None else offer_qty
    fee_credit = replay.rebates if rebates is None else rebates
    paired = min(bid, offer)
    return (
        paired * replay.spread
        + max(0.0, bid - paired) * (replay.settlement_price - replay.bid_price)
        + max(0.0, offer - paired) * (replay.offer_price - replay.settlement_price)
        + fee_credit)


def _gtc_result(replay: _Replay) -> tuple[str, float | None]:
    if replay.both_filled:
        return "BOTH_FILLED", replay.quantity * replay.spread + replay.rebates
    if replay.settlement_price is None:
        return "PENDING", None
    if replay.bid_filled <= EPSILON and replay.offer_filled <= EPSILON:
        return "NO_FILL_SETTLED", 0.0
    return "ONE_SIDE_OR_PARTIAL_SETTLED", _settlement_pnl(replay)


def _state_at(replay: _Replay, due: datetime) -> tuple[float, float, float]:
    bid = offer = rebates = 0.0
    for at, side, qty, rebate in replay.fills or ():
        if at > due:
            break
        if side == "bid":
            bid += qty
        else:
            offer += qty
        rebates += rebate
    return bid, offer, rebates


def _hour_result(connection: sqlite3.Connection, replay: _Replay,
                 config: GtcReplayConfig) -> tuple[str, float | None]:
    if replay.first_fill_at is None:
        return _gtc_result(replay)
    due = replay.first_fill_at + timedelta(seconds=config.early_exit_seconds)
    if replay.completed_at is not None and replay.completed_at <= due:
        return "BOTH_FILLED", replay.quantity * replay.spread + replay.rebates
    if replay.settlement_at is not None and replay.settlement_at <= due:
        return "SETTLED_BEFORE_EXIT", _settlement_pnl(replay)
    if replay.data_cutoff is None or due > replay.data_cutoff:
        return "PENDING", None
    bid, offer, rebates = _state_at(replay, due)
    book_row = connection.execute(
        "SELECT * FROM scalp_books WHERE run_id=? AND market_slug=? "
        "AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1",
        (replay.run_id, replay.slug, due.isoformat())).fetchone()
    if book_row is None:
        return "MISSING_FRESH_EXIT_BOOK", None
    age = (due - _at(book_row["observed_at"])).total_seconds()
    if age < -EPSILON or age > config.maximum_book_age_seconds:
        return "MISSING_FRESH_EXIT_BOOK", None
    episode = {
        "bid_price": replay.bid_price,
        "offer_price": replay.offer_price,
    }
    complete, pnl = _position_pnl(
        episode, bid, offer, rebates, _book(book_row),
        depth_haircut=config.depth_haircut, taker_theta=config.taker_theta)
    return ("FORCED_EXIT" if complete else "INCOMPLETE_FORCED_EXIT",
            pnl if complete else None)


def _summary(rows: list[tuple[_Replay, str, float | None]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[_Replay, str, float | None]]] = defaultdict(list)
    for row in rows:
        grouped[row[0].cohort].append(row)
    output = []
    for cohort, values in sorted(grouped.items()):
        strict = [item for item in values if item[0].coverage_clean]
        resolved = [item for item in strict if item[2] is not None]
        profits = [float(item[2]) for item in resolved]
        pair = [item for item in resolved if item[1] == "BOTH_FILLED"]
        second_leg = [
            (item[0].completed_at - item[0].first_fill_at).total_seconds()
            for item in pair if item[0].completed_at and item[0].first_fill_at]
        output.append({
            "cohort": cohort,
            "orders_started": len(values),
            "coverage_clean_orders": len(strict),
            "coverage_gaps_excluded": len(values) - len(strict),
            "resolved": len(resolved),
            "pending_or_unavailable": len(strict) - len(resolved),
            "both_filled": len(pair),
            "both_fill_rate": len(pair) / len(resolved) if resolved else None,
            "one_side_or_partial": sum(
                item[1] in {"ONE_SIDE_OR_PARTIAL_SETTLED", "FORCED_EXIT",
                            "SETTLED_BEFORE_EXIT"} for item in resolved),
            "no_fill": sum(item[1] == "NO_FILL_SETTLED" for item in resolved),
            "net_pnl_usd": sum(profits),
            "mean_pnl_usd": sum(profits) / len(profits) if profits else None,
            "median_pnl_usd": median(profits) if profits else None,
            "worst_pnl_usd": min(profits) if profits else None,
            "median_second_leg_seconds": median(second_leg) if second_leg else None,
        })
    return output


def _bucket_summary(replays: list[_Replay]) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str, str], list[tuple[_Replay, float]]] = defaultdict(list)
    for replay in replays:
        if not replay.coverage_clean:
            continue
        _status, pnl = _gtc_result(replay)
        if pnl is None:
            continue
        spread = f"{replay.spread:.3f}"
        reversals = int(replay.features["mid_reversals"] or 0)
        score = replay.features["two_side_queue_clearance_score"]
        enough_history = score is not None
        reversal_bucket = ("unavailable" if not enough_history else
                           "0" if reversals == 0 else
                           "1" if reversals == 1 else
                           "2-3" if reversals <= 3 else "4+")
        volume = replay.features["initial_shares_traded"]
        if volume is None:
            volume_bucket = "unavailable"
        elif float(volume) <= 0:
            volume_bucket = "0"
        else:
            exponent = math.floor(math.log10(float(volume)))
            volume_bucket = f"1e{exponent}-1e{exponent + 1}"
        if score is None:
            score_bucket = "unavailable"
        elif float(score) < 0.25:
            score_bucket = "<0.25"
        elif float(score) < 0.5:
            score_bucket = "0.25-0.50"
        elif float(score) < 1:
            score_bucket = "0.50-1.00"
        else:
            score_bucket = ">=1.00"
        balance = float(replay.features["two_way_flow_balance"] or 0)
        if not enough_history:
            balance_bucket = "unavailable"
        elif balance < 0.25:
            balance_bucket = "<0.25"
        elif balance < 0.5:
            balance_bucket = "0.25-0.50"
        elif balance < 0.75:
            balance_bucket = "0.50-0.75"
        else:
            balance_bucket = ">=0.75"
        range_spreads = float(
            replay.features["mid_range_in_initial_spreads"] or 0)
        if not enough_history:
            range_bucket = "unavailable"
        elif range_spreads <= EPSILON:
            range_bucket = "0"
        elif range_spreads < 1:
            range_bucket = "0-1x"
        elif range_spreads < 2:
            range_bucket = "1-2x"
        else:
            range_bucket = ">=2x"
        for feature, bucket in (("initial_spread", spread),
                                ("initial_shares_traded", volume_bucket),
                                ("preplacement_two_way_flow_balance",
                                 balance_bucket),
                                ("preplacement_mid_range_in_spreads",
                                 range_bucket),
                                ("preplacement_mid_reversals", reversal_bucket),
                                ("two_side_queue_clearance_score", score_bucket)):
            rows[(replay.cohort, feature, bucket)].append((replay, pnl))
    output = []
    for (cohort, feature, bucket), values in sorted(rows.items()):
        pair = sum(replay.both_filled for replay, _pnl in values)
        pnls = [pnl for _replay, pnl in values]
        output.append({
            "cohort": cohort, "feature": feature, "bucket": bucket,
            "resolved": len(values), "both_filled": pair,
            "both_fill_rate": pair / len(values),
            "net_pnl_usd": sum(pnls),
            "mean_pnl_usd": sum(pnls) / len(pnls),
        })
    return output


def _capital_summary(replays: list[_Replay]) -> list[dict[str, Any]]:
    by_cohort: dict[str, list[_Replay]] = defaultdict(list)
    for replay in replays:
        if replay.coverage_clean:
            by_cohort[replay.cohort].append(replay)
    output = []
    for cohort, rows in sorted(by_cohort.items()):
        events: list[tuple[datetime, int, float]] = []
        capital_hours = 0.0
        for replay in rows:
            capital = replay.quantity * (replay.bid_price + 1.0 - replay.offer_price)
            seconds = max(0.0, (replay.end_at - replay.started_at).total_seconds())
            capital_hours += capital * seconds / 3600
            if seconds <= EPSILON:
                continue
            events.append((replay.started_at, 1, capital))
            events.append((replay.end_at, -1, -capital))
        active = peak_active = 0
        capital = peak_capital = 0.0
        for _at_value, delta_count, delta_capital in sorted(
                events, key=lambda item: (item[0], item[1])):
            active += delta_count
            capital += delta_capital
            peak_active = max(peak_active, active)
            peak_capital = max(peak_capital, capital)
        output.append({
            "cohort": cohort,
            "observed_capital_hours_usd": capital_hours,
            "maximum_simultaneous_markets": peak_active,
            "maximum_simultaneous_committed_capital_usd": peak_capital,
        })
    return output


def _gtc_rebate_summary(replays: list[_Replay]) -> list[dict[str, Any]]:
    by_cohort: dict[str, list[_Replay]] = defaultdict(list)
    for replay in replays:
        if replay.coverage_clean and replay.resolved:
            by_cohort[replay.cohort].append(replay)
    return [{
        "cohort": cohort,
        "resolved_orders": len(rows),
        "maker_rebates_included_in_net_usd": sum(row.rebates for row in rows),
        "taker_fees_in_gtc_to_settlement_usd": 0.0,
    } for cohort, rows in sorted(by_cohort.items())]


def build_gtc_scalp_report(path: Path, *,
                           config: GtcReplayConfig | None = None) -> dict[str, Any]:
    """Build the exact leave-it replay without writing the live database."""

    policy = config or GtcReplayConfig()
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        replays = _build_replays(connection, policy)
        gtc_rows = [(row, *_gtc_result(row)) for row in replays]
        hour_rows = [(row, *_hour_result(connection, row, policy)) for row in replays]
        feature_available = sum(
            row.features["two_side_queue_clearance_score"] is not None
            for row in replays)
        rolling = build_scalp_report(path, (policy.early_exit_seconds,))
        rolling_exit = {
            row["cohort"]: row for row in rolling["early_exit_replays"]
            if row["horizon_seconds"] == policy.early_exit_seconds}
        rolling_rows = []
        for row in rolling["cohorts"]:
            exit_row = rolling_exit.get(row["cohort"], {})
            rolling_rows.append({
                "cohort": row["cohort"],
                "renewal_seconds_without_fill": 300,
                "started_two_sided_episodes": row["started_two_sided_episodes"],
                "resolved_episodes": row["resolved_episodes"],
                "both_sides_filled": row["both_sides_filled"],
                "both_side_fill_rate": row["both_side_fill_rate"],
                "one_side_or_partial": row["one_side_or_partial"],
                "no_fill": row["no_fill"],
                "early_exit_samples": exit_row.get("samples", 0),
                "early_exit_complete": exit_row.get("complete_exits", 0),
                "early_exit_missing_book": exit_row.get("missing_fresh_book", 0),
                "early_exit_pending": exit_row.get("pending_horizon", 0),
                "early_exit_net_pnl_usd": exit_row.get("net_pnl_usd", 0),
            })
        return {
            "database": str(path.resolve()),
            "policy": {
                "quantity": policy.quantity,
                "minimum_spread": policy.minimum_spread,
                "fixed_original_prices": True,
                "fixed_initial_back_of_queue": True,
                "cancel_or_reprice_before_terminal": False,
                "feature_lookback_seconds": policy.feature_lookback_seconds,
                "early_exit_seconds_after_first_fill": policy.early_exit_seconds,
                "maker_theta": policy.maker_theta,
                "forced_exit_taker_theta": policy.taker_theta,
                "forced_exit_depth_haircut": policy.depth_haircut,
                "cohorts": list(policy.cohorts),
            },
            "fixed_gtc_to_settlement": _summary(gtc_rows),
            "fixed_gtc_with_early_exit": _summary(hour_rows),
            "existing_five_minute_renewal_with_early_exit": rolling_rows,
            "by_preplacement_feature": _bucket_summary(replays),
            "capital": _capital_summary(replays),
            "fixed_gtc_fees_and_rebates": _gtc_rebate_summary(replays),
            "feature_coverage": {
                "orders_started": len(replays),
                "score_available": feature_available,
                "score_unavailable": len(replays) - feature_available,
                "rule": (
                    "Score is the smaller of recent bid- and offer-consuming "
                    "volume divided by that side's initial queue plus requested "
                    "quantity; it uses only rows at or before placement."),
            },
            "interpretation": (
                "Offline counterfactual only. One fixed pair is started per "
                "run/market/cohort while selected; it is never renewed, canceled, "
                "or repriced. Coverage-gap paths are excluded from strict results. "
                "Cohorts overlap and must never be added together. A higher both-"
                "fill probability is useful only when its settlement/exit net, "
                "capital-hours, and held-out days are also favorable."),
        }
    finally:
        connection.close()
