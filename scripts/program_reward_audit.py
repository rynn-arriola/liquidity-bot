#!/usr/bin/env python3
"""Read-only reward economics audit grouped by shared reward program.

The public rewards page states that a program pool is shared by all markets in
that program.  This report therefore never adds a repeated ``reward_pool``
value once per market.  It groups exact order-resting intervals by program,
joins authenticated account earnings, and reports balanced-pair trading P&L.

The default performs one account-wide earnings read through the report traffic
lane.  ``--local-only`` uses the latest locally persisted earnings snapshot and
makes no network calls.  Neither mode writes the ledger or performs a venue
mutation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, "/home/polybot/liquidity-bot/src")

NEW_YORK = ZoneInfo("America/New_York")
WORKING_ORDER_STATES = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
BUY_LONG = "ORDER_INTENT_BUY_LONG"
BUY_SHORT = "ORDER_INTENT_BUY_SHORT"


def _parse_time(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _et_day(value: datetime) -> str:
    return value.astimezone(NEW_YORK).date().isoformat()


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, NEW_YORK)
    end = datetime.combine(day + timedelta(days=1), time.min, NEW_YORK)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _number(document: Mapping[str, Any], name: str) -> float | None:
    value = document.get(name)
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


@dataclass(frozen=True)
class ProgramSnapshot:
    market_slug: str
    side: str
    observed_at: datetime
    program_id: str
    reward_period: str
    reward_pool: float
    discount_factor: float | None
    target_size: float | None
    quantity: float | None
    ticks_from_best: float | None
    order_score: float | None
    total_score: float | None
    snapshot_share: float | None
    selection_rank: int | None

    @property
    def cohort(self) -> str:
        if self.selection_rank is None:
            return "rank_unknown"
        return "top24" if self.selection_rank <= 24 else "slots25plus"


@dataclass(frozen=True)
class RestingSpan:
    market_slug: str
    side: str
    start: datetime
    end: datetime
    snapshot: ProgramSnapshot


@dataclass
class WeightedFacts:
    seconds: float = 0.0
    quantity_seconds: float = 0.0
    target_seconds: float = 0.0
    discount_seconds: float = 0.0
    ticks_seconds: float = 0.0
    external_score_seconds: float = 0.0
    share_seconds: float = 0.0
    quantity_known: float = 0.0
    target_known: float = 0.0
    discount_known: float = 0.0
    ticks_known: float = 0.0
    external_score_known: float = 0.0
    share_known: float = 0.0
    pools: set[float] = field(default_factory=set)
    markets: set[str] = field(default_factory=set)

    def add(self, snapshot: ProgramSnapshot, seconds: float) -> None:
        self.seconds += seconds
        self.markets.add(snapshot.market_slug)
        self.pools.add(snapshot.reward_pool)
        for value, total_name, known_name in (
            (snapshot.quantity, "quantity_seconds", "quantity_known"),
            (snapshot.target_size, "target_seconds", "target_known"),
            (snapshot.discount_factor, "discount_seconds", "discount_known"),
            (snapshot.ticks_from_best, "ticks_seconds", "ticks_known"),
            (snapshot.snapshot_share, "share_seconds", "share_known"),
        ):
            if value is not None:
                setattr(self, total_name, getattr(self, total_name) + value * seconds)
                setattr(self, known_name, getattr(self, known_name) + seconds)
        if snapshot.total_score is not None and snapshot.order_score is not None:
            external = max(0.0, snapshot.total_score - snapshot.order_score)
            self.external_score_seconds += external * seconds
            self.external_score_known += seconds

    @staticmethod
    def average(total: float, known: float) -> float | None:
        return total / known if known > 0 else None


@dataclass
class ProgramDay:
    day: str
    program_id: str
    period: str
    facts: WeightedFacts = field(default_factory=WeightedFacts)
    paid: Decimal = Decimal("0")
    pending: Decimal = Decimal("0")
    trading: Decimal = Decimal("0")

    @property
    def complete_net(self) -> Decimal | None:
        return None if self.pending > 0 else self.paid + self.trading


@dataclass(frozen=True)
class AuditResult:
    programs: tuple[ProgramDay, ...]
    cohorts: Mapping[tuple[str, str, str], WeightedFacts]
    unmapped_earnings: Decimal
    ambiguous_earnings: Decimal
    unbalanced_markets: int
    observations_missing_program: int
    observations_missing_rank: int


def parse_snapshot(row: Mapping[str, Any]) -> ProgramSnapshot | None:
    try:
        document = json.loads(str(row["observation_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    program_id = str(document.get("program_id") or "").strip()
    slug = str(document.get("market_slug") or row.get("market_slug") or "").strip()
    side = str(document.get("side") or row.get("side") or "").strip().lower()
    observed_at = _parse_time(document.get("observed_at") or row.get("observed_at"))
    pool = _number(document, "reward_pool")
    if not program_id or not slug or side not in {"bid", "offer"}:
        return None
    if observed_at is None or pool is None:
        return None
    rank_raw = document.get("selection_rank")
    try:
        rank = int(rank_raw) if rank_raw is not None else None
    except (TypeError, ValueError):
        rank = None
    if rank is not None and rank <= 0:
        rank = None
    return ProgramSnapshot(
        market_slug=slug,
        side=side,
        observed_at=observed_at,
        program_id=program_id,
        reward_period=str(document.get("reward_period") or "unknown"),
        reward_pool=pool,
        discount_factor=_number(document, "discount_factor"),
        target_size=_number(document, "target_size"),
        quantity=_number(document, "quantity"),
        ticks_from_best=_number(document, "ticks_from_best"),
        order_score=_number(document, "order_score"),
        total_score=_number(document, "total_score"),
        snapshot_share=_number(document, "snapshot_share"),
        selection_rank=rank,
    )


def _latest_local_earnings(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return only rows from the latest snapshot for each market reward day."""

    rows = connection.execute(
        "SELECT date,market_slug,program_type,status,reward,observed_at "
        "FROM execution_reward_earnings ORDER BY observed_at"
    ).fetchall()
    latest: dict[tuple[str, str, str], str] = {}
    for row in rows:
        key = (str(row["date"])[:10], str(row["market_slug"]),
               str(row["program_type"]))
        latest[key] = max(latest.get(key, ""), str(row["observed_at"]))
    return [dict(row) for row in rows if str(row["observed_at"]) == latest[
        (str(row["date"])[:10], str(row["market_slug"]),
         str(row["program_type"]))]]


