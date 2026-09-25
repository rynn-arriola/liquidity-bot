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
| 2 | Long-term | `live_commissioning.py` is 11,800 lines, mostly one class; 11 of the 20 longest functions (run 566, `__init__` 426, `_send_summary` 345, `_decide_for_market` 337, `_shutdown` 313); 236 of 319 `src` commits since Aug 1 touched it | IN PROGRESS (operator authorized). Slice 1 done; see log below |
| 3 | Low | `AsyncTradingGateway.retrieve` detects 404 by `"NotFound" in str(exc)` while `9f0d7864a` uses `status_code == 404` | Open; align on status code |
| 4 | Low | `risk.maximum_safe_order_size` unused; NaN `loss_budget` would return full `size_cap` (fail-open) if ever reused. `shadow_math.py` has an unused `split_reward_day_interval` import | Open; delete |
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

Remaining candidates, in order of isolation: earnings/reward reporting
helpers, then scan/top-up scheduling (timing-sensitive: move only, never retune),
then stream recovery, then the core loop/decision/shutdown only if the risk is
justified. Each needs the same proof before commit.
