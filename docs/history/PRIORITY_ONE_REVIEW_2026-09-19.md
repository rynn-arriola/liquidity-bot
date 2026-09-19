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

## Follow-up investigation — operator requested investigation only

An isolated fake-venue experiment executed the production
AccountSourceRunner -> _recover_strategy_state -> _quarantine_working_orders
-> RestFallbackRunner._rest_reconcile -> CommissioningRunner._rest_reconcile
path with30 positively-owned entry orders and one independent held-market
exit order. The affected recovery market itself was already clear, modeling
a condition cleared by the first proof. Account/history/order read leaves
were mocked; the fixture used only a temporary ledger and zero live API calls.

REST performed31 exact-order reads, complete account snapshots and history,
then repeated31 exact reads plus snapshots/history. The position-service hook
was never called between them. Synthetic admission cost0.25s per exact read
plus1s snapshots/history per pass gives17.5s; this is NOT measured production
duration. The WebSocket-mode control retained scope and read zero unrelated
IDs, while still collecting complete account snapshots/history twice.

Mechanism: the REST wrapper intentionally drops strict_order_markets before
calling its parent, disables bulk proof for untagged recovery, and renews a
whole-account entry gate only after full proof. The normal scoped quarantine
always runs both initial and terminal checks even when no cancel is needed.
Unlike shared public-feed recovery, it does not enable held-position service
between stages. Existing test_scoped_recovery_cannot_skip_owned_orders asserts
this strict wrapper behavior; it is not safe to merely forward scope or mark
partial proof account-complete. All47 existing test_rest_fallback and
test_shared_stream_recovery tests passed in6.599s on the loaded checkout.

This reproduces the local latency mechanism consistent with the05:55 episode,
not every millisecond of venue/network timing or the cause of initial unknown
state. No429 appeared in that incident's API window. The16 same-price return
cases are separate; their cancellation logic was not established defective.

Recommended next design, pending operator fix authorization:

- Keep a full fresh account/ownership proof when global authority is invalid.
- After valid proof, hand serial control to due/uncovered positions before
  additional optional/redundant recovery work; avoid recursive recovery and
  retain stop/source-generation/backoff checks.
- Use an explicit exact-owner terminal proof for a scoped recovery follow-up,
  or safely return when no mutation/ambiguity remains; do not renew global
  entry authority from that narrower proof. Reuse the existing complete
  order/position/history plus exact-owner semantics where applicable.
- Recheck quantity, working/absent orders and unresolved intents after every
  await. Unknown ownership/global conflicts still require global recovery;
  stale proof cannot license IOC/reducer placement. Do not change WebSocket
  recovery, prices, size, deadlines, caps, risk windows or API limits.
- Before any fix/restart: focused real-path fake-venue regressions, deliberate
  reversal, complete tests, dedicated edge-case audit and normal operator gates.

No source repair, deployment of trading code, live fault injection or restart
occurred. The next task is a reviewed narrow recovery implementation, not a
claim that the delay is already fixed.
