# Entry churn diagnosis — September23

## Scope and state

Operator paused300-coverage work to prioritize low orders/rotation. Its offline
full-test process was terminated; no collector or recurring schedule was started.
Uncommitted diagnostic improvements remain in the isolated scan-review worktree.
At initial diagnosis main trading source, live configuration and runner were
unchanged. The authorized prepared repair is documented below; deployment and
restart remain unauthorized in this task.

Live source20c44a9d7, supervisor277537/runner277553 started16:00:18Z;
hybrid account state, size15/32, four$30 settled-fill windows. Public books and
market filters are unchanged.

## Observed facts

- At16:29:19.729–16:29:27.682Z,13 different market action plans cancelled
  resting entries with `side_no_longer_desired:account_new_risk_paused`.
  Positive cancel-terminal notifications followed. Replacement plans resumed.
  Therefore the earlier filter-only explanation of low orders was incomplete.
- Read-only ledger inspection through16:33:37Z found185 FAILED intents since
  startup, all `ApiAdmissionDeferred: private observation pending; request not
  sent`. These are local refusals, not185 HTTP429s or185 venue requests.
- The16:33:30Z settled window checkpoint was+$1.9153, cap30, no hard trip.
  Loss scaling therefore did not explain the contemporaneous low count.
- Authenticated16:30:33Z:12 flat entries and exact BUY7.95/BUY5 reducers for
  positions-7.95/-5; zero unresolved/unknown state.
- Authenticated16:39:21Z:26 flat entries and exact BUY5/BUY15 reducers for
  positions-5/-15; zero unresolved/unknown state. Total venue28 versus live
  ledger27 is a non-atomic observation, not proof of missing ownership.
- The16:36:26Z API window had244 calls/80.7s,3.023rps and one real
  cash-history429. Local admission failures and HTTP429 must stay separate.
- The16:29 scan had15 ranked books/12 viable opportunities; filters can
  legitimately limit supply too. Top-up retained owners, not wholesale
  profitability rotation. No reward-dollar impact has been established.

## Code trace and offline reproduction

The hybrid account checkpoint drains notifications before REST proof. Later
equity, event, book and rate-admission awaits can receive more private events.
The final synchronous POST admission refuses any pending event, including a
not-yet-classified harmless acknowledgement. Local refusal starts an entry
backoff, and subsequent planned creates can wait behind it.

An ignored offline diagnostic using the existing fake-client/temporary-ledger
fixtures passed three cases in the exact production environment:

1. An unchanged, owned NEW/no-fill ACK queued after account checkpoint blocks
   POST. Draining it classifies ACK-only and permits POST without extra REST.
2. A position hint arriving during REST proof still blocks POST.
3. An applied position hint remains blocked until complete REST reconciliation.

This proves a timing mechanism, not that every live refusal was an ACK. The
final pending-event guard itself is required; deleting it would introduce a
fill/quantity race.

The account-pause reason is a separate issue. `_entry_risk_allowed` combines
window, equity freshness, day/cash, pause and event gates into one boolean.
`_refresh_balance` immediately withdraws equity authority after a failed
settled-window proof, while the sweep suppresses repeated refresh attempts.
Consequently a failed bracket can fan out into entry cancellations. Current
logs omit the underlying exception/subreason, so the16:29 trigger cannot yet
be assigned to cash mismatch,429,age or another gate. This shared path also
exists in REST mode; it is not established as a hybrid-only defect.

## Recommended next work and acceptance

1. Capture the exact pause blocker/failed bracket category locally with bounded,
   redacted transition telemetry and no diagnostic API calls.
2. Replay ACK/fill/cancel arrivals throughout equity/book/admission waits and
   delayed/failed risk refreshes through the complete decision sweep.
3. Evaluate an ordered pre-plan notification checkpoint and a deliberate wait
   for required proof instead of repeatedly creating already-blocked intents.
   Replan from current quantity; retain the last-moment admission fence.
4. Compare unchanged REST versus hybrid read counts, durable intents, useful
   resting coverage, reducer latency and recovery. Do not force extra account
   reads per harmless ACK, retry unknown creates or relax freshness/stop rules.
5. Require focused regression/reversal tests, full suite and edge-case review
   before a repair is pushed; explicit deployment/restart authority and normal
   flat/clear gates remain necessary. No live fault induction.

No production code fix, policy change, deploy or restart occurred in this
diagnostic continuation. The300-coverage task remains paused.

## Follow-up: operator-requested fully-private stable comparison

Read the immutable `v1.2.0` tag (`a3defc014`, executable equivalent to3625927fb),
not the newer REST rollback tag. It is the last designated pre-REST checkpoint
and passed1,228 tests; by the September10 tagging time the private outage already
existed. The earlier `v1.1.0` is the separate healthy24-slot canary reference:
26 API windows/2,774 attempts/zero429s. Neither tag proves today's feed complete.
No checkout/rollback, historical database activation or live source switch made.

