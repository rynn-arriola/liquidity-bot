# REST bulk proof and reducer continuity — September 11, 2026

## Scope and operator authority

The operator requested reviewed REST optimization, then reported another late
exit/canceled reducer and manually closed the positions. Initially restart was
deferred. They subsequently requested another edge-case review and authorized
restart after everything is checked. Preserve size 10 / 32, temporary maximum
30 pending flat-entry orders, lower clock/loss tiers, $90 HARD, reward selection,
900-second inventory age, exact tiny-residual 300 seconds, pregame protection,
and three IOC attempts within the existing 30-second window.

The stopped-state authenticated snapshot at 23:40:30.889425Z showed zero venue
orders and positions. Runner/supervisor 262812/262801 were absent. The production
ledger still retained four positions and four ACKNOWLEDGED PLACE intents; this
is stale local evidence, not venue exposure. Do not restart without a backup,
exact terminal reconciliation and another complete flat/clear gate. The old
runtime marker is `82acdfd14`; deployment/acceptance must be recorded below.

## Measured recurrence and corrected diagnosis

- `aec-setkameua-krayar-lazrus-2026-09-12`: reducer canceled at
  23:30:54.391432Z, replacement at 23:32:48.493832Z: **114.1 seconds**.
  A discovery/rebind occupied the intervening period.
- `aec-setkameua-tebyar-pytiva-2026-09-12`: reducer canceled at
  23:34:01.153871Z, with no replacement before manual cleanup. The later
  taker close lacks a corresponding bot IOC action; do not claim bot recovery.
- `aec-setkameua-shyant-kalser-2026-09-12`: fill at 23:21:35.805927Z,
  first position observed at 23:21:46.745099Z, hard cancel 23:37:51.204511Z,
  first IOC 23:38:10.849789Z. That is about 95 seconds beyond fill+900,
  or 84 seconds beyond observation+900. This recurrence was late BEFORE the
  first IOC; it does not prove another terminal-retry-window failure.
- The 23:37:12 API window used 658 calls, 430 order-by-ID reads, with zero
  HTTP failures/rate limits. Scheduler p95 input-to-decision was 490.686 seconds.

Local code let acknowledged cancel/IOC intents reach the unresolved-intent
skip before their next confirmation. A bounded priority pass could leave an
uncovered owner behind flat work; discovery guarded kickoff but not ordinary
inventory deadlines or reducer gaps. A successful held owner could also inherit
a flat quote's reward-dwell timer. The latter is a related preventive repair,
not a separately proven cause in these incident logs.

## Implementation

1. Restart-scoped `config/rest_polling_policy.json` enables two complete
   open-order snapshots for unchanged, flat, unambiguous OPEN/zero-fill entries.
   Every identity, quantity, price and status must match. Missing/changed rows,
   held-position orders and ambiguous owners retain exact individual reads.
   Duplicate/foreign identities fail closed; foreign risk is durably recorded.
2. Positions and covered execution history remain mandatory. Startup, rebind,
   recovery, shutdown and failed/expired proof remain strict. Failed/canceled
   proof revokes entry authority; total proof age remains at most 30 seconds.
3. Cadence is five seconds normally, one second with positions/pending intents,
   measured after completion, not a latency guarantee. Bounds are 3–5 normal
   and 1–normal active. The shared 4/s governor and four read workers stay fixed.
4. A held-owner cancel or hard IOC immediately performs focused confirmation
   after its one mutation. This cannot renew global entry authority or perform
   a second mutation. The guarded scheduler alone selects the next action.
5. Confirmed reducer cancellation requeues replacement ahead of flat work;
   bounded visits hand off to a fresh pass without dropping other held owners.
   Successful position service cannot inherit a flat reward-dwell timer.
6. Existing inventory/tiny/kickoff deadline crossings wake positions. Discovery
   is deferred for uncovered/ambiguous inventory and within 60 seconds of its
   existing exit deadline. This is scheduling, not an earlier exit policy.

Setting enabled=false and restarting restores strict ordinary polling while
retaining the continuity repairs. Default WebSocket behavior remains unchanged.
No absent order becomes canceled by inference, no unknown create is retried,
and no risk state is reset. No separate mutation workers were introduced.

## Verification and dedicated edge audit

The complete local pinned-WSL suite passed **1,350 tests in 143.251 seconds**.
Nine deliberate in-memory reversals each failed the intended regression;
all patches were restored. Two additional final-review tests explicitly cover
a partial fill during cancel (replace only six remaining contracts) and flat
during cancel proof (no accidental extra entry) passed in the 13-test continuity
module. The first expanded full run had only a documentation-budget failure
while handoff files were being edited; the corrected audit is clean. Final
complete results and deployment acceptance follow below.

The production-width rehearsal uses 30 unrelated entries, a held reducer,
the real planner/ledger/account-refresh/gateway flow and simulated 4/s admission.
Two IOC attempts expire unfilled, the third fills. From the 05:15:01 simulated
deadline, cancel occurs at 05:15:02.5, IOC attempts at 05:15:05/09.75/15.75,
and flat proof at 05:15:17.25; no fourth IOC or flat entry occurs before closure.
This assumes healthy publication and available liquidity; it is not a venue SLA.

The dedicated review covers empty/30/40-order snapshots, multiple signed/tiny
positions, missing cumulative fields, incomplete pagination, duplicate/foreign
ownership, fills between the two lists, exact missing-order proof, stale/regressed
timestamps, history failure, task cancellation/worker cleanup, unknown cancel,
failed position proof, stop after cancel, timer boundaries, bounded fairness,
source selection/config refusal, restart/rollback, unchanged policy and API load.

REST cannot detect every silently stale successful response and cannot force
fills without liquidity. Natural live acceptance must verify reducer continuity
and timely exits without inducing a fill or failure. Entry acceptance alone is
not proof of those paths.

## Final review and stopped reconciliation

The final clean local suite passed **1,352 tests in 144.559 seconds**; the
documentation/whitespace audits also passed. No source changes followed the
nine successful deliberate-reversal checks. The additional two tests passed.

Before ledger repair, the native SQLite online backup at 23:59:09Z was
347,631,616 bytes, integrity `ok`, SHA-256
`4215607578729abd934a22b78ea4bd64584320d40124daab9cdd44995dad30b3`.
Read-only proof retrieved all 29 stored working orders plus four acknowledged
creates without stored order rows; all 33 were positively CANCELED or FILLED.
Only after repeated zero-order/zero-position venue proof were those exact
terminal events, covered executions and complete flat positions applied.
At 00:02:28Z September 12, the ledger had zero working orders, unresolved
intents, unknown orders and nonzero positions. No venue mutation or risk reset
was used for this repair. Deployment/start acceptance remains to be recorded.
