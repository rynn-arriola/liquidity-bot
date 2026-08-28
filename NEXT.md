# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).

## 0. Remaining natural acceptance

The live runner uses marker `53fa838`. Continue accepting these paths only
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

## 1. Finish GTC coverage-gap scoping

The 12:10Z report proved 17 GTC starts and 17/17 feature rows, but incorrectly
excluded all of them because one cumulative pre-start run gap was treated as
if it overlapped every future pair. The repair must use market/time-overlapping
stream intervals, retain the aggregate counter only for legacy runs without
intervals, and close/reconnect a collector interval on future decode/drop gaps.

Required acceptance: focused edge cases, full local/server suite, exact hashes,
read-only report showing the post-12:10 trials clean, collector restart only,
combined public scope at most 100, no new REST 429, and trading PID unchanged.
Then collect three complete days plus one held-out day before any strategy idea.

## 2. Unknown PLACE without an exchange order ID needs a human path

An ambiguous create without an exchange ID has no safe order-specific lookup;
absence cannot prove cancellation. The stopped-only
`scripts/resolve_orphan_places.py` utility is the reviewed manual recovery path.
Do not automate or loosen it without an explicit safety review.

## 3. One unresolved mutation can have account-wide availability cost

Review whether unrelated proven-safe markets can continue while one owner is
ambiguous without weakening the account-wide unowned-order invariant. Raising
a timeout is not a fix; the venue has hidden accepted state longer than prior
timeout values.

## 4. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination; the runner can
end with `flat=false`. Decide whether the script should fail when the final
record lacks flat proof. Until then, every stop requires final-record plus an
authenticated venue snapshot.

## 5. Per-market trading attribution remains incomplete

Settlements, manual closes and unattributed account fills can be absent from
the execution ledger. Account-boundary reporting is better than matched pairs,
but never use one ledger P&L figure alone to authorize a strategy change.