Direct `git show` and diff review found:

| Concern | v1.2.0 private source | Current hybrid source |
|---|---|---|
| Ordinary private updates | Projected into ledger; valid private evidence refreshed session freshness | Projected with REST gate retained; neutral ACKs keep cadence, other hints latch POST proof |
| Cancel-confirmed owner | Exact owner gets priority replan | Same base priority remains, plus scoped REST reducing proof and hybrid final admission |
| New event awaiting application | No current hybrid pending-queue fence | Any pending event bars POST; safety fence cannot simply be removed |
| Risk refresh failure | Previous mark retained until freshness expired; refresh could retry at subsequent markets | Settled-window failure immediately clears risk observation; subsequent markets in same sweep do not repeat failed bracket |
| REST use | Startup/recovery/reconciliation and periodic balance/history reads still existed | Complete account authority remains REST because private completeness is not proved |

The risk-failure change is from971f7812f (September15 six-hour-window work), not
the September22/23 hybrid change. The per-sweep failed-refresh coalescing is
froma8888b5e (September12), avoiding per-market API amplification. The old planner
already cancelled flat entries for `account_new_risk_paused`; that policy was
not introduced by hybrid. These facts narrow the investigation but do not
identify the missing exception behind the16:29 live cancellation wave.

Safe reuse: preserve exact-owner wakeups, ordered projection and harmless-ACK
handling. Do not copy old readiness assumptions, remove the pending-fill fence,
retry account reads for every market, restore$90 HARD or silently reintroduce
cash-based trading-loss calculation. Validate the candidate scheduling repair
against both the current REST baseline and the older event-driven behavior.

## Expanded investigation —16:52Z

No trading source, configuration or live process changed. Local-only streaming
log analysis and read-only SQLite queries compare bounded intervals, not matched
market cohorts or causal performance experiments.

### Wider evidence

Hybrid interval16:00:18–16:47Z:

- 114 CANCEL plans:43 price-left-touch,30 expected-net/quality removals,
  13 account-risk-pause,10 reducer replacements,9 building-side cancellations,
  7 hard-timeout cancellations,1 other-side removal and1 reward-ineligible.
  Plans are not all submissions; shutdown/recovery can also submit outside this
  action logger. There was one13-market account-risk cancellation wave.
- 257 durable local FAILED PLACE intents:245 entry intents and12 reducing/IOC
  intents. All have `private observation pending; request not sent`. Reducer
  diagnostic records identify9 passive-reducer failures and3 hard-timeout IOC
  failures. These are not venue rejects or additional HTTP429 responses.
- Salvea/Turrom had five consecutive reducer deferrals16:42:09–24Z. Replacement
  acknowledgement16:42:28.621Z was20.969762 seconds after positive cancellation
  confirmation. This is a concrete replacement gap, not the position's entire
 175-second age (which includes earlier working reducers).
- Closed API windows:8,496 admitted attempts/2,756s,7,839 reads,657 mutation
  attempts,7 real429 responses. The limiter increments attempts after admission
  but before final POST checks. Local hybrid refusals therefore consume local
  admission and enter the attempt/rps metric without sending a venue request.
  Do not call this an exact wire-request rate or subtract mismatched endpoints
  and interval boundaries to manufacture one. A diagnostic field initially
  queried nonexistent payload `timeInForce` and returned null; classification
  uses the executor's durable `quote_observation_required` flag and matched
  reducer logs instead.

Earlier REST interval05:11–15:45Z:

- 1,997 CANCEL plans, including485 account-risk-pause plans in35 time clusters
  (20-second separation rule; several clusters contain only one market).
- 10 local failed creates, all expired pregame evidence; no hybrid deferrals.
- Closed API windows recorded161 real429s. The sampled runtimes, durations,
  markets and inventory differ: none of this proves hybrid improved/worsened
  profitability, total API capacity or overall cancellation rate.

Thus account-risk-pause churn is demonstrated in the earlier REST run, not just
the current hybrid run. A rollback alone would not remove that mechanism.
Five-minute GTD expiry remains intentional host-outage protection; it and normal
touch/eligibility changes must not be mistaken for a new rotation policy.

### Additional controlled reproduction

The ignored fake-client diagnostic now passes4 tests, including one repeated
under both REST and hybrid with the production$30 settled-window policy.
It starts with fresh risk authority, injects one failed cash-consistency bracket
and walks13 flat owners. Exactly one refresh is attempted; the real wrapper
clears risk freshness, and the real planner produces13 account-pause cancels,
while loss cap remains30. This demonstrates the fanout mechanism in both modes.
It does NOT prove cash mismatch caused the actual16:29 wave: the live exception
was swallowed and there is no recorded blocker subtype to recover offline.
No live failure was induced; the first harness run lacked cloned market metadata,
which was corrected before the passing run.

### Current position check and priorities

