#!/usr/bin/env python3
"""Offline calibration report: what the bot predicted versus what happened.

Read-only ledger snapshot plus optional market-quality archives and logs. It
makes no API request and has no trading consumer; it only produces evidence
for operator decisions (fill model refresh, entry-quality threshold, entry
horizon, pending-entry caps). Sections:

1. fill_model   - observed first-fill rate per 30 s of resting exposure versus
                  the live model's prediction, in the model's own cells.
2. episodes     - every position from open to flat: P&L, hold time and exit
                  path, attributed to the entry quote's decision-time features.
3. markouts     - price moves after our fills (archive kind ``fill_markout``).
4. suppressed   - entries the cap blocked (archive kind ``entry_suppressed``).
5. coverage     - share of time at the pending-entry cap and the shortfall
                  below it (needs ``--log``; reads ``[window-risk]`` lines).

    .venv/bin/python -B scripts/calibration_report.py --db data/commissioning.db \\
        --days 7 --archive data/market-quality/*.sqlite3 --log logs/commissioning.log
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import zlib
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from liquidity_bot.fill_hazard import (  # noqa: E402
    CALIBRATION_INTERVAL_SECONDS, _price_bin, _queue_bin, _spread_bin,
    _time_to_event_bin, league_from_slug)
from liquidity_bot.pnl import MatchedInventory  # noqa: E402

UTC = timezone.utc
WORKING = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
PROBABILITY_BUCKETS = (0.01, 0.02, 0.05, 0.10, 0.20)
RETAINED_BUCKETS = (0.6, 0.7, 0.8, 0.9)
MARKOUT_HORIZONS = (30, 120, 600)


def stamp(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(UTC)


def bucket(value: float | None, edges: Iterable[float], *, percent=False) -> str:
    if value is None:
        return "unknown"
    fmt = (lambda x: f"{x:.0%}") if percent else (lambda x: f"{x:g}")
    lower = None
    for edge in edges:
        if value < edge:
            return f"<{fmt(edge)}" if lower is None else f"{fmt(lower)}-{fmt(edge)}"
        lower = edge
    return f">={fmt(lower)}"


def kickoff_bin(seconds: float | None) -> str:
    """The model's time bins, with its open-ended top split for the 6 h horizon."""
    if seconds is None:
        return "unknown"
    if seconds >= 4 * 3600:
        return "240m_plus"
    if seconds >= 2 * 3600:
        return "120_240m"
    return _time_to_event_bin(seconds)


# --------------------------------------------------------------------- input

def read_ledger(db: Path) -> dict:
    """One short read-only transaction; observation JSON is not loaded whole."""
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        data = {
            "observations": [dict(r) for r in conn.execute(
                "SELECT client_order_id, market_slug, side, observed_at, quote_price,"
                " fill_probability, fill_risk_driver, model_queue_ahead,"
                " retained_value_ratio, would_refuse_retained_70, projected_reward,"
                " json_extract(observation_json,'$.best_bid') AS best_bid,"
                " json_extract(observation_json,'$.best_offer') AS best_offer,"
                " json_extract(observation_json,'$.seconds_to_event') AS seconds_to_event"
                " FROM execution_quote_observations")],
            "orders": [dict(r) for r in conn.execute(
                "SELECT client_order_id, exchange_order_id, market_slug, side, status,"
                " event_at FROM execution_orders")],
            "intents": [dict(r) for r in conn.execute(
                "SELECT client_order_id, created_at, quote_observation_required"
                " FROM execution_intents WHERE operation='PLACE'")],
            "fills": [dict(r) for r in conn.execute(
                "SELECT execution_id, client_order_id, market_slug, side, price, qty, fee,"
                " liquidity, intent, event_at FROM execution_fills WHERE reversed=0"
                " ORDER BY event_at, received_at, execution_id")],
        }
        conn.rollback()
    finally:
        conn.close()
    return data


