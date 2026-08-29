# Production defect index

History through 2026-08-27: [archive](docs/history/BUGFIXES_THROUGH_2026-08-27.md).

Search it before designing a repair:

```powershell
rg -n -i "<symptom, component, or error>" BUGFIXES.md docs/history/BUGFIXES_*.md
```

## Deployed and live-accepted in `08b5b70`
- **A backward score clock turned half the slate into `OBSERVED`.** The high-water containment passed its regression/reversal and naturally logged one ignored stale sample before reaching 20/20 slots with no retirement. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **The laptop deployment suite lacked the recovery tool's encryption dependency.** `cryptography==50.0.0` is pinned with a declaration regression; the corrected laptop suite passed 807/807 before startup.
- **Rejected TV requests intermittently reset Windows sockets.** The read-only handler drains only a bounded small body before returning 405; regression/reversal and 807 tests pass, with trading still isolated.

## Latest deployed post-flat refill repair

- **Post-flat refill waited behind serial full-slate service.** The repair
  prioritizes proven mandatory gaps, revalidates cache no older than 120 seconds,
  preserves exit/ownership and 900-second idle rules, and measures delay and
  slot-minutes. Sixty-seven focused, 766 local/server and a reversal passed;
  `2ece3c3` is active. [Evidence](docs/history/POST_FLAT_REFILL_2026-08-28.md).

## Latest deployed defect repair; containment live-accepted

- **A Retail 403/code-7 rejection retired all 20 slots.** The gateway called
  every POST failure uncertain. Response-backed 403 now makes only that intent
  `FAILED`; a 60-second new-entry circuit prevents cross-slot churn and alerts
  once, while cancels/reducers stay available unless their own request is
  denied, which opens only a 10-second safety-action cooldown. Exact legacy
  rows repair at startup; timeouts/5xx/absence remain ambiguous. A cooperative outer-loop
  yield plus full no-ID idle backoff prevents CPU spin and SIGTERM starvation.
  Focused tests, three reversals and all 776 local/server tests pass. Commit
  `31f815c` repaired 20 legacy rows, retained all 20 owners through three denied
  probes, left zero unresolved/retired and stopped flat. Access remains denied.
  [Evidence](docs/history/MUTATION_ACCESS_DENIAL_2026-08-28.md).

## Latest deployed private-stream repair

- Silent shards formerly caused global cancellation and cross-shard clearing.
  Overlap replacement now requires per-connection balance, scoped events,
  fresh REST account proof and all-shard liveness; failed proof quarantines.
  `2831026` passed 740 local/server tests and live startup acceptance.

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

## Required entry format

Record symptom, mechanism/disproved alternatives, affected safety flows, regression/reversal proof, verification/deployment state and cost/uncertainty.
