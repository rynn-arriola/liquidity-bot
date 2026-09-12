# Prepared-change live-readiness review — September 12, 2026

Follow-up: findings A/B were subsequently repaired offline, together with the
profiled ownership-lookup bottleneck. See the [repair and performance audit](READINESS_PERFORMANCE_FIXES_2026-09-12.md).
The original review below is retained as dated evidence, not current open status.

## Verdict and scope

Reviewed prepared commit `f449df27d` against its parent and the unchanged loaded
runtime `b826ff11e`. The five repairs are directionally sound, but **withhold
live sign-off until the two entry-admission cases below are corrected**. They
were not covered by the first repair regressions. This is a review, not a
deployment, source repair, restart or completed performance implementation.

The operator requested another review before speed work and wants responsiveness
close to the earlier healthy private WebSocket. No new API rate, automatic source
switch, trading-rule change or live-control authority was granted.

Laptop runner `649372` / supervisor `649355` remains REST, size 10 / 32, with its
original 08:02Z start. A bounded authenticated report-lane snapshot at
**13:53:10Z (09:53:10 ET)** returned **30 orders, zero positions**; every order
was in a flat market. No order was created, canceled or closed by this review.
The local review checkout and GitHub match; the older WSL runtime is intentionally
not deployed, and the unreachable PC remains pending. Parity refreshed a verified
private recovery copy without activating it.

## Findings requiring correction

### A — P2: Final-entry validation can outlive its own clock

`live_commissioning.py:4082–4105` samples `now` before `_frame` and capacity
collection. `_frame` performs synchronous ledger queries, scope/basis/portfolio
work and event projection. The later age, session and deadline predicates still
use the earlier clock. A sufficiently slow local check can therefore authorize
an entry whose book has already expired by actual submission time.

Offline reproduction used the unchanged normal preflight, planner, driver,
executor and source gateways. Only the final validator's `_frame` was wrapped:
it returned its normal frame but advanced the fixture clock six seconds during
local work. **Both WebSocket and REST modes sent one fake create with a six-second
book age**, exceeding the unchanged five-second limit; neither raised a refusal.
This is injected delay, not a measured six-second production query or proof of a
live stale entry. The independent 30-second REST proof remains intact, but cannot
substitute for the shorter book limit.

Correction: collect the required local inputs first, avoid rebuilding unused
planner data at this boundary, then obtain the final current clock and evaluate
time-sensitive book/equity/event/cap authority immediately before send. Keep the
current SUBMITTING reservation counted once, existing source-generation checks
and reducer bypass. Test deadline/ET-band crossings during the validator as well
as admission waits; do not claim the client can make venue submission atomic.

### B — P2: Critical local validation errors become ordinary backpressure

`live_orders.py:266–278` wraps every non-admission `Exception` from the callback
as `ApiAdmissionDeferred`. This correctly marks the intent as NOT SENT/FAILED,
but `_handle_action_failure` then handles it as a normal short delay, before the
existing terminal-invariant branch can see its original type.

Offline reproduction injected `RuntimeInvariantViolation('unowned_working_order',
...)` at this final callback. Result: **`ApiAdmissionDeferred`,
`LOCAL_ENTRY_CHECK_FAILED`, zero creates, zero unresolved intents, and handler
stop result `None`**. No unsafe send occurred; other independent scope checks
remain and may catch persistent corruption later. The defect is lost critical
classification at this boundary, not demonstrated unowned live trading.

Correction: retain exact NOT-SENT durable intent state while preserving original
critical/integrity classification for runner escalation. Ordinary stale-proof
refusals must remain quiet short deferrals; critical failures must not be retried
as rate pressure. Simply re-raising before updating executor handling would
incorrectly turn an unsent order into UNCERTAIN, so both layers need review.

## Other prepared changes and verification

