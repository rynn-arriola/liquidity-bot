# Retained-value shadow and measurement retirement — 2026-08-30

## Decision

Collect a prospective, order-linked retained-value shadow at 60%, 70% and
80%. It is reporting only. It does not refuse, delay, reorder, cancel or resize
an order and it does not change selection. Collection reuses the building
quote observation and the most recent selector result, so it adds no REST read
or WebSocket.

Retire two live collectors that no longer justify their cost:

- the queue-depletion/almost-front public-trade socket and per-order interval
  writer are removed from the next runner version; their SQLite tables,
  report and source remain as historical evidence;
- the laptop's daily selection probe timer is disabled. It had failed on both
  August 29 and August 30 because the migrated script still tried to enter
  `/root/liquidity-bot`, so it produced no usable laptop sample.

The separate spread-scalp collector was already stopped. Its 1.35 GB database
and read-only reports remain preserved; it is no longer active work. Ordinary
hourly reward reporting, public changelog monitoring and nightly ledger backup
are operational controls, not strategy measurements, and remain active.

## Historical cohort audit

The correct realized-trade split is the mechanism headline:

| Exit path | Markets | Pairs | Net per pair |
|---|---:|---:|---:|
| No taker exit | 90 | 1,063.4 | -$0.0016 |
| At least one taker exit | 638 | 7,054.7 | -$0.0646 |

Fill-level canonical P&L attribution is `+$20.3872` building maker,
`-$0.1571` reducing maker and `-$477.6624` reducing taker. Crossing is the
loss mechanism.

The earlier kickoff table was not an entry-time table. It used the market's
first captured quote observation. A replay from actual inventory-opening fills
found 665 position episodes and did not reproduce a 30–60 minute sweet spot:

| Actual entry distance | Episodes | Taker exit | Net per pair | Covered loss |
|---|---:|---:|---:|---:|
| 0–30m | 308 | 73.7% | -$0.0376 | 30.7% |
| 30–60m | 104 | 76.9% | -$0.0514 | 12.0% |
| 60–120m | 64 | 89.1% | -$0.0690 | 10.6% |
| 120–240m | 104 | 96.2% | -$0.0965 | 25.6% |
| 240m+ | 85 | 78.8% | -$0.0744 | 21.1% |

This does not support a no-trade hour or a proximity gate.

The decision-time stressed exit does support prospective measurement. For 661
closed position episodes with an opening-fill observation, retained executable
value is:

`(entry stake + stressed exit P&L) / entry stake`

where bid stake is `quantity × price` and offer stake is
`quantity × (1 - price)`. Historical threshold results were:

| Minimum retained value | Flagged | Taker exits caught | Maker-only blocked | Flagged net/pair | Loss caught |
|---|---:|---:|---:|---:|---:|
| 50% | 92 (13.9%) | 75/529 (14.2%) | 17/132 (12.9%) | -$0.1129 | 25.7% |
| 60% | 106 (16.0%) | 88/529 (16.6%) | 18/132 (13.6%) | -$0.1041 | 27.9% |
| 70% | 131 (19.8%) | 110/529 (20.8%) | 21/132 (15.9%) | -$0.1003 | 34.0% |
| 80% | 185 (28.0%) | 160/529 (30.2%) | 25/132 (18.9%) | -$0.0932 | 45.2% |

The cohort is promising but not an authorization decision: it does not yet
measure the reward actually surrendered, whether a recent selector candidate existed,
or held-out performance under the current size-13/22-slot configuration.

## Prospective evidence contract

Every new building post-only PLACE observation records:

- stressed executable value and retained-value ratio;
- independent `WOULD_REFUSE` labels for 60%, 70% and 80%;
- the best viable market outside the active slate from the most recent already-
  fetched selector snapshot, its projected reward, expected net and reward
  delta versus the observed side;
- selector snapshot age and an explicit missing/stale/no-spare reason;
- whether a recent scan had no candidate and whether replacement availability
  was unknown because selection evidence was absent or stale.

The row is immutable and committed with the exact PLACE intent before the
venue mutation. A missing measurement cannot block an order. Selector evidence
older than the normal 900-second refresh interval is not claimed as a
replacement. The report must compare realized trading loss avoided with
projected and ultimately paid reward surrendered, then freeze a held-out
cohort before any policy proposal.

## Queue collector conclusion

The final read-only report covered 12,227 orders over 5.90 days, 12,158
post-acknowledgement baselines, 712 maker-filled orders with baselines and zero
trade drops/decode failures. It emitted 2,061 clean almost-front reset signals;
only 197 preceded a later fill, a 9.6% signal yield before accounting for the
cancel/repost dark time and lost queue priority. This is too noisy to justify
an automatic same-price reset and does not help decide the retained-value
entry gate. Historical data stays available; collection stops on the next
runner restart.

## Deployment state

Tracked implementation is reviewed locally but not yet deployed or loaded.
All 890 WSL tests pass. A deliberate reversal that made the 70% label suppress
the PLACE failed the permanent no-veto regression (`0 != 1` venue creates); the
restored measurement-only path passed. Migration, stale/unknown selector,
incomplete-exit, no-extra-read, immutable intent linkage and report cases pass.
The current runner was not stopped or restarted. The failed daily probe timer
is disabled and inactive; the live bot and dashboard PIDs were unchanged after
that reversible schedule change.
