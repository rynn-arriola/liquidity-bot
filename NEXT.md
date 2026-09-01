# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).
**Positioned inactive-owner top-up amplification is reviewed for the next restart:** the narrow repair prevents the known reserved `+10.0000` non-open owner from requesting full discovery on every one-minute metadata probe. Flat non-open and authoritative position-zero transitions still request refill. After load, naturally require the position to remain visible/risk-reserved/mutation-free, no recurring lifecycle request from its refresh, ordinary refill for other capacity and clean API windows. [Evidence](docs/history/INACTIVE_POSITION_TOP_UP_AMPLIFICATION_2026-09-01.md).
**Inactive-market lifecycle repair is loaded; positioned-wait natural acceptance remains:** `13ecc441b` safely released four ended flat owners and continued refill without recurring runtime-error classification. Exact positioned inactive wait was not present in the accepted startup, so naturally require full risk/capacity reservation, no impossible mutation, reopen-to-exit restoration or authoritative flat release; do not induce a position or venue closure. [Evidence](docs/history/INACTIVE_MARKET_LIFECYCLE_RECOVERY_2026-09-01.md) · [release review](docs/history/RELEASE_REVIEW_2026-09-01.md).
**UI-maintenance reduce-only gate is loaded; natural transition remains:** the first loaded startup stayed `RUNNING`, confirming a healthy initial observation. Naturally require exact maintenance to cancel/block only flat new-risk quotes, keep reducers/exits live and require three healthy checks before resume; do not induce maintenance or use the operator latch only as a test. [Evidence](docs/history/UI_MAINTENANCE_ENTRY_PAUSE_2026-09-01.md).
**Settlement-wait repair is loaded; natural release acceptance remains:** `0bcfbbc96` reconstructed the one exact `10.0000` empty-book owner, reserved one of 24 slots/full basis risk and let the other 23 owners trade. Authenticated acceptance found 38 orders across 22 unrelated markets and zero orders on the waiting slug; the first mutation window had zero failures/429s. Do not induce settlement, a book return or a fill. Naturally verify that returned depth resumes the ordinary exit path or authoritative flat proof releases the reserved slot, with continued zero entry authorization for the waiting owner.
**Dirty-market scheduler and realized-P&L optimization are loaded; performance acceptance remains:** `13ecc441b` includes the exact-equivalent chronological replay and additive fill index. Re-baseline across healthy steady windows at the loaded 32-slot canary; do not widen beyond 32. Roll back to 24, then `v1.1.0` if needed, on a missed flatten/reducer, lower resting fraction, higher latency or increased `execution_state_unknown`. [Handoff](docs/history/DIRTY_MARKET_SCHEDULER_2026-08-31.md).
**Recovery book validation is loaded; natural one-sided recovery acceptance remains:** `13ecc441b` retains both-empty/missing-metadata/order-payload refusal while allowing valid one-sided recovery data. Do not induce a position or book failure.
**Discord capital-summary rendering needs only operator-visible acceptance:** `0245d31` is pushed, deployed and loaded after 973/973 exact laptop WSL tests and flat/order-free proof. The first routine post traversed the loaded path at 23:52:12Z; the webhook response body is not retained locally, so confirm the visible `Free to withdraw` field. If it is still unavailable, record that message's exact timestamp before changing policy: persistent ambiguity or stale balance must remain fail-closed. [Evidence](docs/history/CAPITAL_GUIDANCE_TERMINAL_HISTORY_2026-08-31.md).
**Sequential hard-exit natural acceptance pending:** active source/marker `31c2574` cancels one working order at a time, building side first, and waits for exact positive terminal proof before selecting the next. The 17:46Z flat-gated restart loaded it and normal startup passed. Naturally require a two-order hard exit with sequential `pending=1`, no builder fill/shared-timer retirement and an exact final reducer/IOC; do not induce it. [Evidence](docs/history/HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md).
**Tiny-residual natural acceptance pending:** source `6c5ea49` is loaded inside `9001a4b`. Naturally require `[action] ... reason=tiny_residual_timeout`, exact residual quantity, no overlapping cancel/IOC, positive flat proof and ordinary slot refill; do not induce a fill. [Evidence](docs/history/TINY_RESIDUAL_CASHOUT_2026-08-31.md).
**Direct 32-slot canary is loaded but has not completed peak-band acceptance:** `01f26a455` runs size 10 / 32 with four private shards. Trough-band windows held roughly 24–29 owners and remained below the 4/s host gate with zero failures/429s and queue depth one, but this is not an actual 32-owner test. Accept only from the 02:00–10:00 UTC peak band at approximately 32 held owners; reduce to 24 immediately for any capacity-linked failure/429, two consecutive `max_waiting > 1` windows, safety deadline, stream loss, persistent ambiguity, unacceptable latency/CPU or lower reward per resting side-hour. [Capacity criteria](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence) · [preparation](docs/history/SLOT_CAPACITY_CANARY_32_PREPARATION_2026-09-01.md).

