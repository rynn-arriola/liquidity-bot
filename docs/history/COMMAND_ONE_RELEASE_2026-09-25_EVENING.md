# Command 1 release — September 25 evening (`63d2917b8`)

Operator issued Command 1 after the Claude code review (items 1-5) and final
pre-deployment/end-to-end reviews ([review record](CODE_REVIEW_2026-09-25.md)).
Released commit `63d2917b88b36a11ddd0ed458f27fc26f56f142f` contains the
September 25 terminal/refill fixes (`9f0d7864a`, previously staged), periodic
account-proof failure logging, the no-behavior-change split of
`live_commissioning.py` into twelve `runner_*` mixins, the shared status-based
not-found helper, the NaN-safe unused sizing helper and a Python 3.14 test
fixture. No configuration, risk, size, slot or strategy change.

## Stop

Supervisor 198478 stopped and runner 198496 received SIGTERM at 23:48:41Z;
`stop_commissioning.sh` returned normally at 23:49:23Z (no `--force`). Final
record: `stop_reason=operator_sigterm`, `flat=true`, stopped 23:49:21Z. During
shutdown `aec-utr-adatak-urasot-2026-09-26` logged four transient
`RuntimeInvariantViolation` shutdown failures before its cancel was confirmed;
the same exception class ("working order has no owned place intent") appears in
earlier shutdowns and remains the open shutdown-ownership item. One +15
position (`aec-itfme-domrec-lukpok`) was observed flat at 23:49:16Z. The
operator reported manually closing some trades during this window. Two Discord
shutdown notifications were not delivered.

## Gates and deployment

Gate 1 (authenticated, 23:50:26Z) and gate 2 (23:54:24Z, immediately before
start): zero venue orders/positions, zero unresolved intents, unknown orders,
ledger working orders and ledger positions; no runner or supervisor present.
Stopped-runtime parity ledger copy verified at 23:50:55Z (1,292,050,432 bytes,
SHA prefix 13c2abafe161); desktop peer offline/pending. Clean WSL `main`
fast-forwarded from `f0ddc06c4` to `63d2917b8`; all 264 tracked
src/tests/scripts/config inputs match the reviewed Windows copy after line-
ending normalization and all 12 `runner_*.py` modules are present; CLI import
smoke passed; full suite in the live checkout: 2,296 tests pass.
`DEPLOYED_COMMIT` set to `63d2917b8` only after those checks.

## Start and acceptance

`start_commissioning.sh auto 15 32 hybrid` started supervisor 397960 and runner
397974 (same persisted settings). First ~3 minutes: zero tracebacks,
`[runtime-recovery]`, critical, invariant or `[periodic-reconcile]` lines;
window 18:00-24:00 ET loss $0/hard $30/cap 30, held=false; API windows 1.987
and 1.582 rps with zero failures/429s. Authenticated 23:57:57Z: 14 venue
orders on 9 markets (ledger 14), one -15 position on
`aec-setkameua-pruser-gazole-2026-09-26` covered by an exact BUY 15 reducer,
zero unresolved/unknown.

Lower breadth after restart is expected, not a regression: before the stop the
runner held 32 active markets mostly retained from earlier in the day
(`kept=31`), while evening fresh scans ranked only 10-18 viable markets
(22:22-23:31Z) and 13-14 after restart. A restart rebuilds from a fresh scan;
selector code is unchanged. Evening restarts therefore reduce breadth until
later scans find more markets.
