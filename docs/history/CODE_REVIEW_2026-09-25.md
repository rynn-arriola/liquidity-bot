# Whole-codebase review — September 25, 2026 (Claude, backup AI operator)

Sampled review, not a line-by-line audit of all ~38,700 source lines. Read in
full: the undeployed `9f0d7864a` diff, `live_orders.py` place/cancel/retrieve,
ledger fill/position/snapshot booking, `risk_windows.py`, `risk.py`, the
reconciler inventory/hard-exit plan, and every `except Exception: pass`
site. AST scan covered all of `src/liquidity_bot`. Full WSL production-
interpreter suite passed (2,286 tests before, 2,290 after item 1).

No money-risking defect was found in the reviewed paths. `9f0d7864a` reviewed
with no defects found. Every item below is pending operator review.

| # | Priority | Finding | Status |
|---|---|---|---|
| 1 | Fix soon | Periodic 10s REST account proof (main proof in hybrid mode) discarded exceptions with no outer log | DONE `f7e6ed903`, pushed, NOT deployed, not in staged `9f0d7864a` |
| 2 | Long-term | `live_commissioning.py` is 11,800 lines, mostly one class; 11 of the 20 longest functions (run 566, `__init__` 426, `_send_summary` 345, `_decide_for_market` 337, `_shutdown` 313); 236 of 319 `src` commits since Aug 1 touched it | DONE, slices 1-11 (operator authorized), pushed, NOT deployed; see log below |
| 3 | Low | `AsyncTradingGateway.retrieve` detects 404 by `"NotFound" in str(exc)` while `9f0d7864a` uses `status_code == 404` | DONE, pushed, NOT deployed; see below |
| 4 | Low | `risk.maximum_safe_order_size` unused; NaN `loss_budget` would return full `size_cap` (fail-open) if ever reused. `shadow_math.py` has an unused `split_reward_day_interval` import | DONE differently, pushed, NOT deployed; see below |
| 5 | Housekeeping | Laptop Windows Python lacks `httpx`, `tzdata`, `cryptography`; 80 test files fail to import there. Use WSL `.venv` for tests | Open; workstation setup only |

Reviewed and judged intentional: the remaining ~26 silent `except Exception`
sites (telemetry/cleanup that must not affect trading), UTC ISO text timestamp
comparison in the ledger (all writes normalized by `_time`), and float use in
economics estimates (money/position arithmetic uses `Decimal`).

## Item 2 slice log — pure relocation, no behavior change

Operator constraint (September 25): do not break anything, change business
logic or change timings. Each slice is a verbatim move into a mixin that
`CommissioningRunner` inherits, so every `self` lookup, subclass override and
call site resolves exactly as before.

**Slice 1 — summary reporting (`runner_summary.SummaryReportingMixin`).**
Moved `_send_summary`, `_scheduled_summary_has_transient_capital_ambiguity`
and `_send_scheduled_summary` (396 lines). Proof: the AST of each moved method
equals the original; all 271 remaining runner methods are AST-identical; no
name-mangled attributes; every global used resolves to the same object;
`CommissioningRunner`, `RestFallbackRunner` and `AccountSourceRunner` resolve
the moved names to the same functions (MRO ends `CommissioningRunner,
SummaryReportingMixin, object`). Two tests changed only their `patch()`
target to the helper's new module path. Full WSL suite: 2,290 tests pass.
`market_quality_store` provenance hashes are unaffected (summary code is not
in that list). Not deployed; newer than the staged `9f0d7864a` package.

**Slice 2 — diagnostic log lines (`runner_diagnostics.RunnerDiagnosticsMixin`).**
Moved `_log_selection_scan`, `_log_maintenance_schedule_state`,
`_log_decision_scheduler`, `_log_api_budget`, `_log_shutdown`,
`_log_book_rate_limit` and `_log_api_backpressure` (214 lines) plus their only
constant `DECISION_SCHEDULER_LOG_SECONDS = 60.0`, which `live_commissioning`
re-exports unchanged. Proof: 7 moved and 264 remaining method ASTs identical;
the constant is the only other module-level removal; every global used
resolves to the same object/value; runner and REST/hybrid subclasses resolve
the moved names to the same functions. No test changes. Full WSL suite: 2,290
tests pass. Not deployed.

