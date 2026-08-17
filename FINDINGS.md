# Findings — liquidity-bot

Empirically established facts, with the measurement that produced each one.
Nothing here is a plan or an intention. If a number appears without a source,
it does not belong in this file.

Companion to `docs/SAFETY_INVARIANTS.md` (rules the code must obey),
`BUGFIXES.md` (bugs that reached production: symptom, mechanism, fix) and
`docs/SCALE_REVIEW.md` (what is and is not proven). This file is the evidence
those two rest on.

**Standing rule: anything critical we establish goes in here immediately.**
Its companion is in `BUGFIXES.md`: every bug that reaches production gets an
entry there when it is fixed, with symptom, mechanism and fix.
Not at the end of the session, not "if it comes up again" -- when it is
measured. A fact that has to be re-derived costs hours and usually comes back
wrong the second time; the exit-anchor question and the per-market reward
breakdown were each researched twice before this rule existed. If it changes a
decision, or it took real work to learn, it belongs here with its measurement.

---

## Current status — 2026-08-15

**`max_markets` is 16** across two private connections -- set by the REST
governor, not the subscription cap (§7, finding 8). Thirty was tried live and
produced zero placements. Six production bugs fixed today, all
mutation-checked; see `BUGFIXES.md` for symptom and mechanism on each.

**Critical facts established 2026-08-15:**

1. **The $40 stop was reading one-sided round trips.** `realized_pnl` returned
   **+3.26** for a trade that lost **$1.55**. The formula is right; the closing
   fill was simply never recorded. Only two paths wrote fills -- the
   order-create response (inline executions only) and the private WebSocket --
   and a shutdown IOC satisfies neither, because it returns before its match
   completes and the fill then lands on a socket shutdown is closing. Three
   closes were lost this way. Fixed by replaying `portfolio.activities` during
   `_rest_reconcile`; `apply_execution` dedupes on `execution_id`.

2. **Fills ARE stored in an ambiguous price frame.** An earlier version of
   this finding claimed the opposite and was wrong; it is replaced rather than
   edited, because it was used as grounds not to investigate.

   The venue prices an execution against a specific outcome
   (`marketMetadata.outcome`), and `normalize_execution` stores `lastPx`
   without recording which outcome it was quoted on. The frame is then
   unrecoverable. Measured on `aec-valorant-ff-ep`: both legs priced against
   *Enterprise Esports* at 0.39 and 0.67, where 0.67 means 0.33 in Fire Flux
   terms. The ledger computed **+$1.34** on a trade that actually lost about
   **$0.30**.

   This matters because `realized_pnl` is what the $40 stop reads, and the
   error runs in the direction that hides losses. See task 24; the exact rule
   is NOT established and two theories have already been wrong.

3. **The shutdown flatten had no price cap.** It asked only whether
   `cross_exit` was complete, never what it cost, then fired an IOC at
   `worst_price`. The runtime hard exit refuses above `max_exit_loss`;
   shutdown had no equivalent, so every redeploy was an uncapped liquidation of
   any open position. Observed: a break-even short crossed out at 0.96 against
   a 0.65 entry.

4. **Tick prices must be rounded off the grid multiplication.** `41 * 0.01` is
   `0.41000000000000003`, and `build_order_payload` validates in `Decimal`.
   Nine of the ninety-nine cent prices land this way: **35, 41, 47, 57, 69, 70,
   82, 83, 94**. An unaligned price is rejected outright and retires the
   market.

5. **`book is not two-sided` is the most common error the bot produces** --
   2,121 occurrences across 21 of 77 markets ever bound, two of them retried
   about 1,100 times each at three-second intervals. Such a market can never
   quote, so the retries are pure cost.

6. **The venue publishes bids only.** The offer on one outcome is one minus the
   best bid on the other, so `best_offer - best_bid` is exactly the gap an
   operator reads between the two outcomes' bids. This is why the quoting rule
   is stated as "two cents in the middle".

7. **The REST rate cannot be exceeded by adding markets.** Every call awaits a
   single shared `AsyncIntervalLimiter` (`adapters.py:334`) that sleeps until
   the minimum interval elapses, so more markets queue rather than raise the
   rate. `from_rate` also refuses anything above the documented 20/s retail
   ceiling. What scales with market count is *freshness*, not rate: 30 quiet
   markets at 4 req/s is a 7.5s cycle against a 5s staleness bound, so quiet
   markets age out and stop quoting. That is safe, and the lever if it bites is
   a longer quiet-refresh interval -- never a higher request rate, since
   maker-lab was rate-limited and settled at 0.3 req/s.

8. **The REST governor caps market width at ~16, not the socket cap.**
   Measured live 2026-08-15T13:48Z at `max_markets = 30`: **29 markets bound,
   58 `book_stale` events, and ZERO placements.** Not one market could quote.

       N markets / 4 req/s must stay under max_book_age_seconds (5.0)
       30 / 4 = 7.5s cycle  ->  every book stale before its turn
       N < 20; 16 leaves a second of margin

   Subscriptions were never the limit -- 30 markets across four connections is
   subscribable and the sockets came up fine. Width past this point is not
   merely wasted, it is **negative**: extra markets starve the ones that would
   otherwise have quoted. Raising `rest_requests_per_second` is not the lever
   (maker-lab was rate-limited and settled at 0.3; we are at 4). Going wider
   needs a longer quiet-refresh interval or a higher staleness bound first,
   measured deliberately.

9. **The market-data WebSocket stops delivering somewhere between 16 and 29
   slugs on one subscription.** Measured 2026-08-15 with a standalone script,
   40 seconds per trial, one `subscribe_market_data` call carrying all slugs:

       subscribed  8  ->  books from  8 slugs, 249 messages
       subscribed 16  ->  books from 16 slugs, 256 messages
       subscribed 29  ->  books from 20 slugs,  20 messages

   At 29 the stream effectively dies: ~1 message per slug and then silence,
   against ~16 per slug at the smaller widths, and nine slugs never deliver at
   all. **This, not the REST governor, is why 30 markets produced zero
   placements.** Books never arrive over the socket, every market goes quiet,
   REST becomes the only path, and everything reports `book_stale`. The
   earlier `30 / 4 req/s = 7.5s` arithmetic described the overwhelmed fallback,
   not the cause -- it is superseded, and finding 8 above should be read with
   that correction.

   Also observed in the same run: `book is not two-sided` surfaced as a
   *stream error* out of `next_book`, killing the whole stream rather than
   skipping one market. One malformed book can stop book delivery for every
   market on that socket.

   **A second run did not reproduce it**: 29 slugs then delivered 27 markets
   in 415 messages and stayed healthy, while 16 delivered only 152. So width
   is not the variable at all -- the feed dies when a malformed book happens
   to arrive, and more markets only raises the odds. One run is noise; measure
   three times before believing a width.

   Fixed by skipping unusable books instead of raising them (streams.py).
   Scope violations and structural decode failures stay fatal.

   **Re-measured after the fix, three runs per width, 25s each:**

       width 16 -> 16/16 slugs,  82- 91 msgs, 0 dropped
       width 20 -> 20/20 slugs, 153-165 msgs, 0 dropped
       width 24 -> 22/24 slugs, 184-202 msgs, 0 dropped
       width 29 -> 27/29 slugs, 202-226 msgs, 0 dropped

   Twelve trials, **zero deaths**. The socket sustains 29. The two slugs
   missing at 24 and 29 are the same two in every run -- `zhuole-hohser` and
   `horana-shevik` simply do not publish. Message volume rises with width, as
   a healthy feed should.

10. **Market width is set by what REST can carry ALONE, not by the socket.**
   The socket sustains 29 (finding 9). The binding question is what happens
   when it drops, because a held position must still be exitable: REST
   refreshes N markets in N/4 seconds against a 5s staleness bound, so 20 sits
   exactly at the limit and 24+ does not fit. Above 20 a feed drop leaves every
   book stale, the exit unpriceable, and inventory riding toward settlement --
   which is how maker-lab lost $443.88.

   **max_markets = 20.** Worst-case exposure $100 against the $340 cap, so the
   risk budget is not binding. This supersedes finding 8, which reasoned from
   REST arithmetic alone before the feed fault was known.

