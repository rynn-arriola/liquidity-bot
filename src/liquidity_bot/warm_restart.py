"""Remember the active market scope so a restart can re-admit still-valid markets.

Operator decision 2026-09-26 (DECISIONS): a market already selected on a
qualifying 2-cent book keeps that status across a restart. The file is local,
ignored state next to the ledger; it is evidence of what this runner held, not
authority to trade. Re-admission applies the mandatory-retention checks with
fresh venue reads, and every order still passes the unchanged per-market gates.

No ledger schema change: rollback or older code simply ignores the file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCOPE_FILENAME = "warm-restart-scope.json"
SCHEMA = 1
# The day_of reward window is six hours; anything older cannot describe a
# market that is still inside its pregame reward period.
MAX_SCOPE_AGE_SECONDS = 21600.0
MAX_SCOPE_MARKETS = 100


def scope_path(ledger_path: Path) -> Path:
    return Path(ledger_path).parent / SCOPE_FILENAME


def write_scope(path: Path, slugs, *, now: datetime) -> None:
    """Atomically replace the remembered scope (write temp, then rename)."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scope clock must be timezone-aware")
    clean = list(dict.fromkeys(str(slug).strip() for slug in slugs if str(slug).strip()))
    payload = {"schema": SCHEMA,
               "written_at": now.astimezone(timezone.utc).isoformat(),
               "markets": clean[:MAX_SCOPE_MARKETS]}
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def read_scope(path: Path, *, now: datetime) -> tuple[tuple[str, ...], float]:
    """Return (slugs, age_seconds). FileNotFoundError or ValueError otherwise."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(raw, dict) or set(raw) != {"schema", "written_at", "markets"}
            or raw["schema"] != SCHEMA or not isinstance(raw["markets"], list)
            or not all(isinstance(slug, str) and slug.strip() for slug in raw["markets"])
            or len(raw["markets"]) > MAX_SCOPE_MARKETS):
        raise ValueError("malformed warm-restart scope")
    written = datetime.fromisoformat(str(raw["written_at"]))
    if written.tzinfo is None or written.utcoffset() is None:
        raise ValueError("warm-restart scope time is not timezone-aware")
    age = (now - written).total_seconds()
    if age < 0:
        raise ValueError("warm-restart scope is from the future")
    if age > MAX_SCOPE_AGE_SECONDS:
        raise ValueError("warm-restart scope is stale")
    return tuple(dict.fromkeys(slug.strip() for slug in raw["markets"])), age
