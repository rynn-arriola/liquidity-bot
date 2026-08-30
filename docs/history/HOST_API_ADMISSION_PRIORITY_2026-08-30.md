# Host API admission priority repair — 2026-08-30

## Scope

This repair followed the 20- and 22-slot Cloudflare `1015`/HTTP 429 evidence.
It changes only local request admission, retry timing, process coordination and
telemetry. It does not change market selection economics, quote size, slot
capacity, exposure, loss limits, exit prices or mutation retry policy.

## Verified defects

1. A background 429 wrote one global cooldown, so a selection/report response
   could delay account proof, cancels and IOC exits for as long as 60 seconds.
2. A passive GTC reducing quote was classified from TIF alone and therefore
   used maker-entry priority even though `OrderRequest.reducing` was known.
3. Background scans and maker creates shared the same 2/second lane. A large
   scan could place each entry behind its entire request burst.
4. Cached post-flat revalidation used background pace even though it is the
   bounded path intended to refill a newly available entry slot quickly.
5. confirmed-429 state registration performed directory, file, `flock` and JSON
   writes synchronously on the asyncio event loop.
6. A worker slept for the full shared delay while the process asyncio lock was
   held. The wait was not interruptible by operator stop.
7. Native Windows silently skipped POSIX locking and could create another
   authenticated request budget on the same key and public IP.

The review also found a second-order serialization defect: merely releasing
the limiter lock was insufficient because the runner itself is serial. An
inline background retry or later 60-second background/entry admission wait
would still prevent the runner from reaching unrelated safety work.

## Implemented contract

- Every cooperating WSL process consumes one file-locked aggregate 4/second
  clock.
- Background selection and reporting have a 2/second lane. Maker entry and
  cached revalidation have an independent 4/second lane. Account proof,
  cancels, passive reducers and IOC exits use the 4/second safety lane.
- A confirmed 429 cools only the class that received it. Background and entry
  retain adaptive bounded cooldown; safety is capped at one second.
- Non-safety reads publish cooldown and return promptly instead of retrying
  inline. Later background/entry work defers locally before network IO while
  more than one second of actual cooldown remains.
- A locally deferred maker entry is durably `FAILED`, not `UNCERTAIN`, because
  no request was sent. It produces no runtime error or market quarantine and
  gates later entry plans for the remaining cooldown before they create another
  intent, then may be reconsidered by a later normal pass.
- The cooldown component is tracked separately from normal pacing. A valid
  sub-1-request/second configuration may wait over one second without being
  mistaken for cooldown and therefore cannot deadlock itself.
- Shared claims and 429 writes run in worker threads. Neither the POSIX lock nor
  the process asyncio lock is held while sleeping.
- The first operator stop interrupts pre-existing limiter and read-retry waits.
  Later shutdown account proofs, cancels and exits capture a fresh generation
  and remain available. Any mutation interrupted during admission is proven
  unsent and closes its durable intent as `FAILED`, not submitting/uncertain.
- Native Windows authenticated executable construction fails closed; production
  clients must run inside WSL where `fcntl` coordination exists.
- Mutations remain single-attempt and never auto-retry. Reducing priority is
  passed out-of-band and never added to the venue payload.

## Dedicated edge-case audit

| Dimension | Reviewed result |
|---|---|
| Success | Every admitted request claims the aggregate clock once; telemetry records the matching traffic class. |
| Failure | Confirmed 429 records only its lane; non-429 behavior and structured adapter metadata are unchanged. |
| Empty/full | Zero pending work adds no request; full selection fan-out remains background-paced and cannot occupy entry's lane clock. |
| Boundary | Safety `Retry-After` is bounded to one second; non-safety cooldown is bounded to 60 seconds; exactly ordinary long pacing is not cooldown. |
| Timing/race | Claims recheck after sleeping, so another coroutine/process may safely win the next slot; no reservation is assumed. |
| Partial/unknown | A pre-network deferral is exact failure; any exception after a mutation call remains uncertain unless the response proves rejection. |
| Ownership | No ownership, reconciliation, absence or foreign-order rule changed; deferred entries never create an unresolved owner. |
| API/load | Aggregate remains 4/second, background remains 2/second, and entry/safety priority does not create extra aggregate capacity. |
| Recovery | Later cadence retries optional work; safety reads retain a short inline 429 retry and existing fail-closed proof. |
| Shutdown | First stop wakes old waits; shutdown calls use the new generation and retain safety admission. |
| Restart | The new state keys are additive. Old global cooldown keys are ignored; no database or config migration is required. |
| Rollback | Reverting the source restores the old state interpretation; the additive JSON keys are harmless to the prior reader. |
| Capacity | Slot count and risk budgets are unchanged. At 22 slots, REST-only full-book freshness remains impossible inside five seconds at 4/second. |

No credential, host address, webhook, private key, database or venue mutation
was used in this review.

## Verification

- Focused adapter/order/selector/runner suite: **445 passed** before the final
  boundary refinement.
- Final complete local suite through the production-matching WSL Python:
  **918 passed in 89.608 seconds**.
- Deliberate reducer-priority reversal failed with `entry != safety`.
- Deliberate shared-cooldown reversal failed when a background 429 imposed
  approximately 30 seconds on entry.
- Deliberately restoring the process asyncio lock around shared file admission
  failed because a concurrent safety claim could not enter.
- All three reversals were restored before the complete suite.

Deployment and live acceptance are separate. This document does not authorize
or claim a process restart.

## Deployment

Commit `ecfc662a` was pushed before deployment, fast-forwarded into the clean
WSL checkout and recorded in `DEPLOYED_COMMIT`. The exact deployed checkout
passed all **918 tests in 69.541 seconds**. Supervisor PID 290240, runner PID
290252 and dashboard PID 116618 remained unchanged; the runner therefore still
has `821f5c85` loaded. No process was signaled or restarted, and no venue API
request was made for deployment acceptance.
