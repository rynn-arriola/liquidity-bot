# Open work only

This file contains unfinished work. Completed implementation history is in
[docs/history/NEXT_THROUGH_2026-08-27.md](docs/history/NEXT_THROUGH_2026-08-27.md).

## 0. Migrate the only live runtime to the laptop

The bot is stopped after authenticated recovery proved zero working orders and
the authoritative ledger snapshot proved zero nonzero positions. Commit
`31f815c` is pushed and deployed; all 13 hashes matched and all 776 local/server
tests passed. On 2026-08-29 00:07Z the laptop independently authenticated and
again found zero working orders/positions. Do not restart the current server or
either workstation without a new operator instruction.

The authorized 22:56Z start repaired all 20 legacy intents. Three entry probes
received the same VPN/proxy 403 while later slots backed off locally; 20 owners,
zero unresolved/retired and zero positions/orders proved containment. The
normal stop completed flat in about 30 seconds. Do not induce more probes.
Do not restart on this host. The operator selected the laptop as the next
primary live runtime and the desktop as a manual cold standby. At 23:21Z the
desktop received direct online snapshots of the production and spread-scalp
databases plus restricted credentials, the deployed recovery archive and a
full-log/crontab diagnostic archive; all validated. The laptop still needs its
own direct verified pull rather than a cloud-synced copy.

Laptop parity, the Ubuntu 24.04 WSL2 runtime, dependencies, AC/network preflight,
direct verified execution/scalp/recovery/diagnostic pull, credential loading,
SQLite integrity/hashes and all 776 tests now pass. The active copies live in
WSL rather than OneDrive. Four matching systemd report/backup timers are
installed but disabled.

Before laptop activation:

1. regain SSH access, disable the preserved server crontab and enable/verify the
   four laptop timers; never run both schedule sets together;
2. prove the desktop runner absent and make the laptop the sole writable
   ledger/API owner (the current server and laptop runners are already absent,
   and the 00:07Z account snapshot was flat/order-free); and
3. start only after the operator explicitly authorizes it, then observe the
   startup, 20-slot state, orders, streams and errors.

Never use GitHub or a general file-sync service for the live SQLite database or
credentials. Any later desktop failover is manual and repeats the stopped-
runtime snapshot and known-account-state gates.

## 1. Remaining natural acceptance

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

Private-only silence now has natural acceptance with unchanged PID and
`cancels=0`. Simultaneous market/private silence still uses the documented
global quarantine and caused a temporary slate rebuild at 16:22Z; investigate
only if recurrence shows a local liveness defect rather than venue/network loss.

## 2. Accept the deployed post-flat mandatory refill

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

## 3. Accept rate-limited private-shard recovery

Commit `a8d91d0` is active. Its clean startup reached 20/20 quoting, 37 resting
orders, zero positions, zero absent and zero persistent unresolved state without
a restart-window error. Do not induce a fault. Natural acceptance must show
`[private-shard-handoff-rate-limit] ... cancels=0`, followed by
`[private-shard-recovery] ... cancels=0`, unchanged PID/owners and no persistent
unresolved/`UNKNOWN_ABSENT`. Non-429, stale and exhausted proof must retain the
global fallback.

## 4. Collect fixed-GTC evidence

Commit `de1478b` repaired the cumulative-gap defect and is deployed. The live
report proves 20 GTC starts, 20/20 feature rows, 20 clean paths and zero false
exclusions. The combined public scope is 98/100, no post-start 429 or collector
exit occurred, and bot PID 21791 was unchanged through 17-minute acceptance.

The collector began before `de1478b`; do not invalidate its 20 clean trials just
to load forward decode/drop interval closure. It loads on the next natural
supervisor restart. Collect three complete days plus one held-out day before
any strategy proposal, and never add overlapping cohorts as separate profit.

## 5. One unresolved mutation can have account-wide availability cost

Review whether unrelated proven-safe markets can continue while one owner is
ambiguous without weakening the account-wide unowned-order invariant. Raising
a timeout is not a fix; the venue has hidden accepted state longer than prior
timeout values.

## 6. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination; the runner can
end with `flat=false`. Decide whether the script should fail when the final
record lacks flat proof. Until then, every stop requires final-record plus an
authenticated venue snapshot. The script now reports every 30 seconds during
its unchanged 300-second grace, but progress output is not flat proof.