**API admission correction active; natural backpressure acceptance pending:** `0ff6e3c6` keeps one aggregate 4/second WSL gate while isolating background 2/second, entry 4/second and safety 4/second lanes; passive reducers are safety, 429 cooldown is class-scoped, long non-safety work defers before network IO, stop wakes old waits and native Windows fails closed. It also stops selection/revalidation at its first local cooldown, keeps designed pause out of Discord `Lost to errors` and runtime-error accounting, avoids cache-to-full-scan amplification, and merges peer-published lane cooldowns into `[api-budget]` without another request. All 926 local/exact-deployed tests pass and both deliberate reversals failed exactly. `Command 1` loaded it after fresh zero-position/zero-order proof. The first live window reported `shared=1`, failures/429s `0`, all lane fields present and no JSON-state, quarantine or runtime error. A later window accepted merged visibility with background `2.7s`, entry/safety zero and local 429s zero; it cleared next window. Naturally require one `[api-backpressure]` line without market-failure fan-out or immediate fallback when a selection is actually deferred, plus bounded safety wait and no mutation retry/quarantine. A 22-book REST-only refresh still cannot fit inside five seconds at 4/second, so stream-loss capacity remains open. [Admission evidence](docs/history/HOST_API_ADMISSION_PRIORITY_2026-08-30.md) · [observability evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md).
## 0a. Load and naturally accept private Ping/Pong liveness
Commit `2aa9fec` includes the `a1ba212` repair and is deployed after a flat-gated restart and 830 WSL tests. Naturally accept a quiet private-shard interval beyond 180 seconds without false handoff and confirm any real Ping/Pong failure still enters bounded recovery with `cancels=0`, unchanged ownership, no persistent unresolved/`UNKNOWN_ABSENT` and no quarantine.

## 0. Continue natural acceptance of size 10 / 32
Loaded executable source `01f26a455` retains the accepted size-10/24 behavior and adds only the reviewed fourth shard, breadth-derived limits and report-lane/capacity instrumentation needed for the direct 32 canary. Continue observing natural fills/exits, stream recovery and lifecycle bursts without inducing a fault.

The score-clock repair already received natural acceptance: one backward sample
logged `action=ignored_stale_sample`, added no retirement and the runner reached
its configured scope. Continue observing size-10 fills/exits, risk scaling,
stream freshness and mutation resolution; do not induce a fault.

## 1. Observe the laptop as the only production runtime
The operator explicitly authorized the laptop restart. Commit `a1ba212` is
loaded after all 810 laptop WSL tests passed. The 12:27Z authenticated
acceptance found the runner/supervisor healthy, 18/20 quoting markets, 35
resting size-13 orders, zero positions/absent/unknown rows and no risk stop.
The laptop is the only writable ledger/API owner; the former server is
destroyed and the desktop is prepared but inactive.

`run parity check` now verifies PC/laptop/GitHub/WSL and refreshes a private,
integrity-checked execution-ledger recovery copy. It never activates that copy;
handoff still requires a stopped-runtime snapshot and account proof.

Continue natural live observation of fills, exits, stream freshness, 20-slot
ownership and access errors. The local TV dashboard is active but remains
local evidence rather than venue flat proof.

