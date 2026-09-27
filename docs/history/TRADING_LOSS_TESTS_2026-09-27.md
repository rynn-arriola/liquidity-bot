# Trading-loss tests — September 27, 2026

**Operator request:** "How do you think we can avoid trading losses based on our data without that much effect on resting hours and rewards", then "look more into this and test it".

Everything here is read-only: ledger, logs (`[reducer-decision]` book snapshots, Sep 19–27) and quote observations. There was no bot change.

## Where the loss is (Sep 17–27, 4,068 closed positions)

| Exit | Positions | Trading |
|---|---:|---:|
| Passive reducer | 2,686 | **+$209.88** |
| Forced IOC | 1,382 | **−$647.91** |
| Total | 4,068 | −$438.03 |

- 102 of the 104 IOC actions in the current logs are the 15-minute `hard_inventory_timeout`.
- **Fees from our fills:** maker −$0.0030 per contract (a rebate), taker +$0.0152 per contract.
- **The price does not typically jump against us after an entry fill.** The mid moves against us by a median of 0¢ at 60 s and 300 s (mean +0.5–1.3¢), for forced and passive positions alike.
- **Mechanism:** in thin books nobody takes our exit within 15 minutes. We then cross the spread and pay the taker fee.

## Test 1 — exit timing and stepping (2,960 replayable positions)

| Rule | Forced | Passive | Total |
|---|---:|---:|---:|
| Actual | −$530.97 | +$87.72 | **−$443.25** |
| Cross at 5 min | −$487.63 | −$95.95 | −$583.58 |
| Cross at 10 min | −$515.30 | +$20.07 | −$495.23 |
| Passive step 1 tick at 5 min, 2 at 10 min | −$523.70 | +$91.73 | −$431.97 |

- The step would have filled only 29 of 1,087 forced positions. Filled means a later book showed a counterparty at the stepped price.
- **Verdict: do not build exit-timing changes.** Crossing earlier converts passive winners into losers, and stepping barely helps.

## Test 2 — entry filters (Sep 19–27, 8 days)

- **Reward estimate:** model reward × the measured 1.77 paid/predicted factor (old pools), scaled to about 0.25× for the halved pools. That scale is an assumption taken from Reward Pulse Sep 25–26 (0.16–0.23×).
- **"Rest cut"** is the share of our resting order-hours that were in skipped setups. The freed slots would rest elsewhere, so the real cut is smaller.

| Skip entries when… | Loss avoided/day | Rest cut | Reward cut/day now | Net/day |
|---|---:|---:|---:|---:|
| F1: 1¢ spread and <50 ahead | $30.58 | 20.2% | ~$11.76 | **+$18.82** |
| F2: F1 only in high-forced leagues* | $13.05 | 8.5% | ~$4.24 | +$8.80 |
| F3: high-forced leagues, 1¢ spread | $19.26 | 26.3% | ~$6.54 | +$12.72 |
| F4: high-forced leagues, any spread | $27.78 | 41.2% | ~$12.19 | +$15.59 |
| F5: 1¢ spread and 2–6 h to kickoff | $18.77 | 39.4% | ~$9.63 | +$9.13 |

\*High-forced leagues: ITF M/W, WTT, CS2, Dota 2, darts, Valorant, Modus, WTA, ATP doubles (UTR/R6 are already excluded). Their forced-exit rates are 55–80%, against 25–29% for Setka.

**Per order-hour** (old pools):
- 1¢ thin books: trading −$0.251/h, reward $0.385/h (≈ $0.096/h now).
- Other books: trading −$0.029/h, reward $0.174/h.

## Not testable from history

- **Resting one tick behind the best price in thin 1¢ books.** It could keep most rest hours with far fewer fills, but all 69,600 entry quotes since Sep 19 were placed at the best price.
- **A longer inventory limit.** The book after a forced exit was not recorded.

Both need a live trial.

## Caveats

- In-sample: the filters are chosen on the same data they are scored on.
- Reward-now scaling is approximate.
- These are associations; they do not prove the filters cause the improvement.
