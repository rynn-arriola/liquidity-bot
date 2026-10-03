#!/usr/bin/env python3
"""Late-favourite trader (BC-2026-10-03-B): buy the near-certain favourite when a match is
almost over and hold it to settlement. Rules: src/liquidity_bot/late_favorite_trader.py;
settings: config/late_favorite_policy.json.

    late_favorite_trader.py run        long-running; cron restarts it if it stops (flock)
    late_favorite_trader.py status     open positions, today's result, last trades

Safety:
- Real orders only when the policy has enabled AND live_orders true; otherwise dry run.
- Refuses to trade while the liquidity bot runner is running (one owner per account).
- Every order is IOC (fills now or cancels): nothing ever rests on the book.
- One trade per event; venue positions (read every minute) cap open positions; buying
  power is checked before each buy; the daily stop counts settled results per ET day.
- An order whose outcome is uncertain blocks new buys until account positions explain it.
- Kill switch: create data/late-favorite/STOP (checked every cycle).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.late_favorite_trader import (  # noqa: E402
    contracts_for, daily_stop_hit, load_policy, pick_entry, realized_result)

OUT = Path("data/late-favorite")
DB = OUT / "trader.db"
STOP_FILE = OUT / "STOP"
ET = ZoneInfo("America/New_York")
CRON_MARK = "# liquidity-bot late favorite trader"
LOOKBACK_HOURS = 14
SLIPPAGE = 0.02
SETTLES_PER_CYCLE = 2
MAX_ATTEMPTS_PER_EVENT = 3
STEP_TIMEOUT = 60  # a read step that hangs is abandoned; the next cycle retries
SETTLE_RETRY_MINUTES = 5
PAGE_SIZE = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY, event_slug TEXT NOT NULL, market_slug TEXT NOT NULL,
  side TEXT NOT NULL, price REAL, cost REAL, qty REAL, fee_coefficient REAL,
  mode TEXT NOT NULL, status TEXT NOT NULL, exchange_order_id TEXT, filled_qty REAL,
  avg_price REAL, created_at TEXT NOT NULL, updated_at TEXT, error TEXT,
  game_state TEXT, settlement REAL, settled_at TEXT, result REAL, next_settle_at TEXT);
DROP INDEX IF EXISTS idx_trades_event;
CREATE INDEX IF NOT EXISTS idx_trades_event_mode ON trades(event_slug, mode);
"""


def _num(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"[late-favorite-trader] at={now_utc().isoformat()} {msg}", flush=True)


def open_db() -> sqlite3.Connection:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def bot_runner_running() -> bool:
    out = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True).stdout
    return "liquidity_bot.live_commissioning" in out


def realized_today(db, mode: str) -> float:
    start = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    row = db.execute("SELECT COALESCE(SUM(result),0) FROM trades WHERE mode=? AND settled_at>=?",
                     (mode, iso(start))).fetchone()
    return float(row[0])


