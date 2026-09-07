# Overall trade history and entry-quality audit — September 7, 2026

## Outcome and scope

Poor pre-entry stressed exit value is a repeatable loss-concentration signal,
not yet a proven profitable refusal strategy. A 70% minimum remains a candidate,
not an operator decision or enabled trading rule. No runner source, risk,
configuration, orders, process or ledger was changed. The laptop runner 770972
was observed alive during this read-only analysis; no new authenticated
exposure snapshot was requested. The prior 14:13:55Z acceptance remains the
last cited venue order/position proof, not a current exposure claim.

Source: SQLite-online parity recovery snapshot captured at
2026-09-07T14:27:33.859566Z, 287,993,856 bytes, integrity `ok`, SHA-256
`513932675fb4199169d3d5dcf39372eae10912aceff01f4454d34380046baf14`.
It contains 6,042 active fill records from August 14 11:48:39Z through
September 7 14:26:32Z. Local analysis opens this frozen copy read-only.

## Method and limitations

- Replay all non-reversed fills in canonical event/receipt/execution order
  through `MatchedInventory`; retain opening basis, LONG/SHORT framing and
  signed fees. Split a direction-flipping fill's fees proportionally between
  the closing episode and new unmatched exposure.
- An episode is fill-reconstructed flat → inventory → flat, not necessarily
  an authenticated venue-position episode. Group outcomes by **opening-fill
  ET day**, not close day or account-report midnight boundaries.
- All 2,510 completed episodes total -$1,123.7327. Another 42 fill-unmatched
  episodes contribute -$0.06 booked fees/rebates, giving exactly canonical
  fill-realized -$1,123.7927. These 42 are NOT a claim of 42 live positions;
  historical settlements/missing closing executions can leave unmatched fills.
  This is not overall account loss, reward-adjusted net, or resolution of the
  account/fill gaps in the [September 6–7 audit](TRADING_LOSS_ATTRIBUTION_2026-09-07.md).
- Match each opening fill to its exact owned building PLACE observation.
  Require correct market/side, observation no later than fill, nonnegative
  book age, complete stressed exit and consistent quoted entry basis. Never
  use a later exit price or maker/taker label as the entry classifier.
- The persisted 60/70/80 labels began August 30 19:32:12Z. Of 1,130 completed
  episodes carrying those labels, exclude 61 inconsistent-basis observations,
  leaving **1,069** with usable pre-entry evidence. Older missing evidence is
  excluded, not backfilled with future prices. Thirteen additional coherent
  shadow-linked episodes are fill-unmatched and excluded from outcome scoring.
- Assert canonical total equality, fee conservation, closed added/matched
  quantity equality, unique fill IDs, exact entry-role/side linkage, persisted
  threshold consistency and the retained-value formula. No opening order is
  reused across the coherent completed sample.
- Thresholds were already selected for shadow before this review. Compare
  older history, August 31–September 5 and September 6–7 separately, plus
  quote sizes 10/13. This is retrospective temporal validation, **not a new
  untouched holdout** or causal execution/reward counterfactual.

Across the full 2,510 completed episodes, 211 lost at least $0.20 per matched
contract ($2 per ten); they contributed -$642.6744. This definition normalizes
quote size and does not imply every episode lost at least $2 in absolute cash.

## Clean comparison: 1,069 completed, linked episodes

Their net trading P&L including recorded fees/rebates is -$536.5150; 221 were
profitable and 91 meet the severe-loss definition above. Rewards are excluded.

| Proposed minimum retained value | Flagged episodes | Flagged share | Net trading P&L in flagged group | Profitable episodes also flagged | Severe episodes flagged |
|---|---:|---:|---:|---:|---:|
| 60% | 213 | 19.9% | -$249.2886 | 44 | 52 / 91 |
| 70% | 259 | 24.2% | -$285.4682 | 48 | 57 / 91 |
| 80% | 320 | 29.9% | -$312.4544 | 58 | 63 / 91 |

These are losses **associated with** flagged historical episodes, not money
that an enabled filter is proven to save. At 70%, the flagged group accounts
for 53.2% of this sample's net trading loss; per-ten-matched-contract P&L is
-$1.3872 versus -$0.3823 for the unflagged group. It misses 34 severe episodes
and flags 48 of 221 profitable ones.

The 70% relationship remains within both quote-size groups: size 10 has 938
coherent episodes, flagged/unflagged P&L per ten contracts -$1.2609/-$0.3699;
size 13 has 131 episodes, -$2.1263/-$0.4564. Capacity/strategy changed over the
period, so this does not isolate the causal effect of one deployment.

| Opening ET day | Coherent episodes | Flagged at 70% | Flagged P&L / ten contracts | Unflagged P&L / ten contracts |
|---|---:|---:|---:|---:|
| Aug 31 | 124 | 30 | -$1.8041 | -$0.4646 |
| Sep 1 | 121 | 25 | -$1.1636 | -$0.4248 |
| Sep 2 | 177 | 34 | -$1.3015 | -$0.2532 |
| Sep 3 | 120 | 30 | -$1.7827 | -$0.4691 |
| Sep 4 | 191 | 48 | -$1.1552 | -$0.2709 |
| Sep 5 | 77 | 24 | -$0.7836 | -$0.2801 |
| Sep 6 | 161 | 38 | -$1.4492 | -$0.4036 |
| Sep 7, partial | 57 | 18 | -$1.0439 | -$0.7145 |

