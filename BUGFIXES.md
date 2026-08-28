# Production defect index

The complete defect record through 2026-08-27 is preserved at
[docs/history/BUGFIXES_THROUGH_2026-08-27.md](docs/history/BUGFIXES_THROUGH_2026-08-27.md).

Search it before designing a repair:

```powershell
rg -n -i "<symptom, component, or error>" BUGFIXES.md docs/history/BUGFIXES_*.md
```

## Latest deployed defects

- Dashboard account-read failure could render false zero/flat state. Fixed by
  publishing venue-backed fields only after a complete account snapshot.
- Closed/not-open flat owners could park nominal 20-slot capacity. Fixed with
  fresh exact metadata and complete ownership proof before transactional
  replacement.
- Alternating permanent book-error strings advanced retirement rounds. Fixed
  by stable permanent failure families.
- Maintenance repeated the same full traceback every minute. Concise attempts
  remain complete; full traces are bounded.
- An open-position paper mark could force loss-realizing hard-stop shutdown.
  Fixed with provisional breach state and newer post-close balance evidence.
- Historical/current reward-table trading rows omitted account losses. Current
  reporting uses bounded account truth when its prerequisites are proved and
  visibly marks fallbacks.

The latest trading-source repair is `1ff0e42`; current deployment and runtime
state must always be read from [STATUS.md](STATUS.md) and the server.

## Reviewed locally; deployment pending

- The summary webhook accepted routine notices because default routing followed
  severity, and missing error config fell back to summary. Operations now use
  error/status with no fallback; live, shadow and dashboard summaries opt in,
  and supervisor alerts select errors. Exact reversal failed; restoration and
  all 724 local tests passed. Commit `1d48584` is pushed; production is unchanged.
- Pregame owners blocked on both sides by named current-book economics could
  retain a nominal slot indefinitely because incremental top-up recognized only
  lifecycle loss. The local repair tracks a 10-minute continuous flat/clear
  dwell and releases oldest-first only when the same normal scan supplies an
  actual viable replacement. Any authorized side, position, working or absent
  order, unresolved intent, unknown evaluation or non-economic blocker resets
  or prevents release. Focused ownership/supply regressions passed, deliberate
  removal of the predicate failed its regression, restoration passed, and all
  719 local tests passed. Production is unchanged pending deploy/restart.

## Deployed and active; natural fault-path acceptance pending

- Venue execution history can be reset, so an empty activity page was wrongly
  described as restart-surviving history. The adapter now exposes only positive
  retained fills and independent trade-state observations; empty history is
  never negative account evidence.
- REST reconciliation could keep the risk gate fresh while one of three private
  WebSocket shards, or the market socket, was silently dead. Each connection
  now has an independent 180-second last-message deadline and scoped recovery.
- A later `TRADE_STATE_BUSTED` event left the original fill in position basis,
  realized P&L and reports. Trade identity/state are durable and the linked
  fill reverses exactly once; market-identity conflicts fail closed and newer
  authoritative position snapshots prevent double reversal.
- Twenty entry decisions could reuse one stale buying-power observation, while
  persistent rejects could hot-loop. Successful entries now reserve cached
  power during the sweep and structured rejection delays are market-local.
  Reducing post-only orders, cancels and IOC exits bypass both protections.
- Read errors discarded useful HTTP context and confirmed 429 reads ignored
  `Retry-After`. Adapters now preserve safe status/code/trace metadata and use
  bounded numeric or HTTP-date retry guidance; mutations remain non-retrying.
- Owned SDK clients used the 30-second default instead of the official
  15-second REST recommendation. Commissioning, probe and reward census now
  construct clients with the same explicit 15-second timeout.
- Changelog checks were manual only. A public-RSS-only monitor now establishes
  a no-spam baseline, durably retries failed notifications and runs under an
  isolated lock/timeout wrapper. Its minute-23 hourly schedule is active.
- The pre-deploy server still has the old fill schema, so immediately deployed
  read-only reports would have failed on the new `reversed` column before the
  next runner startup migrated it. Reports now detect the column read-only:
  old schema treats every historical fill as active, while migrated schema
  excludes busted fills. This migration is now active.

Focused regressions, edge cases and deliberate reversal mutations passed for
each defect. Commit `1ff0e42` is pushed; all 690 local and server tests passed
and source/test parity matches the deployed marker. The reviewed 03:06Z restart
loaded the code and completed the additive migration. Its initial acceptance
reached 20/20 quoting, 37 resting orders, zero positions and zero unresolved
rows with no restart-window traceback, mutation timeout, runtime recovery or
stream-silence event. Fault-specific live acceptance remains natural-only.

## Required entry format

Every production defect must record:

1. operator-visible symptom;
2. measured mechanism and disproved alternatives;
3. safety constraints and affected flows;
4. focused regression plus deliberate reversal proof;
5. complete local/server verification and deployment/live-acceptance state;
6. operational cost or exposure uncertainty.

Do not turn this index into a second current-status file.
