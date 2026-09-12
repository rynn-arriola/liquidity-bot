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

### Final REST restart and initial live evidence

- A fresh parity backup captured at **07:11:50Z** is 361,873,408 bytes with
  SHA-256 `ae9ee78b6867e640d81099f65a630b403cecbb3a19ba95aa8b9de58a5dcd7b13f`.
  The **07:12:40Z** authenticated start gate then proved venue and ledger zero
  orders, positions, unresolved intents and unknown orders.
- The explicit REST restart retained size 10 / 32 and every existing risk and
  strategy rule. Runner `600164` is supervised by `600149`; startup logged max
  30 flat pending entries, positions/reducers excluded, 5-second normal and
  1-second positioned polling, four read workers, healthy official maintenance
  status and no automatic source switch.
- At **07:16:53Z**, authenticated evidence found 28 venue orders across 28
  markets, zero positions, unresolved intents or unknown orders. All first 30
  PLACE intents were `RECONCILED`.
- The first API window ended at **07:17:06Z** after 264.3 seconds: 514 attempts
  (484 reads / 30 mutations), 1.945/s, zero 429s and six failed responses. Ledger
  and log inspection proves all 30 mutations reconciled without an action-failure
  or recovery record, so the six were read attempts. The next three windows
  recorded zero failures. The **07:23:06Z** window recorded one further failed
  response alongside four logged cancel actions, three place actions and one
  additional positive-OPEN-driven retry of a just-submitted cancel. Every intent
  reconciled, but aggregate evidence cannot identify whether the failure was the
  duplicate cancel or one of that window's reads. This retry pattern predates the
  permanent source runner: the ledger contains 88 same-order cancel pairs under
  two seconds from September 11 through the prior runner, versus six after this
  restart at the time of review.
- The current limiter evidence retains aggregate attempts by category but only
  an aggregate failure count. Contained paths do not necessarily log the
  underlying exception, so the exact failed endpoint/status cannot always be
  recovered from this run and is deliberately not inferred. The official September 12
  status page reported production operational with no incident; that does not
  disprove an isolated request failure.
- A natural position appeared at 07:22:20Z and the existing reducer path detected
  it, cancelled the building order and began repricing the passive reducing order.
  Authenticated **07:29:44Z** evidence then found 29 orders across 21 markets,
  zero positions, unresolved intents or unknown orders. This naturally accepts
  detection and passive reduction; it is not acceptance of the unchanged
  900-second hard-exit path or unfilled-IOC continuation.

No code, configuration, source selection or process state changed during the
failure investigation. Exact per-category/status failure telemetry is a proposed
observability improvement, not deployed behavior.

## Cancel-publication and WebSocket follow-up

The later failure investigation found a separate local defect in uncertain-cancel
recovery. After a clean cancel acknowledgement, an authoritative REST read could
briefly continue to publish `OPEN`; recovery then re-sent the same cancel after
only 0.06-0.32 seconds because its throttle began only after that retry. The
reviewed repair groups unresolved cancel intents by exchange order, uses the newest
durable intent timestamp together with any in-memory retry time, and waits the
existing ten-second publication window. It still requires positive active-order
evidence; absent or unknown state cannot authorize a mutation. The focused
regression failed under a deliberate restoration of the old behavior and passed
again after the repair.

The same follow-up preserves failed call category/status in bounded, redacted
limiter telemetry. Examples are `market_book@503` and `mutation_create@503`; raw
operation arguments such as market slugs are never logged, and collecting the
field makes no API request. Read and mutation gateway regressions verify both
attribution paths. The complete WSL suite passed **1,469 tests in 175.442 seconds**.

Two new read-only private-stream probes completed the handshake and protocol Pong.
The ordinary order subscription produced one order snapshot/update frame. A second
probe using both ordinary order and the raw guide's newly listed explicit order-
snapshot type produced two frames for each order request, while balance and
position subscriptions still produced none. Inspection of the installed SDK and
latest package found the same pinned version, `polymarket-us==0.1.2`, with the
documented camelCase subscription payload and a raw-message event emitted before
typed handlers. Therefore the stream is partially publishing order data, but is
still not sufficient for complete account-state authority; REST remains selected
and no automatic source switch or SDK change is included.

The prior runner was stopped before release work. Graceful shutdown truthfully
parked one four-contract position with its bot-owned reducer because kickoff was
about 5.4 hours away. Exact ledger ownership and fresh authenticated venue reads
proved that reducer was the account's sole open order and the position was the sole
exposure. Under the operator's explicit flatten authorization, only that exact
order was cancelled, then the official market-scoped synchronous close-position
operation executed three fills. Fresh authenticated proof returned zero open
orders and zero positions. Account-wide cancel-all was not used. Deployment,
restart and the requested live observation are appended only after completion.

### Deployment and live acceptance

Commit `b826ff11ee739e61704ddba80533e089be84b111` was pushed before
deployment. Windows and GitHub matched; the clean WSL checkout fast-forwarded
from the already GitHub-verified Windows repository after its direct HTTPS fetch
stalled. WSL HEAD and `DEPLOYED_COMMIT` match. The stopped parity capture at
07:58:54Z produced a 362,795,008-byte private recovery copy with SHA-256 prefix
`81126369aa4d`; the offline desktop peer remains pending. The exact deployed
checkout passed all **1,469 tests in 127.423 seconds**.

Fresh authenticated 08:00:50Z evidence proved zero venue orders/positions and
zero unresolved/unknown ledger state, while the ledger truthfully retained the
one locally stale working order/position closed outside the stopped runner. The
normal startup reconcile retrieved the exact terminal order, backfilled the close
executions and applied complete account snapshots before new entries were
authorized; there was no raw ledger edit or risk-baseline reset.

The explicit `auto 10 32 rest` launch started supervisor `649355` and runner
`649372` at 08:02:00Z. It logged a healthy official maintenance state and the
unchanged overnight max of 30 flat pending entries, with positions/reducers
excluded. Its first 290.1-second budget window completed 543 attempts, including
30 mutations, at 1.872/s with zero 429s. The new telemetry attributed all five
failed reads to `order_retrieve@404`; the next 72.9-second window completed 137
attempts/four mutations with zero failures and `failure_categories=none`.

A natural ten-contract fill at 08:06Z provided position-path acceptance. The
runner cancelled the building order, placed a passive reducer and continued
cancel-confirm-repricing it. At 08:08:17Z, authenticated evidence showed 25 flat
entry orders plus one reducer on that positioned market, the +10 position, and
zero unresolved intents or unknown orders. No exchange order had more than one
CANCEL intent during the new session, so the immediate duplicate pattern did not
recur. The open position is still inside the unchanged passive phase; this proves
continuous reducer coverage, not a forced 900-second exit or final close. Both
processes remained alive after the requested 7:15 observation, so no corrective
second restart was warranted. A later authenticated 08:11:48Z proof found 28
flat entry orders plus one reducer on each of two natural positions (+10 and -4),
with zero unresolved intents or unknown orders; both position paths remained
covered while the flat-entry count stayed below its independent ceiling.
