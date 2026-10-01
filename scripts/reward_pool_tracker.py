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


SITE_URL = "https://polymarket.us/rewards"
SITE_PERIODS = {"live": "live", "day-of": "day_of", "early": "early",
                "daily (per event)": "daily_event", "daily": "daily"}
_PROGRAM_ID = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*_\d{8}(?:_v\d+)?")
OUR_FAMILIES = ("table_tennis_moneyline_day_of", "itf_moneyline_dayof", "atp_wta_moneyline_day_of",
                "esports_moneyline_pre_game", "doubles_moneyline_day_of", "darts_moneyline_day_of")
SCORE_REFERENCE_DAYS = 30


def parse_site(html: str) -> list[dict]:
    """Program rows from the public rewards page (one row per program)."""
    out = []
    for chunk in html.split("<tr")[1:]:
        chunk = chunk[chunk.find(">") + 1:]          # drop the <tr ...> attributes
        cells = [[t.strip() for t in re.sub(r"<[^>]+>", "\n", c).replace("&amp;", "&").split("\n")
                  if t.strip()] for c in chunk.split("</td>")[:-1]]
        if len(cells) < 7 or not cells[0]:
            continue
        ids = [t for t in cells[0] if _PROGRAM_ID.fullmatch(t)]
        period = SITE_PERIODS.get(" ".join(cells[2]).strip().lower())
        try:
            pool = float("".join(cells[3]).replace("$", "").replace(",", ""))
            markets = int("".join(cells[6]).replace(",", ""))
        except ValueError:
            continue
        if ids and period:
            name = next((t for t in cells[0] if t not in ids and t not in ("▸", "▾")), "").lstrip("▸▾ ")
            out.append(dict(program_id=ids[0], family=family(ids[0]), period=period,
                            pool=pool, markets=markets, name=name))
    return out


def fetch_site(url: str = SITE_URL, timeout: float = 60.0) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (reward-pool-tracker)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def score(current: list[dict], collected: dict, *, today: str,
          reference_days: int = SCORE_REFERENCE_DAYS) -> dict:
    """Pool score 1-10: offered pools now vs each program's best day in the last 30 days.

    Fixed definition (operator, 2026-10-01: a 1-10 measure that may later decide
    whether we trade). For every program on the rewards page now, its reference
    is the highest daily pool its family/period showed in the last
    ``reference_days`` (rewards-page snapshots plus what the bot recorded). The
    score is 10 x sum(now) / sum(reference), rounded to one decimal and kept in
    [1, 10]. A program seen for the first time counts at its own level.
    "All" uses every program on the page; "ours" uses OUR_FAMILIES (pre-game).
    """
    start = (date.fromisoformat(today) - timedelta(days=reference_days)).isoformat()

    def ref(fam, period, now_pool):
        days = collected["seen"].get((fam, period), {})
        best = [day_pool(c) for d, c in days.items() if d >= start]
        return max(best + [now_pool])

    def part(rows):
        now = sum(r["pool"] for r in rows)
        reference = sum(ref(r["family"], r["period"], r["pool"]) for r in rows)
        value = 10 * now / reference if reference else 10.0
        return dict(score=round(min(10.0, max(1.0, value)), 1), now=now, reference=reference, programs=len(rows))

    ours = [r for r in current if r["family"] in OUR_FAMILIES and r["period"] == "day_of"]
    result = dict(all=part(current), ours=part(ours))
    result["profitable"] = profit_verdict(result["ours"]["score"])
    return result


def profit_verdict(our_score: float) -> str:
    """Operator rule (2026-10-01): an our-markets score of 5 or under means NOT profitable.

    Anchors: 10/10 = the pools before Sep 24 (net about +$112/day); 4.5-4.7/10 =
    the pools since (net about break-even, Sep 25-29). 5-8 is in between and
    unproven, so it is MAYBE until score-history.jsonl plus daily net confirm it.
    """
    return "NO" if our_score <= 5.0 else "MAYBE" if our_score < 8.0 else "YES"


def collect(observations: list[tuple], discovery: list[dict], site: list[dict] = ()) -> dict:
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
    bot_keys = set(seen)
    site_start = min((str(s.get("at", ""))[:10] for s in site), default=None)
    for snap in site:                      # rewards-page snapshots: {at, programs: [...]}
        at = str(snap.get("at", ""))
        for p in snap.get("programs") or []:
            note(p["program_id"], p["period"], p["pool"], at[:10], at)
    return dict(seen=seen, latest_id={k: v[0] for k, v in latest_id.items()},
                bot_keys=bot_keys, site_start=site_start)


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
        # A program first seen on the day rewards-page tracking began is not "new":
        # it was simply not visible to the bot before.
        site_start = collected.get("site_start")
        genuinely_new = ((fam, period) in collected.get("bot_keys", set())
                         or site_start is None or first_seen > site_start)
        alert = ("CHANGED" if changed_on and changed_on >= cutoff else
                 "NEW" if previous is None and first_seen >= cutoff and genuinely_new else "")
        rows.append(dict(
            family=fam, period=period, latest=latest, latest_day=latest_day,
            previous=previous, change=(None if previous in (None, 0) else (latest - previous) / previous),
            changed_on=changed_on, first_seen=first_seen, alert=alert,
            program_id=collected["latest_id"].get((fam, period)), series=series))
    rows.sort(key=lambda r: (r["alert"] == "", -abs(r["change"] or 0), r["family"]))
    return rows