def _order_spans(
    connection: sqlite3.Connection, *, start: datetime, end: datetime,
) -> tuple[list[RestingSpan], int, int]:
    observations: dict[str, ProgramSnapshot] = {}
    malformed_observations: set[str] = set()
    for row in connection.execute(
        "SELECT client_order_id,market_slug,side,observed_at,observation_json "
        "FROM execution_quote_observations"
    ):
        snapshot = parse_snapshot(dict(row))
        if snapshot is None:
            malformed_observations.add(str(row["client_order_id"]))
            continue
        observations[str(row["client_order_id"])] = snapshot

    placed: dict[str, datetime] = {}
    for row in connection.execute(
        "SELECT client_order_id,created_at FROM execution_intents "
        "WHERE operation='PLACE' AND client_order_id IS NOT NULL"
    ):
        at = _parse_time(row["created_at"])
        key = str(row["client_order_id"])
        if at is not None and (key not in placed or at < placed[key]):
            placed[key] = at

    raw: dict[tuple[str, str, str], list[RestingSpan]] = defaultdict(list)
    missing_program = missing_rank = 0
    for row in connection.execute(
        "SELECT client_order_id,market_slug,status,event_at FROM execution_orders"
    ):
        order_id = str(row["client_order_id"])
        span_start = placed.get(order_id)
        if span_start is None:
            continue
        span_end = (end if str(row["status"]).upper() in WORKING_ORDER_STATES
                    else _parse_time(row["event_at"]))
        if span_end is None:
            continue
        span_start = max(span_start.astimezone(timezone.utc), start)
        span_end = min(span_end.astimezone(timezone.utc), end)
        if span_end <= span_start:
            continue
        snapshot = observations.get(order_id)
        if snapshot is None:
            if order_id in malformed_observations:
                missing_program += 1
            continue
        if snapshot.selection_rank is None:
            missing_rank += 1
        key = (snapshot.program_id, snapshot.market_slug, snapshot.side)
        raw[key].append(RestingSpan(
            snapshot.market_slug, snapshot.side, span_start, span_end, snapshot))

    merged: list[RestingSpan] = []
    for spans in raw.values():
        # Replacement acknowledgements can overlap. Slice at every boundary
        # and attribute each occupied segment to the newest active order. This
        # preserves one side-hour even if the selector rank changed between
        # the old order and its replacement.
        boundaries = sorted({point for item in spans
                             for point in (item.start, item.end)})
        for left, right in zip(boundaries, boundaries[1:]):
            active = [item for item in spans
                      if item.start < right and item.end > left]
            if not active or right <= left:
                continue
            owner = max(active, key=lambda item: (
                item.start, item.snapshot.observed_at))
            if (merged and merged[-1].end == left
                    and merged[-1].snapshot == owner.snapshot
                    and merged[-1].market_slug == owner.market_slug
                    and merged[-1].side == owner.side):
                prior = merged[-1]
                merged[-1] = RestingSpan(
                    prior.market_slug, prior.side, prior.start, right,
                    prior.snapshot)
            else:
                merged.append(RestingSpan(
                    owner.market_slug, owner.side, left, right, owner.snapshot))
    return merged, missing_program, missing_rank


