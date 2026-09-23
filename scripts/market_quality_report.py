#!/usr/bin/env python3
"""Offline grade/accounting report. No credentials, API client or trading consumer."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from scripts.market_economics import analyze, read_ledger, stamp, number, encode, ET, boundary
from liquidity_bot.market_quality_measurement import priority_grade
from liquidity_bot.market_quality_store import read_archive

ZERO = Decimal(0)
FEATURES = ("spread", "quote_price", "bid_depth", "offer_depth", "observed_queue_ahead",
            "ticks_from_best", "snapshot_share", "total_score", "order_score",
            "retained_value_ratio", "stressed_exit_pnl", "fill_probability",
            "seconds_to_event", "maximum_remaining_reward", "projected_reward")


def exact_sum(values):
    """Preserve already-computed decimals independent of grouping/order."""
    values = list(values)
    if not values:
        return ZERO
    with localcontext() as ctx:
        ctx.prec = max(28, max(v.adjusted() for v in values) -
                       min(v.as_tuple().exponent for v in values) + len(str(len(values))) + 2)
        return sum(values, ZERO)


def latest_rewards(records, as_of):
    snapshots = [r for r in records if r["kind"] == "rewards" and
                 stamp(r["payload"]["at"]) <= as_of and
                 (not r.get('received_at') or stamp(r['received_at']) <= as_of)]
    if not snapshots:
        return [], None
    snapshots.sort(key=lambda r: (stamp(r["payload"]["at"]), r["sha256"]))
    last = snapshots[-1]
    ties = [r for r in snapshots if stamp(r["payload"]["at"]) == stamp(last["payload"]["at"])]
    if len({r["sha256"] for r in ties}) > 1:
        raise ValueError("conflicting reward snapshots at same time")
    return last["payload"]["rows"], {"at": last["payload"]["at"], "sha256": last["sha256"],
                                     "run_id": last["run_id"]}


def discovery_payloads(records):
    """Never treat a truncated catalog as a complete small universe."""
    complete = [r['payload'] for r in records if r['kind'] == 'discovery']
    parts = defaultdict(list)
    for r in records:
        if r['kind'] == 'discovery_part':
            parts[(r.get('run_id'), r['payload'].get('scan_id'))].append(r['payload'])
    incomplete = 0
    for values in parts.values():
        try:
            values.sort(key=lambda p: p['index'])
            n = values[0]['parts']
            if (type(n) is not int or not 1 <= n <= 20 or len(values) != n or
                    [p['index'] for p in values] != list(range(n)) or
                    len({p['at'] for p in values}) != 1 or
                    any(p['parts'] != n for p in values)):
                raise ValueError('incomplete discovery')
            header = values[0]['header']
            rows = [r for p in values for r in p['candidates']]
            if header['counts']['catalog_markets'] != len(rows):
                raise ValueError('catalog count mismatch')
            complete.append({**header, 'candidates': rows})
        except (KeyError, ValueError, TypeError):
            incomplete += 1
    return complete, incomplete


def grade_metadata(data, first, last):
    """Join via exact client id; never assign a market's later rank to an order."""
    observations = {o["client_order_id"]: o for o in data["observations"]}
    placed = {}
    for i in data["intents"]:
        if i["operation"] == "PLACE" and i["quote_observation_required"]:
            key = i["client_order_id"]
            if key not in placed or stamp(i["created_at"]) < stamp(placed[key]["created_at"]):
                placed[key] = i
    metadata = defaultdict(lambda: {"grades": set(), "ranks": set(), "orders": set(),
                                    "features": defaultdict(list), "bindings": set(), "events": set()})
    begin, end = boundary(first), boundary(last + timedelta(days=1))
    for order in data["orders"]:
        ident = order["client_order_id"]
        i = placed.get(ident)
        if i is None or order["status"] == "REJECTED":
            continue
        left = max(begin, stamp(i["created_at"]))
        right = min(end, stamp(order["event_at"]) if order["status"] in
                    {"CANCELED", "CANCELLED", "EXPIRED", "FILLED"} else end)
        try:
            raw = observations[ident]
            doc = json.loads(raw["observation_json"])
            if raw["market_slug"] != order["market_slug"] or doc["market_slug"] != order["market_slug"]:
                raise ValueError("identity mismatch")
            original = (doc.get("selection_provenance") or {}).get("original") or {}
            rank = original.get("rank")
            # Missing or contradictory provenance cannot become a known grade.
            if (not original.get("snapshot_id") or not original.get("bound_at") or
                    stamp(original["bound_at"]) > stamp(raw["observed_at"]) or
                    not original.get("observed_at") or
                    stamp(original["observed_at"]) > stamp(original["bound_at"])):
                rank = None
        except (ValueError, KeyError, TypeError, AttributeError):
            doc, original, rank = {}, {}, None
        grade = priority_grade(rank)
        while left < right:
            day = left.astimezone(ET).date()
            m = metadata[(day.isoformat(), order["market_slug"])]
            m["grades"].add(grade)
            if grade != "Unknown":
                m["ranks"].add(rank)
                m["bindings"].add((original["snapshot_id"], original["bound_at"]))
            m["orders"].add(ident)
            for f in FEATURES:
                if doc.get(f) is not None:
                    try:
                        m["features"][f].append(number(doc[f]))
                    except ValueError:
                        pass
            left = min(right, boundary(day + timedelta(days=1)))
    return metadata


