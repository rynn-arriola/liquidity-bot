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
