"""Offline capacity sweep for the commissioning business workload."""

from __future__ import annotations

import argparse
from math import floor

from liquidity_bot.capacity import (
    ApiWorkload,
    ExposureProfile,
    HardwareProfile,
    assess_capacity,
    highest_comfortable_slot,
    simulate_request_schedule,
)


def _scenario(name: str, args: argparse.Namespace) -> ApiWorkload:
    shared = dict(
        rest_requests_per_second=args.rest_rps,
        selection_catalog_pages=args.catalog_pages,
        selection_incentive_pages=args.incentive_pages,
        selection_book_requests=args.selection_books,
        contract_batch_size=args.contract_batch_size,
        requested_headroom_fraction=args.request_headroom,
    )
    if name == "normal":
        return ApiWorkload(
            quiet_market_fraction=args.normal_quiet_fraction,
            mutation_requests_per_second=args.normal_mutation_rps,
            **shared,
        )
    if name == "busy":
        return ApiWorkload(
            quiet_market_fraction=args.busy_quiet_fraction,
            mutation_requests_per_second=args.busy_mutation_rps,
            execution_activity_pages=2,
            read_attempt_multiplier=args.busy_read_multiplier,
            **shared,
        )
    if name == "silent-feed-current":
        return ApiWorkload(
            quiet_market_fraction=1.0,
            mutation_requests_per_second=0.0,
            execution_activity_pages=2,
            selection_catalog_pages=0,
            selection_incentive_pages=0,
            selection_book_requests=0,
            rest_requests_per_second=args.rest_rps,
            requested_headroom_fraction=args.request_headroom,
            contract_batch_size=args.contract_batch_size,
        )
    raise ValueError(f"unknown capacity scenario: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline, mutation-free liquidity-bot capacity model"
    )
    parser.add_argument("--minimum", type=int, default=8)
    parser.add_argument("--maximum", type=int, default=48)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--rest-rps", type=float, default=4.0)
    parser.add_argument("--request-headroom", type=float, default=0.25)
    parser.add_argument("--normal-quiet-fraction", type=float, default=0.10)
    parser.add_argument("--busy-quiet-fraction", type=float, default=0.25)
    parser.add_argument("--normal-mutation-rps", type=float, default=0.10)
    parser.add_argument("--busy-mutation-rps", type=float, default=0.25)
    parser.add_argument("--busy-read-multiplier", type=float, default=1.05)
    parser.add_argument("--catalog-pages", type=int, default=52)
    parser.add_argument("--incentive-pages", type=int, default=4)
    parser.add_argument("--selection-books", type=int, default=60)
    parser.add_argument("--contract-batch-size", type=int, default=8)
    parser.add_argument("--response-seconds", type=float, default=0.0)
    parser.add_argument("--baseline-slots", type=int, default=20)
    parser.add_argument("--baseline-runner-cpu", type=float, default=0.564)
    parser.add_argument("--comfortable-runner-cpu", type=float, default=0.75)
    parser.add_argument("--portfolio-cushion-units", type=float, default=10.0)
    parser.add_argument("--exposure-cushion", type=float, default=0.25)
    args = parser.parse_args()
    if args.minimum <= 0 or args.maximum < args.minimum or args.step <= 0:
        parser.error("capacity sweep bounds must be positive and ordered")

    hardware = HardwareProfile(
        baseline_slots=args.baseline_slots,
        baseline_runner_cpu_cores=args.baseline_runner_cpu,
        comfortable_runner_cpu_cores=args.comfortable_runner_cpu,
    )
    exposure = ExposureProfile(
        portfolio_cushion_quote_units=args.portfolio_cushion_units,
        required_cushion_over_slots=args.exposure_cushion,
    )

    print(
        "scenario             slots  REST/s  util   CPU-core  private  "
        "exposure-cushion  misses  backlog  verdict"
    )
    for name in ("normal", "busy", "silent-feed-current"):
        workload = _scenario(name, args)
        for slots in range(args.minimum, args.maximum + 1, args.step):
            item = assess_capacity(
                slots, workload, hardware=hardware, exposure=exposure
            )
            replay = simulate_request_schedule(
                slots,
                workload,
                duration_seconds=300,
                response_seconds=args.response_seconds,
            )
            schedule_safe = (
                replay.missed_deadlines == 0
                and replay.remaining_requests == 0
            )
            print(
                f"{name:<20} {slots:>5}  "
                f"{item.attempted_requests_per_second:>6.2f}  "
                f"{item.request_utilization:>4.0%}  "
                f"{item.projected_runner_cpu_cores:>8.3f}  "
                f"{item.private_connections:>7}  "
                f"{item.exposure_cushion_over_slots:>15.0%}  "
                f"{replay.missed_deadlines:>6}  "
                f"{replay.remaining_requests:>7}  "
                f"{'PASS' if item.comfortable and schedule_safe else 'FAIL'}"
            )
        passing = []
        for slots in range(1, 101):
            item = assess_capacity(
                slots, workload, hardware=hardware, exposure=exposure)
            if not item.comfortable:
                continue
            replay = simulate_request_schedule(
                slots,
                workload,
                duration_seconds=300,
                response_seconds=args.response_seconds,
            )
            if replay.missed_deadlines == 0 and replay.remaining_requests == 0:
                passing.append(slots)
        print(
            f"{name} highest all-factor schedule-safe: "
            f"{max(passing) if passing else None}"
        )
        api_ceiling = highest_comfortable_slot(
            workload,
            minimum=1,
            maximum=100,
            exposure=ExposureProfile(
                portfolio_cushion_quote_units=0,
                required_cushion_over_slots=0,
            ),
        )
        print(
            f"{name} factor ceilings: REST/public={api_ceiling} "
            f"CPU={floor((args.comfortable_runner_cpu / args.baseline_runner_cpu) * args.baseline_slots)} "
            f"exposure={floor(args.portfolio_cushion_units / args.exposure_cushion) if args.exposure_cushion else 'unbounded'} "
            "public-subscription=100"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
