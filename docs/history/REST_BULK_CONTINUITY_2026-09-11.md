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

## Authorized deployment and live acceptance

Reviewed release `1273a90a5` was pushed before deployment. WSL's direct GitHub
fetch stalled, so a verified Git bundle transported the already-pushed exact
commit for a clean fast-forward. All **176** source/test/script/web/config hashes
matched. The complete native suite passed **1,352 tests in 109.488 seconds**.
The deployment marker matches the release. Windows/GitHub/WSL parity passed;
the offline desktop peer is explicitly pending. The 00:09:02Z online recovery
copy was verified; it remains an ignored cold backup, never writable authority.

At **00:09:31.203345Z September 12** (20:09 ET September 11), authenticated
orders/positions and local working/position/unresolved/unknown counts were all
zero. The authorized `auto 10 32 rest-fallback` restart started supervisor
**290335**, runner **290344**. The live account-state line confirms bulk proof
enabled, normal/active 5/1-second targets, 30-second proof age, max-30 pending
entries and four read workers. Official maintenance state was HEALTHY/NORMAL.

A natural fill in `aec-setkamecz-kukzde-bridav-2026-09-12` caused builder cancel
at 00:11:27.991Z and reducer placement at 00:11:47.143Z. A subsequent reducer
reprice canceled at 00:11:54.580Z and replaced at 00:12:14.160Z: **19.58 seconds**,
without an intervening flat entry. Unrelated entries resumed at 00:12:56Z.
This accepts the live continuation path, not instant REST service or a claim
that every latency source is eliminated. The 900-second hard-timeout/three-IOC
path still requires a natural event; no position/failure was induced.

At **00:13:28.238355Z**, authenticated venue state had **eight orders across
eight markets and one position**; local state also had eight working orders,
one position, zero unresolved intents and zero unknown orders. The first API
window counted 314 attempts, two failed requests, zero rate limits, maximum
queue one and zero cooldown. No terminal runner failure occurred; the two
requests are not attributed to a venue outage. This is startup/partial-width
acceptance, not full-width performance proof. Remaining measured REST delay
must stay visible and must not be described as one-second fill/exit latency.

## Natural hard-exit observation

The position in `aec-setkameua-tuzole-pysyev-2026-09-12` naturally reached
the 900-second inventory deadline. The runner canceled its last working order
at 00:25:47.404Z, obtained focused proof at 00:25:54.648Z and submitted the
exact IOC at 00:26:03.841Z. It was absent from the authenticated position list
at 00:27:11.403Z. This accepts one filled hard-exit path. It does not accept an
unfilled IOC retry sequence or promise the same latency when venue responses or
liquidity differ.

At 00:28:27.501Z two newer positions remained: 10 contracts in
`aec-setkameua-derdmy-gomole-2026-09-12` with an exact SELL-10 reducer, and
5 contracts in `aec-setkameua-paldmy-slomyk-2026-09-12` with an exact SELL-5
reducer. At 00:28:41.804Z authenticated venue and local state agreed on 29
working orders and two positions, with zero unresolved intents or unknown
orders. They are being worked passively, not yet closed.

## Later contrary live evidence

The initial filled-IOC observation did not validate unfilled retry continuity.
`aec-setkameua-krydmy-komvad-2026-09-12` remained -10 after IOCs at
01:04:50.882Z, 01:11:47.248Z and 01:14:50.334Z. The ledger positively recorded
each IOC EXPIRED with zero fill in under one second, yet each
`hard_exit_confirmation` continued until about 30 seconds and logged
`hard_exit_retry_window_exceeded`. The attempts were minutes apart, not the
intended bounded sequence inside one 30-second window.

At 01:16:19.043Z venue and ledger agreed on 31 working orders and three
positions with zero unresolved intents/unknown orders. Two other positions had
exact passive reducers. This -10 owner had no reducer while waiting for another
hard retry. The runner stayed alive and ownership stayed known, but the safety
outcome is incomplete. This is positive evidence of local proof/scheduling
delay, not a claim of venue ambiguity or outage. Do not represent
`1273a90a5` as accepting unfilled-IOC continuity.

