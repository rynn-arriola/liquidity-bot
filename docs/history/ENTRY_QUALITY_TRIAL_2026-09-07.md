# Authorized 70% entry-quality trial — September 7, 2026

## Decision and scope

After reviewing the preliminary reward tradeoff, the operator said to enable
the filter today and subsequently authorized Command 1. This supersedes the
earlier disabled-delivery decision, not any other rule. The trial uses
`enabled: true`, `minimum_retained_value: 0.70`, size 10 / 32, the same $90 HARD,
durable ET-day baseline/ratchet, time/loss order caps and 900-second exit clock.
The operator was told there is no automatic midnight disable; no scheduler,
additional trading rule, monitoring automation or automatic rollback is added.

Existing position reduction, shutdown, maintenance and recovery remain intact.
Only the prepared entry-only filter and actual-action estimate repair deploy.
The reward-aware scenario was unfavorable before replacements and substantially
pending; this live trial does not turn that uncertainty into a savings claim.
[Implementation](ENTRY_QUALITY_FILTER_2026-09-07.md) ·
[Tradeoff](ENTRY_QUALITY_REWARD_TRADEOFF_2026-09-07.md).

## Stop and positive reconciliation

- Stopped supervisor `770963`, then gracefully signaled runner `770972`.
  Its terminal record at 15:12:53.564199Z truthfully says `flat=false`,
  `operator_sigterm`: existing shutdown policy parked two passive exits rather
  than crossing far from kickoff. Another owner was flattened by normal shutdown.
- At 15:13:23Z the venue still had two orders/one position; at 15:14:34Z one
  order/one position. These failed the start gate and no runner was launched.
- By 15:15:53Z the venue independently proved zero orders/positions. Both exits
  filled naturally; no additional forced-close request was executed. Two local
  working rows remained because the old process had stopped before their fills.
- A stopped-runtime SQLite-online parity backup at 15:17:47.105361Z was verified
  before reconciliation: 288,722,944 bytes, SHA prefix `fb199255271f`.
- Ignored `.local-tools/reconcile_stopped_trial.py` verified no runner, current
  venue flatness and no unresolved/unknown state, then used existing executor
  GET-by-ID reconciliation for exactly the two owned rows. It added two new
  positively reported execution fills, applied returned trade states and fresh
  complete account snapshots through existing ledger methods. At 15:18:21.409500Z
  venue orders/positions and local working/unknown/unresolved state were zero.
  No venue mutation, manual SQL correction, baseline reset or guessed absence.

## Review and tests

Activation changes only the JSON boolean. The committed-policy regression now
expects the authorized enabled value; legacy lifecycle fixtures explicitly
select the off path, while dedicated entry-quality tests exercise enabled
selection, same-policy wiring, one-side refusal, owned bad-entry cancellation,
actual-action recheck, forged-shadow isolation and unchanged reducers/exits.

The 39-test entry-quality/commissioning slice passed. Reversing the config to
false made the activation regression fail; restored true before the complete
review-tree suite: **1,173 tests passed in 110.837 seconds**. Reuse the earlier
three implementation reversals and completed edge-case audit for unchanged
source. Final deployment diff review confirms no additional source, launcher,
report schedule, capital, order-cap, loss-baseline or exit-policy change.

Review includes disabled rollback, immutable startup loading, invalid config,
one/both/unknown sides, no viable candidates, 32-slot ceiling, positive flat and
ownership proof, unchanged maintenance/recovery/shutdown authority, actual
action price/quantity/fresh book and no additional venue request from the filter.
Next: push, reviewed native deployment/hash/full-suite verification, fresh
flat/clear start gate, normal explicit `auto 10 32` launcher and live acceptance.