Authenticated16:50:57.846Z:19 flat entries plus three exact reducers, covering
positions+6.9/-5/-2.89; zero unresolved/unknown state. The preceding16:50:22
sequential read saw no reducer on+6.9. Logs show reducer acknowledgement
16:50:18.072Z and the subsequent venue read confirms it. Read overlap and venue
publication timing cannot be distinguished from this evidence. Never infer a
persistent missing exit from that non-atomic first snapshot. Same
supervisor/runner remain live.

Repair priorities, still unimplemented:

1. Reducer/timed-exit continuity through hybrid notifications and proof waits.
   Process ordered observations before planning, distinguish neutral ACKs from
   quantity-changing evidence, and avoid repeatedly spending intents/admission
   on already-known proof waits. Replan quantities and retain final admission
   fences, exact REST proof and unknown-create nonretry.
2. Add bounded local risk-refresh failure and pause-blocker telemetry. Keep
   current fail-closed cancellation policy; do not keep entries live on unknown
   risk merely to improve resting time. The exact production trigger requires
   a naturally occurring event after reviewed instrumentation is activated.
3. Compare scheduling/read counts and reducer gaps against current REST and
   historical private behavior; expose admitted-versus-sent distinction if
   reporting rate improvements. No filter, stop-loss, expiry or pricing change.

Enough evidence exists to target hybrid continuity and missing diagnostics.
It is not yet an end-to-end verified fix or a complete attribution of the live
risk-bracket failure. Deployment/restart remains separately authorized.

## Authorized repair — prepared, not activated

The operator accepted the recommendation and explicitly requested that the
unexplained issue remain remembered on the watchlist. [WATCHLIST.md](../../WATCHLIST.md)
now carries the unresolved risk-snapshot trigger with evidence/closure criteria.
The separate300-candidate work remains paused and its worktree is untouched.

### Implementation and tradeoff

- Drain ordered private observations after awaited lifecycle service, immediately
  before constructing the decision frame. Neutral owned no-fill ACKs use the
  existing projector/classifier and add no REST call or new authority.
- Applied uncertainty defers a flat PLACE before a durable intent or mutation
  admission. Required cancels still reach the normal driver. This removes known
  unsendable attempts, not legitimate reprice/expiry/reward cancellations.
- An uncovered, unambiguous held owner with an applied hint obtains one complete
  exact-owner scoped REST proof before planning, unless its existing certificate
  is still current. It never loops proof within the decision, renews entry
  authority, changes polling cadence or reserves a hard-exit attempt while the
  proof is known unusable. A fill/event arriving during that proof still yields;
  source/scope/stop/flat transitions cannot become a new entry pass.
- Retain final POST pending-message and applied-hint fences. A notification that
  arrives during limiter wait can still produce a local unsent failure; this fix
  is not a promise of zero deferrals or zero429s. Never drain and silently reuse
  an already-sized mutation after that race.
- Shared certificate validation preserves exact quantity, canonical direction,
  scope/generation, original age, one-use intent and ambiguity requirements.
  Capture scope/generation before scoped reads as well as checking afterward.
  Ordinary stop yields before planning; shutdown's existing reducing authority
  is not disabled by the new scheduling helper.
- Bounded local risk diagnostics record refresh stage, safe reason/category,
  numeric HTTP status when available, previous snapshot age and pause blockers.
  No raw exception/server body, transfer description, credential or API call is
  added. Repeated signatures coalesce for60s; storage is bounded to32 keys.
  Diagnostic failures cannot change booleans or mask the original exception.
  Cash consistency, settled-window arithmetic and fail-closed entry cancellation
  remain unchanged. Transport, local deferral and HTTP429 stay distinct.

The scoped proof moves necessary reducer work ahead of the flat slate, and may
add a bounded read when genuinely new uncertainty arrives after prior proof.
Neutral ACK handling itself adds none. Controlled read-parity tests are evidence,
not a measured promise of lower total production REST load or better profit.

### Verification and dedicated edge review

First affected slice:137 tests passed in83.630s. Four in-memory reversals each
produced the intended assertion failure: late-ACK preparation, known-hint entry
deferral, preplan reducer proof and risk-failure logging. All patches restored.
The first late-ACK test assertion was incorrectly inside a production exception
handler; the reversal exposed that weak assertion. It now captures planner input
and asserts outside the callback, so the reversed fix fails decisively.

Reviewed success/failure, empty and full32-owner scope, quiet feeds/neutral ACKs,
task-held order, partial fills, stop/source generation, event-during-proof and
limiter races, unknown/absent ownership, one-use certificates and age boundaries,
position flattening, cancellation/reducer continuity, hard-exit attempt state,
API/read amplification, unchanged REST/private modes, logging redaction/failure,
restart and rollback. Existing end-to-end REST/hybrid comparison and continuity
tests retain their original read-count and safety assertions. No schema/config,
strategy/price/size/filter/cap/expiry or stop-loss policy change is included.

