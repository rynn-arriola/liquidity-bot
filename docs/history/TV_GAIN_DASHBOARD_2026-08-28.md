# TV Gain dashboard addition — 2026-08-28

## Operator decision

The existing TV dashboard design and every existing information panel remain
in place. The only UI additions are:

- a separate `TODAY P&L` and `OVERALL P&L` row at the top, leaving the
  existing four-card runtime row unchanged;
- a seven-day table below the existing dashboard whose visible columns are,
  in order, `Date ET | Trading | Gain | Pending | Side Hours | Net`.

The dashboard uses the operator-facing word **Gain**, not reward. Pending gain
is shown separately and is never included in Net. Side Hours means merged
resting side-hours bucketed by Eastern date, using the same accounting helper
as the seven-day report.

## Isolation and data flow

The existing hourly seven-day report remains the single account-reading
process. After its already-bounded reads finish, it atomically publishes
`data/tv-gain-snapshot.json` with owner-only file permissions. No extra venue
request, timer, websocket or credential reader was added.

The TV dashboard validates and reads only that local snapshot. Missing,
stale, malformed, oversized or non-finite data is displayed as unavailable;
the dashboard never calls the venue as a fallback and never substitutes a
false zero.

`TODAY P&L` is the current ET day's account trading value produced by the
report. `OVERALL P&L` is the bot ledger's exact lifetime realized trading P&L
plus bot-era PAID gain, with pending excluded. The ledger is opened using the
new SQLite read-only/query-only path, so the report does not perform schema,
WAL or migration writes.

## Activation boundary

This change does not require or authorize a trading-bot restart. After the
laptop pulls the published commit, restart only the local TV dashboard
service. The table will populate when the existing hourly report next writes
the snapshot. Until then, the new fields fail closed as unavailable while all
existing dashboard information continues to work.

## Verification

- focused reward-table, TV-dashboard and ledger suite: 74 tests passed;
- full repository suite: 791 tests passed;
- Python compilation, JavaScript syntax and shell syntax passed;
- the UI contains the exact requested column order and no visible occurrence
  of the word `reward`;
- the preview API reported the new headline and seven rows while retaining
  the existing dashboard source boundary: local read-only, zero venue calls.
