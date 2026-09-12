# Active operator decisions
This file records human choices, not a backlog or AI recommendations. Code and the running command remain the authority for exact enforced values; if they disagree with this page, stop and show the operator the discrepancy.
## Decision rights
- **September 12 release authorization:** operator requested loading all of today's prepared changes and checking readiness. This supersedes the preceding no-deploy/no-restart hold for the already reviewed whole-bot/readiness/index/account-watch release only. Perform graceful stop, positive flat/order-free/known-ownership proof, reviewed deployment and exact runtime tests, then a fresh start gate and restart in REST size 10 / 32. Observe natural performance and exit handling; no automatic source switch, risk change, force-kill or proof bypass. A failed gate leaves restart blocked.
- **September 12 permanent account sources:** operator authorized implementation, complete end-to-end/edge-case review and a safely gated restart after the fix. Select `websocket` or `rest` explicitly; never automatically switch in either direction. Verify the inactive source only for a bounded operator request, retaining one engine/ledger/executor and max 30 flat pending REST entries. Restart into REST at unchanged size 10 / 32 and existing risk rules. Proposed 0.5-second polling and 8/s remain unaccepted tuning ideas. [Design](docs/ACCOUNT_SOURCE_ARCHITECTURE.md) · [release audit](docs/history/ACCOUNT_SOURCE_RELEASE_2026-09-12.md).
- **September 11 REST continuity work/stop:** after live evidence disproved the first repair's natural acceptance, the operator manually flattened and requested an immediate safe stop plus the narrow exit fix. Keep production stopped after preparing the follow-up; a later deployment/restart needs fresh operator authority and all normal parity/flat/clear gates. Preserve size 10 / 32, every existing strategy/risk rule and temporary max 30 **flat pending entries**. Orders and unresolved reducer reservations in open-position markets, reducers and IOC exits do not consume those 30 entry slots. [Handoff](docs/history/REST_BULK_CONTINUITY_2026-09-11.md).
- **2026-09-11 diagnostic credential retention:** after an isolated second-key test, the operator explicitly authorized retaining the new key for testing and declined revocation despite chat exposure. Store only in Windows Credential Manager under `LiquidityBot/Diagnostics/PolymarketUS/SecondaryKey`; no secret/key value in tracked files. Do not replace the bot's original credentials or automatically use/revoke this diagnostic key. The exposure risk remains; storage does not undo it.
- **2026-09-08 actual-live protection:** after the confirmed Mutti/Picard/Vivarelli incident, operator authorized fixing the live-event gap and restarting after review. Parent-event live/ended/period/score evidence overrides future schedules, forbids entries/re-entry, and triggers existing cancel-confirm-exact-exit handling. Preserve every current size, capacity, reward, cap and HARD rule. [Release](docs/history/ACTUAL_LIVE_EVENT_GUARD_2026-09-08.md).
- **2026-09-07 Discord presentation/deployment:** concise actual reports without repeated methodology/side notes; retain pending/estimated/unavailable labels and critical alerts, never invent withdrawal values. After deployment without restart, the operator authorized **Command 1** to stop safely and restart `f7816c324` with unchanged size 10 / 32 and risk rules. The standalone reward report retains its hourly schedule. [Implementation](docs/history/DISCORD_REPORT_CLEANUP_2026-09-07.md).
- The human operator chooses strategy behavior, risk limits, capital, quote size, slot count, deployment timing and stop/restart timing.
- AI agents investigate, explain, implement, test, review, document and carry out authorized operations. Every fix requires a dedicated final review and relevant edge-case audit before both push and deployment; passing tests do not waive it. Agents should not assume the operator writes code.
- Explain proposed changes in plain language, including expected benefit,
  downside, effect on rewards/orders/losses and whether a restart is required.
- A measurement or watchlist idea is not permission to alter live trading.

## Live strategy and risk choices