10. **A position in a market whose book stops parsing can NEVER be exited.**
   It rides to settlement with certainty. Measured 2026-08-15 on
   `aec-setkamemd-chevic-scovla`: 254 log mentions, of which exactly **one**
   is an order action -- the entry quote at 15:51. After the fill at 18:52 the
   bot never placed another order on that market. The other 253 lines are
   `book is not two-sided` / `book_stale`.

   The chain: `parse_book_response` requires BOTH sides populated, so a
   one-sided book fails; the book then never refreshes and goes stale; the
   reconciler returns `book_stale` as a critical *before* planning anything;
   and `_inventory_plan` never runs. The passive reducer, the hard exit AND
   the pre-kickoff flatten all live inside that path, so all three are lost at
   once.

   **This is not a late flatten, it is no flatten.** About one market in four
   publishes a one-sided book (finding 5), so this is common, not exotic.

   The asymmetry that makes it fixable: exiting a long only needs the **bid**
   side to sell into. A one-sided book with bids prices that exit perfectly
   well. We reject it because the parser demands both sides before returning
   anything -- a requirement that belongs to *quoting*, not to exiting.

11. **Fills are recorded in OUR frame, and match our own orders exactly.**
   Measured 2026-08-15 across all 54 fills, joining each fill to the order that
   produced it: **every MAKER fill has fill price == our order price**, without
   exception. A maker fills at its own limit, so this is the strongest
   available check. The only mismatches are TAKER fills, where a better price
   than the limit is exactly what should happen.

   **So there is no frame inversion in the fills table**, and the earlier
   entry claiming otherwise (superseded below) was wrong.

   The discrepancy is one level up: what our order PRICE MEANS for a
   `BUY_SHORT`. That is the known intent inversion recorded in
   `live_orders.py` -- a short quote submitted at 0.36 rests on the venue at
   0.64. Our ledger is internally consistent; whether our submitted price
   equals the economic price for both intents is the open question.

   For `aec-valorant-ff-ep` the ledger says bought 0.39, sold 0.67 (+$1.34);
   the venue app showed 1.94 in and 1.72 out. 5 x 0.39 = 1.95 matches the buy.
   5 x 0.67 = 3.35 does not match the sell, while 5 x (1 - 0.67) = 1.65
   roughly does. So the BUY_LONG leg reads true and the BUY_SHORT leg does
   not.

   **The decisive test is now cheap**: the session stop reads settled cash
   (finding 12), so comparing cash delta against the ledger's computed P&L on
   any round trip settles which leg is wrong, with no inference.

12. **SOLVED: `BUY_SHORT` is a PURCHASE, not a sale. realized_pnl takes its
   sign from the venue's side label instead of the intent, which flips a loss
   into a gain.**

   Both intents we send are BUY intents and the price goes unchanged --
   documented in `live_orders._intent` and validated by maker-lab across 201
   fills. So `BUY_SHORT` at 0.67 does not sell at 0.67; it BUYS the opposite
   outcome for 0.67. Holding both sides nets the position out, because the
   pair settles at exactly 1.00.

   `aec-valorant-ff-ep`, worked through:

       BUY_LONG   5 x 0.39  =  paid 1.95
       BUY_SHORT  5 x 0.67  =  paid 3.35
       paid for 5 pairs        5.30
       5 pairs settle at       5.00
       realised                -0.30      <- matches the venue app exactly

   `realized_pnl` books the BUY_SHORT as cash IN of 3.35 because the venue
   labels it `ORDER_SIDE_SELL`, and computes +1.34. The label describes the
   outcome being taken, not the direction of our cash.

   **The fix is the sign, not the price.** Cash out for both intents, plus
   1.00 per netted pair. Prices are already correct (finding 11) -- fill price
   equals our order price on every maker fill.

   The session stop is already immune: it reads settled cash (finding 13).
   What is still wrong is per-market attribution and reporting.

13. **SUPERSEDED -- the fill price frame is not derivable from
   portfolio.activities.**
   Attempted 2026-08-15 across every trade on the account. Two obstacles:

   - every leg returned carries `aggressor: true`, and `makerExecutions` is
     empty on all of them. So the feed shows the COUNTERPARTY's order, not
     ours, whenever we were the maker -- which is most fills.
   - the submitted price and the execution price differ by a uniform **0.10**,
     positive for `BUY_SHORT` and negative for `BUY_LONG`
     (0.30->0.42, 0.57->0.65, 0.28->0.38 / 0.52->0.42, 0.59->0.49,
     0.58->0.50). Too consistent to be price improvement, and unexplained.

   Until our own maker legs can be read, any conversion rule would be inferred
   from someone else's order. Two attempts at reasoning this out have already
   been wrong. **Do not write arithmetic on top of it** -- realized_pnl feeds
   the $40 stop.

   What is now available that was not: fills record `outcome` (commit
   4bfadde), so once our own legs are obtainable the rule can be derived
   rather than guessed.

12. **The session loss stop reads settled cash, not a total rebuilt from
   fills.** Cash moves only when a trade actually executes, so there is no
   price frame to decode, no fill that can be missed, and nothing that can
   disagree with itself. Every P&L defect found on 2026-08-15 came from
   rebuilding a number the venue already knows: counterparty legs recorded as
   ours, closing fills lost on a restart, prices quoted against the opposite
   outcome.

       session realised = (cash now - cash at session start) + position x basis

   Open inventory has already spent cash, so it is added back at its own
   basis, and it falls back to the fill ledger whenever cash or a basis is
   unavailable -- a stop that raises is worse than one computed the old way.

   **Settled cash, not marked equity.** The earlier decision to reject marked
   equity (320 contracts move $38 on a twelve-cent mark) does not apply: cash
   does not move on paper price changes. The two were conflated in the test
   that covered this, which set `currentBalance` while reasoning about paper
   movement.

   Per market stays fills-based -- cash is account-wide -- and being wrong
   there costs one market rather than the account.

13. **Reward pools are per market** (`reward_pool=20000.0` on a single market's
   incentive period). More markets reaches more pools; more size only buys a
   larger share of the same one. This is the argument for raising market count
   rather than quote size, and it matches maker-lab's 30 markets at size 5.

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

## 6i. The venue inverts the price on SELL intents

Confirmed live 2026-08-14. Holding a short of 5 entered at **0.36**, the
reducing quote was sent as `ORDER_INTENT_SELL_SHORT` at **0.36** and the venue
rested it at **0.64**, shown in the app as `64c 0/5 filled`.

```
0.64 = 1 - 0.36
```

At that price the order **adds to the short instead of closing it**. The
position can never flatten, and every reducing quote is a new position in
disguise.

maker-lab hit this and documented it in `live_orders.to_intent`: *"the exchange
read our 0.29 as the YES price and charged us the NO side of it"* -- about 41
cents per contract given away on the one fill it got, after *"three wrong
diagnoses before the arithmetic gave it away"*. Their note also warns that
`preview` does not catch it: it echoes which OUTCOME an order takes and says
nothing about which FRAME its price is in.

**Their resolution, and now ours: two intents, never four.**

```python
bid   -> ORDER_INTENT_BUY_LONG     price unchanged
offer -> ORDER_INTENT_BUY_SHORT    price unchanged
```

`SELL_SHORT` and `SELL_LONG` are exactly the two intents maker-lab never sends,
and they are the two whose price convention was never validated here. The venue
nets a BUY on the opposite outcome against an open position, so reduction needs
no separate intent -- maker-lab ran this for days across 201 fills and 101
exits.

The `reducing` flag is retained in `OrderRequest` and deliberately ignored when
choosing the intent. It still governs post-only, time in force, and the
exact-position invariants.

**Note the earlier round trip that appeared to work.** On 2026-08-14 a short of
0.52 entered at 0.74 was closed by `SELL_SHORT` at 0.85 and the position did go
flat. Under inversion that order rested at 0.15, not 0.85 -- deep enough
through the bid to fill immediately and close the position, which is why it
looked correct. It closed at a far worse price than the books recorded, the
same way maker-lab's `+$0.02 close` was really nearer `-$0.82`.

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

## Session-lifetime counters cannot bound a churning system

