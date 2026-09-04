# Scalable pending-entry-order policy — 2026-09-04

## Operator decision

The operator replaced the separate hard-coded overnight and loss-cap values
with one restart-scoped configuration. The goal is to reserve more of the
daily loss budget for later trading while retaining more reward opportunity
than the prior 20-order overnight and 95%-exit-only rules.

This is a risk-allocation decision, not a finding that time alone caused prior
losses and not a guarantee that the bot will trade through EOD. Fewer pending
entries can reduce fills, reward side-hours and spread opportunity; open
positions can still lose. HARD, exposure, stressed-exit, buying-power,
inventory-deadline, recovery and shutdown protections remain unchanged.

## Exact policy

`config/entry_order_policy.json` loads once at process start:

| Condition | Maximum pending flat-entry orders |
|---|---:|
| 00:00 inclusive–09:00 exclusive Eastern | 30 |
| 09:00 inclusive–00:00 exclusive Eastern | 40 |
| 50% of ET-day marked HARD used | 30 |
| 70% used | 25 |
| 80% used | 20 |
| 90% used | 15 |
| 95% used | 10 |

The effective ceiling is the smallest of the configured clock band, the
tightest durable loss tier reached that ET day, and two orders per configured
market slot. At size 10 / 32, HARD remains `$128`, so the loss boundaries are
`$64.00`, `$89.60`, `$102.40`, `$115.20` and `$121.60`. HARD at `$128`/100%
remains the sole money-loss stop.

The loss denominator therefore scales automatically with quote size and slot
capacity. Raw order values also cannot authorize more than physical two-sided
capacity: for example, an eight-slot construction clamps both clock bands and
every looser loss tier to 16.

Working positively owned entries in flat markets and unresolved flat PLACE
intents consume capacity; a matching working order and intent count once. Any
market with a nonzero position is excluded in full. Reducers, IOC exits,
recovery and shutdown are uncounted and unblocked. Overflow cancellation stays
one positively owned order per pass, prioritizing higher reward rank and
complete pairs. Unknown state never authorizes a cap-driven cancel. The final
mutation boundary independently rechecks the effective cap.

Configuration is deliberately not hot-reloaded. The loader accepts only the
documented keys, `America/New_York`, whole order counts from 0–64, a real
overnight interval, strictly increasing fractions inside 0–1 and loss caps
that never loosen as loss increases. Missing, unreadable, malformed or invalid
configuration refuses before secrets, database access or network activity.

## Stop and verification evidence

The operator requested stop-before-work and authorized push, exact deployment
and restart after verification. The graceful stop ended supervisor `2979414`
and runner `2979422`. Its final record reported `operator_sigterm`,
`flat=true`, zero orders sent to Polymarket and stop time
`2026-09-04T03:49:28.179179Z`. An independent authenticated read at
`03:50:00.787970Z` proved zero open orders and zero nonzero positions. The
stopped ledger separately proved zero working orders, unresolved intents,
`UNKNOWN_ABSENT` rows and nonzero positions.

Focused tests cover exact inclusive loss boundaries, EDT and EST clock
boundaries, naive-time refusal, physical-capacity clamping, strict schema and
monotonic validation, malformed-config refusal before secrets, durable
same-day ratcheting/restart, ET-day reset, clock/loss precedence, scheduler
wake-up, 30/40 overflow cancellation, priority/pair retention, unknown-state
refusal and reducer/mutation-boundary exclusions.

Two deliberate reversals proved the requested numbers. Changing overnight 30
to 31 failed all four EDT/EST boundary assertions; restoring 30 passed.
Changing the 70% tier from 25 to 26 failed both exact `$89.60`–`$102.39`
assertions; restoring 25 passed. The complete edited Windows checkout then
passed all 1,102 tests in 106.045 seconds under the production WSL virtual
environment.

## Permanent edge audit

- Success, empty, exact-cap, one-over-cap and physical-capacity-lower-than-
  policy shapes are covered; caps can never exceed two orders per slot.
- Exact 00:00/09:00 boundaries are DST-aware. A time-band transition wakes
  flat owners but cannot reset the durable loss ratchet.
- Negative loss clamps to zero; every configured fraction is finite and
  strictly increases; loss caps can only stay level or tighten.
- Unresolved creates reserve capacity and cannot be double-counted with their
  working order; a fill racing a cancel moves the market outside this ceiling.
- Position/reducer/IOC/recovery/shutdown paths retain their existing authority;
  unknown or unowned state cannot authorize cancellation or new entry.
- The policy adds no venue request, retry, stream, worker or database table.
  Runtime work is deterministic local comparison plus existing ledger reads.
- Rollback is the prior deployment commit and config together. A deployment
  must never mix new source with a missing or old-shaped policy file.

## Deployment and live acceptance

Reviewed commit `09a273e0fb1a2d284e080f504989366881000e1d` was pushed before
deployment. The WSL checkout fast-forwarded to that exact commit from a
verified Git bundle, then passed all 1,102 tests in 78.365 seconds under its
native virtual environment. `DEPLOYED_COMMIT` was set to the same hash with
mode 600 and the changed config/runtime worktree blob hashes matched HEAD.

A repeated authenticated start gate at `04:05:47.515078Z` again proved zero
open orders and zero nonzero positions. The stopped ledger independently held
zero working orders, unresolved intents, `UNKNOWN_ABSENT` rows and nonzero
positions. Persisted size 10 / 32 then started supervisor `2986050` and runner
`2986058`.

The first bounded selector scan used two of ten event pages and found 49 viable
markets. At `04:07:35.954478Z` the loaded runner printed
`time_cap=30 loss_cap=64 effective=30`; it then placed exactly 30 initial
entries. An authenticated `04:08:33Z` snapshot proved 30 open orders and zero
positions, with zero unresolved/unknown ledger state after confirmations.

A market WebSocket disconnected at `04:09:36Z`. The unchanged safety path
canceled the 30 quotes made from the old book, reconnected to a fresh book and
rebuilt them. The temporary low order count the operator saw was this safe
rebuild, not a two-order cap. One rebuilt entry filled; the runner submitted a
passive reducer and returned the account to flat. At `04:14:50.332461Z`, a
final authenticated snapshot proved exactly 30 open orders across 27 markets
and zero nonzero positions. The ledger matched with 30 working orders, zero
unresolved intents, zero `UNKNOWN_ABSENT` rows and zero positions.

The first startup window completed 386 attempts and 34 mutations with zero
failures/429s/cooldown. The recovery interval recorded one venue timeout while
canceling and rebuilding. The following clean 78.1-second post-recovery window
completed 144 attempts and two mutations with zero failures, zero 429s, zero
cooldown and maximum queue depth one. This accepts loaded overnight enforcement
and recovery interaction. The 09:00 band change and later loss tiers remain
natural acceptance paths; do not induce a loss or fill to exercise them.
