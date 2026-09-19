# Exit scheduling review — September18, 2026 ET

Scope: operator requested review, not implementation. Trading source, settings,
orders and processes were not changed. Live source remains `aae337658`.

## Confirmed findings

1. **High priority — building-only orders evade urgent inventory coverage.**
   `rest_fallback.py:_position_priority_must_yield` checks whether *any*
   working order exists, not whether an exact reducer exists. A long position
   with only a BUY entry and a future deadline returns false. The ordinary
   reconciler still cancels that builder when visited; the defect is loss of
   urgency before periodic work. `_position_near_its_flatten_deadline` contains
   the same any-order shortcut. Wrong-side/undersized orders merit coverage
   tests in the repair, not an assumption that all working orders protect risk.
2. **High priority — covered positions can precede new uncovered fills.**
   `live_commissioning.py:_decision_sweep` considers other priority owners when
   the current owner is flat, but proceeds with a current positioned owner.
   A fake account refresh discovering a new building-only position while an
   already covered position is being visited evaluates the covered owner first.
   Immediate same-owner cancel/replace continuation can further consume time.
   Existing tests prioritize positions over flat entries, not these urgency
   classes among positions.

Two isolated fake-client reproductions in the ignored local review harness
passed, confirming current behavior without venue mutations. The117 existing
reconciler/REST-priority/reducer-continuity tests passed; separate self-chase
tests were also run. These are diagnosis tests, not proof of a repaired build.

## Live timing and uncertainty

Budole/pytiva position-4.03 observed00:53:54.621396Z September19 UTC.
Builder cancellation sent00:54:13.800681Z, acknowledged00:54:13.853757Z:
19.232361 seconds after initial observation. Positive cancel observed
00:54:17.593716Z; BUY reducer sent00:54:21.375979Z and acknowledged
00:54:21.446498Z, total26.825102 seconds. Hitsat/jiayua repeatedly repriced its
SELL reducer during that interval. The code gaps are consistent with delayed
priority, but do not attribute every elapsed second to one cause or prove loss.

Repeated SELL replacements at the same0.85 price were observed. Existing code
retains exact-size reducers already at current touch or desired improved price.
Logs do not capture the cancel-time touch/desired-price inputs needed to prove
each reprice unnecessary. Do not remove repricing or alter exit-price policy
solely from repeated replacement prices.

Correction to earlier status: the00:53:19.295220Z API window contained one
`balance@429` among263 attempts over77.7s (3.384/s), plus five order404s.
Later00:54:40–00:57:44Z windows recovered with zero429. This does not establish
the429 as the cause of the26.825s delay. Latest completed01:19:01–01:27:39Z
windows had zero429/cooldown, approximately3.221–3.530 requests/s; individual
order404s remained. No venue-fault or permanent zero429 claim is made.

## Recommended repair, not yet authorized

Use explicit urgency classes: due hard exits, new fills with builders or
missing/incorrect reducers, then optional reprices of already covered inventory,
then flat-entry work. Apply consistent exact-coverage evidence to the pre-periodic
urgent pass, discovery deferral and inter-position scheduling. Preserve cancel
terminal proof, unknown-state refusal, per-owner backoff, bounded fair visits,
stop handling, hard-exit budgets and all price/risk/business settings.

Prefer this focused scheduler correction over faster blanket polling or parallel
order executors: it addresses ordering without deliberately increasing REST
request rate or adding mutation races. The tradeoff is delaying optional
reprices/refill while uncovered inventory is serviced. Add cancel-time quote
diagnostics to assess churn separately. No claim that this eliminates429s.

Required repair acceptance: long/short, partial fills, builder-only/mixed/wrong
quantity, multiple simultaneous fills, due exits, unknown cancels, backoff,
retirement, shutdown, both account-source modes and exhausted visit budgets.
Require regression/reversal/full suites and authorized deployed acceptance.

## Final read-only live check

