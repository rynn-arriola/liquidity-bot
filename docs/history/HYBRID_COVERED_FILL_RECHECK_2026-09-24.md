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
