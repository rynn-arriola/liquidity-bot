# Loss-scaled entry-order capacity — 2026-09-03

## Operator decision and purpose

The operator chose a gradual reduction in pending entry orders as the existing
ET-day adjusted marked-equity loss approaches HARD. The goal is to leave more
of the remaining loss budget available for the rest of the trading day rather
than stopping healthy execution early on a taker-count or fee proxy.

This is a strategy/risk-allocation choice, not a defect correction and not a
promise that the bot will reach EOD. Fewer live entry orders reduce fresh fill
opportunity and reward side-hours, but positions already open can still lose.
The independent exposure, stressed-exit, buying-power, inventory-deadline and
HARD protections remain authoritative.

## Exact policy

The denominator is the same computed HARD amount and the numerator is the same
complete, non-trading-cash-adjusted marked ET-day loss already used by HARD.
At the loaded size 10 / 32 configuration, HARD is `$128`:

| Marked loss utilization | Size 10 / 32 loss boundary | Maximum flat-entry orders |
|---:|---:|---:|
| below 50% | below `$64.00` | configured two-sided capacity, currently 64 |
| 50% | `$64.00` | 40 |
| 70% | `$89.60` | 30 |
| 80% | `$102.40` | 20 |
| 90% | `$115.20` | 10 |
| 95% | `$121.60` | 0 (exit-only) |

Each boundary is inclusive. The tightest tier reached is stored by Eastern
risk date, survives a process restart, and cannot loosen if the mark recovers.
Only the next `America/New_York` day restores the configured two-sided ceiling.
The ordinary HARD stop is unchanged at 100%.

The count contains positively owned working orders in flat markets plus every
unresolved flat-market PLACE not already represented by a working order. This
means an uncertain create reserves capacity and cannot be duplicated. Any
market with a nonzero position is excluded in full, so its builder cancellation
and exact passive reducer can finish through the existing reconciler. IOC exits,
recovery and shutdown are also outside the ceiling.

On a downward transition, every flat owner is made immediately schedulable.
The runner retains higher-ranked markets first and complete two-sided pairs
where the available shape permits it, then submits at most one positively owned
overflow cancellation per normal driver pass. Unknown market state cannot
authorize a cap-driven cancellation. New entry is checked both while planning
and again at the mutation boundary; reducers bypass both checks.

## Verification and permanent edge audit

Fourteen focused regressions cover exact threshold edges, natural configured
capacity, same-day ratchet persistence, process restart, next-ET-day reset,
notification, lowest-value side selection, higher-ranked pair retention and a
complete lower-ranked pair preferred over a higher-ranked one-sided orphan,
unresolved-create reservation/deduplication, unknown-state cancellation refusal,
the zero-entry tier, open-position exclusion and mutation-boundary reducer
availability.

Two deliberate reversals proved the important protections. Changing the 95%
tier from zero back to ten failed the exact `$121.60` and `$128` boundary
assertions. Applying the mutation-boundary gate to reducing quotes failed the
dedicated reducer test. Both changes were restored before the final suite.

The candidate passed all 1,092 tests under the production WSL Python/runtime
while executing directly from the edited Windows checkout.

The final audit covered:

- success, exact inclusive thresholds, negative/zero loss and configured
  20–32-slot capacity;
- full, under-cap and zero-cap order sets, odd/one-sided shapes and preservation
  of higher-ranked complete pairs where possible;
- uncertain PLACE races, working-order deduplication and a second entry check at
  the mutation boundary;
- unknown state, unowned-order containment and positive ownership before cancel;
- fills racing a planned cancellation: a newly nonzero position disables the
  cap cancellation and retains normal builder-cancel/reducer behavior;
- stale/recovered marks, same-day restart, ET rollover and the unchanged HARD
  stop;
- recovery, quarantine, shutdown, passive reducer and IOC paths, none of which
  can be blocked by the new-entry ceiling;
- API/load and rollback: the feature adds no venue read, mutation retry, stream,
  market or worker. It adds small local indexed-ledger reads and one row only
  when a day first tightens; rollback leaves that inert table harmless.

## Deployment boundary

Source commit `6dd8e1aba` contains the reviewed implementation and regressions.
At 2026-09-03 13:55 UTC the laptop bot remained running at size 10 / 32 under
supervisor `2751977` and runner `2751986`, with loaded `DEPLOYED_COMMIT`
`2c3ce0e54`. This candidate was reviewed and prepared for GitHub but was not
copied into the production checkout, deployed or restarted. A later authorized
restart must repeat parity, hash/test, known-account-state and normal live
acceptance gates before claiming the policy is active.
