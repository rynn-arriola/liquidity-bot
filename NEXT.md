# Open work only
**Investigate real REST read throttling and latency before claiming sustained acceptance:** the read-only September 12 follow-up found four real 429s (balance three, cash history one) through 17:11:59Z, despite interval rates below 4/s; it also found a 6.833s max admission wait and later 13.534s response. Determine endpoint/rolling budget and scheduling contribution from redacted evidence; do not assume outage, raise rates, switch source, lower capacity or alter $90 HARD. Runner remains live; latest request authorized status/log review only, not a fix/restart. Three episodes reached flat, including two natural unfilled hard-IOC retries; keep measuring future deadlines and position coverage. [Evidence](docs/history/ACCOUNT_PERFORMANCE_RELEASE_2026-09-12.md).
**Natural hard-exit/final-flat evidence now exists on `2f7ba0222`:** ITF and Kormyk/Kulole retried an unfilled IOC then reached flat at 16:50:28Z and 16:52:09Z; a residual episode also reached flat. No manual closure is attributed to this acceptance. At 17:09:44Z two newer -10 positions remained open with exact BUY-10 reducers. Continue observing, never induce fills or widen IOC authority. [Evidence](docs/history/ACCOUNT_PERFORMANCE_RELEASE_2026-09-12.md).
**Keep REST selected; external WebSocket recovery and natural source acceptance remain:** the 14:30:53–14:31:38Z bounded probe received zero application frames despite Pongs. Do not auto-switch or add a permanent inactive stream. Preserve cancel-publication throttling and compare natural source/load/timing evidence only after an authorized release. The scan watcher and timing hooks are installed; actual cross-source performance percentiles still need live data. [Design](docs/ACCOUNT_SOURCE_ARCHITECTURE.md) · [latest audit](docs/history/ACCOUNT_WATCH_EXIT_REVIEW_2026-09-12.md).
**Naturally accept loaded REST hard-exit continuity:** release `576f47f3a` is running size 10 / 32 in REST fallback after complete review and flat/clear gates. On the next natural unfilled hard IOC, verify the exact reducer/IOC path retries the remaining quantity within the unchanged three-attempt/30-second authority, without duplicate send or unrelated flat-work delay. Continue verifying that the temporary cap never exceeds 30 flat pending entries while open-position orders/reducers/exits remain excluded. Do not induce a fill or failure. [Evidence](docs/history/REST_BULK_CONTINUITY_2026-09-11.md).
**Actual-live guard `105d8d2b8` loaded and entry-accepted:** restart rebuilt 29 authenticated orders on 22 independently confirmed-pregame markets with zero positions and zero incident-market orders. Two startup rechecks naturally rejected newly non-pregame markets. Both 1,226-test suites/168 hashes passed; first two API windows were clean. Natural held-position live evacuation and later-event restart latch acceptance must not be induced. Venue publication lag and unavailable exit depth remain limitations. [Release](docs/history/ACTUAL_LIVE_EVENT_GUARD_2026-09-08.md).
**Discord cleanup is loaded; natural report acceptance remains:** Command 1 restarted `f7816c324` after two authenticated flat/clear gates, reused 1,160 native tests and verified 160 hashes. Runner `770972` rebuilt 39 authenticated orders with zero positions/unknown orders; first API window was clean. Inspect the next natural summary/reward posts and future repeated noncritical/critical alerts without inducing faults. [Handoff](docs/history/DISCORD_REPORT_CLEANUP_2026-09-07.md).
**Reconcile September 6–7 account/fill gaps:** exact-window differences are $14.90 / $7.3964 (today includes $0.1674 open mark). Preserve account authority; require positive activity evidence before attribution or repair. [Cutoffs and evidence](docs/history/TRADING_LOSS_ATTRIBUTION_2026-09-07.md). Completed work: [archive](docs/history/NEXT_THROUGH_2026-08-27.md).
**$90 HARD policy deployed in `43e9db826`:** the authorized Command 1 keeps size 10 / 32, proportional scaling and existing tiers. September 6 post-stop trading was -$104.2022, beyond the new limit; never reset its baseline or loss ratchet to force same-day trading. The prior startup-managed position has cleared. [HARD handoff](docs/history/HARD_STOP_90_POLICY_2026-09-06.md).
**Scalable entry-order policy is loaded; natural transitions remain:** current marker `4654479c1` retains 30 overnight, 40 daytime and 30/25/20/15/10 loss tiers. The falsely persisted September 4 cap from a 63-second 20-slot launch was removed only after a stopped-safe integrity-checked backup and exact row match; explicit 10/32 restarts then proved both daytime-above-30 and overnight-30 behavior with zero positions. Naturally verify the next 09:00 change and genuine 50/70/80/90/95% transitions, including same-day persistence and reducer/exit bypass. Do not induce loss or fills. [Handoff](docs/history/SCALABLE_ENTRY_ORDER_POLICY_2026-09-04.md).
**Scheduled-maintenance automation is loaded; the September 6 transition remains:** the September 5 cycle naturally proved evacuation, flat hold and health-gated recovery after the deleted-record repair. The next production window is scheduled for 02:00–06:00 ET on September 6; naturally verify quote withdrawal, positive flat proof before the outage, no entries during the hold and health-gated recovery. Do not induce a fill, failure or maintenance state. [Handoff](docs/history/SCHEDULED_MAINTENANCE_AUTOMATION_2026-09-04.md).
**One-tick touch entry is loaded; natural strategy acceptance remains:** `2c3ce0e54` restarted after exact parity, 1,078 native tests and a zero-order/zero-position/clean-ledger gate. Initial acceptance found 58 authenticated orders across 31 markets, zero positions/ambiguity and a clean API window, but no exact-one-tick live book occurred. Naturally require both sides at `ticks_from_best=0`, no post-only rejection, and unchanged reducer availability; then compare matched-pair rate, taker reductions, reward side-hours and P&L against the recorded baseline. Do not induce a fill or infer benefit from one episode. [Evidence](docs/history/ONE_TICK_TOUCH_ENTRY_2026-09-03.md).
**Collect program-level reward evidence before changing width:** run the read-only program reward audit after several complete ET reward days and after pending rows settle. Compare program dollars/net per resting side-hour; use top-24 versus rank-25+ only to show exposure until venue earnings can be causally allocated. Do not change size, rank, program gates or the 32-slot canary from this measurement alone. [Measurement boundary](docs/history/PROGRAM_REWARD_AUDIT_2026-09-03.md).
**Bounded event discovery is accepted; page-growth observation remains:** `c741e7a97` restarted cleanly after exact parity and fresh flat/known-state proof, used two of ten event pages, rebuilt 56 authenticated orders across 31 markets and closed its first API window with zero failures/429s/cooldown. Observe `event_pages` naturally over future scans; no induced overflow test and no larger ceiling are justified. [Evidence](docs/history/REWARD_FIRST_DISCOVERY_RECOVERY_2026-09-02.md).
**Inactive-position wait is accepted; natural reopen/flat release remains:** `20812787e` retained the known `+10.0000` owner with full risk/one-slot reservation, no mutation intent and no recurring lifecycle top-up across multiple minute refreshes while ordinary markets rebuilt 36 orders. Naturally require reopen-to-exit restoration or authoritative-flat release; do not induce a venue transition. [Evidence](docs/history/INACTIVE_POSITION_TOP_UP_AMPLIFICATION_2026-09-01.md).
**Nearby-midnight report fallback is deployed/accepted without restart:** `1f9bacd92` passed 1,150 native tests and posted September 6 ~-$104.20 with actual snapshot endpoints. Observe future missing/beyond-15-minute boundaries staying unavailable, cash-flow exclusion and estimate/pending-net labels; prior runtime review fixes still need natural edge-case acceptance. No forced outage, fill or risk reset. [Acceptance](docs/history/NEAR_MIDNIGHT_REPORT_FALLBACK_2026-09-07.md).
**UI-maintenance reduce-only gate is loaded; natural transition remains:** the first loaded startup stayed `RUNNING`, confirming a healthy initial observation. Naturally require exact maintenance to cancel/block only flat new-risk quotes, keep reducers/exits live and require three healthy checks before resume; do not induce maintenance or use the operator latch only as a test. [Evidence](docs/history/UI_MAINTENANCE_ENTRY_PAUSE_2026-09-01.md).
**Settlement/inactive wait natural release acceptance remains:** the loaded runtime reserves one of 32 slots/full basis risk for the exact `10.0000` non-open owner and lets unrelated markets trade. Do not induce settlement, a book return or a fill. Naturally verify that returned depth resumes the ordinary exit path or authoritative flat proof releases the reserved slot, with continued zero entry authorization for the waiting owner.
**Dirty-market scheduler and realized-P&L optimization are loaded; performance acceptance remains:** `13ecc441b` includes the exact-equivalent chronological replay and additive fill index. Re-baseline across healthy steady windows at the loaded 32-slot canary; do not widen beyond 32. Roll back to 24, then `v1.1.0` if needed, on a missed flatten/reducer, lower resting fraction, higher latency or increased `execution_state_unknown`. [Handoff](docs/history/DIRTY_MARKET_SCHEDULER_2026-08-31.md).
**Recovery book validation is loaded; natural one-sided recovery acceptance remains:** `13ecc441b` retains both-empty/missing-metadata/order-payload refusal while allowing valid one-sided recovery data. Do not induce a position or book failure.
**Review safety fixes and persistent recovery loaded in `28de01f20`; natural acceptance remains:** both 1,202-test suites, 166 hashes and fresh flat/clear restart gates passed. Runner `1685485` is correctly paused for maintenance until after 11:30:07.911Z plus health checks; first API window is clean. Observe natural resumption, provisional/confirmed HARD persistence, ambiguity reservation and transport/Discord recovery without induced fills/failures. Older rollback code ignores durable holds and requires explicit risk review. [Release](docs/history/REVIEW_SAFETY_FIXES_2026-09-08.md).
**Sequential hard-exit natural acceptance pending:** active source/marker `31c2574` cancels one working order at a time, building side first, and waits for exact positive terminal proof before selecting the next. The 17:46Z flat-gated restart loaded it and normal startup passed. Naturally require a two-order hard exit with sequential `pending=1`, no builder fill/shared-timer retirement and an exact final reducer/IOC; do not induce it. [Evidence](docs/history/HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md).
**Tiny-residual natural acceptance pending:** source `6c5ea49` is loaded inside `9001a4b`. Naturally require `[action] ... reason=tiny_residual_timeout`, exact residual quantity, no overlapping cancel/IOC, positive flat proof and ordinary slot refill; do not induce a fill. [Evidence](docs/history/TINY_RESIDUAL_CASHOUT_2026-08-31.md).
**Direct 32-slot canary is loaded but has not completed peak-band acceptance:** `20812787e` runs size 10 / 32 with four private shards. Trough-band windows held roughly 18–29 quoting owners and remained below the 4/s host gate with zero failures/429s and queue depth one, but this is not an actual 32-owner test. Accept only from the 02:00–10:00 UTC peak band at approximately 32 held owners; reduce to 24 immediately for any capacity-linked failure/429, two consecutive `max_waiting > 1` windows, safety deadline, stream loss, persistent ambiguity, unacceptable latency/CPU or lower reward per resting side-hour. [Capacity criteria](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence) · [preparation](docs/history/SLOT_CAPACITY_CANARY_32_PREPARATION_2026-09-01.md).
**API admission correction active; natural backpressure acceptance pending:** `0ff6e3c6` keeps one aggregate 4/second WSL gate while isolating background 2/second, entry 4/second and safety 4/second lanes; passive reducers are safety, 429 cooldown is class-scoped, long non-safety work defers before network IO, stop wakes old waits and native Windows fails closed. It also stops selection/revalidation at its first local cooldown, keeps designed pause out of Discord `Lost to errors` and runtime-error accounting, avoids cache-to-full-scan amplification, and merges peer-published lane cooldowns into `[api-budget]` without another request. All 926 local/exact-deployed tests pass and both deliberate reversals failed exactly. `Command 1` loaded it after fresh zero-position/zero-order proof. The first live window reported `shared=1`, failures/429s `0`, all lane fields present and no JSON-state, quarantine or runtime error. A later window accepted merged visibility with background `2.7s`, entry/safety zero and local 429s zero; it cleared next window. Naturally require one `[api-backpressure]` line without market-failure fan-out or immediate fallback when a selection is actually deferred, plus bounded safety wait and no mutation retry/quarantine. A 22-book REST-only refresh still cannot fit inside five seconds at 4/second, so stream-loss capacity remains open. [Admission evidence](docs/history/HOST_API_ADMISSION_PRIORITY_2026-08-30.md) · [observability evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md).
## 0a. Load and naturally accept private Ping/Pong liveness
Commit `2aa9fec` includes the `a1ba212` repair and is deployed after a flat-gated restart and 830 WSL tests. Naturally accept a quiet private-shard interval beyond 180 seconds without false handoff and confirm any real Ping/Pong failure still enters bounded recovery with `cancels=0`, unchanged ownership, no persistent unresolved/`UNKNOWN_ABSENT` and no quarantine.
## 0. Continue natural acceptance of size 10 / 32
Loaded executable source `20812787e` retains the accepted size-10/24 behavior, the reviewed fourth shard, breadth-derived limits and report-lane/capacity instrumentation for the direct 32 canary, plus the accepted inactive-owner scan suppression. Continue observing natural fills/exits, stream recovery and lifecycle bursts without inducing a fault.
The score-clock repair already received natural acceptance: one backward sample logged `action=ignored_stale_sample`, added no retirement and the runner reached its configured scope. Continue observing size-10 fills/exits, risk scaling, stream freshness and mutation resolution; do not induce a fault.

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

