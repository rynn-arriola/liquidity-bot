"""SQLite persistence for reproducible shadow observations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import BookSnapshot, MarketMetadata, RewardEarning, TradePrint
from .rewards import IncentivePeriod


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS shadow_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    policy_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_markets (
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    game_start TEXT,
    tick_size REAL,
    active INTEGER NOT NULL,
    closed INTEGER NOT NULL,
    status TEXT,
    sports_market_type TEXT,
    PRIMARY KEY (run_id, market_slug)
);
CREATE TABLE IF NOT EXISTS shadow_incentive_periods (
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    program_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    period TEXT NOT NULL,
    status TEXT NOT NULL,
    reward_pool REAL NOT NULL,
    discount_factor REAL NOT NULL,
    target_size REAL NOT NULL,
    starts_at TEXT,
    ends_at TEXT,
    PRIMARY KEY (run_id, market_slug, program_id, captured_at)
);
CREATE TABLE IF NOT EXISTS shadow_books (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    exchange_time TEXT,
    state TEXT,
    bids_json TEXT NOT NULL,
    offers_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_books_run_slug_at
    ON shadow_books(run_id, market_slug, observed_at);
CREATE TABLE IF NOT EXISTS shadow_quote_observations (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price REAL,
    size REAL NOT NULL,
    candidate INTEGER NOT NULL,
    score REAL NOT NULL,
    ticks_from_best INTEGER,
    cutoff_price REAL,
    target_size REAL,
    queue_ahead REAL NOT NULL,
    reason TEXT NOT NULL,
    bid_depth REAL NOT NULL,
    offer_depth REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_quotes_run_slug_at
    ON shadow_quote_observations(run_id, market_slug, observed_at);
CREATE TABLE IF NOT EXISTS shadow_score_daily (
    run_id TEXT NOT NULL,
    day_et TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    candidate_seconds REAL NOT NULL DEFAULT 0,
    candidate_score_seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, day_et, market_slug, side)
);
CREATE TABLE IF NOT EXISTS shadow_trades (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    maker_side TEXT NOT NULL,
    request_id TEXT
);
CREATE TABLE IF NOT EXISTS shadow_fill_signals (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    role TEXT NOT NULL,
    side TEXT NOT NULL,
    signaled_at TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    signal_kind TEXT NOT NULL,
    queue_before REAL NOT NULL,
    source_request_id TEXT
);
CREATE TABLE IF NOT EXISTS shadow_exit_replays (
    run_id TEXT NOT NULL,
    fill_signal_id INTEGER NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    lag_seconds REAL NOT NULL,
    method TEXT NOT NULL,
    requested_qty REAL NOT NULL,
    closed_qty REAL NOT NULL,
    remaining_qty REAL NOT NULL,
    complete INTEGER NOT NULL,
    average_exit_price REAL,
    taker_fee REAL NOT NULL,
    pnl REAL NOT NULL,
    loss_budget_pass INTEGER NOT NULL,
    PRIMARY KEY (fill_signal_id, horizon_seconds)
);
CREATE TABLE IF NOT EXISTS shadow_earnings (
    date TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    program_type TEXT NOT NULL,
    status TEXT NOT NULL,
    reward REAL NOT NULL,
    captured_at TEXT NOT NULL,
    attributable INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, market_slug, program_type)
);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ShadowStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def start_run(self, run_id: str, started_at: datetime, policy: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO shadow_runs(run_id,started_at,policy_json) VALUES (?,?,?)",
            (run_id, _iso(started_at), json.dumps(policy, sort_keys=True)))
        self.connection.commit()

    def end_run(self, run_id: str, ended_at: datetime) -> None:
        self.connection.execute(
            "UPDATE shadow_runs SET ended_at=? WHERE run_id=?",
            (_iso(ended_at), run_id))
        self.connection.commit()

    def register_market(self, run_id: str, market: MarketMetadata,
                        periods: Iterable[IncentivePeriod], captured_at: datetime) -> None:
        self.connection.execute(
            "INSERT INTO shadow_markets "
            "(run_id,market_slug,registered_at,game_start,tick_size,active,closed,status,sports_market_type) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,market_slug) DO UPDATE SET "
            "game_start=excluded.game_start,tick_size=excluded.tick_size,active=excluded.active,"
            "closed=excluded.closed,status=excluded.status,sports_market_type=excluded.sports_market_type",
            (run_id, market.slug, _iso(captured_at), _iso(market.game_start),
             market.tick_size, int(market.active), int(market.closed), market.status,
             market.sports_market_type))
        for period in periods:
            self.connection.execute(
                "INSERT OR REPLACE INTO shadow_incentive_periods "
                "(run_id,market_slug,program_id,captured_at,period,status,reward_pool,"
                "discount_factor,target_size,starts_at,ends_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, market.slug, period.program_id, _iso(captured_at), period.period,
                 period.status, period.reward_pool, period.discount_factor,
                 period.target_size, _iso(period.starts_at), _iso(period.ends_at)))
        self.connection.commit()

    def record_book(self, run_id: str, book: BookSnapshot, observed_at: datetime) -> None:
        self.connection.execute(
            "INSERT INTO shadow_books "
            "(run_id,market_slug,observed_at,exchange_time,state,bids_json,offers_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, book.slug, _iso(observed_at), _iso(book.transact_time), book.state,
             json.dumps(book.levels("bid")), json.dumps(book.levels("offer"))))
        self.connection.commit()

    def record_quote(self, run_id: str, slug: str, side: str, observed_at: datetime,
                     *, price: float | None, size: float, candidate: bool, score: float,
                     ticks: int | None, cutoff: float | None, target: float | None,
                     queue_ahead: float, reason: str, book: BookSnapshot) -> None:
        self.connection.execute(
            "INSERT INTO shadow_quote_observations "
            "(run_id,market_slug,side,observed_at,price,size,candidate,score,"
            "ticks_from_best,cutoff_price,target_size,queue_ahead,reason,bid_depth,offer_depth) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, slug, side, _iso(observed_at), price, size, int(candidate), score,
             ticks, cutoff, target, queue_ahead, reason, book.bid_depth, book.offer_depth))
        self.connection.commit()

    def add_score(self, run_id: str, day_et: str, slug: str, side: str,
                  seconds: float, score_seconds: float) -> None:
        self.connection.execute(
            "INSERT INTO shadow_score_daily "
            "(run_id,day_et,market_slug,side,candidate_seconds,candidate_score_seconds) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(run_id,day_et,market_slug,side) DO UPDATE SET "
            "candidate_seconds=candidate_seconds+excluded.candidate_seconds,"
            "candidate_score_seconds=candidate_score_seconds+excluded.candidate_score_seconds",
            (run_id, day_et, slug, side, seconds, score_seconds))
        self.connection.commit()

    def record_trade(self, run_id: str, trade: TradePrint, received_at: datetime) -> None:
        self.connection.execute(
            "INSERT INTO shadow_trades "
            "(run_id,market_slug,trade_time,received_at,price,qty,maker_side,request_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, trade.slug, _iso(trade.trade_time), _iso(received_at),
             trade.price, trade.quantity, trade.maker_side, trade.request_id))
        self.connection.commit()

    def record_fill_signal(self, run_id: str, slug: str, role: str, side: str,
                           at: datetime, price: float, qty: float, signal_kind: str,
                           queue_before: float, request_id: str | None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO shadow_fill_signals "
            "(run_id,market_slug,role,side,signaled_at,price,qty,signal_kind,"
            "queue_before,source_request_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, slug, role, side, _iso(at), price, qty, signal_kind,
             queue_before, request_id))
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_exit(self, run_id: str, fill_signal_id: int, horizon: int,
                    evaluated_at: datetime,
                    due_at: datetime, lag_seconds: float, method: str,
                    requested_qty: float, closed_qty: float, remaining_qty: float,
                    complete: bool, average_exit_price: float | None,
                    taker_fee: float, pnl: float, loss_budget_pass: bool) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO shadow_exit_replays "
            "(run_id,fill_signal_id,horizon_seconds,evaluated_at,due_at,lag_seconds,method,"
            "requested_qty,closed_qty,remaining_qty,complete,average_exit_price,"
            "taker_fee,pnl,loss_budget_pass) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, fill_signal_id, horizon, _iso(evaluated_at), _iso(due_at), lag_seconds,
             method, requested_qty, closed_qty, remaining_qty, int(complete),
             average_exit_price, taker_fee, pnl, int(loss_budget_pass)))
        self.connection.commit()

    def record_earnings(self, earnings: Iterable[RewardEarning], *,
                        captured_at: datetime, attributable: bool = False) -> None:
        for row in earnings:
            self.connection.execute(
                "INSERT INTO shadow_earnings "
                "(date,market_slug,program_type,status,reward,captured_at,attributable) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(date,market_slug,program_type) DO UPDATE SET "
                "status=excluded.status,reward=excluded.reward,captured_at=excluded.captured_at,"
                "attributable=MAX(shadow_earnings.attributable,excluded.attributable)",
                (row.date, row.market_slug, row.program_type, row.status, row.reward,
                 _iso(captured_at), int(attributable)))
        self.connection.commit()
