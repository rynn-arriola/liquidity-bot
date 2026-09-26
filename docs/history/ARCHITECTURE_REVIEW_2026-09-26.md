# Code and architecture review — September 26, 2026

Operator: "Can you review over all code and architecture". Read-only review of `29773e246` (live). It follows the September 25 whole-codebase review, whose five items (file split, one not-found helper, …) are done and are not repeated here.

## Size (MEASURED)

| Area | Size |
|---|---|
| Package | 103 modules, 39,658 lines; 79 modules loaded by the trader |
| Tests | 108 files, 45,314 lines, 2,351 tests, about 3 min parallel |
| Ops | 50 scripts; bash supervisor; PowerShell watchdog; Python host recovery; systemd timers (reward table, changelog, ledger backup); Windows logon task; two Claude schedulers |
| Production state | ledger 1.3 GB (1.13 M `execution_events`); `logs/commissioning.log` 258 MB since 08-29 (about 9 MB/day) |

## Verdict

The safety architecture is strong and should be kept:
- a single-writer durable ledger;
- intent-before-mutation;
- fail-closed proofs;
- flat gates;
- a supervisor with explicit exit codes;
- warm restart;
- a large test suite that catches deliberate regressions.

The main weaknesses are throughput of the decision loop, the runner's structure, and operational hygiene. None is an emergency.

## Findings, highest value first

1. **Decision-loop throughput is the biggest remaining lever on resting time.**
   - One serial loop services up to 32 owners and runs whole-account REST proofs inside the same loop. Decision latency p95 is about 20 s while CPU is 0.2 core.
   - After any terminal notice, creates wait for the next proof, so an expired entry is replaced after a median 27 s (see RESTING_EFFICIENCY_2026-09-26).
   - Proposal: run the account proof as its own paced task that publishes a proof generation, and let owners with a ready create act as soon as a newer proof lands. Safety rules stay identical (no POST before proof); only waiting changes.
   - Medium-large effort, high value. It touches timing, so it needs operator approval.
2. **The runner is hard to reason about.**
   - `CommissioningRunner` composes 11 mixins, and two subclasses (`RestFallbackRunner`, `AccountSourceRunner`) override many of the same methods. Each override branches on `_using_rest_source()` or the hybrid state.
   - `live_commissioning` and `account_source_runner` import each other.
   - `run` is 568 lines and `__init__` 426 lines (about 300 instance fields). 20 functions exceed 150 lines.
   - Tracing today's 404 issue crossed five files and three `_rest_reconcile` layers.
   - Proposal: extract, incrementally and behavior-preserving (same verification method as the file split):
     - `AccountProof` (reconcile, read owned orders, snapshots);
     - `OwnerScheduler` (dirty/due/decision timing);
     - `run()` phases.
   - Do not rewrite.
3. **The release/restart flat gate is not version-controlled.**
   - `.local-tools/review_release_gate.py` gates every Command 1 and the schedulers' restarts, but lives in a git-excluded folder with 378 other ad-hoc files, logs and one-off checks.
   - It has no tests and does not exist on the PC.
   - Proposal: move it to `scripts/` with tests, and archive or delete the rest.
   - Small effort, high value. No trading change.
4. **Unbounded log growth.**
   - `commissioning.log` has no rotation (no logrotate entry): 258 MB and rising about 9 MB/day.
   - Every diagnostic, scheduler check and TV activity read scans its tail; some analyses read 200,000 lines.
   - Proposal: weekly logrotate with `copytruncate` and compression (the supervisor appends with `>>`, so this is safe).
   - Small effort. The ledger (1.3 GB) is fine; a daily backup timer exists, but a restore has not been rehearsed recently.
5. **About 5,100 lines of inactive experiments inside the trading package.**
   - The `scalp_*`, `shadow_*`, `capacity`, `queue_measurement`, `calibration` and `dry_run_acceptance` modules are not loaded by the trader. The scalp runner is not running.
   - They still carry tests and review surface.
   - Proposal: move them to a separate `experiments` package or archive them, unless the operator plans to use them.
   - Small effort. It needs the operator's call on which experiments matter.
6. **Silent broad exception handling.**
   - There are 165 `except Exception` sites. Most are deliberate containment with reporting, but 18 swallow with `pass`.
   - Today's event-evidence loss was one such swallow.
   - Proposal: audit the 18 and log a reason (rate-limited) where the swallow can change trading outcomes.
   - Small-medium effort. No behavior change.
7. **Operational surface is spread across four languages and six schedulers.**
   - It is correct but undocumented as one picture.
   - Proposal: one page `docs/OPERATIONS_MAP.md` covering:
     - what starts what;
     - which state file means what (`host_recovery.json`, `power-guard-stop.json`, `warm-restart-scope.json`, locks);
     - who may restart.
   - Remove stale atomic-write `.tmp` files left in `data/`.
8. **Documentation weight.** 185 history docs; STATUS/NEXT/BUGFIXES headers are single very long lines. They work for agents but are hard for the operator. A short human summary at the top of STATUS would help.

## Suggested order

1. Findings 3, 4, 6 and 7: small, no trading change. They could ship together as restart-free or report-only work, except the logging in item 6, which needs one Command 1.
2. Finding 1: design first, then implement with the operator's approval. Measure with decision latency and replacement gap.
3. Finding 2: incremental extraction alongside finding 1, where it naturally applies.
4. Findings 5 and 8: operator choice.
