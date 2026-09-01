# Dirty-market decision scheduler — 2026-08-31

## Objective and boundary

Prepare the current size-10/24-slot strategy for lower decision latency and
future capacity evaluation without changing strategy output. The loaded live
runner is not part of this change: no stop, restart, deployment or venue
request was performed. Loaded source remains `4b6e4e8`; `v1.1.0` remains the
known-good size-10/24 rollback.

This is a latency change first and a CPU change second. The existing serial
full-slate pass makes an unchanged market wait behind unrelated markets. The
already-documented cancel-confirmation evidence measured another 39.5 seconds
median and 118.6 seconds p90 before replacement after the 1.2-second terminal
confirmation. CPU was approximately 0.677 of one logical core at 24 slots, but
no component split existed, so no CPU-reduction percentage is claimed before
live measurement.

## Prepared design

The runner now selects a subset and passes it through the unchanged
`_service_bound_markets`, `_decision_sweep`, reconciler and action driver.
Selected-market order is preserved so cached buying-power allocation and
mutation order do not change.

- A nonzero position is serviced and planned on every outer pass, outside the
  dirty set. Its passive reducer, durable 900-second inventory clock,
  five-minute tiny-residual clock and 300-second pre-kickoff flatten cannot be
  starved by a quiet book.
- A flat market wakes for its exact book/order/fill/position input, successful
  local mutation, global entry-authority transition or an owned timer.
- A PLACE intent resolving to an exchange state wakes its exact market. A
  terminal CANCEL keeps the existing exact-intent/exact-order priority path.
- Flat resting markets retain exact reward-eligibility dwell and pre-kickoff
  deadlines. Contract and quiet-book refresh timers remain per market.
- Buying power is deliberately not a slate-wide dirty trigger. It remains an
  apply-time affordability gate; otherwise every accepted entry would recreate
  a full sweep while the bot is busiest.
- Market-book messages retain the existing latest-snapshot coalescing. Private
  fill/order/position messages retain wire order and are never coalesced.
- Abandoned flat/order-free markets remain dormant, while any nonzero position
  remains unconditional even after retirement exhaustion.
- Mid-sweep execution-state and equity refresh margins remain unchanged.

All owner classes are read in one ledger query per scheduler selection rather
than repeating per-market intent lookups. The normal active order remains
selector order, followed only by sorted risk owners outside it. A successful
mutation uses an explicit generation, not timestamp inequality, so two actions
with the same clock value cannot miss their exact follow-up.

## Measurement

The prepared runner emits one secret-free `[decision-scheduler]` window with:

- passes, markets seen, evaluated and skipped;
- unconditional position evaluations;
- service/decision process-CPU milliseconds and whole-runner CPU cores;
- cooperative-yield event-loop delay p50/p95;
- book/fill/order input to decision p50/p95;
- input to resulting action p50/p95.

An evaluated no-action input is cleared from action-latency attribution. A
market-local backoff deliberately retains the input through its retry, while a
later timer action cannot be blamed on an old already-evaluated event.

## Review and verification

Focused scheduler regressions cover exact book ownership, selected order,
evaluated/skipped counts, successful-mutation follow-up, position 0→nonzero,
unconditional repeated position service, exact PLACE resolution, buying-power
non-trigger, global authorization, reward dwell, pre-kickoff flatten and
abandoned-with-risk behavior.

The reconciler plus live-runner suites passed 439/439 after implementation. A
deliberate reversal removed nonzero positions from unconditional selection;
the position-service regression failed with an empty scheduled set and passed
after restoration. Native Windows has four unrelated shadow/scalp constructors
that correctly hit the pre-existing POSIX-flock guard forbidding authenticated
executable clients there. Excluding only those exact four platform-guard cases,
the 982-test all-applicable suite completed `OK` with 11 expected skips, zero
failures and zero errors. No scheduler test failed.

Review found and corrected two candidate-only defects before handoff: active
markets had briefly been alphabetically reordered, which could change which
market reserves buying power first; and a create response carrying an embedded
OPEN/FILL could resolve before a later private event, so the successful
mutation itself now wakes that exact owner.

