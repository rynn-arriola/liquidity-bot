#!/usr/bin/env python3
"""Combo Builder - phase 1, QUOTE-ONLY (BC-2026-10-03-C).

Operator 2026-10-03: "You think we can do a combo builder where we get markets from live
that has 90-99", "This is a separare work from what we doing right now but we can use
the same markets so we dont waste market scans".

Reads the Endgame Trader's shared scan (data/late-favorite/candidates.json, written every
cycle, no extra market calls). When 2+ late-stage favourites from different events are
live at the same moment, it creates the combo (POST /v1/combos), asks market makers for a
price (POST /v1/rfqs), records every quote for a few seconds (GET /v1/rfqs/quotes) and
deletes the RFQ (DELETE /v1/rfqs/{id}). It NEVER accepts or confirms a quote, so it never
trades. Results: data/combo-builder/probes.db; summary: `combo_builder.py report`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CANDIDATES = Path("data/late-favorite/candidates.json")
OUT = Path("data/combo-builder")
DB = OUT / "probes.db"
CRON_MARK = "# liquidity-bot combo builder"
MIN_LEG_MID = 0.95          # leg quality for a combo (operator band 90-99; start at 95)
MAX_LEGS = 5                # operator: "Im thinking 5 legs" (was 3)
QTY = "10"                  # contracts asked for in the quote request
QUOTE_WINDOW_SECONDS = 12
MIN_GAP_SECONDS = 120       # at most one probe every 2 minutes
SAME_COMBO_GAP_MINUTES = 15
MAX_PROBES_PER_DAY = 60     # weekly venue quota is 1,000 new combos (all users share it)
POLL_SECONDS = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS probes(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, legs_json TEXT NOT NULL, fair REAL,
  combo_symbol TEXT, rfq_id TEXT, quotes INTEGER, best_buy REAL, best_sell REAL,
  first_quote_seconds REAL, error TEXT, quotes_json TEXT);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    print(f"[combo-builder] at={now_utc().isoformat()} {msg}", flush=True)


def open_db() -> sqlite3.Connection:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def pick_legs(candidates: list[dict], min_mid: float = MIN_LEG_MID, max_legs: int = MAX_LEGS) -> list[dict]:
    """Strongest favourites from different events (one leg per event)."""
    legs, seen = [], set()
    for c in sorted(candidates, key=lambda c: -(c.get("fav_mid") or 0)):
        if (c.get("fav_mid") or 0) < min_mid or c["event"] in seen:
            continue
        seen.add(c["event"])
        legs.append(c)
        if len(legs) == max_legs:
            break
    return legs if len(legs) >= 2 else []


def fair_price(legs: list[dict]) -> float:
    """All legs must win: the product of the favourites' middle prices."""
    return math.prod(leg["fav_mid"] for leg in legs)


