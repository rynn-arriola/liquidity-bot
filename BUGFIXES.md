# Production defect index
History through 2026-08-27: [archive](docs/history/BUGFIXES_THROUGH_2026-08-27.md). Search it before designing a repair with `rg -n -i "<symptom, component, or error>" BUGFIXES.md docs/history/BUGFIXES_*.md`.
- **Loaded in `0245d31`; operator-visible field acceptance pending:** the five-minute Discord post could sample a normal cancel before positive terminal proof and repeatedly show capital guidance as `UNAVAILABLE`, even though a clean sample seconds later produced `$394.25`. Routine summaries now wait at most 60 seconds only while balance is fresh and current order ambiguity is the sole blocker, rechecking the ledger every two seconds with no venue call. Stale or persistent uncertainty remains unavailable; shutdown/critical summaries never wait. Three regressions, exact reversal, 973/973 staged WSL tests, flat-gated restart and the first natural scheduled path passed. [Evidence](docs/history/CAPITAL_GUIDANCE_TERMINAL_HISTORY_2026-08-31.md).
- **Loaded in `9001a4b`; 30-minute canary gate passed:** the capacity model omitted two five-second risk-mark reads, understating every scenario by 0.40/s; it now reproduces 22/24 normal `2.10/2.16/s` and 24 busy `3.78/s`. The TV browser accepts 20-24 and uses 12 columns at 24. Across 26 live windows and 2,774 attempts, 24 slots stayed within `1.209-2.154/s` with zero failures/429s and queue depth one. [Evidence](docs/history/SLOT_CAPACITY_CANARY_24_2026-08-31.md).
- **Loaded and live-accepted in `9001a4b`:** Discord counted every historical `venue:` row as unknown forever, so 55 filled and four rejected rows forced capital guidance to `UNAVAILABLE` despite fresh balance evidence. The summary now counts only active unowned venue rows plus every `UNKNOWN_ABSENT`; the trading ownership gate is unchanged. Live evidence correctly hid the amount during one real cancel intent, then reported zero unknown/unresolved and `Free to withdraw $394.25` after proof. [Evidence](docs/history/CAPITAL_GUIDANCE_TERMINAL_HISTORY_2026-08-31.md).
- **Loaded in `9001a4b`; natural path acceptance pending:** `0.01`–`0.09` contract residues could retain a full reward slot for the ordinary 900-second passive exit window. Source `6c5ea49` gives an exactly venue-aligned residue five passive minutes, then reuses the builder-first, one-cancel-at-a-time, positive-proof IOC path. Unknown/misaligned quantity and insufficient displayed depth remain fail-safe; `0.10+`, entries, capacity and API cadence are unchanged. [Evidence](docs/history/TINY_RESIDUAL_CASHOUT_2026-08-31.md).
- **Active in `31c2574`; natural two-order acceptance pending:** hard exit returned every cancel although the driver executes only the first, then a shared timer blamed untouched orders while reducer-first ordering left the builder live. The repair emits one cancel, building side first, and starts exact-order grace only after submission; ambiguity still quarantines. Reversal, focused slices and all 952 exact deployed-checkout tests passed. The 17:46Z restart followed graceful flat shutdown plus independent authenticated zero-order/zero-position proof; normal size-13/22 startup reached 38 orders with zero failures/429s. [Evidence](docs/history/HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md).
- **Dashboard active and exact runner scope accepted in `fda2bb37`:** after the queue collector was retired, the TV slot map kept consuming its final `STOPPED` scope. The dashboard now rejects stopped/stale/malformed/wrong-capacity scope, falls back to current read-only ledger evidence and prefers a bounded atomic active-scope snapshot tied to the exact publisher PID. Runner PID 937743 published all 22 bound markets after the 17:46Z restart. [Evidence](docs/history/TV_DASHBOARD_ACTIVE_SCOPE_2026-08-30.md).
- **Deployed `0ff6e3c6`; restart acceptance pending:** the first host governor still let a background 429 block safety, shared entry/background clocks, misclassified passive reducers, performed lock/file work on the event loop, slept uninterruptibly and silently lost host coordination on Windows. The repair keeps one aggregate 4/second WSL gate but separates lanes, caps safety cooldown at one second, defers long non-safety work before network IO, preserves reducing priority, wakes old waits and fails closed on Windows. It also stops each selection/revalidation scan at its first local deferral, keeps it out of market-error accounting and asynchronously merges bounded peer cooldowns under a non-blocking evidence lock; genuine per-market errors remain counted. Mutations never retry, another machine remains outside coordination and no local design can guarantee no first edge 429. Two follow-up reversals failed exactly and all 926 local/deployed tests pass. [Admission evidence](docs/history/HOST_API_ADMISSION_PRIORITY_2026-08-30.md) · [observability evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md).
- **Reviewed; deployment/restart acceptance pending:** Discord/dashboard could show a gain while the account was losing because `assetNotional=0` hid open `cashValue` and closed P&L was mislabeled as marked. All current-day displays and HARD now share a cash-bracketed marked-trading equation; incomplete breakdowns fail closed. [Evidence](docs/history/ACCOUNT_MARKED_PNL_2026-08-30.md).
- **Deployed in `859b891`; natural lifecycle acceptance pending:** eight bound markets reached kickoff together, but each expected `no longer pregame` refresh was treated as a recurring runtime error and refill waited for the ordinary 15-minute selector. A bound, positively observed kickoff now requests one immediate incremental top-up; startup still rejects already-started candidates, urgent inventory still has priority, and the existing flat, ownership, reconciliation, viability and pre-commit gates remain unchanged. The focused 48-test class, all 844 Windows/WSL tests and a deliberate reversal passed. [Evidence](docs/history/BOUND_LIFECYCLE_REFILL_2026-08-29.md).
- **Deployed `c8a7a45` without a process restart:** the TV page was 1,542px tall in a 720px viewport, hiding lower information behind scrolling. A viewport-height grid, landscape slot map, height breakpoints and fit-aware activity tail now keep all panels on one screen from 800×600 through 1920×1080. Reversal failed and all 842 tests pass. [Evidence](docs/history/TV_DASHBOARD_VIEWPORT_FIT_2026-08-29.md).
- **Deployed in `859b891`; first summary acceptance pending:** non-trading cash could make Discord's HARD bar under-report the unchanged stop's adjusted-equity consumption. The headline stays raw account P&L; HARD now reads the guard input. Reversal failed and all 844 deployed-checkout tests pass. [Evidence](docs/history/HARD_METER_NONTRADING_CASH_2026-08-29.md).
- **Deployed `2aa9fec`; capacity ceiling remains open:** steady contract/reward refresh had scaled as two REST reads per market per minute. The repair bulk-reads bounded groups (8/8/4 at 20), retains exact validation/exit gates and adds secret-free limiter telemetry. All 830 deployed tests pass. The first exact restart measurement found 10 Cloudflare `1015`/429s on execution-history backfill in 64 seconds despite 1.813 aggregate attempts/s; the next 66.2-second window was clean, but 20 remains the ceiling pending endpoint isolation and a clean restart. [Capacity evidence](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence).
## Deployed in `a1ba212`; natural timeout acceptance pending
- **Quiet private shards were repeatedly classified as dead.** The old clock saw only JSON and ignored healthy protocol Ping/Pong. Each connection now records only an awaited Pong; missing/failing proof enters existing recovery, and REST still cannot certify the socket. Focused suites passed 23/23 and 16/16, the deliberate reversal failed exactly, and all 810 deployed WSL tests passed. The flat-gated restart loaded the repair at 12:25Z. [Evidence](docs/history/PRIVATE_SHARD_FALSE_SILENCE_2026-08-29.md).
## Deployed and live-accepted in `08b5b70`
- **A backward score clock turned half the slate into `OBSERVED`.** The high-water containment passed its regression/reversal and naturally logged one ignored stale sample before reaching 20/20 slots with no retirement. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **The laptop deployment suite lacked the recovery tool's encryption dependency.** `cryptography==50.0.0` is pinned with a declaration regression; the corrected laptop suite passed 807/807 before startup.
- **Rejected TV requests intermittently reset Windows sockets.** The read-only handler drains only a bounded small body before returning 405; regression/reversal and 807 tests pass, with trading still isolated.

## Latest deployed post-flat refill repair
- **Post-flat refill waited behind serial full-slate service.** The repair prioritizes proven mandatory gaps, revalidates cache no older than 120 seconds, preserves exit/ownership and 900-second idle rules, and measures delay and slot-minutes. Sixty-seven focused, 766 local/server and a reversal passed; `2ece3c3` is active. [Evidence](docs/history/POST_FLAT_REFILL_2026-08-28.md).

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