## Reviewed unfilled-IOC follow-up

The terminal IOC proof was not missing. After every IOC mutation,
`position-transition` reconciliation retrieved that owner's exact order and
complete open-order, position and execution-history snapshots. Production
recorded each IOC `EXPIRED` with zero fill in under one second. On the next
decision, `_reconcile_pending_hard_exit` nevertheless started the same focused
network proof again. At production width that redundant work consumed the
confirmation/retry window, so the owner retired and was revisited minutes later.

Prepared REST-only source records a successful post-IOC proof against the exact
hard-exit schedule token and consumes it at most once. Reuse is allowed only
while both the proof and IOC send are within the existing ten-second
confirmation timeout, the token is unchanged, the exact position remains
nonzero and the owner has no unresolved intent, working order or
`UNKNOWN_ABSENT` order. A partial fill therefore retries only the authoritative
remaining quantity. Stale/backward time, a new attempt, restart, working or
missing state, ambiguity or proof failure takes the original exact-proof path.
The scoped proof still cannot renew entry authority or the ordinary poll clock.
Default WebSocket mode, API limits, size, capacity, strategy, pricing, exit
attempt/deadline and risk policy are unchanged.

The dedicated production-width rehearsal proves one cancel and three terminal
IOC attempts finish in 12.5 simulated seconds with 30 unrelated entry orders;
only the four required immediate `position-transition` proofs occur. A separate
partial-fill regression proves the next IOC uses exactly six remaining
contracts. Changed token, stale proof, stale IOC send, working order and absent
order each force a fresh proof. The six affected modules pass **202 tests in
24.102 seconds**; the complete pinned-WSL suite passes **1,354 tests in 152.359
seconds**. Deliberately restoring the duplicate proof fails the production-width
regression; all **10/10** cumulative reversals were detected and restored.

The final edge review covered terminal/partial/flat outcomes, stale and backward
clocks, changed attempt tokens, working/absent/unresolved ownership, proof
failure, stop/restart behavior, 30-order capacity, API load, exact sizing,
attempt/time budgets and default-source isolation. No deployment or restart had
occurred when this review completed; the operator then authorized the normal
safe restart workflow. Natural unfilled-IOC acceptance remains required.

## Follow-up deployment and restart acceptance

Release `39df87915` was pushed before deployment. The stopped old runner's
terminal record was truthfully `flat=false`: two positions remained with exact
reducing orders. At 01:36:39Z authenticated venue and local state agreed on two
orders/two positions with zero unresolved or unknown state. The operator then
manually closed both. Stopped reconciliation at 01:42:23Z positively applied
both terminal order states and two new fills; gates at 01:42:34Z and 01:43:34Z
independently proved zero venue orders/positions and zero ledger working,
position, unresolved or unknown state. No absence was inferred as cancellation.

The stopped-safe 349.2 MB pre-deployment backup passed SQLite integrity `ok`
with SHA-256 `5c51b0e55346430c224985fc145d7d49063ddf132bcdaabb961f20193f96b853`.
Because WSL's GitHub fetch stalled, the verified 6,040-byte Git bundle carried
the already-pushed one-commit fast-forward. Exact WSL HEAD and deployed marker
match `39df87915`; its native checkout passed all **1,354 tests in 106.766
seconds**. Windows/GitHub/WSL parity passed; the offline peer remains pending.

The authorized `auto 10 32 rest-fallback` restart began as supervisor **348491**
and runner **348502**. It logged the unchanged max-30 temporary entry ceiling,
five/one-second normal/position poll targets, 30-second proof age and four read
workers. Official maintenance was HEALTHY/NORMAL. The first startup window made
305 read attempts with zero mutations, failures or rate limits; one market with
unavailable pregame evidence was dropped fail-closed. At 01:49:06Z authenticated
venue and local state matched at **21 orders across 21 markets**, zero positions,
zero unresolved intents and zero unknown orders. This accepts deployment,
startup and ordinary entries only. A naturally unfilled IOC sequence is still
required to accept the repaired path; none was induced.
