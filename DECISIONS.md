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
| Quote size | 10 contracts; size 20 is measurement-only and not authorized |
| Concurrent market capacity | 20 slots |
| Entry horizon | Six hours before the modeled pregame boundary |
| League selection | No league filter |
| New-entry spread | Keep the 2-cent minimum; 1.5-cent retention hysteresis |
| Market loss stop | $40 |
| Account loss threshold | $80 realized-session sensor and $80 marked-equity emergency sensor |
| Risk-day clock | America/New_York, DST-aware |
| Queue position study | Measurement only; never cancel/repost automatically |
| Adverse-fill/cliff study | Measurement/watchlist only pending sufficient data |
| Inventory deadline | 900 seconds, with the separate pre-kickoff flatten rule |

The two $80 sensors protect against different evidence and responses; they are
one chosen dollar threshold, not permission to remove either safety sensor.

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

## Decisions explicitly not authorized

- No automatic same-price cancel/repost to move a nearly-front order backward
  in the queue.
- No size-20 live rollout yet.
- No removal of the 2-cent spread gate.
- No league-based reward-market filter.
- No inference that an absent venue order is canceled.
- No new droplet based solely on the transient August 26 location denial.

When the operator changes one of these choices, update this file and
[STATUS.md](STATUS.md) in the same task and record the evidence or implementation
handoff in the appropriate history file.
