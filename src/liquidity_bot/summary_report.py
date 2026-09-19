"""Notification-worker-only summary accounting and presentation. No API access.

Historical resolution gaps are NOT repaired or concealed by this report.
Closed-fill totals are partial ledger results, never complete account profit.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

from .pnl import MatchedInventory
from .reward_calendar import NEW_YORK
from .bot_era import BOT_ERA_START_DATE


def stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must be aware')
    return result


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite report value')
    return result


def money(value, *, signed=False):
    if value is None:
        return '--'
    return f'${number(value):+,.2f}' if signed else f'${number(value):,.2f}'


def settled_totals(database, through):
    """One read-only replay; gross matches plus actual fill fees once.

    Open marks, deposits, withdrawals and rewards are not read. Entry fees
    are actual incurred costs, not hypothetical open-position losses. Filled
    partial closures count only their matched portion. No settlement is guessed.
    """
    through = stamp(through)
    today = through.astimezone(NEW_YORK).date()
    conn = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True,
                           timeout=1)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        rows = conn.execute(
            'SELECT market_slug,intent,price,qty,fee,event_at,received_at '
            'FROM execution_fills WHERE reversed=0 '
            'ORDER BY event_at,received_at,execution_id').fetchall()
    finally:
        conn.close()
    inventory = {}
    totals = {key: dict(gains=Decimal(0), losses=Decimal(0), fees=Decimal(0))
              for key in ('today', 'lifetime')}
    for row in rows:
        event, receipt = stamp(row['event_at']), stamp(row['received_at'])
        if event > through or receipt > through:
            continue
        gross = inventory.setdefault(row['market_slug'], MatchedInventory()).apply(
            intent=row['intent'], price=row['price'], quantity=row['qty'], fee=0)
        fee = number(row['fee'] or 0)
        for key in ('lifetime', 'today'):
            if key == 'today' and event.astimezone(NEW_YORK).date() != today:
                continue
            totals[key]['gains'] += max(Decimal(0), gross)
            totals[key]['losses'] += min(Decimal(0), gross)
            totals[key]['fees'] += fee
    for item in totals.values():
        item['net'] = item['gains'] + item['losses'] - item['fees']
    return totals


def prepare_summary(payload):
    """Build image model and text fallback from the SAME settled-only values."""
    clean = copy.deepcopy(payload)
    s = clean.pop('_summary_report')
    embed = clean['embeds'][0]
    now = stamp(s['observed_at'])
    try:
        totals = settled_totals(s['report_database'], now)
        coverage = 'Partial ledger · historical settlements not fully reconciled'
    except Exception:
        totals = {key: dict(gains=None, losses=None, fees=None, net=None)
                  for key in ('today', 'lifetime')}
        coverage = 'Trading history unavailable'
    t, life = totals['today'], totals['lifetime']
    reward_at = s.get('rewards_observed_at')
    reward_age = (now - stamp(reward_at)).total_seconds() if reward_at else None
    # Freshness labeling uses existing cached evidence, not another API call.
    valid_rewards = reward_age is not None and reward_age >= 0
    paid = number(s['attributable_paid_rewards_usd']) if valid_rewards else None
    pending = number(s['pending_rewards_usd']) if valid_rewards else None
    net = life['net'] + paid if life['net'] is not None and paid is not None else None
    net_label = 'Overall net' if net is None else ('Net profit' if net >= 0 else 'Net loss')
    # Preserve the original operational warning/state, never marked P&L.
    description = embed['description'].split('**TRADING TODAY')[0].rstrip()
    description += '\n**SETTLED P&L TODAY  ' + money(t['net'], signed=True) + '**'
    embed['description'] = description
    if embed['color'] in (0x2ECC71, 0xE74C3C) and not s['integrity_problems']:
        # Do not recolor an explicit operational failure green.
        if s['readiness_status'] != 'DO_NOT_START_TRIAL':
            embed['color'] = 0xE74C3C if t['net'] is not None and t['net'] < 0 else 0x2ECC71
    fields = embed['fields']
    reward_field = next(f for f in fields if f['name'] == 'Rewards · lifetime')
    lifetime_rows = [
        ('Trading gains', money(life['gains'], signed=True)),
        ('Trading losses', money(life['losses'], signed=True)),
        ('Fees · net rebates', money(life['fees'])),
        ('Trading net', money(life['net'], signed=True)),
        ('Paid · bot-era', money(paid)),
        (net_label + ' · partial', money(net, signed=True)),
        ('Pending', money(pending)),
    ]
    reward_field['value'] = '```text\n' + '\n'.join(f'{k}: {v}' for k,v in lifetime_rows) + '\n```'
    reward_field['value'] += '\nPending excluded from net. ' + coverage + '.'
    reward_note = ('Rewards unavailable' if not valid_rewards else
                   f'Rewards age {reward_age / 60:.0f}m' + (' · stale' if reward_age > 1800 else ''))
    reward_field['value'] += '\n' + reward_note + ' · paid is date-scoped, not exact attribution.'
    embed['footer']['text'] = f'Settled fills · ET · {coverage} · {reward_note}'
    rows = lambda values: [(a, str(b)) for a,b in values]
    loss = (max(Decimal(0), -number(s['hard_stop_pnl_usd']))
            if s.get('hard_stop_pnl_usd') is not None else None)
    hard = number(s['hard_stop_loss_usd'])
    remaining = max(Decimal(0), hard-loss) if loss is not None else None
    count = s.get('pending_entry_count')
    cap = s.get('pending_entry_cap')
    age = s['max_inventory_age_seconds']
    limit = s.get('inventory_limit_seconds')
    source = s.get('account_source') or 'unknown'
    status = s.get('hard_stop_state') or 'See status'
    status = {'Window budget available':'Ready',
              'Awaiting fresh window proof':'Awaiting proof',
              'Awaiting position closure':'Awaiting close'}.get(status, status)
    risk_rows = rows([
        ('Risk state', status), ('Window ET', (s.get('hard_stop_window') or '--').replace(' · confirmed','')),
        ('Loss / limit', money(loss)+' / '+money(hard)),
        ('Budget left', money(remaining)), ('Entries / cap', f'{count if count is not None else "--"} / {cap if cap is not None else "--"}'),
        ('Positions / orders', f'{s["open_positions"]} / {s["working_orders"]}'),
        ('Oldest / limit', f'{age/60:.1f}m / {limit/60:.0f}m' if limit else f'{age/60:.1f}m'),
    ])
    midnight = now.astimezone(NEW_YORK).replace(hour=0,minute=0,second=0,microsecond=0)
    elapsed = (now.astimezone(timezone.utc) - midnight.astimezone(timezone.utc)).total_seconds()/3600
    hours = s['resting_side_hours_today']
    today_rows = rows([
        ('Settled P&L', money(t['net'], signed=True)), ('Trading gains', money(t['gains'], signed=True)),
        ('Trading losses', money(t['losses'], signed=True)), ('Fees · net rebates', money(t['fees'])),
        ('Rest hours', f'{hours:.1f}'), ('Avg sides', f'{hours/elapsed:.1f}' if elapsed > 0 else '--'),
        ('Uptime', f'{max(0,(now-stamp(s["session_started_at"])).total_seconds())/3600:.1f}h'),
    ])
    try:
        from .report_liquidity import resting_period_hours_by_day
        day = now.astimezone(NEW_YORK).date()
        split = resting_period_hours_by_day(s['report_database'], [day], now=now)[day.isoformat()]
        suffix = '+' if split['unallocated'] > 1e-8 else ''
        period_value = f'{split["day_of"]:.1f}{suffix} / {split["daily_event"]:.1f}{suffix}'
    except Exception:
        period_value = '-- / --'
    today_rows.insert(5, ('Day-of / daily h', period_value))
    # Read-only local calculation, exclusively in the notification worker.
    slots = next(f['value'] for f in fields if f['name'] == 'Slots')
    health = next(f['value'] for f in fields if f['name'] == 'Health')
    health = health.replace('REST rate', 'REST ceiling')
    next(f for f in fields if f['name'] == 'Health')['value'] = health
    capital = next((f['value'] for f in fields if f['name'] == 'Capital guidance'), 'Unavailable')
    model = dict(title=embed['title'], timestamp=s['observed_at'],
                 status=description.split('**SETTLED')[0].replace('**','').strip(),
                 color=embed['color'], risk=risk_rows, today=today_rows,
                 lifetime=lifetime_rows, coverage=coverage, reward_note=reward_note,
                 since=BOT_ERA_START_DATE, source=source,
                 slots=slots, health=health, capital=capital)
    fields.insert(1, {'name':'Today · closed fills','inline':False,
                     'value':'```text\n'+'\n'.join(f'{k}: {v}' for k,v in today_rows)+'\n```'})
    clean['allowed_mentions'] = {'parse': []}
    return clean, model
