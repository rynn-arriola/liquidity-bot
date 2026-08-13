"""Bounded command-line probe for read-only adapter acceptance.

The command loads credentials from environment variables or the narrow shared
maker-lab credential handoff. It reads market metadata, books, incentives,
optional earnings, and optional WebSocket snapshots. It has no code path to
the SDK order resource.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from polymarket_us import AsyncPolymarketUS

from .adapters import AsyncIntervalLimiter, AsyncReadOnlyGateway
from .errors import AdapterError
from .rewards import evaluate_order_score, select_applicable_period
from .runtime_secrets import RuntimeSecretError, load_runtime_secrets
from .streams import MarketReadOnlyStream, PrivateReadOnlyStream


def client_from_environment() -> AsyncPolymarketUS:
    """Compatibility name: environment wins, then shared maker-lab secrets."""

    try:
        secrets = load_runtime_secrets(require_polymarket=True)
    except RuntimeSecretError as exc:
        raise SystemExit(str(exc)) from exc
    return AsyncPolymarketUS(
        key_id=secrets.key_id, secret_key=secrets.secret_key)


def _score_dict(result) -> dict[str, Any]:
    return {
        "eligible": result.eligible,
        "score": round(result.score, 8),
        "ticks_from_best": result.ticks_from_best,
        "cutoff_price": result.cutoff_price,
        "depth_walked": round(result.total_depth, 8),
        "reason": result.reason,
    }


async def inspect_markets(
    client: Any,
    slugs: list[str],
    *,
    size: float = 2.0,
    include_earnings: bool = False,
) -> dict[str, Any]:
    """Compose Phase 0 rules with Phase 1 adapters for named markets."""

    gateway = AsyncReadOnlyGateway(
        client, limiter=AsyncIntervalLimiter.from_rate(4))
    incentives = await gateway.fetch_incentives(slugs)
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for slug in slugs:
        market = await gateway.fetch_market(slug)
        book = await gateway.fetch_book(slug)
        program = incentives.get(slug)
        row: dict[str, Any] = {
            "slug": slug,
            "game_start": market.game_start.isoformat() if market.game_start else None,
            "tick_size": market.tick_size,
            "market_active": market.active,
            "market_closed": market.closed,
            "book_state": book.state,
            "bid_depth": round(book.bid_depth, 8),
            "offer_depth": round(book.offer_depth, 8),
        }
        if program is None:
            row.update(applicable_program=None, reason="no_incentive_program")
            rows.append(row)
            continue
        selected = select_applicable_period(program.periods, market.game_start, now)
        if selected is None:
            row.update(applicable_program=None,
                       reported_periods=[item.period for item in program.periods],
                       reason="no_applicable_active_period")
            rows.append(row)
            continue
        if market.tick_size is None:
            row.update(applicable_program=selected.program_id,
                       reason="missing_tick_size")
            rows.append(row)
            continue
        bid = evaluate_order_score(
            side="bid", price=book.best_bid.price, size=size,
            levels=book.levels("bid"), incentive=selected,
            tick_size=market.tick_size)
        offer = evaluate_order_score(
            side="offer", price=book.best_offer.price, size=size,
            levels=book.levels("offer"), incentive=selected,
            tick_size=market.tick_size)
        row.update(
            applicable_program=selected.program_id,
            phase=selected.period,
            phase_allows_initial_entry=selected.period != "live",
            reward_pool=selected.reward_pool,
            target_size=selected.target_size,
            discount_factor=selected.discount_factor,
            bid=_score_dict(bid),
            offer=_score_dict(offer),
            reason="evaluated",
        )
        rows.append(row)

    output: dict[str, Any] = {
        "observed_at": now.isoformat(),
        "order_size_for_scoring_only": size,
        "markets": rows,
    }
    if include_earnings:
        earnings = await gateway.fetch_earnings()
        summary: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"rows": 0, "reward": 0.0})
        for earning in earnings:
            summary[earning.status]["rows"] += 1
            summary[earning.status]["reward"] += earning.reward
        output["account_earnings"] = {
            status: {"rows": values["rows"],
                     "reward": round(float(values["reward"]), 8)}
            for status, values in sorted(summary.items())
        }
    return output


async def observe_streams(
    client: Any,
    slugs: list[str],
    *,
    seconds: float,
) -> dict[str, Any]:
    """Observe event kinds and counts without persisting account payloads."""

    if seconds <= 0:
        return {"market_books": 0, "private_events": {}}
    market_stream = MarketReadOnlyStream(client)
    private_stream = PrivateReadOnlyStream(client)
    await market_stream.start(slugs)
    try:
        await private_stream.start(slugs)
    except Exception:
        await market_stream.close()
        raise

    deadline = time.monotonic() + seconds
    books = 0
    private_counts: dict[str, int] = defaultdict(int)

    async def books_worker() -> None:
        nonlocal books
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                await market_stream.next_book(timeout=remaining)
                books += 1
            except TimeoutError:
                return

    async def private_worker() -> None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                event = await private_stream.next_event(timeout=remaining)
                private_counts[event.kind.value] += 1
            except TimeoutError:
                return

    try:
        await asyncio.gather(books_worker(), private_worker())
    finally:
        await private_stream.close()
        await market_stream.close()
    return {"market_books": books,
            "private_events": dict(sorted(private_counts.items()))}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    client = client_from_environment()
    try:
        result = await inspect_markets(
            client, args.slug, size=args.size, include_earnings=args.earnings)
        if args.stream_seconds:
            result["stream_observation"] = await observe_streams(
                client, args.slug, seconds=args.stream_seconds)
        return result
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Polymarket liquidity eligibility probe")
    parser.add_argument("--slug", action="append", required=True,
                        help="market slug; repeat for multiple markets")
    parser.add_argument("--size", type=float, default=2.0,
                        help="hypothetical size used only for reward scoring")
    parser.add_argument("--earnings", action="store_true",
                        help="include aggregate account earning totals by status")
    parser.add_argument("--stream-seconds", type=float, default=0.0,
                        help="bounded WebSocket observation duration")
    args = parser.parse_args()
    if args.size <= 0 or args.stream_seconds < 0:
        parser.error("size must be positive and stream-seconds cannot be negative")
    try:
        result = asyncio.run(_run(args))
    except AdapterError as exc:
        parser.exit(1, f"read-only probe failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
