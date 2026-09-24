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

## Authorized deployment and restart

Operator subsequently authorized "Ok u can restart." The existing supervisor
was stopped and runner352901 received SIGTERM; normal shutdown completed at
20:19:13Z with `flat=true`. No force kill or ownership teardown failure occurred.
Two queued shutdown notifications were undelivered; that does not negate venue
proof. Authenticated20:19:40Z showed zero venue orders/positions, unresolved
intents, unknown orders, ledger working orders and ledger positions.

The stopped-runtime recovery copy captured20:19:54Z passed integrity and copy
verification (1,191,120,896 bytes, SHA256 prefix `e871926f7c90`). WSL then
fast-forwarded cleanly to the pushed `ebb2f480de1d5682cc799db5703a2ddb393c56e0`.
All241 manifest inputs matched the tested stage, Windows and commit after
normalizing expected Windows CRLF/Linux LF differences. The initial verifier
compared normalized runtime data against raw test hashes and flagged
`pyproject.toml`; correcting the verifier to validate raw tested bytes first,
then compare normalized contents, proved no source/config drift. No test or
trading source was changed to bypass verification.

The production interpreter, all recorded installed package versions, imports,
saved15/32 settings and fixed$30 risk tiers matched. The installed scheduling
module passed18 tests; complete2,192-test evidence was reused rather than
repeated. Only then was `DEPLOYED_COMMIT` advanced to `ebb2f480d`.

The second authenticated flat/clear gate passed20:21:53Z. The launcher started
supervisor420964/runner420980 at approximately20:21:55Z using
`./scripts/start_commissioning.sh auto 15 32 hybrid`. Existing quality tracking
remains enabled; no new measurement or source switch was introduced. Startup
preserved the12:00–18:00ET durable window loss$20.5162/$30 and cap15, with no
HARD/provisional hold. Restart did not reset settled loss or loosen its tier.

First natural position observation at20:23:03.546Z was+15; its normal passive
SELL reducer acknowledgment followed at20:23:05.331Z (1.784s). This is one
observed episode, not comparative latency proof. An unresolved cancel read
briefly returned404 and positive terminal stream evidence followed; acceptance
was followed by authenticated zero unresolved/unknown state in the live checks.

### Initial live acceptance

At20:24:01Z authenticated reads showed nine venue orders and one+15 position,
covered by one SELL15 order; unresolved/unknown state was zero. Ledger13 versus
venue9 was sampled during fresh creates; sequential reads are not an atomic
venue/ledger parity proof. The first position was observed flat20:25:40Z.
Another+15 position received a normal reducer replacement and was observed
flat20:26:22Z. A new partial-fill episode changed+1.77 to+3.54, then-11.46
while the original opposite quote filled. The building-order cancel was
acknowledged20:26:20Z, terminal evidence arrived20:26:23Z, and an exact BUY11.46
reducer was acknowledged20:26:25.637Z (1.888s after reversal observation).
Flat was observed20:26:26.349Z. This does not establish every future race/deadline.

The20:26:25Z sequential authenticated sample spanned that reversal:16 orders,
one-11.46 position,14 flat-market orders and two earlier positioned-market
orders; zero unresolved/unknown state. It must not be interpreted as simultaneous
post-replacement exposure or exact ledger parity. Subsequent runner records
confirm the old order completed and the new exact reducer/flat transition.

The first API window recorded2.135rps and zero429s; the20:25:40Z window recorded
3.253rps and one `cash_history@429`, with zero shared cooldown at reporting time
and1.9s remaining endpoint backoff. Normal trading and reducers continued.
Transient order-retrieve404s also occurred and recovered. No lower-rate or
zero429 guarantee is claimed. Long-duration/deadline acceptance remains normal
live follow-up using existing logs, not a new collector or startup blocker.

Later acceptance:20:27:03Z recorded one additional `balance@429` (2.827rps),
then20:28:08Z recorded zero429s (2.580rps), no endpoint/shared cooldown.
Thus two429s occurred in the observed API summaries, not zero. At20:28:34Z
authenticated state had14 orders, zero positions, zero unknown orders and one
unresolved intent sampled during live activity; do not claim an ambiguity-free
instant at that timestamp. The runner remained live and processing orders.
Final parity passed on documentation commit `a2fc4c3df` and loaded source
`ebb2f480d`; the20:28:07Z integrity-verified recovery copy was1,191,583,744 bytes,
hash prefix `04231ce78f84`. The offline workstation was intentionally skipped.

## Low-order follow-up,21:27Z

Read-only check after the operator noticed fewer orders: same runner420980 and
loaded marker; authenticated21:27:14Z showed15 orders:14 flat entries and one
exact BUY9 covering the sole-9 position, zero unresolved/unknown state.
The settled-risk row at21:27:56Z held P&L-$17.3512, cap15, no HARD trip.
Logs establish cap15 was first reached18:27:01Z (14:27ET), loss$20.1472,
before this20:21:55Z restart. The existing minimum-of-persisted/current-tier
rule retains the reduced cap after gains; the next scheduled window is18ET.
No risk or selection rule was modified in this release.

Existing logs also prove higher cancellation activity. Selecting API summaries
by end timestamps19:19–20:19Z versus20:22–21:22Z yields10,531 attempts over
3,612.0s versus10,348 over3,569.8s (both about2.9rps),28 versus35 HTTP429s.
These are complete summary intervals, not exact matched samples or IP-wide
traffic. Account-new-risk-paused CANCEL decisions numbered29 versus66;
expected-net-not-positive14 versus85; touch repricing11 versus48. Counts are
logged decisions, not distinct orders or confirmed cancellation completions.
At21:26:46Z a cash-history429 was followed by unavailable equity evidence,
entry pause/cancellation, then fresh entry placements21:27:00Z. Pregame proof
gaps also occurred. This explains temporary dips below the cap but does not
establish that the new checkpoint caused the increased churn or that each
economic refusal was incorrect. Follow the existing proof/economics diagnostics
before changing guards; no new collector, code change, stop or restart here.
