#!/usr/bin/env python3
"""Reward Pulse: how good was each reward day, league and market for us?

Read-only daily report card (operator, 2026-09-27). It makes no API request
and has no trading consumer. Inputs: a read-only ledger snapshot (our resting
order-hours and the quote observations' pool / share / queue-ahead evidence)
and the newest archived liquidity-reward snapshot (the bot records it every
15 minutes into ``data/market-quality``).

Each market is attributed to its own reward day (the date in its slug), so a
next-day game rested in the evening is graded with its own day. The measure is
paid + pending dollars per resting order-hour, compared with our normal: the
same league's three market-day weeks before the graded day, weighted 50% /
25% / 25% (most recent first; the ranker's calibration weights). Levels:

    Hot      >= 1.5x normal
    Good     1.1x - 1.5x
    Average  0.75x - 1.1x
    Low      0.4x - 0.75x
    Dead     < 0.4x   (includes unpaid)

Each league also shows why: the day's median reward pool, competing size ahead
of our quote and our share of the reward score, against the previous seven
market days. Markets we rested in for a long time with a real share that were
still unpaid are flagged for follow-up.

Payouts appear about a day later and barely change after that, so the default
run grades the three most recent complete market days (yesterday and the two
before) and rewrites them.

    cd /home/polybot/liquidity-bot
    .venv/bin/python -B scripts/reward_pulse.py --db data/commissioning.db \\
        --archive data/market-quality/*.sqlite3
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UTC = timezone.utc
WORKING = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
WEEK_WEIGHTS = (0.50, 0.25, 0.25)   # same weighting as the ranker calibration
LEVELS = ((1.5, "Hot"), (1.1, "Good"), (0.75, "Average"), (0.4, "Low"), (0.0, "Dead"))
ICONS = {"Hot": "🔥", "Good": "🟢", "Average": "🟡", "Low": "🟠", "Dead": "🔴", "Unrated": "⚪"}
MIN_BASELINE_HOURS = 20.0      # a league needs this much normal before it gets its own baseline
MIN_MARKET_HOURS = 0.25        # shorter rests are listed but not graded
FLAG_MIN_HOURS = 2.0           # unpaid after at least this long ...
FLAG_MIN_SHARE = 0.10          # ... with at least this share of the reward score
CRON_MARK = "# liquidity-bot reward pulse"


# ----------------------------------------------------------------- helpers

def stamp(value) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(UTC)


def market_day(slug: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})$", slug)
    return match.group(1) if match else None


def league(slug: str) -> str:
    parts = slug.split("-")
    return parts[1] if len(parts) > 2 and parts[0] == "aec" else "other"


def level(ratio: float | None) -> str:
    if ratio is None:
        return "Unrated"
    return next(name for floor, name in LEVELS if ratio >= floor)


def graded_level(amount: float, hours: float, normal: float | None) -> tuple[float | None, str]:
    """(ratio to normal, level). A normal of $0 (a league that never paid) grades Dead."""
    if hours < MIN_MARKET_HOURS or normal is None:
        return None, "Unrated"
    if normal <= 0:
        return None, "Dead" if amount <= 0 else "Hot"
    ratio = (amount / hours) / normal
    return ratio, level(ratio)


def median(values: Iterable[float]) -> float | None:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


# ----------------------------------------------------------------- inputs

def resting_hours(orders: list[dict], intents: list[dict], *, now: datetime) -> dict[str, float]:
    """Order-hours per market for our own entry quotes (reducers/exits excluded)."""
    created = {i["client_order_id"]: stamp(i["created_at"]) for i in intents
               if i["quote_observation_required"]}
    hours: dict[str, float] = defaultdict(float)
    for order in orders:
        begin = created.get(order["client_order_id"])
        if begin is None:
            continue
        end = (now if order["status"] in WORKING or not order["event_at"]
               else stamp(order["event_at"]))
        hours[order["market_slug"]] += max((end - begin).total_seconds(), 0.0) / 3600
    return dict(hours)


def payouts(rows: list[dict]) -> dict[str, dict]:
    """Paid + pending liquidity reward per market from one archived snapshot."""
    out: dict[str, dict] = {}
    for row in rows:
        entry = out.setdefault(row["market_slug"], dict(amount=0.0, statuses=set()))
        entry["amount"] += float(row.get("reward") or 0)
        entry["statuses"].add(str(row.get("status", "")).upper())
    return out


def evidence(observations: list[dict]) -> dict[str, dict]:
    """Median reward pool, our share and size ahead per market."""
    per: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for obs in observations:
        for key in ("reward_pool", "snapshot_share", "queue_ahead"):
            if obs.get(key) is not None:
                per[obs["market_slug"]][key].append(float(obs[key]))
    return {slug: {k: median(v) for k, v in vals.items()} for slug, vals in per.items()}


# ----------------------------------------------------------------- grading

def baseline(day: str, hours: dict[str, float], paid: dict[str, dict]) -> dict[str, float]:
    """Weighted $/order-hour per league (and "*" overall) over the three weeks before ``day``."""
    num: dict[str, float] = defaultdict(float)
    den: dict[str, float] = defaultdict(float)
    raw: dict[str, float] = defaultdict(float)
    for slug, h in hours.items():
        d = market_day(slug)
        if d is None or d >= day or d < shift(day, -7 * len(WEEK_WEIGHTS)):
            continue
        # 1-7 days before -> week 1 (50%), 8-14 -> week 2, 15-21 -> week 3
        weight = WEEK_WEIGHTS[((date.fromisoformat(day) - date.fromisoformat(d)).days - 1) // 7]
        amount = paid.get(slug, {}).get("amount", 0.0)
        for key in (league(slug), "*"):
            num[key] += weight * amount
            den[key] += weight * h
            raw[key] += h
    return {k: num[k] / den[k] for k in den
            if den[k] > 0 and (k == "*" or raw[k] >= MIN_BASELINE_HOURS)}


def grade_day(day: str, hours: dict[str, float], paid: dict[str, dict],
              ev: dict[str, dict]) -> dict:
    normal = baseline(day, hours, paid)
    overall_normal = normal.get("*")
    markets = []
    for slug in sorted(s for s in hours if market_day(s) == day):
        h = hours[slug]
        amount = paid.get(slug, {}).get("amount", 0.0)
        lg = league(slug)
        ref = normal.get(lg, overall_normal)
        rate = amount / h if h > 0 else None
        ratio, lvl = graded_level(amount, h, ref)
        e = ev.get(slug, {})
        markets.append(dict(
            market=slug, league=lg, hours=round(h, 2), paid=round(amount, 4),
            statuses=sorted(paid.get(slug, {}).get("statuses", ())),
            per_hour=None if rate is None else round(rate, 4),
            vs_normal=None if ratio is None else round(ratio, 3),
            level=lvl,
            pool=e.get("reward_pool"), share=e.get("snapshot_share"),
            ahead=e.get("queue_ahead")))
    leagues = []
    for lg in sorted({m["league"] for m in markets}):
        rows = [m for m in markets if m["league"] == lg]
        h = sum(m["hours"] for m in rows)
        amount = sum(m["paid"] for m in rows)
        ref = normal.get(lg, overall_normal)
        ratio, lvl = graded_level(amount, h, ref)
        week =[s for s in ev if league(s) == lg and shift(day, -7) <= (market_day(s) or "") < day]
        today = [m for m in rows if m["pool"] is not None]
        drivers = {}
        for key, name in (("pool", "reward_pool"), ("share", "snapshot_share"), ("ahead", "queue_ahead")):
            now_v = median(m[key] for m in today if m[key] is not None)
            before = median(ev[s][name] for s in week if ev[s].get(name) is not None)
            drivers[key] = dict(day=now_v, prior_week=before)
        leagues.append(dict(
            league=lg, markets=len(rows), unpaid=sum(1 for m in rows if m["paid"] <= 0),
            hours=round(h, 1), paid=round(amount, 2),
            per_hour=round(amount / h, 4) if h > 0 else None,
            normal_per_hour=None if ref is None else round(ref, 4),
            normal_basis="league" if lg in normal else "overall",
            vs_normal=None if ratio is None else round(ratio, 3), level=lvl,
            drivers=drivers))
    leagues.sort(key=lambda r: -r["hours"])
    h = sum(m["hours"] for m in markets)
    amount = sum(m["paid"] for m in markets)
    ratio = (amount / h) / overall_normal if h > 0 and overall_normal else None
    flags = [m for m in markets if m["paid"] <= 0 and m["hours"] >= FLAG_MIN_HOURS
             and (m["share"] or 0) >= FLAG_MIN_SHARE]
    return dict(
        day=day, markets=len(markets), paid_markets=sum(1 for m in markets if m["paid"] > 0),
        hours=round(h, 1), paid=round(amount, 2),
        pending=any("PENDING" in m["statuses"] for m in markets),
        per_hour=round(amount / h, 4) if h > 0 else None,
        normal_per_hour=None if overall_normal is None else round(overall_normal, 4),
        vs_normal=None if ratio is None else round(ratio, 3), level=level(ratio),
        leagues=leagues, markets_detail=markets,
        flags=sorted(flags, key=lambda m: -m["hours"]))


# ----------------------------------------------------------------- output

def _money(v) -> str:
    return "-" if v is None else f"${v:,.2f}"


def _num(v, fmt="{:.0f}") -> str:
    return "-" if v is None else fmt.format(v)


def render(report: dict) -> str:
    d = report
    out = [f"# Reward Pulse — {d['day']}: {ICONS[d['level']]} {d['level']}", ""]
    out.append(f"- **Paid{' + pending' if d['pending'] else ''}:** {_money(d['paid'])} over "
               f"{d['hours']:.0f} order-hours = {_money(d['per_hour'])}/h "
               f"(normal {_money(d['normal_per_hour'])}/h, {_num(d['vs_normal'], '{:.2f}')}x)")
    out.append(f"- **Markets:** {d['paid_markets']} of {d['markets']} paid")
    out += ["", "## Leagues", "",
            "| League | Level | Paid | Hours | $/h | Normal $/h | Pool (prior wk) | Ahead (prior wk) | Our share (prior wk) | Unpaid |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in d["leagues"]:
        dr = r["drivers"]
        out.append(
            f"| {r['league']} | {ICONS[r['level']]} {r['level']} | {_money(r['paid'])} | {r['hours']:.1f} | "
            f"{_money(r['per_hour'])} | {_money(r['normal_per_hour'])} | "
            f"{_num(dr['pool']['day'])} ({_num(dr['pool']['prior_week'])}) | "
            f"{_num(dr['ahead']['day'])} ({_num(dr['ahead']['prior_week'])}) | "
            f"{_num(dr['share']['day'], '{:.1%}')} ({_num(dr['share']['prior_week'], '{:.1%}')}) | "
            f"{r['unpaid']}/{r['markets']} |")
    graded = [m for m in d["markets_detail"] if m["level"] != "Unrated"]
    for title, rows in (("Best markets", sorted(graded, key=lambda m: -(m["per_hour"] or 0))[:10]),
                        ("Weakest markets (most hours for least pay)",
                         sorted(graded, key=lambda m: ((m["per_hour"] or 0), -m["hours"]))[:10])):
        out += ["", f"## {title}", "", "| Market | Level | Paid | Hours | $/h | Pool | Share | Ahead |",
                "|---|---|---|---|---|---|---|---|"]
        for m in rows:
            out.append(f"| {m['market']} | {ICONS[m['level']]} {m['level']} | {_money(m['paid'])} | "
                       f"{m['hours']:.1f} | {_money(m['per_hour'])} | {_num(m['pool'])} | "
                       f"{_num(m['share'], '{:.1%}')} | {_num(m['ahead'])} |")
    if d["flags"]:
        out += ["", "## Check: unpaid despite a real share", "",
                f"Rested {FLAG_MIN_HOURS:.0f}+ hours with {FLAG_MIN_SHARE:.0%}+ of the reward score and paid $0.", "",
                "| Market | Hours | Pool | Share | Ahead |", "|---|---|---|---|---|"]
        for m in d["flags"]:
            out.append(f"| {m['market']} | {m['hours']:.1f} | {_num(m['pool'])} | "
                       f"{_num(m['share'], '{:.1%}')} | {_num(m['ahead'])} |")
    out += ["", "_Levels vs our normal (same league, previous 3 weeks weighted 50/25/25): "
            "Hot ≥1.5x, Good 1.1–1.5x, Average 0.75–1.1x, Low 0.4–0.75x, Dead <0.4x._"]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- run

def read_inputs(db: Path, *, since: datetime) -> dict:
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        s = since.isoformat()
        intents = [dict(r) for r in conn.execute(
            "SELECT client_order_id, created_at, quote_observation_required FROM execution_intents "
            "WHERE operation='PLACE' AND created_at>=?", (s,))]
        orders = [dict(r) for r in conn.execute(
            "SELECT client_order_id, market_slug, status, event_at FROM execution_orders")]
        observations = [dict(r) for r in conn.execute(
            "SELECT market_slug, json_extract(observation_json,'$.reward_pool') AS reward_pool,"
            " json_extract(observation_json,'$.snapshot_share') AS snapshot_share,"
            " observed_queue_ahead AS queue_ahead FROM execution_quote_observations"
            " WHERE observed_at>=?", (s,))]
        return dict(intents=intents, orders=orders, observations=observations)
    finally:
        conn.close()


def build(days: list[str], data: dict, reward_rows: list[dict], *, now: datetime) -> list[dict]:
    hours = resting_hours(data["orders"], data["intents"], now=now)
    paid = payouts(reward_rows)
    ev = evidence(data["observations"])
    return [grade_day(day, hours, paid, ev) for day in days]


def cron_line(root: Path) -> str:
    # 05:10 local: after the 04:40 calibration report; yesterday's payouts are in by then.
    return (f"10 5 * * * cd {root} && nice -n 10 ionice -c3 .venv/bin/python -B "
            "scripts/reward_pulse.py --db data/commissioning.db "
            f"--archive data/market-quality/*.sqlite3 >> logs/reward-pulse.log 2>&1 {CRON_MARK}")


def install_cron(root: Path) -> int:
    import subprocess
    from scripts.rotate_logs import with_cron_line
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = current.stdout if current.returncode == 0 else ""
    return subprocess.run(["crontab", "-"], text=True, input=with_cron_line(
        existing, cron_line(root), mark=CRON_MARK)).returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path)
    p.add_argument("--archive", type=Path, action="extend", nargs="+", default=[])
    p.add_argument("--days", type=int, default=3, help="most recent complete market days to grade")
    p.add_argument("--day", action="append", default=[], help="grade this market day (YYYY-MM-DD)")
    p.add_argument("--output-root", type=Path, default=Path("data/reward-pulse"))
    p.add_argument("--install-cron", action="store_true", help="install the daily 05:10 run")
    a = p.parse_args(argv)
    if a.install_cron:
        return install_cron(Path.cwd().resolve())
    if a.db is None or not a.archive:
        p.error("--db and --archive are required")
    from scripts.calibration_report import read_archive_records
    now = datetime.now(UTC)
    from zoneinfo import ZoneInfo
    yesterday = (now.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()
    days = sorted(set(a.day) or {shift(yesterday, -k) for k in range(a.days)})
    snaps = [s for s in read_archive_records(a.archive, {"rewards"}) if isinstance(s.get("rows"), list)]
    if not snaps:
        print("reward-pulse: no archived reward snapshot", file=sys.stderr)
        return 1
    newest = max(snaps, key=lambda s: s.get("at", ""))
    earliest = shift(min(days), -7 * len(WEEK_WEIGHTS) - 2)
    data = read_inputs(a.db, since=datetime.fromisoformat(earliest).replace(tzinfo=UTC))
    reports = build(days, data, newest["rows"], now=now)
    a.output_root.mkdir(parents=True, exist_ok=True)
    for report in reports:
        report["payout_snapshot_at"] = newest.get("at")
        report["generated_at"] = now.isoformat()
        (a.output_root / f"{report['day']}.json").write_text(
            json.dumps(report, indent=1, default=str), encoding="utf8")
        (a.output_root / f"{report['day']}.md").write_text(render(report), encoding="utf8")
        print(f"Reward Pulse {report['day']}: {report['level']} paid={report['paid']} "
              f"hours={report['hours']} vs_normal={report['vs_normal']}")
    (a.output_root / "latest.md").write_text(render(reports[-1]), encoding="utf8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
