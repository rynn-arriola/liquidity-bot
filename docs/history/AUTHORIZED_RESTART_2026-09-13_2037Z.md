# Authorized same-version restart, September 13 20:37Z

The operator requested "Run the bot again" after the explicit20:05:54Z
force-stop. No source, configuration, strategy or risk policy changed.

## Prestart evidence

- No old supervisor/runner; laptop remains the sole live owner.
- Windows/GitHub/WSL parity passed at96c3bee4b; source marker remains
  b2b0d017ea2c3617a48c4d9d168b2bcf7d8334af. All192 normalized tracked
  source/test/script/web/config hashes match. Reused the exact unchanged
  release's1,626-test native acceptance; no new code/test run was necessary.
- Verified private stopped-ledger backup captured20:36:22Z;395,464,704 bytes,
  SHA-256 prefix97e9b6fd6bed. Peer workstation remains unconfigured/unverified.
- Stopped-only prestart helper held the exclusive lease, verified zero venue
  orders/positions and exact positive terminal identity for all30 retained
  orders, then ran the normal REST reconciler with venue mutations disabled.
  Both acknowledged CANCEL intents resolved20:37:05Z. No row deletion,
  fabricated cancellation, risk reset, API order mutation or source switch.
- Independent authenticated20:37:18.458508Z gate proved zero venue orders,
  positions, ledger working orders, unresolved intents and unknown orders.

## Restart and initial acceptance

Launcher: `./scripts/start_commissioning.sh auto 10 32 rest`.
Supervisor168012 / runner168045 started20:37:19Z (16:37 ET).
Retain $90 HARD, manual REST account state, max30 flat pending entries
excluding position-market orders/reducers, and all existing lower caps.
The read-only TV dashboard was reused.

Initial selection ranked39 WebSocket books with39 hits, zero REST fallback,
30 viable candidates and11.3s scan duration. Bound startup30 books also used
the stream exclusively. Account mode confirms automatic switching disabled;
maintenance feed HEALTHY/NORMAL at20:37:53Z. Live entries resumed20:38Z.

First API window through20:38:57.743323Z:284 attempts/98.2s (2.893/s),
39 mutations, zero429/cooldown and zero REST-book requests. Five failures
were individual-order404s, so this is not an error-free-run claim.
Authenticated20:38:57Z showed27 venue orders/zero positions; the non-atomic
ledger read had two unresolved intents and one unknown order during mutation.
At20:39:33.663850Z, authenticated state was29 flat orders across28 markets,
zero positions; ledger29 working, zero unknown orders and one in-flight
intent. The earlier unknown state did not persist. Both original PIDs remained
running. Sustained API, timing, fill/exit and full-capacity acceptance remain
observational; no fill or failure was induced.