**Slice 3 — earnings refresh (`runner_earnings.EarningsReportingMixin`).**
Moved `_refresh_due_earnings` and `_refresh_earnings` (60 lines) including the
`api_workflow("reward_refresh")` wrapper, whose only label is that static
string. Proof: 2 moved and 262 remaining method ASTs identical; no
module-level removals; globals resolve to the same objects. Deliberately NOT
moved: `_publish_entry_pause_transition` (sets the entry-cancel request and
dirties markets), `_can_defer_cash_report`/`_remember_account_report`/
`_reporting_equity` (feed the cash-report entry gate), and the dashboard
`_publish_*_snapshot` writers (tests redirect their output by patching
`live_commissioning.*_CACHE_PATH`; moving them could let tests write the real
dashboard files). No test changes. Full WSL suite: 2,290 tests pass. Not
deployed.

Operator direction (September 25, later): finish slices 4-11 without pausing;
Claude reviews each slice itself before committing and records the review.

**Slice 4 — scan/top-up scheduling (`runner_top_up.TopUpSchedulingMixin`).**
Moved 19 methods (650 lines): `_select_reward_market`, the `_top_up_*`,
`_selection_*`, refill/post-flat request helpers and `_safe_to_scan`. The one
constant also used by remaining code, `POST_FLAT_SELECTION_CACHE_SECONDS =
120.0` with its comment, moved to new `runner_shared.py`; `live_commissioning`
imports the identical object. Proof: 19 moved and 243 remaining method ASTs
identical; globals resolve to the same objects/values; the hybrid
`AccountSourceRunner._replacement_candidates_for_top_up` override still takes
priority and its `super()` reaches the moved code. No test changes. Full WSL
suite: 2,290 pass. Self-review: no timing value changed; the 9 moved `print`
sites are not under any `live_commissioning.print` patch (suite unchanged);
tests patching `live_commissioning.time.process_time` patch the shared `time`
module and still apply. Follow-up (not done, would change measurement code):
`market_quality_store` provenance hashes list `live_commissioning.py` but not
the new mixin files, so its archive fingerprint no longer covers moved
selection code.

**Slice 5 — reward evaluation/rotation (`runner_rewards.RewardRotationMixin`).**
Moved 11 methods (571 lines): `_evaluate_rewards(_inner)`, eligibility
recorders, `_reward_*_and_flat`, `_rotate_reward_market(_inner)`,
`_awaiting_rotation`, `_rotation_blockers`. The whole contract-refresh constant
group (60.0/900.0/60.0/8/5.0) moved with its comment block, unbroken, to
`runner_shared.py`. Property/setter compatibility pairs (e.g. `_reward_eligible`)
deliberately stay together in the runner. Proof: 11 moved and 232 remaining
method ASTs identical; every removed constant still reachable from
`live_commissioning` with its original value and type (new check 2b); globals
resolve to the same objects; no subclass overrides. No test changes. Full
WSL suite: 2,290 pass. Self-review: reward eligibility gates quoting, so this
is decision-adjacent, but relocation is AST-proven identical; a duplicated
generated import was removed before commit.

**Slice 6 — account/risk gates (`runner_account.AccountRiskMixin`).**
Moved 23 methods (672 lines): balance/equity refreshes, cash-report deferral
and reporting equity, window safety/observation/release, loss entry-order cap
and its cancel/block helpers, `_entry_risk_allowed`, `_portfolio_worst_loss`,
`_is_risk_reducing_mutation`, account-proof freshness and safe-stop checks.
`NONTRADING_CASH_MAX_PAGES = 4` and both 5.0s decision refresh margins moved
with their comments to `runner_shared.py`. Only the moved code called
`fetch_nontrading_cash(_changes)`, so the mixin now imports them from
`cash_activity` and `live_commissioning` no longer does; all 24 test patches
were retargeted to `liquidity_bot.runner_account.*`. Proof: 23 moved and 209
remaining method ASTs identical; removed constants keep values; the two
cash functions are the same objects as before; `RestFallbackRunner.
_position_priority_has_fresh_account_proof` and `AccountSourceRunner.
_account_source_recovery_checkpoint` overrides keep priority and chain to the
moved code. Full WSL suite: 2,290 pass. Self-review mutation: pointing one
retargeted patch back at `live_commissioning` fails loudly with
AttributeError, so no stale patch can silently stop intercepting.