The first complete suite passed2,141 tests in223.932s. Final review expanded
redacted cash/transport classification and narrowed routine preplan proof-error
handling to AdapterError; unexpected programming errors retain escalation.
The final exact source passed all2,142 tests in222.090s using the production
virtual environment against an isolated native temporary checkout. The five
changed/new source/test files match the Windows working copy byte-for-byte:
account_source_runner `0d98ebc2a72c`, live_commissioning `8f3a030b3b05`,
risk_diagnostics `21f1b54bbebd`, test_hybrid_preplan `fc68f50ead66`, and
test_risk_diagnostics `e0cef8e45935` (SHA-256 prefixes). Documentation audit and
diff whitespace checks pass. No production files, runtime marker, exposure or
process ownership were changed. At17:19Z the same supervisor/runner retain
their16:00:18Z start. No new authenticated exposure read was added by this work;
the16:50:57Z snapshot above is historical, not a current account claim.

### Still OPEN — not solved by this repair

The exact exception that triggered the16:29 account-risk-pause cancellation wave
remains unknown. The earlier reproduction shows the mechanism in REST and hybrid,
not its production cause. After a separately reviewed/authorized activation,
correlate a natural recurrence with `[risk-diagnostic]` and cancellation timestamps.
Only positive cause evidence and verification of its remedy can close this item.
Do not relax risk checks, force a fault, start extra polling, or mark it resolved
merely because telemetry was added. Natural reducer-gap/API acceptance also remains
pending; the running bot still uses20c44a9d7.

## Command1 activation — September23

The operator authorized the complete reviewed stop/deploy/restart workflow.
Supervisor277537 was stopped first; runner277553 received SIGTERM and completed
ordinary cancel/flatten shutdown17:24:47.190Z with `flat=true`. Independent
authenticated gate17:25:08.212Z proved zero venue orders/positions, unresolved
intents, unknown orders, working ledger orders and ledger positions.

Pre-deploy parity matched Windows/GitHub038408240; the expected WSL source lag
was the sole mismatch. A stopped-ledger online recovery copy captured17:25:21Z
passed native integrity and destination hash/size verification:1,086,062,592 bytes,
SHA-256 prefix`e78299639381`. The offline/unconfigured peer was not contacted.

The redundant WSL HTTPS fetch stalled; only its known read-only fetch processes
were terminated. Deployment instead fetched the already-pushed verified commit
from the local Windows checkout, then fast-forwarded clean WSL main to
`038408240d7a3b77aaf123fed02facafd39b2265`. All235 source/test/config/script
artifacts match that commit and Windows after line-ending normalization.
The saved settings remain auto15/32/hybrid, and the current12–18 ET risk row
retains hard30/cap30, no trip and settled P&L+2.05910000 at17:24:42Z; no baseline
or ratchet was reset.

The actual installed checkout passed all2,142 tests in216.336s. Only afterward
was DEPLOYED_COMMIT set to038408240. Fresh authenticated17:31:41.589Z gate
again proved all six exposure/ownership counts zero. Startup used the preserved
`./scripts/start_commissioning.sh auto 15 32 hybrid`; supervisor326083 and
runner326105 started17:31:42Z. Initial13 public books used zero REST fallbacks.
First entry action occurred17:32:32.885Z. Current12–18ET hard30/cap30 and
untripped settled-P&L row were retained, not reset.

### Initial live acceptance and lower-order question

Authenticated17:36:08Z proved19 flat entries plus one exactSELL15 reducer.
Authenticated17:39:09Z proved15 flat entries plus three exact reducers: BUY4
against -4 Orlpet/Bridav, SELL15 against +15 Iskchr/Otgand, and SELL15 against
+15 Konrom/Voryar. All three were OPEN for the exact remaining quantities;
zero unresolved intents/unknown orders, ledger18 working/three positions agreed.
The bot remained live. This is timestamped exposure, not a permanent guarantee.

The user observed about20 orders versus about30 earlier. Cap30 remains unchanged;
the17:38:58Z durable row showed settled P&L+2.15910000 and no trip. Initial scope
was13 markets;17:35:43Z lifecycle replacement released one expired market and
added three, giving15 active owners. The17:37:32Z fresh selection reported14
ranked books/eight viable candidates. Not every selected owner permits both
sides under unchanged economic/quality gates, and fills replace building orders
with reducers. Final-fence local admission delays also affect refill, so this is
not proof that market supply alone explains every missing order. No20-order
configuration or filter change was made.

First six completed API windows through17:38:58Z recorded1,278 attempts over
429.3s (~2.977/s), two execution_history429s and remaining order-retrieve404s;
following windows recovered with no active endpoint cooldown. Attempts include
local no-send admission refusals and are not exact wire-request counts. Local
forensics found35 unsent entry intents, all private-observation-pending, and no
reducer failure. Twenty-seven cancel plans comprised22 touch reprices, two
expected-net refusals, two reducer reprices and one post-fill builder cancel.
No account-risk-pause cancellation wave or runtime recovery fault was observed.
Thus rate-limit elimination and sustained performance parity are NOT accepted.