Same supervisor388103/runner388121, uptime2h16m at01:27Z, no restart.
Authenticated01:27:30.327479Z:30 orders across23 markets,27 flat entries and
three exact reducers: BUY10 for chypav/kormyk-10, SELL10 for volyur/kryvit+10,
BUY3 for yidwan/elezah-3. Zero unresolved intents/unknown orders. Sequential
ledger read had33 working orders/three positions, so it is not identical-count
venue proof. Earlier01:21:56Z snapshot had four positions including a builder
and one reprice gap; it was not claimed fully protected. No API/trading mutation
was made by this review. Documentation-only handoff is committed separately.

## Follow-up: diagnostics first (operator authorized)

The operator requested item3 first so actual repricing evidence can inform a
combined repair. This authorizes observation code, not activation of the two
scheduling changes. No price, size, cap, risk, scheduler, source-selection,
polling or lifecycle policy is changed. The current process cannot acquire new
Python hooks without a restart; separate observers cannot reconstruct its exact
decision-time book. Live collection therefore awaits an authorized diagnostic
deployment/restart, not a guessed reconstruction from later public data.

Implementation adds one `[reducer-decision]` JSON row for an attempted position
action in the ordinary decision driver (both account-source modes). The exact
planning frame is copied before the driver awaits; the row is emitted after
the driver returns/raises. It records target order/price/exact remaining quantity,
signed position, current bid/offer, existing passive-price helper result,
entry basis/tick, book observation/receipt/transaction clocks, explicit unknown
flags, builder/reducer counts, exact matching/preferred orders and action reason.
The result distinguishes cancel ACK, placement ACK, no ACK and driver exception;
ACK is never described as terminal cancellation or a working replacement.
Only exception type is included, not response bodies or error text.

This is bounded event logging, not per-tick logging: one scalar record per
attempted position action, no full books/order lists, retained history, new
API requests, WebSocket subscriptions, ledger queries/writes or extra awaits.
All data comes from the existing immutable frame and original driver result.
Capture/format/output failures are isolated from execution and remain missing
evidence. Shutdown has its own driver path and is not covered by this hook;
locally deferred pre-driver actions and process death before return likewise
do not produce an acknowledgement record. Existing terminal/timing logs remain
the independent cancellation proof. Logging cannot be used as trading authority.

### Dedicated edge-case audit

- Long/short, partial size, duplicate exact reducers, builder-only exposure,
  one-sided/missing books, unknown basis, stale/future clocks, hard/maintenance
  actions and flat/critical exclusions are covered by focused tests.
- A simulated book replacement during the awaited driver proves the diagnostic
  keeps the original frame, not the newer book.
- Actual fake-client cancellation still succeeds when capture or log output
  raises. Driver exceptions still reach the original recovery path, with no
  newly authorized retry or swallowed execution error.
- Pure capture does not re-run the stateful planner, change its inputs or
  consume the result. Original self-chase and continuity/priority tests remain
  unchanged. No new memory cache exists to survive a source switch or restart.
- Restart/rollback use the existing safe ownership/deployment gates. Removing
  the hook removes only evidence; no schema migration or ledger repair is needed.
- Synchronous logging has small CPU/output overhead, so this is not a claim
  of zero runtime cost or faster trading. No full-slate diagnostic loop is added.

The64 focused tests passed. In-memory reversal disabling the capture hook
produced exactly one expected integration assertion failure (missing row),
without changing source or contacting the venue. The complete source suite
passed all1,854 tests in277.141s using the production virtual environment against
the Windows working copy through WSL. The twelve new tests include four runner
integration tests. Final diff/edge audit and documentation audit passed. No live
repricing conclusion may be claimed from these fake tests; deployment and
restart remain pending explicit authorization. Code is prepared/pushed only.

Read-only authenticated follow-up01:40:46.147748Z:35 orders across27 markets,
30 flat entries and five exact reducers. Positions: cosale/filmih+10 SELL10,
budole/pytiva-10 BUY10, masyev/hohser-10 BUY10, pysyev/cherom+10 SELL10,
yidwan/elezah-0.06 BUY0.06. Zero unresolved/unknown state; ledger agrees.
The running bot still has the original loaded code, not the new diagnostic.

## Follow-up: both priority repairs authorized, prepared only

The operator subsequently requested the other two fixes. This expands the code
scope to the confirmed coverage and inter-position scheduling gaps; it does not
authorize a deployment, restart, new exit-price policy or blanket removal of
repricing. The diagnostic in `d8ce159cc` remains included for later live evidence.