The remaining 41 coherent episodes opened August 30. September 7's separation
is weaker; the relationship is not a guarantee and both groups still lose
on trading before rewards.

## Concrete cases, including a miss

- September 6 `aec-itfme-tobmar-scoeki-2026-09-06`: a 10-contract episode
  lost $8.21 net. Its pre-entry stressed retained value was **1.09%**, so all
  three candidate thresholds flag it. The observation preceded the opening
  fill by 0.883 seconds.
- September 6 `aec-itfme-omabek-marnic-2026-09-06`: $6.58 net loss,
  **1.13%** retained value, likewise flagged before entry.
- September 7 `aec-setkameua-korvit-melvol-2026-09-07`: $4.95 episode net
  loss, **36.57%** retained value, likewise flagged.
- September 6 `aec-itfwo-miczun-zoedol-2026-09-06`: $7.31 episode net loss
  despite **86.82%** retained value at placement. All three thresholds miss it.
  Episode net includes the opening rebate and therefore differs from the
  $7.33 closing-fill net / $7.30 gross price loss quoted in the earlier audit.

Retained value is a **stress model**, not a promise of actual exit proceeds:
`cross_exit` excludes the best entry-side level and halves remaining displayed
depth, then accounts for modeled taker fees. Quotes can rest a long time after
placement: median placement-to-opening-fill lag is 915 seconds in the coherent
sample. A good placement snapshot cannot promise a good later book.

## Evidence-quality defect found, not fixed

736 of 12,570 persisted shadow quote observations fail consistency between the
actual quoted stake and the stake implied by stored stressed value/P&L/ratio.
This affects 63 linked episodes, of which 61 are completed. Example: August 31
13:53:05Z `aec-valorant-aar-berg-2026-08-31`, offer quantity 13, quote 0.31:
actual stake is $8.97, while the stored model implies $7.02.

The observation writer combines `action.price` with cached
`state.stressed_exit_by_side`; it does not verify price/quantity/book identity.
The arithmetic mismatch is proven. The exact timing path causing each mismatch
is not established, and no loss is assigned to it. The reconciler separately
computes its stressed eligibility check from its frame and candidate price;
this finding alone is not proof of an execution-gate failure. Do not wire the
cached shadow ratio directly into a live veto. Repair/validate coherent
same-action measurement first; no fix was authorized or implemented here.

## Reward and replacement boundary

Among 11,834 internally consistent quote observations, a 70% rule flags 3,049
(25.8%). Of these, 2,757 reached terminal status without a recorded fill, 279
have a recorded fill, and 13 remain other/unresolved statuses. Never equate an
unfilled quote with wasted opportunity; it may have earned rewards.

Of those flagged observations, 2,326 had a recent selector spare, 245 had no
viable spare, and 478 had stale selector evidence. These are repeated snapshots,
not distinct guaranteed replacement slots. Even a recent spare would need
fresh validation and would itself need to pass the proposed new filter.

Do not sum repeated per-order reward projections into reward dollars lost.
Paid rewards are shared program/day evidence and cannot uniquely be allocated
to the refused side from current rows; see the
[program audit boundary](PROGRAM_REWARD_AUDIT_2026-09-03.md). The reward forgone,
replacement outcome, dark-slot duration, repeated re-entry and changed pairing
remain unproven. Removing an opening-side fill in hindsight also does not
simulate what the other resting side would have done.

## Recommendation and decision point

There is enough evidence to design a configurable **entry-only exit-quality
gate**, with 70% a candidate to test, not a selected live value. First repair
same-action measurement, then prospectively compare 60/70/80 on frozen future
dates using reward/resting-time and replacement evidence. Any design must
preserve exits/reducers, unknown-state checks, existing time/loss caps and
reward priority among eligible candidates. Decide separately whether the
policy acts per side or requires both entry sides to qualify; this replay does
not establish the paired-order counterfactual.

Exploratory checks do not support a simple high-touch-change ban: 0 / 1–2 / 3+
prior-120-second changes yielded -$0.6093 / -$0.7187 / -$0.4462 per ten matched
contracts. Expensive economic entry stakes above 0.85 were worse in 62 coherent
episodes, but cheap below-0.15 entries were not; do not reuse that association
to justify a symmetric price-floor increase or a league ban. No extra filter
or shorter inventory deadline is authorized by this audit.

Reproduction: ignored `.local-tools/overall_entry_audit.py`, native WSL Python,
read-only frozen parity database. Validation covers arithmetic conservation,
linkage, missing/partial/reversed/flip state and shadow consistency. No runtime
code change means no deployment, full trading test rerun or restart is needed.
