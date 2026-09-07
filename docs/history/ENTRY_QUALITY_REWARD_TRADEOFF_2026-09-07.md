# Entry-quality reward/trading sensitivity estimate — September 7, 2026

## Outcome

The first reward-aware approximation does **not** establish a net benefit for
enabling the 70% filter. In the six matched ET days August 31–September 5,
flagged coherent completed episodes lost $206.6192 net, while three allocation
proxies assign $376.30–$385.22 of reported paid-plus-pending reward to flagged
entry-side resting time. Removing those episodes and their allocated rewards,
with no replacement benefit and unchanged other trading, would be roughly
$170–$179 worse. This is a conditional sensitivity calculation, not accounted
net, proven counterfactual, forecast or statistical confidence interval.

The implementation remains disabled and is not deployed. Recommend keeping it
off pending reward/replacement evaluation; the operator owns activation.

## Matched-day figures

| Opening ET day | Net loss in flagged completed episodes | Reward allocated to flagged entry sides, proxy range |
|---|---:|---:|
| Aug 31 | $43.47 | $48.24–$49.98 |
| Sep 1 | $23.09 | $16.22–$17.07 |
| Sep 2 | $37.77 | $23.45–$24.06 |
| Sep 3 | $46.74 | $26.57–$28.63 |
| Sep 4 | $39.43 | $71.06–$75.41 |
| Sep 5 | $16.12 | $188.44–$190.74 |
| Six-day total | **$206.62** | **$376.30–$385.22** |

The total range compares the three complete model totals; it is not the sum
of daily minima/maxima. There were 810 coherent completed episodes, 191 flagged;
all coherent episodes together lost $381.9654. The $206.6192 already includes
flagged winning episodes and signed fees/rebates. Deposits, withdrawals and
rewards are excluded from trading P&L. This is opening-day fill-episode P&L,
not account marked-equity daily reporting or reconciliation of prior gaps.

The requested comparison initially targeted August 31–September 6. The fresh
account-wide earnings response contains **no September 6 rows**. Missing is
unknown, not zero; September 6 is excluded from both columns of this matched
comparison. The earlier broader sample's $285.4682 includes different dates
(partial August 30 and September 7 plus September 6), so it must not be
subtracted from these six-day reward estimates.

## Method and evidence

- Frozen SQLite-online backup captured 2026-09-07T14:57:48.941148Z;
  288,468,992 bytes, integrity `ok`, SHA-256
  `8e2105286cee4ab28a28174a2bedffdba3522f90f242412cb59b25cf7de112ba`.
- Replay the existing full-basis `overall_entry_audit.py`, retaining its fee,
  canonical-P&L, unique-fill, completed-quantity and entry-observation checks.
  Exclude inconsistent retained-value/actual-stake observations. Use the
  already-selected 70% placement classifier, not later prices.
- One authenticated account-wide `liquidityProgram` earnings read through the
  existing report lane, initiated September 7 15:00:52Z. It returned 1,048 rows
  within the requested date window: 720 PAID and 328 PENDING. No ledger write,
  trading mutation or notification was performed. Local saved earnings were
  empty, so they could not supply an amount.
- Actual reported reward totals: **$365.73 paid + $544.16 pending = $909.89**.
  Pending is about 59.8% and may change; it is used only as a scenario input,
  never finalized net. September 4–5 amounts are entirely pending.
- Reuse the existing program audit's merged order-side intervals, split at ET
  midnight and assign replacement overlaps to the newest observation. Link
  each snapshot to its same-market/side/time quality label; duplicate label
  consistency is asserted. Intervals and placement-time scores approximate
  earning exposure, not a continuous venue reward-score tape.
- Within each **market/day**, allocate its reported earnings among flagged,
  passing and unknown entry sides using three alternative weights: resting
  seconds; initial order score times seconds; initial snapshot share times
  seconds. Each market reward row enters exactly once. Shared advertised
  program pools and repeated 30-second projections are never summed.
- The respective flagged totals are $385.2165 / $382.7744 / $376.2997.
  Score-time allocation leaves $22.9067 assigned to unknown quality and $30.67
  with no usable interval mapping. No missing evidence is labeled passing or
  silently added to avoided reward. Per-day allocation conservation is asserted.

Reproduction: ignored `.local-tools/entry_quality_tradeoff_estimate.py`; the
aggregate result is preserved in ignored
`.local-tools/entry_quality_tradeoff_result.json`. Both are analytical files,
not bot inputs. No production code changed for this estimate.

## What this cannot prove

The venue does not report reward by refused order-side interval. Position
reducers may also earn rewards; initial scores and competition can change
during an interval. Allocation proxies agreeing closely does not establish
accuracy or bound unknown reward. Replacement markets, changes in the other
side's fills, repeated entry, partial fills and dynamic eligibility can change
both trading P&L and reward. The enabled implementation continually rechecks
the book, while this historical classifier uses the placement observation.

The conditional six-day break-even is therefore approximately **$207 of total
reward sacrifice**, not a daily forecast. If the allocation/pending assumptions
hold, replacements would need roughly $170–$179 of additional net contribution
over the six days to offset the modeled shortfall. Replacement rewards alone
are insufficient evidence if those replacement trades also lose money.

## Delivery state

### Market-count follow-up, September 7 15:07Z snapshot

The operator asked how many usual markets would disappear if enabled. The
read-only snapshot captured 15:07:34.268719Z contains 11,907 coherent completed-
stress entry observations; 3,079 (25.9%) are below 70%, with 738 inconsistent
observations excluded. Among 1,515 distinct observed markets, 944 had at least
one flagged placement at some point. That cumulative count is **not** a count
of wholly excluded markets or simultaneous lost slots.

There are no bid/offer placement pairs sharing one exact saved book timestamp,
quantity and market, with both observations fresh and within five seconds.
Allowing different fresh books within five seconds finds only three pairs
(two with neither side flagged, one with one side flagged). This is far too
small and unrepresentative to estimate a both-sides-failing rate or scale it
to a usual 32-market slate. The missing opposite-side observation cannot be
treated as passing or failing. Do not convert the 25.9% placement rate into
eight lost markets, ten simultaneous lost orders or a fixed future capacity.

The configured capacity remains 32. One failed side can leave its market
eligible on the other side; both failed sides prevent fresh entry, while a
qualifying replacement may occupy the slot through existing rules. Exact net
loss of tradable markets requires a same-time two-sided candidate-book audit
and qualifying replacement supply, which this historical placement analysis
does not establish. Recommendation remains to keep the already-disabled gate
off; no policy change or restart is needed for that.

Reproduction: ignored `.local-tools/entry_quality_market_count.py`, stdlib
SQLite URI `mode=ro`, coherent entry-stake/ratio check, complete exit check,
one-to-one side pairing and bounded timestamps. No venue calls. Runner
`770972` was independently observed alive. Current venue orders/positions were
not re-read and are not inferred from this historical ledger.

### Prior implementation delivery

Code/config commit `91efa8cc4` was pushed; all 1,173 tests and three restored
reversals passed. Post-push parity confirms clean Windows/GitHub matching,
WSL HEAD `b4f22b1ae` pending reviewed deployment, and loaded marker `f7816c324`.
Backup PC is unreachable/pending. The verified private recovery copy above was
created by parity. Runner `770972` was independently observed alive after code
verification; no stop/deploy/activation/restart occurred. Current venue exposure
was not re-read; the older 14:13:55Z order/position acceptance is not current proof.
