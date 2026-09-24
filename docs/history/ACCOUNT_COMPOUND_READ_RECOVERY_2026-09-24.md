# Compound account-read recovery — September 24, 2026

## Authority and scope

Operator “Ok work on it” authorizes the scoped repair following the
[noon log review](HYBRID_LOG_REVIEW_2026-09-24.md). It does not authorize a
deployment, stop, restart, order mutation or ledger repair. Preserve size15/32,
hybrid source, four fixed $30 settled-loss windows, all capacity tiers,
entry filters, prices, deadlines, account freshness and polling intervals.

## Evidence and chosen design

The prior 29.38-minute sample had 40 account HTTP429s at 3.307 total requests/s,
stale equity proof and two timed IOC acknowledgments 24–35 seconds late.
That does not identify the venue's limiting rule or prove hybrid caused it.

Code review and failing-before regressions identify avoidable work: a compound
execution refresh starts its exact-order walk before discovering that a required
positions/history endpoint is already cooling down. Financial refresh similarly
starts its balance/positions/balance bracket before discovering a known history
cooldown. These reads cannot complete the intended proof.

Chosen fix: inspect existing shared endpoint backoff before starting the compound
read. This adds no API request, freshness, pacing permit or recovery-probe lease.
Actual requests retain atomic admission. A concurrent failure/probe after the
peek still blocks at the original request boundary. No caching, broader proof
sharing, changed limits or new parallel account writer is introduced.

Execution dependencies are open orders and positions, plus activities in REST
and hybrid modes. Balance-only cooldown does not block execution proof.
Financial dependencies are balances, positions and activities. Ambiguous intent
or absent-order repair deliberately bypasses the optimization: positive exact
order facts can still resolve ownership or permit the existing cancel retry
while position reads are unavailable. Missing orders never mean cancelled.

Optional earnings refresh now checks the current clock and existing position
urgency guard before starting a potentially blocking request. In REST/hybrid,
that guard includes reducer gaps, inventory/tiny deadlines and kickoff. A
deferred report remains due and resumes when urgency clears; failure handling
and normal interval remain. Tradeoff: pending reward reporting may become older
during sustained exit urgency. Mandatory financial/risk proof is not skipped.

## Review and verification

- Original implementation failed both wasted-read regressions: exact-order
  walk and equity bracket were called despite a known required-endpoint cooldown.
- Initial affected run passed 163 tests across account-source, backoff,
  reducer continuity and the initial new regression set.
- Expanded new file passed 17 tests, including identical REST/hybrid cases,
  known cooldown, independent balance circuit, absent-order repair, reporting
  deferral/resume/failure/stop, invalid endpoint, shared-state read-only behavior
  and a successful peek followed by a later admission rejection.
- Existing full-suite coverage exercises actual deadline guards, partial fills,
  exact cancel/reducer transitions, admission races, freshness, settled risk,
  recovery and shutdown. No live faults or synthetic fills were induced.
- Review: no new secrets/log payloads, mutation route, durable schema, rollback
  migration or ownership relaxation. The shared peek uses an off-loop shared
  file lock and existing bounded backoff; it does not modify the shared state.

Full suite completed September24 around16:33Z: **2,185 tests / 94 files passed**
in272.14 seconds. No failed files. Test command:
`wsl -d Ubuntu-24.04 -- bash -lc 'cd /mnt/c/Users/rynna/Bots/liquidity-bot && /home/polybot/liquidity-bot/.venv/bin/python -B scripts/run_tests.py -j 2'`.
Tests use fake data and the candidate tree, not the live database or venue.

### Reusable artifact provenance

[Manifest](ACCOUNT_READ_PRECHECK_2026-09-24.sha256) lists 241 candidate inputs:
tracked src/tests/config/configs/scripts/fixtures and dependency inputs plus the
new regression file. Text extensions normalize CRLF to LF; binary inputs remain
exact. Manifest SHA256:
`85b85c5b5d28b0f9744c334febc84bf5e309d14ead8861e8ca8b9b73aaac9fb0`.
Documentation and volatile/secret data are not test inputs.

Environment: Ubuntu-24.04 WSL, production Python3.12.3 virtualenv. Installed:
anyio4.14.2, certifi2026.7.22, cffi2.1.1, cryptography50.0.0, h11 0.16.0,
httpcore1.0.9, httpx0.28.1, idna3.19, pillow12.3.0, polymarket-us0.1.2,
pycparser3.0, PyNaCl1.6.2, setuptools84.0.0, typing_extensions4.16.0,
websockets17.1. Runner sets candidate PYTHONPATH; the editable live installation
still points to the runtime checkout at e57d6ca7a, loaded marker ab3f7f352.
Any deployment must compare this manifest/environment and its import/launch
configuration; reuse does not replace fresh account/start gates.

## Live state and remaining acceptance

At 16:30Z, supervisor21730/runner21744 remained running with the September23
20:25:35Z start and loaded marker ab3f7f352. No restart or runtime-source edit.
Last authenticated exposure is the earlier 16:15:30Z snapshot: 30 flat entries,
four positions with exact reducers and no ledger ambiguity. That is historical,
not a claim about exposure at the end of this implementation turn.

After separate deployment/restart authorization: verify normal flat/ownership
gates, artifact/environment match and live reducer/cancel/exit behavior. Compare
attempts per endpoint and local deferrals through naturally occurring recovery.
Do not claim a measured REST reduction, eliminated 429s or fixed deadline
latency before this release is loaded and observed. Mandatory endpoint outages
can still delay proof/exits; no safe local optimization guarantees venue access.
The intermittent status-feed timeout recovered in the reviewed log and has no
proven local defect here. Prior teardown invariant and unexplained risk-wave
cause remain separate open work; 300-coverage stays paused.
