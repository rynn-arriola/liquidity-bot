"""Persistent read-only Phase 2 runner.

This process subscribes to public full books and trades, records hypothetical
quotes and exits, and periodically refreshes incentives/earnings. It never
imports or calls an order resource.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .adapters import AsyncReadOnlyGateway, shared_api_limiter
from .errors import AdapterError
from .models import BookSnapshot, MarketIncentive, MarketMetadata, TradePrint
from .discord_summary import LiquiditySummary, build_liquidity_summary
from .notifications import DiscordNotifier
from .probe import client_from_environment
from .rewards import AmbiguousIncentiveError, select_applicable_period
from .shadow_engine import ShadowEngine, ShadowPolicy
from .shadow_store import ShadowStore
from .streams import ShadowMarketStream
from .runtime_secrets import load_runtime_secrets


WINNER_SUFFIXES = (
    "_full_game_winner", "_full_time_winner", "_match_winner", "_fight_winner")


def is_full_game_winner(market: MarketMetadata) -> bool:
    kind = (market.sports_market_type or "").lower()
    return kind == "moneyline" or kind.endswith(WINNER_SUFFIXES)


def is_two_way_slug(slug: str) -> bool:
    return not re.match(r"^.*\d{4}-\d{2}-\d{2}-.+$", slug or "")


def rank_candidates(
    markets: Iterable[MarketMetadata],
    incentives: dict[str, MarketIncentive],
    *,
    now: datetime,
) -> list[MarketMetadata]:
    """Rank observable programs, without claiming the pool is our payout."""

    scored: list[tuple[float, datetime, str, MarketMetadata]] = []
    for market in markets:
        program = incentives.get(market.slug)
        if program is None or market.game_start is None:
            continue
        try:
            period = select_applicable_period(program.periods, market.game_start, now)
        except AmbiguousIncentiveError:
            continue
        if period is None or period.period == "live":
            continue
        # Pool/target is only an observation-priority heuristic. Eligibility,
        # competition, fill hazard and exit cost are measured downstream.
        density = period.reward_pool / period.target_size
        scored.append((-density, market.game_start, market.slug, market))
    return [item[-1] for item in sorted(scored)]


@dataclass(frozen=True)
class RunConfig:
    slugs: tuple[str, ...]
    minutes: float
    database: Path
    max_markets: int
    discovery_horizon_minutes: int
    rest_requests_per_second: float
    incentive_refresh_minutes: float
    discovery_refresh_minutes: float
    policy: ShadowPolicy
    discord_summary_minutes: float = 5.0


class ShadowRunner:
    def __init__(self, client: Any, config: RunConfig,
                 notifier: DiscordNotifier | None = None):
        self.client = client
        self.config = config
        self.gateway = AsyncReadOnlyGateway(
            client,
            limiter=shared_api_limiter(config.rest_requests_per_second),
            default_traffic_class="report")
        self.store = ShadowStore(config.database)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"shadow-{timestamp}-{uuid.uuid4().hex[:8]}"
        self.engine: ShadowEngine | None = None
        self.markets: dict[str, MarketMetadata] = {}
        self.incentives: dict[str, MarketIncentive] = {}
        self.selected_slugs: set[str] = set()
        self.notifier = notifier
        self.started_at: datetime | None = None
        self.last_market_message_at: datetime | None = None

    async def _discover(self) -> list[str]:
        if self.config.slugs:
            return list(dict.fromkeys(self.config.slugs))[:self.config.max_markets]
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(minutes=self.config.discovery_horizon_minutes)
        catalog = await self.gateway.list_sports_markets()
        candidates = [
            market for market in catalog
            if market.active and not market.closed and market.game_start is not None
            and now < market.game_start <= horizon
            and is_full_game_winner(market) and is_two_way_slug(market.slug)
        ]
        found = await self.gateway.fetch_incentives(
            [market.slug for market in candidates])
        ranked = rank_candidates(candidates, found, now=now)
        return [market.slug for market in ranked[:self.config.max_markets]]

    async def _fetch_selection(
        self, slugs: list[str]
    ) -> tuple[dict[str, MarketMetadata], dict[str, MarketIncentive]]:
        markets: dict[str, MarketMetadata] = {}
        for slug in slugs:
            market = await self.gateway.fetch_market(slug)
            if not market.active or market.closed or not is_full_game_winner(market):
                continue
            markets[slug] = market
        incentives = await self.gateway.fetch_incentives(markets)
        markets = {slug: market for slug, market in markets.items()
                   if slug in incentives}
        return markets, incentives

    async def _load_markets(self, slugs: list[str]) -> None:
        self.markets, self.incentives = await self._fetch_selection(slugs)
        self.selected_slugs = set(self.markets)

    async def _rotate_markets(self, observed_at: datetime) -> None:
        if self.engine is None:
            raise RuntimeError("shadow engine is not initialized")
        selected, incentives = await self._fetch_selection(await self._discover())
        old_markets = self.markets
        removed = set(old_markets) - set(selected)
        for slug in removed:
            self.engine.deactivate_market(slug, observed_at=observed_at)
        # Retain a removed subscription only long enough to finish any open
        # inventory replay. It cannot open a new episode after deactivation.
        retained = {slug: old_markets[slug] for slug in removed
                    if self.engine.has_open_episode(slug)}
        self.markets = {**selected, **retained}
        self.selected_slugs = set(selected)
        self.incentives = {
            **incentives,
            **{slug: self.incentives[slug] for slug in retained
               if slug in self.incentives},
        }
        for slug, market in selected.items():
            self.engine.register_market(
                market, incentives[slug].periods, observed_at=observed_at)
        await self._snapshot_books(observed_at)

    async def _refresh_incentives(self, observed_at: datetime) -> None:
        refreshed = await self.gateway.fetch_incentives(self.selected_slugs)
        self.incentives.update(refreshed)
        if self.engine is None:
            return
        for slug in self.selected_slugs:
            market = self.markets[slug]
            program = refreshed.get(slug)
            # Empty periods deliberately invalidate a disappeared program.
            # Keeping the last-known incentive would overstate eligibility.
            periods = program.periods if program is not None else ()
            self.engine.register_market(
                market, periods, observed_at=observed_at)

    async def _snapshot_books(self, observed_at: datetime) -> None:
        if self.engine is None:
            raise RuntimeError("shadow engine is not initialized")
        for slug in self.markets:
            try:
                snapshot = await self.gateway.fetch_book(slug)
            except AdapterError:
                continue
            self.engine.on_book(snapshot, observed_at=observed_at)

    async def _capture_earnings(self, captured_at: datetime) -> None:
        earnings = await self.gateway.fetch_earnings()
        # A shadow quote did not exist on the venue. These rows are evidence
        # about the account, but cannot calibrate this run's score share.
        self.store.record_earnings(
            earnings, captured_at=captured_at, attributable=False)

    async def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        self.started_at = started
        slugs = await self._discover()
        await self._load_markets(slugs)
        self.engine = ShadowEngine(
            self.run_id, self.store, self.config.policy, started_at=started)
        for slug, market in self.markets.items():
            self.engine.register_market(
                market, self.incentives[slug].periods, observed_at=started)
        await self._capture_earnings(started)
        await self._snapshot_books(datetime.now(timezone.utc))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.minutes * 60
        refresh_every = self.config.incentive_refresh_minutes * 60
        next_refresh = loop.time() + refresh_every
        rotate_every = self.config.discovery_refresh_minutes * 60
        next_rotation = loop.time() + rotate_every
        summary_every = self.config.discord_summary_minutes * 60
        next_summary = (loop.time() + summary_every
                        if summary_every > 0 else float("inf"))
        reconnects = 0
        stream: ShadowMarketStream | None = None
        try:
            while loop.time() < deadline:
                if not self.markets:
                    await asyncio.sleep(min(1.0, deadline - loop.time()))
                    observed_at = datetime.now(timezone.utc)
                    if loop.time() >= next_refresh:
                        await self._capture_earnings(observed_at)
                        next_refresh = loop.time() + refresh_every
                    if loop.time() >= next_rotation:
                        await self._rotate_markets(observed_at)
                        next_rotation = loop.time() + rotate_every
                    if loop.time() >= next_summary:
                        await self.post_discord_summary(
                            observed_at=observed_at, reconnects=reconnects)
                        next_summary = loop.time() + summary_every
                    continue
                stream = ShadowMarketStream(self.client)
                rotation_due = False
                try:
                    await stream.start(self.markets)
                    while loop.time() < deadline:
                        now_mono = loop.time()
                        timeout = min(1.0, deadline - now_mono,
                                      max(0.01, next_refresh - now_mono),
                                      max(0.01, next_rotation - now_mono),
                                      max(0.01, next_summary - now_mono))
                        try:
                            event = await stream.next_event(timeout=timeout)
                        except TimeoutError:
                            event = None
                        observed_at = datetime.now(timezone.utc)
                        if isinstance(event, BookSnapshot):
                            self.last_market_message_at = observed_at
                            self.engine.on_book(event, observed_at=observed_at)
                        elif isinstance(event, TradePrint):
                            self.last_market_message_at = observed_at
                            self.engine.on_trade(event, observed_at=observed_at)
                        if loop.time() >= next_refresh:
                            await self._refresh_incentives(observed_at)
                            next_refresh = loop.time() + refresh_every
                        if loop.time() >= next_rotation:
                            rotation_due = True
                            break
                        if loop.time() >= next_summary:
                            await self.post_discord_summary(
                                observed_at=observed_at, reconnects=reconnects)
                            next_summary = loop.time() + summary_every
                except AdapterError:
                    reconnects += 1
                    if reconnects > 5:
                        raise
                finally:
                    await stream.close()
                    stream = None
                if rotation_due and loop.time() < deadline:
                    await self._rotate_markets(datetime.now(timezone.utc))
                    next_rotation = loop.time() + rotate_every
                    continue
                if loop.time() < deadline:
                    await asyncio.sleep(min(2 ** reconnects, 30))
                    await self._snapshot_books(datetime.now(timezone.utc))
        finally:
            if stream is not None:
                await stream.close()
            ended = datetime.now(timezone.utc)
            self.engine.finish(ended)
            try:
                await self._capture_earnings(ended)
            finally:
                await self.post_discord_summary(
                    observed_at=ended, reconnects=reconnects)
        return self.summary(reconnects)

    def discord_snapshot(
        self, *, observed_at: datetime, reconnects: int,
    ) -> LiquiditySummary:
        """Build a shadow-only snapshot; it can never report venue P&L."""

        if self.started_at is None:
            raise RuntimeError("shadow session has not started")

        def scalar(sql: str) -> float:
            row = self.store.query(sql, (self.run_id,))[0][0]
            return float(row or 0)

        exits = int(scalar(
            "SELECT COUNT(*) FROM shadow_exit_replays WHERE run_id=?"))
        completion = (scalar(
            "SELECT AVG(complete) FROM shadow_exit_replays WHERE run_id=?")
            if exits else 0.0)
        pending = self.store.query(
            "SELECT COALESCE(SUM(reward),0) FROM shadow_earnings "
            "WHERE status='PENDING' AND attributable=0")[0][0]
        paid = self.store.query(
            "SELECT COALESCE(SUM(reward),0) FROM shadow_earnings "
            "WHERE status='PAID' AND attributable=1")[0][0]
        eligible_sides = int(scalar(
            "SELECT COUNT(*) FROM shadow_quote_observations q "
            "WHERE q.run_id=? AND q.candidate=1 AND q.observed_at=("
            "SELECT MAX(q2.observed_at) FROM shadow_quote_observations q2 "
            "WHERE q2.run_id=q.run_id AND q2.market_slug=q.market_slug "
            "AND q2.side=q.side)"))
        open_episodes = (sum(
            1 for slug in self.engine.states
            if self.engine.has_open_episode(slug)) if self.engine else 0)
        market_age = ((observed_at - self.last_market_message_at).total_seconds()
                      if self.last_market_message_at else None)
        problems = (() if reconnects == 0 else
                    (f"market WebSocket reconnects: {reconnects}",))
        return LiquiditySummary(
            mode="SHADOW",
            readiness_status="DO_NOT_START_TRIAL",
            observed_at=observed_at,
            session_started_at=self.started_at,
            active_markets=len(self.selected_slugs),
            reward_eligible_sides=eligible_sides,
            score_seconds=scalar(
                "SELECT SUM(candidate_score_seconds) FROM shadow_score_daily "
                "WHERE run_id=?"),
            entry_fills=int(scalar(
                "SELECT COUNT(*) FROM shadow_fill_signals "
                "WHERE run_id=? AND role='entry'")),
            exit_samples=exits,
            exit_completion_rate=completion,
            open_positions=open_episodes,
            working_orders=0,
            max_inventory_age_seconds=(
                self.engine.oldest_open_episode_age(observed_at)
                if self.engine else 0.0),
            unresolved_intents=0,
            unknown_orders=0,
            attributable_paid_rewards_usd=Decimal(str(paid or 0)),
            pending_rewards_usd=Decimal(str(pending or 0)),
            rest_requests_per_second=self.config.rest_requests_per_second,
            market_stream_age_seconds=market_age,
            private_stream_age_seconds=None,
            integrity_problems=problems,
        )

    async def post_discord_summary(
        self, *, observed_at: datetime, reconnects: int,
    ) -> None:
        if self.notifier is None or self.config.discord_summary_minutes <= 0:
            return
        payload = build_liquidity_summary(self.discord_snapshot(
            observed_at=observed_at, reconnects=reconnects))
        await self.notifier.send_payload_async(payload, channel="summary")

    def summary(self, reconnects: int) -> dict[str, Any]:
        def scalar(sql: str) -> float:
            row = self.store.query(sql, (self.run_id,))[0][0]
            return float(row or 0)
        exits = [dict(row) for row in self.store.query(
            "SELECT horizon_seconds,COUNT(*) samples,"
            "AVG(pnl) mean_pnl,AVG(complete) completion_rate,"
            "AVG(loss_budget_pass) budget_pass_rate "
            "FROM shadow_exit_replays WHERE run_id=? GROUP BY horizon_seconds "
            "ORDER BY horizon_seconds", (self.run_id,))]
        return {
            "run_id": self.run_id,
            "database": str(self.config.database.resolve()),
            "markets": sorted(self.markets),
            "book_snapshots": int(scalar(
                "SELECT COUNT(*) FROM shadow_books WHERE run_id=?")),
            "public_trades": int(scalar(
                "SELECT COUNT(*) FROM shadow_trades WHERE run_id=?")),
            "entry_fill_signals": int(scalar(
                "SELECT COUNT(*) FROM shadow_fill_signals "
                "WHERE run_id=? AND role='entry'")),
            "candidate_score_seconds": scalar(
                "SELECT SUM(candidate_score_seconds) FROM shadow_score_daily "
                "WHERE run_id=?"),
            "exit_policies": exits,
            "websocket_reconnects": reconnects,
            "orders_sent": 0,
        }

    def close(self) -> None:
        self.store.close()


def _horizons(raw: str) -> tuple[int, ...]:
    try:
        result = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("horizons must be comma-separated integers") from exc
    if not result:
        raise argparse.ArgumentTypeError("at least one horizon is required")
    return result


async def _run(config: RunConfig) -> dict[str, Any]:
    client = client_from_environment()
    notifier = DiscordNotifier(load_runtime_secrets())
    runner = ShadowRunner(client, config, notifier)
    try:
        try:
            return await runner.run()
        except Exception as exc:
            await notifier.send_async(
                f"🚨 Liquidity Bot SHADOW stopped unexpectedly "
                f"({type(exc).__name__}); no exchange orders were enabled.",
                critical=True)
            raise
    finally:
        runner.close()
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persistent read-only liquidity shadow evaluator")
    parser.add_argument("--slug", action="append", default=[],
                        help="explicit market slug; omit to auto-discover")
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--database", type=Path, default=Path("data/shadow.db"))
    parser.add_argument("--max-markets", type=int, default=5)
    parser.add_argument("--discovery-horizon-minutes", type=int, default=360)
    parser.add_argument("--rest-rps", type=float, default=4.0,
                        help="shared REST budget; hard maximum is 20")
    parser.add_argument("--incentive-refresh-minutes", type=float, default=15)
    parser.add_argument("--discovery-refresh-minutes", type=float, default=15)
    parser.add_argument("--discord-summary-minutes", type=float, default=5,
                        help="summary interval; 0 disables Discord summaries")
    parser.add_argument("--size", type=float, default=2.0)
    parser.add_argument("--horizons", type=_horizons, default=(15, 30, 60, 120))
    parser.add_argument("--exit-depth-haircut", type=float, default=0.5)
    parser.add_argument("--max-exit-loss", type=float, default=5.0)
    args = parser.parse_args()
    if (args.minutes <= 0 or args.max_markets <= 0
            or args.incentive_refresh_minutes <= 0
            or args.discovery_refresh_minutes <= 0
            or args.discord_summary_minutes < 0):
        parser.error("durations and max-markets must be positive")
    config = RunConfig(
        slugs=tuple(args.slug), minutes=args.minutes, database=args.database,
        max_markets=args.max_markets,
        discovery_horizon_minutes=args.discovery_horizon_minutes,
        rest_requests_per_second=args.rest_rps,
        incentive_refresh_minutes=args.incentive_refresh_minutes,
        discovery_refresh_minutes=args.discovery_refresh_minutes,
        discord_summary_minutes=args.discord_summary_minutes,
        policy=ShadowPolicy(
            quote_size=args.size, exit_horizons_seconds=args.horizons,
            exit_depth_haircut=args.exit_depth_haircut,
            max_exit_loss_per_market=args.max_exit_loss),
    )
    try:
        result = asyncio.run(_run(config))
    except AdapterError as exc:
        parser.exit(1, f"shadow run failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
