# Priority1 review — September19

The operator confirmed the full measurement priority order and requested
Priority1 first. This is read-only investigation, not a new fix/deploy/restart
authorization. The order is saved in the measurement checklist.

## Present account and accounting

Runner582918 remained alive at09:41:07Z, up6h48m55s. Authenticated complete
orders/positions at09:41:31.566158Z:25 flat entry orders across17 markets,
zero positions, zero unresolved/unknown orders; ledger agrees25/0.

Read-only replay at09:42:51Z of current execution_settled_risk_windows against
the same durable-receipt cutoff exactly matched current00–06ET P&L
-$6.05820000, cap25, no trip; previous18–00ET -$0.59000000, cap30.
Current threshold is $25 and cap25 correctly follows loss above$5. The
September15 legacy cash tables are stale and not current authority.
The loaded call path uses matched-inventory P&L plus actual fill fees, not
cash movement or unrealized marks; late fills are attributed by durable receipt
for risk, while historical reports use event time. This verifies persisted
calculation consistency, not independent venue-ledger completeness or every
manual attribution. No loss threshold was deliberately induced.

## All recorded exits since02:52:11Z restart

110 position_initial observations and110 later flat observations; no initial
observation lacks a subsequent same-market flat. All33 IOC diagnostic order IDs
are now positively recorded FILLED. This is not proof every passive close was
unassisted/manual-free. Current authenticated flat state independently agrees.
IOC ACK deadline offsets: median6.798740s, maximum36.326760s. ACK and flat times
are separate; eventual exit does not certify on-time exit.

The two largest offsets were the same05:55Z pregame deadline:
orlpet/simjan IOC ACK05:55:29.767491Z, flat05:55:31.990665Z;
aldden/klyvit IOC ACK05:55:36.326760Z, flat05:55:38.001488Z.
They closed before later event-start signals06:00:32Z/06:04:52Z, but missed the
bot's earlier deadline. At05:55:02.352855Z an execution_state_unknown recovery
for orlpet/simjan entered scoped quarantine. Next position cancel was05:55:23Z.
The overlapping API window had zero429, five order_retrieve404s, max admission
wait5.576s. Do not label this a rate-limit incident or prove one404 caused it.

Source review: _recover_strategy_state calls scoped _quarantine_working_orders
with default position preemption disabled. That quarantine awaits initial and
final REST reconciliation; public-bound recovery has explicit position service.
This is a targeted recovery-latency candidate for reproduction, not proof that
any required account proof can safely be skipped or a tested repair exists.

## Cancellation gaps and churn

The earlier139 reprices/25 markets remain the reviewed sample. No exact-size
target already matched candidate or reducing touch;12 quantity mismatches.
Sixteen same-price/size returns have different decision frames. Examples show
candidate0.73 at cancel then0.74 at replacement with a newer book transaction;
same-price repost alone cannot establish a redundant decision or external
price movement versus own-order visibility. No speculative suppression added.

Jamduc/kammaj: old reducer positively CANCELED. Runtime terminal observation
04:03:29.061016Z immediately coincided with MarketStreamUnavailable public
disconnect; replacement ACK04:03:49.131879Z. ACK-to-ACK25.244779s, terminal
observation-to-ACK20.070863s. New reducer was subsequently positively CANCELED.
Exact network/venue outage root cause is not determined from local logs.

Boyvit/kovnaz: old reducer positively CANCELED; terminal observed07:01:45.250386Z.
Another already-due position botjan/soumil canceled, IOC-ACKed07:01:51Z and
flattened07:01:54Z before replacement ACK07:01:56.224719Z. That explains the
15.656512s ACK gap and10.974333s terminal-to-ACK gap with intended due-before-
uncovered priority. No intervening flat entry PLACE is logged in that interval.
These observations are not exact venue working-coverage intervals.

## Disposition

Current ownership and persisted settled-loss calculation passed the bounded
review. Positions exited; not all were on time. Priority1 remains open for
deadline/recovery latency reproduction and a safety-preserving design review;
churn-cost analysis remains secondary. No evidence supports rolling back the
reward adaptation or changing prices, windows, caps or account mode. No
trading source/configuration changed, and the bot was not restarted.
