# Findings — liquidity-bot

Empirically established facts, with the measurement that produced each one.
Nothing here is a plan or an intention. If a number appears without a source,
it does not belong in this file.

Companion to `docs/SAFETY_INVARIANTS.md` (rules the code must obey) and
`docs/SCALE_REVIEW.md` (what is and is not proven). This file is the evidence
those two rest on.

---

## Current status — 2026-08-14

**Bot: stopped.** Account verified flat at the venue (0 open orders, 0 nonzero
positions). Deployed commit recorded in `/root/liquidity-bot/DEPLOYED_COMMIT`.

**Multi-market support is complete**, all 9 steps, 281 tests passing.
`max_markets` is now **8** — the measured private-WebSocket ceiling, see
§7. Quote size is unchanged at one contract: breadth expanded, per-market
exposure did not. Not yet committed, deployed or run.

**The open question is not correctness, it is whether the opportunity exists.**
The machinery works. A backtest over 24 hours of real books authorized one
quote worth about three cents. See §6.

---

## 1. Fee and rebate structure

Verified against live commission data on our own fills, 2026-08-13.

```
taker fee   = 0.06   x qty x p x (1-p)      charged
maker rebate= 0.0125 x qty x p x (1-p)      credited (negative commission)
```

**Rounding is asymmetric against small size.** At one contract, p=0.81:

```
taker fee    $0.0092  ->  rounds UP   to $0.01
maker rebate $0.0019  ->  rounds DOWN to $0.00
```

Measured directly: `passiveExecution` commission was **0.0000 on all four**
maker fills on 2026-08-13. Per-contract economics are roughly **37% worse at
size 1** than at size 10. maker-lab only ever observed the rebate because it
filled 4–537 contracts at a time.

The rebate is maximised at p = 0.50: `p(1-p)` is 0.25 there versus 0.154 at
p = 0.81, so mid-priced markets pay **62% more rebate per contract**.

## 2. Exit behaviour

### The passive-vs-crossed comparison is a selection artifact

maker-lab live data, 2026-08-11/12, 101 exits:

```
kind      n     pnl        fees      winners   losers
passive  61   +$20.16     $0.00        60         1
cross    40   -$34.81     $6.05         2        38
settle   16   -$88.20        -          2        14
```

Per event that reads as **passive +$0.33 vs crossed -$0.87** — and it is
**not a comparison of exit policies.** maker-lab ran
`FLATTEN_NEVER_AT_A_LOSS = True` (`config.py:396`), so its reducing quote was
floored at cost basis. A floored quote can only fill when the market rises to
meet it. Winners therefore exit passively; losers cannot fill at all and leave
only by crossing or by settling.

maker-lab documented this themselves, in `live_runner.py:1609`: the settlement
pool is *"79% losers BY CONSTRUCTION."* The 60-1 and 2-38 splits above are that
construction, measured. Any strategy with a basis floor produces this table
regardless of whether passive exits are actually better.

**So the floor does not avoid losses, it defers and concentrates them.** In
this session it routed -$88.20 into settlement — 86% of the session loss —
while booking the winners as passive-exit profit. maker-lab measured the same
effect at larger scale: 234 positions settled for -$443.88 where crossing at
T-15 would have cost -$64.18, **a +$379.70 swing with 78% of positions
improving** (`live_runner.py:1604`).

**Conclusion: the reducing quote must rest at the touch, with no basis floor.**
liquidity-bot already does this. This is not the code disagreeing with the
safety rule — it is the rule carrying maker-lab's most expensive defect.
See `docs/SAFETY_INVARIANTS.md` invariant 5.

### The 30-second window captured almost none of them

112 position episodes reconstructed from fills and exits:

```
passive latency  p25 13.3s   median 100s   p75 653s   p90 1367s
```

Cumulative share of passive exits captured by timeout:

```
  30s -> 26%      300s -> 31%      900s -> 46%     1800s -> 50%
```

The median passive exit took **100 seconds** — more than three times the
window that was in place. `max_inventory_age_seconds` was raised 30 -> 900 on
this evidence. Caveat: these latencies are times-to-fill for quotes that were
basis-floored, so they describe how long a *winning* position took to come
back. An unfloored quote at the touch should fill faster, not slower, which
makes 900s a conservative bound rather than an optimistic one.

