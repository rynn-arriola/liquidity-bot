"""Durable, isolated storage for two-sided spread-scalp measurements."""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import BookSnapshot, MarketMetadata, TradePrint


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS scalp_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    config_json TEXT NOT NULL,
    reconnects INTEGER NOT NULL DEFAULT 0,
    coverage_gaps INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scalp_markets (
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    question TEXT,
    game_start TEXT,
    tick_size REAL,
    minimum_trade_qty REAL,
    sports_market_type TEXT,
    sports_market_type_v2 TEXT,
    catalog_volume_usd REAL,
    catalog_volume_24h_usd REAL,
    catalog_liquidity_usd REAL,
    PRIMARY KEY (run_id, market_slug)
);
CREATE TABLE IF NOT EXISTS scalp_selections (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    cohort TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    rank INTEGER NOT NULL,
    catalog_volume_usd REAL,
    rank_metric TEXT,
    rank_value REAL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scalp_selections_run_at
    ON scalp_selections(run_id, observed_at, cohort);
CREATE TABLE IF NOT EXISTS scalp_books (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    exchange_time TEXT,
    state TEXT,
    best_bid REAL,
    best_offer REAL,
    spread REAL,
    bid_depth REAL NOT NULL,
    offer_depth REAL NOT NULL,
    shares_traded REAL,
    open_interest REAL,
    notional_traded_usd REAL,
    bids_zlib BLOB NOT NULL,
    offers_zlib BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scalp_books_run_slug_at
    ON scalp_books(run_id, market_slug, observed_at);
CREATE TABLE IF NOT EXISTS scalp_trades (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    maker_side TEXT NOT NULL,
    maker_intent TEXT,
    taker_side TEXT,
    taker_intent TEXT,
    request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_scalp_trades_run_slug_at
    ON scalp_trades(run_id, market_slug, received_at);
CREATE TABLE IF NOT EXISTS scalp_episodes (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    cohort TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    requested_qty REAL NOT NULL,
    bid_price REAL NOT NULL,
    offer_price REAL NOT NULL,
    initial_spread REAL NOT NULL,
    initial_bid_queue REAL NOT NULL,
    initial_offer_queue REAL NOT NULL,
    bid_queue_remaining REAL NOT NULL,
    offer_queue_remaining REAL NOT NULL,
    bid_filled_qty REAL NOT NULL DEFAULT 0,
    offer_filled_qty REAL NOT NULL DEFAULT 0,
    bid_rebate_usd REAL NOT NULL DEFAULT 0,
    offer_rebate_usd REAL NOT NULL DEFAULT 0,
    first_fill_at TEXT,
    completed_at TEXT,
    gross_pair_pnl_usd REAL,
    estimated_rebate_usd REAL,
    estimated_net_pair_pnl_usd REAL,
    close_reason TEXT,
    UNIQUE (run_id, market_slug, cohort, started_at)
);
CREATE INDEX IF NOT EXISTS idx_scalp_episodes_run_cohort_status
    ON scalp_episodes(run_id, cohort, status, started_at);
CREATE TABLE IF NOT EXISTS scalp_episode_fills (
    id INTEGER PRIMARY KEY,
    episode_id INTEGER NOT NULL,
    signaled_at TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    signal_kind TEXT NOT NULL,
    queue_before REAL NOT NULL,
    maker_rebate_usd REAL NOT NULL,
    source_trade_id INTEGER,
    FOREIGN KEY(episode_id) REFERENCES scalp_episodes(id),
    FOREIGN KEY(source_trade_id) REFERENCES scalp_trades(id)
);
CREATE INDEX IF NOT EXISTS idx_scalp_episode_fills_episode
    ON scalp_episode_fills(episode_id, signaled_at);
CREATE TABLE IF NOT EXISTS scalp_settlements (
    run_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    settlement_price REAL NOT NULL,
    PRIMARY KEY (run_id, market_slug)
);
CREATE TABLE IF NOT EXISTS scalp_stream_sessions (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ScalpStore:
    """One-writer WAL store; callers flush at a bounded interval."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript(SCHEMA)
        selection_columns = {
            row[1] for row in self.connection.execute(
                "PRAGMA table_info(scalp_selections)")}
        for name, kind in (("rank_metric", "TEXT"), ("rank_value", "REAL")):
            if name not in selection_columns:
                self.connection.execute(
                    f"ALTER TABLE scalp_selections ADD COLUMN {name} {kind}")
        self.connection.commit()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def flush(self) -> None:
        self.connection.commit()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def start_run(self, run_id: str, at: datetime, config: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO scalp_runs(run_id,started_at,config_json) VALUES (?,?,?)",
            (run_id, _iso(at), json.dumps(config, sort_keys=True)))
        self.flush()

    def latest_unfinished_run(self) -> str | None:
        row = self.connection.execute(
            "SELECT run_id FROM scalp_runs WHERE ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        return str(row[0]) if row is not None else None

    def resume_run(self, run_id: str, at: datetime) -> None:
        """Close the crashed socket interval while preserving open episodes."""

        self.connection.execute(
            "UPDATE scalp_stream_sessions SET ended_at=?,status='INTERRUPTED',"
            "error='collector process ended without closing this stream' "
            "WHERE run_id=? AND ended_at IS NULL",
            (_iso(at), run_id))
        self.flush()

    def finish_run(self, run_id: str, at: datetime, *, reconnects: int,
                   coverage_gaps: int) -> None:
        self.connection.execute(
            "UPDATE scalp_runs SET ended_at=?,reconnects=?,coverage_gaps=? "
            "WHERE run_id=?",
            (_iso(at), reconnects, coverage_gaps, run_id))
        self.flush()

    def update_run_health(self, run_id: str, *, reconnects: int,
                          coverage_gaps: int) -> None:
        """Persist active-run coverage counters before a read-only report."""

        self.connection.execute(
            "UPDATE scalp_runs SET reconnects=?,coverage_gaps=? WHERE run_id=?",
            (reconnects, coverage_gaps, run_id))

    def upsert_market(self, run_id: str, market: MarketMetadata, at: datetime) -> None:
        self.connection.execute(
            "INSERT INTO scalp_markets(run_id,market_slug,first_seen_at,last_seen_at,"
            "question,game_start,tick_size,minimum_trade_qty,sports_market_type,"
            "sports_market_type_v2,catalog_volume_usd,catalog_volume_24h_usd,"
            "catalog_liquidity_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id,market_slug) DO UPDATE SET "
            "last_seen_at=excluded.last_seen_at,question=excluded.question,"
            "game_start=excluded.game_start,tick_size=excluded.tick_size,"
            "minimum_trade_qty=excluded.minimum_trade_qty,"
            "sports_market_type=excluded.sports_market_type,"
            "sports_market_type_v2=excluded.sports_market_type_v2,"
            "catalog_volume_usd=excluded.catalog_volume_usd,"
            "catalog_volume_24h_usd=excluded.catalog_volume_24h_usd,"
            "catalog_liquidity_usd=excluded.catalog_liquidity_usd",
            (run_id, market.slug, _iso(at), _iso(at), market.question,
             _iso(market.game_start), market.tick_size, market.minimum_trade_qty,
             market.sports_market_type, market.sports_market_type_v2,
             market.catalog_volume_usd, market.catalog_volume_24h_usd,
             market.catalog_liquidity_usd))

    def record_selection(self, run_id: str, cohort: str, markets: Iterable[MarketMetadata],
                         at: datetime, *, source: str,
                         rank_metric: str | None = None,
                         rank_values: dict[str, float] | None = None) -> None:
        values = rank_values or {}
        for rank, market in enumerate(markets, 1):
            self.upsert_market(run_id, market, at)
            self.connection.execute(
                "INSERT INTO scalp_selections(run_id,observed_at,cohort,market_slug,"
                "rank,catalog_volume_usd,rank_metric,rank_value,source) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, _iso(at), cohort, market.slug, rank,
                 market.catalog_volume_usd, rank_metric, values.get(market.slug), source))

    def record_book(self, run_id: str, book: BookSnapshot, at: datetime) -> int:
        best_bid = book.best_bid.price if book.bids else None
        best_offer = book.best_offer.price if book.offers else None
        spread = (best_offer - best_bid
                  if best_bid is not None and best_offer is not None else None)
        cursor = self.connection.execute(
            "INSERT INTO scalp_books(run_id,market_slug,observed_at,exchange_time,"
            "state,best_bid,best_offer,spread,bid_depth,offer_depth,shares_traded,"
            "open_interest,notional_traded_usd,bids_zlib,offers_zlib) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, book.slug, _iso(at), _iso(book.transact_time), book.state,
             best_bid, best_offer, spread, book.bid_depth, book.offer_depth,
             book.shares_traded, book.open_interest, book.notional_traded_usd,
             zlib.compress(json.dumps(
                 book.levels("bid"), separators=(",", ":")).encode(), 6),
             zlib.compress(json.dumps(
                 book.levels("offer"), separators=(",", ":")).encode(), 6)))
        return int(cursor.lastrowid)

    def record_trade(self, run_id: str, trade: TradePrint, at: datetime) -> int:
        cursor = self.connection.execute(
            "INSERT INTO scalp_trades(run_id,market_slug,trade_time,received_at,"
            "price,qty,maker_side,maker_intent,taker_side,taker_intent,request_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, trade.slug, _iso(trade.trade_time), _iso(at), trade.price,
             trade.quantity, trade.maker_side, trade.maker_intent,
             trade.taker_side, trade.taker_intent, trade.request_id))
        return int(cursor.lastrowid)

    def start_episode(self, run_id: str, slug: str, cohort: str, at: datetime, *,
                      qty: float, bid_price: float, offer_price: float,
                      bid_queue: float, offer_queue: float) -> int:
        cursor = self.connection.execute(
            "INSERT INTO scalp_episodes(run_id,market_slug,cohort,started_at,status,"
            "requested_qty,bid_price,offer_price,initial_spread,initial_bid_queue,"
            "initial_offer_queue,bid_queue_remaining,offer_queue_remaining) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, slug, cohort, _iso(at), "OPEN", qty, bid_price, offer_price,
             offer_price - bid_price, bid_queue, offer_queue, bid_queue, offer_queue))
        return int(cursor.lastrowid)

    def update_episode(self, episode_id: int, *, bid_queue: float, offer_queue: float,
                       bid_filled: float, offer_filled: float, bid_rebate: float,
                       offer_rebate: float, first_fill_at: datetime | None,
                       completed_at: datetime | None = None) -> None:
        complete = completed_at is not None
        self.connection.execute(
            "UPDATE scalp_episodes SET bid_queue_remaining=?,offer_queue_remaining=?,"
            "bid_filled_qty=?,offer_filled_qty=?,bid_rebate_usd=?,offer_rebate_usd=?,"
            "first_fill_at=?,completed_at=COALESCE(completed_at,?),"
            "ended_at=CASE WHEN ? THEN ? ELSE ended_at END,"
            "status=CASE WHEN ? THEN 'BOTH_FILLED' ELSE status END,"
            "gross_pair_pnl_usd=CASE WHEN ? THEN requested_qty*(offer_price-bid_price) "
            "ELSE gross_pair_pnl_usd END,"
            "estimated_rebate_usd=CASE WHEN ? THEN ? ELSE estimated_rebate_usd END,"
            "estimated_net_pair_pnl_usd=CASE WHEN ? THEN "
            "requested_qty*(offer_price-bid_price)+? ELSE estimated_net_pair_pnl_usd END,"
            "close_reason=CASE WHEN ? THEN 'BOTH_FILLED' ELSE close_reason END "
            "WHERE id=?",
            (bid_queue, offer_queue, bid_filled, offer_filled, bid_rebate,
             offer_rebate, _iso(first_fill_at), _iso(completed_at), int(complete),
             _iso(completed_at), int(complete), int(complete), int(complete),
             bid_rebate + offer_rebate, int(complete), bid_rebate + offer_rebate,
             int(complete), episode_id))

    def record_episode_fill(self, episode_id: int, at: datetime, side: str,
                            price: float, qty: float, signal_kind: str,
                            queue_before: float, maker_rebate: float,
                            source_trade_id: int) -> None:
        self.connection.execute(
            "INSERT INTO scalp_episode_fills(episode_id,signaled_at,side,price,qty,"
            "signal_kind,queue_before,maker_rebate_usd,source_trade_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (episode_id, _iso(at), side, price, qty, signal_kind, queue_before,
             maker_rebate, source_trade_id))

    def close_episode(self, episode_id: int, at: datetime, reason: str) -> None:
        self.connection.execute(
            "UPDATE scalp_episodes SET ended_at=?,status=?,close_reason=? WHERE id=? "
            "AND status='OPEN'",
            (_iso(at), reason, reason, episode_id))

    def record_settlement(self, run_id: str, slug: str, at: datetime,
                          settlement: float) -> None:
        self.connection.execute(
            "INSERT INTO scalp_settlements(run_id,market_slug,observed_at,"
            "settlement_price) VALUES (?,?,?,?) ON CONFLICT(run_id,market_slug) "
            "DO UPDATE SET observed_at=excluded.observed_at,"
            "settlement_price=excluded.settlement_price",
            (run_id, slug, _iso(at), settlement))

    def start_stream(self, run_id: str, at: datetime, slugs: Iterable[str]) -> int:
        cursor = self.connection.execute(
            "INSERT INTO scalp_stream_sessions(run_id,started_at,scope_json,status) "
            "VALUES (?,?,?,'COLLECTING')",
            (run_id, _iso(at), json.dumps(sorted(slugs), separators=(",", ":"))))
        self.flush()
        return int(cursor.lastrowid)

    def finish_stream(self, session_id: int, at: datetime, status: str,
                      error: str | None = None) -> None:
        self.connection.execute(
            "UPDATE scalp_stream_sessions SET ended_at=?,status=?,error=? WHERE id=?",
            (_iso(at), status, str(error)[:300] if error else None, session_id))
        self.flush()
