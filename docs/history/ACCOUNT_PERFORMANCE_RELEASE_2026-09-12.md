# September 12 account-source/performance release attempt

## Authority and reviewed package

The operator authorized loading today's prepared changes and checking live
readiness, superseding the earlier deployment/restart hold. After shutdown
started, the operator reported manually closing exposure. Keep REST selected,
size 10 / 32, max 30 flat pending entries with position/reducer exclusions,
the shared 4/s governor and every existing strategy/risk rule.

Reviewed package: `8ab1738bf`, including the whole-bot R1–R5 repairs,
final-entry readiness, ordered identity-lookup index, interruptible candidate
scans, single-flight position wakeup, serial identity projection and isolated
execution timing. Existing exact-source evidence is reused: 1,525 passing tests,
restored deliberate reversals and dedicated edge-case review. The combined
source/script diff against loaded `b826ff11e` was reviewed again before release;
No configuration/risk change was introduced. Two additional release-blocking
issues found during shutdown are addressed below.

## Shutdown and gates

- Supervisor 649355 stopped and runner 649372 received graceful SIGTERM around
  15:34:56Z. No force-kill, source switch or direct venue mutation was issued.
  Normal runner shutdown owns cancellations/exits.
- Parity found clean Windows/GitHub `8ab1738bf`, WSL `f9f9d8c4` and loaded
  `b826ff11e`. The private online backup at 15:35:04.618045Z was verified:
  368,947,200 bytes, SHA-256 prefix `06596de350d7`. The desktop peer is offline.
- Authenticated 15:36:19Z observation: zero orders, +10 `stolud-lopvla` and
  +10 `chlpaq-marcap` positions. At 15:37:12Z the strict start gate refused:
  venue zero orders/two positions; ledger zero unresolved/unknown/working but
  three nonzero positions.
- After the operator's manual closure, 15:38:09Z authenticated proof showed
  zero venue orders/positions, zero unresolved/unknown/working ledger state,
  but three stale ledger positions. Restart remains refused until normal
  reconciliation proves the full known state and the old owner is absent.
- The old runner repeatedly logged `GET /v1/portfolio/activities`
  `APITimeoutError` before and during shutdown. Its shutdown IOC plans for the
  final two owners were refused locally with `ApiAdmissionDeferred`; a logged
  plan is not a submitted or executed exit. No missing history is bypassed.
