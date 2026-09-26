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

## Progress

- **Finding 3 — done (September 26).**
  - The gate is now tracked as `scripts/release_gate.py`, with pure `gate_result`/`is_flat` covered by `tests/test_release_gate.py`. Deliberately inverting the flat decision fails 6 tests; ignoring ledger positions fails 1.
  - It imports the package from `./src` of the working directory, because credentials are found relative to that package. Callers run it from `/home/polybot/liquidity-bot` via the `/mnt/c` path, so no deploy is needed.
  - On live data its JSON matched the old script field for field, and `--require-flat` refused (exit 1) while trading.
  - Updated callers: the status-check scripts (`wsl_pm_check`, `wsl_pm_restart`, `wsl_pm_fix_deploy`, `gate_read`) and the Command 1 scratch scripts. The scheduler's `pm_check.sh` was re-run successfully on the new path.
  - 352 unreferenced `.local-tools` files (224 MB) were moved, not deleted, to `.local-tools/archive/2026-09-26-unreferenced/`. The 22 files still cited by history evidence stay in place.
- **Finding 4 — done (September 26).**
  - WSL `sudo` needs a password, so there is no system logrotate entry. Instead, `scripts/rotate_logs.py` runs `/usr/sbin/logrotate` as `polybot` with its own state file (`data/logrotate.state`) and a generated config: weekly, `copytruncate`, dated names, `compress` + `delaycompress`, and `rotate 520`, so evidence is effectively never deleted.
  - A daily user-crontab line (`--install-cron`, idempotent, marked `# liquidity-bot log rotation`) runs it at 03:17 with `nice`/`ionice`; logrotate decides when a week has passed. Output goes to `logs/rotate.log`.
  - Tests: `tests/test_rotate_logs.py` includes a real logrotate run under an open `>>` writer (the new write lands at offset 0, and old lines are in the dated file). Dropping `copytruncate` fails 2; duplicating cron lines fails 1.
  - Known cost: lines written during the roughly 1 s copy can be lost at each rotation. Readers were checked: the TV dashboard reads only the last 128 KB, and the status/acceptance scripts tolerate a short file.
