#!/usr/bin/env python3
"""Run the exact read-only GTC-to-settlement scalp counterfactual."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from liquidity_bot.scalp_gtc import GtcReplayConfig, build_gtc_scalp_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only fixed-GTC spread-scalp replay")
    parser.add_argument("--database", type=Path, default=Path("data/scalp.db"))
    parser.add_argument("--size", type=float, default=10.0)
    parser.add_argument("--minimum-spread", type=float, default=0.005)
    parser.add_argument("--feature-lookback", type=int, default=300)
    parser.add_argument("--minimum-feature-coverage", type=int, default=60)
    parser.add_argument("--early-exit", type=int, default=3600,
                        help="seconds after the first fill")
    parser.add_argument("--depth-haircut", type=float, default=0.5)
    parser.add_argument("--maximum-book-age", type=float, default=30.0)
    parser.add_argument("--cohorts", default="PURE_VOLUME_GTC",
                        help="comma-separated stored cohorts")
    args = parser.parse_args()
    if not args.database.exists():
        parser.error(f"database does not exist: {args.database}")
    try:
        config = GtcReplayConfig(
            quantity=args.size, minimum_spread=args.minimum_spread,
            feature_lookback_seconds=args.feature_lookback,
            minimum_feature_coverage_seconds=args.minimum_feature_coverage,
            early_exit_seconds=args.early_exit,
            depth_haircut=args.depth_haircut,
            maximum_book_age_seconds=args.maximum_book_age,
            cohorts=tuple(value.strip() for value in args.cohorts.split(",")
                          if value.strip()))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(build_gtc_scalp_report(args.database, config=config),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