| Choice | Decision in force |
|---|---|
| Production runtime | The former server was destroyed after repeated Polymarket VPN/proxy 403s. The explicitly authorized laptop is the sole live runtime; the desktop is prepared but inactive as its manual cold backup. They must never run together |
| UI maintenance participation | Exact official Polymarket US application maintenance pauses new entries and cancels flat entry quotes without stopping the runner; cancels and position exits remain live. Resume requires three healthy application checks. A persistent operator pause has the same reduce-only scope and cannot override UI maintenance when cleared |
| Scheduled production maintenance | Automatically follow the official Statuspage schedule for the exact production exchange component. Begin withdrawing entries and flattening positions 30 minutes before the announced start; remain paused throughout maintenance; resume no earlier than one hour after the later of scheduled end or official completion and only after three complete authenticated account-health reads plus all existing stream/reconciliation/UI gates. Keep the bot process alive; do not require a manual trigger |
| Quote size | 10 contracts is loaded live; whole values 5-30 are supported and restart-scoped |
| Concurrent market capacity | 32 slots is the loaded direct canary; 24 is the first rollback target and 20 remains the bare-launcher default. Reviewed values 20-32 are accepted, while 33 remains refused. Reduce on a capacity-linked breach; peak-band acceptance at actual 32-owner occupancy remains required before calling 32 durable |
| Entry horizon | Six hours before the modeled pregame boundary |
| Conflicting pregame clocks | Structured market `gameStartTime` remains required. When that same market question contains an exact generated `scheduled for ... UTC` timestamp that is earlier, use the earlier time as the pregame deadline. Question text may only shorten trading time; it may never extend the structured time or replace a missing clock |
| League selection | No league filter |
| New-entry spread | Keep the 2-cent fresh-selection minimum. If a selected market's live book tightens to exactly one tick, quote both touches rather than backing only the offer away; a locked book keeps the safe 2-cent non-crossing back-off. Keep the separate 1.5-cent retention hysteresis |
| Persistent idle slots | After 10 continuous minutes of named current-book economic refusal, a flat/order-free/ambiguity-free slot may be replaced on the normal 15-minute scan only when that scan supplies a viable replacement |
| Post-flat slot recovery | Keep the near-deadline exit safety pause. Once positive position evidence proves the final urgent position flat, mandatory empty/finished capacity may request an immediate refill before the ordinary configured-capacity service sweep. A cached selection may be used only within 120 seconds and only after fresh metadata, incentive and full-book validation of every replacement; economic-idle replacement remains on its normal 15-minute cadence |
| Post-kickoff empty-book recovery | Do not let one exact unpriceable recovery owner disable unrelated markets. Only a persisted bot-owned, basis-matched, nonzero post-kickoff position with zero working/absent orders, zero unresolved intents and a response-backed empty book may enter settlement wait. It permanently cannot quote, reserves one configured slot and its full portfolio risk, keeps private/account monitoring and a 60-second read-only book probe, and returns to the unchanged exit path if depth reappears. Flat proof releases the slot. Every other unknown or failure remains fail-closed |
| Inactive owned-market recovery | Fresh exact closed/inactive/non-open metadata may immediately request replacement of a flat, order-free, ambiguity-free owner through the existing fresh-recheck top-up. An exact bot-owned nonzero position with matching basis and no order ambiguity enters mutation-free inactive-market wait, reserves one slot/full risk and keeps account/private monitoring plus one-minute metadata probes. Fresh open metadata restores the normal exit path; flat proof permits replacement. Missing/failed metadata never proves closure, and repeated unknown refreshes only back off to 15 minutes after 12 failures |
| Portfolio / market exposure | `(configured slots + 10) × / 9 × quote size`; loaded size 10 / 32 is $420 / $90 with a $100 breadth cushion |
| Stressed-exit entry cap | `4 × quote size`; $40 at size 10. This is economic eligibility for a proposed entry, not a realized-loss stop |
| Sole money-loss stop | Marked-equity HARD at `$90 × quote size / 10 × configured slots / 32`; $90 at size 10 / 32. Keep existing open-position cohort confirmation. September 7–8: confirmed HARD must cancel pending entries, immediately evacuate all positions with exact proof, and persistently pause until the next ET day plus fresh flat/account/health gates. No process shutdown or same-day restart reset. September 8 operator authorized fixing all review findings and restarting after verification. [Handoff](docs/history/PERSISTENT_RECOVERY_2026-09-08.md) |
| Loss-scaled entry-order ceiling | Use the same ET-day adjusted marked loss and breadth/quantity-scaled HARD meter. Maximum flat-market pending entries are 30 at 50% HARD, 25 at 70%, 20 at 80%, 15 at 90%, and 10 at 95%. HARD at 100% remains the sole loss stop. The tightest reached tier persists through restart and cannot loosen until the next ET day. Unresolved creates reserve capacity; cancel excess owned entries one at a time from lowest-ranked markets, preserving pairs where possible. Orders in open-position markets, reducers, IOC exits and shutdown are excluded. Values load only at restart from the strict policy file and clamp to the configured two-orders-per-slot capacity |
| Time-scaled entry-order ceiling | Separately allow at most 30 pending flat-market entries from 00:00 inclusive to 09:00 exclusive `America/New_York`, and 40 from 09:00 through 23:59:59. Apply the smaller of the clock cap, durable loss cap and configured two-orders-per-slot capacity. At 09:00 the clock band changes without resetting loss state. Keep reward/rank and pair-preserving retention unchanged; position-market orders, reducers, IOC exits, recovery and shutdown are excluded. Values load only at restart from the strict policy file |
| Risk-day clock | America/New_York, DST-aware |
| Queue position study | Closed and retired; preserve its historical rows/report, never cancel/repost automatically, and do not restart its public-trade collector without a new operator decision |
| Entry-quality filter | September 7: operator explicitly chose to enable the configurable per-side 70% minimum stressed retained-value filter for a live trial despite the preliminary reward-cost uncertainty, then authorized Command 1. Set enabled=true; retain the actual-action estimate repair. Preserve exits, 900 seconds, size 10 / 32, reward priority, caps and $90 HARD. No automatic midnight disable is configured; later disable requires a config change and safe restart. Saved labels remain evidence only. [Trial](docs/history/ENTRY_QUALITY_TRIAL_2026-09-07.md) |
| Spread-scalp study | Closed and stopped; preserve its isolated database/report and do not restart the collector without a new operator decision |
| Inventory deadline | 900 seconds, with the separate pre-kickoff flatten rule; an exact venue-tradable `0.01`–`0.09` contract residue gets five passive minutes, then uses cancel-confirm-exact-IOC cashout |
| Taker-exit session count | Removed. The number of risk-reducing taker orders is not a session stop, entry pause or process-lifetime limit |
| Execution-fee telemetry | Gross positive fees on owned taker fills remain measured against the cent-rounded reference `$10 × (quote size / 10) × (slots / 24)`; $10 at size 10 / 24. Crossing it logs and reports once but must not block/cancel quotes, pause/stop trading or alter entry/exit eligibility. HARD retains enforcement. Maker rebates do not offset the gross observation |

