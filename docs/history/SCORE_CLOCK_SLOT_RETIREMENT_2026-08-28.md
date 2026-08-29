# Score-clock slot retirement — 2026-08-28

## Symptom and evidence

At 2026-08-29 01:58 UTC, authenticated state showed only 10 of the configured
20 markets quoting, 17 working orders, zero positions and zero ownership
ambiguity. The local TV rendered the selected markets without local working
orders as `OBSERVED`. The runner had recorded 34
`commissioning score clock moved backwards` failures and market retirements.

`OBSERVED` did not mean the configured capacity had changed. It meant the
market remained in the dashboard scope but the local ledger had no working
order for it. Retirement caused the missing orders.

## Mechanism

`CommissioningRunner._accrue_score` used a shared wall-clock timestamp for
score integration and raised whenever a later sample was earlier than the
previous accepted sample. The call occurs inside each market's reward/economic
service. `_service_bound_markets` intentionally contains an ordinary
market-local service exception by retiring that owner, so the score-only
exception was misclassified as a trading-service failure and progressively
consumed the slate.

All score accrual call sites were reviewed. Per-market service reads a fresh
runner clock immediately before reward evaluation; no stale loop timestamp was
passed on this route. A host wall-clock correction can still return an earlier
sample. That condition is legitimate for telemetry but must never reach market
retirement under safety invariant 72.

## Repair

The last accepted score timestamp is now a non-decreasing high-water mark. A
backward sample:

- adds zero score and leaves all ET-day pending totals unchanged;
- does not move the accepted timestamp backwards, preventing double-counting;
- resumes normal accrual only after wall time exceeds the high-water mark;
- increments an in-memory diagnostic and emits bounded power-of-two
  `[score-clock] ... action=ignored_stale_sample` log records; and
- cannot escape into market service recovery, retirement, order decisions,
  economics, risk, REST or WebSocket work.

The change does not alter the 20-slot setting, six-hour horizon, market
selection, quote size, spread/economic gates, order mutation flow, position
handling or loss limits. It adds no venue request or subscription.

## Verification

Two focused regressions cover both accounting and complete capacity behavior:

- twenty bound markets receive repeated reversed samples during the real
  `_service_bound_markets` path and remain active with zero retirements and no
  `market_service:*` error; and
- the reversed interval accrues zero, preserves the high-water mark, and the
  first later sample accrues only the time beyond it.

The repair was deliberately reversed by restoring the old exception. The
20-market regression then retired all 20 owners with
`service_failed_RuntimeError` and failed. After restoration, both focused tests
and all 801 repository tests passed. Documentation audit and deployment state
must be checked again after this record is indexed.

## Deployment state

Reviewed on the desktop only. The laptop bot remains stopped and flat on its
previous loaded source. Push, laptop parity/load, laptop tests and any restart
require the next explicit operator step.
