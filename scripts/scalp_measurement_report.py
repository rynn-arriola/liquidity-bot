#!/usr/bin/env python3
"""Report strict pair-fill and arbitrary early-exit scalp replays."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from liquidity_bot.scalp_report import build_scalp_report


def _horizons(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("horizons must be comma-separated seconds") from exc
    if not result or min(result) <= 0:
        raise argparse.ArgumentTypeError("horizons must be positive")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only scalp measurement report")
    parser.add_argument("--database", type=Path, default=Path("data/scalp.db"))
    parser.add_argument("--horizons", type=_horizons,
                        default=(30, 60, 300, 900, 3600))
    parser.add_argument("--depth-haircut", type=float, default=0.5)
    parser.add_argument("--maximum-book-age", type=float, default=30.0)
    parser.add_argument("--compact", action="store_true",
                        help="show collection health and strict cohort totals only")
    args = parser.parse_args()
    if not args.database.exists():
        parser.error(f"database does not exist: {args.database}")
    if not 0 < args.depth_haircut <= 1 or args.maximum_book_age <= 0:
        parser.error("depth haircut or maximum book age is invalid")
    report = build_scalp_report(
        args.database, args.horizons, depth_haircut=args.depth_haircut,
        maximum_book_age_seconds=args.maximum_book_age)
    if args.compact:
        report = {key: report[key] for key in
                  ("database", "counts", "coverage", "cohorts", "interpretation")}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