Measured 2026-08-16. Markets are consumed continuously: a game kicks off, its
book goes quiet, `book_stale` exhausts `MAX_RECOVERY_ATTEMPTS`, and the market
retires permanently. That is correct behaviour — a live game must not be quoted
— but it means **retirement is the normal end of a market's life, not an
exception**. Observed rate: 31 markets over 7h09m, ~4.4/hour, 30 of them
`unrecoverable_book_stale`.

Any health bound measured against a cumulative session total therefore fires on
longevity rather than on sickness, and fires *later* the healthier the session
is — the opposite of what a guard should do. Bounds on this system must measure
a **rate or a burst inside a window**, never a lifetime count.

Corollary, and the reason a window alone is not sufficient: sports markets kick
off in **synchronized slates**, so a whole slate of markets can retire within
minutes of each other with nothing wrong. Any burst detector must exclude
markets whose `game_start` has passed, or the morning slate reads as a cascade.

## `book_stale` is self-perpetuating, and REST contention is not the cause

Measured 2026-08-16 across a 7h09m session, 7,535 `book_stale` events.

The obvious theory was REST starvation: the refresh trigger is 3s
(`live_commissioning.py:1506`), the stale threshold is 5s
(`reconciler.py:258`), and 20 markets needing a refresh every 3s is 6.67 req/s
against a hard 4 req/s cap. **That theory is wrong.** Tested against the data:

```
hour | markets quoting | book_stale | per market
 00  |      19         |    411     |    21
 02  |       8         |    395     |    49
 04  |       6         |    251     |    41
 05  |       6         |    290     |    48
```

Staleness is flat regardless of load, and per-market staleness *rises* as
markets drop. Contention would show the opposite. The market stream was also
healthy the whole session -- all 10 `component=market_stream` errors in the log
are from 2026-08-14 -- and zero books were dropped.

**The actual mechanism is the recovery path feeding itself.** `plan()` returns
`book_stale` BEFORE anything else, then `_handle_strategy_critical` refreshes
the book and returns, and the caller applies `_recovery_delay` -- exponential
backoff to a 30s cap. So the refresh is immediately followed by a wait longer
than the 5s freshness budget it just satisfied. Measured interval between
consecutive `book_stale` events on one market: **34-75s, clustered at 45-60s.**
Every visit fails, refreshes, backs off, and expires the refresh before the
next visit. The refresh is always wasted work.

This burns exactly the quiet markets -- which are the ones with the least
competition for reward share, since reward is paid for RESTING and not for
trading. The 5-second budget assumes a continuously-updating book; an illiquid
market legitimately has nothing to send, and being quiet is not being wrong.

**Fixed** by replanning once on the refreshed book before conceding the
backoff, bounded to a single extra attempt per sweep so a book that never
arrives still yields. The success path then clears `strategy_state:{slug}`, so
a market that recovers stops marching toward MAX_RECOVERY_ATTEMPTS.

## Crossing at 900s vs at the pre-kickoff flatten: no case for waiting

Measured 2026-08-16 against the live ledger (92 fills, 29 reconstructed round
trips). P&L is computed in the venue's own `side` frame -- SELL proceeds minus
BUY costs on a matched pair -- which is unambiguous and avoids the intent
inversion that produced three wrong diagnoses earlier.

```
                                  n   total    per-contract median   winners
passive exits                     9   +$1.05          +0.0380          7/9
taker exits                      20   -$4.76          -0.0740          4/20
  taker <900s (pre-kickoff only)  8   -$2.51          -0.0750          2/8
  taker >=900s (inventory clock) 12   -$2.25          -0.0590          2/12
```

Sub-900s taker exits can only be the pre-kickoff flatten, because the inventory
clock has not fired yet -- that is what makes the split possible without book
snapshots we do not have.

**The inventory-clock crossings were slightly BETTER per contract than the
pre-kickoff ones** (-0.059 vs -0.075). That is the opposite direction from
"holding is cheaper", and it is consistent with the adverse-selection argument:
what survives to the kickoff deadline is the inventory the market moved away
from, exiting into thinner pre-game books.

**This does not settle it.** n=8 against n=12 with per-contract variance far
larger than the 1.6c difference, and the two groups are confounded -- the
sub-900s group is near-kickoff *by construction*, so it faces worse books for
reasons other than timing. The honest reading is: **no evidence supports
waiting past 900s, and weak evidence says it costs.** Keep the current policy.

Caveat on the passive row, repeating section 2: which exits resolve passively
is partly construction, not policy. Do not read +0.038 vs -0.074 as the gain
from a policy change -- read it as why resting time is worth protecting.

## Reward is concentrated in the hours before kickoff

Measured 2026-08-16 across 48 live candidate markets, using the selector's own
projection (`projected_reward` summed over eligible sides).

```
mins to start     n    median reward    share paying > 0
15-30            11       0.000313            100%
30-60             5       0.000327            100%
60-120           10       0.000204             90%
120+             21       0.000022             67%
```

The 15–30 minute bucket pays roughly **14x** the median of the 120+ bucket, and
a third of markets beyond two hours pay nothing at all. (A 0–15 bucket existed
with a much higher value but n=1, so it is not read here.)

**This settles the entry-gate question against widening.** Stopping quotes at
T-15 or T-30 would surrender the best-paying window to avoid fills that cost
about a cent per contract. The T-300s gate is the right size: it removes only
fills that were guaranteed to be force-crossed (BUGFIXES, entry/exit
incoherence) and gives up almost no reward.

**Caveat, and it matters.** This is *projected* reward at a single snapshot,
not realized earnings, and **every market passes through every bucket** as its
game approaches — today's 120+ market becomes a 30-minute market later. So the
claim is NOT "distant markets are worse to hold". It is "reward is available in
the hours before kickoff and largely absent before that", most plausibly
because distant markets have thin or absent books to score against.

**What it implies for effort.** Near kickoff is simultaneously the best-paying
and the most adversely-selected window, so the answer is to keep resting there
and manage fills, not to avoid the window. Combined with the scale of the two
sides — reward ~$25/day against trading ~-$1.2/day, so losses are about 5% of
reward — effort spent increasing RESTING TIME is worth more than effort spent
improving fill quality.

## The `book_stale` replan works, and the event COUNT is the wrong metric

Measured 2026-08-16, comparing the pre-fix session (00:18-07:27) against the
post-fix one on `bae9f41`.

```
escalation depth on book_stale

NEW (65 min)              OLD (7h09m)
  attempt=1   2350          attempt=5    80
  attempt=2     11          attempt=6    56
  attempt=3     20          attempt=7    51
  attempt=7      1          attempt=8    43
                            attempt=9    39
  max reached: 7            attempt=10   37
  retirements: 0            attempt=11   34
                            attempt=12   32  <- permanent retirement
```

**99% of staleness now resolves on the first attempt**, and nothing reaches
`MAX_RECOVERY_ATTEMPTS`. Pre-fix, markets climbed the full ladder and hit 12
thirty-two times, each one a permanent `unrecoverable_book_stale` retirement --
the mechanism that burned twenty slots in forty minutes and ended the session
on `market_retirement_limit_exceeded`.

**The raw event count went UP, from ~400/hour to ~2,190/hour, and that is not
a regression.** The fix removes a backoff that escalated to 30 seconds, so a
market that previously failed once per 45 seconds now re-checks promptly. More
events, each resolving. Counting `book_stale` lines measures how often we LOOK,
not how often we are broken.

**Use escalation depth, not event count.** `attempt=N` is the metric that maps
to harm, because retirement is what costs a slot and only sustained failure
reaches it.

**Not fully closed:** the alternative reading is that this session's books are
genuinely quieter and the fix is being credited for it. Distinguishing them
needs the resting-fraction data from the summary work -- if markets rest a
healthy share of wall-clock time, the churn is benign.

## The same predicate, asked scoped and unscoped, is a place bugs hide

Measured 2026-08-16 from a live 36-minute stall in which the bot quoted
nothing while looking completely healthy.

`risk_state_known(market_slug)` applies its `UNKNOWN_ABSENT` test **only when a
slug is passed**. That scoping was itself a fix (§6g): account-wide, one
market's ordinary in-flight cancel blanked all the others and every market
quarantined its resting quote.

But the scoping only helps callers that pass a slug. `_frame` does.
`_wait_for_initial_private_state` does not -- it asks `gate.ready()`
account-wide, which is defensible for a fresh bind. So a single absent order
in a market that had *already been released* was simultaneously:

