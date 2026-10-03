#!/usr/bin/env python3
"""Late-favorite tracker (read-only): every live sports market, its game state and result.

    late_favorite_tracker.py run       long-running; cron restarts it if it stops (flock)
    late_favorite_tracker.py report    write data/late-favorite/latest.md|json

Design and purpose: src/liquidity_bot/late_favorite.py. API use: the sports event listing
every 30 s (a few pages) plus one settlement read per finished market, all on the
opportunistic report lane of the bot's shared API gate. No order endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.late_favorite import parse_event, summarize  # noqa: E402

OUT = Path("data/late-favorite")
DB = OUT / "tracker.db"
CRON_MARK = "# liquidity-bot late favorite tracker"
POLL_SECONDS = 30
PAGE_SIZE = 500
MAX_PAGES = 6
LOOKBACK_HOURS = 14
SETTLE_AFTER_MINUTES = 5           # after the event ends or the market leaves the listing
SETTLE_RETRY_MINUTES = 10
SETTLE_GIVE_UP_HOURS = 48
SETTLES_PER_CYCLE = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  event_slug TEXT PRIMARY KEY, sport TEXT, series TEXT, title TEXT, start_time TEXT,
  first_seen TEXT, last_seen TEXT, last_live_at TEXT, ended_at TEXT);
CREATE TABLE IF NOT EXISTS event_obs(
  id INTEGER PRIMARY KEY, event_slug TEXT NOT NULL, observed_at TEXT NOT NULL,
  live INTEGER, ended INTEGER, period TEXT, elapsed TEXT, score TEXT, period_scores TEXT);
CREATE INDEX IF NOT EXISTS idx_event_obs ON event_obs(event_slug, observed_at);
CREATE TABLE IF NOT EXISTS markets(
  market_slug TEXT PRIMARY KEY, event_slug TEXT, market_type TEXT, sports_market_type TEXT,
  outcomes TEXT, question TEXT, fee REAL, tick REAL, first_seen TEXT, last_seen TEXT,
  settlement REAL, settled_at TEXT, settle_attempts INTEGER DEFAULT 0, next_settle_at TEXT);
CREATE TABLE IF NOT EXISTS market_obs(
  id INTEGER PRIMARY KEY, market_slug TEXT NOT NULL, observed_at TEXT NOT NULL,
  status TEXT, bid REAL, ask REAL);
CREATE INDEX IF NOT EXISTS idx_market_obs ON market_obs(market_slug, observed_at);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"[late-favorite] at={now_utc().isoformat()} {msg}", flush=True)


def open_db(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


class Tracker:
    def __init__(self):
        self.db = open_db()
        self.last_event: dict[str, tuple] = {}
        self.last_market: dict[str, tuple] = {}
        self.stats = {"cycles": 0, "events": 0, "markets": 0, "event_rows": 0,
                      "market_rows": 0, "settled": 0, "errors": 0}

    async def list_started_events(self, gateway, client) -> list[dict]:
        now = now_utc()
        events: list[dict] = []
        for page in range(MAX_PAGES):
            params = {"categories": ["sports"], "active": True, "closed": False,
                      "limit": PAGE_SIZE, "offset": page * PAGE_SIZE,
                      "startTimeMin": iso(now - timedelta(hours=LOOKBACK_HOURS)),
                      "startTimeMax": iso(now + timedelta(minutes=2))}
            raw = await gateway._call("events.list(late-favorite)",
                                      lambda params=params: client.events.list(params))
            batch = raw.get("events") if isinstance(raw, dict) else None
            if not isinstance(batch, list):
                raise RuntimeError("event listing response is unavailable")
            events.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return events

    def record(self, events: list[dict], at: datetime) -> set[str]:
        stamp = iso(at)
        seen_markets: set[str] = set()
        with self.db:
            for raw in events:
                ev, quotes = parse_event(raw)
                if ev is None:
                    continue
                self.db.execute(
                    "INSERT INTO events(event_slug,sport,series,title,start_time,first_seen,last_seen) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_slug) DO UPDATE SET last_seen=excluded.last_seen,"
                    "sport=COALESCE(excluded.sport,events.sport)",
                    (ev.event_slug, ev.sport, ev.series, ev.title, ev.start_time, stamp, stamp))
                if ev.live:
                    self.db.execute("UPDATE events SET last_live_at=? WHERE event_slug=?", (stamp, ev.event_slug))
                if ev.ended:
                    self.db.execute("UPDATE events SET ended_at=COALESCE(ended_at, ?) WHERE event_slug=?",
                                    (stamp, ev.event_slug))
                if self.last_event.get(ev.event_slug) != ev.key():
                    self.last_event[ev.event_slug] = ev.key()
                    self.db.execute(
                        "INSERT INTO event_obs(event_slug,observed_at,live,ended,period,elapsed,score,period_scores)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (ev.event_slug, stamp, int(ev.live), int(ev.ended), ev.period, ev.elapsed,
                         ev.score, ev.period_scores))
                    self.stats["event_rows"] += 1
                for q in quotes:
                    seen_markets.add(q.market_slug)
                    self.db.execute(
                        "INSERT INTO markets(market_slug,event_slug,market_type,sports_market_type,outcomes,"
                        "question,fee,tick,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(market_slug) DO UPDATE SET last_seen=excluded.last_seen",
                        (q.market_slug, q.event_slug, q.market_type, q.sports_market_type, q.outcomes,
                         q.question, q.fee_coefficient, q.tick, stamp, stamp))
                    if self.last_market.get(q.market_slug) != q.key():
                        self.last_market[q.market_slug] = q.key()
                        self.db.execute(
                            "INSERT INTO market_obs(market_slug,observed_at,status,bid,ask) VALUES(?,?,?,?,?)",
                            (q.market_slug, stamp, q.status, q.best_bid, q.best_ask))
                        self.stats["market_rows"] += 1
        self.stats["events"] = len(events)
        self.stats["markets"] = len(seen_markets)
        return seen_markets

    def due_settlements(self, at: datetime, listed: set[str]) -> list[str]:
        cutoff = iso(at - timedelta(minutes=SETTLE_AFTER_MINUTES))
        rows = self.db.execute(
            "SELECT m.market_slug FROM markets m JOIN events e ON e.event_slug=m.event_slug "
            "WHERE m.settlement IS NULL AND (m.next_settle_at IS NULL OR m.next_settle_at<=?) "
            "AND e.last_live_at IS NOT NULL "
            "AND ((e.ended_at IS NOT NULL AND e.ended_at<=?) OR m.last_seen<=?) "
            "ORDER BY m.last_seen LIMIT ?",
            (iso(at), cutoff, cutoff, SETTLES_PER_CYCLE * 4)).fetchall()
        return [r[0] for r in rows][:SETTLES_PER_CYCLE]

    async def settle(self, gateway, slugs: list[str], at: datetime) -> None:
        for slug in slugs:
            try:
                value = await gateway.fetch_settlement(slug)
            except Exception:
                value = None
            with self.db:
                if value is not None:
                    self.db.execute("UPDATE markets SET settlement=?, settled_at=? WHERE market_slug=?",
                                    (value, iso(at), slug))
                    self.stats["settled"] += 1
                else:
                    row = self.db.execute("SELECT first_seen, settle_attempts FROM markets WHERE market_slug=?",
                                          (slug,)).fetchone()
                    give_up = (row is None or datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                               < at - timedelta(hours=SETTLE_GIVE_UP_HOURS))
                    self.db.execute(
                        "UPDATE markets SET settle_attempts=settle_attempts+1, next_settle_at=? WHERE market_slug=?",
                        ("9999-12-31T00:00:00Z" if give_up
                         else iso(at + timedelta(minutes=SETTLE_RETRY_MINUTES)), slug))

    async def run(self, minutes: float | None) -> int:
        from liquidity_bot.adapters import (
            AsyncReadOnlyGateway, OPPORTUNISTIC_REPORT_RATE_PER_SECOND, shared_api_limiter)
        from liquidity_bot.probe import client_from_environment
        from liquidity_bot.runtime_safety import SingleInstanceLease
        if not Path("data/commissioning.db").exists():
            raise SystemExit("run from the live bot directory (it shares data/ and the API gate)")
        lease = SingleInstanceLease(OUT / "tracker.lease")
        lease.acquire()
        client = client_from_environment()
        gateway = AsyncReadOnlyGateway(
            client, limiter=shared_api_limiter(
                OPPORTUNISTIC_REPORT_RATE_PER_SECOND,
                state_path=Path.cwd() / "data" / "api-rate-limiter.state"),
            default_traffic_class="report")
        stop_at = now_utc() + timedelta(minutes=minutes) if minutes else None
        next_status = now_utc()
        log("start")
        while stop_at is None or now_utc() < stop_at:
            cycle_start = now_utc()
            try:
                events = await self.list_started_events(gateway, client)
                listed = self.record(events, now_utc())
                await self.settle(gateway, self.due_settlements(now_utc(), listed), now_utc())
                self.stats["cycles"] += 1
            except Exception as exc:
                self.stats["errors"] += 1
                log(f"error={type(exc).__name__}:{str(exc)[:160]}")
            if now_utc() >= next_status:
                next_status = now_utc() + timedelta(minutes=15)
                live = self.db.execute("SELECT COUNT(*) FROM events WHERE last_live_at>=?",
                                       (iso(now_utc() - timedelta(minutes=2)),)).fetchone()[0]
                settled = self.db.execute("SELECT COUNT(*) FROM markets WHERE settlement IS NOT NULL").fetchone()[0]
                log(" ".join(f"{k}={v}" for k, v in self.stats.items()) + f" live_events_now={live} settled_total={settled}")
            await asyncio.sleep(max(1.0, POLL_SECONDS - (now_utc() - cycle_start).total_seconds()))
        return 0


def report(_args) -> int:
    conn = open_db()
    ev_obs = defaultdict(list)
    for r in conn.execute("SELECT event_slug, observed_at, period FROM event_obs ORDER BY observed_at"):
        ev_obs[r[0]].append((r[1], r[2]))
    events = {r["event_slug"]: dict(r) for r in conn.execute("SELECT * FROM events")}
    rows = []
    settled = conn.execute("SELECT * FROM markets WHERE settlement IS NOT NULL").fetchall()
    for m in settled:
        e = events.get(m["event_slug"]) or {}
        end = e.get("ended_at") or e.get("last_live_at")
        if not end:
            continue
        end_t = datetime.fromisoformat(end.replace("Z", "+00:00"))
        periods = ev_obs.get(m["event_slug"], [])
        period_times = [t for t, _ in periods]
        obs = []
        for o in conn.execute("SELECT observed_at, bid, ask FROM market_obs WHERE market_slug=? "
                              "ORDER BY observed_at", (m["market_slug"],)):
            at = o[0]
            if e.get("start_time") and at < e["start_time"]:
                continue
            i = bisect.bisect_right(period_times, at)
            if i == 0:
                continue                      # no game state recorded yet
            period = periods[i - 1][1]
            mins_left = (end_t - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() / 60
            obs.append((at, period, o[1], o[2], max(0.0, mins_left)))
        if obs:
            rows.append({"market_slug": m["market_slug"], "sport": e.get("sport") or e.get("series"),
                         "market_type": (m["market_type"] or "").replace("SPORTS_MARKET_TYPE_", ""),
                         "settlement": m["settlement"], "fee": m["fee"], "obs": obs})
    table = summarize(rows)
    totals = {
        "events": conn.execute("SELECT COUNT(*) FROM events WHERE last_live_at IS NOT NULL").fetchone()[0],
        "markets": conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0],
        "settled": len(settled), "with_live_prices": len(rows),
        "since": conn.execute("SELECT MIN(first_seen) FROM events").fetchone()[0],
    }
    (OUT / "latest.json").write_text(json.dumps({"totals": totals, "table": table}, indent=1), encoding="utf-8")
    lines = ["# Late-favourite tracker (read-only)", "",
             f"Since {totals['since']}: {totals['events']} live events, {totals['markets']} markets, "
             f"{totals['settled']} settled, {totals['with_live_prices']} with live prices.", "",
             "Buy the favourite at the ask (taker fee) the first time it costs >= threshold in a stage, "
             "hold to settlement. 'minutes_before_end' uses the recorded end (hindsight, reference only); "
             "'period' is what the bot could see live.", ""]
    for kind in ("period", "minutes_before_end"):
        lines += [f"## By {kind.replace('_', ' ')} (moneyline, all sports)", "",
                  "| Threshold | Stage | Trades | Favourite won | Avg price | $ per contract |",
                  "|---|---|---|---|---|---|"]
        agg = defaultdict(lambda: [0, 0, 0.0, 0.0])
        for r in table:
            if r["stage_kind"] == kind and r["market_type"] == "MONEYLINE":
                a = agg[(r["threshold"], r["stage"])]
                a[0] += r["trades"]; a[1] += r["won"]
                a[2] += r["avg_price"] * r["trades"]; a[3] += r["pnl_per_contract"] * r["trades"]
        for (thr, stage), a in sorted(agg.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            lines.append(f"| {thr:.2f} | {stage} | {a[0]} | {a[1] / a[0]:.0%} | {a[2] / a[0]:.3f} | {a[3] / a[0]:+.3f} |")
        lines.append("")
    lines += ["## Moneyline at 0.90 by sport and period", "",
              "| Sport | Period | Trades | Won | Avg price | $ per contract |", "|---|---|---|---|---|---|"]
    for r in table:
        if r["market_type"] == "MONEYLINE" and r["threshold"] == 0.90 and r["stage_kind"] == "period":
            lines.append(f"| {r['sport']} | {r['stage']} | {r['trades']} | {r['win_rate']:.0%} | "
                         f"{r['avg_price']:.3f} | {r['pnl_per_contract']:+.3f} |")
    (OUT / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Late favourite: {totals}; wrote {OUT / 'latest.md'}")
    return 0


def cron_lines(root: Path, code: Path = ROOT) -> list[str]:
    py = f"{root}/.venv/bin/python -B {code}/scripts/late_favorite_tracker.py"
    return [
        f"*/10 * * * * cd {root} && flock -n data/late-favorite/cron.lock nice -n 10 {py} run "
        f">> logs/late-favorite.log 2>&1 {CRON_MARK}",
        f"55 * * * * cd {root} && nice -n 10 {py} report >> logs/late-favorite.log 2>&1 {CRON_MARK} report",
    ]


def install_cron(root: Path) -> int:
    import subprocess
    (root / "data" / "late-favorite").mkdir(parents=True, exist_ok=True)
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [line for line in existing.splitlines() if CRON_MARK not in line]
    body = "\n".join(kept + cron_lines(root)) + "\n"
    return subprocess.run(["crontab", "-"], input=body, text=True).returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("run", "report", "install-cron"))
    p.add_argument("--minutes", type=float, default=None)
    a = p.parse_args(argv)
    if a.command == "report":
        return report(a)
    if a.command == "install-cron":
        return install_cron(Path.cwd().resolve())
    return asyncio.run(Tracker().run(a.minutes))


if __name__ == "__main__":
    raise SystemExit(main())
