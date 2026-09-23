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
