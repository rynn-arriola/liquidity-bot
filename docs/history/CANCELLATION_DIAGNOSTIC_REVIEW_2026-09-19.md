# Cancellation diagnostic review — September19

Read-only review at09:36Z. Runner582918 remained live, up6h43m at09:35:57Z,
from the02:52:11Z restart. No source/configuration change or restart; current
venue exposure was not queried in this log-only investigation.

## Recorded evidence

450 reducer-decision records from02:55:02Z through08:55:14Z:
139 repricing cancels,34 building-after-fill cancels,31 deadline cancels,
213 passive placements and33 IOC placements. All recorded outcomes were
acknowledgements, not proof of working/terminal order state.

Repricing involved25 markets. None targeted an exact-size reducer already at
the passive candidate or reducing touch. Twelve had size mismatches;127 had
matching size but neither matching price. All had known planning books;
maximum receipt age2.915357s. This narrows the suspected needless-cancel
mechanism but does not establish that every reprice was economically useful.
Ecehar/miykih accounted for34 reprices; the next largest market had13.

Pairing each reprice cancel with the next diagnostic in the same market,
requiring a passive-placement acknowledgement and the same position-opened
timestamp, yields133 pairs. Typical ACK-to-ACK gap is about4.2s; two exceed10s:
jamduc/kammaj at04:03:23Z (25.244779s) and boyvit/kovnaz at07:01:40Z
(15.656512s). Sixteen pairs return to the original price and quantity even
though the original cancel-time frame did not match the desired price/size.

These are screening results, not uncovered-duration measurements. A simple
same-market join crossed two distinct position episodes and falsely suggested
122/909-second gaps; those pairs were excluded. Even within an episode, ACK
times alone do not prove terminal cancellation, working replacement, fills,
or absence of another covering order. Do not count the six unmatched cancels
as failed replacements without reconstructing their exact order lifecycle.

## Next review, by priority

1. Reconstruct the two longer gaps using terminal order proof, fills and
   position/coverage timelines; fix only a confirmed execution defect.
2. Inspect the16 same-price/size returns and high-churn markets using both
   decision frames; separate market movement/own-touch visibility from a
   needless cancel/repost loop. Do not suppress legitimate exits speculatively.
3. Correlate scan overlap, API admission/response and account proof with slow
   replacements, preserving safety priority and rate limits.
4. Compare resting-time and reward cost only after the execution mechanism
   is established. No profitability, reward-API causation or before/after
   improvement claim follows from these unmatched intervals.

Prior coverage/urgency/catalog fixes remain loaded. The diagnostic is live;
the cancellation investigation is not closed. No new deployment or trading
policy change is authorized by a status or priority-list request.