def read_archive_records(paths: Iterable[Path], kinds: set[str]) -> list[dict]:
    """Only the requested kinds, checksum-verified; large discovery rows are skipped."""
    out = []
    marks = ",".join("?" * len(kinds))
    for path in paths:
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(f"SELECT kind, body, sha256 FROM records WHERE kind IN ({marks})",
                                tuple(sorted(kinds))).fetchall()
        finally:
            conn.close()
        for kind, body, digest in rows:
            if isinstance(body, bytes):  # large records are zlib-packed by the store
                from liquidity_bot.market_quality_store import MAX_DISCOVERY_BYTES, PACKED
                if not body.startswith(PACKED):
                    raise ValueError(f"unknown archive encoding: {path}")
                unpacker = zlib.decompressobj()
                raw = unpacker.decompress(body[len(PACKED):], MAX_DISCOVERY_BYTES + 1)
                if len(raw) > MAX_DISCOVERY_BYTES or not unpacker.eof:
                    raise ValueError(f"invalid compressed archive record: {path}")
                body = raw.decode("utf8")
            if hashlib.sha256(body.encode()).hexdigest() != digest:
                raise ValueError(f"quality archive record failed verification: {path}")
            payload = json.loads(body)
            if isinstance(payload, dict):
                out.append(dict(payload, kind=kind))
    return out


def read_cap_timeline(paths: Iterable[Path]) -> list[tuple[datetime, int]]:
    pattern = re.compile(r"^\[window-risk\] at=(\S+) .* cap=(\d+)")
    caps = []
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("[window-risk]"):
                    match = pattern.match(line)
                    if match:
                        caps.append((stamp(match.group(1)), int(match.group(2))))
    return sorted(caps)


# ------------------------------------------------------------------ sections

def first_fill_by_order(fills: list[dict]) -> dict[str, datetime]:
    first: dict[str, datetime] = {}
    for fill in fills:
        key = fill.get("client_order_id")
        if key:
            at = stamp(fill["event_at"])
            if key not in first or at < first[key]:
                first[key] = at
    return first


def fill_model_calibration(data: dict, *, since: datetime, now: datetime) -> dict:
    """First-fill hazard per 30 s of exposure, in the live model's cells.

    Exposure runs from the quote observation to the first fill, else to the
    order's terminal event, else to ``now``; a fill ends exposure (censoring
    matches the model's maker-lab calibration).
    """
    orders = {o["client_order_id"]: o for o in data["orders"] if o["client_order_id"]}
    first_fill = first_fill_by_order(data["fills"])
    cells: dict[str, dict] = defaultdict(lambda: dict(
        orders=0, exposure_seconds=0.0, fills=0, predicted_fills=0.0))
    reliability: dict[str, dict] = defaultdict(lambda: dict(
        orders=0, exposure_seconds=0.0, fills=0, predicted_fills=0.0))
    skipped = 0
    for obs in data["observations"]:
        start = stamp(obs["observed_at"])
        order = orders.get(obs["client_order_id"])
        if start < since or order is None or obs["fill_probability"] is None:
            skipped += 1
            continue
        filled_at = first_fill.get(obs["client_order_id"])
        if filled_at is not None and filled_at >= start:
            end, filled = filled_at, 1
        elif order["status"] not in WORKING and order["event_at"]:
            end, filled = stamp(order["event_at"]), 0
        else:
            end, filled = now, 0
        exposure = max((end - start).total_seconds(), 0.0)
        intervals = exposure / CALIBRATION_INTERVAL_SECONDS
        probability = float(obs["fill_probability"])
        side = str(obs["side"]).lower()
        price = float(obs["quote_price"])
        keys = ["global", f"side:{side}",
                f"queue:{_queue_bin(max(float(obs['model_queue_ahead'] or 0), 0.0))}"]
        if obs["seconds_to_event"] is not None:
            keys.append(f"time_to_event:{_time_to_event_bin(max(float(obs['seconds_to_event']), 0.0))}")
        league = league_from_slug(obs["market_slug"])
        keys.append(f"league:{league}" if league else "league:other")
        price_bin = _price_bin(price) if 0 < price < 1 else None
        if price_bin:
            keys.append(f"price:{price_bin}")
        if obs["best_bid"] is not None and obs["best_offer"] is not None:
            keys.append(f"spread:{_spread_bin(max(float(obs['best_offer']) - float(obs['best_bid']), 0.0))}")
        for target in [cells[k] for k in keys] + [
                reliability[bucket(probability, PROBABILITY_BUCKETS, percent=True)]]:
            target["orders"] += 1
            target["exposure_seconds"] += exposure
            target["fills"] += filled
            target["predicted_fills"] += probability * intervals

    def finish(table):
        rows = []
        for key, c in sorted(table.items()):
            intervals = c["exposure_seconds"] / CALIBRATION_INTERVAL_SECONDS
            observed = c["fills"] / intervals if intervals else None
            predicted = c["predicted_fills"] / intervals if intervals else None
            rows.append(dict(cell=key, orders=c["orders"], fills=c["fills"],
                             exposure_hours=round(c["exposure_seconds"] / 3600, 2),
                             observed_per_30s=observed, predicted_per_30s=predicted,
                             observed_over_predicted=(observed / predicted
                                                      if observed is not None and predicted else None)))
        return rows
    return dict(cells=finish(cells), by_predicted_probability=finish(reliability),
                skipped_observations=skipped)


