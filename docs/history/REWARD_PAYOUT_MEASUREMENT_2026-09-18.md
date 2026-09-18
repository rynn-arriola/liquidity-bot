# Reward payout floor measurement — September 18, 2026

## Decision and evidence

The operator chose measurement after support stated that the $1 minimum is
evaluated separately for each market and reward period. Different markets'
sub-dollar rewards cannot be combined to infer qualification. The earlier
aggregate-threshold explanation is withdrawn. Historical authenticated earnings
include PAID rows below $1 (the August10 evidence in API_CONTRACTS includes 37
rows below $0.10); those observations do not establish today's rule or prove
the meaning of a current payout status.

No payout-based entry/retention gate or restart is authorized by this task.
Selection, size, caps, four $25 loss windows and exits remain unchanged. There
is no additional venue request, background collector or database migration.

## Flow and limits

Reuse the selector's normalized-score projections, quote size and 50% haircut.
Sum maximum remaining reward over viable, reward-eligible sides within each
market's selected incentive period. Count viable markets reaching $1/$1.50;
never combine markets or concurrent programs. Ranking/top_viable is independent.

Scan and five-minute logs expose counts. Each successful scan also records
timestamp, market, program, period, rank, viable sides and remaining dollars,
marked enforcement=none. Discord uses two 29-character rows, `Remain est. >=$1`
and `Remain est. >=$1.50`, with qualifying/viable counts. Missing selections omit
the measurements; an observed empty slate shows 0/0.

These are current-share remaining-potential estimates, not earned reward or
guaranteed payment. They assume continuous participation through the model's
accessible window and omit accrued reward. Actual rests, capacity, quality/
admission gates, fills, competition, the five-minute pregame exit buffer and
program allocation can reduce participation. Below $1 on a later scan does
not prove a market will be unpaid. The projection formula is unchanged.

Existing quote observations retain actual-action side projections and rank.
Compare those, the new scan records and eventual authenticated earnings after
complete periods. Preserve ambiguous multi-program attribution; daily market
earnings do not necessarily prove exact period attribution. Never sum
overlapping scan estimates as income. Top-20 and additional-market comparisons
are measurements, not automatic changes to capacity or prioritization.

## Verification and review

The focused selector/report suites passed 53 tests. Coverage includes separate
sub-dollar markets, combining same-market sides, blocked sides, empty scope,
exact $1/$1.50 boundaries, invalid thresholds, missing report data, mobile
width, unchanged ranking/selection and unchanged request count. Reversing the
sum into a maximum made the regression fail (0 qualifying versus 2); restored.
The scan regression verifies counters and per-market evidence. The first full
run exposed legacy partial scan fixtures without the new measurement method;
optional counters now return unknown on measurement failure, and detail-log
failures are isolated. All57 focused top-up/logging checks then passed.
After the correction and completed documentation, all1,781 tests passed in
260.150s using local source with the production WSL Python environment.
Documentation audit and final diff checks passed. Runtime source was not edited.

Final review covers success/empty/boundary/missing evidence, program ambiguity,
stale scans/midnight/changing books, capacity, API/load, ownership, recovery,
shutdown, restart and rollback. No measurement consumer authorizes orders,
sizing, reconciliation, loss or exits. Work is bounded by the evaluated slate;
logs add local output, without new remote I/O or persisted strategy state.

## Runtime boundary

Read-only process verification confirms supervisor239275 / runner239293,
started September18 18:45:19 UTC, manual REST size10/32, loaded marker
`34f52b1d7`. This continuation leaves that process/source untouched. Current
venue exposure was not queried and remains unverified here. Measurement starts
only after a later authorized reviewed deployment/restart; prepared code is
not live data collection.

## Command 1 deployment

The operator subsequently authorized Command 1. Supervisor239275 was stopped
first and runner239293 exited at20:28:50.923905Z with flat=true. The authenticated
20:29:19Z post-stop gate proved zero venue orders/positions and zero ledger
working/position/unresolved/unknown state. A stopped, integrity-checked recovery
copy was captured20:29:25Z (710,578,176 bytes; SHA-256 prefix3675f000ae0f).

The already-pushed release `6323dc302e7fde32dea3a27efebd8e77e673aef4` was
fast-forwarded into native WSL. All204 source/test/config/script hashes matched
Windows and the reviewed commit. All1,781 tests passed in177.946s in the exact
installed checkout. Marker updated only after that success. The second
authenticated20:33:01Z gate again proved flat/clear; restart used explicit
auto/size10/32/REST. Supervisor282304 / runner282322 started20:33:01Z.
No new implementation or strategy/risk/configuration change was made in this
deployment turn. Post-start measurement/trading acceptance follows below.

At20:34:28Z the successful scan reported4 event pages,52,252 nested market rows,
716 executable,302 rewarded,31 books ranked and27 viable;2/27 cleared$1 and1/27
cleared$1.50 in the remaining-only model. Per-market program/period/rank records
were emitted with enforcement=none. Initial and refresh selection books used
public WebSocket data with zero REST fallback. One unavailable pregame market
was guarded by existing lifecycle checks; subsequent orders proceeded.

Authenticated20:38:16.630941Z acceptance,5m15s after launch:31 venue orders across
23 markets, comprising30 flat-market entries and one OPEN SELL10 reducer exactly
covering the sole +10 position. Ledger working/positions agreed; unresolved and
unknown orders were zero. That proves coverage at the sample, not completion of
the entire future exit cycle. The window retained$3.5282 matched trading loss
against$25 and cap30, with no hold or provisional state.

Four completed API windows through20:37:58Z:633 attempts, eight individual-order
404s, zero429s and zero cooldown; rates2.040/2.390/1.912/2.316 requests per second.
The last rate includes ordinary trading activity; no zero-error or long-term
rate-limit guarantee is claimed. The first startup decision latency p95 was
69.819s, falling to14.997s in the next period; this measurement change does not
claim a performance repair. Subsequent scheduled Discord presentation, complete
reward-period earnings comparison and sustained trading/exit/load observation
remain natural acceptance work. No forced fill/failure or strategy change.