- The official [changelog](https://docs.polymarket.us/changelog) was reopened;
  its latest entry remains the September 11 04:00–07:00 ET maintenance notice.
  The [status page](https://status.polymarketexchange.com/) fetch failed. This
  does not establish a currently declared outage or maintenance.

## Additional release repairs and dedicated edge-case audit

Direct, bounded read-only tests isolated a request-shape difference, rather
than proving all REST down:

| Request | UTC start | Result |
|---|---|---|
| Unfiltered limit 100, descending | 15:38:51Z | APITimeoutError, 15.152s |
| Unfiltered limit 20, descending | 15:39:06Z | APITimeoutError, 15.147s |
| Unfiltered limit 20, default sort | 15:40:15Z | APITimeoutError, 15.154s |
| Trade-only limit 20 | 15:40:30Z | 20 rows/continuation, 0.207s |
| Trade-only limit 100, descending | 15:43:22Z | 100 rows/continuation, 0.284s |

The Retail [activities contract](https://docs.polymarket.us/api-reference/portfolio/get-activities)
documents the type filter, descending order, cursor and EOF. Execution-history
collection already ignores non-trade records: it now requests
`types=[ACTIVITY_TYPE_TRADE]` on every page. No cash/settlement/account-mark
reader is changed. Trade lifecycle including BUSTED, exact account-leg choice,
deduplication, complete required coverage and immutable conflict handling remain.
The normal prepared parser, with read-only durable anchors, proved two-page
coverage over 200 executions and 200 trade states at 15:45:41Z in 4.253s,
including three new execution IDs. It used the production shared report lane
and made no ledger write. An earlier isolated-source probe refused before any
request because its default credential path differed; explicit production file
and limiter paths corrected that diagnostic without copying/exposing secrets.

Shutdown had incremented the per-owner IOC counter before admission and kept
the charge when exact local NOT-SENT refusal occurred. Only
`ApiAdmissionDeferred` now refunds that attempt. Every sent, acknowledged,
uncertain, rejected or other failure retains the prior conservative accounting;
the overall shutdown deadline, backoff, three real attempts, exact quantity,
ownership and fresh proof are unchanged. Nothing retries an unknown create.

Focused regressions passed, then both repairs were deliberately reversed:
the filter assertion errored and the shutdown test showed only three local
refusals with no remaining send capacity. Both were restored. The initial
1,527-test full run exposed six failures from one existing fake portfolio
assuming every type-filtered request was cash-only. Its response now includes
trade records when the documented trade type is requested; all existing loss
and account-leg assertions are preserved. The corrected full suite passed
1,527 tests in 143.799s. Four additional shared lifecycle replays then passed
in 1.015s across strict REST, optimized REST, permanent REST and permanent
WebSocket. The final combined suite is recorded below.

- Empty/full/page boundaries: empty EOF remains no synthetic fill; an empty
  non-EOF first page continues; filters/cursors persist through every page.
  Complete history coverage, duplicate/fill conflicts and BUSTED remain tested.
- Timing and load: no new endpoint, faster cadence, timeout change, independent
  limiter or added request. Trade-only pages can improve useful coverage without
  accepting partial evidence. A locally deferred IOC waits normal backoff and
  a new full reconciliation; it cannot reset the overall shutdown clock.
- Ownership and uncertainty: exact local refusal yields a FAILED durable
  intent; four local refusals still permit only three real IOCs in the test.
  Sent/unknown outcomes never refund, reuse a client ID or trigger blind retry.
- Recovery, shutdown, restart and rollback: one serial owner remains; no live
  patch or process replacement before stopped proof. Source modes share these
  repairs; no auto-promotion or persisted risk/position-clock reset. Old source
  remains schema-compatible but restores these defects and is not a safe
  automatic rollback.
- Business policy: entry/reward/price/size, exact exits, HARD and pending-order
  exclusions are unchanged. External history/stream or liquidity failure can
  still block safe operation. Final diff/secret/whitespace checks required.

## Release status

Final combined isolated suite: **1,531 tests passed in 141.495s**. All 185
source/test/script/web/config normalized hashes match the tested copy. Final
source/test diff, dedicated edge-case, whitespace and documentation audit passed.

The old runner eventually completed its original unfiltered reconciliation and
exited **15:50:06.469082Z, flat=true**, without force-kill or ledger repair.
Thus the history symptom was intermittent/query-dependent, not proof of a
permanent endpoint outage. The 15:51:02.728903Z authenticated gate independently
proved zero venue orders/positions and zero ledger unresolved/unknown/working/
nonzero-position state; runner and supervisor were absent. A stopped online
backup at 15:51:21.598339Z passed integrity, 368,975,872 bytes, SHA-256
`79de0c0dae19878ed89a984bdd2a931232afc2e82d75a325c8bfca3ae0f26d89`.

Push/parity, exact deployed hashes/tests and a fresh start gate remain before
the authorized REST restart. Natural position/exit/API observation is required;
offline passing tests do not establish WebSocket-equivalent live latency.

## Deployment and bounded live acceptance

Release **`908ba6dd6164302b32fe754a05a54ff300628a56`** was pushed, then the clean
WSL checkout fast-forwarded through a verified local Git bundle. All 185
source/test/script/web/config normalized hashes matched; shell syntax and CLI
smoke checks passed. `DEPLOYED_COMMIT` names that release. The exact production
checkout passed **1,531 tests in 140.271s** before startup.

The independent 15:56:27.963118Z start gate proved venue and ledger flat,
order-free and clear. Runner **1173864**, supervisor **1173849**, started at
15:56:29.162408Z, explicit REST size 10 / 32. Saved-source status confirmed
REST active, generation zero, no pending switch. The new lookup index was
positively present. At 15:59:44–15:59:57Z authenticated acceptance found
**30 flat pending entries, zero positions and zero unresolved/unknown state**.

| Window end UTC | Seconds | Attempts / mutations | Requests/s | Failures | 429 | Max queued | Wait p99 / max seconds |
|---|---:|---|---:|---|---:|---:|---|
| 15:59:14 | 165.0 | 480 / 27 | 2.908 | 7 exact-order 404s | 0 | 3 | 0.691 / 0.721 |
| 16:00:25 | 71.0 | 270 / 13 | 3.805 | 0 | 0 | 4 | 0.940 / 1.192 |

Both windows ended with zero queued requests and no cooldown. The first had
34 execution-history calls and the second 26, without history-query failures.
One initial and three later exact event-pregame availability refusals were
contained; unknown event evidence did not authorize entries. The later three
recovered eligibility at their next observation. No runtime exit, position
transition or natural reducer episode occurred in the observed sample.

The scheduler windows measured 0.060/0.063 CPU cores, event-loop-delay p95
0.0003s and action-latency p95 0.625/0.576s. These are not venue-fill-to-exit
measurements or a like-for-like comparison to the earlier CPU-bound recovery.
Decision-input-age p95 remained 74.130/67.436s. No WebSocket-equivalence or
32-market performance sign-off follows from the lower CPU sample.

### Acceptance stopped on the existing capacity guard

The existing 32-market canary rule refuses two consecutive `max_waiting > 1`
windows. Peaks 3 then 4 crossed that criterion, despite no 429 and short,
drained queues. The operator requested unchanged risk/32-market settings for
this release, so the AI safely stopped this run and asked whether to retain
those settings while addressing performance or use the existing 24-market
fallback (which also scales HARD to $67.50). No answer is inferred; no rate,
concurrency, guard, capacity or HARD change was made.

Peak queue count includes concurrent read admission and does not itself prove
persistent overload. The permanent source uses bounded concurrent reads, so
their contribution must be separated from breadth/backlog before proposing a
new acceptance rule; the old guard must not be silently waived. A 24-market
fallback reduces breadth but is not promised to eliminate concurrent-read
queue peaks. Natural reducer/IOC acceptance also remains unproven.

Supervisor 1173849 was stopped and runner 1173864 gracefully signalled. It
cancelled its owned orders and exited **16:02:17.701982Z, flat=true**, after a
5:48.5 run. The **16:02:41.668392Z** authenticated post-stop gate proved zero
venue orders/positions and zero unresolved/unknown/working/nonzero ledger
state. No force-kill, manual ledger repair, automatic source switch or risk
change occurred. The reviewed release remains installed, but trading is
stopped pending the operator's next choice; this is not full live acceptance.

## Conditional restart recheck

The operator subsequently explicitly retained $90 HARD and requested restart
only if all clear. No 24-market/$67.50 fallback is authorized. At
**16:11:55.381333Z**, authenticated orders/positions and all four ledger gate
counts were zero. Neither runner nor supervisor was present; WSL HEAD and
deployed marker remained `908ba6dd6`. No source/configuration change or restart
was made on this recheck. The consecutive queue-peak criterion is explicit in
the original 32-slot canary document and has not been resolved or waived.
Flatness alone cannot clear that acceptance blocker. Preserve size 10 / 32
and $90 HARD while investigating queue attribution; do not substitute a
lower-risk capacity fallback. Initial parity reported only the pending handoff
documentation as local drift; the private ledger copy was verified and the
desktop peer remained offline/pending.

## Queue review after the operator requested remaining fixes

The operator then requested fixing all remaining in-scope issues so the bot
can restart, explicitly retaining $90 HARD. No strategy, capacity, limiter,
poll cadence or mutation change is needed to reproduce the queue warning:
`AsyncIntervalLimiter.wait` counts every caller entering admission, including
intentionally concurrent reads. The configured exact-order reader has four
workers; position/history collection overlaps two read chains. An offline
regression using the real 4/s limiter starts four readers in two successive
rounds. Both rounds produce `max_waiting=4`, `waiting_now=0`, four reads,
zero mutations and zero failures/429, with **no network or credentials**.
Thus two queue peaks above one do not by themselves establish overload.
The AI's earlier stop followed the literal old criterion, but its interpretation
as a demonstrated runtime capacity failure was too strong. No fill/exit failure
occurred in that flat trial. This correction must not claim full performance
acceptance or erase the documented criterion without operator agreement.

A concurrency-aware check was proposed through the operator question: retain
real wait/stall, rate-limit, account-proof and safety-deadline checks rather
than use peak alone. No reply is inferred. Keep the bot stopped until that
acceptance decision is resolved and fresh normal start gates pass. Do not
serialize all reads merely to make the old statistic smaller, hide waiters
outside the measured queue, raise API rates or lower HARD.

The same review found a real **read-only diagnostic defect**: the capacity
model omitted `event_contract_batch`, which the live lifecycle guard emits.
Consequently the report rejected current complete telemetry as unclassified.
The model now conservatively width-scales these calls; they use up to 100
parents per batch, not the unrelated eight-owner contract-batch factor.
Distinct parent occupancy is not logged, so no precise savings are claimed.
Unknown future categories and category/attempt disagreement still fail closed.
The report and limiter docstring now explain the queue-count semantics.
No live planner, source, risk, scheduling, limiter behavior or execution path
changes. The new regression links the actual emitted category to the model,
checks projection totals and verifies future unknown categories still refuse.
Removing the category deliberately fails with the original unclassified error;
the repair was restored. All 21 focused capacity tests pass.

The repaired read-only report consumed the last two real 32-market windows:
236 seconds, 750 attempts, seven initial exact-order 404s, zero 429s.
Using rounded logged elapsed seconds yields 3.803/s maximum (the runtime's
unrounded rate was 3.805/s). Its verdict is still **CANARY ONLY**, not durable
capacity sign-off: above the existing 3/s comfort budget, below the shared
4/s gate, with 3.972/s modeled coincident report work. It now includes all
56 event-lifecycle calls. No rate/comfort threshold was loosened.

Dedicated edge review: this is pure evidence parsing/projection, with no API,
ledger or mutation path. Empty windows and unknown categories retain refusal;
existing fixed/eight-owner categories and totals remain unchanged. The source
docstring changes no limiter counter, lock, admission, cancellation or retry.
The new queue reproduction uses synthetic reads only, no failure induction
against production. Rollback changes diagnostic coverage, not runtime state,
and cannot reset ownership, HARD, position age or any source generation.

Final combined isolated suite: **1,533 tests passed in 144.084s**, with all
185 normalized reviewed file hashes matching the isolated copy. The earlier
queue-only addition passed 1,532 tests in 141.981s. Final diff, dedicated edge
review, documentation audit and whitespace checks passed. This follow-up fixes
diagnostic coverage and interpretation only, not live execution behavior.

The reviewed follow-up was pushed as **`2f7ba0222499124286ec0c631297d5d48012cfb6`**
and deployed while the bot remained stopped. WSL HEAD/marker matched; all 185
normalized hashes matched; the exact production checkout passed **1,533 tests
in 142.559s**. Saved quantity/width remain 10/32 and the configuration derives
exactly $90 HARD. The **16:27:30.969202Z** authenticated gate again proved zero
venue orders/positions and zero unresolved/unknown/working/nonzero ledger state.
No runner/supervisor was present. No restart or venue mutation was made in
this follow-up. Operator approval of a concurrency-aware acceptance criterion
has not been received; the old peak-only criterion was not silently removed.
