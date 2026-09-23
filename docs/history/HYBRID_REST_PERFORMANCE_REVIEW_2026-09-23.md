# Hybrid versus stable REST: scoped performance review, September 23

## Scope and live boundary

The operator asked to verify the current hybrid repair as an upgrade of the
running REST version, focusing only on affected paths. Work stays on
`work/hybrid-account-review`, following `8c7cc1d93`. No live stop, deployment,
restart, account-source switch, venue mutation or risk-setting change is made.
Stable REST is preserved by `stable-rest-2026-09-23` at `6deb707c6`; the original
hybrid trial remains saved by `saved-hybrid-2026-09-23`.

A process-only read during review confirms supervisor23308/runner23326 still
running size15/32 in explicit REST. WSL HEAD is `acba287f5`, deployed marker
`6deb707c6`. No new authenticated exposure read was needed or performed for this
offline task; current order/position counts are not certified here.

## Additional defects and repairs

The prepared terminal-wave/gap coalescing fix did not cover catalog checkpoints.
Those checkpoints aborted discovery on **any** queued hybrid event, even an exact
unchanged order ACK or optional disconnect. Repeated harmless notifications could
therefore discard useful scans, repeat discovery reads and delay refilling slots.
This is a reproducible code defect, not measured attribution of earlier live churn.

Now the serial owner drains/classifies events first and interrupts only while
substantive uncertainty requires reconciliation. Previously applied uncertainty
also interrupts with an empty queue. The REST position watcher remains active;
stop, generation, scope, position-change and exit-deadline checkpoints remain.
No snapshot absence becomes authoritative and no order is sent by discovery.

Review removed an over-broad draft gate-expiry check: read-only discovery may
outlast30 seconds exactly as in REST, but cannot renew the account gate. Final
selection/admission still requires the existing fresh authenticated proof.

A second edge case allowed the base periodic refresh to skip its reads because
the next poll was not due, then the hybrid wrapper cleared its uncertainty flag
as though proof had completed. Hybrid now preserves that flag on this cadence
no-op. A regression demonstrates zero reads and a blocked create before the
deadline, followed by full orders/positions/history proof and normal release
when due. It failed before the fix and again with the guard deliberately removed.

## Controlled comparison

`tests/test_hybrid_rest_comparison.py` runs identical fake-venue workloads through
the permanent runner in REST/hybrid modes. No credentials, production database or
network are used. SDK call counts exclude initialization. Fake-clock timings
measure scheduling only, not network, limiter waits, CPU or venue latency.

| Workload | Stable REST behavior | Prepared hybrid behavior |
|---|---|---|
| 30 resting orders,10 simulated seconds | 8 account reads | Same8 when quiet, ACKs arrive, or listener disconnects |
| 30 terminal cancellations before next poll | 30 exact order reads | Zero exact reads; complete account proof still required |
| One reducer cancel/replace | 4 account reads | 3 reads; same price, size, direction and exit-first sequence |
| Newly visible position at second1 | Discovered at scheduled second5 | Hint wakes complete REST proof at second1 |
| 30-step discovery with ACK/gap traffic | Completes with position watch | Completes with same read count and watch |
| 60-second read-only discovery | Completes; account gate expires | Same; no gate renewal or mutations |

Two further safety cases verify already-applied/queued uncertainty interrupts
discovery and an older REST row still requires exact retrieval after a newer
private order observation. The latter initially looked like extra ACK traffic,
but the test had invented a later private timestamp. That conservative retrieval
is necessary; the implementation was not weakened to suppress it.

All nine comparative/regression tests pass. Restoring the former queue-only catalog veto
causes the ACK discovery test to fail with `LOCAL_CATALOG_ACCOUNT_CHANGED`.
Both repairs were restored before final verification. The exact final executable
and tests passed all2,067 tests in346.250 seconds using the production WSL virtual
environment against this isolated worktree. Documentation and whitespace audits
pass. Source/config/launcher comparison confirms main still matches the stable
REST tag; the hybrid changes remain on the separate review branch.

## Dedicated edge-case and architecture audit

- Authority/ownership: REST still seeds startup and supplies complete positions,
  history, balances and account proof. Durable serial order/fill projection,
  duplicate suppression, foreign-order faults and exact residual size remain.
- Queue/races: bounded queue, held-prefix order, serial drain and final pre-send
  pending-message veto remain. A frame arriving during proof cannot authorize a
  stale reducer. Single-use scoped proof cannot release global entries.
- Failure/recovery: quiet or disconnected optional stream keeps REST polling;
  malformed data, fills, position changes and changed order facts still demand
  proof. Existing cooldown/backoff/admission rules are unchanged.
- Scheduling/load: discovery no longer restarts for harmless notifications;
  REST watch still catches missing private positions. Urgent exit deadlines and
  account changes still interrupt. New discovery logic is hybrid-only.
- Full/empty/boundaries: comparison uses30 orders/32 scope, no-position cases,
  reducer replacement, empty queue with prior uncertainty, and scans across
  account-proof expiry. Existing hybrid regressions cover partial fills, failed
  cancel proof, unknown ownership, hard-exit continuity and late events.
- Lifecycle/rollback: no auto-switch or new durable schema; hybrid remains an
  explicit reviewed restart choice. Stable runtime/tag stays untouched. Stop,
  shutdown and source-generation fences remain in place.
- Business policy: no strategy, reward ranking, price, size, pending cap, risk
  window, settled-loss rule, exit timer or public-book change.

## What is not proved

This is evidence of targeted savings and faster notification-driven discovery,
not a guarantee of lower total requests or faster every-path live performance.
Actual fill notifications intentionally bring proof forward; stale REST rows,
unknown state and event arrival during final admission can require more work.
The old live samples were not controlled and both had four429s. Their differing
mutation counts do not prove extra proof work caused quote churn or trading P&L.

After separately authorized deployment, compare matched windows for endpoint
reads/429s, mutations, scan completion, fill-to-reducer/exit timing, coverage and
resting time. No induced fills or live rate-limit stress test. Do not widen caps
or relax proofs to manufacture performance improvement.
