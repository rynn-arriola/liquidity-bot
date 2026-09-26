"""Human-readable Phase 2 database summary."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def build_report(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        runs = [dict(row) for row in connection.execute(
            "SELECT run_id,started_at,ended_at FROM shadow_runs ORDER BY started_at")]
        scores = [dict(row) for row in connection.execute(
            "SELECT day_et,market_slug,SUM(candidate_seconds) candidate_seconds,"
            "SUM(candidate_score_seconds) score_seconds "
            "FROM shadow_score_daily GROUP BY day_et,market_slug "
            "ORDER BY day_et,market_slug")]
        exits = [dict(row) for row in connection.execute(
            "SELECT horizon_seconds,COUNT(*) samples,AVG(pnl) mean_pnl,"
            "AVG(complete) completion_rate,AVG(loss_budget_pass) budget_pass_rate,"
            "MIN(pnl) worst_pnl,MAX(pnl) best_pnl "
            "FROM shadow_exit_replays GROUP BY horizon_seconds "
            "ORDER BY horizon_seconds")]
        earnings = [dict(row) for row in connection.execute(
            "SELECT status,COUNT(*) rows,SUM(reward) reward,"
            "SUM(attributable) attributable_rows FROM shadow_earnings "
            "GROUP BY status ORDER BY status")]
        counts = dict(connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM shadow_books) books,"
            "(SELECT COUNT(*) FROM shadow_trades) trades,"
            "(SELECT COUNT(*) FROM shadow_fill_signals WHERE role='entry') entry_signals,"
            "(SELECT COUNT(*) FROM shadow_quote_observations WHERE candidate=1) candidates"
        ).fetchone())
        attributable_paid = connection.execute(
            "SELECT COUNT(DISTINCT date) FROM shadow_earnings "
            "WHERE status='PAID' AND attributable=1").fetchone()[0]
        return {
            "database": str(path.resolve()),
            "runs": runs,
            "counts": counts,
            "score_by_et_day_market": scores,
            "exit_policies": exits,
            "earnings": earnings,
            "reward_calibration_ready": attributable_paid >= 3,
            "reward_calibration_note": (
                "Shadow orders do not earn venue rewards; calibration requires "
                "attributable PAID rows from a later controlled live trial."),
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Report a shadow evaluation database")
    parser.add_argument("--database", type=Path, default=Path("data/shadow.db"))
    args = parser.parse_args()
    if not args.database.exists():
        parser.error(f"database does not exist: {args.database}")
    print(json.dumps(build_report(args.database), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
