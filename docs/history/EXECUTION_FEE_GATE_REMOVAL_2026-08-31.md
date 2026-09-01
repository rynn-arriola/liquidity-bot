# Execution-fee gate removal — 2026-08-31

## Operator decision and evidence

At 23:55:38Z a natural hard-timeout exit moved the durable ET-day gross owned
taker-fee total from `$9.5300` to `$10.0800`, crossing the loaded size-10/24
reference of `$10`. The runner had zero API failures/429s and no ownership or
slot fault. Its fee gate nevertheless canceled every flat-market maker quote
as `account_new_risk_paused`, leaving the healthy 24-owner process order-free.

The operator decided to remove that gate and rely on the existing loss
protections. This is a policy change, not permission to erase fee evidence.

## Prepared behavior

- Durable positive fees on owned taker fills are still summed by ET day.
- The reference remains `$10 × (size / 10) × (slots / 24)` so telemetry scales
  with the selected quantity and breadth.
- Crossing it emits `[execution-fee-observation] ... action=observe_only` and
  one non-critical Discord notice.
- The observation is absent from `new_risk_allowed`; it cannot cancel/block a
  quote, pause/stop trading or alter any reducing path.
- The $96 realized and marked-equity account sensors, $40 per-market loss,
  $340 portfolio exposure, $90 market exposure, $8 early-cross refusal and 40
  terminal taker-exit count remain unchanged at size 10 / 24.

No venue call, database schema, API cadence, slot count, selection rule,
position clock, reward rule, P&L formula or capital reserve changes.

## Verification plan

Focused configuration and live-runner tests prove partial fills aggregate,
negative rebates do not offset the gross observation, same-day restart
reconstructs it, ET rollover resets the notice, startup calls it telemetry and
crossing it leaves `new_risk_allowed=true`. Deliberately restoring the fee
condition to entry eligibility fails that final assertion exactly.

Before deployment, run the complete native suite and the exact staged laptop
WSL suite, audit every current fee reference, then use flat/order-free proof.
Live acceptance must reconstruct the already-crossed `$10.0800` total, log
`action=observe_only`, place orders across the configured 24 owners and show no
fee-driven `account_new_risk_paused` cancellation. A restart without those
facts is not acceptance.