### Chosen implementation and tradeoff

- Reuse the existing strict reducer-preservation predicate as one named exact
  coverage helper. Exactly one opposite-side order with remaining quantity
  equal to absolute inventory, no builders/duplicates, no unresolved intent and
  no absent order is required. The public-recovery predicate is behaviorally
  unchanged. Urgent service, exhausted-owner yielding and discovery deferral
  now share that evidence rather than treating any working order as coverage.
- Classify REST owners locally: existing due deadline first, unknown-deadline
  or uncovered inventory second, covered routine work third. No deadline,
  passive price, IOC permission, quantity or risk policy is changed. The urgent
  pre-periodic pass also orders due owners first.
- After existing account proof, allow a higher-urgency changed owner to preempt
  another held owner through the same guarded decision path. Stable sorting
  preserves peer order. Backoff, unresolved intent, isolated state, stop,
  rebind, freshness and mutation admission remain binding. Requeueing a
  previously prioritized owner preserves its exit-only dispatch marker, so
  later flatness cannot accidentally create an extra entry decision.
- Keep bounded visits, counting naturally reached held owners too. An
  exhausted owner can defer flat work but cannot erase other held owners.
  WebSocket source retains empty priority tokens and its old scheduling hooks;
  switching a REST runner instance to WebSocket likewise disables this ranking.

This addresses ordering rather than raising poll frequency, increasing the
shared governor or introducing parallel mutation executors. The rank/coverage
checks use only local ledger state. Changed scheduling can change when existing
proof work occurs; actual REST volume and latency need live acceptance. The
tradeoff is deferring optional repricing, discovery or refill while urgent
inventory is serviced, not a guaranteed latency/429/reward improvement.

### Verification and dedicated edge-case audit

The82 focused tests pass, including twelve new urgency tests, existing priority,
reducer-continuity, diagnostics and shared-recovery tests. Two in-memory
reversals each produce one expected assertion failure: restoring any-order
coverage, and removing inter-position urgency ranks. Original source is never
replaced by the reversal harness. The complete suite passed all1,866 tests in
282.355s using the production virtual environment against the prepared Windows
source through WSL. No code changed after that full-suite run.

- Long/short exact coverage, missing reducers, remaining builders, mismatched
  partial size, duplicates, absent orders and unresolved intents are covered.
- Known due/near deadline and unknown deadline remain conservative. Existing
  tiny/kickoff clocks, hard-IOC retries and terminal proof tests still pass.
- A new fill discovered during proof for a covered owner executes first;
  due/uncovered/covered/flat order is explicit. A real fake-client builder
  cancellation reaches positive CANCELED/RECONCILED proof before exact reducer
  submission and before the covered owner's optional reprice. A new PLACE ACK
  may still await its own visibility; the test does not call it working proof.
- Backoff, unknown mutation and isolated ownership do not gain priority bypass.
  Stop-during-proof and rebind keep existing guards; no new venue client exists.
- A preempted priority owner that becomes flat does not create an extra entry.
  Continuously changing tokens and exhausted owners remain bounded; other
  positions still receive service. One old continuity expectation was updated
  deliberately: an uncovered second owner now goes before a covered first
  owner, while both still receive exactly one bounded visit.
- Both account modes retain their source contracts. No selector, quantity,
  pending-entry accounting, four$25 window, price, expiry, private stream,
  proof-age, limiter, execution schema or order driver rule is changed.
- Recovery shares the already existing exact reducer predicate; shutdown keeps
  its independent cancellation/flattening path. Startup/restart retain all
  ownership/flat proof gates. No migration, new persistent state, or ledger
  repair is introduced; rollback is ordinary reviewed source rollback.

Prepared code is not live acceptance. Before activation require the operator's
deployment/restart instruction, fresh safe stop/start gates, installed-source
hashes/tests, and natural acceptance of first-reducer latency, simultaneous
fills, cancellation proof, request load and diagnostic records. The actual
necessity of repeated live reprices remains unproved until diagnostics run.

## Open-position-first investigation, September19 01:54–02:02Z

The operator prioritized investigating the number of open positions before
finishing the release work. No stop, restart, deployment, venue mutation or
policy change was performed. Supervisor388103/runner388121 remained running
on the older `aae337658` source, verified again at approximately02:01Z.

