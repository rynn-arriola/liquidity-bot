# September24 refill scheduling and per-429 flow provenance

## Authority and deployment boundary

Operator: "Ok fix it" and explicitly requests tracking where each429 comes
from and which flow triggered it. Prepared only; no deployment/restart was
authorized or performed. Supervisor405/runner419 remain on`7f68c06e9`, size15/32
hybrid, four$30 settled-loss windows. The latest authenticated00:01:17Z
September25 (20:01ET September24) snapshot had18 orders:14 flat entries and
four exact reducers for four positions; zero unresolved/unknown, ledger18/4.
This timestamped observation is not a current flat/start gate.

Diagnosis: [refill latency investigation](REFILL_LATENCY_INVESTIGATION_2026-09-24.md).

## Implemented scope

- Post-flat requests now require at least one previously held owner to become
  positively flat. A quantity correction or restored exact reducer alone no
  longer requests a scan. Remaining urgent inventory still blocks discovery.
- The ordinary existing-owner decision/repair pass precedes incremental
  top-up, including admission retries. Stop and stream-recovery requests bar
  discovery and prevent a new scope commit across selection/proof awaits.
- Recent complete selections may supply fewer freshly revalidated replacements
  than all free slots. A recent empty remainder may defer urgent discovery;
  original120s expiry is retained, and the next scan is bounded by that expiry.
  Scheduled full scans remain full, and optional economic-idle eviction retains
  its normal cadence. Zero supply cannot release optional owners or rebind.
- Every429 passing through the shared REST result recorder emits one local
  `[api-429]` JSON event: UTC time, PID, category/templated endpoint, read/mutation,
  traffic lane, nested workflow, response duration, Retry-After and whitelisted
  server IDs including CF-Ray when supplied. Static decorators propagate flow
  through async read workers and reset context on success/error/cancellation.
  Reconciliation labels distinguish periodic, decision, startup and other
  reviewed reasons; scoped position proof, cash history, catalog position watch,
  discovery/rebind, mutation decisions and recovery retain their flow ancestry.
- Aggregated `failure_categories` remains unchanged. Untagged/custom callers
  explicitly say`unattributed`; no guessed flow or retrospective trace. This
  covers the governed REST boundary, not every third-party/raw WebSocket call.
  Local catalog deferrals log their specific LOCAL cause but are not counted
  as venue429s. Existing `requests_sent=0` describes the deferred request, not
  the entire partially attempted scan.

No raw URL parameters, market/order IDs, payloads, auth headers or credentials
enter the new429 record. Server IDs are bounded/whitelisted; logger failure
cannot bypass cooldown or change mutation handling. No additional API request,
polling process, source switch, limiter change or automatic retry was added.
The new records become live only after a separately authorized deployment.

## Verification and final review

The original four targeted regressions failed on unchanged runtime source:
false post-flat, partial-cache fallback, empty-cache fallback and scan-before-
decision scheduling. They pass after repair. Final dedicated coverage is ten
refill cases and seven diagnostics cases, including actual-flat preservation,
future/expired caches, no-op/no-rebind/original expiry, stop before/during
selection and proof, all eleven request-category examples, context reset/worker
inheritance, header redaction, real gateway single-send429 handling, successful
mutations and logger failure. Existing catalog-watch tests retain changed-fill,
inflight cancellation, source/scope/deadline, ownership and critical-failure
coverage. One terminal-scan test mocks the newly preceding decision sweep to
retain its flat fake account; terminal/no-retry assertions remain.

Final isolated stage`/tmp/liquidity-refill-review.UrhW5V`, Python3.12.3,
Ubuntu24.04/WSL and production venv dependencies; no live credentials/database.
`nice -n 10 .../.venv/bin/python -B scripts/run_tests.py -j 4` passed
**2250 tests /98 files in108.28s**. All247 inputs match Windows exactly:
[manifest](REFILL_SCHEDULING_FIX_2026-09-24.inputs.sha256).
Suite SHA256:`9d38d901225478e4a0ce393d3b687f6d5df5123221dbc57345175ca4e390e21b`.
Production dependencies: polymarket-us0.1.2,httpx0.28.1,websockets17.1,
pillow12.3.0; full environment retained alongside the isolated suite log.
An earlier2248-test full pass preceded the final stop-race tightening; the2250
result covers the final source. No source changed after this final pass.

Reviewed preserved boundaries: true-flat/source semantics, clock ordering,
cache expiry, optional release with short supply, real/local429 classification,
single mutation submission, secret redaction, stop/rebind races, unchanged
account proof/cooldown behavior and rollback compatibility. No schema migration,
business filter, price, quantity, GTD, risk/window, exit timer or polling change.

## Limits and next acceptance

This is a focused scheduler repair, not a new concurrent trading engine.
Necessary discovery and pre/post-bind reconciliation still take time. They
retain the existing read-only position watch/interrupt checkpoints and both
ownership proofs; no new mutation is introduced inside collector callbacks.
Fully resumable discovery/incremental socket handoff is not implemented.
Benefit is fewer avoidable scans and no retry-before-decision starvation,
not a proven end-to-end latency bound or elimination of venue429s.

After separately authorized activation: use existing scan/decision/API/reducer
telemetry and new429 flow records; require normal order refills, exact reducers,
no persistent ambiguity, bounded retries and unchanged risk behavior. Compare
matched workloads before quantifying REST/resting-time improvement. No induced
fills, throttle tests or new collector. Restore prior code only through normal
reviewed deployment; durable state and risk windows never reset.
