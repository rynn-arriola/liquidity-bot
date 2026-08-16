#!/usr/bin/env python3
"""Nightly dated snapshot of the execution ledger.

`data/commissioning.db` is the only record of every order, fill, position and
realised P&L this bot has produced. It lives in exactly one place. This makes a
dated copy and prunes old ones.

Uses SQLite's online backup API, not a file copy: the runner writes to this
database continuously in WAL mode, and `cp` of a live WAL database can capture a
torn page. `Connection.backup()` is safe against a concurrent writer and needs
no lock on the source.

    python3 scripts/backup_ledger.py [--db PATH] [--into DIR] [--keep N]

Cron it nightly. Note the limitation: a backup on the same droplet does not
survive losing the droplet. Pull these off periodically.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--into", default="backups/daily")
    parser.add_argument("--keep", type=int, default=30,
                        help="how many dated snapshots to retain")
    args = parser.parse_args()

    source = Path(args.db)
    if not source.exists():
        print(f"no ledger at {source}")
        return 1

    into = Path(args.into)
    into.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = into / f"commissioning-{stamp}.db"

    # Read-only on the source; the backup API still produces a consistent
    # snapshot because SQLite coordinates it internally.
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()

    size = target.stat().st_size
    # Prove the copy opens and carries rows, so a silently truncated file is
    # not mistaken for a backup.
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        orders = check.execute(
            "SELECT COUNT(*) FROM execution_orders").fetchone()[0]
        fills = check.execute(
            "SELECT COUNT(*) FROM execution_fills").fetchone()[0]
    finally:
        check.close()
    print(f"{target}  {size / 1e6:.1f}MB  orders={orders} fills={fills}")

    snapshots = sorted(into.glob("commissioning-*.db"))
    for stale in snapshots[:-args.keep] if args.keep > 0 else []:
        stale.unlink()
        print(f"pruned {stale.name}")
    print(f"retained {len(sorted(into.glob('commissioning-*.db')))} snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