def position_episodes(data: dict) -> list[dict]:
    """Replay every fill once; one episode per flat-to-flat position."""
    inventories: dict[str, MatchedInventory] = {}
    open_episode: dict[str, dict] = {}
    episodes: list[dict] = []
    for fill in data["fills"]:
        slug = fill["market_slug"]
        inventory = inventories.setdefault(slug, MatchedInventory())
        before = inventory.position
        realized = inventory.apply(intent=str(fill["intent"] or ""), price=str(fill["price"]),
                                   quantity=str(fill["qty"]), fee=str(fill["fee"] or 0))
        at = stamp(fill["event_at"])
        episode = open_episode.get(slug)
        if episode is None:
            episode = dict(market=slug, opened_at=at, entry_client_order_id=fill["client_order_id"],
                           entry_qty=Decimal(0), pnl=Decimal(0), closing_liquidity=set(),
                           closed_at=None, max_abs_position=Decimal(0))
            open_episode[slug] = episode
        episode["pnl"] += realized
        if before == 0 or (before > 0) == (inventory.position > 0) and abs(inventory.position) > abs(before):
            episode["entry_qty"] += Decimal(str(fill["qty"]))
        elif before != 0:
            episode["closing_liquidity"].add(str(fill["liquidity"] or "UNKNOWN").upper())
        episode["max_abs_position"] = max(episode["max_abs_position"], abs(inventory.position))
        if inventory.position == 0 or (before != 0 and (before > 0) != (inventory.position > 0)):
            episode["closed_at"] = at
            episodes.append(open_episode.pop(slug))
            if inventory.position != 0:  # flipped through zero: the rest opens anew
                open_episode[slug] = dict(
                    market=slug, opened_at=at, entry_client_order_id=fill["client_order_id"],
                    entry_qty=abs(inventory.position), pnl=Decimal(0), closing_liquidity=set(),
                    closed_at=None, max_abs_position=abs(inventory.position))
    episodes.extend(open_episode.values())
    for episode in episodes:
        liquidity = episode.pop("closing_liquidity")
        episode["exit"] = ("open_or_settled" if episode["closed_at"] is None else
                           "taker_exit" if "TAKER" in liquidity else
                           "passive_reducer" if liquidity == {"MAKER"} else "other")
    return episodes


def episode_summary(data: dict, *, since: datetime) -> dict:
    observations = {o["client_order_id"]: o for o in data["observations"]}
    building = {i["client_order_id"] for i in data["intents"]
                if i["quote_observation_required"]}
    tables: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: dict(
        episodes=0, closed=0, wins=0, pnl=Decimal(0), hold_seconds=0.0)))
    counts = dict(owned_entry=0, other_entry=0)
    for episode in position_episodes(data):
        if episode["opened_at"] < since:
            continue
        entry = episode["entry_client_order_id"]
        obs = observations.get(entry)
        if entry not in building or obs is None:
            counts["other_entry"] += 1
            continue
        counts["owned_entry"] += 1
        league = league_from_slug(episode["market"]) or "other"
        seconds = obs["seconds_to_event"]
        dims = {
            "all": "all",
            "exit": episode["exit"],
            "league": league,
            "kickoff_at_entry": kickoff_bin(float(seconds) if seconds is not None else None),
            "retained_value": bucket(obs["retained_value_ratio"], RETAINED_BUCKETS, percent=True),
            "would_refuse_at_70": {1: "yes", 0: "no"}.get(obs["would_refuse_retained_70"], "unknown"),
            "predicted_fill": bucket(obs["fill_probability"], PROBABILITY_BUCKETS, percent=True),
        }
        closed = episode["closed_at"] is not None
        for dim, key in dims.items():
            row = tables[dim][key]
            row["episodes"] += 1
            if closed:
                row["closed"] += 1
                row["wins"] += episode["pnl"] > 0
                row["pnl"] += episode["pnl"]
                row["hold_seconds"] += (episode["closed_at"] - episode["opened_at"]).total_seconds()
    result = {}
    for dim, rows in tables.items():
        result[dim] = [dict(key=key, episodes=r["episodes"], closed=r["closed"],
                            closed_pnl=r["pnl"],
                            pnl_per_closed=(r["pnl"] / r["closed"]) if r["closed"] else None,
                            win_rate=(r["wins"] / r["closed"]) if r["closed"] else None,
                            avg_hold_minutes=(r["hold_seconds"] / r["closed"] / 60) if r["closed"] else None)
                       for key, r in sorted(rows.items())]
    result["counts"] = counts
    return result


