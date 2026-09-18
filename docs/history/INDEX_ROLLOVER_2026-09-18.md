# September 18 root-index rollover

These September17 handoff entries are historical. Their pending-activation
status was superseded by the September18 restart recorded in
[the maintenance release](MAINTENANCE_BUFFER_2026-09-18.md).
They are preserved here to keep the root handoff within its context budgets.

## FINDINGS.md

- **September17 review/fix evidence:** old planner cancels the reducer then refuses its expensive timed IOC; a boundary-spanning poll backdates a -$6 closing loss and reports $0 in the new window. Fixes retain passive protection and stamp post-read batch receipt.107 focused/both1,749-test suites pass; old-method reversals fail4/8 assertions. Code reproductions, not measured live losses. `ee500e4db` installed with207 matching hashes; NOT restarted/active. [Evidence](END_TO_END_REVIEW_2026-09-17.md).

## NEXT.md

**September17 `ee500e4db` installed; activation awaits authorized restart:**107 focused and both1,749-test suites pass; both reversals detect defects and207 deployed hashes match. No restart/reload occurred. Reuse this unchanged-source deployment evidence, require fresh ownership/account/start gates when restart is authorized, then observe natural deferred-exit/boundary behavior without induced faults. Exchange-resolution accounting remains separate. [Details](END_TO_END_REVIEW_2026-09-17.md).

## BUGFIXES.md

- **Deployed September17 in `ee500e4db`, not restarted — deferred reducers and rollover receipt:** early timed exits cancelled passive protection before discovering their IOC was too costly/thin; the shared planner now retains/restores the exact reducer without critical retirement. REST reconciliation backdated new fills to request start, potentially omitting a boundary loss; it now stamps receipt after collection without refreshing account proof or moving duplicate fills.107 focused/both1,749-test suites pass, both reversals detect defects and207 installed hashes match. Activation awaits authorized restart/normal gates. [Review/repair](END_TO_END_REVIEW_2026-09-17.md).

## STATUS.md

- **September17 `ee500e4db` DEPLOYED ON DISK, NOT RESTARTED:** deferred-reducer and REST receipt-time fixes await the next authorized restart.207 normalized file hashes match; both1,749-test suites pass (native165.601s),107 focused tests and both reversals pass. DEPLOYED_COMMIT identifies installed source only; original supervisor432/runner446 retain their September16 starts and old loaded trading source. No orders/config/settings changed; current venue exposure not re-read. Resolution-only accounting remains separate. [Evidence](END_TO_END_REVIEW_2026-09-17.md).

## DECISIONS.md

- **September17 review follow-up:** operator authorized the deferred-reducer/REST receipt-time fixes and then explicitly authorized deployment without restart. Install reviewed files only; do not stop/restart or reload the current bot. Activation awaits a later authorized restart with normal gates. Preserve all risk/entry/exit settings; exchange-resolution accounting remains separate. [Evidence](END_TO_END_REVIEW_2026-09-17.md).
