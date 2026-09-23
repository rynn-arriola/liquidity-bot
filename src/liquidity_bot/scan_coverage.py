"""Bounded, opt-in broad-book measurement. Never imported by the live runner.

The comparison is the selector's modeled first-scan shortlist, not the live
account's holdings. Metadata is frozen for at most 120 seconds. Rankings are
research estimates, not executable entry proof or expected paid profit.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import time

from .errors import AdapterError
from .market_selector import (
    RewardMarketSelector, _opportunity_key, evaluate_market_opportunity,
)
from .streams import MarketReadOnlyStream

MAX_WATCH = 300
MAX_OBSERVE_SECONDS = 120
MAX_BOOK_AGE_SECONDS = 3.0


class DiscoveryReads:
    """Expose only discovery reads; keep the gateway's report lane intact."""

    def __init__(self, gateway):
        self._gateway = gateway

    @property
    def last_upcoming_event_pages(self):
        return self._gateway.last_upcoming_event_pages

    async def list_upcoming_sports_markets(self, **kwargs):
        return await self._gateway.list_upcoming_sports_markets(**kwargs)

    async def fetch_incentives(self, slugs):
        return await self._gateway.fetch_incentives(slugs)


class CoverageSelector(RewardMarketSelector):
    """Reuse discovery/gates and exact rotating shortlist, but fetch no books."""

    def __init__(self, *args, watch_limit=MAX_WATCH, **kwargs):
        super().__init__(*args, **kwargs)
        if type(watch_limit) is not int or not self.policy.max_book_candidates <= watch_limit <= MAX_WATCH:
            raise ValueError("watch limit must cover the baseline and be at most 300")
        self.watch_limit = watch_limit
        self.baseline_slugs = ()
        self.watched = ()
        self.candidate_count = 0

    def _detailed_candidates(self, ranked):
        baseline, deferred = super()._detailed_candidates(ranked)
        self.baseline_slugs = tuple(item[0].slug for item in baseline)
        baseline_set = set(self.baseline_slugs)
        self.watched = tuple((baseline + [
            item for item in ranked if item[0].slug not in baseline_set
        ])[:self.watch_limit])
        self.candidate_count = len(ranked)
        # Returning no detailed candidates makes the inherited discovery path
        # incapable of issuing REST books or mutating live selector state.
        return [], deferred


class WatchBooks:
    """At most three persistent diagnostic feeds; failure clears its shard.

    No REST fallback, renewal, reconnect or timestamp invention. A quiet book
    becomes unavailable rather than suggesting that a candidate is bad.
    """

    def __init__(self, client, *, stream_factory=MarketReadOnlyStream):
        self.client = client
        self.stream_factory = stream_factory
        self.books = {}
        self.failures = {}
        self.close_failures = {}
        self.frames = 0
        self._used = False

    @asynccontextmanager
    async def watch(self, slugs):
        clean = tuple(dict.fromkeys(slugs))
        if len(clean) > MAX_WATCH or any(not isinstance(s, str) or not s.strip() for s in clean):
            raise ValueError("watch requires at most 300 nonempty slugs")
        if self._used:
            raise RuntimeError("watch instances are single-use")
        self._used = True
        streams, readers = [], []
        try:
            for offset in range(0, len(clean), 100):
                scope = frozenset(clean[offset:offset + 100])
                stream = self.stream_factory(self.client)
                streams.append(stream)
                shard = len(streams) - 1
                try:
                    await asyncio.wait_for(stream.start(sorted(scope)), 5.0)
                except (AdapterError, OSError, TimeoutError) as exc:
                    self.failures[shard] = type(exc).__name__
                    continue
                readers.append(asyncio.create_task(self._read(stream, scope, shard)))
            yield self
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            async def close(stream):
                await asyncio.wait_for(stream.close(), 5.0)
            try:
                results = await asyncio.gather(*(close(s) for s in streams),
                                               return_exceptions=True)
                self.close_failures = {i: type(result).__name__
                    for i, result in enumerate(results) if isinstance(result, BaseException)}
            finally:
                self.books.clear()

    async def _read(self, stream, scope, shard):
        try:
            while True:
                book = await stream.next_book()
                if book.slug not in scope:
                    raise AdapterError("diagnostic stream scope mismatch")
                self.books[book.slug] = book
                self.frames += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # This isolated diagnostic loses coverage, never trading authority.
            self.failures[shard] = type(exc).__name__
            for slug in scope:
                self.books.pop(slug, None)


