#!/usr/bin/env python3
"""One-off deep backfill of venue fills into the execution ledger.

`fetch_executions` reads two pages per sweep on purpose: it runs on every
reconcile and shares the one 4 req/s governor with quoting, so walking history
there would spend the budget the bot needs to rest orders. That keeps the
ledger current but means it never *repairs* itself -- fills written while a
recording defect was live stay missing forever, and no amount of running the
bot fixes them.

This walks the whole activity history once and applies anything the ledger does
not already hold. `apply_execution` dedupes on execution id, so replaying is
safe and re-running this is a no-op.

**READ THIS BEFORE RUNNING IT AGAIN.** Run bare on 2026-08-17 it did real
damage, and the damage was not the fills:

  * The venue's history is the **account's**, not this bot's. It reached back to
    June and imported ~700 markets of maker-lab trading at 1000+ contracts a
    side, against this bot's bounded 5-30 range. There is no field that
    separates them, so
    "backfill everything" cannot produce a liquidity-bot-only ledger.
  * `apply_execution` rebuilds **positions**, not just fills. Replaying the
    fills of a market that has since settled leaves the ledger holding a
    position the venue does not have -- 345 of them, in markets settled days
    earlier. A runner that believes it holds inventory plans *exits* for it, so
    this turns a reporting problem into real orders against phantom positions.

The ledger was restored from the backup below and the bot restarted flat. The
honest fix is a mode that writes fills **without** touching positions, which
the ledger API does not currently expose. Until that exists, treat this script
as diagnostic: `--dry-run` is safe, a real run is not, and the check at the end
will tell you if you have just created phantom inventory.

Two hard requirements, both enforced below:

  1. **The runner must be stopped.** This writes to the ledger the runner owns,
     and paging full history is exactly what triggered venue rate limiting
     before. A stopped bot has the whole request budget and nothing to lose.
  2. **The ledger is backed up first**, through SQLite's online backup API, to
     `backups/backfill-<timestamp>.db`. Never `cp` a WAL database.

    python3 scripts/backfill_executions.py            # backfill
    python3 scripts/backfill_executions.py --dry-run  # count, write nothing
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway  # noqa: E402
from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, shared_api_limiter)
from liquidity_bot.execution_ledger import ExecutionLedger, LedgerConflict  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402


def _runner_is_alive() -> bool:
    proc = Path("/proc")
    if not proc.is_dir():          # not Linux; caller must judge for itself
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"liquidity_bot.live_commissioning" in raw:
            return True
    return False


def _backup(db: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = db.parent.parent / "backups" / f"backfill-{stamp}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dest = sqlite3.connect(str(target))
    with dest:
        source.backup(dest)        # online backup API, safe against a writer
    source.close()
    dest.close()
    return target


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/commissioning.db")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if _runner_is_alive():
        print("REFUSED: liquidity_bot.live_commissioning is running.")
        print("  Stop it first (scripts/stop_commissioning.sh). This writes to")
        print("  the ledger the runner owns, and paging full venue history")
        print("  competes with quoting for the one 4 req/s budget.")
        return 2

    db = Path(args.db)
    if not db.exists():
        print(f"REFUSED: {db} does not exist")
        return 2

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    before = conn.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0]
    conn.close()
    print(f"ledger holds {before} fills before backfill")

    if not args.dry_run:
        backup = _backup(db)
        print(f"ledger backed up -> {backup}")

    now = datetime.now(timezone.utc)
    ledger = ExecutionLedger(db)
    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(),
        default_traffic_class="report")
    account = AsyncAccountSnapshotGateway(
        gateway, resolve_client_id=ledger.resolve_client_order_id)

    print(f"walking activity history (up to {args.max_pages} pages)...")
    executions = await account.fetch_executions(
        observed_at=now, max_pages=args.max_pages)
    print(f"venue returned {len(executions)} of our executions")

    added = conflicts = 0
    if args.dry_run:
        print("(dry run -- nothing written)")
    else:
        for execution in executions:
            try:
                if ledger.apply_execution(execution, received_at=now):
                    added += 1
            except LedgerConflict:
                # Already recorded. The dedupe hashes the whole payload, so a
                # replay of a fill written before a field was added looks like
                # a conflicting rewrite. It is not -- the execution id is the
                # identity, and we already hold it.
                conflicts += 1
        reversed_trades = sum(
            int(ledger.apply_trade_state(state, received_at=now))
            for state in executions.trade_states)
        print(f"applied {added} new fills ({conflicts} already held)")
        print(
            f"applied/repaired {reversed_trades} trade-state observation(s)")

    ledger.close()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    after = conn.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0]
    print(f"ledger holds {after} fills after backfill  (+{after - before})")

    # The check that would have caught the 2026-08-17 damage before the bot was
    # restarted. Replaying the fills of a settled market rebuilds a position
    # the venue does not have, and the runner will plan exits against it.
    held = conn.execute("SELECT COUNT(*) FROM execution_positions "
                        "WHERE ABS(net_qty) > 1e-9").fetchone()[0]
    if held and not args.dry_run:
        print("")
        print(f"  *** WARNING: the ledger now holds {held} non-zero positions.")
        print("  *** Compare against the VENUE before starting the runner. Any")
        print("  *** market that has settled is phantom inventory, and the")
        print("  *** runner will place real exit orders against it.")
        print("  *** Restore the backup above if they do not match the venue.")
    print("")
    print("active fills per day, with the MAKER share -- a day well below the others")
    print("is a day whose passive legs are still missing:")
    for row in conn.execute(
            "SELECT substr(event_at,1,10) d, COUNT(*) n, "
            "SUM(CASE WHEN liquidity='MAKER' THEN 1 ELSE 0 END) mk "
            "FROM execution_fills WHERE reversed=0 GROUP BY d ORDER BY d"):
        share = (100.0 * row[2] / row[1]) if row[1] else 0.0
        print(f"   {row[0]}  fills={row[1]:<5d} maker={row[2]:<5d} ({share:.0f}%)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