def _price(value):
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Builder:
    def __init__(self):
        self.db = open_db()

    def recent(self, key: str) -> bool:
        since = (now_utc() - timedelta(minutes=SAME_COMBO_GAP_MINUTES)).isoformat()
        return bool(self.db.execute("SELECT 1 FROM probes WHERE legs_json=? AND at>=?", (key, since)).fetchone())

    def probes_today(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM probes WHERE at>=?",
                               ((now_utc() - timedelta(hours=24)).isoformat(),)).fetchone()[0]

    async def probe(self, gateway, client, legs: list[dict]) -> None:
        key = json.dumps([[leg["market"], leg["side"]] for leg in sorted(legs, key=lambda l: l["market"])])
        fair = fair_price(legs)
        row = {"at": now_utc().isoformat(), "legs_json": key, "fair": fair}
        rfq_id = None
        try:
            body = {"legs": [{"symbol": leg["market"], "side": "SIDE_BUY" if leg["side"] == "long" else "SIDE_SELL"}
                             for leg in legs]}
            combo = await gateway._call("POST /v1/combos", lambda: client.post("/v1/combos", body=body,
                                                                              authenticated=True))
            info = combo.get("combo", combo) if isinstance(combo, dict) else {}
            symbol = info.get("symbol") or info.get("id")
            row["combo_symbol"] = symbol
            if not symbol:
                raise RuntimeError(f"no combo symbol in response: {str(combo)[:200]}")
            started = time.monotonic()
            rfq = await gateway._call("POST /v1/rfqs", lambda: client.post(
                "/v1/rfqs", body={"symbol": symbol, "qtyDecimal": QTY, "restRemainder": False},
                authenticated=True))
            rfq_id = rfq.get("rfqId") if isinstance(rfq, dict) else None
            row["rfq_id"] = rfq_id
            if not rfq_id:
                raise RuntimeError(f"no rfq id in response: {str(rfq)[:200]}")
            quotes, first = {}, None
            while time.monotonic() - started < QUOTE_WINDOW_SECONDS:
                await asyncio.sleep(1.5)
                got = await gateway._call("GET /v1/rfqs/quotes", lambda: client.get(
                    "/v1/rfqs/quotes", query={"rfqId": rfq_id}, authenticated=True))
                for q in (got.get("quotes") if isinstance(got, dict) else None) or []:
                    if isinstance(q, dict) and q.get("id"):
                        if q["id"] not in quotes and first is None:
                            first = time.monotonic() - started
                        quotes[q["id"]] = q
            buys = [p for p in (_price(q.get("buyPrice")) for q in quotes.values()) if p]
            sells = [p for p in (_price(q.get("sellPrice")) for q in quotes.values()) if p]
            row.update(quotes=len(quotes), best_buy=min(buys) if buys else None,
                       best_sell=max(sells) if sells else None, first_quote_seconds=first,
                       quotes_json=json.dumps(list(quotes.values()))[:20000])
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"[:400]
        finally:
            if rfq_id:
                try:
                    await gateway._call("DELETE /v1/rfqs", lambda: client.delete(
                        f"/v1/rfqs/{rfq_id}", authenticated=True))
                except Exception as exc:
                    row["error"] = (row.get("error") or "") + f" | delete failed {type(exc).__name__}"
            cols = ",".join(row)
            with self.db:
                self.db.execute(f"INSERT INTO probes({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
        log(f"probe legs={len(legs)} fair={fair:.3f} quotes={row.get('quotes')} best_buy={row.get('best_buy')} "
            f"error={row.get('error')}")

    async def run(self, minutes: float | None) -> int:
        from liquidity_bot.adapters import AsyncReadOnlyGateway, shared_api_limiter
        from liquidity_bot.probe import client_from_environment
        from liquidity_bot.runtime_safety import SingleInstanceLease
        if not Path("data/commissioning.db").exists():
            raise SystemExit("run from the live bot directory")
        lease = SingleInstanceLease(OUT / "builder.lease")
        lease.acquire()
        client = client_from_environment()
        gateway = AsyncReadOnlyGateway(
            client, limiter=shared_api_limiter(1.0, state_path=Path.cwd() / "data" / "api-rate-limiter.state"),
            default_traffic_class="background", retry_attempts=1)
        stop_at = now_utc() + timedelta(minutes=minutes) if minutes else None
        last_probe = 0.0
        log("start (quote-only; never accepts)")
        while stop_at is None or now_utc() < stop_at:
            try:
                data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
                age = (now_utc() - datetime.fromisoformat(data["at"].replace("Z", "+00:00"))).total_seconds()
                legs = pick_legs(data.get("candidates", [])) if age <= 30 else []
                if (legs and time.monotonic() - last_probe >= MIN_GAP_SECONDS
                        and self.probes_today() < MAX_PROBES_PER_DAY):
                    key = json.dumps([[l["market"], l["side"]] for l in sorted(legs, key=lambda l: l["market"])])
                    if not self.recent(key):
                        last_probe = time.monotonic()
                        await self.probe(gateway, client, legs)
            except FileNotFoundError:
                pass
            except Exception as exc:
                log(f"error={type(exc).__name__}:{str(exc)[:200]}")
            await asyncio.sleep(POLL_SECONDS)
        return 0


def report() -> int:
    db = open_db()
    rows = db.execute("SELECT * FROM probes ORDER BY id").fetchall()
    done = [r for r in rows if not r["error"]]
    quoted = [r for r in done if r["quotes"]]
    print(f"probes={len(rows)} errors={len(rows) - len(done)} with_quotes={len(quoted)}")
    for r in rows[-10:]:
        legs = json.loads(r["legs_json"])
        edge = None if r["best_buy"] is None or r["fair"] is None else r["fair"] - r["best_buy"]
        print(f"  {r['at'][11:19]} legs={len(legs)} fair={r['fair']:.3f} quotes={r['quotes']} "
              f"best_buy={r['best_buy']} edge_vs_fair={None if edge is None else round(edge, 3)} "
              f"first={r['first_quote_seconds']} err={(r['error'] or '')[:120]}")
    return 0


def cron_line(root: Path, code: Path = ROOT) -> str:
    return (f"* * * * * cd {root} && flock -n data/combo-builder.lock "
            f"{root}/.venv/bin/python -B {code}/scripts/combo_builder.py run "
            f">> logs/combo-builder.log 2>&1 {CRON_MARK}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("run", "report"))
    ap.add_argument("--minutes", type=float, default=None)
    a = ap.parse_args(argv)
    if a.command == "report":
        return report()
    return asyncio.run(Builder().run(a.minutes))


if __name__ == "__main__":
    raise SystemExit(main())