A later independent review found a third candidate-only defect before
deployment. `_scheduler_owner_sets` is reached twice per outer loop, but its
active-order arm filtered only by `status` while the existing order index began
with `market_slug`. On the 39,652-row live ledger, SQLite therefore scanned all
historical orders in 3.66–3.93 milliseconds per call, or about 7.3–7.9
milliseconds per pass. A new `(status, market_slug)` covering index changes the
plan from `SCAN` to `SEARCH`; an in-memory live-ledger copy measured
0.067–0.122 milliseconds per call. Writable ledger startup creates the index
additively for both new and existing databases. Dedicated regressions prove
the migration and exact query plan, and temporarily removing the index made
the plan regression fail with the original `SCAN`. The restored ledger and
live-runner slice passes 420/420, and the complete pinned-WSL suite passes
988/988 in 96.975 seconds. No live database was modified.

## Activation and rollback gate

Do not load this during the announced Polymarket maintenance and do not restart
without a fresh operator command. On a later flat-gated restart, accept first
at 24 slots. Compare the same predeclared metrics: span-weighted resting side
hours per bound market-hour, distinct markets acted on per hour,
cancel-to-replace gap, `execution_state_unknown`/quarantine counts, decision
latency and `[decision-scheduler]` evaluated/skipped/CPU fields.

Rollback to `v1.1.0` on any missed pre-kickoff flatten, position reaching its
inventory clock without a working reducer, lower resting fraction, higher
decision latency or any increase in `execution_state_unknown`. Capacity may
then advance only 26 → 28 → 30 → 32 with the existing API gates and reward per
resting side-hour as an additional stop condition. Worker processes remain out
of scope; if later needed, they may perform pure calculation over immutable
snapshots only, with no credentials, limiter claim or venue call.

## Pre-load staging and baseline — 2026-09-01

The reviewed source/index commits were fast-forwarded into the clean laptop WSL
checkout at `17144599577b5b19af75debd048ec6a9efa62171`. An isolated archive of
that exact checkout passed 988/988 tests in 70.815 seconds. Windows, GitHub and
WSL source match. The existing runner PID 1030888 and
`DEPLOYED_COMMIT=4b6e4e80b39ea1960053bddf16d3e2591193fb53` remain unchanged; no
stop or restart occurred. A read-only live-ledger check confirmed
`idx_execution_orders_status` is still absent, so the additive migration has
not run against production and no staged source is loaded.

Parity's verified online snapshot at 2026-09-01T04:05:54.736447Z froze the
pre-load size-10/24 comparison window beginning with the current runner at
00:14:34Z:

- 154.2647 resting side-hours / 175.9699 side-span hours = 87.6654%
  span-weighted resting; 1.7268 resting side-hours per market-span hour;
- 611 actions across 39 distinct markets; full UTC hours 01/02/03 reached
  24/17/17 distinct action markets respectively;
- cancel action→next same-owner-side PLACE: n=203, p50 43.073s, p90 195.867s;
- positive terminal cancel confirmation→next same-owner-side PLACE: n=275,
  p50 48.999s, p90 144.717s;
- zero `execution_state_unknown` lines and zero runner failures;
- two quarantines: one recovered private disconnect and one recovered market
  stream silence. These are part of the baseline rather than scheduler faults.

This 3.8558-hour window is the frozen comparison, not an acceptance verdict.
Use evaluated/skipped plus decision/action latency across several comparable
post-load windows, and retain the existing rollback triggers.

## Load and initial live acceptance — 2026-09-01

`Command 1` stopped supervisor/runner 1030880/1030888 gracefully. The terminal
record proved `flat: true`; a separate authenticated read then proved zero open
orders and zero nonzero positions, while the ledger proved zero unresolved
intents, unknown absences and nonzero positions. `DEPLOYED_COMMIT` advanced to
the reviewed executable commit `17144599577b5b19af75debd048ec6a9efa62171`
only after that proof. Supervisor 1037235 and runner 1037243 then started once
at size 10 / 24. The additive `idx_execution_orders_status` migration is
present on the live ledger.

Startup completed its full account/market safety preflight before mutation.
At 04:30Z one read-only authenticated acceptance found 44 open orders across
all 24 selected markets and zero nonzero-position markets, matching zero
unresolved intents and zero unknown absences in the ledger. The first two API
windows reported 458 attempts/45 mutations over 208.4 seconds and 108/5 over
73.3 seconds, with zero failures, zero rate limits, zero cooldown and queue
depth one.

