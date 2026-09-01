# Direct 32-slot canary preparation — 2026-09-01

## Scope and live boundary

The operator chose a direct 24 → 32 measurement canary, with reduction if it
creates a problem, instead of repeating 26/28/30 deployments. This document
records preparation only. The laptop runner stayed live at size 10 / 24; no
process was stopped or restarted, no runtime slot file was changed, no venue
mutation was made for this work, and the prepared source was not deployed.

The purpose is to measure the highest comfortable breadth without silently
changing what the strategy trades. Selector ordering, quote size, spread and
price gates, incentive economics, fill model, position timers, entry horizon,
mutation sequencing and reconciliation remain unchanged.

## Complete Retail API inventory

All authenticated Python entrypoints were searched again. Every one either
uses the live runner's limiter or constructs its gateway through the same
POSIX-file-locked host coordinator. No independent authenticated Windows
client is allowed.

| Work | Live class | Scaling at 32 |
|---|---|---|
| balance/positions/balance marked-risk bracket | safety | account-wide/fixed below another page |
| cash-history proof and equal-cash bracket | safety | account-wide/page-sensitive |
| open orders, positions and execution reconciliation | safety | account-wide/page/ambiguity-sensitive |
| per-eight-owner market and incentive batches | safety | three batches at 24, four at 32 |
| streamed-book fallback and exact market reads | safety | market/event-shaped; conservatively scaled `32/24` |
| catalog/incentive discovery and candidate books | background | burst/page/candidate-shaped; conservatively scaled `32/24` |
| maker creates and cached revalidation | entry | business-event-shaped; conservatively scaled `32/24` |
| cancels, passive reducers and IOC exits | safety | business/position-shaped; conservatively scaled `32/24` |
| missing-order retrieval, lifecycle/settlement and recovery probes | safety | failure/event-shaped; included in tail/busy gates |
| runner earnings refresh | background | fixed |
| hourly reward table | report | at most 12 reads; average `12/3600`, active cap `0.25/s` |
| manual reports/census/shadow/scalp measurement | report | absent unless running; cannot create a second host budget |
| five-minute Discord summary and TV dashboard cache | local only | zero Retail API calls |
| UI-maintenance probe and docs changelog monitor | public web/docs | not an authenticated Retail API call |
| Kalshi collector | separate venue | not Polymarket API demand |

The hourly worst path is one earnings call; up to four historical cash pages;
two balance calls, one position call and up to four current-day cash pages: 12
calls. It now uses a distinct report lane that requires at least one unused
aggregate interval and permits at most one request every four seconds. A
report claim writes only `next_report_at`/report cooldown. It cannot publish a
four-second delay onto discovery, entry or safety; a higher-priority request
arriving immediately after a report claim can wait at most the normal one
aggregate interval (`0.25s`). The wrapper may use 45 minutes of its hourly
interval rather than competing for an exact minute.

## Empirical projection

`scripts/api_capacity_report.py` parses only complete `[api-budget]` windows,
requires category totals to equal attempts, and refuses a category not
classified in the source model. It makes no API or database call.

The latest twenty healthy 24-slot windows covered 1,595.6 seconds:

| Metric | Observed 24 | Conservative 32 projection |
|---|---:|---:|
| median attempts/s | 1.852 | 2.361 |
| p95 attempts/s | 2.295 | 2.912 |
| maximum attempts/s | 2.485 | 3.189 |
| maximum with coincident hourly report | — | 3.329 |
| maximum local queue | 8 | not inferable |
| failures / confirmed 429s | 0 / 0 | not inferable |

Observed category rates were led by market books (`1.076/s`), then balance
(`0.166/s`), discovery (`0.108/s`), positions (`0.103/s`), creates (`0.098/s`),
cancels (`0.082/s`) and execution history (`0.079/s`). Fixed account work was
held fixed; eight-owner batches stepped by `ceil(32/8) / ceil(24/8)`; every
other market/event/failure-shaped category scaled by `32/24`.

## Burst attribution and control

The read-only report retains event tags emitted between complete limiter
windows, ranks the highest sustained request intervals and prints exact UTC
times, top API categories, logged signals and conservative drivers. This adds
no endpoint, credential, database read or live-process instrumentation.

The latest 100 24-slot windows showed a maximum `2.801/s` at
`2026-09-01T15:38:12Z`, led by 165 book reads and accompanied by runtime
recovery, market-retirement and stale-book signals. The other highest windows
separated selection/refill, unresolved-order retrieval and mutation churn from
the book-heavy baseline. Their conservative 32-slot maximum was `3.601/s`, or
`3.739/s` with a coincident opportunistic report. This longer tail sample is
why 32 remains a canary rather than a stable setting even though no failure or
confirmed 429 occurred.

