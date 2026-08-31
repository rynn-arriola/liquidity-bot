# Tiny residual cashout — 2026-08-31

## Operator decision

An exact, venue-tradable position from `0.01` through `0.09` contracts gets a
five-minute passive exit chance. If it is still open at five minutes, the bot
uses its normal cashout path: cancel every working order one at a time with
positive terminal proof, then submit one IOC for the exact remaining position.

This band is contract quantity, not the dashboard's marked dollar value. A
position of `0.10` or more retains the normal 900-second inventory deadline.

## Measured evidence

Read-only replay of the commissioning ledger found:

| Evidence | Result |
|---|---:|
| Accepted fractional PLACE orders below 1 contract | 569 across 121 markets |
| Accepted exact `0.01` orders | 87; all reconciled |
| Fills below 1 contract | 384; minimum `0.01` |
| Quantity/minimum-alignment rejections | 0 |
| Accepted orders or fills below `0.01` | 0 |
| Accepted `0.01`–`0.09` orders | 204 across 34 markets; all reconciled |
| `0.01`–`0.09` fills | 102 across 67 markets; 52 maker / 50 taker |

The exact `0.03` residue seen by the operator was not untradable. On August 15,
`aec-setkameua-boyvit-hurore` received a maker `0.03` fill, later placed an
exact `0.03` reducing order, received an exact `0.03` maker fill, and was then
positively observed flat. The issue was waiting, not a venue minimum.

Among 32 historical maker-small episodes that could be paired to their later
reduction, clearance took 134.5–2,178.6 seconds (average 855.6 seconds). Only
one cleared within five minutes and 23 remained beyond 15 minutes. This does
not prove five minutes is an economic optimum; it proves the existing passive
clock can let a few cents retain an entire market slot for most or all of the
900-second window. The operator selected five minutes as the policy tradeoff.

## Implementation and safety boundaries

- The durable position-open clock is the timer. Restarting does not grant a
  fresh grace period. If an older, larger position shrinks into the tiny band
  after five minutes, its residue is due immediately.
- The venue's current `minimum_trade_qty` must prove that the exact remainder
  is at least the minimum and exactly aligned. Unknown or misaligned quantity
  does not enter the accelerated path.
- A builder is canceled before a reducer. Only one cancel is planned at a
  time; the next cancel or IOC waits for exact terminal intent proof.
- The IOC uses the current exact position and fresh book. Half-depth remains
  preferred; because the maximum settlement exposure is nine cents, the tiny
  path may fall back to full displayed depth. If even that cannot cover the
  residue, the existing recoverable thin-book path keeps the position and
  retries rather than claiming flat.
- A slot is not released until the ordinary position/order/ambiguity gates
  positively prove it flat and clear.
- No selector, quote size, entry spread, six-hour horizon, 22-slot capacity,
  risk threshold, WebSocket or REST polling rule changes. The feature adds no
  API read or socket and should usually shorten tiny-residue slot occupancy.

## Verification

- 87 reconciler/configuration tests passed.
- 435 live commissioning, reconciler and slot-management tests passed.
- The complete Windows discovery ran 961 tests; its only four errors were the
  already-intentional POSIX-lock refusal for retired shadow/scalp constructors
  on native Windows. No trading, reconciliation or scheduler test failed.
- After deployment to the laptop checkout, all 961 tests passed in its WSL
  virtual environment.
- Boundary reversal: changing `>= 300` to `> 300` made the exact-five-minute
  regression fail.
- Alignment reversal: removing the venue-alignment requirement made the
  coarse-minimum regression fail.
- `git diff --check` passed before documentation.

## Deployment state

Source commit `6c5ea49` is pushed and staged in laptop checkout HEAD `3ae1c69`.
The operator explicitly withheld restart authorization: supervisor PID 937731
and runner PID 937743 remained unchanged, and `DEPLOYED_COMMIT` truthfully
remains `31c2574`. The feature is therefore available for the next separately
authorized restart but is not active in the current process.