def markout_summary(records: list[dict], *, since: datetime) -> dict:
    """Positive markout = the market moved in our favour after the fill."""
    groups: dict[tuple, dict] = defaultdict(lambda: dict(samples=0, total=0.0, adverse=0))
    missing = 0
    for r in records:
        if r.get("kind") != "fill_markout" or stamp(r["fill_at"]) < since:
            continue
        if r.get("mid") is None:
            missing += 1
            continue
        signed = (float(r["mid"]) - float(r["price"])) * (1 if r["side"] == "ORDER_SIDE_BUY" else -1)
        for key in (("all", r["role"], r["horizon_seconds"]),
                    (league_from_slug(r["market"]) or "other", r["role"], r["horizon_seconds"])):
            g = groups[key]
            g["samples"] += 1
            g["total"] += signed
            g["adverse"] += signed < 0
    rows = [dict(group=k[0], role=k[1], horizon_seconds=k[2], samples=g["samples"],
                 avg_markout_cents=round(100 * g["total"] / g["samples"], 3),
                 adverse_share=g["adverse"] / g["samples"])
            for k, g in sorted(groups.items(), key=lambda i: (i[0][0] != "all", i[0]))]
    return dict(rows=rows, samples_without_book=missing)


def suppressed_summary(records: list[dict], *, since: datetime) -> dict:
    by: dict[tuple, int] = defaultdict(int)
    sides = set()
    for r in records:
        if r.get("kind") != "entry_suppressed" or stamp(r["at"]) < since:
            continue
        by[("reason", r["reason"])] += 1
        by[("league", league_from_slug(r["market"]) or "other")] += 1
        by[("kickoff", kickoff_bin(r.get("seconds_to_event")))] += 1
        sides.add((r["market"], r["side"]))
    return dict(total=sum(v for (d, _), v in by.items() if d == "reason"),
                distinct_market_sides=len(sides),
                rows=[dict(dimension=d, key=k, suppressions=v) for (d, k), v in sorted(by.items())])


def coverage_summary(data: dict, caps: list[tuple[datetime, int]], *,
                     since: datetime, now: datetime, step_seconds: int = 30) -> dict:
    """Time share at the pending-entry cap and mean shortfall below it."""
    building = {i["client_order_id"]: stamp(i["created_at"]) for i in data["intents"]
                if i["quote_observation_required"]}
    intervals = []
    for order in data["orders"]:
        start = building.get(order["client_order_id"])
        if start is None:
            continue
        end = (now if order["status"] in WORKING or not order["event_at"]
               else stamp(order["event_at"]))
        if end > since:
            intervals.append((start, end))
    days: dict[str, dict] = defaultdict(lambda: dict(samples=0, at_cap=0, shortfall=0))
    if not caps:
        return dict(days=[], note="no [window-risk] cap lines supplied")
    t, index = max(since, caps[0][0]), 0
    while t < now:
        while index + 1 < len(caps) and caps[index + 1][0] <= t:
            index += 1
        cap = caps[index][1]
        working = sum(1 for a, b in intervals if a <= t < b)
        day = days[t.date().isoformat()]
        day["samples"] += 1
        day["at_cap"] += working >= cap
        day["shortfall"] += max(cap - working, 0)
        t += timedelta(seconds=step_seconds)
    return dict(days=[dict(day=d, samples=v["samples"], at_cap_share=v["at_cap"] / v["samples"],
                           mean_shortfall=v["shortfall"] / v["samples"])
                      for d, v in sorted(days.items())])