The shared 4/s governor is the universal burst control. Work is then controlled
at its source: stream freshness plus bounded empty/dead-market retry for books;
caching/batching/coalescing for selection; exact-ID bounded proof for ambiguity;
and entry replanning for avoidable mutation churn. Safety work is not traded
away for capacity: nonzero positions, cancels, reducers, IOC exits, account
proof and recovery keep priority. Reports yield on their separate lane. If
those mandatory classes contend, the correct control is slot rollback.

The deterministic model is deliberately harsher:

| Scenario | 24 | 32 | Meaning |
|---|---:|---:|---|
| normal | 2.17/s | 2.47/s | average fits, but ten quiet-book deadlines miss at both widths |
| busy/recovery | 4.00/s | 4.73/s | 32 can saturate the internal 4/s gate |
| full public-stream silence | 9.24/s | 11.94/s | whole-slate REST fallback is not viable |

Therefore 32 is suitable only for a live bounded canary, not promotion to the
durable maximum. The official Retail authenticated ceiling remains 20/s per
API key; the bot intentionally keeps the much lower 4/s host gate because
earlier Cloudflare 1015/429 episodes occurred below the published aggregate
number and on account endpoints during restart/lifecycle bursts.

## Width-dependent mechanics and risk

Prepared source accepts whole widths 20-32, keeps the missing-file/default at
20 and refuses 33 before venue access. Thirty-two opens four private streams
of exactly eight markets; the public market subscription remains below its
documented 100-market bound. At size 10 / 32 the existing formulas derive:

- portfolio exposure: `(32 + 10) × 10 = $420`;
- per-market exposure/loss: `$90 / $40` (unchanged from size 10);
- realized and marked account thresholds: `8 × 10 × 32 / 20 = $128`;
- seven-day operating reserve: `$896`;
- comfortable balance: `$320 + $100 + $896 = $1,316`;
- gross fee observation: `$13.33` (non-enforcing);
- taker-exit ceiling: `40` and early-cross loss: `$8` (size-derived,
  unchanged by breadth).

The TV dashboard accepts the runtime width and renders 32 slots in two rows of
16 on its existing no-scroll instrument panel. The five-minute Discord summary
continues from local/cache state and does not become report-lane work.

## Verification and business-output review

The exact WSL environment passed all 1,052 tests. Three deliberate reversals
were detected: restoring the 24-slot parser ceiling failed the 32 boundary
regression; mapping report claims onto the background clock failed the exact
shared-state ownership regression; and erasing burst attribution failed its
timestamp/driver regression. The report-lane reversal initially revealed that
a timing-only assertion was too broad, so the permanent test now also requires
`next_report_at` and forbids report claims from writing the background or entry
clock.

The final edge audit covered success/failure, empty/full categories, 20/32/33
boundaries, timing/races, partial/incomplete evidence, API/load, recovery,
shutdown, restart, rollback and configured-width interactions. The decision,
selector, ranking, quote economics, size, position timers, order ownership,
reconciler and mutation paths have no behavioral diff. The only live-runner
source change is an additional secret-free report-cooldown telemetry field;
32 remains inactive until a later authorized restart reads that configured
width. A report that wins an actually idle aggregate claim can impose only the
ordinary 0.25-second shared interval on traffic arriving immediately after it;
it cannot write a trading lane clock or cooldown.

## Canary acceptance and rollback

Before a separate authorized restart: complete the exact WSL suite, deliberate
reversal, edge-case audit and final diff review; push/deploy only afterward;
prove zero venue positions/orders and zero unresolved/unknown ownership; and
preserve `v1.1.0` plus loaded 24 as rollback references.

Reduce immediately on any of:

- a capacity-linked failure or confirmed 429;
- `max_waiting > 1` in two consecutive complete windows;
- a missed reducer, pre-kickoff flatten, account proof or other safety deadline;
- market/private stream coverage loss or persistent account ambiguity;
- increased `execution_state_unknown`/quarantine;
- unacceptable decision latency, CPU or thermals;
- lower reward per resting side-hour against a comparable 24-slot window.

Normal acceptance also requires exact 32-owner scope, four healthy private
shards, ordinary lifecycle refill, no unresolved/unknown state and continued
business-output equivalence. The first reduction target is 24; use the earlier
22 known-good rollback if the failure matches that procedure.
