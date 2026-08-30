"""Private local handoff of the runner's currently bound market scope."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Iterable


ACTIVE_SCOPE_NAME = "RUNNER_ACTIVE_MARKETS"
ACTIVE_SCOPE_SCHEMA_VERSION = 1


def write_active_scope_snapshot(
    path: Path,
    *,
    observed_at: datetime,
    markets: Iterable[str],
    capacity: int,
    status: str = "RUNNING",
    publisher_pid: int | None = None,
) -> None:
    """Atomically publish a no-secret active-scope snapshot for the TV view."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("active-scope observation must be timezone-aware")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise ValueError("active-scope capacity must be a positive integer")
    if status not in {"RUNNING", "STOPPED"}:
        raise ValueError("active-scope status must be RUNNING or STOPPED")
    pid = os.getpid() if publisher_pid is None else publisher_pid
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        raise ValueError("active-scope publisher PID must be a positive integer")

    clean: list[str] = []
    seen: set[str] = set()
    for raw in markets:
        if not isinstance(raw, str):
            raise ValueError("active-scope markets must be strings")
        market = raw.strip()
        if not market or len(market) > 512:
            raise ValueError(
                "active-scope markets must be non-empty bounded strings")
        if market in seen:
            raise ValueError("active-scope markets must be unique")
        seen.add(market)
        clean.append(market)
    if len(clean) > capacity:
        raise ValueError("active-scope market count exceeds capacity")

    payload = {
        "schema_version": ACTIVE_SCOPE_SCHEMA_VERSION,
        "scope": ACTIVE_SCOPE_NAME,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat()
        .replace("+00:00", "Z"),
        "status": status,
        "publisher_pid": pid,
        "capacity": capacity,
        "markets": clean,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
