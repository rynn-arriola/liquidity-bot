# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).

**Capacity ceiling remains open:** the bounded 22-slot canary passed its 30-minute startup/selection-burst gate with 3,392 attempts, zero failures/429s, request rates `1.263-2.643/s` and maximum queue depth one. The 2026-08-30 12:13Z restart loaded `821f5c85` with the size-13 taker-exit count raised from the stale fixed 20 to `4 × size = 52`; no slot-dependent capital, API or timeout budget increased. Its first accepted window reached 38 working orders across 22 markets, zero positions/unresolved/absent/foreign rows and `2.579/s` with zero failures/429s. Naturally verify which independent ceiling is reached first over a normal session: 52 exits or $3.90 fees. [Capacity criteria](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence) · [Taker-exit evidence](docs/history/TAKER_EXIT_SCALING_2026-08-30.md).

**API admission priority repair reviewed; deployment and restart acceptance pending:** the next build keeps one aggregate 4/second WSL gate while isolating background 2/second, entry 4/second and safety 4/second lanes; passive reducers are safety, 429 cooldown is class-scoped, long non-safety work defers before network IO, stop wakes old waits and native Windows fails closed. Three reversals failed exactly and all 918 local tests pass. After deployment and a separately authorized restart, naturally accept scheduled and lifecycle/reward-end scans: require `shared=1`, class cooldown fields, zero JSON-state errors, bounded safety wait, no mutation retry and no false unresolved intent/quarantine. A 22-book REST-only refresh still cannot fit inside five seconds at 4/second, so stream-loss capacity and risk-bearing-market priority remain independent open constraints. [Evidence](docs/history/HOST_API_ADMISSION_PRIORITY_2026-08-30.md).
## 0a. Load and naturally accept private Ping/Pong liveness
Commit `2aa9fec` includes the `a1ba212` repair and is deployed after a flat-gated restart and 830 WSL tests. Naturally accept a quiet private-shard interval beyond 180 seconds without false handoff and confirm any real Ping/Pong failure still enters bounded recovery with `cancels=0`, unchanged ownership, no persistent unresolved/`UNKNOWN_ABSENT` and no quarantine.

## 0. Continue natural acceptance of the size-13 deployment
Commit `2aa9fec` is active after 830 deployed WSL tests and exact marker/source proof. At 14:27Z it held 19/20 quoting slots, 34 quantity-13 orders and zero positions/absent/unresolved rows or risk stop. Continue observing the measured execution-history 429s as part of the capacity gate.

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

`run parity check` now verifies PC/laptop/GitHub/WSL and refreshes a private,
integrity-checked execution-ledger recovery copy. It never activates that copy;
handoff still requires a stopped-runtime snapshot and account proof.

Continue natural live observation of fills, exits, stream freshness, 20-slot
ownership and access errors. The local TV dashboard is active but remains
local evidence rather than venue flat proof.

The responsive dashboard assets are active at `c8a7a45` without a process or
bot restart. Browser verification covered 800×600 through 1920×1080 with no
page/panel overflow while preserving the two P&L cards, six-column gain table,
20-slot map, health and newest fitting activity rows. Continue treating it as
local evidence rather than authenticated venue state.

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

After the next normal deployment/restart, collect the 60%, 70% and 80%
`WOULD_REFUSE` labels under the current size-13/22-slot configuration. Require
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

`stop_commissioning.sh` primarily proves process termination; the runner can
end with `flat=false`. Decide whether the script should fail when the final
record lacks flat proof. Until then, every stop requires final-record plus an
authenticated venue snapshot. The script now reports every 30 seconds during
its unchanged 300-second grace, but progress output is not flat proof.