def render(rows: list[dict], *, generated_at: str, today: str, pool_score: dict | None = None,
           site_programs: int = 0, site_error: str | None = None) -> str:
    alerts = [r for r in rows if r["alert"]]
    out = [f"# Reward Pool Tracker — {today}", "",
           "Reward pools **offered** by Polymarket (not what we were paid), from the public rewards page "
           "and what the bot recorded. Report-only; not connected to trading.", "",
           f"_Generated {generated_at[:16]}Z; {site_programs} programs on the rewards page"
           + (f"; rewards page unavailable ({site_error})" if site_error else "") + "._", ""]
    if pool_score:
        a, o = pool_score["all"], pool_score["ours"]
        out += ["## Pool score (1-10: offered pools now vs each program's best day in the last 30 days)", "",
                f"- **Our markets: {o['score']}/10 — profitable: {pool_score['profitable']}** "
                f"(${o['now']:,.0f} offered now vs ${o['reference']:,.0f} at best; {o['programs']} pre-game "
                "programs we trade; 5 or under = not profitable)",
                f"- **All rewarded markets: {a['score']}/10** — ${a['now']:,.0f} offered now vs "
                f"${a['reference']:,.0f} at best ({a['programs']} programs; history builds from the "
                "first rewards-page snapshot, so programs the bot never saw start at 10)", ""]
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
    p.add_argument("--no-site", action="store_true", help="skip reading the public rewards page")
    a = p.parse_args(argv)
    if a.install_cron:
        return install_cron(Path.cwd().resolve())
    if a.db is None:
        p.error("--db is required")
    from scripts.calibration_report import read_archive_records
    now = datetime.now(UTC)
    today = now.date().isoformat()
    a.output_root.mkdir(parents=True, exist_ok=True)
    site_dir = a.output_root / "site"
    site_dir.mkdir(exist_ok=True)
    current, site_error = [], None
    if not a.no_site:
        try:
            current = parse_site(fetch_site())
            if not current:
                raise ValueError("no programs parsed from the rewards page")
            (site_dir / f"{now:%Y%m%dT%H%M%SZ}.json").write_text(
                json.dumps(dict(at=now.isoformat(), programs=current)), encoding="utf8")
        except Exception as exc:             # reporting only: fall back to bot data
            site_error = f"{type(exc).__name__}: {exc}"[:200]
            current = []
    cutoff = (now - timedelta(days=SCORE_REFERENCE_DAYS + 5)).strftime("%Y%m%dT")
    snaps = []
    for f in sorted(site_dir.glob("*.json")):
        if f.name < cutoff:
            f.unlink()
            continue
        try:
            snaps.append(json.loads(f.read_text(encoding="utf8")))
        except ValueError:
            continue
    if not current and snaps and (now - datetime.fromisoformat(snaps[-1]["at"])).total_seconds() <= 12 * 3600:
        current = snaps[-1]["programs"]
    discovery = read_archive_records(a.archive, {"discovery"}) if a.archive else []
    collected = collect(read_observations(a.db, a.since), discovery, snaps)
    rows = summarize(collected, today=today)
    pool_score = score(current, collected, today=today) if current else None
    text = render(rows, generated_at=now.isoformat(), today=today, pool_score=pool_score,
                  site_programs=len(current), site_error=site_error)
    (a.output_root / "latest.md").write_text(text, encoding="utf8")
    (a.output_root / "latest.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf8")
    (a.output_root / f"{now:%Y-%m-%d}.md").write_text(text, encoding="utf8")
    if pool_score:
        record = dict(at=now.isoformat(), **pool_score)
        (a.output_root / "latest-score.json").write_text(json.dumps(record, indent=1), encoding="utf8")
        with (a.output_root / "score-history.jsonl").open("a", encoding="utf8") as fh:
            fh.write(json.dumps(record) + "\n")
    alerts = [r for r in rows if r["alert"]]
    print(f"Reward Pool Tracker: {len(rows)} programs tracked, {len(current)} on the rewards page, "
          f"{len(alerts)} alerts, score all={pool_score['all']['score'] if pool_score else '-'} "
          f"ours={pool_score['ours']['score'] if pool_score else '-'}" + (f"; site error {site_error}" if site_error else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