Never use GitHub or a general file-sync service for the live SQLite database or
credentials. Any later desktop failover is manual and repeats the stopped-
runtime snapshot and known-account-state gates.

## 1a. Complete the separate manual weekly Drive recovery
Parity is PC/laptop/GitHub only. `run weekly task` separately uploads one
versioned encrypted archive; Codex or Claude may use its own Drive access. The
agent-neutral packager creates online SQLite snapshots, an integrity/hash
manifest, bounded logs and streaming AES-256-GCM. A 391,163,120-byte laptop
archive from 03:39Z has 14 artifacts; both databases and the laptop-to-PC hash
passed. Upload/readback and a restore drill remain because the operator deferred
Drive. Never upload recovery/SSH keys, raw SQLite/WAL files or plaintext.

## 2. Remaining natural acceptance
The current live runner loaded source marker `4b6e4e8`. Continue accepting these paths
only when they occur naturally; do not induce a live failure:

- persistent economic-idle replacement commits one-for-one against real supply
  without touching retained orders or reducing active count;
- positively closed, flat/order-free owners release safely;
- alternating `unrecoverable_book_*` variants remain one failure family;
- private WebSocket shards and market stream recover after 180-second silence;
- low buying power or rejection blocks only new risk in that market;
- a venue `TRADE_STATE_BUSTED` reverses its linked fill exactly once;
- no unmanaged order, unresolved intent, `UNKNOWN_ABSENT` row or position is
  hidden by operator reporting.

Private-only silence has natural acceptance with unchanged PID and `cancels=0`.
Simultaneous silence still uses global quarantine; investigate only a proven local defect.

## 3. Accept the deployed post-flat mandatory refill

Commit `2ece3c3` is active after authenticated flat/order-free proof, a graceful
stop, parity, 766 server tests and a clean size-10 restart. Natural acceptance
requires a real final near-deadline position closing
while mandatory capacity is reclaimable, then `[top-up-request]` followed by
either a validated `[top-up-cache]` or bounded fresh-scan fallback and
`[top-up] ... committed=yes`. Confirm unchanged ownership safety, no economic-
idle early eviction, restored `active=20`, and materially lower
`post_flat_wait_seconds`/`post_flat_underfilled_slot_minutes`. Do not induce an
exit or position merely to test it. Through 22:14Z there was no
`[top-up-request]`; the 22:11Z `trigger=scheduled` refill is not acceptance.

## 4. Accept rate-limited private-shard recovery

Commit `a8d91d0` is active. Its clean startup reached 20/20 quoting, 37 resting
orders, zero positions, zero absent and zero persistent unresolved state without
a restart-window error. Do not induce a fault. Natural acceptance must show
`[private-shard-handoff-rate-limit] ... cancels=0`, followed by
`[private-shard-recovery] ... cancels=0`, unchanged PID/owners and no persistent
unresolved/`UNKNOWN_ABSENT`. Non-429, stale and exhausted proof must retain the
global fallback.

## 5. Collect the retained-value refusal shadow

From the active runner, collect the 60%, 70% and 80%
`WOULD_REFUSE` labels under the current size-10/24-slot configuration. Require
complete PLACE/fill linkage, recent-candidate versus stale/no-candidate counts, projected
and paid reward opportunity cost, dark-slot time and canonical realized trading
outcome. Freeze a later held-out interval before proposing any threshold. The
shadow remains incapable of changing live orders.

The queue-reset, daily selection-probe and fixed-GTC collectors are closed.
Their databases and reports remain evidence; do not restart them merely to add
sample volume.

## 6. One unresolved mutation can have account-wide availability cost

Review whether proven-safe markets can continue around one ambiguous owner
without weakening account-wide ownership. A longer timeout is not a fix.

## 7. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination; the runner can end with `flat=false`. Decide whether the script should fail when the final
record lacks flat proof. Until then, every stop requires final-record plus an
authenticated venue snapshot. The script now reports every 30 seconds during
its unchanged 300-second grace, but progress output is not flat proof.
