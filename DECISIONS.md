# Active operator decisions
This file records choices made by the human operator. It is not a backlog and
not a list of AI recommendations. Code and the running command remain the
authority for exact enforced values; if they disagree with this page, stop and
show the operator the discrepancy.

## Decision rights

- The human operator chooses strategy behavior, risk limits, capital, quote size, slot count, deployment timing and stop/restart timing.
- AI agents investigate, explain, implement, test, review, document and carry
  out authorized operations. Every fix requires a dedicated final review and relevant edge-case audit before both push and deployment; passing tests do not waive it. Agents should not assume the operator writes code.
- Explain proposed changes in plain language, including expected benefit,
  downside, effect on rewards/orders/losses and whether a restart is required.
- A measurement or watchlist idea is not permission to alter live trading.

## Live strategy and risk choices

| Choice | Decision in force |
|---|---|
| Production runtime | The former server was destroyed after repeated Polymarket VPN/proxy 403s. The explicitly authorized laptop is the sole live runtime; the desktop is prepared but inactive as its manual cold backup. They must never run together |
| UI maintenance participation | Exact official Polymarket US application maintenance pauses new entries and cancels flat entry quotes without stopping the runner; cancels and position exits remain live. Resume requires three healthy application checks. A persistent operator pause has the same reduce-only scope and cannot override UI maintenance when cleared |
| Quote size | 10 contracts is loaded live; whole values 5-30 are supported and restart-scoped |
| Concurrent market capacity | 32 slots is the loaded direct canary; 24 is the first rollback target and 20 remains the bare-launcher default. Reviewed values 20-32 are accepted, while 33 remains refused. Reduce on a capacity-linked breach; peak-band acceptance at actual 32-owner occupancy remains required before calling 32 durable |
| Entry horizon | Six hours before the modeled pregame boundary |
| League selection | No league filter |
| New-entry spread | Keep the 2-cent fresh-selection minimum. If a selected market's live book tightens to exactly one tick, quote both touches rather than backing only the offer away; a locked book keeps the safe 2-cent non-crossing back-off. Keep the separate 1.5-cent retention hysteresis |
| Persistent idle slots | After 10 continuous minutes of named current-book economic refusal, a flat/order-free/ambiguity-free slot may be replaced on the normal 15-minute scan only when that scan supplies a viable replacement |
| Post-flat slot recovery | Keep the near-deadline exit safety pause. Once positive position evidence proves the final urgent position flat, mandatory empty/finished capacity may request an immediate refill before the ordinary configured-capacity service sweep. A cached selection may be used only within 120 seconds and only after fresh metadata, incentive and full-book validation of every replacement; economic-idle replacement remains on its normal 15-minute cadence |
| Post-kickoff empty-book recovery | Do not let one exact unpriceable recovery owner disable unrelated markets. Only a persisted bot-owned, basis-matched, nonzero post-kickoff position with zero working/absent orders, zero unresolved intents and a response-backed empty book may enter settlement wait. It permanently cannot quote, reserves one configured slot and its full portfolio risk, keeps private/account monitoring and a 60-second read-only book probe, and returns to the unchanged exit path if depth reappears. Flat proof releases the slot. Every other unknown or failure remains fail-closed |
| Inactive owned-market recovery | Fresh exact closed/inactive/non-open metadata may immediately request replacement of a flat, order-free, ambiguity-free owner through the existing fresh-recheck top-up. An exact bot-owned nonzero position with matching basis and no order ambiguity enters mutation-free inactive-market wait, reserves one slot/full risk and keeps account/private monitoring plus one-minute metadata probes. Fresh open metadata restores the normal exit path; flat proof permits replacement. Missing/failed metadata never proves closure, and repeated unknown refreshes only back off to 15 minutes after 12 failures |
| Portfolio / market exposure | `(configured slots + 10) × / 9 × quote size`; loaded size 10 / 32 is $420 / $90 with a $100 breadth cushion |
| Stressed-exit entry cap | `4 × quote size`; $40 at size 10. This is economic eligibility for a proposed entry, not a realized-loss stop |
| Sole money-loss stop | Marked-equity HARD at `8 × quote size × configured slots / 20`; $128 at loaded size 10 / 32. An open-position breach is provisional until a breach-time position closes |
| Risk-day clock | America/New_York, DST-aware |
| Queue position study | Closed and retired; preserve its historical rows/report, never cancel/repost automatically, and do not restart its public-trade collector without a new operator decision |
| Adverse-fill/cliff study | Collect 60%, 70% and 80% retained-value `WOULD_REFUSE` labels plus recent-selector/reward/no-candidate/unknown cost with zero extra venue calls; no live refusal gate is authorized |
| Spread-scalp study | Closed and stopped; preserve its isolated database/report and do not restart the collector without a new operator decision |
| Inventory deadline | 900 seconds, with the separate pre-kickoff flatten rule; an exact venue-tradable `0.01`–`0.09` contract residue gets five passive minutes, then uses cancel-confirm-exact-IOC cashout |
| Taker-exit session count | Removed. The number of risk-reducing taker orders is not a session stop, entry pause or process-lifetime limit |
| Execution-fee telemetry | Gross positive fees on owned taker fills remain measured against the cent-rounded reference `$10 × (quote size / 10) × (slots / 24)`; $10 at size 10 / 24. Crossing it logs and reports once but must not block/cancel quotes, pause/stop trading or alter entry/exit eligibility. HARD retains enforcement. Maker rebates do not offset the gross observation |

