"""Private local handoff of the runner's latest proven trading mark."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path


def write_live_pnl_snapshot(
    path: Path,
    *,
    observed_at: datetime,
    risk_date: date,
    marked_pnl: Decimal,
    realized_pnl: Decimal | None,
    unrealized_pnl: Decimal | None,
    nontrading_cash: Decimal,
) -> None:
    """Atomically publish no-secret P&L data for the local TV dashboard."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("live P&L observation must be timezone-aware")
    values = (marked_pnl, nontrading_cash)
    if not all(value.is_finite() for value in values):
        raise ValueError("live P&L values must be finite")
    if (realized_pnl is None) != (unrealized_pnl is None):
        raise ValueError("live P&L decomposition must be complete or absent")
    if realized_pnl is not None:
        if not realized_pnl.is_finite() or not unrealized_pnl.is_finite():
            raise ValueError("live P&L decomposition must be finite")
        if realized_pnl + unrealized_pnl != marked_pnl:
            raise ValueError("live P&L decomposition does not reconcile")

    payload = {
        "schema_version": 1,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat()
        .replace("+00:00", "Z"),
        "risk_date_et": risk_date.isoformat(),
        "marked_pnl": format(marked_pnl, "f"),
        "realized_pnl": (
            format(realized_pnl, "f") if realized_pnl is not None else None),
        "unrealized_pnl": (
            format(unrealized_pnl, "f")
            if unrealized_pnl is not None else None),
        "nontrading_cash": format(nontrading_cash, "f"),
        "scope": "ACCOUNT_WIDE_TRADING",
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