- harmless to every per-market decision, and
- fatal to every stream bind in the process.

Both readings were true at once, which is exactly why the wrong one survived
scrutiny. The absent order was explicitly considered and dismissed early --
"that test is scoped, and that market is gone" -- and the dismissal was correct
about the scoped callers and wrong about the whole system.

**The general lesson:** when a predicate takes an optional narrowing argument,
its behaviour is really two predicates, and safety reasoning has to name which
one it is talking about. Enumerate the call sites of both forms before
concluding a state is contained. Here: `grep -n "risk_state_known(" src/` is a
five-second check that would have redirected the first hour of diagnosis.

**Corollary, on retries.** The same stall retried its failed operation four
times and failed identically each time. A retry that keeps failing is evidence
the fault is *below* the thing retrying -- not evidence that retry is the fix.
Shipping the recovery alone would have converted a silent hang into a loud
infinite loop and looked like progress. See BUGFIXES 2026-08-16.

## A manual close leaves a one-sided P&L record that eats the loss stops

Measured 2026-08-16 on `aec-setkawoua-melolh-kololh-2026-08-16` (Melnyk Olha),
after the operator recalled closing that position by hand during the previous
session.

What the ledger holds for that market:

```
fills:      1  BUY 5 @ 0.28, MAKER, 14:20:16Z   <- the entry
            (no sell fill, ever)
orders:    35 CANCELED, 1 FILLED, 1 REJECTED
position:  net_qty 0, cost 1.39
realized:  -1.39
```

The bot bought 5 at 0.28 and then walked a sell ladder down -- 0.43, 0.30,
0.29, 0.28, 0.27, 0.26, 0.25, 0.24, 0.21, 0.20, 0.19, 0.18 (rejected) -- and
**not one of those sells ever filled**. The position is nevertheless flat. It
was closed by hand at the venue.

**The exit is invisible to the ledger.** `fetch_executions` back-fills from
`portfolio.activities`, and the manual close carried no bot client-order-id to
attribute it to. So the entry is recorded, the exit is not, and the market
reads **-1.39 realized against a position that is actually closed**.

**Why that is not cosmetic:** `realized_by_market()` and `realized_pnl()` are
what drive the $2 per-market stop, the $5 session stop and the $15 hard stop.
A phantom -1.39 consumes 70% of that market's stop and 28% of the session stop
before a single real loss occurs. Loss stops are the only automated protection
on live capital, and this makes them fire early on money that was never lost.

This is a **second, independent cause** of the one-sided-fill defect already
recorded as NEXT.md's top priority. That one is about IOC exits never reaching
`execution_fills`; this one needs no bug at all -- a human closing a position in
the UI produces it by construction, and always will.

**Operational rule until the ledger can attribute unowned executions:** closing
a bot position by hand is not free. It is safe for the *account* -- the bot
re-reads positions from REST and stays flat -- but it silently debits the loss
stops. Prefer stopping the bot, or expect the stops to trip early and know why.

Worth noting what did NOT happen: no `venue:` order was ever recorded, in the
whole ledger. Manually *cancelling* or *closing* stays invisible to the
ownership test. Manually *placing* would not -- that trips
`ownership_is_known()` and is a terminal safety event that stops every market
at once, by design.

## Crossed exits are where the money goes, and the 900s clock pulls the trigger

Measured 2026-08-16 across the whole ledger, splitting every market by how its
exit filled.

```
exit was TAKER (crossed):   28 markets   net  -30.22
exit was MAKER only:        11 markets   net   -9.67
NO exit fill recorded:      12 markets   net  -22.41
                                        TOTAL  -62.30
```

**Taker exits are ~76% of real recorded losses.** Entries are almost entirely
MAKER; the damage is on the way out. Individual crosses:

```
bought 0.92 -> crossed out 0.79   (-13c/contract)
sold   0.65 -> bought back 0.96   (-31c/contract)
sold   0.36 -> bought back 0.46   (-10c/contract)
```

Hold time from entry to last exit, for markets that lost more than $1, clusters
exactly where you would expect if a timer were forcing the exit:

```
911s   -4.20   kundmy-tsokos
966s   -3.60   bornac-servas
```

`max_inventory_age_seconds` is **900**. Those are not positions that failed to
find a passive exit for their own reasons -- they are positions the clock
crossed out.

**The guard that should have stopped the expensive ones cannot fire.**
`_inventory_plan` refuses a cross costing more than `max_exit_loss` while
outside the kickoff window, and the comment is explicit that "before the
kickoff deadline a too-expensive exit can wait for a better book". But
`max_exit_loss` is wired to `max_market_loss_usd` = **$40**, the session stop.
Five contracts can lose at most about $5, so `pnl < -40` is unreachable and
every timeout crosses at whatever price the market has moved to.

**That number is doing two unrelated jobs.** `reconciler.py` uses it at the
exit (the cross cap) AND in `_flat_plan` (the entry gate: do not open a side
whose stressed close would cost more than this). Retuning it for the exit would
tighten entry by ~50x. It has to be split, exactly as the loss stops and the
exposure budgets were split for the same reason -- "stop after losing $40" and
"never pay more than $40 to leave one position" are different statements.

The `-22.41` with no exit fill recorded is not trading loss; it is the
one-sided-record defect (manual closes, IOC exits that never reach
`execution_fills`).

**Fixed so far:** exits now rest one tick inside the touch while still at or
above entry, to raise passive fill probability and reach the clock less often.
The `max_exit_loss` split is identified but NOT yet done.

## Re-pricing costs ~70 seconds of not resting, and that is where coverage goes

Measured 2026-08-16T21:47-21:56Z, first clean session after the day's fixes.

The Discord summary read **8 of 20 markets resting**, with idle reasons
`no_quote_yet 6` and `target_not_met 6`. `target_not_met` is the reward gate
declining correctly -- the venue pays only once a side reaches `targetSize`.
`no_quote_yet` is the interesting half: it means the market HAS a book, has NO
economic blocker, and still holds no order.

Those markets are not idle. They are being worked constantly -- 18 of 20 saw an
action inside three minutes -- but the orders do not stay. One market's bid
walked 0.69 -> 0.72 -> 0.75 -> 0.87 in six minutes, and every move produced a
`resting_price_left_the_touch` cancel followed by a replacement on a LATER pass:

```
21:49:49  PLACE  bid 0.69
21:50:10  CANCEL bid              rested 21s
21:51:24  PLACE  bid 0.72           <- 74s holding nothing
21:51:57  CANCEL bid              rested 33s
21:52:44  PLACE  bid 0.75           <- 47s holding nothing
21:54:22  CANCEL bid              rested 98s
21:56:06  PLACE  bid 0.87           <- 70s holding nothing
```

Across every market and side in that window:

```
cancel -> next place on the SAME side: 6 samples
  median 69.4s   mean 81.2s   min 40.0s   max 157.6s
  total dark time: 487s across 6 re-prices
```

**A re-price costs about seventy seconds of not earning.** The cancel is one
mutation and the replacement is another, the driver applies at most one
mutation per market per service pass, so the two land on different passes and
the gap is a full service interval. On a book that moves, a side spends roughly
half its life dark, and the resting count reads 8-9 out of 20 no matter how
healthy the process is.

**This is not churn in the 2026-08-14 sense** -- those quotes died two seconds
after placement to a state bug. These cancels are all correct: the touch really
did move, and an order off the touch earns a discounted score or nothing. The
loss is in the latency between deciding to re-price and having re-priced.

**Do not fix this by widening the cancel trigger.** Leaving a stale order at the
old price trades dark time for scoring at a discount, and the reward formula is
`discountFactor ^ ticks * size` -- the penalty compounds per tick, so a badly
stale order is worth little more than no order. The candidates worth testing
are (a) a shorter service interval, which shrinks every gap directly, and (b)
placing the replacement before cancelling the old, which removes the gap
entirely at the cost of briefly holding two quotes on one side -- bounded, but
it doubles that side's exposure for the overlap and needs its own risk check.

**Unmeasured:** the resting FRACTION over a long window, which is the number
that actually maps to reward. 8/20 is an instantaneous count and understates
coverage if the dark windows are staggered across markets.

## Re-deriving the REST-contention theory a second time (it is still wrong)