### Not crossing is also wrong

maker-lab set `FORCE_EXIT_BEFORE_START_MIN = 0` because holding measured
~3.13c/contract cheaper than crossing. The residue then reached settlement:

```
unpaired positions settling  -$88.20   (86% of that session's loss)
16 settlements, every one payout = 0.0
```

Neither extreme is right. Current policy: wait 900s for the free passive exit,
then flatten unconditionally 300s before kickoff. Settlement is never allowed.

### liquidity-bot's own exits, 2026-08-13

Four round trips, **every one a taker exit at the 30-second deadline**, passive
0-for-4. Holding times 28s, 31s, 31s, 30s — the timer won every time.

```
entry maker 0.81  ->  exit taker 0.81  ->  realized -$0.01 (the commission)
```

Three of four were price-flat: the loss was purely the crossing fee. The
exit was never a bad price, it was an unnecessary crossing.

### A thin exit book used to end the whole session

Measured 2026-08-14 against `cross_exit`: at one contract with the 0.5 stress
haircut, the hard exit cannot complete whenever **exit-side depth is under
2.0** contracts.

```
exit-side depth   1.0  -> incomplete
                  1.5  -> incomplete
                  2.0  -> completes
```

That is not a corner case. The universe sample in section 5 contains live books
with exit-side depth of exactly `1.0`. Entry is gated on exit depth existing,
but depth can vanish during the 900-second hold.

It used to be terminal: `hard_exit_insufficient_stressed_depth` returned
`critical:`, which exits 4, and the supervisor does **not** restart exit 4. One
thin book on one market ended the session and left the process down until an
operator noticed -- while the other seven markets were healthy.

Now the inventory clock defers instead (`hard_exit_deferred_thin_book`,
recoverable). Refusing to cross into a thin book is still correct; ending the
session over it was not. The pre-kickoff flatten at T-300s remains the real
deadline and still re-prices against displayed depth.

More generally, **every condition that describes one market's inventory or exit
now retires that market rather than stopping the session**: the hard-exit
family, the inventory clock and basis checks, and the exit-confirmation
timeout. A retired market keeps its streams and reconciler so its exit is still
worked, but never quotes again -- not even after reaching flat, and not through
a later scan. Seven healthy markets no longer pay for one bad book.

The bound matters as much as the mechanism. Retirement resets the exit budget,
so a market whose exit can never complete would be replanned forever, including
after kickoff when nothing can be done. Ten failed rounds abandons it, shutdown
makes the final attempt, and more distinct retirements than `max_markets` is
treated as systemic and does stop the session.

## 3. Spread regime

maker-lab markout at 900s, by the spread the fill landed on:

```
under 2c   n=856   -0.00868/contract   <- the only losing regime
2-4c       n=403   +0.00530
4-7c       n=267   +0.01893
7-12c      n=149   +0.00399
12-25c     n=110   +0.05841            <- best
over 25c    n=33   +0.04561
```

`minimum_entry_spread = 0.02` rests on this. It is the best-sampled number in
either project. It is also expensive: it rejects **88% of market-minutes** (see
§6). Wide books are the best fills — do not add a maximum spread.

**Markout is positive at every horizon** (+0.0041 to +0.0078/contract, n=168–197
at -1s/60s/300s/900s). Fills are not adversely selected. This does *not* mean
spread capture is guaranteed revenue: maker-lab's paired markets still netted
**-$15.62**, which is why `spread_capture` stays 0.

## 4. Reward mechanics

Score is `discountFactor ** ticks_from_best x size`, accumulated outward until
`targetSize` is reached. **Spread does not enter the formula** on this venue —
the quadratic-in-spread concern in maker-lab's notes came from the `.com`
scheme and does not apply here.

Reward pays for **resting**, not trading. maker-lab measured 37 of 153 minutes
with nothing on the book — **24.2% of the clock scoring zero**.

Observed programme parameters, 2026-08-13:

```
setka*   pool  $35   df 0.50   target  5,000
itf*     pool  $30   df 0.35   target  7,500
dota2    pool $100   df 0.35   target 15,000
atp      pool $200   df 0.30   target 15,000
```