class Trader:
    def __init__(self):
        self.policy = load_policy()
        self.mode = "live" if (self.policy.enabled and self.policy.live_orders) else "dry"
        self.db = open_db()
        self.positions: dict[str, Decimal] = {}
        self.positions_at: datetime | None = None
        self.buying_power: Decimal | None = None

    async def list_live_events(self, gateway, client) -> list[dict]:
        now = now_utc()
        events = []
        for page in range(6):
            params = {"categories": ["sports"], "active": True, "closed": False,
                      "limit": PAGE_SIZE, "offset": page * PAGE_SIZE,
                      "startTimeMin": iso(now - timedelta(hours=LOOKBACK_HOURS)),
                      "startTimeMax": iso(now + timedelta(minutes=2))}
            raw = await gateway._call("events.list(late-favorite-trader)",
                                      lambda params=params: client.events.list(params))
            batch = raw.get("events") if isinstance(raw, dict) else None
            if not isinstance(batch, list):
                raise RuntimeError("event listing response is unavailable")
            events.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return events

    async def refresh_account(self, account) -> None:
        now = now_utc()
        positions = await account.fetch_positions(observed_at=now)
        self.positions = {p.market_slug: p.net_qty for p in positions if p.net_qty != 0}
        balance = await account.fetch_balance(observed_at=now)
        self.buying_power = balance.buying_power
        self.positions_at = now

    def resolve_uncertain(self) -> bool:
        """True while any order outcome is still unexplained (blocks new buys)."""
        rows = self.db.execute("SELECT id, market_slug, created_at FROM trades "
                               "WHERE mode=? AND status='UNCERTAIN'", (self.mode,)).fetchall()
        pending = False
        for r in rows:
            if self.positions_at is None or self.positions_at <= datetime.fromisoformat(
                    r["created_at"].replace("Z", "+00:00")):
                pending = True
                continue
            held = self.positions.get(r["market_slug"])
            with self.db:
                self.db.execute("UPDATE trades SET status=?, filled_qty=?, updated_at=? WHERE id=?",
                                ("FILLED" if held else "NOT_FILLED", float(abs(held)) if held else 0.0,
                                 iso(now_utc()), r["id"]))
        return pending

    def open_count(self) -> int:
        if self.mode == "live":
            return len(self.positions)
        return self.db.execute("SELECT COUNT(*) FROM trades WHERE mode='dry' AND status='FILLED' "
                               "AND settlement IS NULL").fetchone()[0]

    async def buy(self, gateway, trading, cand, event: dict) -> None:
        from liquidity_bot.live_orders import OrderRequest, build_order_payload
        market = await gateway.fetch_market(cand.market_slug)
        # Market-style IOC (operator: "we can do market order as long we still profit"):
        # take what is offered now, paying at most SLIPPAGE above the quoted price and
        # never more than max_price, so a win always clears the fee. Size on that worst
        # price so one trade never risks more than risk_per_trade_usd.
        worst_cost = min(self.policy.max_price, cand.cost + SLIPPAGE)
        qty = contracts_for(self.policy.risk_per_trade_usd, worst_cost, market.minimum_trade_qty or 0.01)
        if qty <= 0:
            return
        tick = Decimal(str(market.tick_size or 0.01))
        limit = worst_cost if cand.side == "long" else 1 - worst_cost   # long-frame price
        rounding = "up" if cand.side == "short" else "down"
        steps = Decimal(str(limit)) / tick
        steps = steps.to_integral_value(rounding="ROUND_CEILING" if rounding == "up" else "ROUND_FLOOR")
        price = steps * tick
        state = event.get("eventState") or {}
        game = json.dumps({k: state.get(k, event.get(k)) for k in ("type", "period", "elapsed", "score")})
        stamp = iso(now_utc())
        with self.db:
            cur = self.db.execute(
                "INSERT INTO trades(event_slug,market_slug,side,price,cost,qty,fee_coefficient,"
                "mode,status,created_at,game_state) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (cand.event_slug, cand.market_slug, cand.side, float(price), cand.cost, float(qty),
                 cand.fee_coefficient, self.mode, "SENDING" if self.mode == "live" else "FILLED",
                 stamp, game))
        trade_id = cur.lastrowid
        log(f"BUY mode={self.mode} event={cand.event_slug} market={cand.market_slug} side={cand.side} "
            f"price={price} cost={cand.cost:.3f} qty={qty} game={game}")
        if self.mode != "live":
            with self.db:
                self.db.execute("UPDATE trades SET filled_qty=?, avg_price=? WHERE id=?",
                                (float(qty), cand.cost, trade_id))
            return
        if self.buying_power is not None and self.buying_power < Decimal(str(cand.cost)) * qty:
            with self.db:
                self.db.execute("UPDATE trades SET status='FAILED', error='buying power' WHERE id=?", (trade_id,))
            return
        request = OrderRequest(cand.market_slug, "bid" if cand.side == "long" else "offer", price, qty,
                               time_in_force="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", post_only=False)
        try:
            payload = build_order_payload(request, market)
        except Exception as exc:
            with self.db:
                self.db.execute("UPDATE trades SET status='FAILED', error=? WHERE id=?",
                                (f"{type(exc).__name__}: {exc}"[:300], trade_id))
            return
        try:
            response = await trading.create(payload)
        except Exception as exc:
            from liquidity_bot.errors import ApiAdmissionDeferred
            from liquidity_bot.live_orders import OrderAdmissionFailed, OrderMutationRejected
            exact = isinstance(exc, (ApiAdmissionDeferred, OrderAdmissionFailed, OrderMutationRejected))
            with self.db:
                self.db.execute("UPDATE trades SET status=?, error=?, updated_at=? WHERE id=?",
                                ("FAILED" if exact else "UNCERTAIN", f"{type(exc).__name__}: {exc}"[:300],
                                 iso(now_utc()), trade_id))
            log(f"order {'failed' if exact else 'UNCERTAIN'} market={cand.market_slug} error={type(exc).__name__}")
            return
        filled, notional = 0.0, 0.0
        for e in response.get("executions") or []:
            if not isinstance(e, dict):
                continue
            q = _num(e.get("quantity") or e.get("qty") or e.get("lastShares"))
            px = _num(e.get("price") or e.get("lastPx"))
            if q:
                filled += q
                notional += q * (px if px is not None else float(price))
        # Our cost per contract: the long-frame fill price, or one minus it for the short side.
        avg = notional / filled if filled else None
        avg_cost = None if avg is None else (avg if cand.side == "long" else 1 - avg)
        with self.db:
            self.db.execute("UPDATE trades SET status=?, exchange_order_id=?, filled_qty=?, avg_price=?, "
                            "updated_at=? WHERE id=?",
                            ("FILLED" if filled > 0 else "UNCERTAIN", str(response.get("id") or ""),
                             filled, avg_cost, iso(now_utc()), trade_id))
        log(f"order sent id={response.get('id')} filled={filled} avg_cost={avg_cost}")

    async def settle(self, gateway, still_playing: set[str]) -> None:
        """Read a result only once its match is no longer being played, at most
        SETTLES_PER_CYCLE per cycle and every SETTLE_RETRY_MINUTES per trade."""
        now = now_utc()
        rows = self.db.execute(
            "SELECT * FROM trades WHERE mode=? AND status='FILLED' AND settlement IS NULL "
            "AND (next_settle_at IS NULL OR next_settle_at<=?) ORDER BY id", (self.mode, iso(now))).fetchall()
        tried = 0
        for r in rows:
            if r["event_slug"] in still_playing:
                continue
            if self.mode == "live" and r["market_slug"] in self.positions:
                continue                                 # still held: not settled yet
            if tried >= SETTLES_PER_CYCLE:
                break
            tried += 1
            try:
                value = await gateway.fetch_settlement(r["market_slug"])
            except Exception:
                with self.db:
                    self.db.execute("UPDATE trades SET next_settle_at=? WHERE id=?",
                                    (iso(now + timedelta(minutes=SETTLE_RETRY_MINUTES)), r["id"]))
                continue
            qty = r["filled_qty"] or r["qty"]
            cost = r["avg_price"] if r["avg_price"] is not None else r["cost"]
            result = realized_result(r["side"], cost, qty, value, r["fee_coefficient"])
            with self.db:
                self.db.execute("UPDATE trades SET settlement=?, settled_at=?, result=? WHERE id=?",
                                (value, iso(now_utc()), result, r["id"]))
            log(f"SETTLED market={r['market_slug']} side={r['side']} cost={r['cost']:.3f} qty={qty} "
                f"settlement={value} result={result:+.2f}")

    async def run(self, minutes: float | None) -> int:
        from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
        from liquidity_bot.adapters import AsyncReadOnlyGateway, shared_api_limiter
        from liquidity_bot.live_orders import AsyncTradingGateway
        from liquidity_bot.probe import client_from_environment
        from liquidity_bot.runtime_safety import SingleInstanceLease
        if not Path("data/commissioning.db").exists():
            raise SystemExit("run from the live bot directory (it shares data/ and the API gate)")
        if not self.policy.enabled:
            raise SystemExit("late-favourite policy is disabled")
        lease = SingleInstanceLease(OUT / "trader.lease")
        lease.acquire()
        client = client_from_environment()
        gateway = AsyncReadOnlyGateway(
            client, limiter=shared_api_limiter(4.0, state_path=Path.cwd() / "data" / "api-rate-limiter.state"),
            default_traffic_class="background")
        trading = AsyncTradingGateway(gateway)
        account = AsyncAccountSnapshotGateway(gateway, resolve_client_id=lambda _x: None)
        stop_at = now_utc() + timedelta(minutes=minutes) if minutes else None
        next_account = now_utc()
        next_status = now_utc()
        log(f"start mode={self.mode} risk=${self.policy.risk_per_trade_usd:g} max_open={self.policy.max_open_positions} "
            f"daily_stop=${self.policy.daily_loss_stop_usd:g} band={self.policy.min_price:g}-{self.policy.max_price:g} "
            f"rules={sorted(self.policy.rules)}")
        while stop_at is None or now_utc() < stop_at:
            cycle = now_utc()
            try:
                if STOP_FILE.exists():
                    log("STOP file present; not trading")
                    await asyncio.sleep(30)
                    continue
                if self.mode == "live" and now_utc() >= next_account:
                    await asyncio.wait_for(self.refresh_account(account), STEP_TIMEOUT)
                    next_account = now_utc() + timedelta(seconds=60)
                blocked = None
                if self.mode == "live" and bot_runner_running():
                    blocked = "liquidity_bot_running"
                elif self.mode == "live" and self.resolve_uncertain():
                    blocked = "uncertain_order"
                elif daily_stop_hit(realized_today(self.db, self.mode), self.policy):
                    blocked = "daily_stop"
                events = await asyncio.wait_for(self.list_live_events(gateway, client), STEP_TIMEOUT)
                still_playing = {e.get("slug") for e in events
                                 if not ((e.get("eventState") or {}).get("ended") or e.get("ended"))}
                await asyncio.wait_for(self.settle(gateway, still_playing), STEP_TIMEOUT)
                for event in events:
                    if blocked or self.open_count() >= self.policy.max_open_positions:
                        break
                    cand = pick_entry(event, self.policy)
                    if cand is None:
                        continue
                    # One position per event; an IOC that did not fill (price moved past
                    # the cap) may be retried, at most MAX_ATTEMPTS_PER_EVENT times.
                    held, attempts = self.db.execute(
                        "SELECT COALESCE(SUM(status NOT IN ('NOT_FILLED','FAILED')),0), COUNT(*) "
                        "FROM trades WHERE event_slug=? AND mode=?",
                        (cand.event_slug, self.mode)).fetchone()
                    if held or attempts >= MAX_ATTEMPTS_PER_EVENT:
                        continue
                    if self.mode == "live" and cand.market_slug in self.positions:
                        continue
                    # No timeout around buy(): cancelling a sent order mid-flight would
                    # leave its outcome unknown; the client itself times out at 15 s.
                    await self.buy(gateway, trading, cand, event)
                    if self.mode == "live":
                        next_account = now_utc()          # recount positions before the next buy
                        await asyncio.wait_for(self.refresh_account(account), STEP_TIMEOUT)
                if now_utc() >= next_status:
                    next_status = now_utc() + timedelta(minutes=15)
                    log(f"status mode={self.mode} open={self.open_count()} realized_today=${realized_today(self.db, self.mode):.2f} "
                        f"blocked={blocked} live_events={sum(1 for e in events if (e.get('eventState') or {}).get('live'))}")
            except Exception as exc:
                log(f"error={type(exc).__name__}:{str(exc)[:200]}")
            await asyncio.sleep(max(1.0, self.policy.poll_seconds - (now_utc() - cycle).total_seconds()))
        return 0


