# Trading-loss price-mix verification — 2026-08-31

## Verdict

The pasted report's descriptive tables are reproducible, but the claim that
raising `minimum_entry_price` from 0.10 to 0.15 will fix the loss is not. The
report classifies fill liquidity (`MAKER`/`TAKER`) as order role
(`entry`/`exit`) and defines an extreme market from any fill, including the
expensive exit whose loss it then measures. Those two substitutions make the
association unsuitable as a causal entry-filter decision.

No strategy parameter was changed from this report.

## Independent source and method

The verification used the SQLite-online parity snapshot captured at
2026-08-31T16:41:59Z, SHA-256 prefix `dee48a3da3fa`, with integrity `ok`. It
read active, non-reversed fills from the clean post-`isAggressor` era, assigned
ET reward days, converted LONG/SHORT fills through the canonical economic-cost
function, included recorded fees in balanced-leg P&L and made no venue call.

The supplied price distributions are fill-row percentages, not contract-
weighted percentages. Repeating that method reproduces them (rounding explains
the old cohort's few 0.1-point differences):

| Cohort / liquidity | 0-.15 | .15-.30 | .30-.50 | .50-.70 | .70-.85 | .85-1 |
|---|---:|---:|---:|---:|---:|---:|
| 08-22..08-28 MAKER | 1.1% | 9.4% | 37.8% | 38.2% | 11.5% | 1.9% |
| 08-22..08-28 TAKER | 0.9% | 9.2% | 29.3% | 40.8% | 13.5% | 6.3% |
| 08-29..08-31 MAKER | 4.6% | 15.6% | 36.6% | 25.4% | 13.6% | 4.2% |
| 08-29..08-31 TAKER | 1.9% | 11.7% | 19.1% | 34.1% | 15.3% | 18.0% |

The report's extreme definition is “any BUY fill below 0.15 or above 0.85”
(strictly above, so 0.85 itself is normal). That exactly reproduces its recent
balanced-market split:

| Cohort | Class | Markets | Pairs | P&L | Per pair |
|---|---|---:|---:|---:|---:|
| 08-29..08-31 | normal | 148 | 2,003.26 | -$108.78 | -$0.0543 |
| 08-29..08-31 | extreme | 45 | 968.01 | -$102.49 | -$0.1059 |

It also reproduces the recent extreme league counts: ITF men 24, Setka men UA
9 and ITF women 6. The older extreme row remains exactly 39 markets, 534.9
pairs, -$100.04 and -$0.1870/pair; two additional late-resolved balanced
markets now make its normal row 349 markets instead of the pasted snapshot's
347.

The daily table through August 30 reproduces exactly when fees are included.
The later snapshot moved partial August 31 to 31 balanced markets, 430.25
pairs, -$58.28 and -$0.1354/pair. That is ordinary partial-day drift, not a
contradiction.

## Why the proposed lever is not established

`MAKER` and `TAKER` describe liquidity, not whether the bot was building or
reducing inventory. Since August 24, the durable PLACE intent records the
actual role through `quote_observation_required`; the business-logic audit
already prohibits inferring role from intent name or liquidity.

Using that role on the recent cohort changes the result materially. Markets
with a proven building entry outside 0.15-0.85 were 21 markets / 479.65 pairs
at -$0.0730 per pair; the other 172 markets / 2,491.62 pairs were -$0.0707.
That difference is small, not the reported twofold gap. Looking specifically
at a proven cheap building entry below 0.15 gives 14 markets / 248.64 pairs at
-$0.0354 per pair versus -$0.0744 for the rest—the opposite direction. This
does not prove cheap entries are good; it proves the pasted grouping cannot
justify blocking them.

The “any extreme fill” definition leaks the outcome into the classifier. A
market becomes extreme when its later buy-back costs above 0.85, then that same
cost makes its pair loss large. This accurately identifies where loss was
realized but does not show that the original entry was below 0.15 or that a
0.15 entry gate would have prevented it.

Other overstatements remain:

- normal-market loss moved from about -$0.0442 to -$0.0543 per pair, roughly
  23% worse, so “barely moved” is only relative to the extreme bucket;
- the known two-order hard-exit sequencing defect caused a roughly -$7.91
  episode today, so “not execution” is too strong; and
- 62-75 candidates measured under the current 0.10 policy do not prove 22
  slots remain supplied after applying 0.15. Historical candidate books are
  insufficient to recompute that counterfactual honestly.

## Decision boundary

Keep the 0.10 floor unchanged. Before proposing 0.15, measure a prospective
entry-role counterfactual: at each otherwise-authorized building decision,
record whether 0.15 would refuse it, then compare canonical realized trading
outcome, paid reward opportunity and dark-slot/replacement cost over complete
ET days. The current retained-value shadow already establishes the required
order-role and replacement-evidence pattern; enforcement still requires a
separate operator decision.