def rest_bounds(data, records, first, last):
    """Local ACK/terminal bounds, NOT venue-qualified reward seconds.

    Filled/modified/absent orders receive a zero lower bound. No event history
    is fabricated for legacy orders. Same-side overlaps are unioned.
    """
    witnesses = defaultdict(list)
    absent = set()
    for record in records:
        p = record["payload"]
        if record["kind"] == "order_witness":
            witnesses[p["order"]["client_order_id"]].append(p)
        elif record["kind"] == "order_absent":
            absent.add(p["client_order_id"])
    observations = {r["client_order_id"]: r for r in data["observations"]}
    intent_starts = {}
    for intent in data["intents"]:
        if intent["operation"] == "PLACE":
            ident = intent["client_order_id"]
            at = stamp(intent["created_at"])
            intent_starts[ident] = min(intent_starts.get(ident, at), at)
    modified = {r["client_order_id"] for r in data["intents"] if r["operation"] == "MODIFY"}
    intervals = defaultdict(lambda: {"lower": [], "upper": []})
    coverage = defaultdict(lambda: {"orders": 0, "witnessed": 0})
    for order in data["orders"]:
        ident = order["client_order_id"]
        if ident not in observations or order["status"] == "REJECTED":
            continue
        raw = observations[ident]
        side = raw["side"]
        values = witnesses[ident]
        good = [p for p in values if p["order"]["market_slug"] == order["market_slug"] and
                p["order"].get("exchange_order_id") == order.get("exchange_order_id")]
        starts = [stamp(p["at"]) for p in good if p["order"]["status"].upper() in {"OPEN", "NEW", "PARTIAL"}]
        stops = [p for p in good if p["order"]["status"].upper() in {"CANCELED", "CANCELLED", "EXPIRED", "FILLED"}]
        start = intent_starts.get(ident, stamp(raw["observed_at"]))
        terminal = max(stops, key=lambda p: stamp(p["at"])) if stops else None
        upper_end = stamp(terminal["at"]) if terminal else boundary(last + timedelta(days=1))
        lower_start = min(starts) if starts else None
        lower_end = stamp(terminal["order"]["event_at"]) if terminal else None
        lower_known = (lower_start is not None and lower_end is not None and
                       ident not in absent and ident not in modified and number(order["filled_qty"]) == 0 and
                       lower_end >= lower_start and upper_end >= lower_end)
        cursor = max(start, boundary(first))
        stop = min(upper_end, boundary(last + timedelta(days=1)))
        while cursor < stop:
            day = cursor.astimezone(ET).date()
            right = min(stop, boundary(day + timedelta(days=1)))
            key = (day.isoformat(), order["market_slug"])
            coverage[key]["orders"] += 1
            coverage[key]["witnessed"] += int(bool(starts and terminal))
            intervals[(key, side)]["upper"].append((cursor, right))
            if lower_known:
                a, b = max(cursor, lower_start), min(right, lower_end)
                if a < b:
                    intervals[(key, side)]["lower"].append((a, b))
            cursor = right
    totals = defaultdict(lambda: {"lower": ZERO, "upper": ZERO})
    for (key, side), variants in intervals.items():
        for name, spans in variants.items():
            stop = None
            for a, b in sorted(spans):
                if stop is not None:
                    a = max(a, stop)
                if a < b:
                    totals[key][name] += Decimal(str((b-a).total_seconds())) / 3600
                stop = max(stop, b) if stop is not None else b
    return totals, coverage


