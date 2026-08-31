# Current trading-loss attribution — 2026-08-31

## Question and evidence boundary

The operator asked what produced most of the trading loss in the current
August 29–31 interval.  This is a ledger-only analysis: it made no venue call
and changed no order, position or strategy setting.

The verified laptop SQLite backup contained active fills through
`2026-08-31T17:02:31Z`.  Replaying every earlier active fill in canonical
LONG/YES price-frame order established each market's opening basis, then
attributed only realizations and fees whose fill landed on the three ET days.
The result exactly matched `ExecutionLedger.realized_pnl()` from the August 29
ET boundary.

## Result

Canonical realized trading P&L was `-$234.4553`:

| Component | P&L | Share of loss |
|---|---:|---:|
| Adverse paired price movement | `-$215.6253` | 92.0% |
| Observed fill fees net of rebates | `-$18.8300` | 8.0% |

Reducing taker fills realized `-$209.9547` of the adverse component, 97.4% of
its net loss.  That is where the loss became final: the bot held one outcome,
the book moved away, and the reducing side completed at a worse combined cost.
It is not evidence that exits should be refused; refusing a required reducer
can carry the position into a still larger pre-kickoff or settlement loss.

The largest closing-price bucket was economic cost 0.85–1.00 at `-$88.01`.
That remains an outcome label, not an entry-selection cause.  In contrast,
inventory whose prior average cost was below 0.15 produced only `-$6.6940`,
3.1% of adverse net loss.  Prior average entry costs from 0.30–0.70 produced
`-$114.4582`, 53.1%.  This independently rejects using 0.15 as the immediate
lever.

League labels describe concentration, not a league ban:

| League | Canonical net P&L |
|---|---:|
| Setka UA | `-$90.0720` |
| ITF men | `-$69.1709` |
| ITF women | `-$27.6591` |

Setka UA and ITF men together were 67.9% of the canonical loss.  The active
operator decision remains no league filter because this table does not price
the paid reward and replacement supply those leagues also provide.

The balanced-pair completion report put 84.7% of its `-$197.53` sample loss in
markets taking more than 15 minutes to complete.  That short interval must not
override the older clean 2,200-pair result: completion P&L was U-shaped and
900–1,800 seconds was its best band.  Therefore the 900-second inventory
deadline remains unchanged; a proposed 600-second study was withdrawn after
the larger evidence was re-read.

## Decision and operational action

The operator accepted the recommendation to keep the 0.10 entry floor, no
league filter, size 13 / 22 slots and the 900-second inventory clock.  The
60/70/80 retained-value refusal labels remain prospective and measurement-only.
Seventy percent is a provisional candidate, not authorized trading behavior;
it requires three complete ET days and must beat the paid reward and dark-slot
opportunity it would surrender.

The already-reviewed sequential hard-exit repair was activated separately at
a safe flat boundary.  The old runner first closed its position naturally,
graceful shutdown proved `flat: true`, and an independent authenticated
`17:45:43Z` snapshot proved zero open orders and zero positions.  Deployment
marker `31c2574` was advanced before starting exactly one size-13/22-slot
supervisor/runner pair.  At `17:50:44Z` PID 937743 had 22 bound markets, 38
working orders across 19 markets, zero positions, zero unresolved intents and
zero unknown orders.  Its startup API window had zero failures and zero 429s.
Natural two-order hard-exit acceptance remains pending and must not be induced.