2026-08-16T22:00Z. Recording the mistake, because the theory is attractive
enough that it has now been derived twice from first principles and the
arithmetic looks compelling both times.

The pitch: `_refresh_quiet_book_if_due` re-reads a book over REST at 3s, the
reconciler refuses one older than 5s, `rest_requests_per_second` is hard-capped
at 4, so 20 markets want `20/3 = 6.7 req/s` against 4 and must starve.

**It is wrong, and the section "`book_stale` is self-perpetuating, and REST
contention is not the cause" above already disproves it with data.** Staleness
per market RISES as markets drop -- 21/market at 19 markets, 49/market at 8 --
which is the opposite of contention.

What made me believe it the second time was **using the event count as the
metric**, which the section "The `book_stale` replan works, and the event COUNT
is the wrong metric" also already warns against. Counting `book_stale` lines
measures how often we LOOK, not how often we are broken.

The correct metric, on the same window that produced the false alarm:

```
422  attempt=1
  5  attempt=2
  7  attempt=3
  0  markets retired
```

Nearly every event is one failure that recovers on the replan. Nothing
escalates, nothing costs a slot. A sustained 25/min of `attempt=1` is a healthy
bot visiting quiet books, not a starved one.

**Two rules that would have prevented this, both already written down:**
grep FINDINGS before deriving a resource-contention theory, and never reason
about `book_stale` from event counts.

**`max_markets = 20` is not unexamined.** Its code comment reasons about
exposure, which is what made it look capital-only, but the REST budget for 20
markets is worked through in the section beginning "REST budget on the shared
4 req/s governor" -- 0.033 req/s per market with an active WebSocket book, and
0.333 req/s only when a book goes quiet and falls back. The 6.7 req/s figure
assumes every market falls back at once, which is the overwhelmed-fallback case
that section explicitly distinguishes.

**Still open and genuinely unexplained:** the measured 70s median gap between a
`resting_price_left_the_touch` cancel and its replacement (see the section
above). That measurement stands -- it is timestamps, not inference -- but its
cause is NOT REST contention and is not yet established. Do not attach it to
this theory.

## The docs disagreed with the running config on four safety numbers

Audited 2026-08-16 from a second machine reading only the markdown, then
resolved against `CommissioningConfig` and the live command line.

**The runner takes no config overrides.** Its command line is
`--auto-select-rewards --database --lock-file --arm-live
--accept-one-contract-risk --exclusive-api-key --maker-lab-stopped` -- not one
of which sets a limit. So the dataclass defaults in `commissioning.py` ARE what
executes, and it is the only authority.

```
quote_size                       5
max_markets                      20
max_market_loss_usd              40
max_session_loss_usd             40
hard_stop_loss_usd               45
max_inventory_age_seconds        900
force_exit_before_start_seconds  300
max_early_exit_loss_usd          4
rest_requests_per_second         4
```

What the prose said instead:

| | claimed | live |
|---|---|---|
| loss stops | $2/$5/$15 (README, COMMISSIONING, PHASE5, SCALE_REVIEW); $30/$35 (invariant 25) | **40/40/45** |
| quote size | 1 contract (README, invariants 13/57) | **5** |
| max_markets | 8 (README, invariants 13/57, NEXT item 5) | **20** |
| inventory age | 30s (README, SCALE_REVIEW, COMMISSIONING) | **900s** |

Even the shorthand used throughout FINDINGS, NEXT and BUGFIXES -- "the $40
stop" -- is incomplete: the emergency ceiling is $45, not $40.

**Why this matters more than a typo.** Three of the four are risk limits. A
reader reasoning from "a $2 market stop" and "one contract" concludes the
downside is bounded at pocket change, when the live configuration risks 5
contracts across 20 markets against a $40 stop. Every one of those documents
reads as authoritative and none carried a date.

**The trap that produced it:** the numbers moved (1 -> 5 contracts, 8 -> 30 ->
16 -> 20 markets, 30s -> 900s, and the stops repeatedly), and each move updated
the code plus the file being worked in, never the rest. Nothing cross-checked
prose against config.

`README.md` and invariants 13, 25 and 57 are corrected. Invariant 57 keeps its
superseded text struck through rather than deleted, per the WATCHLIST rule --
"eight is measured, not chosen" was used as grounds not to investigate raising
it, and deleting the claim would hide why the ceiling stood for so long.

**All corrected 2026-08-16**: `README.md`, invariants 13/25/57,
`COMMISSIONING.md`, `PHASE5_ACCEPTANCE.md`, `SCALE_REVIEW.md`,
`COMMISSIONING_ACCEPTANCE.md`, `IMPLEMENTATION_PLAN.md` and NEXT item 5.

**One part of that audit did not hold.** It also reported FINDINGS carrying
duplicate section numbers -- two each of 10, 12 and 13 -- which would matter,
since other documents cite findings by number and WATCHLIST graduates entries
"to FINDINGS with the number that decided it". Checked exhaustively: FINDINGS
has 16 numbered headings, sections 1-8 plus 6b-6i, **no duplicates and no
sections 10, 12 or 13 at all**. `SAFETY_INVARIANTS.md` has 63 invariants, also
with no duplicates; 10, 12 and 13 exist there and are unique, which is the
likely source of the confusion. Recorded because a false structural claim is
worth closing explicitly -- otherwise it gets re-investigated.

**Rule going forward:** a safety number in prose is a copy, not a source. Cite
`commissioning.py` or quote the value with the date it was verified.

## Resting fraction, measured properly: 63% -- and the re-price penalty is small

Measured 2026-08-17T01:35Z over a 228-minute live window, 27 markets through 20
slots, 43 sides.

**The first version of this measurement was wrong.** It divided each side's
resting time by the whole window, and markets rotate as games kick off -- so a
market bound for ten minutes could score at most 4% however perfectly it quoted.
That reported a 30.0% mean which was almost entirely rotation, not performance.
`scripts/resting_fraction.py` now divides by each market's own active span and
reports the headline mean only over markets present for >=80% of the window.

```
 of span  of window   span  orders  market / side
   92.4%      91.3%   226m      29  sirvea-salnic    bid
   92.3%      74.7%   185m      24  urevad-sirvea    bid
   90.5%      89.4%   226m      12  chitsa-yinhua    offer
   86.3%      85.3%   226m      24  marshi-yujahn    offer
   85.2%      84.1%   226m      30  chitsa-yinhua    bid
   68.3%      67.4%   226m      58  yujkim-ziyzho    bid
   41.9%      41.4%   226m      23  reikom-alejon    offer
    3.7%       3.7%   226m      13  zhixia-ryunak    bid

mean over markets present >=80% of window: 62.8%  (13 sides)
```

**Re-pricing costs far less than the six-sample estimate suggested.** The side
with 30 placements rests 85.2%; the one with 58 rests 68.3%; the one with 29
rests 92.4%. Order count barely predicts resting fraction. NEXT item 3 was
written on a 25-minute window and a 6-sample gap measurement, and it
**overstates the case** -- a side re-pricing thirty times still rests 85%.

The real spread is between markets, not between busy and quiet sides: two
markets sit at 3.7% and 6.7% on a full 226-minute span, which is where the
coverage is actually being lost. That is worth understanding before optimising
the mutation path.

**Method note for whoever repeats this.** Denominator choice dominated the
answer: the same data reads 30% or 63% depending on it, and 30% would have
justified work that 63% does not. State the denominator whenever quoting a
resting number.

## Same-price re-place churn: looked obvious, measured at 10%

2026-08-17T01:40Z. Recorded because the sample that suggested it was vivid and
wrong, and the next person will find the same sample.

`zhixia-ryunak` rests 3.7% of a full 226-minute span across 37 placements, and
its action log reads like a textbook churn bug -- cancel for
`resting_price_left_the_touch`, then re-place at the IDENTICAL price, four
cycles in a row, ~40s dark each time:

```
01:33:32 PLACE  bid 0.43
01:34:38 CANCEL bid  resting_price_left_the_touch
01:35:26 PLACE  bid 0.43     <- same price
01:36:18 CANCEL bid
01:36:56 PLACE  bid 0.43     <- same price
```

The obvious reading is a flapping touch being chased, with a known fix shape --
the `reward_eligibility_dwell_seconds` hysteresis already exists for exactly
that failure on the eligibility gate.