Period pools on one market: `day_of` $200, `early` $200, `live` $1,600. **The
live period carries 8x the pool** and is the one period the pregame design
forbids.

Only `day_of` can be projected: other periods have no reliable per-market
start/end clock, so the runner can only quote in the **6 hours before start**.

The venue's $1 minimum payout is an **aggregate settlement threshold**, not a
per-market entry requirement. Applying it per side per market was wrong and
was removed.

## 5. Universe shape

Measured 2026-08-13:

```
active liquidityProgram markets      13,707
with a day_of period                  7,443
period mix   live 8,488 | day_of 7,443 | early 4,364
             daily_event 4,474 | tournament_with_cutoff 2,576
```

**Those counts are the whole programme, not this bot's addressable universe.**
Quoting the 13,707 on its own is misleading. Measured 2026-08-14T05:0xZ, the
funnel to what the runner can actually act on:

```
catalog rows                    17,254
active and not closed           17,254
supported moneyline              1,037     -94%  most rewards are spreads/totals/props
still pregame                    1,004
  starting within  6h              218     -78%  only day_of has a projectable clock
  starting within 12h              471
  starting within 24h              659
rewarded (of the 218)              199
after the 2c catalog spread filter  33     -83%  matches the backtest's 88% rejection
```

**The addressable universe is roughly 200 markets at any moment, and 33 after
the spread filter.** The +6-12h bucket held 253, so this is not a dead-of-night
trough: the count stays near this all day as games roll forward. Widening the
six-hour window is not a hidden win either -- it would mean quoting periods
whose reward cannot be projected.

Rewarded pregame **moneylines** do exist — setka, itf, atp, wta, cs2, lol,
dota2 — the same leagues `fill_hazard.py` is calibrated on. An earlier sample
suggesting otherwise (40/40 futures) was biased by pagination order.

Books are **wildly one-sided**: `bid 23,388 / ask 1.0`, `bid 31,168 / ask 17`.
Since the gate scores each side independently, a thin side on a deep book is a
real opportunity — but the *opposite* side is where you must exit, so a thin
side usually means a hopeless passive exit.

Discovery cost, measured: per-slug metadata for 7,443 markets is **31 minutes**
at 4 req/s. The bulk catalog join is ~39 requests, about 10 seconds.

## 6. Backtest — liquidity-bot's gate over maker-lab's books

Replay of the exact live gate over 2026-08-11/12, 9,306 in-window
market-minutes across 712 markets. Programme parameters assumed from §4
(historical incentive rows are a single later snapshot), everything else
measured.

```
market-minutes sampled          9,306
  spread >= 2c                  1,077   (11.6%)
  reward-eligible sides           691
  ECONOMICALLY VIABLE               1   (0.14%)

best gate_ratio    1.2389        median   0.0034
```

**One authorized quote in 24 hours.** It ran 749 seconds on an ATP market
($200 pool, 20 contracts at the touch), never filled, and accrued **$0.032**.

Rejection causes: `spread_below_2c` 8,229, `target_not_met` 1,463,
`reward_below_weighted_exit_cost` 690.

`target_not_met` is a trap worth naming: a book too thin to reach `targetSize`
earns **nothing at all**, so "thinner is better" has a floor. Too deep destroys
your share; too thin and the programme never activates.

Authorized uptime: **749s of 86,400 = 0.87%.** The reward *rate* was fine —
$0.154/hour, ~$3.70/day if authorized continuously. Uptime is the constraint.

### Quote size

Sweep over the same books:

```
size    authorized market-minutes
   1                            1
   2                            0
   5                            0
  10                            0
  25                            0
```

But on a book measured directly, **reward scales linearly with size** (25x size
-> 24.4x reward) and unwind scales linearly too, so `gate_ratio` is nearly
size-invariant (0.0310 -> 0.0305 across 25x). Size changes **how much you earn**
on a qualifying book, not **whether** it qualifies. The single qualifying book
passed at 1.24, too thin a margin to survive any increase.

## 6b. The gate, not the search, is what rejects everything

First live census, 2026-08-14T04:57Z, read-only over the whole universe:

```
executable 212 | rewarded 199 | candidate books 33 | viable 0
coverage_complete: true          best_gate_ratio: 0.392
```