Konrom/Voryar's first position observation17:33:59.635Z already had an opposite
working order; do not count the whole interval to replacement as uncovered.
Reducer cancel terminal confirmation17:34:20.209Z to new acknowledgement
17:34:21.692Z was1.482792s. Three covered natural positions accept initial
reducer handling, not hard-timeout/final-flat behavior or long-run profitability.

The new risk diagnostic recorded17:36:56.890Z refresh_failed at final_balance,
reason=cash_changed_history, category=adapter, previous snapshot age9.367s.
There was no corresponding cancellation wave; a later risk row refreshed.
This confirms the diagnostics work and identifies that particular consistency
failure, NOT the unlogged16:29 trigger. That original incident remains OPEN.
No additional production code/policy change was made during acceptance.

## REST comparison follow-up,17:51Z — investigation paused by operator

Read-only process checks found supervisor326083/runner326105 absent. Log ends
with a separate startup_recovery_only run17:48:07–17:49:14Z, flat=true.
This diagnostic did not stop/start either process. The earlier run's termination
cause and the recovery launch source were not established; do not attribute
the stop to429. Authenticated17:51:49Z proves zero venue orders/positions,
unresolved intents, unknown orders, working ledger orders and ledger positions.
Operator then deferred investigation; leave stopped, no restart authorized.

Only completed, wholly contained API windows were compared, with weighted
counts/elapsed seconds (not an average of rounded window rates):

| Sample UTC | Seconds | Attempts/s | Reads/s | HTTP429 |
|---|---:|---:|---:|---:|
| Stable REST14:30–15:30 |3550.9|2.9314|2.7785|15|
| Prior hybrid first15min16:00:18–16:15:18 |861.4|2.9255|2.7235|1|
| Prior hybrid17:08–17:23 |836.2|3.0352|2.8067|2|
| Repaired hybrid first15min17:31:42–17:46:42 |848.9|3.0522|2.8248|2|

No aggregate REST saving is demonstrated: current first15min reads/s is about
3.7% above the earlier hybrid startup and0.6% above its final comparison slice.
Different markets/fills/startup work prevent causal attribution to the repair.
Whole repaired-run completed windows through17:46:52 cover909.3s:2,790 attempts,
2,575 reads,215 mutation attempts, two execution_history429s and17 exact-order404s.
Those429s were in windows ending17:34:26Z and17:36:46Z; later completed windows
had none. Mutation attempts include unsent local refusals; these are runner-local
counts, not exact host-wide wire traffic or proof of an endpoint quota.

Current first15min read rates: exact order0.6785/s, execution history0.4229/s,
open orders0.4088/s, positions0.4088/s, balances0.3805/s and cash history0.2521/s.
Balance+cash+execution history together remain1.0555/s (~37% of reads).
Public REST book reads were zero in all comparison samples. Private assists
were active (last196 order observations, six position hints,107 ACK-only,
13 urgent,82 cadence and two scoped admissions); this is not a silent-feed
fallback diagnosis. Both cash and execution categories map to the same
GET /v1/portfolio/activities endpoint, which is useful for the next load review.

Next: verify run boundaries/stop provenance, compare workload-matched useful
resting coverage and reducer timing, then trace duplicate account/history proof
consumers before proposing a safe reduction. Do not weaken exit or risk proof,
change caps, activate300 coverage, or restart. Local comparison helper is ignored;
no production source/config change. Current changelog was re-opened (latest
September21 crypto documentation); live status page retrieval failed, so no
fresh status/outage claim follows.

### Stop investigation prioritized over the deferred REST comparison

Clarification of the operator steer: investigate the stopped bot now; return
to REST performance later. Read-only root system journal establishes the WSL
distro shutdown at17:47:52Z (`System is powering down`, session1217 stopped),
followed by distro service startup17:48:04Z. This explains disappearance of both
trader and supervisor; the original normal runner had no terminal result.
The shared VM kernel uptime staying seven days does not disprove a distro restart.
No reviewed kernel OOM event or bot traceback identifies a429-caused death.

Persisted host-recovery state reports one attempt and status=recovered, matching
Windows watchdog status. Existing code only launches recovery after both normal
processes disappear, and intentionally remains paused after positive flatness.
Recovery17:48:07–17:49:14Z canceled exposure and flattened three positions.
Three local unsent shutdown deferrals retried via positive proof; final account
and ledger flatness were independently verified17:51:49Z. No action in this
diagnostic stopped, started or modified the trader or watchdog.

Windows System events record a Codex Store update17:47:22–25Z, before the distro
shutdown. This is timing correlation, not proof the app update initiated WSL
termination. The precise initiating actor remains unknown. Further host
lifecycle investigation is the recommendation before another normal restart;
no launcher/service/power policy change or restart was authorized/performed.