The marked HARD sensor blocks new entries immediately but, while inventory is open, waits for a breach-time position to close and a later adjusted balance instead of forcing a paper-loss liquidation. Once confirmed, cancel pending entries and evacuate every remaining position through sequential cancel-confirm-exact-IOC; keep retrying safely during temporary failure, never claim flat without proof, and resume only on a later ET day after fresh flat/account gates. The daily latch survives restarts. Realized session and per-market P&L remain evidence only.
The operator removed the separate taker-exit count ceiling on 2026-09-01 after a normal size-10/32 run reached 40 risk-reducing exits at only `-$36.2075` against the then `$128` HARD. Count/fees remain non-authoritative. September 7–8 approves persistent classified transport/throttle/startup waits, continuing held-position exit rounds, a recoverable-cascade entry pause and supervisor attention mode. Unknown integrity/ownership defects still lock trading; manual stop still stops. September 8 authorizes the four reviewed safety fixes, push/deploy and a safely gated restart, including prepared persistent recovery. Preserve business rules; provisional HARD must also survive restart.
Twenty-four passed 30 continuous minutes of startup and selection bursts with zero failures/429s, sustained queue, safety deadline, stream loss or persistent account ambiguity. Loaded size 10 / 24 uses three private shards as `8/8/8`, keeps the 4/s governor and six-hour horizon, scales breadth-dependent account stops, and lets quantity-derived values follow size 10. The operator elected a direct 24 → 32 canary rather than staged 26/28/30 runs to shorten the search, with immediate reduction to 24 (or 22 if the known-good response requires it) on any capacity-linked failure/429, two consecutive queue-depth breaches, safety deadline, stream-coverage loss, persistent account ambiguity, unacceptable decision latency/CPU or lower reward per resting side-hour. Thirty-two adds a fourth `8`-market private shard but must not alter selector order, quote economics, exact timer/event ownership, private wire ordering or the reconciler/mutation path. Every nonzero position remains unconditional; unchanged flat markets alone may skip, and buying power remains an apply-time gate. Do not distribute venue authority across workers; future workers, if justified, are pure calculation over immutable snapshots.
## Reporting and data choices

