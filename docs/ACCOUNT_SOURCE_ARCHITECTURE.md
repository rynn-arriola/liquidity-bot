# Permanent account-source architecture

Design prepared September 12, 2026 against `c44d3aef1`. The operator authorized
permanent configurable fallback, requested architecture first, then rejected
automatic switching in either direction. This is the
implementation blueprint, not deployed behavior. Implementation remains pending;
the prior completed restart does not activate this design.

The design-session read-only snapshot at 06:15:39Z returned 30 venue orders
across 21 markets and zero venue positions. Subsequent local counts within the
same non-atomic read were 30 working orders, one position, one unresolved intent
and zero unknown orders. This is a changing-account observation, not whole-account
flat/clear proof or evidence that a position closed. No trading process or
runtime source/configuration was changed in this design session.

## Decision and modes

Use one trading engine, one ledger writer and one mutation executor. WebSocket
and REST readers feed an account-state coordinator. Readers never place/cancel
orders, write the authoritative ledger, reset timers or decide strategy. Public
full-book WebSocket remains independent.

Modes: `websocket` and `rest`; keep `rest-fallback` as a compatible alias.
There is no automatic source selection, failover or failback. The launcher's
existing `auto` market-selection argument is unrelated and remains supported.
Explicit CLI selection overrides validated startup configuration. Status shows
selected source and pending operator switch. The inactive source stays closed
during normal trading; only an explicit bounded switch request may start a
candidate. Same-source transport reconnection remains automatic.

## Measured basis and corrections

- `RestFallbackRunner` already shares the strategy through inheritance, keeps
  public books, optimizes unchanged flat-order proof and prioritizes positions.
- `_rest_poll_due` measures 5 normal / 1 active seconds after completed inline
  reconciliation. Read, queue and processing duration add to that interval.
  A resting entry can fill while the local position is still flat; accelerating
  only already-known positions does not bound initial fill detection.
- The 06:04:28Z live window logged 419 attempts over 327.6 seconds, 1.279/s,
  zero failures/429s and decision-input-age p95 433.889 seconds. This establishes
  delay at low average API use, not an exact fill/exit delay or CPU attribution.
  Profile stages before deciding which processing path to optimize.
- Preserve the loaded exact-owner hard-exit proof repair throughout extraction.
  [Incident/reversal evidence](history/REST_BULK_CONTINUITY_2026-09-11.md).
- Retail documents 20 authenticated requests/s per key and 20 public requests/s
  per IP. The coordinated host ceiling remains 4/s; earlier endpoint-specific
  429s occurred at lower averages. The proposed 8/s ceiling and 0.5-second target
  are unaccepted tuning experiments, not defaults or latency guarantees.
- Post-only prevents taking liquidity at submission, but does not guarantee a
  favorable fill or protect a resting stale quote from adverse selection.

## Boundaries and migration

| Component | Responsibility | Existing code to retain/extract |
|---|---|---|
| WebSocket reader | Scope, per-shard health, ordered events, bounded reconnect | `streams.py`, `private_stream.py`; retain eight-market private shards |
| REST reader | Bounded complete snapshots, exact reads, history coverage, timing | `account_snapshots.py`, read portions of runner and REST subclass |
| Account coordinator | Source selection, handoff generation, validation, serialized ledger projection and proof invalidation | `venue_state.py`, `live_session.py`, current reconciliation |
| Position service | Fair urgent wakeups, pending transitions and durable exit deadlines | Existing priority/reducer-continuity machinery |
| Trading engine | Selection, economics, prices, quantity, caps, risk, lifecycle and actions | `CommissioningRunner`, reconciler, `LiveOrderExecutor` |
| API scheduler | Host admission, lane priority, bounded concurrency and backpressure | Shared governor in `adapters.py` |

Extract account-source behavior behind composition in small behavior-preserving
steps. Existing runner classes may remain compatibility wrappers during
migration; do not create separate WebSocket/REST copies of strategy logic.

