#!/usr/bin/env python3
"""One bounded read-only venue/ledger gate; never mutates trading state.

Used before every Command 1 start and by the status scheduler's guarded
restart/deploy scripts. Prints one JSON line. With ``--require-flat`` it exits
non-zero ("START REFUSED") unless the venue and the ledger are flat and clear.

    cd /home/polybot/liquidity-bot
    .venv/bin/python -B /mnt/c/Users/rynna/Bots/liquidity-bot/scripts/release_gate.py --positions --require-flat

The package is imported from ``./src`` of the working directory (the checkout
being gated), because credentials are located relative to that package; the
script itself may live in a different checkout.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

FLAT_KEYS = ("venue_orders", "venue_positions", "unresolved_intents",
             "unknown_orders", "ledger_working", "ledger_positions")


def gate_result(orders: Iterable[Any], positions: Iterable[Any], ledger: Any, *,
                observed_at: datetime, include_positions: bool) -> dict[str, Any]:
    """Summarize venue and ledger exposure; pure apart from ledger reads."""

    orders, positions = list(orders), list(positions)
    positioned = {p.market_slug for p in positions if p.net_qty != 0}
    result: dict[str, Any] = {
        "observed_at": observed_at.isoformat(),
        "venue_orders": len(orders),
        "venue_order_markets": len({o.market_slug for o in orders}),
        "venue_positions": sum(p.net_qty != 0 for p in positions),
        "venue_flat_market_orders": sum(o.market_slug not in positioned for o in orders),
        "venue_position_market_orders": sum(o.market_slug in positioned for o in orders),
        "unresolved_intents": len(ledger.unresolved_intents()),
        "unknown_orders": ledger.account_unknown_order_count(),
        "ledger_working": len(ledger.working_orders()),
        "ledger_positions": sum(Decimal(row[0]) != 0 for row in
                                ledger.query("SELECT net_qty FROM execution_positions")),
    }
    if include_positions:
        result["position_coverage"] = [
            {"market": p.market_slug, "quantity": str(p.net_qty),
             "orders": [{"side": o.side,
                         "remaining": str(o.original_qty - o.filled_qty),
                         "status": o.status}
                        for o in orders if o.market_slug == p.market_slug]}
            for p in positions if p.net_qty != 0]
    return result


def is_flat(result: dict[str, Any]) -> bool:
    return not any(result[key] for key in FLAT_KEYS)


async def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path.cwd() / "src"))
    from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
    from liquidity_bot.adapters import (
        AsyncReadOnlyGateway, OPPORTUNISTIC_REPORT_RATE_PER_SECOND, shared_api_limiter)
    from liquidity_bot.execution_ledger import ExecutionLedger
    from liquidity_bot.probe import client_from_environment

    account = AsyncAccountSnapshotGateway(AsyncReadOnlyGateway(
        client_from_environment(),
        limiter=shared_api_limiter(OPPORTUNISTIC_REPORT_RATE_PER_SECOND),
        default_traffic_class="report", max_pages=4))
    orders = await account.fetch_open_orders(observed_at=datetime.now(timezone.utc))
    positions = await account.fetch_positions(observed_at=datetime.now(timezone.utc))
    ledger = ExecutionLedger.open_read_only("data/commissioning.db")
    try:
        result = gate_result(orders, positions, ledger,
                             observed_at=datetime.now(timezone.utc),
                             include_positions="--positions" in argv)
    finally:
        ledger.close()
    print(json.dumps(result))
    if "--require-flat" in argv and not is_flat(result):
        print("START REFUSED: gate is not flat and clear", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
