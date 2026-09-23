# Permanent account-source architecture

## September 22 addition: optional hybrid mode

The operator authorized private WebSocket assistance with permanent REST
fallback. `hybrid` is an explicit restart-scoped mode; the existing `rest`
and strict `websocket` modes retain their contracts. The earlier prohibition
on a permanent inactive listener does not apply inside this opted-in mode.
Nothing automatically changes or persists a different selected mode.

One global private subscription set feeds the same serial ledger projector.
Order updates use the existing exact order/fill identities and deduplication.
Snapshots and position notifications only schedule complete REST proof: they
cannot infer absent orders, flatten inventory, or guess a missing market ID.
Balance messages are not required and cannot grant account readiness.
REST still seeds startup, proves positions and balance, backfills executions,
audits the whole account, and supplies exact order state when no private proof
arrives. Public books and every trading/risk/exit rule are unchanged.

The optional listener owns no REST call or ledger writer. Connect/close waits
are bounded; failure reconnects in the background with capped exponential
backoff while the existing REST runner continues. A healthy quiet socket does
not certify subscription delivery. The notification queue is bounded and
backpressures; upstream overflow becomes an explicit gap requiring REST.
Task-held observations precede queued observations even when REST service
drains them before the outer loop consumes its task result.

September22 reducer repair preserves the REST cancel-confirm-replace contract:
a completed exact-owner proof (complete account orders/positions/history included)
can discharge the applied-hint veto once for its exact reducing intent. It cannot
renew global entry authority, bypass the usual REST/driver gates, survive another
observation/proof or pending frame, or authorize a different quantity/direction.
Proof age, source generation, scope and account ambiguity are rechecked at final
admission. Entries still require global proof. Positively owned, unchanged-cumulative
NEW/CANCEL acknowledgements retain normal polling cadence; fills, incomplete fields,
unknown event types and position/snapshot hints do not. Old fully-private mode did
not globally revoke readiness for every order ACK; this restores that distinction
without treating the incomplete current stream as authoritative position coverage.

An unapplied observation or a projected hint without subsequent REST proof blocks
order-create admission, including reducers whose exact size could have changed.
The narrow scoped reducer proof above does not clear the global hint requirement.
Cancels remain available. Normal hints retain the prior
valid REST certificate only for the existing unchanged-flat-order bulk proof;
transport/schema/integrity/read failures revoke it. This avoids turning every
notification into a full-slate exact-order scan. Mandatory REST freshness and
the configured 5/1-second targets are not relaxed. Invalid wire messages fall
back; identity/ledger conflicts retain critical handling.

Use `./scripts/start_commissioning.sh auto 15 32 hybrid` only after an
authorized reviewed deployment and ordinary flat/clear startup gates. Disabling
uses the same workflow with `rest`. Live in-process switches involving hybrid
are refused in this release; the existing REST/WebSocket handoff is unchanged.
Configuration defaults and the persisted live selection are not changed by
implementation. `[hybrid-account]` telemetry distinguishes listening, receiving
and REST fallback and counts notifications/gaps without new API calls.

Offline acceptance covers exact REST fallback, a saved lookup from positive
terminal evidence, duplicate/partial fills, task-held ordering, snapshot
non-authority, admission races, backpressure, reconnect and stop. Actual REST
savings and latency need comparable live evidence; the previously suggested
2.5–2.9 requests/second is not a measured or guaranteed result.

The original architecture and historical release descriptions follow.

Design prepared September 12, 2026 against `c44d3aef1`. The operator authorized
permanent configurable fallback, requested architecture first, then rejected
automatic switching in either direction. This is the
implementation blueprint. The September 12 implementation adds the shared runner,
explicit verified handoffs, immutable independent REST reads and an indexed hot
lookup. Deployment/live acceptance are recorded only in STATUS.md and the
[release audit](history/ACCOUNT_SOURCE_RELEASE_2026-09-12.md).

The first release overlaps independent positions/history reads inside the existing
serial proof cycle; it does **not** move reconciliation or decisions into a second
writer/background worker. A fully independent polling watch, chunked catalog work
and cross-source end-to-end latency percentiles remain follow-on measurement work.
There is no claim that a one-second target guarantees one-second fill detection.

The September 13 load follow-up keeps mandatory history in that same proof but
uses the documented newest-first order plus durable execution IDs as a bounded
overlap anchor. Stable, flat, unambiguous proofs may stop on an all-known first
page between 30-second deep audits. Startup/recovery/shutdown, open positions,
ambiguity and new execution bursts remain deep and fail closed. This is request
reduction only; it does not cache account authority or change polling cadence.

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

Shared public-stream failure may surface through a synchronous book drain or
freshness read before its background reader completes. `MarketStreamUnavailable`
must yield to the outer recovery owner, not retire each market and repeat account
proof for every slot. The owner joins old readers and applies held private evidence
before queued evidence, then runs coordinated rebind/account proof. A pending
handoff bars another decision sweep; failure never opens whole-slate REST-book
fallback or promotes a different account source. Market-local book errors retain
their existing scope. [September13 repair/evidence](history/SHARED_STREAM_RECOVERY_AND_REST_LOAD_2026-09-13.md).

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

