# Active operator decisions

This file records choices made by the human operator. It is not a backlog and
not a list of AI recommendations. Code and the running command remain the
authority for exact enforced values; if they disagree with this page, stop and
show the operator the discrepancy.

## Decision rights

- The human operator chooses strategy behavior, risk limits, capital, quote
  size, slot count, deployment timing and stop/restart timing.
- AI agents investigate, explain, implement, test, review, document and carry
  out authorized operations. They should not assume the operator writes code.
- Explain proposed changes in plain language, including expected benefit,
  downside, effect on rewards/orders/losses and whether a restart is required.
- A measurement or watchlist idea is not permission to alter live trading.

## Live strategy and risk choices

| Choice | Decision in force |
|---|---|
| Production runtime | Production server; no local runner |
| Quote size | 10 contracts live; whole values 5-30 may be configured, but no non-10 size activates without an explicit safe restart decision |
| Concurrent market capacity | 20 slots |
| Entry horizon | Six hours before the modeled pregame boundary |
| League selection | No league filter |
| New-entry spread | Keep the 2-cent minimum; 1.5-cent retention hysteresis |
| Persistent idle slots | After 10 continuous minutes of named current-book economic refusal, a flat/order-free/ambiguity-free slot may be replaced on the normal 15-minute scan only when that scan supplies a viable replacement |
| Post-flat slot recovery | Keep the near-deadline exit safety pause. Once positive position evidence proves the final urgent position flat, mandatory empty/finished capacity may request an immediate refill before the ordinary 20-market service sweep. A cached selection may be used only within 120 seconds and only after fresh metadata, incentive and full-book validation of every replacement; economic-idle replacement remains on its normal 15-minute cadence |
| Portfolio exposure | `30 × quote size`: 20 slot-notionals plus a proportional 10-notional cushion; $300 at size 10 |
| Market loss stop | `4 × quote size`; $40 at size 10 |
| Account loss threshold | `8 × quote size`; $80 at size 10 for both realized-session and marked-equity sensors; an open-position marked breach is provisional until a breach-time position closes |
| Risk-day clock | America/New_York, DST-aware |
| Queue position study | Measurement only; never cancel/repost automatically |
| Adverse-fill/cliff study | Measurement/watchlist only pending sufficient data |
| Spread-scalp study | Authorized as isolated measurement only: strict full fills on both fixed-price sides, separate pure-volume and reward-slot cohorts, plus a bounded pure-volume fixed-GTC-to-settlement cohort; no orders or strategy gate |
| Inventory deadline | 900 seconds, with the separate pre-kickoff flatten rule |

The two account sensors protect against different evidence and responses; they
share one size-scaled threshold, not permission to remove either sensor.
The marked sensor blocks new entries immediately. When inventory is open, it
must not force a loss-realizing shutdown from paper movement alone: it waits
for a breach-time position to close and a later adjusted balance. A confirmed
A confirmed result may stop even while another position remains open. The
realized sensor independently pauses new risk at the same scaled threshold.

## Reporting and data choices

- Reward and risk days use Eastern Time.
- The seven-day reward table and hourly Discord post remain enabled in their
  reviewed read-only form.
- `score hours` in the operator-facing report means merged resting side-hours,
  not session model score-seconds.
- Pending rewards are displayed but never counted as paid or finalized net.
- “Run daily task” means the local-only backup utility described by the ignored
  workstation `AGENTS.local.md`. It is manual, never scheduled, never pushed, and
  must not stop or restart the bot.

## Multi-machine operating choice

- Work may be performed from the desktop or laptop, but GitHub `main` is the
  synchronization point between them; neither workstation is independently
  authoritative.
- Production remains the only live runtime. It is compared to
  `DEPLOYED_COMMIT` plus exact source/test hashes, not blindly to the newest
  documentation-only GitHub commit.
- “Run parity check” means run `scripts/check_parity.py --sync` on the current
  workstation. It automatically fetches and fast-forwards a clean behind-only
  checkout to GitHub `main`. Both workstation runs are required before claiming
  the desktop, laptop, GitHub and server are all current.
- Parity permission does not authorize overwriting dirty/diverged work,
  deploying trading code or restarting production. Those remain explicit
  review/operator decisions.
- Local databases, recovery archives, SSH keys and other ignored files are not
  repository parity. They remain machine-specific and must never be committed.

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

When the operator changes one of these choices, update this file and
[STATUS.md](STATUS.md) in the same task and record the evidence or implementation
handoff in the appropriate history file.