| Area | Review conclusion |
|---|---|
| R1 fill conflict | Genuine conflict propagates in both modes; existing replay/outcome enrichment remains in the ledger. No new issue found in the changed path. |
| R2 equity data age | Retaining original observation time is correct; slow or failed cash proof cannot manufacture freshness. Honest age can defer entries during slow scans. |
| R3 entry boundary | Current reservation/reducer exclusions are preserved; findings A/B need correction before approval. It is not a measured speed improvement. |
| R4 private teardown | Held results precede queued successors, valid prefixes survive a later queue error, and integrity failures still attempt shutdown. Recovery already pending avoids redundant rebind. No additional confirmed defect found in these changed paths. |
| R5 legacy report | Unattributed remainder is no longer represented as settlement/trading proof; canonical daily accounting is unchanged. Older narrative comments are not current cash authority. |
| Scope and policy | Source selection remains explicit, one ledger/executor is authoritative, REST 30 excludes positioned orders/reducers, and size/risk/exit settings are unchanged. |

Reused the exact prepared-source **1,488-test complete pass** and original
13 reversal failures; no source changed during this review. Reran the focused
review, source-control, REST, REST edge-case and order suites: **217 tests passed
in 34.547 seconds**. Additional fake-only probes above expose gaps despite those
passing suites. Probe output is diagnostic observation, not a new passing safety
regression. No new failing tests were committed to the main test suite.

The dedicated audit covered success/empty/full states, reservation boundaries,
timing, critical/unknown state, fill ownership, source return, API load, recovery,
shutdown, restart and rollback. The findings limit sign-off; this does not claim
all possible defects are absent or that simulation proves venue behavior.

## Performance recommendation after correction

Keep one shared strategy and authoritative ledger/executor for both sources.
The current architecture is a useful foundation, not yet evidence of near-old-
WebSocket performance. A healthy private WebSocket delivers event-driven account
updates; REST discovery of a change also depends on its next poll and response.
The official [private-stream contract](https://docs.polymarket.us/api-reference/websocket/private)
was reopened during this review. No authenticated WebSocket recovery probe or
source promotion was performed, and no current feed-restoration claim is made.

1. Separate immutable collection from serial projection before adding one
   in-flight account monitor. Existing `_rest_reconcile` writes the ledger and
   cannot be backgrounded unchanged. Resting entries also need monitoring,
   because their fills can occur before local position state becomes nonzero.
2. Make selection/catalog work interruptible between bounded chunks. Its
   EventAwareGateway can record event facts, so it also cannot simply become a
   second writer. Service known positions and exact pending transitions before
   continuing flat entry/discovery work, preserving fairness and exit budgets.
3. Profile local hot paths and reuse only appropriately scoped fresh proof.
   Preserve exact order/position checks on reducers and ambiguous transitions;
   do not speed up by accepting stale state, assuming cancellation or resetting
   the three-IOC/30-second authority.
4. Benchmark fill-to-observation, observation-to-builder-cancel/reducer,
   cancel-proof-to-replacement and deadline-to-first-IOC at matching workload and
   proof strength. Use replay while the private feed is incomplete; accept real
   performance only from later natural live traffic. A poll setting or generic
   decision-input-age percentile is not fill/exit latency.

Current production telemetry at 13:46:54Z reported 255 API attempts over 152.5
seconds (1.672/s), two `order_retrieve@404` failures and no 429. A separate
scheduler window reported 87,531.6 ms decision CPU and decision-input-age p95
486.002 seconds. These are different metric windows; they establish a need for
stage profiling, not an exact causal breakdown or 486-second fill detection.
At 13:44:22Z an incremental top-up committed after a logged lifecycle wait of
472.1 seconds; the field is request-to-refill timing, not position-exit delay.

Keep the shared 4/s governor and current 5/1-second targets until comparable
full-width measurements justify tuning. The official
[Retail 20/s ceiling](https://docs.polymarket.us/api-reference/rate-limits) is not
a performance guarantee or authorization to raise the project's rate. Do not add
a permanently connected inactive source or hybrid mode contrary to explicit
manual-source policy. Better scheduling can improve the bot after an observation;
it cannot guarantee that polling receives a venue change as quickly as healthy
push delivery. Speed does not guarantee rewards or profitability.

No speed implementation or live deployment is included in this review. Correct
A/B, run focused reversals and the complete suite, and repeat final edge-case
review before proceeding. Deployment and restart still require new authority.
