# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).
Do not add deployment narratives here after their remaining action is closed.

## 0. Remaining natural acceptance

Commit `1ff0e42` is pushed, deployed and active. Local/server suites passed all
690 tests, parity passed, the additive trade-state migration completed, and the
reviewed flat/order-free restart reached 20/20 quoting with zero unresolved
rows and no new error signature. Continue accepting these paths only when they
occur naturally:

- queue scope reaches exactly 20 when viable supply permits;
- positively closed, flat/order-free owners release without touching retained
  orders or healthy streams;
- alternating `unrecoverable_book_*` variants remain one failure family;
- no unmanaged order, unresolved intent, `UNKNOWN_ABSENT` row or nonzero
  position is hidden by the dashboard;
- each private WebSocket shard and the market stream remains independently
  live, or enters bounded recovery after 180 seconds of silence;
- low buying power or a rejected entry affects only new risk in that market;
  reducing orders and exits continue normally;
- a venue `TRADE_STATE_BUSTED` observation reverses its linked fill once and
  removes it from P&L, basis and fill counts;
- no induced disconnect or synthetic live failure is used for testing.

## 1. Unknown PLACE without an exchange order ID still needs a human path

An order create can fail ambiguously before returning an exchange ID. There is
then no safe order-specific lookup and absence cannot prove cancellation. The
stopped-only `scripts/resolve_orphan_places.py` utility is the reviewed manual
recovery path.

Future automation would need several delayed complete account snapshots, no
unowned order, no position and no durable order under the client ID. It must
remain conservative beyond the measured delayed-visibility window. Do not
implement or loosen this without an explicit safety review.

## 2. One unresolved mutation can still have account-wide availability cost

Ownership ambiguity must continue blocking its own market and unsafe shutdown
actions. Review whether unrelated proven-safe markets can continue without
weakening the account-wide unowned-order invariant. Raising a timeout is not a
fix; the venue has hidden accepted state longer than earlier timeout values.

## 3. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination. A runner may end
with `flat=false`. Decide whether the script should return failure when the
latest final record lacks positive flat proof, while preserving bounded
non-flat shutdown behavior. Until then, every stop requires final-record plus
authenticated venue verification.

## 4. Per-market trading attribution remains incomplete

Settlements, operator manual closes and unattributed account fills can be
invisible or ambiguous in the execution ledger. Account-boundary reporting is
better than the old matched-pair-only row, but per-market realized attribution
is still incomplete. Do not use a single ledger P&L number to authorize a
strategy change. The long-form mechanism and proposed sequencing are in the
archived NEXT/FINDINGS files.
