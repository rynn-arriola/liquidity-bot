# Production defect index

History through 2026-08-27: [archive](docs/history/BUGFIXES_THROUGH_2026-08-27.md).

Search it before designing a repair:

```powershell
rg -n -i "<symptom, component, or error>" BUGFIXES.md docs/history/BUGFIXES_*.md
```

## Latest deployed defects

- Dashboard false-zero account state, parked closed owners, alternating book
  failures, maintenance trace growth, provisional open P&L and historical cash
  cutoffs were repaired in `1c82778`; 745 local/server tests passed.
- Dashboard scaling now reads the configured instance instead of stale class
  fallbacks; size 20 displays `$80/$160/$160`.

## Latest deployed post-flat refill repair

- **Post-flat refill waited behind serial full-slate service.** The repair
  prioritizes proven mandatory gaps, revalidates cache no older than 120 seconds,
  preserves exit/ownership and 900-second idle rules, and measures delay and
  slot-minutes. Sixty-seven focused, 766 local/server and a reversal passed;
  `2ece3c3` is active. [Evidence](docs/history/POST_FLAT_REFILL_2026-08-28.md).

## Latest deployed no-ID startup repair

- Startup refuses a no-ID PLACE before book binding and never clears/retries it; repair blocks any same-market live order. Flat proof resolved the 18:35Z intent; `c28aba2` passed 755 tests, three reversals and parity. Its 19:54Z start reached 19/20 quoting, 19 resting, 0 positions/absent/unresolved and no risk stop.

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

Focused regressions and reversals passed. The reviewed 03:06Z restart completed
the migration and reached 20/20 quoting, 37 resting orders, zero positions and
unresolved rows, with no traceback, mutation timeout, recovery or stream-silence
event. Fault-specific live acceptance remains natural-only.

## Required entry format

Record symptom, mechanism/disproved alternatives, affected safety flows, regression/reversal proof, verification/deployment state and cost/uncertainty.