## Operator-authorized unchanged restart17:58:30Z

The operator subsequently explicitly requested restart. No production code,
launcher, host settings or business policy was changed. Revalidated all235
runtime/workstation source/test/config/script hashes against038408240; reused
the same release's already completed2,142-test installed suite rather than
repeating it. The temporary test log did not survive the distro shutdown; its
completed result remains in the prior verified tool evidence and release record.
Parity passed at b8cd031e3 with loaded038408240; stopped-ledger backup17:57:39Z
was verified,1,087,430,656 bytes/SHA prefix4902c66bfa25. Peer not contacted.

No old supervisor/trader remained. Fresh authenticated17:58:23.823Z gate proved
zero venue orders/positions, unresolved/unknown intents/orders, working ledger
orders and ledger positions. Launched unchanged auto15/32/hybrid at17:58:30Z,
supervisor768/runner782, independent local dashboard808. The12–18ET hard30/cap30
row retained settled P&L+0.93910000 after recovery, not a baseline reset.
Public book collections used17/17 then12/12 stream hits, zero REST fallback.
First entry plan17:59:24.792Z. Authenticated17:59:43.127Z shows six flat entries,
zero positions/unknown orders and one in-flight intent; sequential live ledger
read had seven working orders, not an atomic venue/ledger equality proof.
Initial placement acceptance is not sustained performance/zero429 acceptance.
REST performance review remains deferred; host shutdown initiator remains open.

First completed90.8s API window at18:00:02Z:219 attempts/201 reads/18 mutation
attempts,2.412/s, one execution_history429, no active cooldown at window end.
The18:00:21Z risk row remained+0.93910000/cap30/untripped. Local intent review
showed15 reconciled creates/four reconciled cancels/four unsent entry deferrals,
no reducer failure, risk-cancellation wave or runtime recovery. The previously
sampled in-flight create was no longer unresolved; no position had occurred.
This restart does not resolve or eliminate the deferred history429 issue.

## Resumed REST diagnosis and reported order drop18:07Z

No source/config/strategy change, deployment or restart. Pre-work parity passed
503299f36/loaded038408240 with verified private backup18:03:08Z; peer not contacted.
Same supervisor768/runner782 remained running. Authenticated18:07:06.598Z:
eight orders = seven flat-market entries plus one BUY4.8 OPEN reducer covering
the sole-4.8000 Steyar/Pitser position; zero unresolved/unknown ledger state.
Local18:07:17Z risk row: settled+1.04910000, cap30, untripped. No account-risk
cancellation wave or reducer failure in this run through that cutoff.

Read-only order/intent join at18:08:05Z found18 EXPIRED,18 CANCELED,2 FILLED,
19 OPEN for this run's creates (local evidence, not a second venue snapshot).
Each expired order lasted300.001–300.003s. First startup cohort expired from
18:04:24 through18:04:47Z; another cohort18:06:27–18:06:40Z. Thus the drop is
not a new20-order cap or a risk-loss reduction: five-minute GTD expiry is
removing entries while refill is delayed. Recorded20 failed entry intents by
18:07:17Z were all `ApiAdmissionDeferred: private observation pending; request
not sent`; later deferrals continued through18:07:59Z. These are local safety
refusals, not venue create429s. The account-wide one-second entry cooldown
following one refusal also skips later plans without creating more intents.
The final POST guard can see either unapplied private queue data or a pending
REST reconciliation latch; current generic error does not distinguish them.
Do not remove the final guard or assume every event was a harmless ACK.

Completed telemetry519.1s:1,369 attempts/1,286 reads/83 mutation attempts/four
read429s, approximately2.64 attempts/s and2.48 reads/s. Includes locally unsent
mutation attempts, excludes other processes, and is not a controlled comparison.
The18:07:10Z window had zero failures/cooldowns. Local one-second entry deferrals
are not themselves evidence of new429s. Existing cancels were ten price-touch
changes, five reducer reprices, two economic changes and one builder after fill.
Sole reducer replacement18:06:14.218Z terminal proof to18:06:15.704Z ACK took
1.485s; broader position/exit performance remains observational.

REST trace confirms complete order lists, position watch, exact owner repairs,
execution history and risk cash/balance brackets remain consumers. Cash and
trade history share `/v1/portfolio/activities` and its existing shared cooldown.
Flat history overlap optimization, duplicate periodic-balance coalescing and
bulk order proof already exist; proposing them again would not be a new saving.
Prior single-list optimization failed race tests; keep two-list proof bracket.
Recommendation: prioritize expiry/refill event-drain/admission scheduling,
then measured duplicate proof work, preserving business rules and final safety
fences. User requested diagnosis; implementation remains a separate decision.

## Extended REST comparison through18:20Z and new risk-wave evidence

