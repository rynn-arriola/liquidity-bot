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