- Reward and risk days use Eastern Time.
- Discord capital guidance is read-only: exposure reserve plus seven hard-stop days for delayed rewards. Pending is excluded; stale/ambiguous state is unavailable; `free = max(0, min(buying power, settled cash - comfortable))`.
- Deposits and withdrawals must not affect trading losses; rewards and other proven non-trading cash are excluded too. The operator requested a nearby saved snapshot when restart crosses midnight: reporting uses the first ET opening up to 15 minutes late, clearly labels its actual interval as estimated and does not finalize calendar-day net. Risk baselines and $90 HARD remain unchanged. [Fallback](docs/history/NEAR_MIDNIGHT_REPORT_FALLBACK_2026-09-07.md).
  At configured size 10 / 32 and $90 HARD, $320 footprint + $100 cushion + $630 weekly reserve = $1,050 comfortable.
- A routine five-minute Discord summary may wait up to 60 seconds when fresh balance evidence is blocked only by current order ambiguity. It rechecks the local ledger every two seconds without venue traffic; stale balance evidence and ambiguity that survives the bound still report `UNAVAILABLE`, while shutdown and critical summaries never wait.
- The seven-day reward table and hourly Discord post remain enabled and read-only, but they are opportunistic rather than trading-priority work. They use only an idle shared-API interval, at most one request every four seconds, and may finish later within the hourly interval. Their lane never publishes delay onto discovery, entry or safety clocks; a report claim can consume only the one aggregate 0.25-second interval it found idle. The five-minute cached Discord summary stays on its existing schedule and adds no venue call.
- `score hours` means merged resting side-hours, not model score-seconds.
- Pending rewards are displayed but never counted as paid or finalized net.
- The laptop TV dashboard starts automatically after a successful bot start.
  It remains local/read-only, consumes no venue API budget, and a display or
  browser failure must not fail or restart trading. Preserve every existing
  card and panel; add Today P&L and Overall P&L at the top and append the daily
  table in this order: `Date ET`, `Trading`, `Gain`, `Pending`, `Side Hours`,
  `Net`. Visible TV copy says Gain, not reward, and Net always excludes pending.
- “Run daily task” means the local-only backup utility described by the ignored
  workstation `AGENTS.local.md`. It is manual, never scheduled, never pushed, and
  must not stop or restart the bot.

## Multi-machine operating choice

