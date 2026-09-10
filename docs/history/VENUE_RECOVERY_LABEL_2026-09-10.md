# Venue recovery reporting — September 10, 2026

## Scope and incident evidence

The operator authorized the reporting fix and restart, then asked to continue
after an interrupted stop. Only presentation changes are authorized here.
Generic API/stream recovery was logged as `maintenance-pause` and its successful
account-health gate was announced as maintenance. That is not proof of an
official maintenance window or of the underlying outage cause.

The previous runtime repeatedly lacked private initial snapshots
(`order_eof=0/4 account_balance=0/4`) after a disconnect. Successful REST health
checks did not satisfy private-stream startup proof. The relationship to any
separately reported funding/balance-ledger incident is unproven; do not present
that possible association as the established cause or an IP-ban diagnosis.

The graceful stop ended at 2026-09-10 21:34:50Z with `operator_sigterm`,
`flat=true`. At 21:36:35Z independent authenticated reads proved zero orders
and positions; the ledger had zero working/unknown orders and unresolved
intents. Both supervisor and runner were absent. Parity matched Windows,
GitHub and native WSL at `ded9cee3`; the inactive backup PC was unavailable.

## Repair and dedicated edge-case review

- Unplanned coordinator output uses `venue-recovery-pause` and venue recovery
  wording; `planned=True` retains scheduled-maintenance messages and tags.
- Enter, failed probe, successful probe, and health-gate completion use the
  same selected label. The success message still explicitly says quoting is
  blocked until stream and exact reconciliation gates pass.
- The read-only API capacity report recognizes the new tag as runtime recovery.
  Existing log tags remain supported. Historical ambiguous maintenance labels
  are not retroactively proof of a scheduled outage.
- Inspected every coordinator caller: only the existing scheduled coordinator
  sets `planned=True`; startup and private/market/bound recovery use the default.
- Success/failure and three-read streak behavior, empty/full account snapshots,
  stop/timing races, unknown ownership, API pace, recovery, shutdown, restart,
  configured size/capacity and risk persistence are unchanged. This diff has
  no new venue call, mutation, schema, state reset, gate or classifier change.
- Rollback affects wording only. No database migration or reconstruction is
  needed; reverting the label must also preserve its report consumer.

## Verification

The focused reporting regression covers both planned states and a failed probe
followed by three complete reads, preserves no-create/no-cancel assertions,
and checks that health success cannot claim resumed quoting. Reversing the
label selection failed the unplanned subtest. The capacity consumer regression
also failed when recognition was removed; after restoration all 20 capacity
tests pass. Final pinned-venv review-worktree suite: **1,228 tests passed** in
117.270 seconds. Documentation audit and final diff/edge-case review passed.

## Release acceptance

Pending final suites, push, native deployment verification and fresh start gate.
The bot remains stopped until these checks complete. No strategy, cap, size,
HARD, entry-quality or live-event protection changes are included.
