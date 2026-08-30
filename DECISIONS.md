# Active operator decisions

This file records choices made by the human operator. It is not a backlog and
not a list of AI recommendations. Code and the running command remain the
authority for exact enforced values; if they disagree with this page, stop and
show the operator the discrepancy.

## Decision rights

- The human operator chooses strategy behavior, risk limits, capital, quote
  size, slot count, deployment timing and stop/restart timing.
- AI agents investigate, explain, implement, test, review, document and carry
  out authorized operations. Every fix requires a dedicated final review and relevant edge-case audit before both push and deployment; passing tests do not waive it. Agents should not assume the operator writes code.
- Explain proposed changes in plain language, including expected benefit,
  downside, effect on rewards/orders/losses and whether a restart is required.
- A measurement or watchlist idea is not permission to alter live trading.

## Live strategy and risk choices

| Choice | Decision in force |
|---|---|
| Production runtime | The former server was destroyed after repeated Polymarket VPN/proxy 403s. The explicitly authorized laptop is the sole live runtime; the desktop is prepared but inactive as its manual cold backup. They must never run together |
| Quote size | 13 contracts live; whole values 5-30 are supported and restart-scoped |
| Concurrent market capacity | 20 slots remains the bare-launcher default. The 22-slot measurement canary passed, and on 2026-08-30 the operator explicitly selected another flat-gated restart at 22 after ending the monitoring period; the live setting is therefore 22 until a later operator decision |
| Entry horizon | Six hours before the modeled pregame boundary |
| League selection | No league filter |
| New-entry spread | Keep the 2-cent minimum; 1.5-cent retention hysteresis |
| Persistent idle slots | After 10 continuous minutes of named current-book economic refusal, a flat/order-free/ambiguity-free slot may be replaced on the normal 15-minute scan only when that scan supplies a viable replacement |
| Post-flat slot recovery | Keep the near-deadline exit safety pause. Once positive position evidence proves the final urgent position flat, mandatory empty/finished capacity may request an immediate refill before the ordinary configured-capacity service sweep. A cached selection may be used only within 120 seconds and only after fresh metadata, incentive and full-book validation of every replacement; economic-idle replacement remains on its normal 15-minute cadence |
| Portfolio / market exposure | `30 × / 9 × quote size`; $390 / $117 at size 13. The cap is unchanged by the 22-slot canary: 20 slots leave a 10-notional cushion and 22 leave an 8-notional (36%) cushion |
| Market loss stop | `4 × quote size`; $52 at size 13 |
| Account loss threshold | `8 × quote size`; $104 at size 13 for both realized-session and marked-equity sensors; an open-position marked breach is provisional until a breach-time position closes |
| Risk-day clock | America/New_York, DST-aware |
| Queue position study | Measurement only; never cancel/repost automatically |
| Adverse-fill/cliff study | Measurement/watchlist only pending sufficient data |
| Spread-scalp study | Authorized as isolated measurement only: strict full fills on both fixed-price sides, separate pure-volume and reward-slot cohorts, plus a bounded pure-volume fixed-GTC-to-settlement cohort; no orders or strategy gate |
| Inventory deadline | 900 seconds, with the separate pre-kickoff flatten rule |
| Taker-exit session count | `4 × quote size`; 52 at size 13. The independent `0.30 × quote size` execution-fee ceiling remains unchanged and may stop the session first |

The two account sensors protect different evidence and responses; their shared size-scaled threshold is not permission to remove either. The marked sensor blocks new entries immediately but, while inventory is open, waits for a breach-time position to close and a later adjusted balance instead of forcing a paper-loss liquidation. A confirmed result may stop with another position open; the realized sensor independently pauses new risk at the same threshold.

Twenty-two passed 30 continuous minutes of startup and selection bursts with zero failures/429s, sustained queue, safety deadline, stream loss or persistent account ambiguity. After ending that monitoring period, the operator explicitly chose to restart at 22 again; 22 is the current live setting while 20 remains the bare-launcher default. Any later capacity-safety breach still requires a graceful stop and positive flat proof before another start.

## Reporting and data choices

- Reward and risk days use Eastern Time.
- The seven-day reward table and hourly Discord post remain enabled in their
  reviewed read-only form.
- `score hours` in the operator-facing report means merged resting side-hours,
  not session model score-seconds.
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
- No removal of the 2-cent spread gate.
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

When the operator changes one of these choices, update this file and
[STATUS.md](STATUS.md) in the same task and record the evidence or implementation
handoff in the appropriate history file.
