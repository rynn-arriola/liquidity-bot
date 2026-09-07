# September 6–7 trading-loss diagnosis

Read-only investigation on September 7, 2026. No execution code, configuration,
orders or process state changed. Runner 770972 was observed alive at 14:16:55Z;
the last authenticated exposure evidence remains the 14:13:55Z acceptance
(39 orders, zero positions), not a claim about exposure at this later cutoff.

## Measured fills

A consistent read-only SQLite transaction replayed all non-reversed fills in
event/receipt/execution order through canonical `MatchedInventory`, retaining
pre-period cost basis and signed fees. Owned role comes from the PLACE intent's
`quote_observation_required`, not maker/taker liquidity. September 6 and 7
calendar-ET attribution sums exactly to canonical `realized_pnl(since=...)`:
-$127.6294. Latest included fill: September 7 14:16:52.308032Z.

| Calendar ET day | Net adverse price movement | Net fees after rebates | Recorded matched net |
|---|---:|---:|---:|
| September 6 | -$84.0322 | $7.4200 | -$91.4522 |
| September 7, through 10:16:52 ET | -$32.8472 | $3.3300 | -$36.1772 |

About 91.6% of this recorded loss is price movement, 8.4% net fees. Owned taker
reducers realized $79.0622 and $22.8420 of adverse price movement respectively;
maker reducers also lost $5.4510 and $9.7552. Gross profitable closing fills
offset only $3.5690 and $1.8000 against gross losing closes of $87.6012 and
$34.6472. Fill counts are not independent trades or position episodes.

Concrete evidence:

- September 6 20:00:52Z, `aec-itfwo-miczun-zoedol-2026-09-06`: 10 contracts
  with 78-cent LONG basis closed at 5-cent LONG-equivalent value, losing $7.30
  before $0.03 fee. The preceding exact-market action at 20:00:51.578747Z was
  `PLACE_IOC reason=hard_inventory_timeout side=offer price=0.05`.
- September 7 12:36:40Z, `aec-cs2-m33-masq-2026-09-07`: nine contracts,
  58-cent LONG basis to 35 cents, $2.07 price loss plus $0.12 fee. The preceding
  12:36:39.680037Z action was the same hard-inventory-timeout IOC path.
- September 7 13:51:49Z, `aec-cs2-dei-piv-2026-09-07`: nine contracts,
  85-cent LONG basis to 50 cents, $3.15 price loss plus $0.14 fee. The preceding
  13:51:48.579144Z action again explicitly records hard inventory timeout.

This establishes adverse inventory movement realized on closing, including
timeout exits. It does not prove shortening the timeout improves results,
that mandatory exits caused the preceding price move, or that a one-tick
entry offset caused these losses. Do not weaken exits or change the existing
0.10 floor, 900-second clock, leagues, size, capacity or loss policy from this
diagnosis. Entry/after-fill quality is a further measurement question.

## Account reconciliation is incomplete

Account reporting and fill replay are separate evidence sources. September 6
reported approximately -$104.2022 across September 6 00:01:46.645731 ET to
September 7 00:07:54.512370 ET, not an exact midnight calendar interval. Replay
over those exact endpoints is -$89.3022, leaving **-$14.9000 unattributed**.

The runner's cached account mark at September 7 14:18:55.403865Z is -$43.5736:
closed -$43.4062 plus open mark -$0.1674, with nontrading cash zero. Replay from
its durable 04:07:54.512370Z opening through the same cutoff is -$36.1772.
The difference is **-$7.3964 including the open mark**, or **-$7.2290 against
closed account P&L**. This is cached runner evidence, not a fresh independent
authenticated account snapshot.

Do not overwrite account P&L with the smaller fill totals, attribute the gaps
to fees, or label them settlements/manual trades without positive evidence.
The prior positively classified $100 nontrading transfer is excluded from
September 6 trading loss. Reward amounts are not assumed to offset these
losses: September 6 daily reward/net remains pending in the observed cache.

Remaining work is to reconcile those exact windows against authoritative
activity and durable fill/settlement/cash-flow evidence. The available fills
support the dominant mechanism, not a complete dollar-by-dollar account audit.

Local reproduction used ignored `.local-tools/sept_loss_attribution.py` under
native WSL Python with `ExecutionLedger.open_read_only`; no private ledger,
logs or report cache are committed. Source and tests are unchanged, so no new
runtime tests or restart are required for this documentation-only finding.
