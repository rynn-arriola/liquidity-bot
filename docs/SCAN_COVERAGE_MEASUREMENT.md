# Broad public-book coverage measurement — September 23, 2026

## Decision and scope

The operator accepted measuring wider candidate coverage before expanding live
selection. This is a separate opt-in executable, not a new runner dependency.
Production remains on its stable REST account source; the saved hybrid review
branch is independent. No deployment, restart, new strategy or wider live
entry scope is authorized by this measurement.

The current selector examines all discovered executable metadata/rewards, then
deeply evaluates at most100 books. It retains a ranked head and rotates a tail;
this is not blindly taking the first100 API rows. Wider continuous book coverage
might find a better candidate sooner, but that benefit is not established.

## Architecture and alternatives

- Reuse the exact existing selector's event discovery, exact-symbol incentive
  batches, metadata gates, candidate order and rotating100-market shortlist.
  A measurement-only subclass captures candidates and returns no detailed rows,
  so inherited discovery cannot fetch REST books.
- Include every baseline candidate, then add ranked deferred candidates up
  to300. Preserve the complete candidate count and report everything beyond
  the diagnostic cap as unwatched. This is not exhaustive coverage if more exist.
- Watch groups of at most100 with separate read-only public full-book feeds,
  keeping each connection open for the bounded observation. The official
  [market WebSocket contract](https://docs.polymarket.us/api-reference/websocket/markets)
  documents100 markets per subscription, not100 total. Three separate adapters
  avoid changing the production stream's single-subscription implementation.
- Freeze metadata once, conservatively age it from discovery start, expire it
  after120 seconds, and sample received books once per second for at most120
  seconds. Zero REST book calls, renewal requests, reconnects or retries.
- Use canonical opportunity evaluation and ranking for both scopes at the same
  instant, with original book receipt times strictly less than3 seconds old.
  Positive lifecycle changes already present, kickoff, reward end and ET-day
  rollover remain binding. Metadata is not refreshed during observation:
  results are ranking-only, never current executable lifecycle proof.
- A missing/stale/future/mismatched book is unknown, not an unprofitable market.
  An incomplete baseline suppresses the claimed improvement list. Additional
  missing candidates and cap truncation keep wider coverage explicitly partial.
- Only report-lane event/incentive reads are exposed. The explicit existing
  production limiter path shares the4/s host governor and opportunistic report
  lane. Discovery times out after120 seconds; no retry follows failure.
- Single-instance POSIX lock is keyed to that shared limiter, not the worktree.
  Feed failure clears only that shard. Cancellation closes all feeds, including
  startup-in-progress, with bounded close waits. No ledger, account-state,
  order, cancel, private-stream or Discord access is used.

Directly increasing the production book cap was rejected: it changes live
selection latency before coverage/load evidence exists. Wiring a persistent
watchlist into trading is a later phase; it needs safety-priority scheduling,
renewal/load, reconnect-generation, metadata refresh and freshness acceptance.
This diagnostic does not claim to solve that production phase yet.

## What the output means

`baseline_top` is the modeled first scan using current configured policy,
not authenticated live orders and not the live scanner's current rotation
cursor. `broader_top` uses exactly the same policy/books with wider scope.
`outside_baseline_better_or_fills_gap` is null if baseline coverage is unknown;
otherwise it lists outside candidates that rank higher or fill modeled empty
capacity. It is not permission to cancel retained markets.

Every row preserves program kind, per-market projected reward, receipt time,
baseline membership and viability. Projections are not paid rewards, saved
losses, promised profit or additive independent pools. No $1/$1.50 gate, new
sport/type, size, cap, risk window, exit clock or account-source change occurs.

The report also records frames, shard failures, missing coverage, metadata age,
evaluation time, diagnostic CPU time and REST telemetry. Quiet books may age
out without renewal; that is availability evidence, not negative economics.

## Operator-run procedure

Run only on the production WSL host with its existing governor and a separate
reviewed source checkout. Keep credentials in the established secret loader;
never place them in command arguments. Set the loader's credential-file path
when the separate checkout lacks the normal sibling layout. Specify the
verified running size and slot settings, and confirm configuration parity.

```text
PYTHONPATH=<reviewed-checkout>/src <production-venv>/bin/python -B \
  -m liquidity_bot.scan_coverage --quote-size 15 --slots 32 \
  --watch-limit 300 --seconds 60 \
  --limiter-state <live-repo>/data/api-rate-limiter.state \
  --output <new-private-output-file>.json
```

The output must not exist; no prior evidence is overwritten. Native Windows
refuses rather than bypassing POSIX coordination. There is no recurring task,
auto-start or implicit retry. A blocked/incomplete discovery ends the attempt.

## Acceptance and next phase

Unit/integration tests cover canonical shortlist parity, rotating baselines,
300/100 subscription bounds, deduplication, empty/over-cap universes, superior
outside candidates, missing baseline suppression, stale/future receipts,
metadata expiry, kickoff/program-end/midnight, day-of priority, discovery429,
startup failure/cancellation, disconnect/scope violation, close and no REST
books. Reversing freshness and baseline-completeness guards makes both focused
regressions fail; both guards are restored.

Final verification:22 focused tests and all2,021 tests in the production WSL
virtual environment pass (complete final suite293.605 seconds). An earlier
intermediate full suite passed2,015 tests. The two deliberate reversals failed
exactly as intended. Final documentation/whitespace audits pass.

A short sample can validate transport/coverage only. Before live broadening,
collect separately approved representative windows and compare matched ranking
coverage, candidate freshness, response/load, fill-to-reducer/exit latency and
resting time. Keep price/exit/risk policy unchanged. Do not infer guaranteed
profit or zero429s from one snapshot.

## Dedicated final edge-case review

- Production isolation: the new module has no importer in the runner or
  launcher. Compared with `stable-rest-2026-09-23`, the only source/test additions
  are the diagnostic and its tests; all existing execution/configuration files
  match. The hybrid branch remains unchanged at `f63036ac2`.
- Selection/authority: current gates, canonical day-of-first ordering and
  policy are reused, but results never return to the live selector. No ownership
  or exposure proof is inferred from public data; top32 is a ranking comparison,
  not32 entries or permission to replace retained orders.
- Timing/partial state: same-sample books and clock, strict receipt aging,
  conservative metadata lifetime, explicit omitted scope, expired programs and
  ET rollover. Baseline gaps suppress improvement claims. No paid-net claim.
- Failure/load: missing books never trigger REST, discovery uses the shared
  report lane and fixed timeout, errors do not retry, failed shards lose their
  books, caches are bounded to300. Measurement can consume bandwidth/CPU;
  separate process isolation does not prove zero performance impact.
- Shutdown/restart: startup cancellation, partial startup, disconnect, foreign
  book and close failure have regressions. All close attempts finish before
  cache release; a failed close is reported. No automatic restart or durable
  order state exists. Ending the process cannot stop the trader.
- Rollback/security: stop/omit the opt-in executable; no production rollback
  is needed. Preserve private JSON outside git, never serialize credentials or
  raw error payloads, and retain the single live host limiter/measurement lock.

## First bounded production-host observation

September23 13:40:47–13:41:46 UTC, size15/32 policy,60 one-second samples:
38 eligible candidates, all38 inside the modeled100-market baseline. All38
delivered books;190 frames, zero shard/close failures. Three samples had complete
fresh baseline coverage, with28/28/29 viable markets and zero outside-baseline
improvements because there were no outside candidates. Subsequent quiet books
became stale; conservative metadata age passed120 seconds near the end and
those rows correctly became unknown. This is not60 fully comparable samples.

Discovery consumed16 successful REST reads (3 events,13 incentive batches),
zero REST books, zero mutations and zero429s in this diagnostic. Shared governor
confirmed active; mean rate over the approximately143-second whole diagnostic
was0.112/s. Discovery/report admission accounted for about83 seconds before
book sampling. Diagnostic CPU over book observation/connection cleanup was
0.234 seconds; this does not establish production entry/exit performance.

The100-market cap was not binding in this particular eligible universe.
Three-feed/>100 venue acceptance and peak-universe opportunity benefit remain
unmeasured. Private JSON is retained at the production host's ignored
`data/scan-coverage-2026-09-23-first.json`; no ongoing collector remains.
Production supervisor23308/runner23326 and marker`6deb707c6` were unchanged
afterward. Current venue exposure was not queried; do not infer flatness.
