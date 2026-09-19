# Priority 3/4 measurement improvements — September 19, 2026

## Authorization and design

After the [review](PRIORITY_THREE_FOUR_REVIEW_2026-09-19.md), the operator said
"Ok do it and remember this priority list so we can check next time." Scope:
original/current selection-rank provenance, reward completeness/revisions,
and validation of the existing fill-risk model against this bot. No trading
rule, model coefficient, risk, capacity, order, deploy or restart change.
The durable priority order/checkpoints are in
[MEASUREMENT_CHECKLIST](../MEASUREMENT_CHECKLIST.md).

Bigger picture: use the existing selector/quote ledger and dated account-wide
earnings, not a second collector. Observation writes remain part of the
existing intent path; analysis remains read-only/offline with no trading
consumer. A blank current rank is not replaced by an old rank. Reward
absence is not zero. Early cancellation cannot become a successful no-fill
prediction. Refitting the model or loosening completeness gates was rejected
without held-out economic evidence and a separate operator decision.

## Implementation

`selection_provenance.py` records each current-process binding's original
rank, snapshot time/hash and bound time, separately from its latest rank and
explicit missing reason. Retained owners keep the original across scans;
removal/rebind or process restart begins a new binding scope. Empty/missing,
future, stale and absent-from-viable-slate are distinguished. Legacy/recovery
owners without a known initial rank are not backfilled from a later scan.
The snapshot ID hashes its observed time and ordered viable slate; it is local
snapshot identity, not a venue request ID or as-known historical replay.

The in-memory map stays within active capacity. Snapshot construction is
bounded to100 opportunities and reuses the existing viable ranking. No API
or new ledger read occurs. Errors cannot veto binding or a valid entry;
failed optional binding evidence retains known origins and records explicit
unknown tombstones for new owners, preventing a later scan from inventing
their original rank. Other
quote measurements continue with an explicit provenance error. Evidence
does not participate in selection, ownership, risk or exit planning.

Original/current evidence, model horizon and calibration ID are persisted
only inside existing immutable `observation_json`; no database migration,
backfill or new collector. Old consumers tolerate added fields. The existing
`selection_rank` retains its current-rank meaning. Rebinding after a failed
rotation also begins a new binding scope; the feature does not claim a
market's first-ever acquisition rank across process/scope changes.

`scripts/measurement_validation.py` reuses the existing consistent read-only
ledger transaction and supplied earnings JSON. It produces a new ignored
timestamped `report.json` plus `report.md`, captures ages, date ranges and
source hashes, and opens no API client or execution gateway.

- Reward comparison uses the **same** ledger with two supplied snapshots,
  isolating reward revisions from changing fill history. PAID/PENDING/SKIPPED
  remain separate; a disappeared row becomes missing/unknown, not zero.
  Invalid/missing, manual, cross-day, unmatched and ambiguous-program rows
  stay in an explicit unresolved queue. Mixed program identity is unallocated.
  A newer "previous" snapshot is refused. Totals are reported subtotals,
  never certified complete bot-only profit or grounds to discard unknown losers.
- Fill validation compares the recorded probability with first fill inside
  **30 seconds**, not lifetime filled status. Early cancel/expiry without a
  fill is censored; positive early fills remain positive. Rejected, unknown,
  mismatched, modified, invalid-clock, missing-execution and other-horizon
  rows are excluded explicitly. Reversed fills do not count. Empty samples
  have unavailable rates/scores, not zero accuracy.
- The last completed UTC day is a temporal holdout; earlier complete UTC days
  form the reference. Partial days/horizons are excluded. Reported predictions
  are not refitted, and no outcome alters the bot. Program type/quantity/model
  identity are separated in additional strata. Reward days remain ET.
- Legacy probabilities have no recorded horizon/model identity. The report
  explicitly assumes the historically configured30s horizon and labels model
  identity unknown. Newly loaded observations will carry the exact settings.
  Local placement time, cancellation censoring, selected-entry bias, correlated
  quotes and revised ledger history remain limitations. The old maker-lab
  prior is deliberately conservative, not a fitted point probability.

## Offline data acceptance

At11:02:30Z, reused09:27:51Z September19 earnings and21:50Z September18 snapshot;
**zero new venue requests**. Private result:
`data/measurement-validation/20260919T110230.349897Z/` on the active owner.
Seven completed ET reward dates September12–18:193 changed market-days and778
unresolved rows. Reported subtotals were day_of$717.60 paid/$89.63 pending,
daily_event$0/$6.83 and unallocated$26.75/$33.90. These are not new deposits.

UTC September12–17 reference:19,634 scorable observations,167 fills within30s,
mean prediction2.939%, observed0.851%, Brier0.008917. September18 holdout:
6,032 observations/266 markets,58 fills within30s, mean prediction3.175%,
observed0.962%, Brier0.010045. Excluded11,205 censored,40 rejected/unknown,
110 unproved/modified and2 incomplete horizons. All37,023 sampled legacy
observations lack the new rank provenance as expected: it is not loaded.

This supports running the diagnostic, not reducing modeled risk or claiming
greater profit. The conservative prior exceeding observed selected-entry
frequency does not by itself prove a broken model; censored observations and
the evolving program mix preclude an automatic calibration replacement.

## Verification and release boundary

The first88 focused tests and501 runner/measurement tests passed. Deliberately
overwriting retained origins, converting censored orders to negative labels,
and converting missing paid reward to zero each caused its focused regression
to fail by assertion. A fourth reversal which lost an unknown original rank
and backfilled it on a later scan also failed. All in-memory patches restored.
The first complete native suite passed1,923 tests in199.717s; the expanded
1,927-test suite passed196.277s. Final24 focused checks include the last
failure-origin and fill-identity edges. All six changed source/script/test
file hashes match the isolated native test tree. Final shipping suite is
complete: all1,927 tests passed in197.071s on the exact reviewed shipping
files. Documentation audit and final diff/secret/authority review pass.

Dedicated edge review: stale/future/empty/duplicate/overlarge scans; capacity,
retain/remove/rebind/restart; unknown origins; optional failure isolation;
JSON persistence/legacy compatibility; invalid/duplicate/future rewards;
pending-to-paid revisions and disappearing rows; manual/mixed/unmatched data;
30-second exact boundaries; cancelled-before-horizon censoring; partial/reversed
or missing fills; clock/identity failures; complete-day temporal separation;
read-only DB/no-network/no-model update; API/load and rollback. Trading-source
changes are limited to optional observation provenance. The prior Priority1
repair remains prepared separately; no pending safety work was forgotten.

Runner582918 remains the existing sole owner. Current exposure was not queried
for this historical/offline task and is not inferred from report rows. Windows
and GitHub may advance; WSL70467880/loaded1273aadd remain deliberately behind
the prepared trading-source changes. No deployment or restart is authorized.

Final process check at approximately11:05Z: the same runner was present with
8h13m uptime and WSL HEAD70467880. The changes are prepared for a later reviewed
deployment/restart, not collecting new provenance yet. The saved priority
checkpoints distinguish available offline tools from pending live activation.
