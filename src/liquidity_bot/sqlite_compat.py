"""Read-only SQLite compatibility helpers for rolling deployments."""

from __future__ import annotations

import sqlite3


def active_fills_predicate(
    connection: sqlite3.Connection, *, alias: str | None = None,
) -> str:
    """Filter busted fills when migrated, and remain valid before migration."""

    columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(execution_fills)")
    }
    if "reversed" not in columns:
        return "1=1"
    prefix = f"{alias}." if alias else ""
    return f"{prefix}reversed=0"
