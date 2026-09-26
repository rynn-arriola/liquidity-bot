# Open-order listing supersedes a lagging "not found" — September 26, 2026

Operator: "I think we should fix that", then "Ok add the fix". Pushed, **not
deployed**; takes effect at the next Command 1.

## Symptom (MEASURED, production log since the 02:35Z restart)

`[runtime-recovery]` rose from about 20 an hour to 190 (03Z) and 287 (04Z).
The main components were:

| Component | Lines |
|---|---:|
| `strategy_state` (`execution_state_unknown`) | 240 |
| `strategy_state_recovery` (`could not re-read N working order(s) … NotFoundError … status=404`) | 230 |

`[periodic-reconcile]` also failed repeatedly. Trading continued (about 980 placements in 2 h, 32 markets), but decisions paused often.

Before the restart, HTTP 404 on an owned order read was 0–2 an hour. Hour 03Z had 92 such lines and hour 04Z had 143.

## Mechanism

- **Only two markets.** All 27 distinct 404 order ids were in two in-play markets holding positions: `aec-cs2-is-btf-2026-09-26` and `aec-utr-kupana-piclin-2026-09-26`. Mostly these were 5-minute GTD reducers, plus GTC reducers.
- **A lag, not a lost order.** The venue answered `GET /v1/order/{id}` with 404 from about 15 s after placement for up to 7 minutes. The order was nevertheless in the complete open-order listing: no row was ever marked `UNKNOWN_ABSENT`. A live read-only probe at 04:5xZ found the current orders in those markets listed and readable, so it is a lag.
- **Why it spread.** The SDK path is unchanged (`/v1/order/{id}` in 0.1.2 and 1.0.2). In REST authority, every whole-account proof exact-reads each owned working order. Any failure fails the proof, so the gate closes and every market reads `execution_state_unknown`. The next proof reads the same order again.
- **Blocked time.** First-to-last 404 span per order, summed: EXPIRED 38 min, CANCELED 17 min, FILLED 3 min.

## Fix

`RestProofMixin._rest_reconcile`, after the complete open-order listing has been applied to the ledger, drops a blocking read failure only when all of these hold:

- that order's exact read failed with not-found (`errors.is_not_found`), recorded by every exact-read implementation via `_note_order_read_failure`;
- exactly one listed row has that id, with status OPEN or PARTIAL;
- exactly one owned ledger row (not `venue:`, not terminal) has the same client id, exchange id, market, side, price and original quantity.

It logs `[order-read-listed] superseded_not_found=N ids=…`.

Unchanged:
- An order missing from the listing keeps its failure; absence is never cancellation proof.
- Other read failures (auth, 5xx, backpressure) keep theirs.
- Unresolved PLACE intents without an order row keep theirs.
- Fills still come from execution history, and positions from the complete position snapshot.
- No trading, risk, selection, pricing or timing change.

## Verification

- `tests/test_order_read_listing.py` (5 tests), with the real hybrid runner/ledger and a fake venue, covers:
  - a listed 404 is superseded, the order stays working and the gate is ready;
  - an unlisted 404 still fails;
  - an auth failure beside a listed 404 still fails;
  - an identity/terminal/foreign mismatch never matches;
  - a missing or duplicate listing keeps the failure.
- Deliberate reversals each fail: no supersede call, no not-found gate, duplicates allowed, terminal listing allowed, not-found never noted.
- Full WSL suite: 2,344 PASS.

## Acceptance after Command 1

Watch the following, while in-play reducers exist:
- `[order-read-listed]` lines appear;
- `[runtime-recovery]` returns to about 20 an hour;
- `[periodic-reconcile] outcome=failed` is rare;
- `execution_state_unknown` is no longer repeated across markets.

`[order-read-listed]` must never name an order that later proves to have been gone.

## Command 1 (05:18Z) and a warm-restart startup fix

Command 1 steps:
- Graceful stop at 05:18:51Z, flat. Two shutdown ownership retries in one WTA market cleared.
- Gate 1 at 05:19Z: all zero.
- `45cfb5735` fast-forwarded into WSL; hashes match; SDK 1.0.2.
- Production suite: 2,344 PASS.
- Gate 2 at 05:21:28Z: all zero. `DEPLOYED_COMMIT=45cfb5735`.
- Started `auto 15 32 hybrid` (supervisor 440299).

The runner then died three times in about 29 s each. The supervisor held `ATTENTION_REQUIRED` ("3 fast process deaths", trading stopped); the account stayed flat throughout.

Cause:
- This was the first restart with a saved scope.
- `[warm-restart] outcome=readmitted remembered=32 readmitted=32`.
- `_select_reward_market` then asked `selection.top_viable(0)` for fresh markets, and the selector raises `ValueError: selection limit must be positive`.
- The earlier warm-restart tests covered partial re-admission only.

Fix (operator standing instruction "just fix it so you can run the bot"): skip the fresh request when re-admitted markets already fill every slot. A regression test fails with the original `ValueError` without the fix. Full suite: 2,345 PASS. Deployed by a second stop/gate/start in the same Command 1.
