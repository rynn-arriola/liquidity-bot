# Bounded execution-proof reuse — September 23, 2026

## Scope and operating boundary

Operator authorized the recommended fix after the
[architecture review](ACCOUNT_PROOF_ARCHITECTURE_REVIEW_2026-09-23.md).
This implements the first conservative phase: execution-proof reason telemetry
and same-decision-sweep receipts. It does not implement a general data cache,
slower polling, financial-bracket sharing or an independent account service.
No restart/deployment authorization was inferred. The live laptop runner remains
on321728fc9; its source and configuration are not edited by this work.

## Implementation

`execution_proof.py` owns an immutable receipt and a one-use, IO-free cycle.
`RestFallbackRunner._decision_sweep` creates a cycle only in explicit REST/hybrid
with the existing REST optimization enabled. It always discards the cycle at
return/cancellation/failure. Nested service invalidates the parent receipt.

A successful routine full execution proof or a scoped position-transition/
hybrid-preplan proof can leave a receipt. A later scoped consumer may consume it
only when it covers that owner and all of these remain true:

- identical ledger total-change counter, bound scope, source mode/generation,
  private-observation revision and account-gate state;
- unchanged context across the original collection, no queued/held private
  observation, no switch/mutation in flight, no stop/rebind;
- known ownership and target risk state, no unresolved intent or absent order;
- original proof age at most30 seconds and completion age strictly below one
  second, with ordered/nonfuture timestamps;
- same decision sweep, one use, and the REST optimization still enabled.

Every failure or miss uses the original proof path; no retry, polling deadline
or safety gate is weakened. A scoped receipt cannot replace a full proof or
renew global entry authority. Hard-exit confirmation and strategy-recovery
consumers retain their existing dedicated handling and cannot use this new
shortcut. The existing positively proved IOC/reducer continuation caches remain.
All financial reads, transfer classification, settled-fill loss arithmetic,
capital, event/book freshness and final post-admission checks are unchanged.

Reuse preserves the original start/completion times in both base REST and
hybrid reducer certificates. It never reapplies a snapshot or creates a new
freshness timestamp. Any private observation invalidates the short receipt;
the revision also catches an observation already applied during collection.
That conservative invalidation does not revoke independent REST authority or
change the existing optional-stream gap behavior.

`[execution-proof]` emits bounded reason counters no more than once per minute
when a decision sweep completes. It distinguishes global/scoped proof reasons,
reuse/miss causes and exact-read causes (strict, positioned, ambiguous, missing,
changed/older, changed second-list row). It makes no venue request and contains
no order IDs/payloads. Counts describe proof work, not exact wire traffic, and
must be paired with existing `[api-budget]` endpoint counts. Existing
`[rest-order-proof] exact` semantics are preserved, including strict reads.

## Evidence and limitations

The fake full-proof-to-compatible-scoped sequence with30 flat orders plus one
15-contract position eliminates one orders/positions/history collection:
**three fewer reads** in both REST and hybrid, with no create/cancel and
unchanged quantity/gate/poll timestamps. A following attempted reuse must read
again. Scoped-to-global remains a full read; therefore this does NOT remove all
overlap from the earlier12-call diagnostic. Live hit rate and savings are still
unknown;10–20% remains a target rather than a forecast.

Focused baseline/feature tests and deliberate reversals are run in native
temporary WSL test trees using the installed production interpreter. They do
not open the production ledger, credentials or venue. Three in-memory reversals
are expected to assert-fail: removing reuse, removing runtime state checks, and
removing receipt revision/scope/age validation. Patches are context-scoped and
automatically restored. Full-suite results are recorded below before handoff.

## Dedicated edge-case review

- **Lifecycle:** new cycle per sweep; nested/failed/cancelled sweeps cannot
  restore old receipts; startup/recovery/shutdown are not converted to cached
  full-account proofs. There is no durable schema or restart cache.
- **Races:** fills, partials, durable mutation intents, ledger changes, source/
  scope changes and private events revoke compatibility. Event-during-proof,
  including applied events, cannot leave a reusable receipt. Final admission
  still refuses a queued late event after a successful reuse.
- **Unknown state:** no unresolved/absent/foreign state can use the shortcut;
  no absent order becomes canceled and no uncertain mutation is retried.
  Failed/cancelled collection clears receipts and retains fail-closed gates.
- **Boundaries:** 0/30-second proof age, just-under/exactly-one-second completion
  age, reversed/future clocks, different owners and single consumption are
  tested. Receipt clocks never renew base/hybrid caches.
- **Load/performance:** no new network call, writer or wait; optional reuse is
  bounded O(1) receipt state plus existing local ledger checks. Diagnostics have
  fixed reason cardinality. Urgent work is never delayed to form a batch.
- **Business/rollback:** no pricing, size15/32, four$30 windows, entry tiers,
  market filters, GTD, reducer quantity/direction or loss-accounting change.
  Existing REST optimization disablement disables new reuse, not safety reads.
  Rollback is source-only with the normal independently authorized release gate.

## Verification record

Final native WSL stage `/tmp/liquidity-execution-proof.Uorm6R` using the installed
production interpreter passed **295 focused tests in71.666s** and **2,162 full
tests in234.739s**. All three deliberate in-memory reversals assertion-failed
as expected. Final review corrected an introduced exact-read telemetry-counter
placement regression and added coverage, plus scope/source changes during proof.

Workstation and isolated-stage source/test SHA-256 values match exactly:

- `execution_proof.py`: `38b32ae886bb7fc8af16422048454ab747ed13b6286a2b4f3adda184f5c581bc`
- `rest_fallback.py`: `e891bc42b3b243654ddf6129b1045f138214ec2c10ae00fe3e293ee52f0cfee8`
- `account_source_runner.py`: `247a6c47bccd0cf842c7235073f53fba09d2f298b3016d36cff61e143a4df62a`
- `test_execution_proof_reuse.py`: `b927505b8b73f999be309d05dd0f362e6ff71dc1de0095de1c7c845a74f5deca`

Prepared only: production marker/source remain321728fc9, with no stop/restart or
order mutation. This turn made no authenticated exposure query; the last cited
venue check is19:26:58Z, not fresh flat/clear proof. Future activation requires
separate authorization and normal release gates. Natural hit/miss/endpoint and
reducer-latency acceptance is pending; synthetic savings do not establish live
savings or eliminate429s.
