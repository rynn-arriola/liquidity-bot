# REST hard-exit retry repair — 2026-09-11

## Operational state

The operator force-stopped the runner and supervisor after a position did not
close automatically, then manually handled venue orders and inventory. A local
process check proved no trading runner or supervisor remains. Manual venue
actions were not followed by an authenticated account read, so current orders,
positions and ownership state are unknown. The operator explicitly instructed
**“Dont restart.”** This repair is not deployed or loaded.

## Incident evidence

Market `aec-setkamemd-cilevg-mocdav-2026-09-11` filled approximately +10 at
0.69. The passive reducer repriced and later reached its hard timeout. The first
exact IOC SELL 10 at 0.69 was submitted at 15:58:19Z and was terminal unfilled
by about 15:58:20Z. Its REST confirmation did not complete until approximately
15:58:52Z, about 33 seconds later. The reconciler then recorded
`hard_exit_retry_window_exceeded` at 15:59:45Z. A later IOC overlapped the
operator's manual intervention and the runtime entered execution-state
quarantine rather than assuming an outcome.

There was no accompanying 429 or evidence of a platform-wide outage. The local
REST implementation made a complete account proof retrieve every known order
by ID. Roughly 30 unrelated entry orders therefore consumed more than the
unchanged 30-second hard-exit window. After the IOC became terminal, the held
position and working-order rows could also remain byte-for-byte unchanged, so
the position-priority scheduler did not recognize that the exact owner needed
its next retry before flat markets.

## Repair

- Temporary REST mode uses a dedicated hard-exit proof: complete open-order,
  position and mandatory execution-history snapshots remain required, while
  individual order reads are strict only for the exact held owner.
- Scoped proof can apply positive account facts but cannot refresh the global
  entry gate or the ordinary REST poll clock. If it fails or is cancelled, it
  revokes existing entry authority; only a later complete account proof can
  restore entries.
- Reconciler hard-exit attempt/sent/start state is exposed as a scheduling-only
  token. A terminal unfilled IOC can therefore requeue the same held owner
  immediately even when position and working-order rows did not change.
- Temporary REST mode removes the extra post-proof retry sleep. The complete
  scoped proof itself is the wait. Same-owner visits remain finite and mutation
  authority remains capped at three hard IOC submissions within 30 seconds.
- The default WebSocket path, pricing, sizing, selection, reward priority,
  lifecycle rules, $90 HARD policy and temporary 30 pending-entry ceiling are
  unchanged.

## Verification and edge cases

Focused regressions cover forty unrelated owned orders without any unrelated
GET-by-ID during hard-exit proof, no renewal of global entry freshness, proof
failure revoking entry authority, correct routing through the focused proof,
immediate terminal retry, and enough finite visits for three send/proof pairs.
Existing tests retain the opposite rule for normal account-wide REST proof:
all forty orders are still read strictly. Flat-during-proof, unresolved intent,
working-order, retry-limit, deadline, shutdown and default-mode guards remain
on their existing paths.

The deliberate reversal removed the hard-exit scheduling token and the exact
terminal-retry regression failed; restoration passed. The final pinned WSL
suite passed all 1,305 tests in 129.989 seconds. Compilation, documentation and
whitespace audits also passed.

## Activation boundary

Do not deploy or restart from this document. A later operator instruction must
be followed by exact source parity and a fresh authenticated proof of orders,
positions and ownership. Natural live acceptance should confirm an unfilled
hard IOC receives its next exact retry without an unrelated-order scan; do not
induce a position or failure for testing.
