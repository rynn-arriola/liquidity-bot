#!/usr/bin/env python3
"""Measurement-only market economics and prospective shadow comparison.

No order/ledger writer, strategy import, recurring job or trading consumer.
Default offline; --fetch-earnings makes one governed account-wide report read.
Each run produces a NEW private artifact directory, never overwrites a ledger.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from liquidity_bot.pnl import MatchedInventory, intent_direction  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
D = Decimal
ZERO = D(0)
WORKING = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}
TERMINAL = {"FILLED", "CANCELED", "CANCELLED", "EXPIRED"}
ALIASES = {"day-of": "day_of", "dayof": "day_of", "daily-event": "daily_event"}


def number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("missing/boolean number")
    try:
        result = D(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid number") from exc
    if not result.is_finite():
        raise ValueError("nonfinite number")
    return result


def stamp(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(UTC)


def boundary(day: date) -> datetime:
    return datetime.combine(day, time.min, ET).astimezone(UTC)


def period(value: str) -> str:
    return ALIASES.get(value, value)


def league(slug: str) -> str:
    parts = slug.split("-")
    return parts[1] if len(parts) > 2 and parts[0] == "aec" else "unclassified"


def signature(doc: dict) -> tuple[str, ...]:
    """Never silently transfer old-program, pool, size or discount economics."""
    p = period(str(doc["period"]))
    if p not in {"day_of", "daily_event"} or not doc["program"]:
        raise ValueError("unsupported/missing program")
    values = [number(doc[k]) for k in ("pool", "target", "discount", "quantity")]
    if any(v <= 0 for v in values) or values[2] > 1:
        raise ValueError("invalid program economics")
    return (p, str(doc["program"]), league(str(doc["market"])),
            *(str(v.normalize()) for v in values))


def empty_row(day: str, slug: str) -> dict:
    return {"day": day, "market": slug, "league": league(slug),
            "paid": None, "pending": None, "skipped": None,
            "reward_status": "MISSING", "reward_observed_at": None,
            "closed_fill_pnl": ZERO, "fill_fees": ZERO, "fills": 0,
            "closing_fills": 0, "losing_closes": 0,
            "worst_close": ZERO, "unowned_fills": 0,
            "side_hours": ZERO, "quantity_hours": ZERO,
            "placement_model_reward": ZERO,
            "programs": set(), "periods": set(), "signatures": set(),
            "entry_time_bands": set(), "selection_ranks": set(),
            "flags": set(), "training_eligible": False}


def latest_cached_earnings(conn: sqlite3.Connection, as_of: datetime) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM execution_reward_earnings")]
    latest: dict[tuple, datetime] = {}
    for r in rows:
        at = stamp(r["observed_at"])
        if at > as_of:
            continue
        key = (r["date"], r["market_slug"], r["program_type"])
        latest[key] = max(latest.get(key, at), at)
    return [r for r in rows if latest.get((r["date"], r["market_slug"], r["program_type"])) == stamp(r["observed_at"])]


def read_ledger(db: Path) -> dict:
    """One consistent, short read transaction, no ExecutionLedger constructor."""
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        result = {"capture_started_at": datetime.now(UTC).isoformat()}
        queries = {
            "intents": "SELECT client_order_id,operation,market_slug,created_at,quote_observation_required FROM execution_intents",
            "orders": "SELECT * FROM execution_orders",
            "observations": "SELECT client_order_id,market_slug,side,observed_at,observation_json FROM execution_quote_observations",
            "fills": "SELECT * FROM execution_fills WHERE reversed=0",
        }
        for key, query in queries.items():
            result[key] = [dict(row) for row in conn.execute(query)]
        result["cached_earnings"] = latest_cached_earnings(conn, datetime.now(UTC))
        result["capture_finished_at"] = datetime.now(UTC).isoformat()
        conn.rollback()
    finally:
        conn.close()
    return result


def analyze(data: dict, earnings: list[dict], *, first: date, last: date,
            as_of: datetime) -> dict:
    """Retrospective evidence, not a historical as-known backtest."""
    as_of = stamp(as_of.isoformat())
    if first > last or last >= as_of.astimezone(ET).date():
        raise ValueError("analysis requires completed ET days")
    start, end = boundary(first), boundary(last + timedelta(days=1))
    cells: dict[tuple, dict] = {}
    diagnostics: dict[str, int] = defaultdict(int)

    def cell(day: str, slug: str) -> dict | None:
        if first.isoformat() <= day <= last.isoformat():
            return cells.setdefault((day, slug), empty_row(day, slug))
        return None

    def at_cell(at: datetime, slug: str) -> dict | None:
        return cell(at.astimezone(ET).date().isoformat(), slug)

    # The latest complete external snapshot OR explicitly dated cached rows.
    reward_groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in earnings:
        if r.get("program_type") != "liquidityProgram":
            continue
        day = date.fromisoformat(str(r["date"])).isoformat()
        if first.isoformat() <= day <= last.isoformat():
            reward_groups[(day, str(r["market_slug"]))].append(r)
    for (day, slug), rows in reward_groups.items():
        f = cell(day, slug)
        try:
            statuses = [str(r["status"]).upper() for r in rows]
            if len(set(statuses)) != len(statuses) or not set(statuses) <= {"PAID", "PENDING", "SKIPPED"}:
                raise ValueError("duplicate/unknown reward status")
            observed = [stamp(r["observed_at"]) for r in rows]
            if max(observed) > as_of or len(set(observed)) != 1:
                raise ValueError("future/mixed reward snapshot")
            amounts = [number(r["reward"]) for r in rows]
            if any(v < 0 for v in amounts):
                raise ValueError("negative reward")
            f.update(paid=ZERO, pending=ZERO, skipped=ZERO)
            for status, amount in zip(statuses, amounts):
                f[status.lower()] += amount
            f["reward_observed_at"] = observed[0].isoformat()
            f["reward_status"] = "+".join(sorted(statuses))
        except (ValueError, KeyError, TypeError):
            f["flags"].add("invalid_reward_snapshot")
            f["reward_status"] = "INVALID"

    placed: dict[str, dict] = {}
    modified = set()
    for i in data["intents"]:
        ident = str(i["client_order_id"])
        if i["operation"] == "MODIFY":
            modified.add(ident)
        elif i["operation"] == "PLACE":
            if ident not in placed or stamp(i["created_at"]) < stamp(placed[ident]["created_at"]):
                placed[ident] = i
    observations = {str(o["client_order_id"]): o for o in data["observations"]}
    # Union overlaps; select one latest observation, never add both estimates.
    spans: dict[tuple, list[tuple]] = defaultdict(list)
    for order in data["orders"]:
        ident, slug = str(order["client_order_id"]), str(order["market_slug"])
        intent = placed.get(ident)
        if intent is None or not intent["quote_observation_required"]:
            continue  # reducers are not reward-building exposure
        if order["status"] == "REJECTED":
            diagnostics["rejected_orders_excluded"] += 1
            continue
        left = stamp(intent["created_at"])
        if left >= end:
            continue
        right = end if order["status"] in WORKING else stamp(order["event_at"])
        if right <= start:
            continue
        flags = set()
        if right < left:
            diagnostics["reversed_order_intervals"] += 1
            f = at_cell(left, slug)
            if f: f["flags"].add("invalid_order_interval")
            continue
        if order["status"] not in WORKING | TERMINAL:
            flags.add("unknown_order_state")
        if ident in modified:
            flags.add("modified_order_history")
        if number(order["filled_qty"]) > 0:
            flags.add("filled_order_rest_duration_approximate")
        doc = None
        try:
            obs = observations[ident]
            doc = json.loads(obs["observation_json"])
            if (doc["market_slug"] != slug or obs["market_slug"] != slug or
                    doc["side"] not in {"bid", "offer"} or doc["side"] != obs["side"]):
                raise ValueError("observation identity mismatch")
            stamp(doc["observed_at"])
            values = {"period": doc["reward_period"], "program": doc["program_id"],
                      "market": slug, "quantity": doc["quantity"], "pool": doc["reward_pool"],
                      "discount": doc["discount_factor"], "target": doc["target_size"]}
            sig = signature(values)
            qty = number(doc["quantity"])
            rate = number(doc["projected_reward"]) / D(30)  # validated historical runtime horizon
            if rate < 0 or qty != number(order["original_qty"]):
                raise ValueError("invalid quote economics")
        except (ValueError, KeyError, TypeError):
            flags.add("missing_or_invalid_quote_observation")
            sig, qty, rate = None, ZERO, ZERO
            doc = None
        side = doc["side"] if doc else str(order["side"])
        cursor, stop = max(left, start), min(right, end)
        while cursor < stop:
            next_midnight = boundary(cursor.astimezone(ET).date()+timedelta(days=1))
            segment_end = min(stop, next_midnight)
            key = (cursor.astimezone(ET).date().isoformat(), slug)
            f = cell(*key)
            f["flags"].update(flags)
            if sig:
                f["signatures"].add(sig)
                f["periods"].add(sig[0]); f["programs"].add(sig[1])
                if doc.get("selection_rank") is not None:
                    f["selection_ranks"].add(int(doc["selection_rank"]))
                secs = doc.get("seconds_to_event")
                if secs is not None:
                    hours = number(secs)/3600
                    f["entry_time_bands"].add("0-6h" if hours<=6 else "6-12h" if hours<=12 else "12-24h" if hours<=24 else "outside")
            spans[(key, side)].append((cursor, segment_end, left, qty, rate, ident))
            cursor = segment_end
    for (key, side), segments in spans.items():
        f = cell(*key)
        points = sorted({p for s in segments for p in s[:2]})
        for a, b in zip(points, points[1:]):
            active = [s for s in segments if s[0] < b and s[1] > a]
            if not active: continue
            owner = max(active, key=lambda s: (s[2], s[5]))
            seconds = D(str((b-a).total_seconds()))
            f["side_hours"] += seconds/3600
            f["quantity_hours"] += seconds/3600*owner[3]
            f["placement_model_reward"] += seconds*owner[4]

    # Replay all fills for basis; a manual leg taints the entire inventory
    # episode. Never count a manual close as an independently owned strategy.
    inventories: dict[str, MatchedInventory] = defaultdict(MatchedInventory)
    order_index = {str(o["client_order_id"]): o for o in data["orders"]}
    episode_days: dict[str, set[str]] = defaultdict(set)
    tainted: dict[str, bool] = defaultdict(bool)
    fills = sorted(data["fills"], key=lambda r: (stamp(r["event_at"]), stamp(r["received_at"]), r["execution_id"]))
    for r in fills:
        if r.get("reversed", 0): continue
        at = stamp(r["event_at"])
        if at >= end: break
        slug, ident = str(r["market_slug"]), str(r.get("client_order_id") or "")
        day = at.astimezone(ET).date().isoformat()
        inv = inventories[slug]
        if inv.position == 0:
            episode_days[slug] = set(); tainted[slug] = False
        episode_days[slug].add(day)
        i = placed.get(ident)
        order = order_index.get(ident)
        owned = bool(i and order and i["market_slug"] == slug and
                     order["market_slug"] == slug and not ident.startswith("venue:") and
                     (not r.get("exchange_order_id") or
                      r["exchange_order_id"] == order.get("exchange_order_id")))
        if not owned: tainted[slug] = True
        closing = inv.position * intent_direction(str(r["intent"])) < 0
        fee = number(r["fee"])
        delta = inv.apply(intent=r["intent"], price=r["price"], quantity=r["qty"], fee=fee)
        f = cell(day, slug)
        if f:
            f["closed_fill_pnl"] += delta; f["fill_fees"] += fee; f["fills"] += 1
            f["unowned_fills"] += int(not owned)
            if closing:
                f["closing_fills"] += 1
                f["losing_closes"] += int(delta < 0)
                f["worst_close"] = min(f["worst_close"], delta)
        for d in episode_days[slug]:
            affected = cell(d, slug)
            if affected:
                if tainted[slug]: affected["flags"].add("manual_or_unattributed_episode")
                if len(episode_days[slug]) > 1: affected["flags"].add("cross_day_inventory")
    for slug, inv in inventories.items():
        if inv.position:
            for day in episode_days[slug]:
                f = cell(day, slug)
                if f: f["flags"].add("unmatched_inventory_at_cutoff")

    for f in cells.values():
        if f["side_hours"] <= 0: f["flags"].add("no_same_day_entry_rest")
        if f["reward_status"] == "MISSING": f["flags"].add("reward_not_reported")
        if "PENDING" in f["reward_status"]: f["flags"].add("pending_reward")
        if len(f["signatures"]) != 1: f["flags"].add("ambiguous_or_missing_program_parameters")
        # Filled order durations are approximate, but not automatically bad
        # trades. Keep them visible; all rates are explicitly observational.
        blocking = f["flags"] - {"filled_order_rest_duration_approximate"}
        f["training_eligible"] = not blocking and f["reward_status"] in {"PAID", "SKIPPED", "PAID+SKIPPED"}
        f["paid_contribution"] = None if f["paid"] is None else f["paid"] + f["closed_fill_pnl"]
        f["provisional_contribution"] = None if f["paid"] is None else f["paid"] + f["pending"] + f["closed_fill_pnl"]
        f["reward_per_side_hour"] = None if f["paid"] is None or not f["side_hours"] else (f["paid"]+f["pending"])/f["side_hours"]
    rows = sorted(cells.values(), key=lambda f: (f["day"], f["market"]))
    return {"schema": 1, "enforcement": "none", "as_of": as_of.isoformat(),
            "ledger_capture_started_at": data.get("capture_started_at"),
            "ledger_capture_finished_at": data.get("capture_finished_at"),
            "first_day": first.isoformat(), "last_day": last.isoformat(),
            "basis": "retrospective_current_ledger_not_as_known_backtest",
            "rows": rows, "diagnostics": dict(diagnostics)}


def build_cohorts(report: dict, *, min_markets: int = 20, min_days: int = 3,
                  min_hours: Decimal = D(10)) -> list[dict]:
    """Transparent observational floor, NOT a statistical confidence bound."""
    if min_markets < 1 or min_days < 1 or min_hours <= 0:
        raise ValueError("positive research sample thresholds required")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    population: dict[tuple, list[dict]] = defaultdict(list)
    for row in report["rows"]:
        for sig in row["signatures"]:
            population[sig].append(row)
        if row["training_eligible"]:
            groups[next(iter(row["signatures"]))].append(row)
    result = []
    for sig, rows in sorted(groups.items()):
        hours = sum((r["side_hours"] for r in rows), ZERO)
        paid = sum((r["paid"] for r in rows), ZERO)
        pnl = sum((r["closed_fill_pnl"] for r in rows), ZERO)
        days = sorted({r["day"] for r in rows})
        markets = len({r["market"] for r in rows})
        ratios, losses = [], []
        for day in days:
            group = [r for r in rows if r["day"] == day]
            h = sum((r["side_hours"] for r in group), ZERO)
            predicted = sum((r["placement_model_reward"] for r in group), ZERO)
            if predicted > 0:
                ratios.append(sum((r["paid"] for r in group), ZERO)/predicted)
            losses.append(max(ZERO, -sum((r["closed_fill_pnl"] for r in group), ZERO)/h))
        # Avoid survivor bias: an incomplete loser cannot disappear while its
        # complete winners become a trusted ranking. Default requires every
        # matched cohort row to be usable, including zero-reward SKIPPED rows.
        complete = len(rows) == len(population[sig])
        unmatched = [r for r in report["rows"] if r["league"] == sig[2] and not r["signatures"]]
        ready = (complete and not unmatched and markets >= min_markets and
                 len(days) >= min_days and hours >= min_hours and len(ratios) == len(days))
        result.append({"signature": sig, "market_days": len(rows), "markets": markets,
                       "days": len(days), "side_hours": hours, "paid": paid,
                       "closed_fill_pnl": pnl, "paid_contribution_per_side_hour": (paid+pnl)/hours,
                       "reward_per_side_hour": paid/hours, "cost_per_side_hour": max(ZERO,-pnl/hours),
                       "reward_conversion_floor": min(D(1),min(ratios)) if ready else None,
                       "worst_day_cost_per_side_hour": max(losses) if ready else None,
                       "last_day": max(days), "sample_ready": ready,
                       "population_market_days": len(population[sig]),
                       "unmapped_league_market_days": len(unmatched),
                       "complete_population": complete and not unmatched,
                       "knowledge_as_of": report["as_of"],
                       "reward_observed_through": max(r["reward_observed_at"] for r in rows)})
    return result


def read_scan_log(path: Path, *, through: datetime) -> dict | None:
    """Ignore partial/legacy lines; never combine different scans into a slate."""
    latest = None
    with path.open("rb") as stream:
        stream.seek(0, 2)
        end = stream.tell()
        start = max(0, end-16*1024*1024)
        stream.seek(start)
        if start:
            stream.readline(end-start)  # discard a possibly partial first line
        while stream.tell() < end:
            raw = stream.readline(end-stream.tell())
            if not raw.endswith(b"\n"):
                break  # writer may still be appending the record
            line = raw.decode("utf-8", errors="replace")
            marker = "[market-measurement] "
            if marker not in line: continue
            try:
                doc = json.loads(line.split(marker, 1)[1])
                at = stamp(doc["at"])
                if doc["schema"] != 1 or doc["enforcement"] != "none": continue
                if at <= through and (latest is None or at > stamp(latest["at"])):
                    latest = doc
            except (ValueError, KeyError, TypeError):
                continue
    return latest


def shadow_rank(cohorts: list[dict], scan: dict | None, *, now: datetime) -> dict:
    if scan is None:
        return {"status": "awaiting_complete_scan_measurement", "rows": []}
    try:
        at = stamp(scan["at"])
        candidates = scan["candidates"]
        if (not isinstance(candidates, list) or len(candidates)>100 or
                len({c["market"] for c in candidates})!=len(candidates)):
            raise ValueError("invalid candidate identities")
    except (ValueError, KeyError, TypeError):
        return {"status": "invalid_scan", "rows": []}
    if not timedelta(0) <= now-at <= timedelta(minutes=20):
        return {"status": "stale_or_future_scan", "rows": []}
    if scan.get("schema") != 1 or scan.get("enforcement") != "none":
        return {"status": "invalid_scan", "rows": []}
    models = {tuple(c["signature"]): c for c in cohorts if c["sample_ready"]}
    result = []
    for candidate in candidates:
        row = {"market": candidate["market"], "period": candidate.get("period", "unknown"),
               "current_rank": candidate.get("current_rank"), "shadow_rank": None,
               "score_per_side_hour": None, "status": "insufficient_comparable_history"}
        result.append(row)
        if row["current_rank"] is None:
            row["status"] = "current_strategy_ineligible"; continue
        try:
            model = models.get(signature(candidate))
            if model is None: continue
            # Never learn from outcomes received after the prospective scan.
            if (stamp(model["knowledge_as_of"]) > at or
                    stamp(model["reward_observed_through"]) > at or
                    date.fromisoformat(model["last_day"]) >= at.astimezone(ET).date()):
                row["status"] = "outcomes_not_known_at_scan"; continue
            if (at.astimezone(ET).date()-date.fromisoformat(model["last_day"])).days > 14:
                row["status"] = "stale_history"; continue
            start = stamp(candidate["game_start"])
            if start <= at+timedelta(minutes=5):
                row["status"] = "at_exit_boundary"; continue
            if candidate["program_end"] and stamp(candidate["program_end"]) <= at:
                row["status"] = "program_ended"; continue
            if candidate.get("program_start") and stamp(candidate["program_start"]) > at:
                row["status"] = "program_not_started"; continue
            seconds = number(scan["projection_seconds"])
            if seconds <= 0: raise ValueError("invalid projection horizon")
            sides = [s for s in candidate["sides"] if s["viable"] and s["reward_eligible"]]
            if not sides: raise ValueError("no eligible sides")
            model_end = start
            if period(candidate["period"]) == "daily_event":
                model_end = min(model_end, boundary(at.astimezone(ET).date()+timedelta(days=1)))
            if candidate["program_end"]:
                model_end = min(model_end, stamp(candidate["program_end"]))
            usable_end = min(model_end, start-timedelta(minutes=5))
            if usable_end <= at:
                row["status"] = "no_accessible_reward_time"; continue
            modeled_seconds = min(seconds, D(str((model_end-at).total_seconds())))
            rate = sum((number(s["projected_reward"]) for s in sides),ZERO)/modeled_seconds*3600/len(sides)
            if rate < 0: raise ValueError("negative projection")
            row["score_per_side_hour"] = rate*model["reward_conversion_floor"]-model["worst_day_cost_per_side_hour"]
            row["usable_until"] = usable_end.isoformat()
            row["accessible_hours"] = D(str((usable_end-at).total_seconds()))/3600
            row["accrued_reward"] = None
            row["remaining_reward_scenario"] = rate*model["reward_conversion_floor"]*row["accessible_hours"]*len(sides)
            row["scenario_assumption"] = "unchanged_share_continuous_rest_not_guaranteed"
            row["status"] = "positive_observational" if row["score_per_side_hour"]>0 else "nonpositive_observational"
            row["sample_markets"] = model["markets"]
        except (ValueError, KeyError, TypeError):
            row["status"] = "invalid_candidate"
            row["score_per_side_hour"] = None
    # Rank only supported candidates, separately by the current priority tier.
    # Unknown candidates are NOT assigned zero or silently placed last.
    for tier in ("day_of", "daily_event"):
        supported = sorted((r for r in result if period(r["period"])==tier and r["score_per_side_hour"] is not None), key=lambda r:(-r["score_per_side_hour"], r["current_rank"], r["market"]))
        for rank, row in enumerate(supported,1): row["shadow_rank"] = rank
    return {"status": "comparison_only_supported_subset_per_tier", "scan_at": scan["at"], "rows": result}


def encode(value: Any) -> Any:
    if isinstance(value,D): return str(value)
    if isinstance(value,set): return sorted(value)
    raise TypeError(type(value).__name__)


def frozen_cohorts(document: dict) -> list[dict]:
    """Load a prior report, not a current replay pretending to precede a scan."""
    if document.get("schema") != 1 or document.get("enforcement") != "none":
        raise ValueError("unsupported measurement model")
    at = stamp(document["as_of"])
    result=[]
    for original in document["cohorts"]:
        row=dict(original)
        if type(row["sample_ready"]) is not bool or len(row["signature"])!=7:
            raise ValueError("invalid model fields")
        if stamp(row["knowledge_as_of"]) != at:
            raise ValueError("model knowledge timestamp mismatch")
        row["signature"]=tuple(row["signature"])
        for key in ("reward_conversion_floor","worst_day_cost_per_side_hour"):
            row[key]=None if row[key] is None else number(row[key])
        if row["sample_ready"] and (row["reward_conversion_floor"] is None or
                not 0<=row["reward_conversion_floor"]<=1 or
                row["worst_day_cost_per_side_hour"] is None or
                row["worst_day_cost_per_side_hour"]<0 or not row["complete_population"]):
            raise ValueError("invalid ready model")
        result.append(row)
    if len({r["signature"] for r in result})!=len(result):
        raise ValueError("duplicate model signature")
    return result


def money(value: Decimal | None) -> str:
    return "unknown" if value is None else f"${value:.2f}"


def render(report: dict) -> str:
    rows = report["rows"]
    flags: dict[str,int] = defaultdict(int)
    for row in rows:
        for flag in row["flags"]: flags[flag]+=1
    lines = ["# Market economics — measurement only", "",
             f"ET dates: {report['first_day']} through {report['last_day']}. As of {report['as_of']}.", "",
             "No entry authority. Paid, pending and unknown remain separate. Local resting hours and placement-based predictions are approximations, not venue scoring seconds.", "",
             f"Market-days: {len(rows)}; usable observational rows: {sum(r['training_eligible'] for r in rows)}.",
             f"Reported paid: {money(sum((r['paid'] for r in rows if r['paid'] is not None),ZERO))}; pending: {money(sum((r['pending'] for r in rows if r['pending'] is not None),ZERO))}.",
             "These are reported subtotals; missing rewards are not zero. Closed-fill replay is not complete account P&L.", "",
             "## All observed market groups (including incomplete rows)", "",
             "| Period / group | Market-days | Side-hours | Reported paid | Pending | Recorded closed P&L | Missing/invalid rewards |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        p = next(iter(r["periods"])) if len(r["periods"])==1 else "unallocated"
        groups[(p,r["league"])].append(r)
    for (p,l),group in sorted(groups.items()):
        h=sum((r["side_hours"] for r in group),ZERO)
        paid=sum((r["paid"] for r in group if r["paid"] is not None),ZERO)
        pending=sum((r["pending"] for r in group if r["pending"] is not None),ZERO)
        pnl=sum((r["closed_fill_pnl"] for r in group),ZERO)
        missing=sum(r["paid"] is None for r in group)
        label=f"{p} / {l}".replace("|","/").replace("\n"," ")
        lines.append(f"| {label} | {len(group)} | {h:.2f} | {money(paid)} | {money(pending)} | {money(pnl)} | {missing} |")
    lines.extend(["", "Manual/unmatched costs remain visible here; no bot-only or finalized profit claim. Unallocated includes reward-date mismatches and cannot be assigned to a program by guesswork.", "",
             "## Comparable cohorts", "",
             "| Period / league / program | Markets | Days | Side-hours | Paid reward/hr | Paid contribution/hr | Usable/all rows | Sample ready |",
             "|---|---:|---:|---:|---:|---:|---:|---|"])
    for c in report["cohorts"]:
        sig=c["signature"]
        label=" / ".join((sig[0],sig[2],sig[1])).replace("|","/").replace("\n"," ")
        lines.append(f"| {label} | {c['markets']} | {c['days']} | {c['side_hours']:.2f} | {money(c['reward_per_side_hour'])} | {money(c['paid_contribution_per_side_hour'])} | {c['market_days']}/{c['population_market_days']} | {c['sample_ready']} |")
    lines.extend(["", "Sample readiness is a research floor, not statistical confidence or permission to trade. Exact pool/size/target/discount signatures are retained in JSON. Pending and incomplete rows never train a model.", "", "## Coverage", ""])
    lines.extend(f"- {key}: {count} market-days" for key,count in sorted(flags.items()))
    lines.extend(["", "## Shadow comparison", "", report["shadow"]["status"], "",
                  "Ranks compare only supported candidates within day_of/daily_event tiers. Unknown is not zero. No counterfactual profit is claimed.", "",
                  "| Market | Current rank | Shadow rank (tier subset) | Score/side-hour | Evidence |",
                  "|---|---:|---:|---:|---|"])
    for row in report["shadow"]["rows"]:
        slug=str(row["market"]).replace("|","/").replace("\n"," ")
        lines.append(f"| {slug} | {row['current_rank']} | {row['shadow_rank']} | {money(row['score_per_side_hour'])} | {row['status']} |")
    lines.extend(["",
                  "## Interpretation limits", "",
                  "- Reward dates are not automatically shifted to fit orders; no reward is allocated across ambiguous programs.",
                  "- Account-wide/manual or cross-day inventory is excluded from training, not hidden from the detail.",
                  "- Resting starts at local intent time; partial fills, downtime and qualifying depth can overstate useful time.",
                  "- Placement-based reward rates are held constant only for an explicitly approximate calibration; repeated scans are never summed as income. Time/price-band exposure is recorded, but market-day earnings cannot be exactly allocated to individual entries or bands.",
                  "- Historical parameter matches and sample size do not guarantee future profitability. Full market/day detail is in report.json.",
                  "- Preserve all current entry/exit/cap/risk rules. No minimum-payout gate or automatic source switch."])
    return "\n".join(lines)+"\n"


async def fetch_earnings(limiter_state: Path) -> list[dict]:
    from liquidity_bot.adapters import AsyncReadOnlyGateway, shared_api_limiter, OPPORTUNISTIC_REPORT_RATE_PER_SECOND
    from liquidity_bot.probe import client_from_environment
    client=client_from_environment()
    try:
        gateway=AsyncReadOnlyGateway(client, limiter=shared_api_limiter(
            OPPORTUNISTIC_REPORT_RATE_PER_SECOND,
            state_path=str(limiter_state.resolve())), default_traffic_class="report")
        rows=await gateway.fetch_earnings()
        at=datetime.now(UTC).isoformat()
        return [{"date":r.date, "market_slug":r.market_slug,"program_type":r.program_type,
                 "status":r.status,"reward":str(r.reward),"observed_at":at} for r in rows]
    finally:
        await client.close()


async def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path("data/commissioning.db"))
    parser.add_argument("--days",type=int,default=14)
    parser.add_argument("--end",type=date.fromisoformat)
    source=parser.add_mutually_exclusive_group()
    source.add_argument("--fetch-earnings",action="store_true")
    source.add_argument("--earnings-json",type=Path)
    parser.add_argument("--limiter-state",type=Path,
                        help="required with --fetch-earnings: active owner's shared limiter file")
    parser.add_argument("--scan-log",type=Path)
    parser.add_argument("--model-json",type=Path,
                        help="prior report.json captured before the compared scan")
    parser.add_argument("--output-root",type=Path,default=Path("data/market-economics"))
    parser.add_argument("--min-markets",type=int,default=20)
    parser.add_argument("--min-days",type=int,default=3)
    args=parser.parse_args()
    if not 1<=args.days<=90: parser.error("days must be 1..90")
    if args.fetch_earnings and args.limiter_state is None:
        parser.error("--fetch-earnings requires the active owner's --limiter-state")
    now=datetime.now(UTC)
    last=args.end or (now.astimezone(ET).date()-timedelta(days=1))
    first=last-timedelta(days=args.days-1)
    data=read_ledger(args.db)
    if args.fetch_earnings:
        earnings=await fetch_earnings(args.limiter_state); source_name="fresh_account_wide_api"
    elif args.earnings_json:
        earnings=json.loads(args.earnings_json.read_text(encoding="utf-8"));source_name="supplied_snapshot"
    else:
        earnings=data["cached_earnings"];source_name="latest_per_market_local_cache_not_complete_snapshot"
    now=datetime.now(UTC)
    report=analyze(data,earnings,first=first,last=last,as_of=now)
    report["earnings_source"]=source_name
    report["cohorts"]=build_cohorts(report,min_markets=args.min_markets,min_days=args.min_days)
    scan=read_scan_log(args.scan_log,through=now) if args.scan_log else None
    models=(frozen_cohorts(json.loads(args.model_json.read_text(encoding="utf-8")))
            if args.model_json else report["cohorts"])
    report["shadow_model_cohorts"]=models
    report["shadow"]=shadow_rank(models,scan,now=now)
    report["research_thresholds"]={"markets":args.min_markets,"days":args.min_days,"side_hours":10}
    report["implementation_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    directory=args.output_root/now.strftime("%Y%m%dT%H%M%S.%fZ")
    directory.mkdir(parents=True,exist_ok=False)
    for name,value in (("report.json",report),("earnings.json",earnings),("scan.json",scan)):
        with (directory/name).open("x",encoding="utf-8") as handle:
            json.dump(value,handle,default=encode,allow_nan=False,indent=2)
    (directory/"report.md").write_text(render(report),encoding="utf-8")
    print(f"Measurement saved: {directory}\nRows={len(report['rows'])}; usable={sum(r['training_eligible'] for r in report['rows'])}; shadow={report['shadow']['status']}; enforcement=none")
    return 0


if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