**Slice 7 — REST account proof (`runner_rest_proof.RestProofMixin`).**
Moved 11 methods (512 lines): `_rest_reconcile`, `_collect_rest_snapshots`,
`_fetch_rest_order_snapshot`, `_note_periodic_reconcile`, orphan-place proof
advance, unresolved-owner scoping, quiet-book REST refresh check,
transition-proof freshness, retained-value replacement snapshot and both
hard-exit reconcile helpers. `ORPHAN_PLACE_CLEAR_PROOFS = 3` and
`ORPHAN_PLACE_CLEAR_MIN_SECONDS = 10.0` moved with their comment to
`runner_shared.py`. Proof: 11 moved and 198 remaining method ASTs identical;
removed constants keep values; globals resolve to the same objects. Seven
overrides (`RestFallbackRunner` x5, `AccountSourceRunner` x2, `_rest_reconcile`
in both) keep priority and their chain reaches the moved code after the
subclass layers, exactly as before; `rest_fallback.py:883`'s explicit
`CommissioningRunner._rest_reconcile(...)` resolves to the same function. The
verifier's chain check was corrected to walk past both subclass layers
(first version wrongly required a single hop). No test changes. Full WSL
suite: 2,290 pass.

**Slice 8 — stream recovery (`runner_streams.StreamRecoveryMixin`).**
Moved 16 methods (940 lines): private/market/bound stream recovery, stream
rebind/open/settle/consume, bound-recovery position service/preemption,
startup-recovery scoping, transient-error classification (`@classmethod`),
venue-outage wait, strategy-state recovery and recovery delay. Moved to
`runner_shared.py` with comments, unbroken: the three
`MAX_*_STREAM_RECOVERY_ATTEMPTS = 12`, `_OrphanPlaceEvidencePending`,
`_CancellationEvidencePending` (same `CommissioningUnavailable` base and
docstrings, re-exported by `live_commissioning`) and the maintenance-probe
group (3/3/60.0). Proof: 16 moved and 182 remaining method ASTs identical;
removed names reachable with original values; both exception classes are
single objects shared by all catch sites; four overrides
(`AccountSourceRunner._consume_ready_stream_tasks`, three `RestFallbackRunner`
bound-recovery hooks) keep priority and chain to the moved code. The verifier
now compares raw class-dict entries so classmethods compare correctly. No test
changes. Full WSL suite: 2,290 pass. Self-review note: those two exception
classes' `__module__` is now `liquidity_bot.runner_shared`; nothing reads it,
and it would only appear in an uncaught traceback's text.

**Slice 9 — startup gates (`runner_startup.StartupMixin`).**
Moved 6 methods (496 lines): `preflight` (with its unchanged
`api_workflow("startup")` label), `preflight_until_ready`,
`_release_partial_startup` and the three startup scoping helpers. No constants
needed moving. Proof: 6 moved and 176 remaining method ASTs identical; globals
resolve to the same objects; `RestFallbackRunner.preflight` and
`AccountSourceRunner.preflight` keep priority and their `super()` chains reach
the moved code. The test-patched `load_runtime_secrets`/`ReconcilerPolicy`
are not used by moved code. No test changes. Full WSL suite: 2,290 pass.

**Slice 10 — stop/HARD/quarantine/shutdown (`runner_shutdown.ShutdownMixin`).**
Moved 14 methods (808 lines): `request_stop`, `_stop_reason`, HARD latch/
release/announce, maintenance-flatten and flatten-deadline checks, order
quarantine and its terminal assertion, `_shutdown_slugs` and `_shutdown`. No
constants needed moving. Proof: 14 moved and 162 remaining method ASTs
identical; globals resolve to the same objects; `AccountSourceRunner._shutdown`,
`RestFallbackRunner._position_near_its_flatten_deadline` and
`RestFallbackRunner._quarantine_strategy_orders` keep priority and chain to the
moved code. Signal registration (`add_signal_handler(sig, runner.request_stop,
...)`) stays in the runner and binds through the instance as before. No test
changes. Full WSL suite: 2,290 pass.