**Only 33 markets are candidates at all.** The `day_of` period filter and the
catalog spread filter remove the rest, so the runner's 25-book window is not
leaving a large unexamined pool behind it -- the census fetched all 33 and
found nothing viable. Widening the search is not the lever.

Where the rejections actually land, per side:

```
projected_reward_does_not_cover_probability_weighted_exit_cost   62
spread_below_entry_minimum                                        2
target_not_met                                                    2
```

**94% fail the economic gate itself.** The best side anywhere in the universe
scored `gate_ratio` 0.392 against the 1.0 needed, so nothing was close.

This puts the weight on open question 3. `reward_projection_haircut` 0.5 and
`reward_cost_safety_multiple` 2 multiply to a **4x** margin, and neither number
has any evidence behind it. At 0.392 the best market clears comfortably without
that 4x and clears nothing with it. Before changing either, sample `gate_ratio`
across many passes and measure how often a market would clear at 3x, 2x and 1x
-- the census does this read-only and at no risk.

Touch depth across the same sample: median 141 contracts, minimum 0.02. Depth
is not the scarce thing; the margin is.

## 6c. First stable multi-market run, 2026-08-14 05:15-10:58Z

**5h43m on a single process, no restarts.** Every prior attempt died in 22-27
seconds.

```
PLACE 1,817 | CANCEL 1,817 | all CANCELED | unresolved 0
fills 0 | positions 0 | markets quoted 16 | retirements 0
```

The place -> cancel -> reconcile cycle that deadlocked the ledger on its very
first attempt now completes 1,817 times with nothing stuck. That question is
closed.

### Reward at one contract is arithmetically negligible

Measured programme parameters on the markets actually quoted:

```
day_of   pool $35   discount 0.5   targetSize 5,000
1 contract at the touch = 0.020% of target = $0.007 for the whole 6h period
```

The $1 payout floor is **aggregate across all markets and it accumulates**, so
the number that matters is the accrual rate, not any single market. Eight slots
running continuously keep covering new six-hour windows as games approach:

```
$0.007 per market per 6h period  =  $0.001167 per market-hour
8 slots x 24h                    =  192 market-hours/day
                                 =  $0.224/day  ->  $1 every 4-5 days
```

Venue earnings after 5.7 hours: **$0.00**, consistent with the arithmetic
rather than a defect.

Two things make $0.224/day a **ceiling, not an expectation**. It assumes 100%
resting uptime, and this run had `viable=0` for long stretches plus 1,817
place/cancel cycles. It also assumes a clean 1/5,000 score share: score
accumulates outward from the touch until `targetSize` is reached, so on a deep
book the target is filled by orders at or inside our price and we earn nothing
at all for that period.

**This is the scale problem, not a bug.** The machinery works; one contract
earns on the order of a dollar every few days at best. Note that section 6 measured `gate_ratio` as nearly size-invariant
(0.0310 -> 0.0305 across 25x), so size changes what a qualifying book pays
without changing whether it qualifies.

### A cancelled order is briefly unreadable

246 `GET /v1/order/{id}` 404s in 5.7 hours. Every id that 404'd returned a
proper terminal state when retried later, so the window is eventual
consistency, not absence.

It was expensive. The generic retry in `_call` covers only HTTP 429, so a 404
left the CANCEL intent unresolved, `risk_state_known()` went false, and the
runner treated it as `execution_state_unknown`: **539 strategy-state
recoveries, each quarantining every working order and cancelling resting
quotes.** Reward pays for resting time, so this churn attacked the only thing
that earns. `retrieve` now waits the window out, bounded at four attempts --
which assumes nothing about the order's state, unlike reading absence as
cancellation.

## 6d. Rewards are real and material -- correcting section 6c

Queried account-wide from the venue 2026-08-14 (`GET /v1/incentives/earnings`,
no market filter). **This account has earned $70.92 in liquidity rewards.**

```
PAID $32.06 | PENDING $38.80 | SKIPPED $0.06

2026-07-12   $0.01
2026-07-16   $0.05
2026-08-10  $32.06
2026-08-11   $5.39
2026-08-12  $33.41
```

166 distinct markets earned. Best single markets: **$5.38, $4.85, $4.02,
$3.22, $2.95**.