**Measured across the whole session instead of that one market:**

```
re-prices after a price-left-the-touch cancel: 119
  came back to the SAME price:  12   (10%)
  genuinely moved:             107   (90%)
  dark time on same-price trips: median 49s, 14 min total
```

14 minutes of 228, across 43 sides. Adding dwell hysteresis to the re-price
trigger would recover a few minutes an hour at the cost of resting at a stale
price, and reward discounts per tick from the touch.

**What the data does support** is that the poor-resting markets are the ones
whose touch moves constantly. `zhixia-ryunak` logged 19
`resting_price_left_the_touch` cancels and rests 3.7%; `sirvea-salnic` logged 3
and rests 92.4%, with its cancels being `side_no_longer_desired` (the economic
gate) instead. A book that moves every 40 seconds cannot be rested in, so its
projected reward is never realised no matter how good the projection was.

That points at **selection**, not the order path: rank on whether a book holds
still long enough to rest in, not only on projected reward. Two markets is a
hypothesis, not a result -- it needs the touch-move rate correlated against
resting fraction across a full slate before anything is changed.

## 55% of our resting time is spent where reward is not

Measured 2026-08-17T02:20Z over the 21:47Z session: 42 markets, 56.4 hours of
total resting time, bucketed by how far from kickoff each resting second
occurred (`scripts/resting_by_kickoff.py`).

```
 mins to kickoff    resting   share    projected reward (section above)
            0-15      1.58h    2.8%    n=1, not read
           15-30      2.64h    4.7%    0.000313   100% pay
           30-60      5.42h    9.6%    0.000327   100% pay
          60-120     15.78h   28.0%    0.000204    90% pay
         120-240     21.50h   38.1%    0.000022    67% pay
            240+      9.47h   16.8%    0.000022    67% pay

inside the 0-120 window that pays: 45.1%
```

Reward accrues per second of resting, so a bucket's share of resting time is
directly comparable to its reward density. **The largest single bucket, 38% of
all resting time, is the worst-paying one.** More than half of everything the
bot did in four hours was spent where a third of markets pay nothing and the
median pays a fourteenth of the 15-30 minute window.

**This is the largest earning lever identified so far**, and unlike the five
theories that died during the same session it is a direct measurement of our own
behaviour rather than an inference about a mechanism.

**Why it happens.** `_opportunity_key` ranks on `projected_reward_total` first,
which should already prefer near-kickoff markets since that projection is what
falls off with distance. `game_start` appears in the key only as the **seventh
tiebreaker**, behind six continuous-valued fields that essentially never tie. So
proximity influences selection only through the projection, and the projection
is evidently not separating the buckets in practice.

**Two caveats that bound the conclusion.**

1. The reward figures are *projected* at a snapshot, not realised. The venue
   exposes no per-period earnings breakdown (no period field on
   `GET /v1/incentives/earnings`), so resting time cannot be attributed to money
   directly. This compares our time against a projection, not against income.
2. **Every market passes through every bucket.** Resting at 240+ costs nothing
   if that slot would otherwise sit empty. The cost is only real if
   near-kickoff candidates were passed over -- and with `viable=85` against 20
   slots there is a 4:1 oversupply, which makes it likely but not proven.

**The measurement that would close it:** the time-to-kickoff distribution of the
85 viable candidates at selection time. If most are inside two hours, distant
markets are displacing them and the ranking needs proximity promoted out of
seventh place. If most are distant too, the slate simply had nothing nearer and
nothing is being lost.

## ...and there is nothing nearer to rest in. Do not "fix" the ranking

Measured 2026-08-17T02:22Z, immediately after the section above, by running the
live selector read-only and bucketing its viable candidates by time to kickoff.

```
viable candidates: 74
        0-15      1
       30-60      4
      60-120      4
     120-240      8
        240+     57

inside the 0-120 window: 9 of 74  (12%)   against 20 slots
```

**Only nine candidates exist inside the paying window, for twenty slots.** The
55% of resting time spent beyond two hours is therefore not selection choosing
badly -- it is the overnight slate having almost nothing nearer. Promoting
`game_start` out of seventh place in `_opportunity_key` would take those nine
first and then fill eleven slots with distant markets anyway.

This confirms, rather than assumes, the caveat recorded with the previous
section: resting at 240+ costs nothing when the slot would otherwise sit empty.
The measurement stands; the action does not follow from it.

**What would change the answer:** the same scan run during a dense slate --
European or US afternoon -- when many markets are inside two hours. If near
candidates then outnumber slots and the ranking still fills up with distant
ones, proximity genuinely is mis-weighted. Overnight tells you nothing about
that.

**An unexplained discrepancy, left open deliberately.** The top-ranked distant
candidates here project `0.011986`, `0.011767`, `0.011682`, while the section
above records a 120+ bucket median of `0.000022` -- a 500x gap on what is
nominally the same quantity, `projected_reward_total` summed over eligible
sides. Either the two measurements are not the same field, or one is wrong.
Until that is resolved, **neither number should be used to justify a change to
selection**, because the whole argument for reweighting rests on projections
being comparable across buckets.

**Sixth theory this session that measurement stopped before it became a code
change.** The others: REST contention, re-price latency, same-price churn,
`book_stale` as a fault, and markets being unable to quote. The pattern that
caught all six was checking the specific claim rather than the plausible
mechanism.

## Trading losses now exceed reward. The 5% premise no longer holds

Measured 2026-08-17T12:0xZ over 203 fills and 93 markets.

This project's founding economics, recorded above, are "~$25/day in rewards
against about -$1.2/day in trading P&L -- losses are ~5% of reward", and nearly
every design decision follows from that ratio. **It is no longer true.**

Realised P&L attributed to the day of each market's first fill, split by whether
both sides of the round trip were actually recorded:

```
day        complete   P&L    | one-sided   P&L      reward that day
2026-08-14      2    -5.03   |     1     -2.93       5.48
2026-08-15     23   -11.22   |     5     -9.88      28.46
2026-08-16     14   -27.58   |     6     -9.60      20.25
2026-08-17     27   -51.03   |    15    -26.32      not posted
TOTAL          66   -94.86   |    27    -48.73
```

The `one-sided` column is the known bookkeeping defect (NEXT TOP PRIORITY) and
is not trustworthy. **The `complete` column is** -- both fills recorded, priced
in the venue's own frame by `realized_pnl`. It shows:

```
2026-08-15   reward +28.46   trading -11.22   net +17.24
2026-08-16   reward +20.25   trading -27.58   net  -7.33
2026-08-17   reward      ?   trading -51.03   (half a day)
```

**The bot went from profitable to loss-making around 2026-08-16**, and the loss
rate is accelerating: -5.03, -11.22, -27.58, -51.03. Per-market loss is growing
too, not just market count -- -0.49/market on 08-15 against -1.89/market on
08-17.

**The mechanism is crossed exits.** By category:

```
exit crossed (TAKER)   55 markets   -85.19   median -1.21   worst -8.33
NO EXIT RECORDED       27 markets   -48.73   (bookkeeping, not trustworthy)
exit passive (MAKER)   11 markets    -9.67   median -0.31
```

A passively-exited market loses a median of **31 cents**. A crossed one loses
**$1.21**, four times as much, and there are five times as many of them.

And the maker/taker mix inverted:

```
2026-08-15   37 MAKER   18 TAKER   33% taker
2026-08-16   13 MAKER   36 TAKER   73% taker
2026-08-17   25 MAKER   68 TAKER   73% taker
```

Entries are makers and exits are takers in the venue's frame, so 73% taker means
**almost every position is now being exited by crossing rather than resting
out**. Total fees flipped from a rebate to **+$3.46 paid**.

**Why size is NOT the lever.** Reward scores `discount ** ticks * size` and
losses scale with size too, so cutting `quote_size` scales both sides equally
and leaves the ratio unchanged. What changed the ratio is the taker share.

**What is not yet established:** why the taker share doubled between 08-15 and
08-16. Candidates, none tested: more concurrent positions (`max_markets` 8 ->
20) meaning more of them reach the 900s inventory clock; thin books that cannot
be exited passively at all (measured separately -- a 19c spread with our exit at
the touch and the only bid 19 cents away); and the pre-kickoff flatten firing
more often. **Do not act on any of these without measuring first** -- six
theories died during this session.

