# Configurable entry-quality filter — September 7, 2026

## Operator scope and activation boundary

The operator approved building/testing a configurable 70% stressed retained-value
entry filter and correcting the saved estimate mismatch. They separately asked
for enable/disable control and whether refused quotes are top-20 markets.
Activation/restart still requires a separate decision. The checked-in policy
ships **disabled**; do not silently enable it on a later generic restart.
No trading stop, start, restart, order mutation or production deployment was
performed for this implementation. The existing size-10/32 runner remains on
`f7816c324`. Current venue exposure was not independently re-read; the prior
14:13:55Z acceptance is historical evidence, not a new flatness claim.

## Behavior when separately enabled

`config/entry_quality_policy.json` is independent of the existing time/loss-cap
file and contains `enabled: false` and `minimum_retained_value: 0.70`.
`--entry-quality-policy` can select an alternate file. Both keys are required;
duplicates, unknown/missing keys, missing/unreadable files, non-boolean enabled,
non-numeric/boolean/non-finite thresholds and values outside (0,1] refuse startup
before secrets, database or venue access. Settings are immutable for a run.

The pure policy consumes a fresh `cross_exit` result, never a saved shadow row.
It requires complete exit depth, positive finite entry cost and finite modeled
P&L. Retained value is `(entry_cost + exit_pnl) / entry_cost`; exact equality
passes with only 1e-12 numerical tolerance. This is a stressed model, not a
guaranteed liquidation value: exclude the entry-side touch, halve remaining
displayed depth and include the existing modeled taker fee. Quantity scales
the actual stake and depth requirement automatically; 70% is not a HARD tier.

Each flat-market entry side is independent. A failing side is ineligible for
fresh selection and normal quoting; an already-resting failing entry is
cancelled through the existing owned-order/one-action driver. The other side
may remain eligible. Current reward priority among passing candidates is
unchanged; high reward does not override an enabled quality refusal. Both-side
refusal may use the existing ten-minute economic-idle/normal-scan replacement,
with positive release proof and a qualifying replacement, not immediate slot
churn. Unknown quality is not a replaceable economic reason.

Selector, per-market economics and reconciler receive the same restart policy.
Before a flat post-only action reaches the driver, the runner checks its exact
price/quantity against a fresh local book again, without an API request.
Existing order caps, buying-power, stream, ownership, exposure and HARD gates
remain. Nonzero positions, passive reducers, IOC exits, maintenance evacuation
and shutdown bypass this entry-only gate; the 900-second deadline is unchanged.
There is no new loss stop, league ban, slot change, hot reload or mutation retry.

## Estimate repair

The observation writer previously combined the actual action price/quantity
with cached side economics. The audit proved 736 mismatches in 12,570 shadow
observations. It now recalculates the stressed exit on the actual action and
current local book, validates market identity and timestamps capture using the
current clock. The cached exit result is not modified. Other reward/hazard
forecast fields retain their existing model; no reward model is recalibrated.
Missing observation remains a reporting gap and cannot authorize/veto an order.
The independent execution policy never consumes `WOULD_REFUSE` fields.

## Top-20 and reward-priority evidence

Read-only production-ledger transaction at September 7 14:47:10Z. This is a
later cutoff than the earlier overall episode audit. Internally consistent
shadow placements: 11,855; inconsistent excluded: 738. Of those coherent rows,
3,456 have a saved fresh selector rank (September 3 04:38:46Z through September
7 14:46:46Z). The other 8,399 have no usable rank; no rank is guessed.

| Saved selector rank | All coherent ranked quotes | Below-70% quotes | Below-70% orders with recorded fills |
|---|---:|---:|---:|
| 1–10 | 1,638 | 590 | 45 |
| 11–20 | 900 | 262 | 16 |
| 21+ | 918 | 225 | 15 |
| Total | 3,456 | 1,077 | 76 |

Thus **79.1% of ranked flagged quotes** (852/1,077) and **80.3% of flagged
orders with recorded fills** (61/76) were top 20. Top 20 also comprised 73.4%
of all ranked quotes. These are placement/order counts, not distinct markets,
complete trade episodes, duration shares or actual paid reward fractions.
Another 1,980 flagged coherent placements have unknown rank.

Within top 10, the median per-side 30-second projected reward was $0.001790
for flagged quotes versus $0.000985 for unflagged quotes (about 1.82x). At ranks
11–20 it was $0.000180 versus $0.000218; at 21+ $0.000017 versus $0.000063.
These estimates are not actual earnings and cannot be multiplied by a day or
summed over repeated placements to claim lost rewards. They establish that
flagged risk is **not confined to low-priority/low-projected-reward markets**.
Program/day earnings cannot identify exact reward sacrificed by a per-side
refusal. Replacement eligibility, resting duration, pairing and paid-net
tradeoff remain uncertain; the prior loss concentration is not guaranteed
savings. Keep the feature off until the operator chooses activation.

Rank audit reproduction: ignored `.local-tools/entry_quality_rank_audit.py`,
SQLite read-only transaction, exact saved `selection_rank`, internally coherent
entry basis and non-reversed recorded fills. No venue request or ledger write.

## Dedicated review and verification

- Success/off path: good entries and both sides pass when disabled; large
  advertised rewards cannot rescue a bad enabled entry. Forged saved shadow
  labels still cannot suppress a good live action.
- Boundaries: 5/10/13/30 quantities, exact threshold, just below, zero/negative/
  above-one/non-finite/boolean/string config values, incomplete/invalid exit
  evidence, missing/duplicate config keys and unavailable config.
- Safety: passive LONG/SHORT reduction, deadline IOC, maintenance evacuation,
  account pause, unknown ownership and kickoff preserve existing decisions;
  bad owned entry cancellation uses the existing action path. Unknown quality
  cannot trigger economic-idle replacement. Existing full-suite recovery,
  shutdown, cap and configured-capacity regressions remain required.
- Timing/restart: old cached economics cannot supply the actual-action exit;
  market/price/size/book linkage is explicit. Fresh action-boundary recheck
  rejects a bad proposed entry before a PLACE intent or driver call. Config
  file edits do not mutate an already-created policy; startup propagates the
  same policy to selector and reconciler.
- Resources: no new venue call, stream, table, history replay or worker. Only
  bounded local book walks are added; score/rank/side selection ordering and
  recovery scheduling remain unchanged outside the named entry refusal.
- Reversals: disabling the pure policy made all three selected policy/
  reconciler/selector tests fail; restoring the cached observation made the
  exact-action regression fail; removing the action-boundary recheck made its
  no-driver/no-intent test fail. Every reversal was restored before final tests.
- Rollback: policy stays disabled for delivery. Future disable is a reviewed
  config change plus authorized restart; no automatic restart or ledger reset.

Initial review suite: 1,170 tests passed in 115.382 seconds. After three added
regressions and all restored reversals, the final complete suite passed
**1,173 tests in 115.853 seconds** using the pinned WSL Python against the
Windows review checkout. Final source diff review found no additional defect;
the unknown-quality replacement boundary was excluded before that final suite.
No deployment, activation or restart is included in this delivery.
