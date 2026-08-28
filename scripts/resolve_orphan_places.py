#!/usr/bin/env python3
"""Resolve PLACE intents the venue left with no exchange order id.

`POST /v1/orders` can return InternalServerError *without* an exchange order
id. `LiveOrderExecutor.place` then records the intent `UNCERTAIN` with
`exchange_order_id` NULL. Every resolution path in the ledger keys on the
exchange id -- `_upsert_order` resolves intents `WHERE exchange_order_id=?` --
so nothing can ever resolve it. The intent is unresolved forever, which arms
the account-wide `unresolved_mutation_timeout_seconds` stop and then refuses
the next startup with "execution ledger has unresolved intents".

Observed twice on 2026-08-18 (15:16Z and 20:53Z). See FINDINGS.md.

This is a stopgap for an operator, NOT the fix. The runner should reach the
same conclusion by itself; see NEXT.md.

Note what the fix CANNOT be: looking the order up by our client order id.
Polymarket retail order entry exposes no client-order-id field, so the id in
this ledger is purely local and was never sent -- see
`ExecutionLedger.resolve_client_order_id`. The exchange id returned by create
is the only ownership anchor there is, which is exactly why a create that
returns none leaves nothing to ask about.

Safety. It refuses unless ALL of these hold, per intent:

  * the intent is PLACE and UNCERTAIN with a NULL exchange order id
  * no `execution_orders` row exists for its client order id
  * the venue reports NO open order in that market
  * the venue reports NO non-zero position in that market
  * no runner process is alive

The last three are what make this safe rather than a guess: an order that was
actually created would appear as an open order, or as a position if it filled
instantly. Absence from both, with no id ever returned, is positive evidence
the create did not happen -- which is a different thing from reading absence
as cancellation, and is why this is limited to PLACE.

    python3 scripts/resolve_orphan_places.py            # report only
    python3 scripts/resolve_orphan_places.py --apply    # write
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.errors import AdapterError  # noqa: E402

DB = "data/commissioning.db"
ACTIVE = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}


def runner_is_alive() -> str:
    return subprocess.run(
        ["pgrep", "-f", "liquidity_bot.live_commissioning"],
        capture_output=True, text=True).stdout.strip()


def candidate_blockers(
    conn: sqlite3.Connection,
    row,
    *,
    live_clients: set[str],
    live_markets: set[str],
    held: set[str],
) -> list[str]:
    """Return every reason this candidate is unsafe to resolve."""

    client = str(row["client_order_id"])
    market = str(row["market_slug"])
    order = conn.execute(
        "SELECT status FROM execution_orders WHERE client_order_id=?",
        (client,)).fetchone()
    blockers: list[str] = []
    if order is not None:
        blockers.append(f"an order row exists (status {order['status']})")
    if client in live_clients:
        blockers.append("the venue reports it as a live order")
    # The local client id was never sent with a retail order, so a create that
    # actually landed would normally be adopted as ``venue:<id>``. Matching
    # only on the local id therefore cannot prove absence. Any live order in
    # the candidate market blocks repair, even when its exchange id is unknown
    # to this intent.
    if market in live_markets:
        blockers.append("the venue reports a live order in this market")
    if market in held:
        blockers.append("the venue holds a position in this market")
    return blockers


async def venue_state(attempts: int = 10, pause: float = 6.0):
    """Read the venue, retrying, and NEVER inferring emptiness from failure.

    This script exists for the aftermath of a venue throwing 500s, so a
    single-shot read is the wrong shape: measured 2026-08-18T21:00Z, plain
    `GET /v1/orders/open` failed 4 of 6 probes and the first version of this
    gave up immediately. It must retry.

    What it must not do is treat a failed read as "no orders" -- that would
    turn an unreadable venue into permission to mark intents FAILED, which
    is exactly the guess the guards exist to prevent. Exhausting the
    attempts raises, and the caller refuses.
    """

    from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
    from liquidity_bot.adapters import AsyncReadOnlyGateway
    from liquidity_bot.probe import client_from_environment

    account = AsyncAccountSnapshotGateway(
        AsyncReadOnlyGateway(client_from_environment()))
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            now = datetime.now(timezone.utc)
            orders = await account.fetch_open_orders(observed_at=now)
            positions = await account.fetch_positions(observed_at=now)
        except Exception as exc:
            last = exc
            print(f"  venue read attempt {attempt + 1}/{attempts} failed: "
                  f"{type(exc).__name__}")
            await asyncio.sleep(pause)
            continue
        live_clients = {
            str(o.client_order_id) for o in orders if o.status in ACTIVE}
        live_markets = {
            str(o.market_slug) for o in orders if o.status in ACTIVE}
        held = {p.market_slug for p in positions if p.net_qty != 0}
        return live_clients, live_markets, held
    raise AdapterError(
        f"venue unreadable after {attempts} attempts; refusing to resolve "
        f"anything on a guess (last: {last})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write the transition; default is report only")
    args = parser.parse_args()

    alive = runner_is_alive()
    if alive:
        print(f"REFUSED: a runner is alive (pids {alive.split()}). "
              "Stop it first -- this writes to the ownership path.")
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    candidates = conn.execute(
        "SELECT intent_id,market_slug,client_order_id,created_at,error "
        "FROM execution_intents WHERE state='UNCERTAIN' AND operation='PLACE' "
        "AND exchange_order_id IS NULL").fetchall()
    if not candidates:
        print("nothing to do: no orphan PLACE intents")
        return 0

    live_clients, live_markets, held = asyncio.run(venue_state())
    print(f"venue: {len(live_clients)} live order(s), "
          f"{len(held)} market(s) with a position\n")

    resolved = 0
    for row in candidates:
        client = str(row["client_order_id"])
        print(f"{row['intent_id']}  {row['market_slug']}")
        print(f"    client_order_id = {client}")
        print(f"    created_at      = {row['created_at']}")

        blockers = candidate_blockers(
            conn, row,
            live_clients=live_clients,
            live_markets=live_markets,
            held=held,
        )
        if blockers:
            print("    SKIP: " + "; ".join(blockers))
            continue

        print("    venue has no live order in this market and no position -- "
              "the create did not happen")
        if not args.apply:
            print("    would set state FAILED (dry run)")
            continue
        with conn:
            conn.execute(
                "UPDATE execution_intents SET state='FAILED',updated_at=?,"
                "error=? WHERE intent_id=? AND state='UNCERTAIN'",
                (datetime.now(timezone.utc).isoformat(),
                 "operator: venue 500 returned no exchange id; venue "
                 "confirmed no such order and no position",
                 row["intent_id"]))
        resolved += 1
        print("    -> FAILED")

    print(f"\n{resolved} resolved" if args.apply
          else f"\n{len(candidates)} candidate(s); re-run with --apply")
    print("remaining unresolved:", conn.execute(
        "SELECT COUNT(*) FROM execution_intents "
        "WHERE state NOT IN ('RECONCILED','FAILED')").fetchone()[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