**The immediate implication is a risk decision, not an engineering one.** The
bot is currently losing more on trading than it earns in reward, on the numbers
that are trustworthy, and the trend is one-directional across four days.

## The 900s inventory clock now looks like the expensive one -- reversing an earlier read

Measured 2026-08-17T12:3xZ on 203 fills, against the 92 that produced the
earlier section "Crossing at 900s vs the pre-kickoff flatten: no case for
waiting". **That section's conclusion no longer holds on the larger sample.**
It is superseded rather than deleted, because it was used to keep the current
policy.

Method differs from that section and is stated so the two are not confused:
every fill the bot sends is now a PURCHASE (FINDINGS 6i -- two intents, never
four), so a matched YES+NO pair settles at exactly 1.0000 and per-contract P&L
is `1 - paid`, with no intent-inversion ambiguity at all.

```
bucket                              n   total/contract    median
crossed <900s (kickoff-driven)     12        -0.4705     +0.0500
crossed >=900s (inventory clock)   29        -2.1825     -0.1898
```

Crossing driven by the kickoff deadline has a **positive** median (+5c per
contract); crossing driven by the inventory clock has a **-19c** median. The
earlier reading had these the other way round at -0.075 against -0.059, on 8
and 12 samples.

**Why this is coherent with everything else measured today.** Splitting the same
49 completed pairs by how they were assembled:

```
both legs MAKER            7 mkts   paid 0.9714/pair   +2.9c per pair
mixed (one leg crossed)   40 mkts   paid 1.0620/pair   -6.2c per pair
```

A pair bought entirely passively is profitable; a pair completed by crossing is
not. The 900-second clock is precisely the thing that ends passive completion
early and moves a market from the first bucket to the second. 40 of 49 pairs --
82% -- were completed by crossing.

**What it does NOT establish.** The two buckets are confounded exactly as the
earlier section warned: the sub-900s group is near-kickoff by construction. That
confound was used to explain why sub-900s looked *worse*; it now looks *better*
despite it, which strengthens rather than weakens the reversal, but does not
remove it. n=12 against n=29 with per-contract variance far larger than the
difference in totals.

**This is not a tuning parameter.** `assert_authorized` pins
`max_inventory_age_seconds` to exactly 900 and `force_exit_before_start_seconds`
to exactly 300, and the bot refuses to arm otherwise. Changing it edits the
authorized commissioning envelope, and the 900s clock is a risk control as well
as an exit policy -- it bounds how long inventory is held at all. Removing it
means a position can be carried until the pre-kickoff flatten, which under the
current 2-hour selection horizon is up to ~2 hours, and up to ~6 hours if that
horizon is reverted.

**The decision, stated plainly:** the evidence now favours letting the passive
exit run to the kickoff deadline, and the cost of being wrong is holding
inventory longer. That is an operator risk decision, not a code change.

## Settlement, priced against the venue's own resolutions
## (first reading -$21.12 -- SUPERSEDED the same day, see the correction below)

Every earlier attempt to answer "what happens if we ride to settlement" was an
inference. This one is not. The venue publishes no settlement rows, but it
publishes the answer: a resolved market carries `outcomes` and `outcomePrices`,
and the winning outcome prices at exactly `1`. Our fills already record
`outcome` -- the side we hold. So for any position that went to settlement,
payout is `qty` if our side won and `0` if it did not. No model, no proxy.

`scripts/accounting.py --settlements`, run 2026-08-17 against the live ledger:

```
priced           45 markets  (14 won / 31 lost)
staked                98.14
returned              77.02
settlement P&L       -21.12
win rate              31.1%     (break-even 46.0%)

worst:  -8.15  Teodor Lefter     qty 10.00  paid 8.15 -> 0.00
        -4.64  Dominik Matic     qty  8.42  paid 4.64 -> 0.00
        -4.50  Maria Andrienko   qty  5.00  paid 4.50 -> 0.00
```

We paid 46c on average for these and they came in 31% of the time. That is a
21.5% loss on stake, and it is a real cash number, not `realized_pnl`.

**The sample is selected, and that is the whole caveat.** These 45 are not a
random slice of our trading -- they are the positions the exit ladder *failed to
close*. A position reaches settlement precisely when nobody would take the other
side at a price we would accept, which is disproportionately when it is moving
against us. So -$21.12 is what riding cost on the trades that were hardest to
exit; it is **not** the counterfactual for a policy of deliberately riding
everything, which would include the easy-to-exit winners too. Read it as an
upper bound on the damage, not as an estimate of the policy.

**What it does settle:** the "just let them ride" option is not free money.
Whatever the exit policy costs in crossed spreads, the positions that already
ride are losing more than that per dollar staked. Anyone proposing to hold
longer has to explain why the held sample would look different from this one.

### The cross-check that makes the number trustworthy

The report reconciles three independent sources and prints the gap:

```
equity_change = -(paid for fills) + settlements + rewards_paid
```

Marked equity is ground truth; fills and reward come from the account; that
leaves settlements derivable by subtraction. Derived-by-subtraction and
measured-from-resolutions must agree, and a gap means fills are missing from the
ledger. Both figures are *deltas between snapshots* -- the first version of this
compared a 25-minute window against an all-time total and printed a $31 "gap"
that was pure units error. A zero-length window now correctly reports a zero
gap.

Resolutions are final once published, so they are cached in `data/resolutions.json`
and only unseen markets cost a venue call. This matters: analysis shares the one
4 req/s governor with the live runner, and an earlier version of this analysis
triggered venue rate limiting by walking trade history while the bot was quoting.

**16 positions could not be priced**: 11 not yet resolved, 5 with no `outcome`
recorded on the fill. The second group is the remaining coverage hole.

## The on-droplet test suite is only a gate if `tests/` was deployed too

`check_sync.sh` compared `src/` and nothing else, so `tests/` drifted silently.
Deploying the `isAggressor` fix on 2026-08-17 then produced this:

```
FAIL: test_a_fill_only_the_venue_saw_still_reaches_the_stop
AssertionError: 0.0 != -3.05 within 2 places
```

Correct new code, failing on the droplet and passing locally. The droplet's copy
of that test predated the fix and built its trades with no `isAggressor` key, so
the new `leg = aggressor if isAggressor else passive` read `passiveExecution`,
found nothing, recorded no fills, and reported `realized_pnl` of 0.00.

**The false failure is the benign direction.** The reverse is the dangerous one:
a stale test that no longer exercises the deployed path *passes*, and the gate
CLAUDE.md requires before a restart certifies nothing at all. There is no
warning when this happens -- the suite just prints OK.

`check_sync.sh` now diffs `tests/` as well and fails on drift. Sync `tests/`
whenever you sync `src/`; `scp` of individual files makes forgetting the default.

### CORRECTION, same day: -$21.12 became +$5.90 once the ledger was repaired

The number above was measured hours before the `isAggressor` fix reached the
droplet, so it was computed on a ledger that was missing about 30% of its fills
-- and the missing ones were overwhelmingly the **passive** side. Within twenty
minutes of deploying the fix and restarting, the same command reported:

```
                        before repair      after repair
ledger fills                    212               300
  MAKER / TAKER                        163 MAKER / 137 TAKER
  BUY_LONG / BUY_SHORT                 146 / 146  (exactly balanced)
closed pairs             49 mkts -12.65    89 mkts -32.60
settled positions        45 mkts -21.12    12 mkts  +5.90
```

**The mechanism is the whole point.** A missing passive fill is a missing *leg*.
Every position whose hedge never reached the ledger looked like a naked bet
carried into settlement, when the account had actually been flat on it all
along. Repairing the ledger moved 33 markets out of the settled bucket and into
closed pairs, and the settled bucket flipped from a $21 loss to a $6 gain.

**So the conclusion drawn above -- "riding is not free money, the positions that
already ride are losing" -- is not supported.** It was an artefact of counting
one leg of a two-legged position. The honest statement is that we do not yet
know what riding costs, because we have never had a complete ledger to measure
it on. Do not carry the -$21.12 forward, and do not carry forward the win-rate
comparison against break-even either; both were computed on half-positions.

**What survives.** The *method* is sound -- resolutions are published, the
winner prices at 1, our fills record the side we hold. The mechanical part was
never in doubt. What was wrong was its input.

