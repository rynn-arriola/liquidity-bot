"""Isolated market-data collector for strict two-sided scalp measurement.

The process uses catalog reads plus one market WebSocket. It imports no order,
portfolio, balance, incentive, or execution-ledger writer and cannot mutate the
live bot. The production ledger is opened read-only only to mirror its current
20-market queue scope into the reward-market cohort.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from .errors import AdapterError
from .models import BookSnapshot, MarketMetadata, TradePrint
from .probe import client_from_environment
from .runtime_safety import LeaseHeld, SingleInstanceLease
from .scalp_engine import ScalpMeasurementEngine, ScalpPolicy
from .scalp_store import ScalpStore
from .shadow_run import is_full_game_winner, is_two_way_slug
from .streams import ShadowMarketStream


def read_reward_scope(path: Path) -> tuple[str, ...]:
    """Read the newest actively collected 20-slot scope without a writer lock."""

    if not path.exists():
        return ()
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT scope_json,scope_size FROM execution_queue_stream_sessions "
            "WHERE status='COLLECTING' AND ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return ()
    finally:
        connection.close()
    if row is None:
        return ()
    try:
        values = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return ()
    clean = tuple(dict.fromkeys(str(value).strip() for value in values
                                if str(value).strip()))
    return clean if len(clean) == int(row[1]) and len(clean) <= 20 else ()


def eligible_scalp_markets(markets: list[MarketMetadata], *, now: datetime,
                           horizon: datetime) -> list[MarketMetadata]:
    candidates = [
        market for market in markets
        if market.active and not market.closed
        and market.game_start is not None and now < market.game_start <= horizon
        and is_full_game_winner(market) and is_two_way_slug(market.slug)
    ]
    return sorted(candidates, key=lambda market: (market.game_start, market.slug))


def _book_volume(book: BookSnapshot) -> tuple[float, str] | None:
    if book.shares_traded is not None:
        return book.shares_traded, "book.sharesTraded"
    if book.notional_traded_usd is not None:
        return book.notional_traded_usd, "book.notionalTraded"
    return None


def rank_observed_volume(markets: list[MarketMetadata],
                         books: dict[str, BookSnapshot], *,
                         limit: int) -> list[MarketMetadata]:
    shares = []
    notionals = []
    for market in markets:
        book = books.get(market.slug)
        if book is None:
            continue
        if book.shares_traded is not None:
            shares.append((book.shares_traded, market))
        elif book.notional_traded_usd is not None:
            notionals.append((book.notional_traded_usd, market))
    # Never compare contract counts with dollar notionals.  Retail currently
    # supplies sharesTraded; notionalTraded is a homogeneous fallback only
    # when no candidate exposes the shares field.
    measured = shares if shares else notionals
    measured.sort(key=lambda item: (-item[0], item[1].game_start, item[1].slug))
    return [market for _value, market in measured[:limit]]


@dataclass(frozen=True)
class ScalpRunConfig:
    database: Path
    live_database: Path
    minutes: float
    pure_markets: int
    horizon_minutes: int
    refresh_minutes: float
    rest_requests_per_second: float
    policy: ScalpPolicy


class ScalpRunner:
    def __init__(self, client: Any, config: ScalpRunConfig):
        self.client = client
        self.config = config
        self.gateway = AsyncReadOnlyGateway(
            client,
            limiter=AsyncIntervalLimiter.from_rate(config.rest_requests_per_second),
        )
        self.store = ScalpStore(config.database)
        unfinished = self.store.latest_unfinished_run()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = unfinished or f"scalp-{stamp}-{uuid.uuid4().hex[:8]}"
        self.resumed = unfinished is not None
        self.engine = ScalpMeasurementEngine(self.run_id, self.store, config.policy)
        if self.resumed:
            self.engine.restore_open()
        self.catalog: dict[str, MarketMetadata] = {}
        self.candidates: list[MarketMetadata] = []
        self.latest_books: dict[str, BookSnapshot] = {}
        self.latest_book_at: dict[str, datetime] = {}
        self.reward_slugs: tuple[str, ...] = ()
        self.pure_slugs: set[str] = set()
        self.discovery_offset = 0
        self.settlement_offset = 0
        self.scope: tuple[str, ...] = ()
        self.reconnects = 0
        self.coverage_gaps = 0

    def _apply_cohorts(self, at: datetime) -> None:
        self.engine.set_cohorts({
            "PURE_VOLUME": set(self.pure_slugs),
            "REWARD_SLOTS": set(self.reward_slugs),
        }, at=at)

    def _rank_pure(self, at: datetime) -> None:
        fresh_books = {
            slug: book for slug, book in self.latest_books.items()
            if slug in self.latest_book_at
            and (at - self.latest_book_at[slug]).total_seconds() <= 180
        }
        pure = rank_observed_volume(
            [market for market in self.candidates if market.slug in self.scope],
            fresh_books, limit=self.config.pure_markets)
        self.pure_slugs = {market.slug for market in pure}
        values = {
            market.slug: _book_volume(fresh_books[market.slug])[0]
            for market in pure
        }
        metrics = {
            _book_volume(fresh_books[market.slug])[1] for market in pure
        }
        self.store.record_selection(
            self.run_id, "PURE_VOLUME", pure, at,
            source="observed_full_book_stats",
            rank_metric=(next(iter(metrics)) if len(metrics) == 1
                         else "mixed_book_volume" if metrics else None),
            rank_values=values)
        self._apply_cohorts(at)

    async def _selection(self, at: datetime) -> tuple[str, ...]:
        horizon = at + timedelta(minutes=self.config.horizon_minutes)
        catalog = list(await self.gateway.list_upcoming_sports_markets(
            start=at, end=horizon, limit=500))
        self.catalog = {market.slug: market for market in catalog}
        self.candidates = eligible_scalp_markets(
            catalog, now=at, horizon=horizon)
        reward_slugs = read_reward_scope(self.config.live_database)
        missing = [slug for slug in reward_slugs if slug not in self.catalog]
        if missing:
            try:
                self.catalog.update(await self.gateway.fetch_markets_by_slugs(missing))
            except AdapterError:
                self.coverage_gaps += len(missing)
        reward: list[MarketMetadata] = []
        for slug in reward_slugs:
            market = self.catalog.get(slug)
            if market is None:
                self.coverage_gaps += 1
                continue
            reward.append(market)
        self.reward_slugs = tuple(market.slug for market in reward)
        self._rank_pure(at)

        # Observe twice the requested pure cohort (up to the socket's combined
        # 100-market ceiling) so volume ranking has contemporaneous controls.
        # On each refresh, retain current high-volume markets and rotate the
        # unused discovery capacity through the rest of the eligible universe.
        fixed = set(self.reward_slugs) | self.pure_slugs | self.engine.required_slugs
        capacity = max(0, 100 - len(fixed))
        discovery_target = min(
            capacity, max(self.config.pure_markets * 2, self.config.pure_markets + 20))
        remaining = [market for market in self.candidates if market.slug not in fixed]
        exploration: list[MarketMetadata] = []
        if remaining and discovery_target:
            start = self.discovery_offset % len(remaining)
            rotated = remaining[start:] + remaining[:start]
            exploration = rotated[:discovery_target]
            self.discovery_offset = (start + len(exploration)) % len(remaining)
        discovery = [self.catalog[slug] for slug in sorted(self.pure_slugs)
                     if slug in self.catalog] + exploration
        self.store.record_selection(
            self.run_id, "VOLUME_DISCOVERY", discovery, at,
            source="upcoming_moneylines_for_book_volume")
        self.store.record_selection(
            self.run_id, "REWARD_SLOTS", reward, at,
            source="execution_queue_stream_sessions")
        self._apply_cohorts(at)
        await self._capture_settlements(at)
        scope = tuple(sorted(
            self.engine.required_slugs | {market.slug for market in discovery}))
        if len(scope) > 100:
            raise ValueError("combined scalp measurement scope exceeds 100 markets")
        self.store.flush()
        return scope

    async def _capture_settlements(self, at: datetime) -> None:
        retained = sorted(
            slug for slug in self.engine.required_slugs
            if slug not in self.catalog and self.engine.has_exposure(slug))
        if not retained:
            return
        start = self.settlement_offset % len(retained)
        rotated = retained[start:] + retained[:start]
        batch = rotated[:5]
        self.settlement_offset = (start + len(batch)) % len(retained)
        for slug in batch:
            try:
                market = await self.gateway.fetch_market(slug)
                if market.active and not market.closed:
                    continue
                settlement = await self.gateway.fetch_settlement(slug)
            except AdapterError:
                continue
            self.store.upsert_market(self.run_id, market, at)
            self.store.record_settlement(self.run_id, slug, at, settlement)
            self.engine.settle_market(slug, at=at)

    async def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        if self.resumed:
            self.store.resume_run(self.run_id, started)
        else:
            self.store.start_run(self.run_id, started, {
                **asdict(self.config.policy),
                "pure_markets": self.config.pure_markets,
                "horizon_minutes": self.config.horizon_minutes,
                "refresh_minutes": self.config.refresh_minutes,
                "rest_requests_per_second": self.config.rest_requests_per_second,
                "cohorts": ["PURE_VOLUME", "REWARD_SLOTS"],
            })
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + self.config.minutes * 60
                    if self.config.minutes > 0 else math.inf)
        refresh_seconds = self.config.refresh_minutes * 60
        rank_seconds = 30.0
        next_refresh = loop.time()
        next_rank = loop.time() + rank_seconds
        stream: ShadowMarketStream | None = None
        stream_session: int | None = None
        decode_seen = 0
        dropped_seen = 0
        completed_run = False
        try:
            while loop.time() < deadline:
                if loop.time() >= next_refresh or not self.scope:
                    new_scope = await self._selection(datetime.now(timezone.utc))
                    next_refresh = loop.time() + refresh_seconds
                    if new_scope != self.scope:
                        if stream is not None:
                            await stream.close()
                            if stream_session is not None:
                                self.store.finish_stream(
                                    stream_session, datetime.now(timezone.utc), "REBOUND")
                            stream = None
                            stream_session = None
                        self.scope = new_scope
                if loop.time() >= next_rank:
                    self._rank_pure(datetime.now(timezone.utc))
                    next_rank = loop.time() + rank_seconds
                if not self.scope:
                    self.coverage_gaps += 1
                    self.store.flush()
                    await asyncio.sleep(min(5.0, max(0.0, deadline - loop.time())))
                    continue
                if stream is None:
                    stream = ShadowMarketStream(self.client)
                    try:
                        await stream.start(self.scope)
                    except Exception as exc:
                        self.reconnects += 1
                        self.coverage_gaps += 1
                        stream = None
                        await asyncio.sleep(min(2 ** min(self.reconnects, 5), 30))
                        continue
                    now = datetime.now(timezone.utc)
                    stream_session = self.store.start_stream(self.run_id, now, self.scope)
                    decode_seen = 0
                    dropped_seen = 0
                timeout = min(1.0, max(0.01, deadline - loop.time()),
                              max(0.01, next_refresh - loop.time()))
                try:
                    event = await stream.next_event(timeout=timeout)
                except TimeoutError:
                    event = None
                except AdapterError as exc:
                    self.reconnects += 1
                    self.coverage_gaps += 1
                    if stream_session is not None:
                        self.store.finish_stream(
                            stream_session, datetime.now(timezone.utc), "ERROR", str(exc))
                    await stream.close()
                    stream = None
                    stream_session = None
                    continue
                observed_at = datetime.now(timezone.utc)
                if isinstance(event, BookSnapshot):
                    self.latest_books[event.slug] = event
                    self.latest_book_at[event.slug] = observed_at
                    self.engine.on_book(event, observed_at=observed_at)
                elif isinstance(event, TradePrint):
                    self.engine.on_trade(event, observed_at=observed_at)
                if stream.decode_failures > decode_seen:
                    self.coverage_gaps += stream.decode_failures - decode_seen
                    decode_seen = stream.decode_failures
                if stream.dropped_events > dropped_seen:
                    self.coverage_gaps += stream.dropped_events - dropped_seen
                    dropped_seen = stream.dropped_events
                self.store.flush()
                message_age = (
                    (observed_at - stream.last_message_at).total_seconds()
                    if stream.last_message_at is not None else math.inf)
                if message_age >= 180:
                    self.reconnects += 1
                    self.coverage_gaps += 1
                    if stream_session is not None:
                        self.store.finish_stream(
                            stream_session, observed_at, "ERROR", "180s market silence")
                    await stream.close()
                    stream = None
                    stream_session = None
            completed_run = True
        finally:
            ended = datetime.now(timezone.utc)
            if stream is not None:
                await stream.close()
            if stream_session is not None:
                self.store.finish_stream(
                    stream_session, ended,
                    "STOPPED" if completed_run else "INTERRUPTED")
            if completed_run:
                self.engine.finish(ended)
                self.store.finish_run(
                    self.run_id, ended, reconnects=self.reconnects,
                    coverage_gaps=self.coverage_gaps)
            else:
                # Preserve durable first-leg state for the supervisor's next
                # process.  An interruption is missing coverage, not evidence
                # that a hypothetical position closed.
                self.store.flush()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        rows = self.store.query(
            "SELECT cohort,COUNT(*) episodes,"
            "SUM(status='BOTH_FILLED') both_filled,"
            "SUM(first_fill_at IS NOT NULL AND status!='BOTH_FILLED') one_side_open,"
            "AVG(CASE WHEN status='BOTH_FILLED' THEN "
            "(julianday(completed_at)-julianday(first_fill_at))*86400 END) "
            "mean_second_leg_seconds,"
            "SUM(COALESCE(estimated_net_pair_pnl_usd,0)) completed_net_pnl "
            "FROM scalp_episodes WHERE run_id=? GROUP BY cohort ORDER BY cohort",
            (self.run_id,))
        return {
            "run_id": self.run_id,
            "database": str(self.config.database.resolve()),
            "scope_markets": len(self.scope),
            "cohorts": [dict(row) for row in rows],
            "reconnects": self.reconnects,
            "coverage_gaps": self.coverage_gaps,
            "orders_sent": 0,
        }

    def close(self) -> None:
        self.store.close()


async def _run(config: ScalpRunConfig) -> dict[str, Any]:
    lease = SingleInstanceLease(config.database.with_suffix(
        config.database.suffix + ".lock"))
    lease.acquire()
    client = None
    runner = None
    try:
        client = client_from_environment()
        runner = ScalpRunner(client, config)
        return await runner.run()
    finally:
        if runner is not None:
            runner.close()
        if client is not None:
            await client.close()
        lease.release()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated strict two-sided spread-scalp measurement")
    parser.add_argument("--database", type=Path, default=Path("data/scalp.db"))
    parser.add_argument("--live-database", type=Path,
                        default=Path("data/commissioning.db"))
    parser.add_argument("--minutes", type=float, default=0,
                        help="0 runs until interrupted")
    parser.add_argument("--pure-markets", type=int, default=40)
    parser.add_argument("--horizon-minutes", type=int, default=1440)
    parser.add_argument("--refresh-minutes", type=float, default=15)
    parser.add_argument("--rest-rps", type=float, default=1.0)
    parser.add_argument("--size", type=float, default=10.0)
    parser.add_argument("--no-fill-timeout", type=float, default=300.0)
    parser.add_argument("--minimum-spread", type=float, default=0.005)
    args = parser.parse_args()
    if (args.minutes < 0 or not 1 <= args.pure_markets <= 80
            or args.horizon_minutes <= 0 or args.refresh_minutes <= 0
            or not 0 < args.rest_rps <= 2):
        parser.error("invalid duration, market count, horizon, refresh, or REST rate")
    config = ScalpRunConfig(
        database=args.database, live_database=args.live_database,
        minutes=args.minutes, pure_markets=args.pure_markets,
        horizon_minutes=args.horizon_minutes,
        refresh_minutes=args.refresh_minutes,
        rest_requests_per_second=args.rest_rps,
        policy=ScalpPolicy(
            quantity=args.size, no_fill_timeout_seconds=args.no_fill_timeout,
            minimum_spread=args.minimum_spread),
    )
    try:
        result = asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 130
    except (AdapterError, ValueError, LeaseHeld) as exc:
        parser.exit(1, f"scalp measurement failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