Readers publish immutable observations with source/generation, request and
market scope, request start/completion, receipt time, venue time when supplied,
exact identities and pagination/completeness evidence. Receipt time never
substitutes for venue publication freshness. One consumer validates and applies
observations. No shared venue sequence or atomic cross-endpoint snapshot is assumed.

Generation changes invalidate old reader authority, but never discard a valid
delayed fill or terminal event solely because its connection is obsolete.
Executions remain idempotent by durable identity. Older snapshots cannot replace
newer exact positions; uncertain ordering requires reconciliation. Preserve
request scope, snapshot page boundaries and task-held event order.

Today's `_rest_reconcile` mutates ledger/risk state across awaits: it cannot be
moved unchanged into a background task. Separate collection from projection
first. Only independent reads overlap. Bounded queue overflow revokes proof;
never silently drop/coalesce executions. Public books may coalesce by market
while preserving actual timestamps. There is no second bot or mutation worker.

## State transitions

| State | Entry authority | Transition |
|---|---|---|
| Initializing / reconciling | None | Selected source plus complete account/risk proof |
| Selected source active | Existing gates; REST adds proof and entry cap | Operator request starts target verification |
| Verifying requested source | Healthy selected source remains authoritative | Verified target and safe decision boundary permit handoff |
| Switching | New entries paused; exact safety work has priority | Commit after proof; failed/stale request aborts |
| Selected source unavailable | None | Same-source recovery; another source requires an operator request |

Use account-wide switching. One failed private shard removes primary WebSocket
readiness until same-source recovery or an operator-requested replacement is
proved. Public-book failure has
independent price-freshness/recovery gates; switching private sources cannot fix it.

Source failure revokes new-risk authority and retains existing recovery behavior;
it never issues a switch request. A requested handoff pauses new-entry admission
at commit, retains all durable intents and performs complete REST reconciliation.
The same planner resumes with existing positions, orders, quantities, inventory
ages, IOC budgets, ET HARD state and reservations. Unknown sends are never retried.
Switching alone does not cancel healthy orders or require flatness. Failed proof
uses existing quarantine/recovery; source selection cannot bypass integrity errors.

Only an unexpired operator request creates a bounded candidate. Verification
shares the API governor; a failed request leaves no permanent probe running.
A WebSocket candidate must deliver genuine private state from every shard and
validate against fresh REST during a bounded verification window. Ping/Pong alone
cannot promote a silent candidate. Preserve the reviewed empty-subscription
exception: a shard's real balance delivery and complete REST can prove empty state
when no order EOF is emitted. Partial snapshots or nonempty expected state require
complete positive proof; never fabricate a missing event.

At promotion, freeze decisions, consume a task-held earlier event before draining
later events, complete reconciliation, recheck unchanged source generation/scope,
stop and health, then commit the source atomically. No handoff commits concurrently
with a mutation submission. Urgent position work defers/aborts the handoff so the
existing exit manager can continue. Failure leaves the selected source active
if healthy; otherwise its existing recovery hold remains. Source recovery
cannot clear maintenance, operator pause, HARD, daily loss tiers or entry restrictions.

Idle business-event silence is normal. Distinguish transport health from missing
private changes using routine REST audit and outstanding-order confirmation
deadlines. Allow bounded catch-up/recheck for ordinary races; persistent unexplained
disagreement removes authority. Frozen successful REST without a trustworthy
publication watermark cannot always be detected; do not claim otherwise.

A switch request includes a unique ID, expected source/generation, target,
creation time and expiry. Consume it once; stale/duplicate/wrong-generation
requests cannot switch. Persist only a successful selection for future startup;
never replay an unfinished request after restart. Stop supersedes every request.
This control path has no independent venue client or mutation authority.
Report requested/verifying/completed/failed once with a concise reason.

The target design switches within the running process and retains healthy
orders. Verification can briefly pause entries or defer for exits, so zero
latency impact is not promised. Steady state runs only the selected private
source. Operator intervention is required to change sources after an outage.