## 5. Accept and evaluate the authorized entry-quality trial

The operator explicitly authorized the 70% filter and Command 1 on September 7.
Enabled-config review/native suites pass 1,173 tests; deployed `b089b3a6e` and
restarted after a fresh flat/clear gate. Initial quotes/API accepted; evaluate net.
The first matched reward sensitivity is unfavorable before replacements:
$207 flagged loss versus $376–$385 modeled reward sacrifice, substantially
pending. Validate reward/replacement net; saved labels remain non-authoritative. [Tradeoff](docs/history/ENTRY_QUALITY_REWARD_TRADEOFF_2026-09-07.md) · [implementation](docs/history/ENTRY_QUALITY_FILTER_2026-09-07.md).

The queue-reset, daily selection-probe and fixed-GTC collectors are closed. Their databases and reports remain evidence; do not restart them merely to add sample volume.

## 6. Naturally accept no-ID PLACE self-recovery after deployment

Naturally require one real 5xx/no-id PLACE to show the three-proof/ten-second wait, zero duplicate mutations, same-PID recovery and no missed risk/stop.
Do not induce it; the stopped-only resolver remains the contrary-evidence fallback.

## 7. Graceful stop success is not proof of flatness

`stop_commissioning.sh` primarily proves process termination; the runner can end with `flat=false`. Decide whether the script should fail when the final record lacks flat proof. Until then, every stop requires final-record plus an authenticated venue snapshot. The script now reports every 30 seconds during
its unchanged 300-second grace, but progress output is not flat proof.
