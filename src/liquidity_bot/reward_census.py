"""Read-only census of the rewarded universe against the live entry gate.

This module answers one question and takes no risk to do it: across every
rewarded pregame market the executor could legally bind, what does the *exact*
live economic gate decide, and by how far does it miss?

It deliberately owns no economics of its own. It drives
``RewardMarketSelector.select`` and serializes the ``RewardMarketOpportunity``
rows that the live runner would have consumed, so a census result can never
drift from the behaviour it claims to predict. There is no order client here
and no code path to the SDK order resource.

The decisive derived column is ``gate_ratio``: the live rule reduces to

    projected_reward * (1 - fill_probability)  >  2 * fill_probability * unwind

so ``gate_ratio`` is that left side divided by that right side. A side is
viable exactly when the ratio exceeds 1, and the ratio states how far a
rejected side actually was rather than only that it failed. Because projected
reward scales with ``order_score / total_score``, ``max_total_score_for_entry``
converts the same margin into the touch depth that would have qualified.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from polymarket_us import AsyncPolymarketUS

from .adapters import (
    AsyncReadOnlyGateway, REST_REQUEST_TIMEOUT_SECONDS, shared_api_limiter)
from .errors import AdapterError
from .fill_hazard import league_from_slug
from .market_selector import (
    RewardMarketOpportunity,
    RewardMarketSelection,
    RewardMarketSelector,
    RewardSelectionPolicy,
    SideOpportunity,
)
from .runtime_secrets import RuntimeSecretError, load_runtime_secrets


# ``RewardSelectionPolicy`` caps deep evaluation at 100 books. The census asks
# for the maximum so a single pass covers the whole executable set whenever it
# is small, and reports ``deferred_markets`` when it is not.
CENSUS_BOOK_CANDIDATES = 100


@dataclass(frozen=True)
class CensusRow:
    """One book side of one rewarded market, as the live gate saw it."""

    observed_at: str
    slug: str
    league: str | None
    game_start: str | None
    seconds_to_event: float | None
    period: str
    reward_pool: float
    target_size: float
    discount_factor: float
    best_bid: float
    best_offer: float
    spread: float
    side: str
    price: float
    touch_depth: float
    order_score: float
    total_score: float
    snapshot_share: float
    projected_reward: float
    maximum_remaining_reward: float
    fill_probability: float
    fill_risk_driver: str
    stressed_exit_complete: bool
    stressed_exit_pnl: float
    unwind_cost: float
    expected_net_value: float | None
    gate_ratio: float | None
    max_total_score_for_entry: float | None
    blocker: str | None
    viable: bool


@dataclass(frozen=True)
class CensusResult:
    observed_at: datetime
    scan_seconds: float
    catalog_markets: int
    executable_catalog_markets: int
    rewarded_executable_markets: int
    unsupported_programs: int
    catalog_spread_filtered_markets: int
    eligible_pregame_markets: int
    books_evaluated: int
    viable_markets: int
    selected_slug: str | None
    deferred_markets: tuple[str, ...]
    failures: tuple[str, ...]
    rows: tuple[CensusRow, ...]

    @property
    def coverage_complete(self) -> bool:
        """True when no executable candidate went unevaluated this pass."""

        return not self.deferred_markets


def _gate_terms(side: SideOpportunity) -> tuple[float, float | None, float | None]:
    """Return (unwind_cost, gate_ratio, max_total_score_for_entry).

    ``gate_ratio`` is ``None`` when the side never reached the economics step,
    because a blocker earlier in the chain means no stressed exit was priced
    and there is no meaningful margin to report.
    """

    unwind_cost = max(0.0, -side.stressed_exit_pnl)
    if not side.stressed_exit_complete:
        return unwind_cost, None, None
    benefit = side.projection.projected_reward * (1.0 - side.fill_probability)
    cost = 2.0 * side.fill_probability * unwind_cost
    if cost <= 0:
        # No probability-weighted exit cost to cover. Any positive projected
        # reward clears; report an explicit infinity rather than dividing.
        return unwind_cost, float("inf") if benefit > 0 else 0.0, None
    ratio = benefit / cost
    total_score = side.projection.total_score
    maximum = ratio * total_score if total_score > 0 else None
    return unwind_cost, ratio, maximum


def _rows_for(
    opportunity: RewardMarketOpportunity,
    *,
    observed_at: datetime,
) -> list[CensusRow]:
    market = opportunity.market
    book = opportunity.book
    seconds_to_event = (
        (market.game_start - observed_at).total_seconds()
        if market.game_start is not None else None)
    rows: list[CensusRow] = []
    for side in opportunity.sides:
        unwind_cost, gate_ratio, maximum = _gate_terms(side)
        rows.append(CensusRow(
            observed_at=observed_at.isoformat(),
            slug=market.slug,
            league=league_from_slug(market.slug),
            game_start=(
                market.game_start.isoformat()
                if market.game_start is not None else None),
            seconds_to_event=seconds_to_event,
            period=opportunity.period.period,
            reward_pool=opportunity.period.reward_pool,
            target_size=opportunity.period.target_size,
            discount_factor=opportunity.period.discount_factor,
            best_bid=book.best_bid.price,
            best_offer=book.best_offer.price,
            spread=opportunity.spread,
            side=side.side,
            price=side.price,
            touch_depth=(
                book.best_bid.size if side.side == "bid"
                else book.best_offer.size),
            order_score=side.projection.order_score,
            total_score=side.projection.total_score,
            snapshot_share=side.projection.snapshot_share,
            projected_reward=side.projection.projected_reward,
            maximum_remaining_reward=side.projection.maximum_remaining_reward,
            fill_probability=side.fill_probability,
            fill_risk_driver=side.fill_risk_driver,
            stressed_exit_complete=side.stressed_exit_complete,
            stressed_exit_pnl=side.stressed_exit_pnl,
            unwind_cost=unwind_cost,
            expected_net_value=side.expected_net_value,
            gate_ratio=gate_ratio,
            max_total_score_for_entry=maximum,
            blocker=side.blocker,
            viable=side.viable,
        ))
    return rows


def census_from_selection(
    selection: RewardMarketSelection,
    *,
    observed_at: datetime,
    scan_seconds: float,
) -> CensusResult:
    """Flatten one live selection pass into per-side census rows."""

    rows: list[CensusRow] = []
    for opportunity in selection.opportunities:
        rows.extend(_rows_for(opportunity, observed_at=observed_at))
    return CensusResult(
        observed_at=observed_at,
        scan_seconds=scan_seconds,
        catalog_markets=selection.catalog_markets,
        executable_catalog_markets=selection.executable_catalog_markets,
        rewarded_executable_markets=selection.rewarded_executable_markets,
        unsupported_programs=selection.unsupported_programs,
        catalog_spread_filtered_markets=(
            selection.catalog_spread_filtered_markets),
        eligible_pregame_markets=selection.eligible_pregame_markets,
        books_evaluated=selection.books_evaluated,
        viable_markets=selection.viable_markets,
        selected_slug=(
            selection.selected.market.slug
            if selection.selected is not None else None),
        deferred_markets=selection.deferred_markets,
        failures=selection.failures,
        rows=tuple(rows),
    )


def summarize(results: Iterable[CensusResult]) -> dict[str, Any]:
    """Reduce one or more passes to the numbers the decision turns on."""

    passes = list(results)
    rows = [row for result in passes for row in result.rows]
    priced = [row for row in rows if row.gate_ratio is not None]
    blockers: dict[str, int] = {}
    for row in rows:
        key = row.blocker or "none"
        blockers[key] = blockers.get(key, 0) + 1
    depths = sorted(row.touch_depth for row in rows)
    ratios = sorted(
        (row.gate_ratio for row in priced
         if row.gate_ratio not in (float("inf"),)),
        reverse=True)
    return {
        "passes": len(passes),
        "rows": len(rows),
        "distinct_markets": len({row.slug for row in rows}),
        "viable_rows": sum(1 for row in rows if row.viable),
        "viable_markets": sum(result.viable_markets for result in passes),
        "coverage_complete": all(
            result.coverage_complete for result in passes),
        "blockers": dict(sorted(
            blockers.items(), key=lambda item: (-item[1], item[0]))),
        "touch_depth_min": depths[0] if depths else None,
        "touch_depth_median": (
            depths[len(depths) // 2] if depths else None),
        "best_gate_ratio": ratios[0] if ratios else None,
        "best_max_total_score_for_entry": max(
            (row.max_total_score_for_entry for row in priced
             if row.max_total_score_for_entry is not None),
            default=None),
    }


def write_csv(path: Path, rows: Iterable[CensusRow]) -> int:
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(CensusRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return len(rows)


async def run_census(
    gateway: AsyncReadOnlyGateway,
    *,
    now: datetime,
    policy: RewardSelectionPolicy | None = None,
    selector: RewardMarketSelector | None = None,
) -> CensusResult:
    """Run one read-only pass of the live selector and flatten the result."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("census clock must be timezone-aware")
    selector = selector or RewardMarketSelector(
        gateway,
        policy or RewardSelectionPolicy(
            max_book_candidates=CENSUS_BOOK_CANDIDATES,
            enforce_reward_cost_gate=True),
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    selection = await selector.select(now=now)
    return census_from_selection(
        selection,
        observed_at=now,
        scan_seconds=loop.time() - started,
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        secrets = load_runtime_secrets(require_polymarket=True)
    except RuntimeSecretError as exc:
        raise SystemExit(str(exc)) from exc
    client = AsyncPolymarketUS(
        key_id=secrets.key_id, secret_key=secrets.secret_key,
        timeout=REST_REQUEST_TIMEOUT_SECONDS)
    gateway = AsyncReadOnlyGateway(
        client,
        limiter=shared_api_limiter(args.requests_per_second),
        default_traffic_class="report")
    selector = RewardMarketSelector(
        gateway,
        RewardSelectionPolicy(
            max_book_candidates=CENSUS_BOOK_CANDIDATES,
            enforce_reward_cost_gate=True),
    )
    results: list[CensusResult] = []
    try:
        for index in range(args.samples):
            if index:
                await asyncio.sleep(args.interval_seconds)
            result = await run_census(
                gateway,
                now=datetime.now(timezone.utc),
                selector=selector,
            )
            results.append(result)
            print(
                f"[census] pass {index + 1}/{args.samples} "
                f"at={result.observed_at.isoformat()} "
                f"scan={result.scan_seconds:.1f}s "
                f"executable={result.executable_catalog_markets} "
                f"rewarded={result.rewarded_executable_markets} "
                f"books={result.books_evaluated} "
                f"viable={result.viable_markets} "
                f"deferred={len(result.deferred_markets)}",
                flush=True,
            )
    finally:
        await client.close()

    rows = [row for result in results for row in result.rows]
    if args.csv:
        written = write_csv(Path(args.csv), rows)
        print(f"[census] wrote {written} rows to {args.csv}", flush=True)
    return summarize(results)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only census of rewarded markets against the live entry gate"))
    parser.add_argument("--samples", type=int, default=1,
                        help="number of scan passes")
    parser.add_argument("--interval-seconds", type=float, default=900.0,
                        help="delay between passes")
    parser.add_argument("--requests-per-second", type=float, default=4.0,
                        help="shared REST budget, never above the live cap")
    parser.add_argument("--csv", type=str, default=None,
                        help="optional path for per-side rows")
    args = parser.parse_args()
    if args.samples <= 0:
        parser.error("samples must be positive")
    if args.interval_seconds < 0:
        parser.error("interval-seconds cannot be negative")
    if not 0 < args.requests_per_second <= 4:
        parser.error("requests-per-second must be in (0, 4]")
    try:
        summary = asyncio.run(_run(args))
    except AdapterError as exc:
        parser.exit(1, f"read-only census failed: {exc}\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
