# Bigger contracts or more orders? — research, September 26, 2026

The operator asked which is more profitable: a larger contract size or a higher order count. The operator added that capital is not a constraint, and that the loss stop can rise if profit rises. The source data is read-only: the ledger, the archived account payouts (snapshot 20:29Z, payout days fully posted through September 24) and `[window-risk]` logs.

## Size: 10 contracts (to September 21) versus 15 (from September 22)

**Per market we traded** (resting time joined to that market's payouts):

| | Size 10, Sep 15–21 | Size 15, Sep 22–24 |
|---|---|---|
| Markets / resting order-hours | 1,472 / 3,658 | 717 / 1,381 |
| Paid per order-hour | $0.316 | $0.231 |
| Paid per contract-hour | $0.032 | $0.015 |
| Trading P&L per order-hour | -$0.085 | -$0.062 |
| **Net per order-hour** | **$0.231** | **$0.170** |

**Same reward program at both sizes** (paid per order-hour):

| Program | Size 10 | Size 15 |
|---|---|---|
| Table tennis | 0.356 | 0.321 |
| ITF | 0.109 | 0.073 |
| Esports | 0.315 | 0.204 |

**Model.** The bot's own share model, over 50,570 recent quotes, puts reward against trading risk as follows:

| Size | Reward | Trading risk |
|---|---|---|
| 20 | ×1.20 | ×1.33 |
| 25 | ×1.36 | ×1.67 |
| 30 | ×1.51 | ×2.00 |

The bigger our order, the bigger our share of the pool, but with diminishing returns.

**Reading.**
- Raising the size by 50% did not raise payment, even within the same programs. That comparison is confounded by an account-wide payout decline in the same days.
- Even the model says reward grows more slowly than risk.
- A bigger size also reaches the loss ratchet faster, which cuts the order count.

## Order count: how often it is capped

**Time at each entry cap (last 7 days, 167 h):**

| Cap | Hours |
|---|---|
| 30 | 87.7 |
| 25 | 39.9 |
| 20 | 18.2 |
| 15 | 18.1 |
| 10 | 3.1 |

- The loss ratchet held the cap below 30 for **47% of the time**. That costs about 715 order-hours a week (about 100 a day).
- Even at 30, the cap binds about 79% of the time (RESTING_EFFICIENCY).
- Supply exists: the first funnel line showed 46–53 viable markets (92–106 sides).

**Windows.** No six-hour window tripped the $30 hard stop in 31 windows. Settled window results ranged from -$24.63 to +$9.60, mostly -$10 to -$17.

**Marginal orders earn less.** Net per resting order-hour by priority grade (MARKET_QUALITY_READOUT) is:
- S: $0.26;
- A: $0.20;
- B: $0.08;
- C: $0.07;
- D: $0.09.

The ranker's first run found 31 positive untraded markets.

## Estimate (rough, before trading losses on the added orders)

| Change | Extra order-hours/day | Extra net/day |
|---|---|---|
| Stop the ratchet from cutting the cap (e.g. stop $30 → $45 with the same fractions) | about 100 | about $8–17 |
| Cap 30 → 40 when supply allows | up to about 190 | about $13–17 |
| Size 15 → 30 | 0 | model best case about +$36; actual size-10→15 evidence negative; trading risk doubles |

## Recommendation

- Grow the **order count first**, keeping size 15:
  - raise the pending-entry cap 30 → 40;
  - raise the window stop to about $45 while keeping the same tier fractions (caps at $9/$18/$27/$36 loss).
- Judge the change with the daily calibration report and posted payouts over 3–5 days.
- Test size separately afterwards, as a controlled trial (for example size 20 on alternate days), so the result is not confounded again.
- Every change here is a risk decision for the operator and needs a restart.
