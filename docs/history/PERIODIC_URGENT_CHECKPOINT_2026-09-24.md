# Periodic urgent-position checkpoints — September 24, 2026

## Scope and implementation

Operator authorized "Ok fix it" after the narrowed scheduling investigation.
No new instrumentation, measurement process, broader cache, source-mode change,
trading-policy change, deployment or restart is included. The preceding review
is [here](ACCOUNT_SCHEDULING_REVIEW_2026-09-24.md).

The main loop now invokes a source-specific checkpoint after each due periodic
balance or execution-account stage, including its ordinary failed-read path.
REST/hybrid uses the existing serial stream consumer, preserving task-held
events before queued private updates, then the existing bounded exit-only
urgent-position service. Stop/stream-stop returns terminate ordinary work;
rebind yields to existing recovery. The original pure-WebSocket hook remains
non-servicing. The loop samples the current clock before the next periodic
stage instead of using the pre-balance timestamp after potentially slow work.

No stage publishes partial proof, skips mandatory account reads or resets
polling intervals. The normal due-time calculations remain intact. The helper
is not a new executor, receipt cache, API reader, background task or permission
to retry an unknown order. Required proof and market service inside the existing
exit path remain required; stream recovery uses its existing owner. A flat
checkpoint does not initiate account or market service. Source selection can
change only through the existing explicitly requested handoff and its guards.

This removes the verified scheduling gap, not the duration of an in-flight
request or mandatory exit proof. It cannot guarantee lower aggregate REST load,
zero429s, immediate flatness or better trading P&L. Healthy quoted prices,
entry gates, reducer quantity, deadline definitions, backoff, IOC budgets,
size15/32, four$30 windows, pending-entry tiers and settled-loss accounting are
unchanged. Extra exit service remains finite and governed; ordinary work may
wait for required urgent handling.

## Regression and edge-case review

The new main-loop regression failed on the old source in all eight scenarios:
REST/hybrid, balance/execution stage, newly urgent position/deadline crossing.
The same regression passes after the patch. It uses the existing fake venue
and temporary ledger, not production state. Its successful exit-only mock
flattens the test owner, verifies pending stream processing precedes service,
and confirms both mandatory periodic stages still run exactly once.

The affected test module passes18 tests, seven new tests with additional
subcases. New coverage includes failed periodic reads, stop/rebind before or
during the checkpoint, stream exceptions/stops, replacement task propagation,
held-private-before-queued ordering in REST/hybrid, pure-WebSocket no-op and
flat-state no-read behavior. Existing coverage retains partial/multiple-position
exact quantities, stale/backoff/unknown/isolation constraints, fresh financial
brackets, rollover, and flat-during-service/proof/book exit-only enforcement.

Final source diff is two runner files (37 inserted lines), with scheduling tests
only. No pricing/risk/ledger/gateway/configuration implementation changed.
Rollback restores those source files through the normal stopped deployment
workflow; there is no schema, durable risk migration or new configuration.

## Production-compatible verification

The complete suite runs in an isolated native WSL test copy, never the live
source/ledger. Python3.12.3 from the production virtualenv, candidate `src:tests`
on PYTHONPATH, `nice -n 10 ... scripts/run_tests.py -j 4`.
The [input manifest](PERIODIC_URGENT_CHECKPOINT_2026-09-24.sha256) covers241
source/test/configuration/script/dependency-input files. Its post-test check
must pass before evidence is accepted. The editable installed package path
does not override the explicit candidate import path.

Dependency versions: anyio4.14.2, certifi2026.7.22, cffi2.1.1,
cryptography50.0.0, h11 0.16.0, httpcore1.0.9, httpx0.28.1, idna3.19,
pillow12.3.0, polymarket-us0.1.2, pycparser3.0, PyNaCl1.6.2,
setuptools84.0.0, typing_extensions4.16.0, websockets17.1.

At19:29Z, all2,192 tests across94 files passed in107.67s wall time. All241
manifest entries remained unchanged after the run. Manifest SHA256:
`d8725e79b33c5631c17ae0f74db0af4d271a739f3d5c9bed0d5516f11a02e13f`.

## Live state and next activation

Runner352901 remained running on `f4f2bdb24` at approximately19:28Z. No live
API request or authenticated exposure check was required by this offline fix;
current venue orders/positions are unverified, not inferred from local tests.
No restart or runtime-source deployment. After separate authorization, verify
manifest/environment and saved settings, normal stop/flat/ownership/start gates,
then live acceptance using existing logs. Do not add measurements or repeat
valid complete-suite evidence solely because deployment copies the same files.