The optimization itself is not yet accepted. The first six scheduler windows
reported 0 skipped across 168 pass owners; steady windows used 0.679-0.726 CPU
core with 70-80 second duration. Source inspection matches the observation:
`_scheduled_market_slugs` treats `next_book_refresh` as a full decision due
condition, while each quiet-book check is scheduled only three seconds after
the latest book. Every deadline has therefore elapsed before the next long
pass, even without another dirty trigger. A brief fail-closed equity freshness
transition canceled two bids as `account_new_risk_paused`; both were
automatically replaced 14-45 seconds later, and marked day P&L was only
`-$3.3890`, so no fee/loss gate fired.

Leave the healthy 24-slot bot live and collect comparable multi-window resting,
action and execution-state evidence. Do not widen capacity or claim a CPU win.
A follow-up design must separate cheap quiet-book liveness servicing from full
business-decision eligibility while preserving the five-second planning
freshness gate, unconditional position service and existing rollback triggers.

## CPU attribution review and prepared ledger repair — 2026-09-01

The first post-load attribution corrected two overclaims while preserving the
useful diagnosis.  The 04:43:11Z `[api-budget]` window really did contain 87
attempts over 62.2 seconds, including 51 `market_book` reads, 84 total reads
and three mutations.  That proves book REST is the largest request category in
that window.  It does **not** prove that 93% of elapsed time was REST: p95 wait
and response values are not averages and cannot be multiplied by every
request.  Likewise `max_waiting=1` counts admission waiters, not in-flight
requests, and the once-per-outer-loop event-delay sample cannot exclude every
short synchronous CPU burst.  The source remains mostly serial, but those
three telemetry fields must retain their narrower meanings.

One independent CPU defect was proven directly.  The live read-only ledger had
3,697 fills across 1,048 historical markets and no market-leading fill index.
`realized_by_market()` called `realized_pnl(slug)` once for every historical
market, so every risk refresh scanned the full fill table 1,048 times.  Live
read-only timing was 311.562 ms for `realized_by_market()`, 19.750 ms for one
`realized_pnl()` and 0.256 ms for `working_orders()`.  On an in-memory copy of
the same fills, the old market query took 262–278 ms for the complete map; a
single chronological replay took about 19 ms, and adding
`execution_fills(market_slug, reversed)` reduced individual market lookup to
22–29 ms for the complete repeated benchmark.

Local implementation commit `773677b2f` prepares both repairs without changing
the accounting equation: all non-reversed fills are still replayed in
`event_at, received_at, execution_id` order, each market retains independent
inventory matching, a `since` cutoff still replays old basis before attributing
new realization, and an all-reversed historical market still appears with
zero.  The additive index is created on every ledger open with `IF NOT EXISTS`.
Focused tests cover exact equivalence to individual replay, interleaved
markets, cutoff basis, reversed-only history, one fill-history SELECT, query
plan and migration of an existing database.

Deliberately restoring the old implementation made the one-read regression
fail with three fill queries for two markets.  Deliberately removing the index
made both index tests fail and restored SQLite's `SCAN execution_fills` plan.
After restoration, the focused ledger suite passed 59/59 and an isolated
archive using the pinned laptop WSL environment passed 995/995 in 74.696
seconds.  Windows' unpinned Python 3.14 environment is intentionally not the
acceptance environment; it lacks the project SDK and timezone packages.

No profiler attribution is claimed for the remaining CPU.  A temporary,
isolated `py-spy` install could not attach to the live runner because Linux
`ptrace_scope=1` denied the non-root observer and passwordless sudo was not
available.  Kernel tracing policy was not changed.  Do not redesign quiet-book
REST or the scheduler from the incomplete phase counters alone.  Review
`773677b2f` next; only a later explicitly authorized flat restart may migrate
and load it.  The live process, source checkout and ledger were not changed by
this preparation.

## Independent replay review and maintenance-stop correction — 2026-09-01