**Slice 11 — decisions/orders/exits (`runner_decisions.DecisionMixin`).**
Moved 26 methods (1,469 lines): `_decide_for_market`, `_prepare_market_decision`,
`_decision_sweep`, action failure handling, uncertain-cancel retry, owned-order
reads, decision-time execution-state refresh, urgent-position/exit service,
hard-exit retry delay and reducer-decision capture/recording. Moved to
`runner_shared.py` with comments, unbroken: `MAX_RECOVERY_ATTEMPTS = 12` with
`_ConfirmedMarketNotOpen`/`_RewardUnavailable`, and `MAX_RETIREMENT_ROUNDS = 10`.
Only moved code used `capture_reducer_decision`/`completed_reducer_decision`, so
`live_commissioning` no longer imports them; the 4 `test_reducer_diagnostics`
patches (`print`, `capture_reducer_decision`) were retargeted to
`liquidity_bot.runner_decisions.*` — every `[reducer-decision]` line those
tests read is printed by moved code. Proof: 26 moved and 136 remaining method
ASTs identical; removed names keep values; ten REST/hybrid overrides keep
priority and chain to the moved code. Full WSL suite: 2,290 pass.
Self-review mutation: pointing one retargeted `print` patch back at
`live_commissioning` fails the test, so a stale patch cannot silently pass.

**Final state and end-to-end proof.** Eight `runner_shared` import
statements were merged into one (same 23 names; no other statement changed).
Against the pre-refactor baseline `cb951ecc5`: all 285 original
`CommissioningRunner` method definitions (including property/setter pairs) are
found AST-identical through the MRO; class-level attributes are unchanged; all
32 literal module constants keep value and type; every original module-level
name is still importable from `live_commissioning`; no method name is defined
in two MRO layers. Full WSL suite: 2,290 pass. `live_commissioning.py` fell
from 11,860 to 4,898 lines; 147 methods, `run`, `__init__` and the
property/compatibility layer remain in the runner.

Deployment notes: activation must ship `live_commissioning.py` together with
all twelve `runner_*.py` modules (a partial install would fail at import, not
trade differently). None of this is in the staged `9f0d7864a` package. Follow-
up (not done, would change measurement code): `market_quality_store`
provenance hashes still list only `live_commissioning.py` among runner files.

## Item 3 — one "not found" classification

`errors.is_not_found(exc)` is now the single rule used by
`AsyncTradingGateway.retrieve` (bounded post-cancel 404 wait) and the hybrid
order-read 404/terminal-proof path in `AccountSourceRunner`: HTTP status 404
first, with the long-standing `"NotFound"` text match retained for errors built
without a status. The SDK (`polymarket_us` client) raises `NotFoundError`
for exactly status 404 and the gateway keeps both name and status, so for real
venue responses both call sites classify exactly as before; retry count and
delays (4 attempts, 0.5/1/2 s) are unchanged. Tests: new
`test_not_found_classification.py` (real SDK errors through `_adapter_error`,
other statuses, status-over-wording) and two `test_live_orders.py` cases
(reworded 404 still waits; a 500 is not retried). Mutation: restoring the old
text-only check fails the reworded-404 test. Full WSL suite: 2,295 pass.

## Item 4 — corrected finding and fix

Correction: the `shadow_math.py` "unused import" was a false positive of the
initial scan. `split_reward_day_interval` is re-exported there and imported
from `shadow_math` by `shadow_engine.py` and `tests/test_shadow.py`; it was
left unchanged. `risk.maximum_safe_order_size` is unused by production code
but has its own tests in `tests/test_risk.py`, and project rules forbid
deleting tests, so instead of deleting it the NaN trap was removed: a NaN
`loss_budget` or `size_cap` now raises `ValueError` instead of returning the
full cap. Finite behavior (including existing tests) is unchanged; no trading
path calls it. New regression test covers both NaN inputs; removing the guard
fails it. Full WSL suite: 2,296 pass.
