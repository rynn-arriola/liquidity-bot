# Implementation plan

Each phase has a hard acceptance gate. Later phases do not begin merely
because earlier code exists.

## Phase 0: pure decision core

- Lifecycle selection
- Full-depth target cutoff
- Per-order reward score
- Conservative expected-net-value calculation
- Safe configuration validation

Gate: deterministic tests pass; no network or exchange side effects exist.

## Phase 1: read-only adapters

- Incentive program pagination and symbol filtering
- Market catalog/event time adapter
- Full market-book WebSocket adapter
- Incentive earnings adapter
- Private order/position/balance WebSocket decoder

Gate: recorded API fixtures cover empty, malformed, paginated, reconnect, and schema-wrapper cases. No order resource is imported by the running application.

## Phase 2: shadow evaluator

- Continuously calculate eligible score-seconds
- Record program phase, target cutoff, ticks, queue depth, and hypothetical order
- Estimate conservative reward from paid earnings only
- Estimate fill hazard and hypothetical completion cost
- Replay maximum-inventory-age candidates (15/30/60/120 seconds) using the
  full book at the exit time, not spread-at-fill approximations
- Require stressed depth to support a bounded exit for the full proposed size

Gate: several complete reward days reconcile predicted eligibility to venue earnings, and the lower-bound expected net value is positive after simulated exit costs.

## Phase 3: execution ledger

- Durable pre-submit intent/outbox
- Private WebSocket primary state
- REST startup/reconnect snapshots
- Idempotent fills and exact decimal positions
- One asynchronous request governor

Gate: crash, timeout, duplicate fill, stale snapshot, partial fill, and reconnect tests all pass without duplicate orders or lost positions.

## Phase 4: dry-run execution

- Desired-state reconciler
- Inventory-aware quote state machine
- Shadow-selected maximum inventory age with a soft passive exit and a hard,
  price-capped IOC exit
- Per-market and portfolio worst-case-loss breakers

Gate: a multi-hour dry run produces no path where unmatched inventory can outlive its timeout without an explicit critical failure.

## Phase 5: controlled live trial

- Exclusive handoff of the existing account/API key; maker-lab stopped
- Whole-account flat and open-order reconciliation before arming
- One local process lease and fresh private-stream state
- All executable sports markets joined to their rewards; one selected market at a time for commissioning
- 10 contracts per quote across at most 20 bound markets for commissioning
  (5 -> 10 on 2026-08-20; earlier text read "exactly one contract per quote")
- Pregame only
- Detached supervision, persisted recovery-only restart, and graceful operator
  stop
- Per-market/session loss breakers and market-scoped kill switch

Gate: the readiness evaluator must first permit a trial. Scaling then requires
paid rewards plus rebates plus trading P&L to be positive over enough independent
events. Pending rewards do not satisfy either profitability claim.
