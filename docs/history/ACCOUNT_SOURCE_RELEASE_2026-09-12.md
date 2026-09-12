# Permanent manual account-source release — September 12, 2026

## Authority and scope

The operator approved implementation, end-to-end review, edge-case checks and a
restart after the fix, reaffirming review-before-restart during development.
REST remains the selected source. No automatic failover/failback, larger API
budget, different polling interval or strategy/risk change was authorized.
The existing size 10 / capacity 32 and max 30 flat pending REST entry orders stay.
Positions, reducers and IOC exits never consume that additional entry ceiling.

## Implementation and measured evidence

- One `AccountSourceRunner` delegates both modes to the existing strategy,
  planner, ledger and mutation executor. Compatibility classes/REST alias remain.
- Mode changes require a process/generation-bound, expiring operator request.
  Candidate private collection is bounded; genuine per-shard state and complete
  REST account/history/balance evidence precede promotion. The current engine,
  working orders, position ages, IOC budgets and risk latches are retained.
- Independent exact GET workers now return normalized observations; one serial
  owner projects them. Positions/history collection overlaps without overlapping
  proofs or introducing another writer. The shared governor remains 4/s.
- A queued mutation rechecks the source generation after admission. The handoff
  itself performs no place/cancel and defers for active mutations or urgent exits.
- Positive task-held/queued/retired-stream evidence is ordered and retained;
  abandoned partial candidate pages are removed. Failed proof does not persist
  the target or grant entry authority. Stop supersedes switching.
- Supervisor restart uses the last verified source only when proof belongs to
  that exact child run; an earlier process's status cannot override an explicit
  startup choice after failed preflight.
- An offline native SQLite copy of the verified 06:19:52Z recovery ledger held
  116,573 intents. Twenty-nine latest-PLACE lookups took 0.727968 seconds with
  repeated table scans versus 0.000373 seconds using the additive covering search
  prefix `(client_order_id, operation, created_at DESC)`; returned rows matched.
  This is query-level evidence, not an end-to-end latency claim. The index changes
  no ledger row or trading calculation and is compatible with older readers.

## Dedicated end-to-end and edge-case audit

Reviewed startup/config validation, steady-state source hooks, manual handoff,
admission, exact-order reconciliation, reducer continuation, failed-source recovery,
shutdown, supervisor restart and additive-schema rollback. No credentials or live
database were added to tracked files. No test submitted a real venue mutation.

| Area | Evidence / retained boundary |
|---|---|
| Empty/30/full-width | Empty bidirectional handoffs; inherited bulk tests for 30 entries and full-width position-priority/IOC replay |
| Open positions | Real ledger/planner tests retain a live reducer, quantity, original opening/deadline and state objects through both switches; existing partial/tiny/long/short and exact-exit coverage retained |
| Entry cap / business rules | WebSocket lifecycle/cap/HARD replay through the new runner; REST 30-count excludes positioned orders and unresolved reducer reservations; lower clock/loss rules unchanged |
| Race / ordering | Held private event precedes backlog; late partial page during balance blocks promotion; in-flight mutation defers and changed generation blocks send |
| Incomplete / unknown | Silent candidate cannot promote; partial EOF, position disagreement, failed history/balance/exact GET, foreign/duplicate order and absence remain fail-closed |
| Bounds / cancellation | Candidate and stream queues bounded; all callback kinds overflow closed; single-flight read cancellation joins workers; handoff proof deadline 30 seconds |
| Recovery / stop | Explicit REST switch away from a dead private feed; failed/cancelled handoff restores selected source without resetting proof; stop closes request; normal shutdown retains existing exact exit limits |
| Restart | Atomic request publication; duplicate/stale/wrong-generation/expired request refusal; no previous-run replay; successful source persistence and supervisor child-run validation |
| Risk / ownership | Same ledger/executor/reconciler; no unknown-create retry or absence-as-cancel; HARD/operator/maintenance holds survive switching |
| Rollback | Additive index only; old releases ignore new source control files and require explicit supported launch arguments plus normal safe rollback gates; no automatic rollback or source change |

Three deliberate source reversals removed the intent index, post-admission
generation check and private/REST position comparison. All three targeted tests
failed for the expected assertions. Restored code is required before release.
The supervisor fixture was extended for its new read-only run-identity command;
the exact-GET failure fixture now injects failure at the actual venue-read boundary,
so it tests both old and new collection implementations rather than a bypassed helper.

## Release evidence

Development started from clean `72045edfc`; the initial parity check passed the
available Windows/GitHub/WSL authorities, with the offline desktop peer pending.
The old REST runner `529535` / supervisor `529523` remained active during development.
Final suite, stop/flat gates, deployment hashes, restart and authenticated acceptance
are appended below when actually verified; none is inferred from unit tests.

The final native review suite passed **1,467 tests in 130.916 seconds**. This
includes the restored reversal targets, permanent-runner REST bulk/position/IOC
replays, WebSocket business/safety replays and isolated real supervisor-shell tests.
Dedicated diff review found no business-policy change, mutation parallelism,
credential addition or source-triggered risk-latch reset. The test harness emits
pre-existing temporary-directory ResourceWarnings; these are not venue errors.

## Limits and remaining acceptance

### Verified stopped deployment

- Code commit `eb06d1b9cdc25fa3954778cbda55d93de1ce12ed` was pushed before
  deployment. The clean WSL checkout fast-forwarded to that exact reviewed commit;
  direct WSL HTTPS fetch stalled, so it fetched the already GitHub-verified Windows
  checkout locally. No tracked files or user changes were overwritten.
- All **181 source/test/script/web/config hashes** match after line-ending
  normalization. `DEPLOYED_COMMIT` names the code release. The complete exact
  deployed suite passed **1,467 tests in 125.093 seconds**; shell syntax and runner
  CLI smoke checks passed. No live runner was active during deployment/tests.
- Graceful shutdown ended at **07:03:14Z**, reporting `flat=false` with two
  parked positions/reducers. Authenticated **07:04:05Z** proof found two orders,
  two positions and zero unresolved/unknown state. Restart was withheld.
- The operator manually closed the positions. At **07:07:55Z**, complete venue
  reads proved zero orders/positions; the inactive ledger still had the prior two
  rows. A leased, disarmed prestart helper first required flat venue and exact
  terminal responses for both owned order IDs, disabled every mutation entry
  point, then invoked the normal complete REST reconciler. It finished ledger
  flat/clear with **zero venue mutations**; no guessed cancellation, raw row edit,
  baseline reset or hard-stop/loss-cap reset was used.
- The verified stopped recovery copy captured at **07:03:45Z** is 351,232,000
  bytes, integrity `ok`, SHA-256
  `64a41cdba1546dd1bed978b4fb4ec9479743d71832e2ef5c89312d22d7ddccb8`.
  It predates the prestart reconciliation; a fresh parity copy and final gate
  must precede restart. The offline desktop peer remains explicitly pending.

### Remaining measurement

The first release overlaps independent reads inside one serial proof cycle. It
does not yet run a separate background fill-watch job or promise one-second fill
detection. Chunked catalog work and comparable cross-source end-to-end latency
percentiles remain follow-on measurement work; do not widen cadence or API budget
based on the offline index benchmark alone. Startup without bound scope and long
venue-health holds retain their existing gates rather than bypassing them for
switch control. A stale status refuses requests.

Live WebSocket handoff remains unaccepted until an operator explicitly requests it
and genuine venue private data passes verification. Do not induce fills, outages,
HARD losses or source changes to manufacture acceptance. No architecture can
guarantee executable liquidity or detect every stale-but-successful REST response
without an authoritative venue publication watermark.