def compare(selector, books, *, now, discovered_at, top_n=32):
    """Compare both scopes on identical received books at one aware instant."""
    if any(t.tzinfo is None or t.utcoffset() is None for t in (now, discovered_at)):
        raise ValueError("measurement clocks must be timezone-aware")
    if type(top_n) is not int or not 1 <= top_n <= 32:
        raise ValueError("top_n must be in [1, 32]")
    metadata_age = (now - discovered_at).total_seconds()
    unknown, ineligible, opportunities = {}, [], []
    for market, incentive, _ in selector.watched:
        slug = market.slug
        if not 0 <= metadata_age <= MAX_OBSERVE_SECONDS:
            unknown[slug] = "metadata_expired"
            continue
        book = books.get(slug)
        if book is None or book.received_at is None or book.slug != slug:
            unknown[slug] = "missing_book"
            continue
        age = (now - book.received_at).total_seconds()
        if not 0 <= age < MAX_BOOK_AGE_SECONDS:
            unknown[slug] = "stale_or_future_book"
            continue
        try:
            # Same ranking-only lifecycle treatment as a completed live scan.
            # Cached event receipts must NEVER be presented as entry proof.
            period = selector._applicable_period(
                market, incentive, now, require_event_freshness=False)
            if period is None:
                ineligible.append(slug)
                continue
            item = evaluate_market_opportunity(
                market, incentive, book, period=period, now=now,
                policy=selector.policy, fill_hazard_model=selector.fill_hazard_model)
            opportunities.append(item)
        except (AdapterError, ValueError):
            unknown[slug] = "evaluation_failed"
    opportunities.sort(key=_opportunity_key)
    viable = [item for item in opportunities if item.viable_sides]
    baseline_set = set(selector.baseline_slugs)
    baseline = [item for item in viable if item.market.slug in baseline_set][:top_n]
    broad = viable[:top_n]
    comparable = not any(slug in unknown for slug in baseline_set)
    # Do not call an outside candidate better when baseline books are missing.
    better = None if not comparable else [item.market.slug for item in broad
        if item.market.slug not in baseline_set and
        (len(baseline) < top_n or _opportunity_key(item) < _opportunity_key(baseline[-1]))]
    return {
        "observed_at": now.isoformat(), "metadata_age_seconds": metadata_age,
        "comparison": "modeled_first_scan_not_actual_live_orders",
        "ranking_only_not_entry_proof": True,
        "candidate_count": selector.candidate_count,
        "watched_count": len(selector.watched),
        "unwatched_count": selector.candidate_count - len(selector.watched),
        "baseline_count": len(baseline_set), "evaluated_count": len(opportunities),
        "baseline_comparable": comparable,
        "coverage_complete": not unknown and selector.candidate_count == len(selector.watched),
        "unknown": unknown, "currently_ineligible": ineligible,
        "baseline_top": [item.market.slug for item in baseline],
        "broader_top": [item.market.slug for item in broad],
        "outside_baseline_better_or_fills_gap": better,
        "estimates_not_paid_rewards": True,
        "ranked": [{"slug": item.market.slug, "period": item.period.period,
                    "viable": bool(item.viable_sides),
                    "projected_reward": item.projected_reward_total,
                    "book_received_at": item.book.received_at.isoformat(),
                    "in_baseline": item.market.slug in baseline_set}
                   for item in opportunities],
    }