**This is also the clearest possible demonstration of the fill defect's cost.**
The bot was not mis-trading. It was mis-*recording*, and every number built on
top -- `realized_pnl`, the loss stops, the settlement study, the case for
changing the exit policy -- inherited the error and pointed the wrong way.
Nothing measured before 2026-08-17T13:20Z on this ledger should be trusted.

**Still incomplete.** `fetch_executions` reads two pages of 100 activities per
sweep, so the backfill reaches back only as far as recent history; older gaps
persist. 7 markets holding $19.00 of cost still carry no `outcome` and cannot be
priced at all. Re-measure once the ledger stops moving -- and check that it has
stopped before quoting any figure from it.

## The venue's fill history is the ACCOUNT's, and replaying it forges positions

Attempted 2026-08-17 to repair the three days of fills written before the
`isAggressor` fix. Walking the venue's full activity history and replaying it
through `apply_execution` did work -- 300 fills became 1495 -- and it was still
the wrong thing to do, for two reasons that were not obvious beforehand.

**1. There is no field that says which bot placed a fill.** The history reaches
back to 2026-06 and is the *account's*, so it imported ~700 markets of maker-lab
trading. The two are trivially distinguishable by eye and not at all in code:

```
2026-07-03 .. 07-20   ~270 fills    0-4% MAKER    1000+ contracts/side  (maker-lab)
2026-08-09 onward     ~780 fills   50-100% MAKER     5-10 contracts/side  (this bot)
```

A liquidity-bot-only ledger cannot be reconstructed from venue history alone.

**2. Replaying fills rebuilds POSITIONS, and that is the dangerous half.**
`apply_execution` maintains `execution_positions`. Replaying the fills of a
market that has since settled leaves the ledger holding a position the venue
does not have. The result: **345 non-zero positions** in markets settled days
or weeks earlier, against a venue reporting zero.

```
aec-cs2-nexo-gel-2026-08-12       net_qty = -25.00     (settled 5 days earlier)
aec-wtt-giopic-chalut-2026-08-10  net_qty =  -6.57
```

That is not a reporting error. **A runner that believes it holds inventory
plans exits for it**, so restarting would have placed real orders against
positions that do not exist. Caught before the restart; ledger restored from
the pre-backfill snapshot, bot restarted flat and quoting.

**What this rules out.** "Just replay the history" is not available as a repair
for the pre-fix days. A safe version needs a mode that writes fills *without*
touching positions, which the ledger API does not expose today. `--dry-run` is
safe and tells you the shape of what is there; a real run is not.

**The loss stops were never at risk**, checked at the time: the stop reads
`ledger.realized_pnl() - self._realized_baseline`, and the baseline is
re-snapshotted at startup and daily, so imported history is absorbed rather
than tripping anything.

**And note what this does NOT block.** Fill recording going forward was already
fixed and verified. This only concerns repairing 2026-08-14..16 retrospectively,
which affects historical reporting and nothing the bot does next.

## Reward PAID/PENDING is not cash. The transfer history is

`/v1/incentives/earnings` reports a `status` of PAID or PENDING per row, and
`scripts/rewards_report.py` totals them. On 2026-08-17 that read PAID $37.91 /
PENDING $102.79, with nothing marked PAID after 08-10 -- which was read, by me,
as "the venue has not paid us in a week". **That reading was wrong**, and the
operator caught it by simply looking at their balance.

Actual payouts land as `ACTIVITY_TYPE_TRANSFER` rows in
`GET /v1/portfolio/activities`, carrying `accountBalanceChange` with
`description = "liquidity reward payout"`:

```
2026-08-11T15:35:07   $32.06  COMPLETED   liquidity reward payout
2026-08-17T14:20:17    $5.85  COMPLETED   liquidity reward payout
2026-08-17T14:26:23    $5.83  COMPLETED   liquidity reward payout
```

### ...and the correction to THAT, an hour later: they reconcile exactly

The paragraph above originally concluded the two views "do not reconcile and are
not meant to". Wrong, and worth keeping visible because the mistake is easy to
repeat. Watched for another twenty minutes, a third payout landed and the totals
matched to the cent:

```
2026-08-11T15:35   32.06
2026-08-17T14:20    5.85
2026-08-17T14:26    5.83
2026-08-17T14:33   42.77
                  ------
                   86.51   ==  the PAID column, exactly
```

**The `date` column is the date reward was EARNED, not the date it was paid.**
So "nothing PAID since 2026-08-10" never meant "no money has arrived since
08-10" -- it meant "reward earned after 08-10 has not been paid yet", which was
true when read and stopped being true while it was being read. PAID is cash
received and it is reliable; the trap is reading its date as a payment date.

Payouts also arrive in bursts, not on a daily schedule: three landed within
thirteen minutes on 08-17, clearing two full earning days at once. A single
reading of this table says nothing about whether payouts have stalled. Compare
two readings, or read the transfer history, which is what
`accountBalanceChange.description = "liquidity reward payout"` gives you.

The same activity stream is also the only place deposits appear:

```
2026-08-10  $50  COMPLETED  Polymarket Deposit
2026-08-11 $300  REJECTED   Apple Pay
2026-08-11 $400  COMPLETED  Apple Pay
```

Deposits are what make a true P&L possible at all -- `equity - deposits` needs
no ledger and no model. **Do not compute it from a partial page walk.** The read
above covers 8 pages; there was trading in 2026-06/07, so earlier deposits
almost certainly sit outside it, and a bottom line drawn from an incomplete
deposit list is worse than no bottom line.

**Cost of getting this wrong.** Paging 60 pages to find these rows tripped venue
rate limiting while the bot was quoting -- the second time in this project, both
times mine. Six pages was enough to find the rows; the query is bounded at eight
now. The runner was unaffected (uptime unbroken, escalation never past
`attempt=3`, the 120 concurrent errors were ordinary `book_stale`), but that was
luck rather than design.

**To do:** teach `rewards_report.py` to read the transfer history and show cash
received beside accrued, and pull the full deposit history at a moment when the
bot is stopped. See NEXT.

## True P&L without the ledger: equity minus funding. The bot is ~break-even

Measured 2026-08-17T15:00Z, from venue cash movements only -- no ledger, no
model, no settlement data. This is the one P&L calculation today that did not
collapse when the ledger turned out to be wrong, and it should be the default.

**Account-wide** (complete history; the activity walk reached `eof` in 20 pages):

```
equity now                420.23
deposits in              4408.00
withdrawals out          1798.50
remediation credits       224.60   (two goodwill payouts, 06-27 and 07-02)
net funding in           2834.10
reward received           117.77

all-in P&L              -2413.87
trading alone           -2531.64
```

That loss is almost entirely **pre-liquidity-bot**: maker-lab traded 06/07 at
1000+ contracts a side against this bot's 5-10.

**This bot alone.** Its era starts 2026-08-10, and the identity closes:

```
equity_now = equity_at_08_10 + funding_since + reward_received + trading_since
    420.23 = E + 450.00 + 91.94 + T
         T = -121.71 - E
```

`E >= 0`, so **trading since 08-10 has lost at least $121.71** -- far worse than
the -$29.88 the ledger reports for 08-14..17, which places roughly $90 of loss
in 08-10..13, the days the ledger does not cover at all. Independent
confirmation that the ledger understates losses, arrived at without using it.

Total reward earned 08-10..16 is $140.70, so:

```
net = T + reward = 18.99 - E
```

| E (equity on 08-10) | net result |
|---|---|
| 0 | +18.99 |
| ~19 | break-even |
| >19 | negative |

There were $250 of deposits over 08-05..09 and the account was trading into
08-10, so E was very probably well above $19. **The honest read is
roughly break-even, likely slightly negative** -- reward very nearly covers
trading losses but does not clearly beat them.

**Do not restate this as "+$24".** An earlier figure that afternoon compared
08-14..17 ledger trading against 08-14..16 reward and got +24.31; it missed the
worse 08-10..13 trading entirely because those fills are not in the ledger.

**E is not recoverable.** Equity on 08-10 cannot be read back from the venue,
which is precisely why `accounting.py` writes dated snapshots -- from now on
this question is a subtraction, not an inference. Take a snapshot before any
change whose effect you will want to measure.
