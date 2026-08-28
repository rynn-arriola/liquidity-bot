# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).

## 0. Remaining natural acceptance

The live runner uses marker `2831026`. Continue accepting these paths only
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

## 1. Naturally accept private-shard overlap recovery

Commit `2831026` is active and startup acceptance passed with Polymarket's real
empty-subscription pattern: `order_eof=0/3 account_balance=3/3`. Do not induce a
private-stream failure. On a natural 180-second silence, acceptance must show
`[private-shard-recovery] ... cancels=0`, unchanged runner PID, retained active
owners and no unresolved/`UNKNOWN_ABSENT` state. The fallback must still
quarantine globally if any replacement balance, scope, REST or liveness proof
fails.

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