The [September 12 read/exit follow-up](history/REST_THROTTLE_EXIT_SCHEDULING_2026-09-12.md)
adds a REST-only early pass for due/uncovered, non-isolated positions not blocked
by an unknown intent or owner backoff. It uses the unchanged service/proof/action
path and cannot become a flat entry pass. Periodic equity work coalesces only a
complete same-day mark younger than five seconds and retains its original next
due time; a failed equity bracket is attempted only once per decision sweep.
Data ages, cash classification, HARD and source-switch proof are not renewed by
this scheduling. Deployment/live acceptance remain separate operator decisions.

The [quiet-book follow-up](history/JUST_IN_TIME_BOOK_SERVICE_2026-09-12.md)
keeps bulk lifecycle work but services ordinary quiet books at the owner
decision, after account/equity proof. This avoids aging early slate reads before
they are used. Economic-replacement dwell retains its normal book checks, and
fast pause cancellation adds no price read. The three-second quiet threshold,
five-second book age limit and original stream receipts remain unchanged in
both account modes. Startup, shutdown and selection keep explicit validation.

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

September 12 performance checkpoint: the measured identity-lookup scan is fixed
offline by an additive ordered intent index, benefiting both sources without
new requests. This is not the independent account-watch/catalog implementation.
Current normalization may adopt an unresolved intent through its identity resolver
and therefore write the ledger; raw fetches must be separated from that projection
before background collection. [Measurements and remaining work](history/READINESS_PERFORMANCE_FIXES_2026-09-12.md).

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

## Prepared scan-watch and timing follow-up

The [September 12 follow-up](history/ACCOUNT_WATCH_EXIT_REVIEW_2026-09-12.md)
implements bounded checkpoints during incremental candidate discovery and a
single-flight REST position watch in that scope. It is a wakeup only, never a
substitute for full order/position/history proof. WebSocket uses serial private
event draining instead. Concurrent history/order/candidate collectors cannot
adopt identities; projection stays on the owner. The existing shared rate,
5/1-second targets, disabled-optimization five-second cadence and manual source
selection remain. There is no always-running second full-account reconciler.

`[execution-timing]` JSON is evidence only. Compare source/action/reason and
`observation_kind` separately; initial/restarted observations are not confirmed
fill-delivery times. Report sample counts and null coverage with any percentiles.
`deadline_offset_seconds` is signed and only present when the existing deadline
can be recovered; acknowledgements are not proof of execution or flatness.
Live measurements require the prepared code to be deployed with operator authority.

## Implemented operator control

### Account-read load and reducer continuity

The [September13 account-load review](history/ACCOUNT_READ_AND_REDUCER_LOAD_2026-09-13.md)
retains the existing single owner and single-flight watchers. It adds endpoint
429 backoff shared across lanes/processes, with one recovery probe instead of
inline retries. The immediate priority decision can consume a successful exact
passive cancel-confirmation once, with strict age/version/scope/source checks;
equity remains independently checked and entry authority is not renewed.
Exact reducers already at the active touch are retained, avoiding self-induced
cancel/repost churn. No always-running second full-account reconciler, broader
cache authority, polling increase or automatic source switch is introduced.
This reduces avoidable requests, not the venue's ability to rate-limit. Runtime
deployment and live acceptance are tracked in STATUS, not implied by this design.

### Source selection

The launcher accepts `websocket`, `rest` or compatible `rest-fallback` as its
fourth argument. If omitted, it loads the last successfully verified selection;
without a saved selection it uses validated `config/account_source_policy.json`.
An explicit startup argument wins. Failed startup cannot replace that explicit
choice with an earlier process's saved selection. Configuration is restart-scoped.

On the active laptop, run `PYTHONPATH=src .venv/bin/python -B -m liquidity_bot.account_source status`
to read local control status, not venue proof. The same command with `rest` or
`websocket` instead of `status` submits one explicit request; operators must have
authorization before invoking it. Requests expire in five minutes and bind to one
process/source generation. WebSocket verification lasts at most 45 seconds by
default; final account proof has a 30-second deadline. No new order or cancellation
is submitted by the handoff itself. Urgent exits, partial/disagreeing state or a
changed scope abort the request, requiring a new operator request. Regular private
recovery services the control checkpoint; startup without a bound market and
prolonged venue-health holds do not bypass their existing gates to service a switch.
A stale status refuses the request.

The inactive private WebSocket is closed in REST steady state. Returning to REST
retains positive old-stream tail evidence and expires pre-tail proof. Status files
and selection/request controls live beside the active ledger under ignored `data/`;
they contain no credentials and are not synchronized as live authority.
