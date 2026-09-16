# Seven-day reward-table settled-loss reporting — September 15, 2026 ET

## Problem and evidence

The Discord reward report showed `unavail` for trading P&L on September 11–13.
The six-hour risk-window change did not cause this. The historical account
metric requires adjacent ET opening-equity snapshots near midnight. September
12 and 13 started far outside the reviewed 15-minute window, so the report
correctly refused to label those wide intervals as calendar-day account P&L.

A 22:38 ET read-only production run had only three of seven account-marked rows
proven. Paid reward, pending reward and resting side-hours were populated. The
missing account boundary also prevented finalized net, as intended. The defect
was presentation coverage: known durable closed-fill loss was not shown as a
separate measurement, leaving the operator with no daily trading-loss number.

## Reviewed design

The report now presents two explicitly different measurements:

- `Settled` replays every non-reversed execution fill chronologically so prior
  inventory establishes basis. It assigns matched/closed P&L and observed fill
  fees to the fill's ET event day. Open inventory and non-trading cash do not
  enter this value. Every requested ET day has a numeric value, including zero.
- `Account` retains the stricter marked-equity result. Missing or late midnight
  evidence remains `--`; it is never filled from the ledger. Only this proven
  account value can finalize daily net.

This avoids both earlier failure modes: it no longer hides known closed-fill
loss, and it does not repeat the September 6 defect that mislabeled incomplete
matched-pair estimates as full account P&L. The dashboard cache carries the
same separate settled field for future consumers. No venue request, order,
position, risk, capacity, reward ranking or trading rule changes.

## Production-data review

The modified script was run read-only against the live ledger without posting
to Discord. It produced settled values for all seven rows: -$48.59, +$7.17,
-$28.44, +$191.03, -$16.64, -$35.96 and -$55.76, for a seven-day settled total
of +$12.82 at that observation. These are account-wide ledger fills, not bot-
only attribution. The two unexpectedly positive days were traced to complete
large manual/imported round trips: one realized +$65.1152 and another
+$230.4549 after fees. Their quantities, prices and opposite fills reconcile;
they are not deposits or rewards.

Account-marked P&L can differ sharply because it includes carried open marks
and trading that the local fill ledger may not fully attribute. The two columns
therefore must not be summed, substituted or compared as if identical.

## Verification and release state

The focused reward-table suite passes 31 tests. A deliberate reversal that
discarded daily realized deltas made the new ET-boundary regression fail for
both affected days; the implementation was restored and the test passed. The
regression also proves reversed fills are excluded, older basis is replayed,
fees follow their event day, missing account marks stay absent and cannot
finalize net, and the Discord rows remain fixed-width and below platform caps.

The exact production virtual environment then passed all 1,740 tests from an
isolated native-Linux checkout in 165.082 seconds. Paid/pending rewards retain
their venue-reported inputs; account marks retain the existing midnight proof;
resting side-hours retain local order-history inputs; finalized net still
requires a proven account mark plus posted paid reward. The new settled series
adds no API request and no value is silently substituted into another field.

Commit `6bd3f0cc4` was pushed, then fast-forwarded into the production checkout
without restarting the trading process. The production checkout passed the 42
report/layout tests. The normal 23:07 ET system timer loaded the new script,
posted successfully to the dedicated Discord webhook and exited zero.

Its dashboard artifact proves all seven settled rows are numeric and the other
fields remain populated from their existing sources: paid reward on September
9–13, pending reward on September 13–15, account marks on four of seven days,
and resting side-hours on every day. Net remains pending where account evidence
is missing/estimated or reward is not final. Supervisor `1009` and runner
`1023` kept their original start time and remain loaded on trading source
`63c2c42f9`; there was no restart and no trading-code deployment.