def build_report(data, earnings, records, health, *, first, last, as_of):
    if first > last or last >= as_of.astimezone(ET).date():
        raise ValueError("only completed ET days may be analyzed")
    unique = {}
    for record in records:
        if record.get('received_at') and stamp(record['received_at']) > as_of:
            continue
        if record['payload'].get('at') and stamp(record['payload']['at']) > as_of:
            continue
        identity = (record.get('run_id'), record.get('seq'))
        if identity != (None, None):
            if identity in unique and unique[identity] != record:
                raise ValueError("conflicting archive identity")
            unique[identity] = record
        else:
            unique[(None, len(unique))] = record
    records = list(unique.values())
    base = analyze(data, earnings, first=first, last=last, as_of=as_of)
    metadata = grade_metadata(data, first, last)
    bounds, witness_coverage = rest_bounds(data, records, first, last)
    capture_complete = bool(health) and all(
        h.get('closed') and h.get('sequence_complete') and
        not any(h.get(k, 0) for k in ('dropped', 'errors', 'disabled')) for h in health)
    if not capture_complete:
        # Lost records can hide modifications/absence. Keep conservative bounds.
        for value in bounds.values():
            value['lower'] = ZERO
    buckets = defaultdict(list)
    for row in base["rows"]:
        key = (row["day"], row["market"])
        m = metadata.get(key)
        grades = m["grades"] if m else set()
        grade = ("Unknown" if not grades or "Unknown" in grades else
                 next(iter(grades)) if len(grades) == 1 else "Mixed")
        row["priority_grade"] = grade
        row["performance"] = "unproven"
        row["original_ranks"] = sorted(m["ranks"]) if m else []
        row["feature_ranges"] = {k: {"min": min(v), "max": max(v), "n_orders": len(v)}
                                 for k, v in m["features"].items()} if m else {}
        row["local_rest_bounds_hours"] = bounds.get(key)
        row["rest_witness_coverage"] = witness_coverage.get(key)
        row["grade_attribution_eligible"] = row["training_eligible"] and grade not in {"Mixed", "Unknown"}
        row["paid_net_observed"] = (row["paid"] + row["closed_fill_pnl"]
                                     if row["paid"] is not None else None)
        row["finalized_net"] = row["paid_net_observed"] if row["grade_attribution_eligible"] else None
        # Never expose the legacy pending-inclusive metric under a paid label.
        row.pop("reward_per_side_hour", None)
        tier = next(iter(row["periods"])) if len(row["periods"]) == 1 else "Mixed"
        size = next(iter(row["signatures"]))[-1] if len(row["signatures"]) == 1 else "Mixed"
        buckets[(tier, str(size), grade)].append(row)
    groups = []
    for (tier, size, grade), rows in sorted(buckets.items()):
        usable = [r for r in rows if r["grade_attribution_eligible"]]
        hours = exact_sum(r["side_hours"] for r in usable)
        paid = exact_sum(r["paid"] for r in usable)
        pnl = exact_sum(r["closed_fill_pnl"] for r in usable)
        groups.append(dict(period=tier, size=size, grade=grade, performance="unproven",
                           market_days=len(rows), usable_market_days=len(usable),
                           distinct_markets=len({r["market"] for r in usable}),
                           completed_days=len({r["day"] for r in usable}),
                           coverage=Decimal(len(usable))/len(rows),
                           usable_approx_side_hours=hours, usable_paid=paid,
                           usable_trading_net=pnl, usable_paid_net=paid+pnl,
                           recorded_losing_closes=sum(r['losing_closes'] for r in rows),
                           recorded_closes=sum(r['closing_fills'] for r in rows),
                           worst_recorded_close=min((r['worst_close'] for r in rows), default=ZERO),
                           paid_per_approx_side_hour=paid/hours if hours else None,
                           net_per_approx_side_hour=(paid+pnl)/hours if hours else None,
                           sample_floor_met=len({r["market"] for r in usable}) >= 20 and
                           len({r["day"] for r in usable}) >= 3 and hours >= 10,
                           comparable=False, reason="observational_subset_requires_matched_later_period_validation"))
    matched = defaultdict(list)
    for row in base['rows']:
        if len(row['signatures']) == 1:
            matched[next(iter(row['signatures']))].append(row)
    cohort_comparisons = []
    for signature, rows in sorted(matched.items()):
        by_grade = defaultdict(list)
        for row in rows:
            by_grade[row['priority_grade']].append(row)
        cohort_comparisons.append(dict(signature=signature,
            complete_attribution=all(r['grade_attribution_eligible'] for r in rows),
            performance='unproven', automatic_promotion=False,
            grades=[dict(grade=grade, market_days=len(rs),
                         usable_market_days=sum(r['grade_attribution_eligible'] for r in rs),
                         paid=exact_sum(r['paid'] for r in rs if r['paid'] is not None),
                         trading_net=exact_sum(r['closed_fill_pnl'] for r in rs),
                         approximate_side_hours=exact_sum(r['side_hours'] for r in rs),
                         missing_rewards=sum(r['paid'] is None for r in rs),
                         pending_rewards=sum('PENDING' in r['reward_status'] for r in rs))
                    for grade, rs in sorted(by_grade.items())]))
    sums = lambda rows, field: exact_sum(r[field] for r in rows if r[field] is not None)
    source = {f: sums(base["rows"], f) for f in ("paid", "pending", "closed_fill_pnl", "side_hours")}
    grouped = {f: exact_sum(sums(rows, f) for rows in buckets.values()) for f in source}
    if source != grouped:
        raise ValueError("grade totals failed reconciliation")
    counts = defaultdict(int)
    for record in records:
        counts[record["kind"]] += 1
    begin, end = boundary(first), boundary(last + timedelta(days=1))
    in_period = lambda r: bool(r['payload'].get('at')) and begin <= stamp(r['payload']['at']) < end
    discoveries, incomplete_scans = discovery_payloads([r for r in records if in_period(r)])
    discoveries.sort(key=lambda p: stamp(p['at']))
    flow = defaultdict(int)
    for discovery in discoveries:
        for candidate in discovery['candidates']:
            flow[candidate['stage']] += 1
    admissions = [r['payload'] for r in records if r['kind'] == 'entry_admission' and in_period(r)]
    return dict(schema=1, enforcement="none", first_day=first.isoformat(), last_day=last.isoformat(),
                as_of=as_of.isoformat(), basis="retrospective_ledger_not_as_known_backtest",
                groups=groups, matched_cohorts=cohort_comparisons, rows=base["rows"], reconciliation=dict(passed=True, source=source, grouped=grouped,
                    scope="local_grade_partition_not_account_certification",
                    complete_reward_coverage=all(r['paid'] is not None for r in base['rows']),
                    missing_reward_market_days=sum(r['paid'] is None for r in base['rows'])),
                archive_health=health, archive_capture_complete=capture_complete, record_counts=dict(counts),
                ledger_capture_started_at=data.get('capture_started_at'),
                ledger_capture_finished_at=data.get('capture_finished_at'),
                selection_flow=dict(scans=len(discoveries), candidate_observations_by_stage=dict(flow),
                                    incomplete_scans=incomplete_scans,
                                    admitted_attempts=sum(p['allowed'] is True for p in admissions),
                                    refused_attempts=sum(p['allowed'] is False for p in admissions),
                                    counts_are_repeated_observations_not_distinct_markets=True),
                latest_observed_pools=discoveries[-1].get("pools", []) if discoveries else [],
                latest_pool_observed_at=discoveries[-1]['at'] if discoveries else None,
                limits=["No causal savings or untraded-market payouts", "Mixed/unknown/pending cohorts remain visible",
                        "Ratios are observed subsets with approximate local resting time, not venue scoring time",
                        "Priority grade is not profitability grade; no automatic filter promotion"])