async def observe(selector, watcher, *, seconds, clock, top_n=32):
    if not math.isfinite(seconds) or not 1 <= seconds <= MAX_OBSERVE_SECONDS:
        raise ValueError("observation must be between 1 and 120 seconds")
    loop = asyncio.get_running_loop()
    # One bounded metadata discovery; no refresh loop or hidden REST fallback.
    started = clock()
    discovery = await asyncio.wait_for(selector.select(now=started), 120.0)
    snapshots = []
    cpu_start = time.process_time()
    async with watcher.watch([m.slug for m, _, _ in selector.watched]):
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            sample_start = loop.time()
            row = compare(selector, dict(watcher.books), now=clock(),
                          discovered_at=started, top_n=top_n)
            row["evaluation_seconds"] = loop.time() - sample_start
            row["stream_failures"] = dict(watcher.failures)
            snapshots.append(row)
            await asyncio.sleep(min(1.0, max(0.0, deadline - loop.time())))
    return {"mode": "read_only_scan_coverage", "order_mutations": 0,
            "rest_book_requests": 0, "frames": watcher.frames,
            "close_failures": watcher.close_failures,
            "cpu_seconds": time.process_time() - cpu_start,
            "policy": asdict(selector.policy),
            "discovery_failures": discovery.failures,
            "samples": snapshots}


async def _run(args):
    from polymarket_us import AsyncPolymarketUS
    from .adapters import AsyncReadOnlyGateway, shared_api_limiter, REST_REQUEST_TIMEOUT_SECONDS
    from .commissioning import CommissioningConfig
    from .runtime_secrets import load_runtime_secrets

    # Explicit production governor path avoids a second worktree-local budget.
    if not args.limiter_state.is_file():
        raise ValueError("existing live shared limiter state is required")
    limiter = shared_api_limiter(state_path=args.limiter_state.resolve())
    config = CommissioningConfig(
        market_slug="auto", economic_risk_accepted=False,
        exclusive_api_key_confirmed=False, maker_lab_stopped_confirmed=False,
        quote_size=Decimal(args.quote_size), max_markets=args.slots)
    secrets = load_runtime_secrets(require_polymarket=True)
    client = AsyncPolymarketUS(key_id=secrets.key_id, secret_key=secrets.secret_key,
                              timeout=REST_REQUEST_TIMEOUT_SECONDS)
    try:
        gateway = AsyncReadOnlyGateway(client, limiter=limiter, default_traffic_class="report",
                                       retry_attempts=1, event_max_pages=10, incentive_max_pages=10)
        selector = CoverageSelector(DiscoveryReads(gateway), config.reward_selection_policy(),
                                    watch_limit=args.watch_limit,
                                    clock=lambda: datetime.now(timezone.utc))
        result = await observe(selector, WatchBooks(client), seconds=args.seconds,
                               clock=lambda: datetime.now(timezone.utc), top_n=args.slots)
        result["rest_telemetry"] = asdict(limiter.telemetry())
        return result
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quote-size", choices=[str(n) for n in range(5, 31)], required=True)
    parser.add_argument("--slots", type=int, choices=range(20, 33), required=True)
    parser.add_argument("--watch-limit", type=int, choices=range(100, 301), default=300,
                        metavar="100..300")
    parser.add_argument("--seconds", type=int, choices=range(1, 121), default=60,
                        metavar="1..120")
    parser.add_argument("--limiter-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Reserve without overwriting evidence, before making any network request.
    with args.output.open("x", encoding="utf-8") as output:
        try:
            import fcntl
            # One observer per live governor, even across different worktrees.
            with args.limiter_state.with_suffix(".scan-coverage.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = asyncio.run(_run(args))
        except (Exception, KeyboardInterrupt) as exc:
            json.dump({"status": "incomplete", "error_type": type(exc).__name__}, output)
            print(f"Measurement incomplete: {type(exc).__name__}; no retry.")
            return 1
        json.dump(result, output, indent=2, default=str, allow_nan=False)
    print(f"Read-only evidence saved to {args.output}; no trading change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
