# Covered-fill REST recheck diagnosis — September24,21:50Z

## Scope and outcome

Operator asked to double-check whether WebSocket/REST timing overlap causes
extra requests. Diagnosis only; no source/configuration changes, deployment,
restart, API probe, live event injection or new collector. Runner420980 and
loaded `ebb2f480d` remain active. No fresh authenticated exposure query;
the last venue snapshot is historical, not current account proof.

Confirmed a reproducible extra-check path: a WebSocket fill already fully
covered by a fresh successful REST proof immediately triggers another complete
orders/positions/history collection. No new execution, position or working
order is produced by that duplicate fill. This is notification scheduling
duplication, not double-booking the trade and not WebSocket being too fast.

The prior [live trace](PERIODIC_URGENT_CHECKPOINT_2026-09-24.md#actual-live-flow-observation21392144z)
shows adjacent REST checks after a private fill and stale private events after
REST projection. It does NOT log every branch condition or raw private payload.
The replay establishes a real mechanism, not that it explains every adjacent
live check, all increased traffic, financial failures or the venue429 threshold.

## Exact code path

1. `AccountSourceRunner._hybrid_order_needs_immediate_proof` returns true for
   every FILL/PARTIAL_FILL before checking whether that execution already exists
   and is covered by a completed account proof.
2. `_observe_private` classifies urgency before the existing serial projector
   applies the event. The ledger correctly deduplicates an already-known fill;
   stale order data cannot overwrite newer data.
3. The hybrid handler nevertheless retains its precomputed urgent classification,
   sets `_hybrid_requires_reconcile` and clears `_rest_poll_completed_at`.
4. `_refresh_execution_state_for_decision` consequently collects orders,
   positions and history immediately despite a fresh proof and no elapsed poll
   interval. Ordinary REST mode without the notification would do zero reads
   at that same instant.

This is separate from same-sweep receipt reuse and financial-bracket sharing.
Removing the account source or weakening unknown-state gates is not the remedy.

## Offline reproduction and controls

Ran3 focused cases using existing `HybridRestComparisonTests` fixtures, real
runner/projector/ledger code and fake client methods with temporary ledgers.
Installed production Python interpreter; no credentials or production database.
All3 passed in1.161s. Diagnostic script is machine-local
`.local-tools/check_hybrid_overlap.py`; portable recipe/results are below.

Construct one owned10-contract BUY at0.49. Move fake venue order to FILLED,
add the matching immutable execution/trade and exact+10 position. At event
time+1s perform complete REST proof, then hold time fixed below active cadence.

| Case | Subsequent reads | Result |
|---|---|---|
|Fresh REST proof, no new message|0|Position+10, one fill, zero working orders|
|Same covered fill delivered after proof|orders1,positions1,history1|Same account facts; urgent counter increments|
|Same covered fill queued during proof and consumed afterward|orders1,positions1,history1|Same account facts; urgent counter increments|
|Genuinely new fill, not yet REST-proved|orders1,positions1,history1|Required proof discovers/confirms+10 exactly once|

Every case asserted zero creates/cancels. Covered-fill cases assert the gate
was fresh, polling not due, fill count/quantity/order state unchanged afterward.
The during-proof case queues the notification after the fake positions response
has already included that exact fill; it is not a race with unknown new exposure.
Counts are SDK fake-client reads for this scenario, not a live savings forecast.

## Recommended repair boundary (not implemented)

Recognize only positively covered duplicate notifications using immutable
execution identity/content and complete recent order/position/history coverage.
Do not decide solely from an old order timestamp, matching quantity, a stored
execution ID, queue age or `stale_order_event`: those alone can hide a new fill,
fee/trade linkage/bust or conflicting account evidence.

Keep serial projection and conflict checking. Do not renew proof timestamps,
clear an earlier unrelated reconciliation requirement or skip queued new events.
Genuinely new/changed/unknown execution, newer order state, failed/expired proof,
reconnect and ambiguous ownership retain existing REST verification. Preserve
polling cadence, financial checks, settled-loss rules, entry caps and exit priority.
Regression coverage should include duplicate partials/terminals, missing or
conflicting trade metadata, changed fees, stale envelope with unseen execution,
another market changing during proof and stop/source-generation boundaries.

Implementation and any restart remain separately authorized. The recommendation
targets a demonstrated redundant read path; it does not promise elimination of429s.

## Authorized implementation (prepared, not deployed)

Subsequent operator request "Okay lets fix this" authorizes this narrow repair,
not deployment/restart or any trading-policy change. The live runner remains
untouched. No new live collector, venue probe, credential use or injected event.

Chosen design: a bounded, process-local positive-history cache from the last
successful complete REST proof, used only to classify duplicate notifications.
Rejecting all fill notifications or trusting a durable execution ID alone would
hide new exposure; broader proof sharing would exceed this repair's scope.

- Retain at most512 active-market executions already returned by mandatory
  history, with original full-proof start/completion, source generation and scope.
  No extra history/position/order request or durable schema is added.
- For an incoming fill, require matching execution identity, client/exchange
  order identity, direction/intent, price, quantity, fee, outcome, trade ID and
  venue execution time. REST-only liquidity/trade-state enrichment does not make
  a private replay new; an explicitly busted/reversed execution cannot qualify.
- Require a positively owned, non-ambiguous current order matching status,
  cumulative/original quantities, price, direction and exact venue order time.
  A stale envelope carrying a genuinely new execution remains urgent.
- Require same source/generation/scope, original fresh REST gate, unchanged
  completion clock, polling not due, no earlier hint, absent/unknown order,
  unresolved intent, stop, rebind, source switch or mutation in flight.
- Always run the normal serial projector/conflict checks. Every private event
  still invalidates same-sweep execution-proof reuse. A duplicate changes no
  risk value, position quantity, receipt time, loss accounting or proof clock.
- Failed/canceled full proof, any scoped position proof, and an uncertain
  notification invalidate coverage. A periodic cadence no-op cannot renew it.
  Cache misses/old partial order envelopes take the existing conservative path.
- Reuse the existing minute telemetry with `covered_fill`; no new process or
  additional polling. ACK/gap classification is unchanged.

Business rules unchanged: quote size15, configured32 slots, four$30 windows,
loss reducer tiers, public books/selection, entry/reducer prices, GTC/GTD/IOC,
15-minute inventory and pre-kickoff deadlines, financial reads, history coverage,
normal/active polling, REST fallback and global API governor.

### Validation and review

The two formal overlap regressions failed on the old source: each observed
orders1+positions1+history1 instead of zero. Both pass after the patch, including
notifications queued during REST collection and consumed afterward. A genuinely
new fill still makes the same three required reads. Partial fills and repeated
identical deliveries retain exactly-once quantities and original proof clocks.

Regression coverage additionally includes changed fee/trade/quantity/timestamp,
changed cumulative/status/order timestamp, malformed/missing/null fields,
stale-order/new-execution, reversed trades, private-only IDs absent from the
latest REST batch, stop/scope/source/generation/rebind/mutation/ambiguity guards,
backward/expired clocks, failed/canceled collection, scoped-proof invalidation,
prior/later position hints and normal active polling. Existing hybrid admission,
REST comparison, preplan and same-sweep receipt tests remain required.

Offline replay saving is three reads for the demonstrated overlap, not an
aggregate live percentage or a guarantee against429. More conservative cache
misses deliberately retain required REST work. Natural `covered_fill`, total
REST/endpoint429 and reducer-latency acceptance must wait for an authorized
deployment; no position is opened merely to test the repair.

Rollback is source-only through the existing stopped/flat gated workflow;
there is no configuration, database or policy migration. No running checkout
source was overwritten during validation.

### Final prepared-artifact evidence — September24,22:10Z

- All2,211 tests in95 files pass in106.95s (19 new covered-fill regressions).
  Command: `nice -n 10 /home/polybot/liquidity-bot/.venv/bin/python -B scripts/run_tests.py -j 4`
  from isolated `/tmp/covered-fill-review.MrowFN`; log `full-suite-final.log`.
- Native production Python3.12.3/WSL; exact installed package versions in the
  [environment record](HYBRID_COVERED_FILL_2026-09-24.environment.txt).
  No live credentials/database were copied into the stage. Test files use fake
  venue clients/temporary ledgers; dashboard tests use a local test HTTP server.
- [SHA-256 manifest](HYBRID_COVERED_FILL_2026-09-24.sha256) records245 identical
  stage/workstation source, test, script, config, dependency and dashboard inputs.
  Hashes are raw staged bytes; deployment comparison must account for CRLF/LF
  normalization, then verify imports/environment. Markdown was also staged for
  documentation tests; subsequent evidence-only edits receive a separate audit.
- Initial staging omitted documentation/dashboard fixtures, and one new test
  incorrectly added an11th fill to a10-contract fully-filled order. Restoring
  those fixtures and making that test a5-contract partial+1 scenario fixed the
  test setup; no runtime safety behavior was relaxed. The complete final suite
  above supersedes those unsuccessful runs.
- Final diff reviewed: runtime changes are confined to the account-source
  coordinator; no mutation executor, risk, selector, finance, timing policy,
  poll interval or schema changes. Two new indexed local reads occur only for
  a candidate covered fill; non-fill ACKs do not take this new query path.
- Process rechecked: same supervisor420964/runner420980, live size15/32 hybrid,
  loaded marker`ebb2f480de1d5682cc799db5703a2ddb393c56e0`. Live checkout/source
  untouched; exposure not freshly queried and must be treated as unknown here.

Pending: explicit activation authorization, fresh shutdown/start gates and
natural live acceptance. Prepared source is not evidence of deployed savings.
