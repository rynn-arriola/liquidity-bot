# Single-order lookup diagnosis — September 26, 2026

Operator asked where REST load comes from and whether order lookups can be
reduced. Offline (live log since 23:54Z restart, code); no venue calls.

## Measured load (23:54Z–01:47Z, 112.6 min, 17,881 calls, 2.65/s)

order_retrieve 22.2% (35.2/min), open_orders 17.2% (27.4/min),
execution_history 17.0%, positions 15.8%, balance 10.5% (16.7/min),
mutation_create 6.1%, incentive batches 4.1%, market batches 2.4%, other ≈5%.
Recent windows: 0 failures, 0 rate-limited.

## Where single-order lookups come from

`[rest-order-proof]`/`[execution-proof]` per minute: ~350-520 orders proved
from the bulk open-orders list (`bulk_unchanged`) and 25-40 exact reads, of
which 24-36 are `exact.positioned` (plus a few missing/ambiguous/changed). In
`RestFallbackRunner._read_owned_order_states` the bulk shortcut applies only to
an OPEN, unfilled, matching order in a **flat** market with no unresolved intent
or absence; every working order in a market with a position is read exactly on
every proof. With `rest_polling_policy.json` active cadence 1 s (normal 5 s)
while any position or unresolved intent exists, proofs run back-to-back
(~27 open-orders lists/min), so position time drives most REST load. Hybrid
counters: `cadence=728` versus `urgent=150` proofs — most proofs are
timer-driven even though fills/position changes already wake an immediate proof
through the private stream.

The 2026-09-24 REST usage audit reached the same boundary: positioned exact
reads are not all redundant; removing them risks missed fills/exits, and a
one-list shortcut previously failed race tests.

## Options (operator decision)

- **A. Keep.** 2.65/s is under the 4/s shared governor with zero recent 429s.
- **B. Hybrid-aware active cadence.** While the private stream is receiving
  with no gap, relax the positioned REST cadence (e.g. 1 s → 2-3 s) because fills
  and position changes already wake an immediate proof; keep 1 s whenever the
  stream is in gap/REST fallback. Could cut a large share of positioned-time
  proofs (open orders, positions, execution history and positioned exact
  reads). Cost: a fill the stream fails to deliver is found up to a couple of
  seconds later. Timing/risk change; needs design, tests and approval.
- **C. Skip exact reads of unchanged positioned orders.** Largest single saving
  (~0.5/s) but previously rejected as unsafe; not recommended.
