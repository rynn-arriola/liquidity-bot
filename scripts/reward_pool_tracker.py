#!/usr/bin/env python3
"""Reward Pool Tracker: the reward pools Polymarket OFFERED, by program, and when they change.

Report-only (operator, 2026-09-30: "build it but dont connect it to the bot
yet"; "dont look at the payment we got but the pool that was offered"). No API
request, no trading consumer, no Discord post. Sources, both recorded by the
running bot:

* quote observations: the program id and pool of every entry quote (Sep 14+);
* market-quality ``discovery`` records: every program the selector's scans saw
  (Sep 23+), including programs we never quoted.

A program family is the program id without its date/version suffix, e.g.
``table_tennis_moneyline_day_of_20260924`` -> ``table_tennis_moneyline_day_of``.
For each family and period the report shows the pool offered per day, the
latest pool, the previous different pool and the change, and flags families
that changed or first appeared in the last ``--alert-days`` days.

    .venv/bin/python -B scripts/reward_pool_tracker.py --db data/commissioning.db \\
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UTC = timezone.utc
CRON_MARK = "# liquidity-bot reward pool tracker"
_SUFFIX = re.compile(r"_\d{8}(?:_v\d+)?$")


def family(program_id: str) -> str:
    return _SUFFIX.sub("", str(program_id))


def collect(observations: list[tuple], discovery: list[dict]) -> dict:
    """{(family, period): {day: {pool: count}}} plus the latest program id per family."""
    seen: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    latest_id: dict = {}
    def note(pid, period, pool, day, at):
        key = (family(pid), str(period))
        seen[key][day][float(pool)] += 1
        if key not in latest_id or at >= latest_id[key][1]:
            latest_id[key] = (pid, at)
    for pid, period, pool, observed_at in observations:
        if pid and pool is not None and period:
            note(pid, period, pool, str(observed_at)[:10], str(observed_at))
    for rec in discovery:
        at = str(rec.get("at", ""))
        for p in rec.get("pools") or []:
            ident = p.get("identity") if isinstance(p, dict) else None
            if ident and len(ident) >= 5 and ident[4] is not None:
                note(ident[0], ident[1], ident[4], at[:10], at)
    return dict(seen=seen, latest_id={k: v[0] for k, v in latest_id.items()})


def day_pool(counts: dict) -> float:
    """The pool offered that day: the most frequently seen value."""
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def summarize(collected: dict, *, today: str, alert_days: int = 2) -> list[dict]:
    rows = []
    cutoff = (date.fromisoformat(today) - timedelta(days=alert_days)).isoformat()
    for (fam, period), by_day in collected["seen"].items():
        days = sorted(by_day)
        series = [(d, day_pool(by_day[d])) for d in days]
        latest_day, latest = series[-1]
        previous = next((p for _, p in reversed(series) if p != latest), None)
        changed_on = None
        if previous is not None:
            for (d0, p0), (d1, p1) in zip(series, series[1:]):
                if p1 == latest and p0 != latest:
                    changed_on = d1
        first_seen = days[0]
        alert = ("CHANGED" if changed_on and changed_on >= cutoff else
                 "NEW" if previous is None and first_seen >= cutoff else "")
        rows.append(dict(
            family=fam, period=period, latest=latest, latest_day=latest_day,
            previous=previous, change=(None if previous in (None, 0) else (latest - previous) / previous),
            changed_on=changed_on, first_seen=first_seen, alert=alert,
            program_id=collected["latest_id"].get((fam, period)), series=series))
    rows.sort(key=lambda r: (r["alert"] == "", -abs(r["change"] or 0), r["family"]))
    return rows


def render(rows: list[dict], *, generated_at: str, today: str) -> str:
    alerts = [r for r in rows if r["alert"]]
    out = [f"# Reward Pool Tracker — {today}", "",
           "Reward pools **offered** by Polymarket (not what we were paid), from what the bot recorded. "
           "Report-only; not connected to trading.", "",
           f"_Generated {generated_at[:16]}Z._", ""]
    out += ["## Alerts (last 2 days)", ""]
    if alerts:
        for r in alerts:
            if r["alert"] == "NEW":
                out.append(f"- **NEW** `{r['family']}` ({r['period']}): ${r['latest']:,.0f} (first seen {r['first_seen']})")
            else:
                out.append(f"- **CHANGED** `{r['family']}` ({r['period']}): ${r['previous']:,.0f} → "
                           f"${r['latest']:,.0f} ({r['change']:+.0%}) on {r['changed_on']}")
    else:
        out.append("- No pool changes or new programs.")
    changed = [r for r in rows if r["previous"] is not None]
    out += ["", "## Pools that changed (all history)", "",
            "| Program | Period | Before | Now | Change | Since |", "|---|---|---|---|---|---|"]
    for r in sorted(changed, key=lambda r: r["change"] or 0):
        out.append(f"| {r['family']} | {r['period']} | ${r['previous']:,.0f} | ${r['latest']:,.0f} | "
                   f"{r['change']:+.0%} | {r['changed_on']} |")
    days = sorted({d for r in rows for d, _ in r["series"]})[-8:]
    out += ["", "## Pool offered per day (programs the bot saw)", "",
            "| Program | Period | " + " | ".join(d[5:] for d in days) + " |",
            "|---|---|" + "---|" * len(days)]
    for r in sorted(rows, key=lambda r: (r["period"], r["family"])):
        s = dict(r["series"])
        out.append(f"| {r['family']} | {r['period']} | " +
                   " | ".join(f"{s[d]:,.0f}" if d in s else "-" for d in days) + " |")
    return "\n".join(out) + "\n"


def read_observations(db: Path, since: str) -> list[tuple]:
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("PRAGMA query_only=ON")
        return conn.execute(
            "SELECT json_extract(observation_json,'$.program_id'), json_extract(observation_json,'$.reward_period'),"
            " json_extract(observation_json,'$.reward_pool'), observed_at FROM execution_quote_observations"
            " WHERE observed_at>=?", (since,)).fetchall()
    finally:
        conn.close()


def cron_line(root: Path) -> str:
    return (f"20 */3 * * * cd {root} && nice -n 10 ionice -c3 .venv/bin/python -B scripts/reward_pool_tracker.py "
            f"--db data/commissioning.db --archive data/market-quality/*.sqlite3 "
            f">> logs/reward-pool-tracker.log 2>&1 {CRON_MARK}")


def install_cron(root: Path) -> int:
    import subprocess
    from scripts.rotate_logs import with_cron_line
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = current.stdout if current.returncode == 0 else ""
    return subprocess.run(["crontab", "-"], text=True, input=with_cron_line(
        existing, cron_line(root), mark=CRON_MARK)).returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path)
    p.add_argument("--archive", type=Path, action="extend", nargs="+", default=[])
    p.add_argument("--since", default="2026-09-14")
    p.add_argument("--output-root", type=Path, default=Path("data/reward-pool-tracker"))
    p.add_argument("--install-cron", action="store_true", help="install the 3-hourly run")
    a = p.parse_args(argv)
    if a.install_cron:
        return install_cron(Path.cwd().resolve())
    if a.db is None:
        p.error("--db is required")
    from scripts.calibration_report import read_archive_records
    now = datetime.now(UTC)
    discovery = read_archive_records(a.archive, {"discovery"}) if a.archive else []
    rows = summarize(collect(read_observations(a.db, a.since), discovery), today=now.date().isoformat())
    a.output_root.mkdir(parents=True, exist_ok=True)
    text = render(rows, generated_at=now.isoformat(), today=now.date().isoformat())
    (a.output_root / "latest.md").write_text(text, encoding="utf8")
    (a.output_root / "latest.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf8")
    (a.output_root / f"{now:%Y-%m-%d}.md").write_text(text, encoding="utf8")
    alerts = [r for r in rows if r["alert"]]
    print(f"Reward Pool Tracker: {len(rows)} programs, {len(alerts)} alerts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