def render(report):
    lines = ["# Market quality — measurement only", "",
             f"ET {report['first_day']} through {report['last_day']}; as of {report['as_of']}", "",
             "Priority is not profitability. Performance remains unproven; rates use approximate local resting hours.",
             "This overview mixes programs. Exact program/pool/size/league comparisons and missing coverage are in matched_cohorts in JSON.", "",
             "| Period | Size | Grade | Usable/all market-days | Paid | Trading net | Paid net | Paid/h* | Net/h* |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    fmt = lambda x: "unknown" if x is None else f"{x:.2f}"
    for g in report["groups"]:
        lines.append(f"| {g['period']} | {g['size']} | {g['grade']} | {g['usable_market_days']}/{g['market_days']} | "
                     + " | ".join(fmt(g[k] if g['usable_market_days'] else None) for k in ("usable_paid", "usable_trading_net", "usable_paid_net", "paid_per_approx_side_hour", "net_per_approx_side_hour")) + " |")
    lines += ["", "* Usable subset only; not an unbiased comparison. Zero usable rows means unavailable, not zero earnings.",
              "Full JSON retains every paid/pending/mixed/unknown row, feature ranges, local time bounds and archive gaps.",
              "Reconciliation: source totals equal all grade + Mixed/Unknown totals. Fees counted once."]
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--archive", type=Path, action="append", default=[])
    p.add_argument("--earnings-json", type=Path)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--output-root", type=Path, default=Path("data/market-quality-reports"))
    a = p.parse_args()
    if not 1 <= a.days <= 90:
        p.error("days must be 1..90")
    now = datetime.now(timezone.utc)
    last = now.astimezone(ET).date() - timedelta(days=1)
    records, health = [], []
    for path in a.archive:
        rs, hs = read_archive(path)
        records += rs
        health.append(hs)
    records.sort(key=lambda r: (stamp(r["received_at"]), r["run_id"], r["seq"]))
    earnings, source = latest_rewards(records, now)
    if a.earnings_json:
        raw = a.earnings_json.read_bytes()
        earnings = json.loads(raw)
        source = {"supplied_snapshot_sha256": hashlib.sha256(raw).hexdigest()}
    report = build_report(read_ledger(a.db), earnings, records, health,
                          first=last-timedelta(days=a.days-1), last=last, as_of=now)
    report["reward_source"] = source
    report["implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    out = a.output_root / now.strftime("%Y%m%dT%H%M%S.%fZ")
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    (out / "report.json").write_text(json.dumps(report, default=encode, indent=2, allow_nan=False), encoding="utf8")
    (out / "report.md").write_text(render(report), encoding="utf8")
    print(f"Report: {out}; enforcement=none; API requests=0")


if __name__ == "__main__":
    main()