## REST responsiveness and configuration

1. Watch resting entries as potential fills. Share one complete account polling
   job among consumers; permit at most one in flight and never stack missed polls.
2. Process completed observations and wake affected owners promptly. Keep
   position transitions and durable deadlines ahead of flat-entry work. Catalog,
   reward and report work yields between bounded chunks; do not wait for a full
   market scan to service exposure. Long CPU work requires profiling before repair.
3. Reuse fresh immutable proof only for its exact fields/scope. Relevant mutation,
   observation, source/scope change, expiry or failure invalidates it. Scoped exit
   proof cannot grant entry authority or renew account-wide equity freshness.
4. Keep one mutation executor, bounded read concurrency and fair rotation among
   urgent owners. An illiquid position cannot consume another owner's attempts.
   Original three-IOC/30-second authority and durable exit clocks remain binding.
5. Start with 5/1-second targets, four read workers, shared 4/s and the 30-entry
   REST ceiling. Zero positions plus resting orders is not an idle account.
   Faster fill-watch cadence requires measured full-width load and response tails.
6. Measure actual fill-to-observation where venue time is trustworthy,
   observation-to-cancel/reducer, cancel-confirmation, confirmation-replacement,
   deadline-first-IOC and final-flat-proof latency. Record p50/p95/max, missing
   coverage, read/queue/processing time and endpoint counts identically in both modes.

Validate mode, polling targets, read concurrency, switch-verification bounds and
REST pending-entry ceiling once at startup. Effective cap is the minimum of clock,
durable loss, configured capacity and active REST ceiling. Open-position orders,
reducers, IOC, recovery and shutdown are excluded. Switching from a wider
WebSocket slate blocks additions immediately and drains only proven excess flat
entries using existing reward-ranked, pair-preserving cancellation. Returning to
WebSocket cannot reset the loss ratchet. Do not hot-reload during position handling.

## Delivery and acceptance

First extract source collection/projection contracts and replay both existing
modes to prove behavior parity. Next implement the coordinator and handoffs using
fake feeds with the real ledger/planner/executor. Then introduce asynchronous
collection and proof reuse after ordering, single-writer and stale-result tests
pass. Profile comparable workloads before selecting later cadence tuning.

Mandatory scenarios: zero/30/full-width orders; long/short/partial/tiny positions;
fills during create/cancel/switch; timeout after POST; delayed/duplicate/busted
fills; missing rows and partial pagination; one dead shard with healthy Pongs;
legitimate idle silence; scope rotation during probe; queue overflow; disconnect
during promotion; stop at every await; maintenance/HARD/ET rollover; both sources
down; multiple urgent owners with one illiquid market; restart/rollback with
durable unknown intents and original position-open clocks.

Reject any duplicate mutation, over-exit, entry from stale/partial proof, lost
ownership/reservation, reset deadline, unbounded queue/read amplification or
background-induced safety starvation. Preserve existing regressions and perform
deliberate reversals, full-suite and final review before push. Performance evidence
must improve end-to-end timing at comparable proof strength at full width, including
busy/slow-response/recovery cases. Setting an interval is not acceptance.

Deployment/restart follows normal reviewed release and account gates. Live
acceptance uses natural traffic without induced fills/outages. If the private
stream stays silent, REST acceptance can complete while live WebSocket handoff stays
explicitly pending; simulation does not establish venue behavior.

## Official scope and limits

Retail [private stream](https://docs.polymarket.us/api-reference/websocket/private)
and [rate limits](https://docs.polymarket.us/api-reference/rate-limits) were reopened
September 12. The changelog was reopened; the public status fetch failed, so no
new outage or recovery claim is made. Retail batch-create IDs do not prove exchange
outcomes, so batching does not replace confirmation. Institutional FIX/gRPC access
is not assumed for this Retail account.

Stable boundaries and replayable incidents reduce future rework. They cannot
guarantee that the venue never changes or that every exit has available liquidity.