Sequential authenticated report-lane snapshots, with local ambiguity checks:

| UTC capture | Open positions | Flat entry orders | Positioned orders | Ambiguity |
|---|---:|---:|---:|---:|
|01:54:37|8|17|7|0|
|01:56:43|6|4|6|0|
|02:01:31|2|30|2|0|

The missing reducer in the first sample was cosale/filmih during its normal
cancel-confirm-IOC transition, not proof of a permanently stranded position.
The last two positions were cherom/tuzole-10 with OPEN BUY10 and
pysyev/cherom+10 with OPEN SELL10. Venue and ledger agreed at the last check;
each had exact-size exit coverage. At01:57:43Z the six then-held positions were
5m23s–13m15s old according to durable fill basis. The pending-entry limit
intentionally excludes positioned-market orders, so several overlapping fills
can accumulate while the existing passive-exit windows run. Position count
alone does not establish an exit failure or breach of the entry-order cap.

Natural timed exits were observed on the unchanged live source:

- cosale/filmih: IOC ACK01:54:37.397028Z,18.876s after its deadline;
  flat observed01:54:40.678377Z.
- annfri/deskra: IOC ACK01:59:33.185147Z,4.910s after its deadline;
  flat observed01:59:35.436840Z.
- marmak/derdmy: IOC ACK02:00:27.545123Z,6.106s after its deadline;
  flat observed02:00:30.777048Z.

Authenticated subsequent absence confirms these positions were no longer
held. This is evidence the old timed exit path runs, not proof every exit is
on time or that new code is live-accepted. The ordinary900-second clock is
subject to the existing execution/proof/price rules, not guaranteed flatness
at exactly15 minutes. No clock or price policy was changed.

### Actual replacement gaps, not misleading first-create ages

Read-only order history shows original opposite-side entry orders already
covered two positions before their first newly-created reducer. It would be
incorrect to call the294s and145s initial-observation-to-new-create intervals
entirely unprotected:

- cherom/tuzole's original BUY10 expired01:49:37.841166Z; replacement ACK
  01:50:18.830557Z,40.989s later.
- marmak/derdmy's original SELL10 expired01:49:38.164918Z; replacement ACK
  01:50:19.086804Z,40.922s later.
- Both expiry events were durably received01:49:42.307515Z, leaving36.523s
  and36.779s from known expiry to replacement ACK. ACK is not working-proof
  time, so these are measured to acknowledgement, not full coverage bounds.

During that interval annfri/deskra continued cancel-confirm-replace cycles;
its final replacement ACK01:50:18.578118Z immediately preceded the other two.
It had29 reprice cancellations in the episode by01:57:43Z. This corroborates
the need to prioritize uncovered positions over a covered owner's optional
repricing, which the prepared repair targets. It does not prove every reprice
was unnecessary or attribute the entire41-second interval to one mechanism;
cancel-time book inputs are not available in the old runtime.

The current work therefore remains exact coverage and urgency scheduling,
not a new open-position cap, shorter holding period, aggressive exit price,
higher polling limit or blanket suppression of repricing. The diagnostic
prepared in `d8ce159cc` is still required for the latter question. Latest
02:00:44Z API window:200 requests/62.5s=3.198/s, four order-retrieve404s and
zero429s; this is not a claim that the whole run had no429s.

Follow-up02:04:38.230864Z authenticated31 orders:30 flat entries plus exact
OPEN BUY1.9 covering the only position, a new annfri/deskra-1.9 fill first
observed02:02:47Z. Unknown orders0; one live intent in flight and ledger30
orders versus venue31 during sequential reads, so do not claim a flat/clear
restart gate. All six positions from01:56:43Z were now absent. Cherom/tuzole's
timed IOC ACK02:03:05.462233Z was12.153s past deadline, flat observed
02:03:08.665090Z; pysyev/cherom's ACK02:03:51.873091Z was7.262s past deadline,
flat observed02:03:53.962648Z. This distinguishes the new fill from a stuck
old position. Prepared priority fixes were committed/pushed as `f54b540fe`;
running source is unchanged and no deployment or restart occurred.