def _balanced_trading_by_market_day(
    connection: sqlite3.Connection, *, since: datetime,
) -> tuple[dict[tuple[str, str], Decimal], int]:
    columns = {str(row[1]) for row in connection.execute(
        "PRAGMA table_info(execution_fills)")}
    active = "reversed=0" if "reversed" in columns else "1=1"
    market_days: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(
        lambda: {"long": Decimal("0"), "short": Decimal("0"),
                 "paid": Decimal("0")})
    fee_expression = "fee" if "fee" in columns else "'0' AS fee"
    for row in connection.execute(
        "SELECT market_slug,intent,qty,price,received_at," + fee_expression + " "
        "FROM execution_fills "
        f"WHERE {active} AND received_at>=? ORDER BY received_at",
        (since.isoformat(),),
    ):
        intent = str(row["intent"])
        if intent not in {BUY_LONG, BUY_SHORT}:
            continue
        parsed = _parse_time(row["received_at"])
        if parsed is None:
            continue
        qty = Decimal(str(row["qty"]))
        price = Decimal(str(row["price"]))
        key = (_et_day(parsed), str(row["market_slug"]))
        cell = market_days[key]
        cell["paid"] += Decimal(str(row["fee"] or "0"))
        if intent == BUY_LONG:
            cell["long"] += qty
            cell["paid"] += qty * price
        else:
            cell["short"] += qty
            cell["paid"] += qty * (Decimal("1") - price)

    result: dict[tuple[str, str], Decimal] = {}
    unbalanced = 0
    for (day, slug), cell in market_days.items():
        if cell["long"] <= 0 or cell["long"] != cell["short"]:
            unbalanced += 1
            continue
        result[(day, slug)] = cell["long"] - cell["paid"]
    return result, unbalanced


def build_audit(
    db: str | Path,
    earnings: Iterable[Mapping[str, Any]],
    *,
    days: int,
    now: datetime,
) -> AuditResult:
    if days <= 0:
        raise ValueError("days must be positive")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    last_day = now.astimezone(NEW_YORK).date()
    first_day = last_day - timedelta(days=days - 1)
    start, _ = _day_bounds(first_day)
    _, final_end = _day_bounds(last_day)
    end = min(now.astimezone(timezone.utc), final_end)

    connection = sqlite3.connect(f"file:{Path(db).resolve().as_posix()}?mode=ro",
                                 uri=True)
    connection.row_factory = sqlite3.Row
    try:
        spans, missing_program, missing_rank = _order_spans(
            connection, start=start, end=end)
        trading, unbalanced = _balanced_trading_by_market_day(
            connection, since=start)
    finally:
        connection.close()

    programs: dict[tuple[str, str], ProgramDay] = {}
    cohorts: dict[tuple[str, str, str], WeightedFacts] = defaultdict(WeightedFacts)
    market_programs: dict[tuple[str, str], set[str]] = defaultdict(set)

    for span in spans:
        cursor = span.start
        while cursor < span.end:
            local_day = cursor.astimezone(NEW_YORK).date()
            _, midnight = _day_bounds(local_day)
            stop = min(span.end, midnight)
            seconds = (stop - cursor).total_seconds()
            day = local_day.isoformat()
            key = (day, span.snapshot.program_id)
            item = programs.setdefault(key, ProgramDay(
                day=day, program_id=span.snapshot.program_id,
                period=span.snapshot.reward_period))
            item.facts.add(span.snapshot, seconds)
            cohorts[(day, span.snapshot.program_id,
                     span.snapshot.cohort)].add(span.snapshot, seconds)
            market_programs[(day, span.market_slug)].add(span.snapshot.program_id)
            cursor = stop

    unmapped = Decimal("0")
    ambiguous = Decimal("0")
    for row in earnings:
        day = str(row.get("date") or "")[:10]
        if day < first_day.isoformat() or day > last_day.isoformat():
            continue
        slug = str(row.get("market_slug") or "")
        status = str(row.get("status") or "").upper()
        try:
            reward = Decimal(str(row.get("reward") or "0"))
        except Exception:
            continue
        owners = market_programs.get((day, slug), set())
        if len(owners) == 0:
            unmapped += reward
            continue
        if len(owners) != 1:
            ambiguous += reward
            continue
        program = programs[(day, next(iter(owners)))]
        if status == "PAID":
            program.paid += reward
        elif status == "PENDING":
            program.pending += reward

    for (day, slug), pnl in trading.items():
        owners = market_programs.get((day, slug), set())
        if len(owners) == 1:
            programs[(day, next(iter(owners)))].trading += pnl

    return AuditResult(
        programs=tuple(sorted(programs.values(),
                              key=lambda item: (item.day, item.program_id))),
        cohorts=dict(cohorts),
        unmapped_earnings=unmapped,
        ambiguous_earnings=ambiguous,
        unbalanced_markets=unbalanced,
        observations_missing_program=missing_program,
        observations_missing_rank=missing_rank,
    )


