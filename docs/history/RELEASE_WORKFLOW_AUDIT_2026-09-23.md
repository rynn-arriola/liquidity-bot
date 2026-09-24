# Release efficiency audit — September 23, 2026 ET

Operator requested faster fixes/restarts and approved removal of unnecessary
checks. Scope selected: redundant engineering/release requirements, not trading
protection. No bot source, tests, launcher, configuration or ledger was edited;
no stop/start or authenticated venue request was made.

## Evidence

The [last release](EXECUTION_PROOF_REUSE_2026-09-23.md) ran 2,162 tests in an
isolated tree using the production interpreter in234.739s, then repeated the
installed suite in220.255s. A follow-up review also repeated focused tests with
no source change. These are actual recorded durations, not a guarantee that
every previous run was redundant: complete artifact/environment provenance
must match before skipping a run. A future comparable release may avoid the
second approximately3m40s suite when that evidence exists.

The old engineering rules explicitly required complete local and server suites
and a deliberate reversal for every fix. They conflicted with the existing
instruction to reuse completed evidence. The new canonical
[workflow](../RELEASE_WORKFLOW.md) resolves this with verification tiers,
failing-before/passing-after regressions, narrowly required reversals and explicit
matching-input criteria. No tests were removed. Accounting changes cannot be
misclassified as formatting; unknown/shared scope escalates to broad testing.

The stop script checks process presence every two seconds and exits early when
the runner is gone;300s is a maximum grace, not an unconditional five-minute
delay. The prior teardown ownership invariant remains a separate open defect.
Do not bypass ownership proof or shorten exit opportunity to conceal it.

## Review and verification scope

Docs-only acceptance: documentation audit, local links, line budgets, diff and
cross-reference review; no trading test suite or mutation test is appropriate.
Reviewed unchanged restart, small formatting fix, financial consumer change,
missing/mismatched test evidence, dependency drift, explicit stop/Command1,
failed fresh gate and incomplete live observation. Fresh account checks and
runner admission, parity/verified backups and single-writer rules remain.
The procedure requires no new software guard, API request or reporting schedule.

During this task supervisor21730 and runner21744 were present, elapsed about
15,704/15,703s. This proves process presence only; current orders/positions were
not queried and remain unknown to this task. Loaded marker from parity remains
ab3f7f352. Initial parity passed with a verified private recovery copy captured
2026-09-24T00:47:13Z. No runtime source deployment or restart is needed.

Next authorized release: record preparation time, stopped time and post-start
acceptance separately. Faster release time is a target pending that measurement,
not a claim of faster trading or lower live REST usage.

## Parallel full-suite runner

The follow-up implementation adds `scripts/run_tests.py`. It discovers every
`test_*.py`, gives each file an isolated subprocess and runs up to four files at
once. Failed files print their complete unittest output immediately; successful
files stay compact, and the final report lists the slowest modules. A successful
process that discovered zero tests is refused. `-j 1` retains a serial-isolation
diagnostic path and filename globs provide focused runs.

The first Windows-native full attempt was invalid because that unrelated Python
3.14 installation lacks the bot's `httpx` and `tzdata` dependencies. This was
environment evidence, not a code/test failure, and is excluded from acceptance.
Using the installed production WSL Python3.12 environment, a four-file mixed
sample passed55 tests in6.10s. The complete four-worker run then passed **2,166
tests across93 files in150.39s**. The prior installed sequential result was
2,162 tests in220.255s; the new runner adds four self-tests, so this is not an
identical-count benchmark, but measured wall time fell69.865s (31.7%).

The slowest remaining file is `test_live_commissioning.py`:488 tests in94.47s.
Further class-level sharding could reduce the critical path, but would need its
own isolation/order-dependence proof. It is not included in this conservative
first release. The runner makes no venue request and changes no production
source, configuration, launcher, business rule or runtime safeguard.
