# Phase 4 acceptance

Implemented and tested on 2026-08-12 ET without network mutations.

## Reconciler behavior

- Flat, known, fresh, pregame state can request small post-only reward quotes.
- Unknown execution state or stale books produce a critical fail-closed result.
- A first fill cancels every building order immediately.
- The reducing quote works at the active touch even when that realizes a small
  bounded loss; it does not park only at profitable prices.
- At the hard timeout all remaining orders are canceled first.
- A price-capped IOC is requested only after cancellation is confirmed and the
  stressed full book can close the entire position inside the exit-loss budget.
- Unconfirmed cancellation beyond grace and inventory remaining after an IOC
  both produce explicit critical failures.
- Per-market and portfolio worst-case-loss breakers stop new risk.

## Accelerated multi-hour acceptance

Each candidate timeout ran for four logical hours with alternating long and
short fills:

| Timeout | Entry fills | Hard IOC exits | Max observed age | Criticals |
|---:|---:|---:|---:|---:|
| 15s | 48 | 48 | 15s | 0 |
| 30s | 48 | 48 | 30s | 0 |
| 60s | 48 | 48 | 60s | 0 |
| 120s | 48 | 48 | 120s | 0 |

Orders sent to Polymarket: **0**.

This validates state-machine safety mechanics. It does not select a live
timeout or demonstrate profit; Phase 2 evidence must do that.
