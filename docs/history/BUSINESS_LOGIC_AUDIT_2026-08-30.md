# Business-logic audit and staged repairs — 2026-08-30

## Scope and safety

The audit and all validation were read-only with respect to Polymarket. Commit
`0755a30` was pushed and deployed to the WSL checkout without stopping or
restarting the live runner and without any venue mutation. The existing runner
kept its prior code loaded; these repairs change behavior only after a later
authorized restart.

## 1. Automated taker-exit evidence budget

The runner sends both entry and reducing fills as `BUY_*` intents in the
strategy price frame. The old safety counter searched for `SELL_*`, so real
automated IOC exits never consumed the 20-order evidence budget or its fee
budget.

The repair identifies taker fills whose client order has a durable owned
`PLACE` intent. It counts distinct client orders while summing every positive
partial-fill fee. Maker fills, manual/imported fills, reversed fills, and
unowned identifiers are excluded. A deliberate restoration of the old
intent filter failed the live-BUY regression exactly.

## 2. One canonical binary price frame

Retail evidence and the live order adapter establish that stored prices are
always in the LONG/YES frame. Therefore:

- `BUY_LONG` at `p` costs `p`;
- `BUY_SHORT` at `p` costs `1-p`;
- opposite inventory realizes `1 - long_cost - short_cost` per pair;
- a venue economic basis for negative inventory converts back to the strategy
  frame with `1-cost`.

The prior ledger mixed raw `p` and `1-p` interpretations between consumers.
The repair centralizes direction, economic cost/proceeds, open-inventory cost,
venue-basis conversion, and incremental matched-inventory P&L in
`src/liquidity_bot/pnl.py`. Ledger risk, daily/pair accounting, dashboard
accounting, and settlement reporting use the same functions.

Read-only production replay for `2026-08-29T04:19:36Z` through
`2026-08-30T03:39:08Z` covered 364 active fills. Canonical realized P&L was
`-$79.44650000`, exactly the settled-cash change of `-$79.4465`. Replacing the
one-minus conversion with raw price made the asymmetric `0.83/0.85` pair test
report `-$3.40` instead of `+$0.10`; restoration passed.

Windowed P&L now replays earlier fills to establish opening basis but attributes
only in-window realizations and fees. Open short inventory is added back at its
positive economic cost rather than as a negative signed value.

## 3. Entry/exit summary roles

Production fills cannot be labelled entry versus exit from `BUY_LONG` versus
`BUY_SHORT`: either can build or reduce depending on current inventory. The
durable `PLACE` intent already records the decision role through
`quote_observation_required` (building true, reducing false). Summary counts
now use that role, count execution IDs once, use execution time rather than a
late receipt time, and exclude manual and reversed fills. Restoring intent-name
classification failed the same-BUY entry/reducer regression exactly.

## 4. Economics label verification

The reported concern that the UI always called economics “enforced” was stale.
Since commit `69dcf16b`, the startup banner derives `advisory` or `enforced`
from `enforce_reward_cost_gate`. The default and uncommon enabled branches are
both covered. Hard-coding `advisory` failed the enabled-branch regression.

## 5. Timeout and re-entry policy

No live policy was changed. The commissioning ledger stores the entry snapshot
and latest queue state, not a continuous historical book, so it cannot honestly
price an earlier forced exit. The separate full-book scalp archive overlaps
172 real fills across 54 markets, but its stored episodes are counterfactual,
not venue executions.

The existing read-only replay was run at 300, 600, and 900 seconds using a 50%
displayed-depth haircut and a maximum 30-second book age. For the
`REWARD_SLOTS` cohort:

| Horizon | Complete exits | Completion | Net P&L | Mean P&L |
| ---: | ---: | ---: | ---: | ---: |
| 300s | 121/125 | 96.8% | -$56.1515 | -$0.4641 |
| 600s | 117/118 | 99.15% | -$16.6573 | -$0.1424 |
| 900s | 113/113 | 100% | -$4.1106 | -$0.0364 |

The archive itself had 2,184,965 books and 9,293 episodes, but only 29.2%
stream coverage with 40,045 decode/drop gaps. The direction of the available
evidence favors retaining 900 seconds, and its coverage is insufficient to
authorize either a shorter timeout or a new re-entry cooldown. Any later
change requires clean, live-strategy-specific book-path evidence and explicit
reward-opportunity-cost attribution.

## Verification

- Focused accounting, ledger, live-runner, reporting, and edge-case suites
  passed.
- Broader ledger/live-runner suite: 382 tests passed.
- Each substantive repair received a deliberate reversal that failed the
  intended regression before the repair was restored.
- Documentation audit passed with the current `FINDINGS.md` index at 99/100
  lines. The exact deployed commit passed 872/872 in 58.520 seconds using the
  production WSL virtual environment from an isolated clean worktree.

The first run directly inside the live checkout found one environment-coupled
dashboard test: live data persisted the separately authorized 22-slot canary,
while the test expected its default empty-data value of 20. The live capacity
file was not modified. Isolating the exact same commit from live data removed
that unrelated fixture input and the complete suite passed. Deployment left
runner PID 74548 and supervisor PID 74539 unchanged.
