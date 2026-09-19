#!/usr/bin/env python3
"""Offline Priority3/4 validation. No venue calls, model fitting or trading consumer."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.market_economics import (ET, analyze, encode, number, read_ledger, stamp)


def reward_validation(report: dict, previous: dict | None = None) -> dict:
    """Never turn absent reward into zero or attribute a mixed program."""
    old = {(r['day'], r['market']): r for r in previous['rows']} if previous else {}
    before_times = [stamp(r['reward_observed_at']) for r in old.values() if r['reward_observed_at']]
    after_times = [stamp(r['reward_observed_at']) for r in report['rows'] if r['reward_observed_at']]
    if before_times and after_times and max(before_times) > min(after_times):
        raise ValueError('previous earnings snapshot is newer than current snapshot')
    groups = defaultdict(list)
    unresolved, revisions = [], []
    for row in report['rows']:
        key = (row['day'], row['market'])
        periods = row['periods']
        group = (next(iter(periods)) if len(periods) == 1
                 and len(row['signatures']) == 1 else 'unallocated')
        groups[group].append(row)
        flags = set(row['flags']) - {'filled_order_rest_duration_approximate'}
        if flags:
            unresolved.append({'day': row['day'], 'market': row['market'],
                               'reasons': sorted(flags), 'attribution': group})
        if key in old:
            before = old[key]
            fields = ('paid', 'pending', 'skipped', 'reward_status')
            if any(before[k] != row[k] for k in fields):
                revisions.append({'day': key[0], 'market': key[1],
                                  'before': {k: before[k] for k in fields},
                                  'after': {k: row[k] for k in fields}})
    totals = {}
    for name, rows in groups.items():
        totals[name] = {'market_days': len(rows),
                       'missing': sum(r['reward_status'] == 'MISSING' for r in rows),
                       'training_eligible': sum(r['training_eligible'] for r in rows)}
        for key in ('paid', 'pending', 'skipped', 'closed_fill_pnl', 'side_hours'):
            totals[name][key] = sum((r[key] or Decimal(0) for r in rows), Decimal(0))
    return {'groups': totals, 'unresolved': unresolved, 'revisions': revisions,
            'comparison': 'same_ledger_different_reward_snapshots' if previous else 'not_supplied',
            'note': 'Reported subtotals only; missing is unknown, pending is not paid net.'}


def fill_validation(data: dict, *, as_of: datetime, days: int = 7) -> dict:
    """30-second first-fill labels; early cancellation is censored, not a loss.

    The deployed fixed model is scored without refitting. Last completed UTC
    day is a temporal holdout; older completed days are the reference sample.
    Retrospective revised ledger, not as-known historical replay. Local intent
    time approximates placement; publication latency is not reconstructed.
    """
    cutoff = as_of.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = cutoff - timedelta(days=days)
    holdout = cutoff - timedelta(days=1)
    orders = {r['client_order_id']: r for r in data['orders']}
    placed = {r['client_order_id']: r for r in data['intents'] if r['operation'] == 'PLACE'}
    modified = {r['client_order_id'] for r in data['intents'] if r['operation'] == 'MODIFY'}
    fills = defaultdict(list)
    for f in data['fills']:
        if not f.get('reversed', 0) and f.get('client_order_id'):
            fills[f['client_order_id']].append(f)
    exclusions = Counter()
    groups = defaultdict(list)
    strata = defaultdict(list)
    provenance = Counter()
    seen = set()
    for q in data['observations']:
        at = stamp(q['observed_at'])
        if not start <= at < cutoff:
            continue
        client = q['client_order_id']
        if client in seen:
            raise ValueError('duplicate quote observation identity')
        seen.add(client)
        doc = json.loads(q['observation_json'])
        horizon = doc.get('fill_model_horizon_seconds')
        if horizon is not None and number(horizon) != 30:
            exclusions['different_model_horizon'] += 1
            continue
        rank = doc.get('selection_provenance') or {}
        provenance[rank.get('current', {}).get('reason', 'legacy_without_provenance')] += 1
        o, intent = orders.get(client), placed.get(client)
        if not o or not intent or client in modified:
            exclusions['unproved_or_modified_order'] += 1
            continue
        if o['market_slug'] != q['market_slug'] or intent['market_slug'] != q['market_slug']:
            exclusions['identity_mismatch'] += 1
            continue
        status = o['status'].upper()
        if status in {'REJECTED', 'UNKNOWN_ABSENT'}:
            exclusions['rejected_or_unknown'] += 1
            continue
        try:
            probability = float(number(doc['fill_probability']))
            if not 0 <= probability <= 1:
                raise ValueError('probability out of range')
        except (KeyError, ValueError, TypeError):
            exclusions['invalid_probability'] += 1
            continue
        exact = fills[client]
        if any(f['market_slug'] != q['market_slug'] or (
                f.get('exchange_order_id') and o.get('exchange_order_id')
                and f['exchange_order_id'] != o['exchange_order_id']) for f in exact):
            exclusions['fill_identity_mismatch'] += 1
            continue
        times = [stamp(f['event_at']) for f in exact if stamp(f['event_at']) <= as_of]
        if times and min(times) < at:
            exclusions['fill_before_observation'] += 1
            continue
        first = min(times) if times else None
        end = at + timedelta(seconds=30)
        if end > cutoff:
            exclusions['incomplete_horizon'] += 1
            continue
        event = stamp(o['event_at'])
        if event > as_of or event < at:
            exclusions['invalid_order_clock'] += 1
            continue
        if (first is not None and event < first
                and status in {'FILLED', 'CANCELED', 'CANCELLED', 'EXPIRED'}):
            exclusions['terminal_before_fill'] += 1
            continue
        if not first and (status == 'FILLED' or number(o.get('filled_qty', 0)) > 0):
            exclusions['missing_fill_execution'] += 1
            continue
        if first is not None and first <= end:
            outcome = 1
        elif event >= end and status in {'OPEN', 'NEW', 'PARTIAL', 'FILLED', 'CANCELED', 'CANCELLED', 'EXPIRED'}:
            outcome = 0
        else:
            exclusions['censored_before_horizon'] += 1
            continue
        split = 'holdout' if at >= holdout else 'reference'
        groups[split].append((probability, outcome, at.date().isoformat(), q['market_slug']))
        stratum = (split, doc.get('reward_period', 'unknown'),
                   str(doc.get('quantity', 'unknown')),
                   doc.get('fill_model_calibration_id') or 'legacy_unrecorded')
        strata[stratum].append((probability, outcome))
    result = {}
    for split in ('reference', 'holdout'):
        vals = groups[split]
        n = len(vals)
        result[split] = {
            'observations': n, 'markets': len({r[3] for r in vals}),
            'days': sorted({r[2] for r in vals}),
            'fills_within_30s': sum(r[1] for r in vals),
            'mean_prediction': sum(r[0] for r in vals)/n if n else None,
            'observed_rate': sum(r[1] for r in vals)/n if n else None,
            'brier_score': sum((r[0]-r[1])**2 for r in vals)/n if n else None,
        }
    return {'horizon_seconds': 30, 'start_utc': start.isoformat(),
            'end_utc': cutoff.isoformat(), 'holdout_start_utc': holdout.isoformat(),
            'model_source': 'recorded_placement_probability_no_refit',
            'splits': result, 'exclusions': dict(exclusions),
            'strata': [{'split': k[0], 'period': k[1], 'quantity': k[2],
                        'calibration_id': k[3], 'observations': len(v),
                        'predicted_rate': sum(x[0] for x in v)/len(v),
                        'observed_rate': sum(x[1] for x in v)/len(v)}
                       for k, v in sorted(strata.items())],
            'rank_provenance': dict(provenance),
            'verdict': 'diagnostic_only_not_strategy_approval',
            'limitations': ['cancellation censoring and selected-entry bias',
                           'historical model is a conservative upper-risk prior, not a point forecast',
                           'local placement time; retrospective revised ledger',
                           'legacy missing horizon assumes historical 30-second setting',
                           'correlated repeated quotes, not independent trials']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('data/commissioning.db'))
    parser.add_argument('--earnings-json', type=Path, required=True)
    parser.add_argument('--previous-earnings-json', type=Path)
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--output-root', type=Path, default=Path('data/measurement-validation'))
    args = parser.parse_args()
    if not 3 <= args.days <= 90:
        parser.error('days must be 3..90')
    data = read_ledger(args.db)
    now = datetime.now(timezone.utc)
    last = now.astimezone(ET).date() - timedelta(days=1)
    first = last - timedelta(days=args.days-1)
    def reward_report(path):
        earnings = json.loads(path.read_text(encoding='utf8'))
        return analyze(data, earnings, first=first, last=last, as_of=now)
    current = reward_report(args.earnings_json)
    previous = reward_report(args.previous_earnings_json) if args.previous_earnings_json else None
    result = {'schema': 1, 'enforcement': 'none', 'as_of': now.isoformat(),
              'ledger_capture': data['capture_finished_at'],
              'reward_observed_at': sorted({r['reward_observed_at'] for r in current['rows'] if r['reward_observed_at']}),
              'reward_dates_et': [first.isoformat(), last.isoformat()],
              'rewards': reward_validation(current, previous),
              'fills': fill_validation(data, as_of=now, days=args.days)}
    result['input_sha256'] = {'earnings': hashlib.sha256(args.earnings_json.read_bytes()).hexdigest(),
                             'implementation': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    directory = args.output_root / now.strftime('%Y%m%dT%H%M%S.%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    with (directory/'report.json').open('x', encoding='utf8') as handle:
        json.dump(result, handle, default=encode, allow_nan=False, indent=2)
    lines = ['# Measurement validation — no trading authority', '',
             f"Captured: {now.isoformat()}; rewards ET: {first} through {last}.",
             f"Reward observations: {', '.join(result['reward_observed_at']) or 'UNAVAILABLE'}.", '',
             '## Priority 3: rewards', '',
             '| Attribution | Rows | Paid | Pending | Missing | Usable |',
             '|---|---:|---:|---:|---:|---:|']
    for group, v in result['rewards']['groups'].items():
        lines.append(f"| {group} | {v['market_days']} | ${v['paid']:.2f} | ${v['pending']:.2f} | {v['missing']} | {v['training_eligible']} |")
    lines += ['', f"Changed market-days: {len(result['rewards']['revisions'])}; unresolved: {len(result['rewards']['unresolved'])}.",
              'Missing is unknown. Pending is not paid profit. Revisions/details remain in report.json.', '',
              '## Priority 4: 30-second fill check', '',
              '| Sample | Observations | Predicted | Observed | Brier score |',
              '|---|---:|---:|---:|---:|']
    for name, v in result['fills']['splits'].items():
        values = ('Unavailable',)*3 if not v['observations'] else (
            f"{v['mean_prediction']:.2%}", f"{v['observed_rate']:.2%}", f"{v['brier_score']:.6f}")
        lines.append(f"| {name} | {v['observations']} | {' | '.join(values)} |")
    lines += ['', 'UTC completed-day split; early cancellations are censored. No model was refitted.',
              'The prior is deliberately conservative; mixed programs, selected entries and correlated quotes limit interpretation.',
              'Original-rank provenance requires the prepared runtime change; legacy rows remain unknown.']
    (directory/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf8')
    print(f"Saved {directory}/report.json; enforcement=none; no model changed")
    print(json.dumps(result['fills'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
