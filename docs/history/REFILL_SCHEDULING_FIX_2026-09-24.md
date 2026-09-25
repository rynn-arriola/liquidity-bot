# September24 refill scheduling and per-429 flow provenance

## Activated restart and low-order review — September25 UTC / September24 ET

The operator subsequently authorized "Ok restart". Stopped authenticated gate
00:06:15Z and final gate00:09:18Z proved zero venue orders/positions and zero
unresolved/unknown/working ledger state. Reviewed source`ef5a73f3d` was pushed,
deployed with247 matching inputs and the existing2250-test production-environment
evidence; installed17 focused new tests and21 catalog-watch tests passed.
Supervisor4354/runner4368 started about00:09:20Z, auto15/32 hybrid. Runtime HEAD
and DEPLOYED_COMMIT match. Window18–00ET logs hard30/cap30, matched-fill basis,
open orders/positions excluded. No business filters/configuration changed.

The old run's00:05:30Z fresh scan already found11 viable markets/19 sides from
16 books, while its retained scope held20 prior owners. Restart00:09:48Z found
11 viable/18 sides from15 books;00:10:33Z found11/17. By00:16:56Z it found13/24
and committed two added owners. Thus raw retained-owner counts and fresh
selection counts are different; the initial explanation that missing markets
had simply expired was too broad. Existing stricter fresh-entry gates versus
retention can reduce scope across restart. Exact per-market causes for every
omitted owner remain unavailable: full discovery capture rejects catalogs over
20000 rows, while these catalogs contain28618–29051. Scan/book measurements
remain available. This diagnostic gap is not a trading filter or restart change.

The Fomin/Rehotun trade sequence was initially misread from intermittent snapshots.
Positive fills show BUY15 at0.48 (00:11:22), SELL15 at0.49 (00:12:37), flat proof,
new two-sided entries00:12:42, SELL15 at0.50 (00:13:44), then BUY15 at0.47
(00:14:31). These are two completed cycles, not duplicate exit overselling.
A transient hybrid quantity observation -30 corrected to REST -15; no oversized
mutation was seen in this sequence. Do not conflate that observation with venue
exposure or claim it establishes all hybrid edge cases safe.

Authenticated00:19:13Z proved22 venue orders:21 flat entries plus one exact
SELL12.42 reducer for the sole+12.42 position, zero unresolved/unknown state.
Ledger21 working versus venue22 is a non-atomic live observation, not equality
proof. The earlier fresh in-flight intent cleared. Runner remained healthy for
over nine minutes. All completed API windows through00:19Z had zero429, at
2.100–3.380 requests/s, with individual-order404s still present. No matched-load
speedup or permanent zero429 claim. No further trading code or restart was
performed during the double-check; local analysis and handoff only.

## Authority and deployment boundary

### Subsequent read-only scan-to-order trace,00:24–00:31Z

At00:24:57Z discovery returned28481 raw nested market records; metadata checks
retained727 executable markets,653 had reward records,15 reached detailed book
evaluation and12 passed at least one side (20 viable sides). The detailed rows
were day_of. Three whole-market refusals were both-side entry-quality, one-cent
spread and extreme price; four other sides failed entry quality. The precise
split of638 pre-book exclusions is not archived because of the full-catalog
measurement bound described above. Supported reward period, active time/horizon
and catalog spread checks all precede books; the unchanged100-book budget was
not binding. Missing catalog BBO is allowed to reach book evaluation.

The00:25:03 binding retained13 owners and added Kim/Perov, yielding14 assigned
markets. Scan12 versus scope14 is explained by retained owners. Authenticated
00:29:53Z returned16 orders, all within that scope:13 flat entry orders across
eight markets plus three exact reducers for positions -15,+6,-15. No unknown
orders/unresolved intents. Three assigned owners had no order: Sir/Ur and
Rak/Neb approached00:30 kickoff (both later retired00:30:20), while Fomin/Rehotun
had flattened00:29:32 and resumed two orders by00:30:14. Later00:29:58 scan had
10 viable candidates; retained scope reduced14→12 at the lifecycle retirement.
These timestamps are sequential observations, not an atomic cross-API snapshot.

187 durable entry quote observations since startup all contain retained-value
ratios>=0.70 (minimum0.765605639344262). Existing working flat entries joined to
their quote observations. Market ranking/side eligibility is re-evaluated with
fresh data before placement; a side refused during scan may qualify later.
Mencheyuhwan/Onychofunkoz bid shows repeated quality-based cancellation/re-entry
around00:27–00:30; this confirms churn but does not establish a new defect or
authorize changing the70% rule. Investigate its score inputs/threshold crossings
and resting-time cost using existing records before proposing a business change.

The optional entry-admission archive also tries to read clientOrderId/side from
the wire payload, which contains neither field (side is encoded in intent;
client identity remains in the durable ledger). These diagnostic fields are
null, preventing exact admission-record/order joins through those fields.
Durable order/intent/quote joins still work. A future diagnostic-only repair
should propagate local identity separately without modifying the API payload.
No trading code, config, venue mutation or restart was performed for this trace.

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