**Section 6c's arithmetic was right and its conclusion was wrong.** $0.007 per
market per period is correct *for one contract*, and I presented it as the
strategy's ceiling while this same account had already earned $33 in a day. The
error was only ever querying earnings per market for the markets bound right
now, never account-wide. Always query account-wide.

Three differences compound, and size is the smallest of them:

```
                maker-lab (earning)      liquidity-bot now
quote size      ~10-25 contracts         1
markets         166                      capped at 8
period          included live ($1,600)   pregame only (day_of, $200)
```

Roughly 15x x 20x x 8x. **Breadth and period dominate size.**

### The failure was settlement, not the reward mechanism

2026-08-12: rewards **+$33.41**, session net about **-$102**, of which
**-$88.20 was settlement** (section 2). Rewards were never the problem. The
residue reaching settlement was, and the 300-second pre-kickoff flatten
(invariant 6) is the fix for exactly that leak.

That reframes the open question. It is not "can this strategy earn" -- measured
yes, $32-33 on a good day. It is whether it can earn without giving it back at
settlement, which has never been tested with the flatten in place.

### Live versus pregame is UNRESOLVED, and fills cannot resolve it

The `live` period carries the 8x pool and the pregame design forbids it
(section 4), so this split decides whether scaling the current design is worth
doing. **The venue does not expose it.** `GET /v1/incentives/earnings` accepts
only `start_date`, `end_date`, `market_slug`, `program_type` and returns only
`reward`, `programType`, `marketSlug`, `date`, `status` -- confirmed against
the published reference on 2026-08-14. There is no period field to fetch.

An earlier note here claimed "two thirds pregame" from maker-lab's aggregate
fill timing. **That was wrong and is retracted.** Attributing all 166 earning
markets by fill timing:

```
                                              amount   share
no fills at all (pure resting)                $34.68     49%
fills only AFTER game start (live)            $28.40     40%
fills only BEFORE game start (pregame)         $3.42      5%
fills on both sides                            $4.36      6%
                                              ------
                                              $70.86
```

**Fills are a bad proxy for reward attribution**: half the money came from
markets that never filled, because reward pays for resting, not trading. The
aggregate pregame share was high only because pregame fills clustered on
markets that earned little; the largest earners ($5.38, $4.85, $4.02, $3.22)
had *zero* pregame fills.

What can be said: at most 5-11% is clearly pregame, 40% is clearly
live-associated, and 49% is unattributable by this method. That leans against
a pregame-only executor reaching these numbers, and it does not settle it.

Settling it needs resting time per period, which neither the venue nor
maker-lab's schema records. The practical answer is to run the pregame
executor at size and measure what it earns.

Raw data: `data/venue_reward_earnings_2026-08-14.csv`, 168 rows, the full
per-market per-day breakdown behind every number above.

### Order of work this implies

1. Fix the place/cancel churn. Reward pays for resting time and the runner is
   currently cancelling its own quotes about twice a second, so any size
   increase multiplies risk without multiplying reward.
2. Breadth before size. Breadth is the larger lever (20x against 15x) and the
   safer one: more markets at one contract concentrates no more risk per
   market. Connections are independent and each carries its own
   10-subscription quota (section 7), so 16 markets is two connections.
3. Size last, bounded by capital. Exposure is roughly `size x markets` dollars
   at two sides near $0.50. Against sub-$100 capital that is size 6 at eight
   markets, or size 3 at sixteen. Breadth and size trade against each other.

## 6e. The place/cancel churn: our own quote makes the side uneconomic

1,817 place/cancel cycles in 5.7 hours, all at the same price, roughly two per
second -- the loop tick. Reward pays for resting time, so this attacked the
only thing that earns. Diagnosed 2026-08-14 by logging the plan action, its
reason, and the per-side economics; none of it was recoverable from the ledger,
which records that an order was cancelled but never why.

The cancel reason is **`side_no_longer_desired`**: the side leaves
`desired_sides` the instant our order becomes visible, and re-qualifies about
250ms later once it is gone.

```
CANCEL          side_no_longer_desired      working=1
PLACE_POST_ONLY reward_positive_candidate   price=0.74  working=0
CANCEL          side_no_longer_desired      working=1
PLACE_POST_ONLY reward_positive_candidate   price=0.74  working=0
```

The mechanism is in `rewards.py`:

```python
if not order_already_resting:
    depth[order_price] = depth.get(order_price, 0.0) + size
```

Eligibility depends on cumulative depth reaching `targetSize`. Flat, our size
is added; resting, it is not, because it is assumed already in the streamed
book. **So our own contract decides eligibility whenever the book sits on that
boundary**, and the side oscillates once per pass.

Measured margin at the moment of a place: `net={'offer': 0.002705}` -- three
tenths of a cent. At that thinness any asymmetry between the flat and resting
calculations flips the sign, so this is not a rare edge.

### Fixed and confirmed live

`rewards.py` now counts our size in both cases. The public book does contain a
resting order of ours, so skipping it avoids double-counting in principle --
but the book is only eventually consistent, and the window before it catches up
is exactly the window the runner evaluates in, leaving depth short by our own
size. Counting it always makes "worth placing?" and "worth keeping?" answer the
same question. The residual error is over-counting one contract against a
targetSize in the thousands, which biases toward leaving a quote resting: the
safe direction, because resting is what earns.

Measured before and after on the same runner:

```
before   place -> cancel -> place at the loop tick, ~2 per second
after    a quote rested 4 minutes (12:19:27 -> 12:23:25) before any cancel
```

and the cancel that ended it was legitimate -- the bid's blocker had genuinely
become `projected_reward_does_not_cover_probability_weighted_exit_cost`, the
economics had moved, rather than the eligibility flipping on our own presence.

The reproduction lives in `test_eligibility_does_not_flip_on_our_own_resting_order`:
nine resting against a target of ten, our one contract deciding it.

## 6f. First fill, 2026-08-14T11:48:39Z

```
SELL 0.5200 @ 0.7400   fee 0.0000 (maker)   intent ORDER_INTENT_BUY_SHORT
position -0.52 on aec-wtt-manwan-yuxqin-2026-08-14
```

**Partial**: 0.52 of a 1-contract quote. Worth noting because the exit path had
only ever been reasoned about for whole contracts.

Fee was **0.0000** -- consistent with section 1, where the maker rebate at this
size rounds to zero rather than paying.

The reducing bid then chased the touch (0.55, 0.56, 0.68, cancel/replace each
pass), which is `replace_reducing_at_active_touch` rather than the eligibility
oscillation above -- a second, distinct source of churn on the inventory path.

### The exit machinery works, first end-to-end proof

```
11:48:39.081  FILL       SELL 0.52 @ 0.74   MAKER  fee 0.0000
11:48-12:03   passive reducer chases the touch, never fills
12:03:39.300  CANCEL     hard_timeout_cancel_confirm_first
12:03:39.436  PLACE_IOC  hard_inventory_timeout  bid @ 0.85
12:03:39.604  FILL       BUY  0.52 @ 0.85   TAKER  fee 0.0000
              position FLAT, unresolved 0
```

The 900-second clock fired to the second (11:48:39 + 900s), cancelled and
confirmed **before** crossing as invariant 6 requires, and cleared the
position. Nothing stuck.

```
ROUND TRIP P&L  -$0.0572
```

**The loss is entirely adverse price movement, not cost.** Both legs charged
zero: the maker rebate rounds down as section 1 predicts, and the taker fee did
too -- `0.06 x 0.52 x 0.85 x 0.15 = $0.004`, rounded to zero. Section 1 measured
that fee rounding *up* at size 1 and p=0.81, so rounding cuts both ways
depending on size and price.

The market moved 11 cents against us across a 15-minute hold. That is the cost
of the 900-second window, and it is the risk maker-lab's positive 900s markout
said was acceptable. **n = 1**: one sample decides nothing, and it is the first
real evidence either way.

## 6g. One market's in-flight mutation blocks all eight

Diagnosed 2026-08-14, **not fixed**, needs an explicit decision.

`_frame` sets `state_known` from

```python
state_known = self.gate.ready(self.ledger, now=now) and self.ledger.risk_state_known()
```

and `risk_state_known()` begins `if self.unresolved_intents(): return False` --
**account-wide**. So an unresolved intent in *any* market makes *every* market
report `execution_state_unknown`, which the runner recovers from by
quarantining. With eight markets placing and cancelling there is nearly always
a mutation in flight somewhere, so the markets interfere with each other
continuously. At one market this was invisible; at eight it serializes the bot.