Operator requested more investigation using the new logs. Same process/source,
no changes to runtime, strategy, expiry, risk or restart. Pre-work parity76e2da6ee
passed; private backup18:17:21.711Z,1,088,598,016 bytes/SHA prefix4e87c3a5a76d.
Offline comparisons retain only wholly contained completed API windows and use
sum(reads)/sum(seconds), not average of rounded per-window rates.

| Sample UTC September23 | Completed seconds | Reads/s | Attempts/s | HTTP429 |
|---|---:|---:|---:|---:|
| Stable REST14:30–15:30 | 3550.9 | 2.7785 | 2.9314 | 15 |
| Earlier hybrid16:00:18–16:15:18 | 861.4 | 2.7235 | 2.9255 | 1 |
| Repaired hybrid17:31:42–17:46:42 | 848.9 | 2.8248 | 3.0522 | 2 |
| Same release restarted17:58:30–18:20:00 | 1272.9 | 2.7897 | 2.9735 | 8 |
| Latest subinterval18:10–18:20 | 563.5 | 2.9849 | 3.1819 | 1 |

The full restarted sample contains3,551 reads/234 mutation attempts; aggregate
read rate is effectively unchanged versus stable, not a proven reduction. The
latest subinterval is about7.4% higher, descriptive only: markets, fills,
positions, scan/recovery load and entry count differ. Full requested interval
workloads (not exactly the cropped API-window duration): stable352 reconciled
creates/195 cancels/31 fill rows in60min; restarted130 creates/39 cancels/17 fills
in21.5min plus70 locally unsent entry deferrals. This is not a controlled A/B.
Attempt telemetry includes local no-send mutations and excludes separate
processes; these are runner-local read/attempt rates, not exact IP-wide wire RPS.

Latest10-minute exact-order reads0.8696/s versus stable0.6097/s (about43% higher),
the largest upward category. Latest balances0.3727,cash history0.2431,trade
history0.3833,open orders0.3709,positions0.4277/s. Public book reads remain zero.
Other market/reward metadata reads still exist despite WS books. Repeated
`rest-order-proof` bulk skips prove the shortcut executes, not that hybrid alone
created its savings: stable REST also includes that optimization. No per-call
reason counter currently separates bulk disabled, positioned owner, absent row,
changed timestamp/economics and exact-repair callers, so attribution remains
incomplete. Do not infer every exact read is redundant or remove proof guards.

Current eight429s:execution_history4,cash_history2,balance1,positions1. Thus the
earlier user-facing history/balance summary was incomplete: positions also
throttled. Cash/trade history share the same activities endpoint/cooldown.
Last eight completed windows through18:19:44Z had zero429s. This does not prove
the venue's trigger or an absent future throttle. Official changelog re-opened:
latest relevant published entry remains September21 crypto documentation, not
a new account rate policy. Live status fetch failed; no operational-status claim.

At18:19:24Z two report-lane reads proved26 entries plus three exactly covered
positions. The first eligibility diagnostic omitted the production client-ID
resolver and falsely reported field mismatch for every owned row; discard that
eligibility result. Corrected read-only identity lookup, no adoption/writes:
18:20:21Z venue12 entries plus two SELL15 reducers covering both+15 positions,
zero unknown/unresolved. Nine of14 snapshot rows met ordinary bulk conditions;
two positioned owners and three no-longer-working local rows account for the
remaining non-overlapping reasons in this sequential sample. No timestamp lag.
It cannot establish historical fallback causes or atomic venue/ledger equality.
Only these four report-lane requests were added; no stress/probe loop.

New diagnostics capture a distinct later cancellation mechanism:
-18:19:50.929578Z a0.01 BUY fill; recorded18:19:50.996976Z.
-18:19:50.997928Z risk refresh fails at equity_snapshot with
 `cash_changed_positions`, previous mark age5.780s (not an HTTP error).
-18:19:53.634Z another14.99 BUY and18:19:56.622Z SELL15 complete the episode.
-18:19:57.144Z entry gate reports equity_unknown.
-18:19:59.518–18:20:17.687Z entry-cancellation plans spread across15 markets.
-The18:20:44 API window records no failures/429s. Fresh local risk18:20:47Z
 remains settled+1.32510000, cap30, untripped; later entry plans resume.

The rejected cash bracket is a valid protection against inconsistent snapshots;
the opportunity is recovery/scheduling before repeated cancellation/refill, not
accepting inconsistent cash or changing settled-loss rules. Timing strongly
supports concurrent trading as this bracket's cause; raw balance values were
not retained. This identifies THIS wave, not the still-uninstrumented original
16:29 exception. Preserve that original watchlist uncertainty separately.
Recommendation: investigate bounded consistent-snapshot recovery and serial
entry/refill scheduling first, then exact-read attribution. Any implementation
needs authority, focused race/failure tests and unchanged fail-closed gates.

## Reviewed repair awaiting deployment

The operator authorized the scoped fix and a gated restart. The implementation
does not change selection, pricing, quote size, slot count, five-minute GTD,
settled-fill risk, loss tiers, reducers, exits or the four $30 windows.

