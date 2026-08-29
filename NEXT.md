# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).

**Capacity ceiling remains open:** instrumentation is deployed at `2aa9fec`, but the first 20-slot restart produced 10 Cloudflare `1015`/429s on execution-history backfill despite only `1.813` aggregate attempts/s in that window; the next ordinary window recovered cleanly. Keep 20 slots, isolate the endpoint/rolling-window cause, obtain a no-429 restart, then replay evidence before any bounded canary. Resolve whole-slate quiet-book fallback and any exposure increase separately. [Criteria and measurements](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence).
## 0a. Load and naturally accept private Ping/Pong liveness
Commit `2aa9fec` includes the `a1ba212` repair and is deployed after a flat-gated restart and 830 WSL tests. Naturally accept a quiet private-shard interval beyond 180 seconds without false handoff and confirm any real Ping/Pong failure still enters bounded recovery with `cancels=0`, unchanged ownership, no persistent unresolved/`UNKNOWN_ABSENT` and no quarantine.

## 0. Continue natural acceptance of the size-13 deployment
Commit `2aa9fec` is active after 830 deployed WSL tests and exact marker/source
proof. At 14:27Z it held 19/20 quoting slots, 34 quantity-13 orders and zero
positions/absent/unresolved rows or risk stop. Continue observing the measured
execution-history 429s as part of the capacity gate.

The score-clock repair already received natural acceptance: one backward sample
logged `action=ignored_stale_sample`, added no retirement and the runner reached
20/20. Continue observing quantity-13 fills/exits, risk scaling, stream
freshness and mutation resolution; do not induce a fault.

## 1. Observe the laptop as the only production runtime
The operator explicitly authorized the laptop restart. Commit `a1ba212` is
loaded after all 810 laptop WSL tests passed. The 12:27Z authenticated
acceptance found the runner/supervisor healthy, 18/20 quoting markets, 35
resting size-13 orders, zero positions/absent/unknown rows and no risk stop.
The laptop is the only writable ledger/API owner; the former server is
destroyed and the desktop is prepared but inactive.

On the desktop, `run parity check` verifies/fast-forwards only tracked files
against GitHub. Its old recovery databases are not current laptop production
state; any handoff still requires a new stopped-runtime snapshot from the laptop.

Continue natural live observation of fills, exits, stream freshness, 20-slot
ownership and access errors. The local TV dashboard is active but remains
local evidence rather than venue flat proof.

The additive Gain/P&L dashboard update requires no bot restart. After this
commit reaches the laptop, restart only the optional TV service; its table will
populate after the hourly report atomically writes the first local snapshot.
Confirm the two top P&L cards, all six ordered columns and snapshot age while
the existing slot, health and activity information remains unchanged.

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
The latest stopped runner loaded `31f815c`. Continue accepting these paths
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
Simultaneous market/private silence still uses global quarantine; investigate
only if recurrence proves a local defect rather than venue/network loss.

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

## 5. Collect fixed-GTC evidence

Commit `de1478b` repaired the cumulative-gap defect and is deployed. The live
report proves 20 GTC starts, 20/20 feature rows, 20 clean paths and zero false
exclusions. The combined public scope is 98/100, no post-start 429 or collector
exit occurred, and bot PID 21791 was unchanged through 17-minute acceptance.

The collector began before `de1478b`; do not invalidate its 20 clean trials just
to load forward decode/drop interval closure. It loads on the next natural
supervisor restart. Collect three complete days plus one held-out day before
any strategy proposal, and never add overlapping cohorts as separate profit.

## 6. One unresolved mutation can have account-wide availability cost

Review whether proven-safe markets can continue around one ambiguous owner
without weakening account-wide ownership. A longer timeout is not a fix.

## 7. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination; the runner can
end with `flat=false`. Decide whether the script should fail when the final
record lacks flat proof. Until then, every stop requires final-record plus an
authenticated venue snapshot. The script now reports every 30 seconds during
its unchanged 300-second grace, but progress output is not flat proof.