def status(_args) -> int:
    db = open_db()
    for mode in ("live", "dry"):
        rows = db.execute("SELECT * FROM trades WHERE mode=? ORDER BY id", (mode,)).fetchall()
        if not rows:
            continue
        settled = [r for r in rows if r["settlement"] is not None]
        print(f"{mode}: trades={len(rows)} open={sum(1 for r in rows if r['status']=='FILLED' and r['settlement'] is None)} "
              f"settled={len(settled)} won={sum(1 for r in settled if (r['result'] or 0) > 0)} "
              f"result=${sum(r['result'] or 0 for r in settled):.2f} today=${realized_today(db, mode):.2f}")
        for r in rows[-10:]:
            print(f"   {r['created_at'][11:19]} {r['market_slug'][:44]:44} {r['side']:5} cost={r['cost']:.3f} "
                  f"qty={r['qty']:g} {r['status']} {'' if r['result'] is None else f'{r['result']:+.2f}'} {r['game_state']}")
    return 0


def cron_line(root: Path, code: Path = ROOT) -> str:
    return (f"*/5 * * * * cd {root} && flock -n data/late-favorite/trader-cron.lock "
            f"{root}/.venv/bin/python -B {code}/scripts/late_favorite_trader.py run "
            f">> logs/late-favorite-trader.log 2>&1 {CRON_MARK}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("run", "status"))
    p.add_argument("--minutes", type=float, default=None)
    a = p.parse_args(argv)
    if a.command == "status":
        return status(a)
    return asyncio.run(Trader().run(a.minutes))


if __name__ == "__main__":
    raise SystemExit(main())