# -------------------------------------------------------------------- output

def build_report(data: dict, records: list[dict], caps: list[tuple[datetime, int]], *,
                 since: datetime, now: datetime) -> dict:
    return dict(
        generated_at=now.isoformat(), since=since.isoformat(), api_requests=0,
        enforcement="none",
        fill_model=fill_model_calibration(data, since=since, now=now),
        episodes=episode_summary(data, since=since),
        markouts=markout_summary(records, since=since),
        suppressed=suppressed_summary(records, since=since),
        coverage=coverage_summary(data, caps, since=since, now=now) if caps else
        dict(days=[], note="no --log supplied"),
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}" if abs(value) < 1 else f"{value:.2f}"
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value)


def render(report: dict) -> str:
    out = [f"# Calibration report — since {report['since']}",
           "", "Read-only; 0 API requests; evidence only (no setting is changed).", ""]

    def table(title, rows, columns):
        out.extend([f"## {title}", "", "| " + " | ".join(columns) + " |",
                    "|" + "---|" * len(columns)])
        out.extend("| " + " | ".join(_fmt(r.get(c)) for c in columns) + " |" for r in rows)
        out.append("")

    fm = report["fill_model"]
    cols = ["cell", "orders", "fills", "exposure_hours", "observed_per_30s",
            "predicted_per_30s", "observed_over_predicted"]
    table("Fill model: observed vs predicted first-fill rate per 30 s", fm["cells"], cols)
    table("Fill model by predicted probability", [dict(r, cell=r["cell"]) for r in fm["by_predicted_probability"]], cols)
    ep = report["episodes"]
    ecols = ["key", "episodes", "closed", "closed_pnl", "pnl_per_closed", "win_rate", "avg_hold_minutes"]
    for dim in ("all", "exit", "retained_value", "would_refuse_at_70", "kickoff_at_entry",
                "predicted_fill", "league"):
        table(f"Positions by {dim.replace('_', ' ')}", ep.get(dim, []), ecols)
    table("Markouts after fills (cents per contract; positive = favourable)",
          report["markouts"]["rows"],
          ["group", "role", "horizon_seconds", "samples", "avg_markout_cents", "adverse_share"])
    table("Entries blocked by the pending-entry cap", report["suppressed"]["rows"],
          ["dimension", "key", "suppressions"])
    table("Pending-entry cap coverage", report["coverage"].get("days", []),
          ["day", "samples", "at_cap_share", "mean_shortfall"])
    return "\n".join(out)


def encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


CRON_MARK = "# liquidity-bot calibration report"


def cron_line(root: Path) -> str:
    return (f"40 4 * * * cd {root} && nice -n 10 ionice -c3 .venv/bin/python -B "
            "scripts/calibration_report.py --db data/commissioning.db --days 7 "
            "--archive data/market-quality/*.sqlite3 "
            "--log $(ls logs/commissioning.log-2* 2>/dev/null | grep -v gz$ | tail -1) "
            f"logs/commissioning.log >> logs/calibration-report.log 2>&1 {CRON_MARK}")


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
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--archive", type=Path, action="extend", nargs="+", default=[])
    p.add_argument("--log", type=Path, action="extend", nargs="+", default=[])
    p.add_argument("--install-cron", action="store_true",
                   help="install the daily 04:40 run for this checkout (idempotent)")
    p.add_argument("--output-root", type=Path, default=Path("data/calibration-reports"))
    a = p.parse_args(argv)
    if a.install_cron:
        return install_cron(Path.cwd().resolve())
    if a.db is None:
        p.error("--db is required")
    if not 1 <= a.days <= 90:
        p.error("days must be 1..90")
    now = datetime.now(UTC)
    since = now - timedelta(days=a.days)
    records = read_archive_records(a.archive, {"fill_markout", "entry_suppressed"})
    report = build_report(read_ledger(a.db), records, read_cap_timeline(a.log),
                          since=since, now=now)
    out = a.output_root / now.strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.json").write_text(json.dumps(report, default=encode, indent=1), encoding="utf8")
    (out / "report.md").write_text(render(report), encoding="utf8")
    print(f"Report: {out}; enforcement=none; API requests=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
