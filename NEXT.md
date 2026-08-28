# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).

## 0. Remaining natural acceptance

The live runner still has source `2831026` loaded. Continue accepting these paths only
when they occur naturally; do not induce a live failure:

- persistent economic-idle replacement commits one-for-one against real supply
  without touching retained orders or reducing active count;
- positively closed, flat/order-free owners release safely;
- alternating `unrecoverable_book_*` variants remain one failure family;
- private WebSocket shards and market stream recover after 180-second silence;
- low buying power or rejection blocks only new risk in that market;
- a venue `TRADE_STATE_BUSTED` reverses its linked fill exactly once;
- no unmanaged order, unresolved intent, `UNKNOWN_ABSENT` row or position is
  hidden by operator reporting.

## 1. Activate and accept rate-limited private-shard recovery

The bot is stopped and flat. Commit `2831026` passed startup acceptance with
Polymarket's real empty-subscription pattern. The first natural overlap then proved a confirmed
429 on the final balance read still caused global quarantine. Correction
`a8d91d0` is deployed and activates only on the next authorized start. Do not
start without explicit operator authorization and do not induce a fault.
Natural acceptance must show
`[private-shard-handoff-rate-limit] ... cancels=0`, followed by
`[private-shard-recovery] ... cancels=0`, unchanged PID/owners and no persistent
unresolved/`UNKNOWN_ABSENT`. Non-429, stale and exhausted proof must retain the
global fallback.

## 2. Collect fixed-GTC evidence

Commit `de1478b` repaired the cumulative-gap defect and is deployed. The live
report proves 20 GTC starts, 20/20 feature rows, 20 clean paths and zero false
exclusions. The combined public scope is 98/100, no post-start 429 or collector
exit occurred, and bot PID 21791 was unchanged through 17-minute acceptance.

The collector began before `de1478b`; do not invalidate its 20 clean trials just
to load forward decode/drop interval closure. It loads on the next natural
supervisor restart. Collect three complete days plus one held-out day before
any strategy proposal, and never add overlapping cohorts as separate profit.

## 3. Unknown PLACE without an exchange order ID needs a human path

An ambiguous create without an exchange ID has no safe order-specific lookup;
absence cannot prove cancellation. The stopped-only
`scripts/resolve_orphan_places.py` utility is the reviewed manual recovery path.
Do not automate or loosen it without an explicit safety review.

## 4. One unresolved mutation can have account-wide availability cost

Review whether unrelated proven-safe markets can continue while one owner is
ambiguous without weakening the account-wide unowned-order invariant. Raising
a timeout is not a fix; the venue has hidden accepted state longer than prior
timeout values.

## 5. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination; the runner can
end with `flat=false`. Decide whether the script should fail when the final
record lacks flat proof. Until then, every stop requires final-record plus an
authenticated venue snapshot.

## 6. Per-market trading attribution remains incomplete

Settlements, manual closes and unattributed account fills can be absent from
the execution ledger. Account-boundary reporting is better than matched pairs,
but never use one ledger P&L figure alone to authorize a strategy change.