An independent live-copy review approved `773677b2f`.  Against 3,712 fills and
1,051 markets, old and new maps had identical keys and zero value mismatches
both account-wide and with a cutoff.  The market-leading index supplied most
of the measured reduction (344.5 ms to 24.6 ms for the old repeated replay),
while the single chronological pass reduced the remaining work to 18.1 ms.
An ordering index was tested and rejected because removing the temporary sort
changed only 16.7 ms to 15.8 ms; any later growth problem belongs to a
fill-invalidated cache, not another index.  Keep both prepared changes and load
them only on a separately authorized flat restart.

The same review called 11:22-11:29Z healthy steady state.  That interpretation
does not survive the incident timeline.  The runner entered maintenance at
06:56Z, passed its account health gate only at 11:10Z, and then encountered
inactive contracts and empty books throughout recovery.  Fast passes in that
window skipped normal successful decision work because failure responses were
quick; they cannot prove that the earlier 70-80 second passes were merely a
startup transient.  Re-baseline only after venue and per-market data are
healthy.  The telemetry field `cpu_cores` is recent process CPU divided by the
full wall-clock reporting window, not an active-work-only ratio; lifetime
`TIME/ELAPSED` answers a different averaging question.

The maintenance stop exposed a separate recovery defect.  Generic
`_refresh_book` accepts one-sided books but immediately dereferences both sides
to validate potential entry payloads, and it performs that active-entry
validation even when an inactive/retired owner needs only recovery or exit
pricing.  This produced empty-iterable `ValueError` and inactive-market
`OrderValidationError`.  Split generic book acquisition from entry-only
validation and cover one-sided plus inactive-with-position recovery before a
future deployment.  Do not globally disable empty-book refresh: the ITF owner
still had a 10-contract position after shutdown and must remain unconditional.
Only flat, order-free owners may receive maintenance-aware bounded backoff,
with stream evidence waking them immediately.

The runner received SIGTERM, exhausted 31 bounded shutdown book retries and
exited itself at 11:45:16Z; it was not force-killed.  Its terminal record said
`flat=false`.  An independent authenticated 11:48Z snapshot proved zero open
orders and one nonzero position, `10.0000` contracts in
`aec-itfme-pathom-julbie-2026-09-01`.  The bot remains stopped and must not be
restarted or called flat until authoritative position resolution.

## Recovery book validation repair — 2026-09-01

The operator authorized the recovery repair and a later safe restart.  Local
commit `5f1ef3369` leaves `_refresh_book` responsible only for authenticated
book acquisition, receipt timing, dirty scheduling and required contract
identity.  A separate startup boundary validates every side that is actually
present before entry authorization.  Non-recovery startup therefore retains
tick, quantity, active/open and payload checks; recovery can retain a valid
one-sided or inactive/closed book without invoking entry-only rules.  The REST
adapter's both-sides-empty refusal and the mutation-time payload validator are
unchanged.

Three focused regressions cover one-sided acquisition without `min`/`max` on
an empty collection, inactive-book acquisition with entry refusal retained,
and recovery startup with an inactive one-sided market.  Deliberately restoring
the generic-path validation and unconditional side dereferences failed all
three with the exact old `ValueError`/`OrderValidationError` shapes.  After
restoration, the focused eight-test recovery slice, complete 371-test live
runner module and full 998-test pinned-WSL suite passed.  Final review found no
change to ownership, empty-book refusal, API request count, mutation sequencing,
position service, shutdown budgets, quote size, slot capacity or risk policy.

This repair does not clear the live operational blocker.  A fresh 12:02Z
account snapshot still proved zero open orders and the same `10.0000` position;
the exact market metadata was `active=true`, `closed=true`, status
`MARKET_STATUS_CLOSED`, and its authenticated book remained empty on both
sides.  No code can safely cross an empty book or force venue settlement.

The reviewed commits were pushed and the clean WSL checkout fast-forwarded to
`43a4c78acb54ba1f3fd7d0ad6950f1a711175841`.  Its exact post-deployment suite
passed 998/998 in 71.823 seconds, after which `DEPLOYED_COMMIT` advanced to the
executable commit `5f1ef33693818aaa5a8ad5a22e367d208dd17090` (the later commit
is documentation only).  A final authenticated gate at 12:17Z again proved
zero open orders, the same 10-contract position, closed metadata and a
both-sides-empty book.  The safety gate therefore refused startup.  No runner,
supervisor or dashboard was started and no venue mutation occurred.