async def fetch_earnings() -> list[dict[str, Any]]:
    """Make the report's sole venue read; imports stay lazy for pure tests."""

    from liquidity_bot.adapters import AsyncReadOnlyGateway, shared_api_limiter
    from liquidity_bot.probe import client_from_environment

    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(),
        default_traffic_class="report")
    rows = await gateway.fetch_earnings(program_type="liquidityProgram")
    return [{
        "date": str(row.date),
        "market_slug": str(row.market_slug),
        "program_type": str(row.program_type),
        "status": str(row.status),
        "reward": str(row.reward),
    } for row in rows]


def _fmt(value: float | None, digits: int = 2) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


def render(result: AuditResult) -> str:
    lines = [
        "program reward audit (ET reward days; pool is shared, never summed per market)",
        "",
        ("day        program                 period       mkts side-hr  pool  "
         "size  ext-score  share%   trading   paid pending      NET  $/hr"),
    ]
    for item in result.programs:
        facts = item.facts
        pool = median(facts.pools) if facts.pools else None
        size = facts.average(facts.quantity_seconds, facts.quantity_known)
        external = facts.average(
            facts.external_score_seconds, facts.external_score_known)
        share = facts.average(facts.share_seconds, facts.share_known)
        net = item.complete_net
        hours = facts.seconds / 3600
        net_per_hour = (
            float(net) / hours if net is not None and hours > 0 else None)
        lines.append(
            f"{item.day:10}  {item.program_id[:22]:22}  {item.period[:10]:10} "
            f"{len(facts.markets):4d} {hours:7.2f} "
            f"{_fmt(pool):>6} {_fmt(size):>5} {_fmt(external):>10} "
            f"{_fmt(None if share is None else share * 100, 4):>7} "
            f"{float(item.trading):9.2f} {float(item.paid):6.2f} "
            f"{float(item.pending):7.2f} "
            f"{('--' if net is None else f'{float(net):.2f}'):>8} "
            f"{_fmt(net_per_hour, 4):>7}")

    lines.extend(["", "virtual width exposure (exact only after selection_rank instrumentation):",
                  "day        program                 cohort       side-hr mkts avg-size"])
    for (day, program_id, cohort), facts in sorted(result.cohorts.items()):
        size = facts.average(facts.quantity_seconds, facts.quantity_known)
        lines.append(
            f"{day:10}  {program_id[:22]:22}  {cohort:12} "
            f"{facts.seconds / 3600:7.2f} {len(facts.markets):4d} {_fmt(size):>8}")

    lines.extend([
        "",
        "coverage:",
        f"  quote observations missing program facts: {result.observations_missing_program}",
        f"  quote observations missing selection_rank: {result.observations_missing_rank}",
        f"  reward not mapped to one observed program: ${float(result.unmapped_earnings):.2f}",
        f"  reward mapped to multiple observed programs: ${float(result.ambiguous_earnings):.2f}",
        f"  unbalanced fill markets excluded from trading P&L: {result.unbalanced_markets}",
        "  pending reward never enters NET; settled positions remain outside the trading lower bound.",
    ])
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    if args.local_only:
        connection = sqlite3.connect(
            f"file:{Path(args.db).resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            earnings = _latest_local_earnings(connection)
        finally:
            connection.close()
    else:
        earnings = await fetch_earnings()
    print(render(build_audit(args.db, earnings, days=args.days, now=now)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