The log shows the cross-market shape plainly -- a recovery in one market
failing on another market's cancel:

```
strategy_state_recovery:...vorvol-yurart  attempt=10
    "runtime recovery cancel is not yet reconciled in ...ivaana-buiedu"
```

Measured over 40 minutes after the churn fix:

```
recovery events          37
execution_state_unknown  25
actual plan actions      14
markets affected          2 of 8
```

Recovery outnumbers real work 2.6 to 1, about one spurious unknown-state every
96 seconds.

**Sampled directly, it is far worse than that ratio suggests.** Polling the
three inputs to `risk_state_known()` once a second for a minute:

```
state KNOWN    39/60 samples
state UNKNOWN  21/60 samples          -> 35% of wall-clock time
during unknown: unresolved=1, unknown_absent=1
```

That pairing is the **ordinary cancel lifecycle**, not an anomaly: a cancel is
issued so its intent is unresolved, the order stops appearing in the open-order
snapshot so it is marked `UNKNOWN_ABSENT`, and both clear once `retrieve`
confirms the terminal state. `risk_state_known()` fails on either condition and
both are account-wide, so **every routine cancel blanks quoting for all eight
markets, about a third of the time.**

This is why placements do not survive. The observable signature is a run of
`PLACE_POST_ONLY` at the same price with `working=0` and *no* `CANCEL` in the
action log -- the cancels come from `_quarantine_working_orders`, which acts
directly and never goes through the planner, so it leaves no action record.

Safety is unaffected throughout: zero open orders at the venue, position flat,
no accumulation. The cost is opportunity, and reward pays for resting time.

### Fixed, in maker-lab's style

maker-lab already solved this and we did not copy it. Its quarantine is scoped
to **one side of one market**, and the loop then carries on:

```python
_quarantine_other_side(run_id, slug, side, ...)
    "**This side is durably blocked**"
stats["failed"] += 1
continue                      # skip this side, keep every other one running
```

liquidity-bot instead blanked the whole account on any unprovable state. That
is the difference between 201 fills and $70.92 of rewards on one side, and 2
fills on the other.

The fix scopes the unresolved-intent test to the market being planned and keeps
the ownership tests account-wide:

```
per market   unresolved intents for that slug
account-wide UNKNOWN_ABSENT orders, venue:-prefixed working orders
```

The durable-intent design is per order and per market, so what we know about
market B genuinely does not change because a cancel is in flight in market A.
Ownership violations are different and must stay global -- a `venue:` order
anywhere means something else is trading the account.

The risk is that this widens when the reconciler is willing to build new risk,
which is a safety property, so it wants review rather than a quiet deploy.

## 6h. Where the reward actually comes from: thin books

Breaking the $70.92 down by league, from
`data/venue_reward_earnings_2026-08-14.csv`:

```
setkameua   $20.59   45 market-days   avg $0.458   29.0%
setkamemd   $15.11   14               avg $1.079   21.3%   best per market
itfme       $12.94   35               avg $0.370   18.2%
itfwo        $8.63   31               avg $0.278   12.2%
wtt          $5.27   16               avg $0.329
valorant     $2.48    4               avg $0.620
setkamecz    $2.28    8 | cs2 $2.24 10 | setkawoua $1.36 3
atp          $0.01    1
mlb          $0.01    1
```

**Table tennis and lower-tier tennis are 93% of everything paid.** `atp` and
`mlb` -- deep, liquid, heavily quoted books -- paid **one cent each**.

That is the reward formula, not luck. Share is our score over the competing
score up to `targetSize`, so a thin book with little competing maker size makes
one contract a real fraction, and a deep book makes it a rounding error. It
also caps the size lever: past some point our own size eats the share it is
trying to buy.

**liquidity-bot is already hunting in the right place.** The 25 markets it
quoted on 2026-08-14 were `setkamemd`, `setkameua`, `itfwo`, `itfme` and `wtt`
-- exactly the leagues that produced the 93%.

### The ranking prior disagreed with the payouts

`_candidate_key` sorts on `historical_reward_yield_prior`, which was built from
2-28 shadow observations per league. Against 168 market-days of actual payout,
after the existing shrinkage:

```
league       effective prior  rank    measured rank
cs2                   0.0608     1                9    <- last
setkamecz             0.0406     2                7
wtt                   0.0396     3                6
valorant              0.0372     4                2
setkameua             0.0366     5                3
setkamemd             0.0355     6                1    <- best
```

`cs2` ranked first on a sample of two and measured last of nine; `setkamemd`,
the best earner, ranked sixth. Recalibrated from the payout data, normalised so
the corpus mean matches the existing global constant.

**Units caveat, deliberately tolerated**: the new numbers are reward per
market-day, not per contract-hour, because we cannot recover how long or how
much maker-lab rested in each market. The value is a ranking key only, so
relative order is what matters. Replace it with true contract-hours if resting
time is ever recorded.

## 7. API limits

**Private WebSocket: 10 subscriptions per connection.** Measured by adding one
at a time; number 11 returns `max subscriptions per connection reached`. The
subscribe call itself does *not* raise — the error arrives asynchronously on
the socket, so a naive client believes it succeeded.

This constrains multi-market directly:

```
1 x orders    all slugs      (order events always carry marketSlug)
N x positions per-market     (positionSubscription OMITS marketSlug)
1 x balance   account-wide
-------------------------------------------------
N + 2 <= 10   ->   N <= 8 markets per connection
```

`PrivateReadOnlyStream.start` refuses above 10 rather than let the venue drop
the overflow silently. Positions must be scoped one market per subscription,
because the projector may only infer an omitted slug when the request covers
exactly one market. A single
multi-slug positions subscription re-creates the defect that ended the
2026-08-13T05:39Z session on its first fill.

**Public market data has no such problem.** Measured 2026-08-14: one
`subscribe_market_data` call carrying 8 slugs was accepted and delivered books
for **8 of 8** within 12 seconds, zero errors. The public side needs one
subscription regardless of market count; only the private side is constrained.

**REST budget** on the shared 4 req/s governor:

```
account-wide      ~0.40 req/s   (balance 1/5s, reconcile 2/10s)
per market        ~0.033 req/s  with an active WebSocket book
per market        ~0.333 req/s  when the book goes quiet and falls back to REST
```

Worst case (every book quiet) supports ~10 markets. REST is not the binding
constraint; the WebSocket subscription cap is.

**The cap is per connection, and connections are independent.** Measured
2026-08-14: `ws.private()` returns a fresh socket object per call, and two
sockets each held 10 subscriptions simultaneously with zero errors. Two
consequences:

- A reconnect gets its own quota, so recovery never contends with the closing
  connection's subscriptions. Eight markets at exactly 10 subscriptions needs
  no spare.
- More than 8 markets is reachable with additional connections, at the cost of
  recovering, reconciling and freshness-gating each socket separately.

## 8. Accounting

**Unresolved.** maker-lab carries a **$40.09** balance-versus-ledger gap that
was never closed. liquidity-bot's 2026-08-13 trades include an unexplained
**+$0.64** on a shared account, where the venue computed realized P&L against a
cost basis that was not from that round trip.

This does not block a one-contract safety test. It **does** block any
profitability claim or scaling decision.

---

## Open questions

1. **Does a book exist that clears the gate with margin?** The one that did
   passed at 1.24. Everything about size and market count depends on the answer.
   `reward_census.py` measures it read-only; it has not been run in anger.
2. **Can a passive exit fill inside a usable window on *our* books?** maker-lab
   says 46% by 900s on theirs. liquidity-bot is 0-for-4 at 30s. The 900s change
   is untested live.
3. **Is the 50% reward haircut and the 2x cost margin justified?** Neither has
   any evidence. Together they are 4x. **Now the binding constraint**: the
   2026-08-14 census measured 94% of all sides in the universe failing on this
   one test, with the best at `gate_ratio` 0.392 -- which clears 1.0 without the
   4x and clears nothing with it. See section 6b.
4. ~~Does the venue accept multiple private connections?~~ **Answered**
   2026-08-14: yes, and each connection carries its own 10-subscription
   quota. See §7.
5. ~~Should the reducing quote rest at the touch or at break-even basis?~~
   **Answered** 2026-08-14: the touch, no floor. A basis floor sorts winners
   into passive exits and strands losers until they settle. See §2.
