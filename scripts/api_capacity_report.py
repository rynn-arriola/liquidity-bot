"""Project observed limiter windows to a proposed slot width.

This is a local, read-only report. It reads only the runner's secret-free log,
opens no credential or database and makes no venue request.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from statistics import median
from typing import Mapping

from liquidity_bot.capacity import (
    ApiBudgetWindow,
    parse_api_budget_line,
    project_api_budget_window,
)


_LOG_TAG = re.compile(r"^\[([^]]+)\]")


@dataclass(frozen=True)
class AttributedApiWindow:
    """One limiter window plus secret-free causal signals from its interval."""

    observed_at: str
    window: ApiBudgetWindow
    signal_counts: Mapping[str, int]
    drivers: tuple[str, ...]


_RECOVERY_SIGNALS = frozenset({
    "bound-stream-recovery",
    "private-shard-recovery",
    "quarantine",
    "runtime-recovery",
    "venue-recovery-pause",
})
_SELECTION_SIGNALS = frozenset({
    "selection",
    "top-up",
    "top-up-cache",
    "top-up-plan",
    "top-up-request",
})


def _field(line: str, name: str, default: str = "unknown") -> str:
    prefix = f"{name}="
    for token in line.split():
        if token.startswith(prefix):
            return token[len(prefix):]
    return default


def _burst_drivers(
    window: ApiBudgetWindow,
    signal_counts: Mapping[str, int],
) -> tuple[str, ...]:
    """Classify a window without claiming causality beyond logged evidence."""

    drivers: list[str] = []
    signals = set(signal_counts)
    categories = window.attempts_by_category
    attempts = max(1, window.attempts)
    market_books = categories.get("market_book", 0)
    mutations = sum(
        count for name, count in categories.items()
        if name.startswith("mutation_")
    )
    account_reads = sum(
        categories.get(name, 0)
        for name in (
            "balance", "cash_history", "execution_history", "open_orders",
            "positions",
        )
    )

    if "maintenance-pause" in signals:
        drivers.append("venue maintenance")
    if signals & _RECOVERY_SIGNALS:
        drivers.append("runtime recovery")
    if signals & _SELECTION_SIGNALS:
        drivers.append("selection/refill")
    if "unresolved-repair" in signals or categories.get("order_retrieve", 0) >= 3:
        drivers.append("ambiguity repair")
    if "market-retired" in signals:
        drivers.append("market lifecycle")
    if mutations >= 8 and mutations / attempts >= 0.08:
        drivers.append("mutation churn")
    if market_books / attempts >= 0.50:
        # market_book covers both ordinary freshness reads and the quiet-book
        # REST fallback. Context signals say which mechanism was present; keep
        # the label honest when the log cannot separate them further.
        drivers.append(
            "quiet-book fallback"
            if "book-stale-why" in signals
            else "book refresh"
        )
    if account_reads / attempts >= 0.30:
        drivers.append("account proofs/reconciliation")
    return tuple(drivers or ("steady mixed workload",))


def _attributed_windows(
    path: Path,
    *,
    source_slots: int,
    last: int,
) -> list[AttributedApiWindow]:
    """Return current windows with signals emitted since the prior window."""

    rows: list[AttributedApiWindow] = []
    signals: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "[api-budget]" not in line:
            match = _LOG_TAG.match(line)
            if match:
                signals[match.group(1)] += 1
            continue
        # An API line closes the interval even when it is legacy evidence or
        # a different width. Never leak its signals into the next interval.
        if " slots=" in line:
            row = parse_api_budget_line(line)
            if row is not None and row.slots == source_slots:
                rows.append(AttributedApiWindow(
                    observed_at=_field(line, "at"),
                    window=row,
                    signal_counts=dict(signals),
                    drivers=_burst_drivers(row, signals),
                ))
        signals.clear()
    return rows[-last:]


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + .999999) - 1))
    return ordered[index]


def _windows(path: Path, *, source_slots: int, last: int) -> list[ApiBudgetWindow]:
    return [
        item.window
        for item in _attributed_windows(
            path, source_slots=source_slots, last=last)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only empirical API-capacity projection")
    parser.add_argument("--log", type=Path, default=Path("logs/commissioning.log"))
    parser.add_argument("--source-slots", type=int, default=24)
    parser.add_argument("--target-slots", type=int, default=32)
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--host-rps", type=float, default=4.0)
    parser.add_argument("--comfort-rps", type=float, default=3.0)
    parser.add_argument("--hourly-report-requests", type=int, default=12)
    parser.add_argument("--show-bursts", type=int, default=8)
    parser.add_argument("--burst-rps", type=float, default=2.25)
    args = parser.parse_args()
    if min(
        args.source_slots, args.target_slots, args.last, args.show_bursts,
    ) <= 0:
        parser.error("slot counts, --last and --show-bursts must be positive")
    if args.host_rps <= 0 or not 0 < args.comfort_rps <= args.host_rps:
        parser.error("request budgets are invalid")
    if args.burst_rps <= 0:
        parser.error("--burst-rps must be positive")

    evidence = _attributed_windows(
        args.log, source_slots=args.source_slots, last=args.last)
    windows = [item.window for item in evidence]
    if not windows:
        parser.error("no matching API budget windows were found")
    projections = [
        project_api_budget_window(
            row,
            args.target_slots,
            scheduled_report_requests=args.hourly_report_requests,
        )
        for row in windows
    ]
    observed_rps = [row.attempts / row.elapsed_seconds for row in windows]
    projected_rps = [row.projected_host_average_rps for row in projections]
    coincident_rps = [
        row.projected_host_coincident_report_rps for row in projections]
    categories: Counter[str] = Counter()
    seconds = sum(row.elapsed_seconds for row in windows)
    for row in windows:
        categories.update(row.attempts_by_category)

    print(
        f"API capacity evidence: {len(windows)} windows, "
        f"{seconds:.1f}s, {args.source_slots} -> {args.target_slots} slots")
    print(
        "metric                       observed      projected")
    print(
        f"median requests/s            {median(observed_rps):>8.3f}      "
        f"{median(projected_rps):>8.3f}")
    print(
        f"p95 requests/s               {_percentile(observed_rps, .95):>8.3f}      "
        f"{_percentile(projected_rps, .95):>8.3f}")
    print(
        f"maximum requests/s           {max(observed_rps):>8.3f}      "
        f"{max(projected_rps):>8.3f}")
    print(
        f"max with hourly report                       "
        f"{max(coincident_rps):>8.3f}")
    print(
        f"maximum limiter queue        {max(row.max_waiting for row in windows):>8}")
    print(
        "  queue peak includes concurrent read admission; compare configured "
        "readers, actual wait times and drained queues before calling overload")
    print(
        f"failures / confirmed 429s    "
        f"{sum(row.failures for row in windows)} / "
        f"{sum(row.rate_limited for row in windows)}")
    print()
    print("observed category rates")
    for category, attempts in categories.most_common():
        print(f"  {category:<28} {attempts / seconds:>7.3f}/s")
    print()
    print("highest call windows and logged drivers")
    ranked = sorted(
        evidence,
        key=lambda value: (
            value.window.attempts / value.window.elapsed_seconds,
            value.observed_at,
        ),
        reverse=True,
    )
    bursts = [
        item for item in ranked
        if item.window.attempts / item.window.elapsed_seconds >= args.burst_rps
    ]
    print(
        f"  threshold={args.burst_rps:.3f}/s windows={len(bursts)}/"
        f"{len(evidence)}")
    for item in ranked[:args.show_bursts]:
        row = item.window
        rps = row.attempts / row.elapsed_seconds
        level = "BURST" if rps >= args.burst_rps else "normal"
        top_categories = ",".join(
            f"{name}:{count}"
            for name, count in Counter(row.attempts_by_category).most_common(4)
        )
        print(
            f"  {item.observed_at:<32} {rps:>5.3f}/s {level:<6} "
            f"drivers={'+'.join(item.drivers)}")
        print(f"    categories={top_categories or 'none'}")
        top_signals = ",".join(
            f"{name}:{count}"
            for name, count in Counter(item.signal_counts).most_common(5)
        )
        print(f"    signals={top_signals or 'none'}")
    burst_drivers: Counter[str] = Counter(
        driver for item in bursts for driver in item.drivers)
    print(
        "  burst-driver frequency="
        + (", ".join(
            f"{name}:{count}"
            for name, count in burst_drivers.most_common()
        ) or "none")
    )
    print()
    print("safe burst controls")
    print(
        "  every authenticated call: shared 4/s host gate smooths demand; "
        "queue/latency reveal pressure that raw 429 counts can miss")
    print(
        "  book refresh/fallback: prefer stream freshness; bound empty/dead "
        "market retries, but always service positions")
    print(
        "  selection/refill: cache, batch and coalesce discovery; preserve "
        "mandatory slot replacement")
    print(
        "  ambiguity repair: retrieve the exact order until terminal proof; "
        "never guess or suppress it")
    print(
        "  mutation churn: replan/coalesce entries; never delay cancels, "
        "reducers or IOC exits")
    print(
        "  recovery/account proofs: fail closed and keep safety priority; "
        "roll slot width back if these contend")
    print(
        "  scheduled reports: use only the opportunistic report lane and "
        "yield to live traffic")
    print()
    projected_max = max(projected_rps)
    if projected_max <= args.comfort_rps:
        verdict = "PROJECTED NORMAL-WINDOW HEADROOM"
    elif projected_max <= args.host_rps:
        verdict = "CANARY ONLY: BELOW HARD GATE, ABOVE COMFORT BUDGET"
    else:
        verdict = "REFUSE: PROJECTED HARD-GATE SATURATION"
    print(f"VERDICT: {verdict}")
    print(
        "This projection includes the separate hourly report but cannot "
        "prove degraded-stream or recovery bursts; use the deterministic "
        "stress model and live rollback gates too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