The marked HARD sensor blocks new entries immediately but, while inventory is open, waits for a breach-time position to close and a later adjusted balance instead of forcing a paper-loss liquidation. A confirmed result may stop with another position open. Realized session and per-market P&L remain reporting evidence with no independent pause or stop authority.

The operator removed the separate taker-exit count ceiling on 2026-09-01 after a normal size-10/32-slot run reached 40 risk-reducing exits and terminated at only `-$36.2075` marked trading P&L against the `-$128` HARD threshold. Taker activity and gross fees may remain evidence, but neither is trading authority. Among these account-activity measures, only the marked-equity HARD sensor may terminate the runner; ordinary system-integrity and explicit operator stops remain unchanged.
Twenty-four passed 30 continuous minutes of startup and selection bursts with zero failures/429s, sustained queue, safety deadline, stream loss or persistent account ambiguity. Loaded size 10 / 24 uses three private shards as `8/8/8`, keeps the 4/s governor and six-hour horizon, scales breadth-dependent account stops, and lets quantity-derived values follow size 10. The operator elected a direct 24 → 32 canary rather than staged 26/28/30 runs to shorten the search, with immediate reduction to 24 (or 22 if the known-good response requires it) on any capacity-linked failure/429, two consecutive queue-depth breaches, safety deadline, stream-coverage loss, persistent account ambiguity, unacceptable decision latency/CPU or lower reward per resting side-hour. Thirty-two adds a fourth `8`-market private shard but must not alter selector order, quote economics, exact timer/event ownership, private wire ordering or the reconciler/mutation path. Every nonzero position remains unconditional; unchanged flat markets alone may skip, and buying power remains an apply-time gate. Do not distribute venue authority across workers; future workers, if justified, are pure calculation over immutable snapshots.
## Reporting and data choices

- Reward and risk days use Eastern Time.
- Discord capital guidance is read-only: exposure reserve plus seven hard-stop days for delayed rewards. Pending is excluded; stale/ambiguous state is unavailable; `free = max(0, min(buying power, settled cash - comfortable))`.
  At loaded size 10 / 32, $320 footprint + $100 cushion + $896 weekly reserve = $1,316 comfortable.
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

- `v1.1.0` is the current immutable stable rollback anchor selected before capacity work beyond 24 slots; its executable content matches loaded source `4b6e4e8`, while `v1.0.0` remains the earlier 20-slot checkpoint.
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
- The maximum-30 validator is dormant capability, not permission to change the
  live size. Size 20 remains measurement-only until separately selected.
- No removal of the 2-cent fresh-selection gate beyond the explicit one-tick touch behavior for a market already selected on a qualifying book.
- No live spread-scalping orders or automatic early-exit rule; collected scalp
  evidence must be reviewed before any separate strategy proposal.
- No league-based reward-market filter.
- No inference that an absent venue order is canceled.
- No new droplet based solely on the transient August 26 location denial.
- No residential proxy, disguised IP or other attempt to evade Polymarket's
  location/access controls; obtain support confirmation before changing hosts.
- No unattended or automatic desktop/laptop failover. A handoff requires the
  old runner to be absent, known account state, a verified stopped-runtime
  ledger snapshot and explicit start authorization on the new owner.

When the operator changes one of these choices, update this file and [STATUS.md](STATUS.md)
in the same task and record the evidence or implementation handoff in the appropriate history file.
