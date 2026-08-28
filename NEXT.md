# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).
Do not add deployment narratives here after their remaining action is closed.

## 0. Reviewed rollout and live acceptance

The production bot remains on its earlier deployed source. The local official-
API remediation is not active until final tests pass, it is committed and
deployed, the additive ledger migration runs, and the operator separately
authorizes a restart after complete authenticated flat/order-free proof.
Read-only reports remain compatible with the pre-migration schema during this
deployment window and begin excluding busted fills after migration.

After that safe start, accept both the existing slot repair and the new
reliability behavior from natural operation:

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

## 5. Official API remediation awaits rollout

The ordered review in
[docs/POLYMARKET_API_REFERENCE.md](docs/POLYMARKET_API_REFERENCE.md) is
implemented locally: venue-history reset semantics, independent stream
liveness, busted-trade reversal, entry-only buying-power/rejection protection,
structured HTTP diagnostics with `Retry-After`, a 15-second REST timeout and an
isolated changelog RSS monitor. Focused regressions and deliberate mutation
checks passed; the complete final regression remains the rollout gate.

The RSS monitor is code only until an isolated server schedule is explicitly
installed. Deployment and restart remain separate operator decisions.