- `v1.2.0` is the immutable reviewed pre-REST-fallback rollback checkpoint at `a3defc014`; it retains fail-closed behavior during the private-feed incident. `v1.1.0` and `v1.0.0` remain the earlier immutable 24-slot and 20-slot checkpoints.
- Work may be performed from the desktop or laptop, but GitHub `main` is the
  synchronization point between them; neither workstation is independently
  authoritative.
- Unqualified “run the bot” targets the laptop. Only explicit “run on the
  computer/PC” selects the desktop, still with the single-owner handoff/preflight. “Stop bot now,” “stop it now,” or “just stop it” means immediate graceful stop before checks so manually cancelled orders cannot be replaced; never force-kill and verify venue state afterward. “Restart now while flat” uses the same stop-first sequence, then positive post-stop proof and normal startup gates; failure leaves it stopped. “Command 1” is the complete immediate stop → post-stop proof → finish only missing review/test/push/parity/deploy verification for already in-scope AI changes → fresh gate → persisted-setting restart → live acceptance workflow; valid completed work is reused, and any uncertain gate leaves the bot stopped.
- The laptop is the primary live runtime. The desktop is prepared for development,
  offline verification and manual cold backup only. Exactly one machine may use the
  live API credentials and active ledger at a time; there is no automatic
  failover and the two machines must never run concurrently.
- The active SQLite ledger follows the live runtime. It must move only as a
  verified stopped-runtime snapshot during a deliberate handoff and must never
  be bidirectionally synchronized through GitHub, OneDrive, Dropbox or another
  live file-sync service. Backups on the inactive machine are recovery copies,
  not a second writable authority.
- Compare runtime to `DEPLOYED_COMMIT` plus source/test hashes, not a newer documentation-only commit.
- “Run parity” runs `scripts/check_parity.py --sync` here and on a reachable SSH
  peer. Clean behind-only checkouts may fast-forward; all other work is preserved.
- It also checks WSL/loaded source and places a hash/integrity-verified online
  ledger backup on reachable PCs; ignored copies are never writable authority.
- WSL may live-fast-forward only docs, tests and parity tooling. Runner/config/
  launcher changes require reviewed deployment. Parity never waits for Drive.
- Parity cannot overwrite dirty/diverged work, expose secrets, deploy trading
  code, activate a recovery ledger, hand off ownership or control the bot.
- “Run weekly task” manually creates and verifies one new client-encrypted
  archive in Drive folder `Liquidity Bot Private Recovery`. It is not
  scheduled. Report source/role/time/hash/age; never describe a stale copy as
  current. Codex is the primary AI operator and Claude is the backup AI
  operator; either may run the same workflow through its own Drive access.
- Databases, archives, credentials, logs and keys must never be committed. The
  execution ledger may travel between PC and laptop only as a verified SQLite
  online-backup copy over key-authenticated SSH; Drive accepts only the separate
  verified encrypted weekly archive. Private/recovery keys enter neither.

## Decisions explicitly not authorized

- No automatic same-price cancel/repost to move a nearly-front order backward
  in the queue.
- The maximum-30 validator is dormant capability, not permission to change the live size. Size 20 remains measurement-only until separately selected.
- No removal of the 2-cent fresh-selection gate beyond the explicit one-tick touch behavior for a market already selected on a qualifying book.
- No live spread-scalping orders or automatic early-exit rule; collected scalp
  evidence must be reviewed before any separate strategy proposal.
- No league-based reward-market filter.
- No inference that an absent venue order is canceled.
- No new droplet based solely on the transient August 26 location denial.
- No residential proxy, disguised IP or other attempt to evade Polymarket's location/access controls; obtain support confirmation before changing hosts.
- No unattended or automatic desktop/laptop failover. A handoff requires the
  old runner to be absent, known account state, a verified stopped-runtime
  ledger snapshot and explicit start authorization on the new owner.
When the operator changes one of these choices, update this file and [STATUS.md](STATUS.md) in the same task and record the evidence or implementation handoff in the appropriate history file.
