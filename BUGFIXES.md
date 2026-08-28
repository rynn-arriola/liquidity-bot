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

Read current deployment/runtime state from [STATUS.md](STATUS.md) and the server.

## Reviewed locally; deployment pending

- Two silent private shards caused global cancellation and a transient zero-
  order slate although the runner and 20 owners survived; filtered snapshots
  could also clear another shard's state. Stale shards now overlap-replace only
  after exact-scope order EOF per replaced shard, one account-global balance
  proof, buffered events, fresh REST balance/account proof and all-shard
  liveness. Late
  heartbeats use the same no-cancel proof; any failure retains global quarantine.
  Scoped snapshots clear only their markets. Focused 20-slot, partial/failure,
  race, startup and cross-shard regressions passed; reversals reproduced defects.
  Full-suite and deployment state are in [STATUS.md](STATUS.md).

## Latest deployment additions

- Summary routing leaked routine notices through severity/fallback. Operations
  now use error/status only; summaries explicitly opt in. `1d48584` is active.
- Pregame owners blocked on both sides by named current-book economics could
  retain a slot. A deployed 10-minute flat/clear dwell releases oldest-first
  only against actual supply; authorization, exposure, ambiguity or other
  blockers prevent/reset release. Regressions and reversal passed; natural
  replacement acceptance is pending.
- Fixed-GTC excluded 17 post-reconnect pairs because one cumulative old gap was
  treated as episode-local. Coverage now uses overlapping stream intervals;
  legacy evidence stays conservative. The report is active; decode/drop closes
  its interval after the next collector reload. This is measurement-only.

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

Focused regressions and reversal mutations passed for each defect. The reviewed
03:06Z restart completed the additive migration. Its initial acceptance
reached 20/20 quoting, 37 resting orders, zero positions and zero unresolved
rows with no restart-window traceback, mutation timeout, runtime recovery or
stream-silence event. Fault-specific live acceptance remains natural-only.

## Required entry format

Record symptom, measured mechanism/disproved alternatives, safety/affected
flows, regression plus reversal proof, verification/deployment/acceptance state,
and operational cost or uncertainty. Do not duplicate current status here.
