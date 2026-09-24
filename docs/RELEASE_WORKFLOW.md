# Proportionate fix and restart workflow

Operator-approved September 23, 2026: remove redundant development/release work,
not trading protections. This governs verification requirements in AI_OPERATOR
and AI_ENGINEERING; older historical release checklists are evidence, not new
requirements to repeat. No existing test is deleted by this policy.

## Choose the smallest sufficient verification

| Change | Required verification |
|---|---|
| Unchanged restart | Verify deployed artifact/environment/settings, old runner absent and fresh authenticated flat/known state; retain normal startup gates. No full suite or reversal just because the process restarts. Investigate any new incident first. |
| Documentation only | Documentation audit, links and final diff. No trading suite, mutation test or trader restart. |
| Isolated display/report formatting | Focused report tests, representative rendered output including missing/large values, consumer/import review. No blanket trading suite or reversal. |
| Isolated code repair | Failing-before/passing-after regression, affected module and integration tests, relevant edge-case/diff review. Escalate if isolation cannot be demonstrated. |
| Execution, account state, risk, scheduling, selection, persistence, shared API admission, startup/shutdown, dependencies or uncertain scope | Focused regressions plus one complete suite in the production-compatible environment, affected end-to-end paths, race/failure/rollback review. |

Accounting/data changes are not presentation-only: changes to settled P&L,
balances, reward attribution or shared report inputs require their complete
accounting/consumer coverage; any trading/risk consumer uses the broad tier.
An isolated-looking edit with shared callers or imports uses the broader tier.

For a bug, showing its regression fail on the old implementation and pass on
the new one is sufficient sensitivity evidence. A separate deliberate reversal
is needed only when that evidence is absent and the repair affects a critical
invariant. Do not repeat a completed sensitivity check on unchanged code.
Run destructive/mutation tests only in an isolated test tree with fake data,
never against production source, credentials or the live ledger.

## Reuse completed tests, not old account state

Record once: tested artifact manifest (source, tests, configuration, scripts,
fixtures and dependency inputs), interpreter/platform and installed dependency
versions, command, result and timestamp. Keep a concise record in the release
handoff; a commit name or hashes of only the changed files are insufficient.
Exclude secrets and volatile live data from the manifest.

A passing isolated-stage run using the production environment can satisfy the
deployment suite when all relevant inputs match the deployed tree/environment.
Verify that match and the installed import/launch configuration; do not rerun
the same full suite solely because files moved or documentation HEAD advanced.
Changed inputs, missing provenance, environment drift, new failures or a
deployment-specific dependency invalidate affected evidence. Re-test the
affected scope, broadening when uncertain. Never reuse a failing test result.
Path-dependent deployment behavior still needs its installed smoke check.

Account/ownership/freshness checks are NOT reusable test results. Preserve both
post-stop and fresh pre-start gates and the runner's own admission checks.

## Keep avoidable work out of downtime

For planned fixes/deployments, prepare and review an isolated candidate while
the bot runs, provided testing does not contend with trading or use live APIs.
Stop only when the release is ready and restart is authorized. Explicit stop
requests and Command 1 still mean stop first; finish only missing verification
afterward, never repeat completed valid work. Unsafe live incidents take priority.

Deployment remains: stop -> fresh flat/clear proof -> verified artifact and
configuration -> fresh start gate -> authorized start -> live acceptance.
Use saved size/source/slot/risk settings; no bare-default launch or risk reset.
Keep required parity and recovery backups; this policy does not change those
tools or silently waive their checks. Do not repeatedly run parity mid-task.

Observe startup and the affected live paths after launch, using existing local
telemetry first and bounded authenticated checks where exposure matters. Report
"running, acceptance in progress" separately from "accepted". Honor explicitly
requested observation durations; never induce fills or await an unrelated rare
event as a deployment gate. Unobserved cases remain labeled follow-ups, not
claims of complete live acceptance. New safety failures block acceptance.

## Protections not removed

Single-writer/host locks, account ownership, unresolved-order handling, exact
reducers, exit deadlines, settled-loss stops, pregame exclusion, rate-limit
backoff, fresh proofs, durable intent and shutdown confirmation stay unchanged.
No force kill, ledger repair, new restart or trading-policy change is authorized
by this workflow. A guard suspected of being redundant needs concrete evidence
that equivalent protection remains before proposing a separate runtime change.
