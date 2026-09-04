# Midnight–9 AM Eastern entry-order ceiling — 2026-09-03

## Operator decision

The operator added a separate maximum of 20 pending entry orders from midnight
through 9:00 AM Eastern, without removing or replacing the existing loss-scaled
40/30/20/10/0 tiers. The purpose is to use less entry breadth during the early
part of the day and preserve more capacity to keep trading toward EOD.

This is a risk-allocation choice, not evidence that clock time caused the loss
and not a guarantee that the bot will reach EOD. The completed-pair ledger since
August 18 showed the largest six-hour total loss in 12 AM–6 AM ET (`-$265.16`)
and the worst loss per pair in 6 AM–12 PM ET (`-8.86¢`). Those overlapping
observations motivated the chosen 00:00–09:00 window but do not isolate a causal
time effect from market mix, fill selection or exit conditions.

## Exact policy

- From 00:00 inclusive to 09:00 exclusive `America/New_York`, the clock-based
  pending-flat-entry ceiling is 20. The timezone conversion is DST-aware.
- The effective ceiling is `min(clock ceiling, durable loss ceiling)`. A 10- or
  zero-order loss tier therefore remains 10 or zero overnight.
- At 09:00, only the clock ceiling lifts. The current day's loss ratchet remains
  intact and still cannot widen before the next Eastern risk day.
- Working flat-market entry orders and unresolved flat-market PLACE intents
  consume capacity. A working order and its same client intent are deduplicated.
- Markets with a nonzero position are excluded in full. Passive reducers, IOC
  exits, recovery and shutdown remain unblocked and uncounted.
- Overflow drains through the existing positively-owned, one-cancel-per-pass
  path. Higher-ranked/reward candidates and complete pairs keep priority.
  Unknown state cannot authorize a cancellation.
- The planner and the final mutation boundary both recheck the effective cap.
  Midnight and 09:00 alter the scheduler signature so flat owners wake without
  waiting for a book event.
- The feature is local clock/accounting logic. It adds no venue request, stream,
  worker, order retry or persistence table.

## Verification

Focused regressions cover EDT and EST midnight/09:00 boundaries, naive-clock
refusal, small configured capacity, tighter-loss precedence, 09:00 restoration
without loss reset, scheduler wake-up, exact overflow cancellation, existing
loss behavior, position/reducer exclusion and mutation-boundary reducer bypass.

A deliberate reversal changed the cap from 20 to 21. The exact EDT and EST
window assertions failed `21 != 20`; restoring 20 made the test pass. This
proves the operator's requested value is test-enforced.

Source commit `44c35078c` passed all 1,098 tests in 103.039 seconds under the
production WSL Python/runtime while importing directly from the edited Windows
checkout. The documentation audit also passed after this record was added.

## Deployment boundary

This change was prepared and pushed without touching the live WSL checkout, deployment
marker, supervisor or runner. It requires the ordinary reviewed push, exact
parity check, authenticated flat/order-free gate and Command 1 restart before it
can affect venue orders. Natural acceptance should confirm the first midnight
or 09:00 transition, no cap overshoot, unchanged rank preference, and continued
position/reducer/exit service. Do not induce a loss or fill for acceptance.