- Cash movement between the account bracket's balance/position/history reads is
  now a typed consistency race. The runner retries the *entire* bracket once
  with a fresh timestamp. No rejected candidate is published. A second race,
  HTTP/backpressure error, malformed response, stale mark, ET boundary or any
  other failure still withdraws entry authority exactly as before.
- A private observation arriving after planning still fails the final no-I/O
  POST fence. That local failure no longer opens the account-wide one-second
  entry cooldown; only its market takes the existing recovery delay while the
  next pass drains/classifies the event. Real fill/quantity uncertainty still
  requires REST proof before any new POST.
- Existing complete two-list bulk proof remains unchanged. Exact reads for
  positioned, absent, changed, ambiguous or recovery owners were not removed;
  current evidence cannot prove them redundant. The cash retry can add one
  bounded full bracket only during a real consistency race, while avoiding the
  measured broad cancel/refill fanout.

Focused tests cover typed bracket rejection, one-successful retry, second-race
fail-closed behavior, ordinary429 failure behavior, normal API cooldowns,
operator interruption and hybrid local-only deferral. Deliberately removing
each behavior made its regression fail; restoration passed. The exact WSL
production environment then passed all2,145 tests in378.696 seconds. Final diff
review found no business-policy drift, ownership relaxation, mutation retry,
credential exposure or rollback dependency. Deployment/live acceptance remains
subject to the normal authenticated flat/clear gates.

## Restart gate blocked by live exposure

The reviewed repair was committed and pushed as `220976c3f`. At18:50Z the old
runner reproduced the shared hybrid cooldown across several markets, then the
authorized workflow gracefully stopped supervisor768/runner782. Shutdown
cancelled flat entries but correctly parked three pregame positions behind
reducers instead of crossing them. The18:51:23Z mandatory gate refused restart.
A fresh18:52:34Z authenticated read still showed positions+11,+15 and-4, each
with exactly one opposite-side OPEN reducer for11,15 and4 respectively; venue
orders3, unresolved intents0, unknown orders0, ledger working3 and ledger
positions3. The repair remains undeployed and loaded source remains038408240.
No reducer was cancelled and no loss-making close was authorized. Deployment
and restart require natural/manual flatness or explicit authority for the
existing price-capped close procedure, followed by a new flat/clear gate.

## Deployment and live acceptance

The operator manually flattened the three parked positions and requested that
the reviewed release be completed and left running. At18:56:46Z a fresh venue
read proved zero orders and positions, but the stopped ledger still contained
the three old owners. The existing stopped reconciliation made zero venue
mutations, obtained exact terminal confirmation for all three, and cleared the
ledger. The mandatory18:57:15Z gate then passed with zero venue orders,
positions, unresolved intents, unknown orders, working ledger orders and ledger
positions.

WSL fast-forwarded to `321728fc9`, which contains repair `220976c3f` plus its
documentation. The exact installed environment passed all2,145 tests in
211.744 seconds. All235 deployed source/test/config/script hashes matched;
workstation/GitHub/WSL/loaded-marker parity and the private stopped-ledger
recovery copy passed. A second authenticated gate at19:06:28Z was flat and
clear. Auto size15/32 hybrid started as supervisor7592/runner7606. The loaded
12–18ET row proved loss0, hard30, cap30, held=false and
`basis=matched_fills`; open orders and positions remain excluded.

Five complete API windows during the first7 minutes recorded1,213 admitted
attempts at2.431–3.624/s. Four genuine account-read429s occurred: one positions,
one balance and two cash-history. They correctly expired equity authority,
cancelled18 flat entries fail-closed and then refilled. The next two complete
windows had zero failures/429s; a later window had only one ordinary
order-retrieve404. This release does not claim that Polymarket's low-rate429s
are eliminated.

The targeted hybrid behavior did improve: from the startup marker through the
acceptance cutoff there were zero `mutation-access-backoff` lines and no logged
`LOCAL_HYBRID_OBSERVATION_PENDING` fanout. A natural+0.47 position obtained its
exact reducer after cancel-confirm-replace and later flattened. A natural-13
position obtained an exact BUY13 reducer. Another transient-1 position was
observed while its builder and wrong-size reducer were being cancelled; the
exact BUY1 reducer was acknowledged13.43 seconds after the position observation
and the position subsequently flattened. No reducer or fill was induced.

Authenticated19:13:48Z acceptance showed32 venue orders across22 markets:31
flat-market entries plus one OPEN BUY13 reducer for the sole-13 position. There
were zero unresolved intents and zero unknown orders; ledger working/position
state agreed. The bot remained running. Natural repeat observation remains
required for cash-consistency retry, endpoint429 frequency and reducer timing;
the original16:29 trigger and the WSL shutdown initiator remain open watchlist
items. The separate300-candidate scanner measurement remains paused.
