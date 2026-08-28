# Production defect index

History through 2026-08-27: [archive](docs/history/BUGFIXES_THROUGH_2026-08-27.md).

Search it before designing a repair:

```powershell
rg -n -i "<symptom, component, or error>" BUGFIXES.md docs/history/BUGFIXES_*.md
```

## Latest deployed defects

- Dashboard account failure rendered false zero/flat; venue fields now require
  a complete snapshot. Closed flat owners parked slots; fresh metadata plus
  ownership proof now gates transactional replacement. Stable failure families
  stop alternating book errors from advancing rounds. Maintenance traces are
  bounded. Open paper loss is provisional until post-close evidence. Historical
  scans skip only post-cutoff cash; `1c82778` passed 745 local/server tests.

## Locally verified; deployment pending — no-ID startup loop

- After whole-account reconciliation, startup refuses a no-ID PLACE once with
  the stopped-only recovery command, before book binding. It never clears or
  retries ambiguity. The resolver now blocks on any live order in that market,
  not the unsent local client ID. Stop progress prints every 30 seconds without
  shortening the 300-second grace. Fresh proof found zero venue orders/positions
  and resolved the one 18:35Z intent; the bot remains stopped.
## Latest deployed private-stream repair

- Silent shards caused global cancellation and cross-shard snapshot clearing.
  Stale shards now overlap-replace after per-connection balance, scope-checked
  buffered events, fresh REST account proof and all-shard liveness. Empty order
  streams need no invented EOF; scoped snapshots clear only their markets.
  Failed proof retains global quarantine; reversals reproduced both defects.
  Commit `2831026` passed 740 local/server tests and live startup acceptance.

- **Active; natural fault-path acceptance pending:** a handoff reconciled 34 orders, then final
  balance Cloudflare `1015`/429 drove quarantine. Frozen handoff now gives only
  confirmed 429 reads four slower proof attempts (15/30/60 or bounded
  `Retry-After`) while draining events/rechecking liveness with zero cancels.
  Non-429, stale or exhausted proof retains global quarantine. `a8d91d0` is
  active; 744 local/server tests, reversal and clean 20/20 startup passed.

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

Record symptom, mechanism/disproved alternatives, affected safety flows,
regression/reversal proof, verification/deployment state and cost/uncertainty.
