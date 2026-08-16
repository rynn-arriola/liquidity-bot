# Next session — outstanding fixes

## TOP PRIORITY (2026-08-16): the $40 stop is computing from one-sided fills

**Measured 2026-08-15 06:53Z.** `realized_by_market()` returns:

```
tkamak-tsokos   +3.26     <-- the trade actually LOST $1.55
karfriwilvin    -2.93     <-- opening buy, no close recorded, market is flat
derand-savvol   -2.43     <-- same
realized_pnl() = -2.70    <-- noise, not P&L
```

`tkamak-tsokos` is off by **$4.81, in the direction that hides losses**: the
opening sell of 5 @ 0.65 was recorded as $3.25 of proceeds and the buy-back at
0.96 was never recorded at all, so a loss reads as a profit.

Every trade in that table is one-sided. Causes, fix together:

1. **Closing fills from IOC exits never reach `execution_fills`.** The 06:22Z
   shutdown flatten executed at the venue (confirmed in
   `portfolio.activities`) and left no fill row.
2. **Recorded fills sit in two price frames.** `SELL_*` intents are read by the
   venue at `1 - price`, so even a complete pair can compute wrong.
3. **There is no settlements table at all** (added 2026-08-16). The ledger has
   intents, events, orders, fills, positions, position_reconciliations and
   reward_earnings -- nowhere to record a payout. maker-lab had
   `settlements(pnl)`. Every position that resolves is therefore booked as a
   total loss of its entry cost regardless of outcome.
4. **A manual close produces this by construction** (added 2026-08-16). It
   carries no bot client-order-id, so `fetch_executions` cannot attribute it.
   Confirmed on `melolh-kololh`: bot bought 5 @ 0.28, operator closed by hand,
   ledger reads -1.39 on a position that is flat.

**Measured 2026-08-16.** 12 markets have exactly one fill, position flat, and
realized loss equal to the entry cost -- **-$22.41 unverifiable**, inside a
reported total of +$5.33.

**Why it is first:** two reasons, and the second is newer. It is the only
automated protection on live capital, unreliable in *both* directions -- a fake
profit masks a real loss and never fires; a fake loss halts trading for nothing.
And **it makes every other measurement unweighable**: a contract bought at 0.60
that settles at $1 is +$2.00 while the books say -$3.07, so nothing downstream
-- resting fraction, exit policy, selection -- can be judged against P&L until
this is fixed.

## 2. Sides that never quote at all (2026-08-16)

Measured over a 25.6 min live window with `scripts/resting_fraction.py`: only
**27 of a possible 40 sides** (20 markets x 2) ever placed an order. The other
13 sat on `target_not_met` -- the venue pays only once a side reaches
`targetSize`, so the bot correctly declines to quote below it.

That is bigger than anything in the order path. Fixing the re-price latency
(item 3) recovers about a third of the time on 27 sides; getting more sides past
the reward gate addresses 13 sides that currently earn nothing at all.

**This is a SELECTION change, not an execution one.** Rank candidates by whether
their sides actually clear `targetSize` now, rather than discovering it after
binding a slot. Related to item 7 below (`target_not_met` churn), which is the
same gate seen from the resting side.

**First step:** confirm the 13 is stable over hours rather than a property of
one late-evening slate. `scripts/resting_fraction.py` reports it.

## 3. Re-price latency: a cancel and its replacement are two visits (2026-08-16)

Measured live, two independent numbers that agree:

```
per-market visit interval:  median 35.2s  (582 samples, 22 markets)
cancel -> replace gap:      median 69.4s  (6 samples)
2 x 35.2 = 70.4  ~=  69.4
```

`apply_one` executes at most one mutation per pass, so a
`resting_price_left_the_touch` cancel lands on one visit and its replacement on
the next. A side re-pricing six times in 25 min rested **66.9%** against 88.9%
for a side that never re-priced -- roughly a third of its earning time.

The one-mutation limit is deliberate and its reason is sound: several mutations
through a 4 req/s governor take real time, and a book that ages meanwhile must
report `book_stale` rather than quote on stale data.

Candidates, least invasive first:

- **Revisit a market right after it mutates** instead of making it wait a full
  sweep. Weakens no gate; only reorders the queue.
- **Issue a re-price as one visit.** Both halves already come from the same
  fresh frame, and waiting 35s for the second half is *worse* for price
  accuracy. Risk is the placement landing before the cancel confirms, briefly
  doubling that side's exposure -- needs a confirm-then-place step.
- **Shorten the visit interval.** ~1.6s of sweep per market at 22 markets. What
  dominates that 1.6s is **unmeasured** -- measure before changing.

**Do not start this before item 1.** Its payoff is measured in reward, and
reward cannot be weighed against a P&L that is currently wrong by -$22.41.

## 4. hard_exit_blocked_cancel_unconfirmed loop

Fired 10 times over 10 minutes on `tkamak-tsokos` and never once got a clean
book, which is why that position was still open when a deploy landed. The hard
exit must clear resting orders before it can cross, but the soft reducer
re-places every pass, so the cancel confirmation keeps losing the race.
`cancel_confirm_grace_seconds` is 5.0.

## 5. Capital deployment: max_markets is now 20, not 8

**Partly done.** This item was written when `max_markets` was 8 and described
raising it to 30. It is **20** today; the 8 was a per-connection WebSocket
subscription limit (10 per connection: 1 orders + 8 positions + 1 balance)
mistaken for an account-wide one. Connections are independent and each carries
its own quota, and 29 subscriptions were later sustained.

What remains open is whether 20 is the right number, and that is now an
**earning** question rather than a plumbing one -- see item 2. Measured
2026-08-16, only 27 of a possible 40 sides ever quoted, so adding slots may
just add idle ones. Establish why sides sit on `target_not_met` before raising
it further.

## 6. Discord alert severity

Normal recovery paths still fire `critical=True`, so real alerts get buried.
Keep critical for: loss stops, ownership violations, retirement, abandonment,
unexplained runner failures.

## 7. Residual target_not_met churn

Small now -- a handful of cancels an hour, down from ~7/minute.

## 8. API rate

4 req/s against maker-lab's 0.3. ~2,500 placements without a ban, but untested.

---

## Fixed 2026-08-15 (all mutation-checked, deployed)

- recovery could not load a market whose game had started (`for_exit`)
- one market's fault cancelled all eight markets' quotes (scoped quarantine)
- plan/action exceptions retired one market instead of ending the session
- a one-pass spread dip cancelled resting quotes (superseded by the 2c rule)
- a tight book is priced to exactly 2c instead of being refused
  (`entry_quote_price`) -- 50/50 and 49/50 both quote 49/49; a book already
  wider than 2c is quoted at the touch, untouched
- a one-pass reward-eligibility flip cancelled every quote (90s dwell), and
  that path now records a blocker instead of writing nothing
- shutdown parks a position on its opposite-side exit instead of liquidating
  it at any price (`fdd20cd`)

Written 2026-08-14 at the end of a long session. Ordered by what actually
blocks earning. Every claim here is measured; see `FINDINGS.md` for the data.

**Current state**: running on `4cc4520`, flat, 0 unresolved intents, no
crashes. Quote size 5, 8 markets, $40 realised stop, reward-cost gate OFF
(maker-lab's entry rule: 2c spread plus risk caps, nothing else).

---

## 1. Quote churn — RESOLVED 2026-08-15

**The cancels were never planned actions.** They came from
`_quarantine_working_orders`, which does not log as an `[action]` — which is
why the cancel tally read zero while quotes visibly vanished. It cancelled
*every bound market*, driven by any single market reporting
`execution_state_unknown`.

Quarantine now takes a slug. Global remains for genuinely account-wide faults
(stream reconnect, rotation).

Measured before: PLACE 2597 / CANCEL 2596, ~1.5s apart, nothing rested.
Measured after (01:44–01:50Z, 8 markets): PLACE 29 / CANCEL 7, twelve orders
resting concurrently, median age 181s. Two of the seven cancels were the
`target_not_met` sign-flip below; five were legitimate repositioning.

**Residual, much smaller**: `desired_sides` still drops a side on a bare
`expected_net <= 0` sign flip. Hysteresis mirroring `spread_hysteresis` would
remove the last ~7% of churn. Not urgent now that quotes rest for minutes.

## 1b. One market's error no longer stops the session

Three paths took all eight markets down for one market's fault, all fixed
2026-08-15 and mutation-checked:

- recovery bound a market whose game had started → `no longer pregame` →
  `startup_unavailable` retry loop for 2h20m with a position held.
  `_refresh_contracts(for_exit=True)` skips gates that exist to stop us
  *opening* risk; recovery markets are marked retired so they exit, never quote.
- a planning exception on one market → whole-session terminal → now retires it.
- an unexpected action exception on one market → whole-session terminal → now
  retires it.

`RuntimeInvariantViolation` stays terminal everywhere: it is about the process,
not a market. Retirement stays bounded — ten rounds per market, and a full
slate of retirements still stops the session, so a systemic fault is not
silently absorbed.

## 1c. Old quote churn detail (kept for reference)

**Symptom**: `[action] ... kind=CANCEL reason=side_no_longer_desired` two to
three seconds after `PLACE_POST_ONLY`, repeatedly, same market and price.

**Cause**: with the reward-cost gate advisory, `expected_net_by_side` sits
near zero and flickers sign between passes. `_flat_plan` drops any side whose
`expected_net <= 0` from `desired_sides`, so the side leaves and rejoins on
consecutive passes.

**This is a second, distinct churn cause.** The first — our own contract
flipping `target_not_met` eligibility — was fixed in `rewards.py` (see
FINDINGS 6e) and confirmed: a quote rested four minutes instead of 500ms.

**Fix**: hysteresis on `desired_sides`, mirroring what `minimum_entry_spread`
already does with `spread_hysteresis` — a resting side should need a clearly
negative `expected_net` to be dropped, not merely a sign flip. `_flat_plan` in
`reconciler.py` already receives `frame.working_orders`, so it knows which
sides are resting.

**Why it matters**: reward pays for resting time. A quote cancelled after two
seconds earns nothing, and this is the last known thing between the bot and
actually collecting.

## 2. Discord noise — recovery alerts are marked critical

**Symptom**: constant alerts, most of them these two:

```
Liquidity Bot found identified one-market state from a prior process...
Liquidity Bot hit a recoverable runtime failure and proved flat; restarting.
```

**Cause**: they fire on every restart that finds leftover state, and they are
sent `critical=True`. Roughly fifteen restarts happened during the
2026-08-14 session, each after a `kill -9` deploy.

**Fix**: these are a normal, healthy recovery path, not faults. Send them
non-critical. Keep critical for: loss stops, ownership violations, retirement,
abandonment, and unexplained runner failures.

**Related**: alert volume was already reduced once for the idle scan (which
claimed "new risk is disabled until reconnect and reconciliation pass" on a
perfectly healthy bot). Same class of problem — an alert that cries wolf on a
normal state trains the operator to ignore Discord.

## 3. Graceful stop — deployed, never verified

`_venue_proves_flat()` was added so shutdown exits as soon as the venue shows
no orders and no position, rather than waiting on `risk_state_known()` (false
35% of wall-clock time during ordinary cancels).

**Deployed but never timed.** Verify: `kill -TERM` a flat runner and measure.
Before the change it exceeded two minutes and never completed.

**Why it matters**: slow stops are the root of the whole restart-noise cycle.
Slow stop → operator uses `kill -9` → leftover orders and intents → recovery
run on next start → the two alerts in item 2. Fix the stop and that entire
class disappears.

---

## Bigger levers, not bugs

### Capital cannot be deployed at the current shape

```
5 contracts x 8 markets x 2 sides x ~$0.50 = $40   of $385.88 available
```

`max_markets = 8` is the binding constraint, and it comes from plumbing: the
private WebSocket accepts 10 subscriptions per connection, and positions must
be scoped one market per subscription. 1 orders + 8 positions + 1 balance = 10.

Connections are independent and each carries its own quota (measured, FINDINGS
7), so 30 markets is 4 connections. maker-lab ran 30. That is the single
change that most closes the trade-volume gap.

### API rate is 13x maker-lab's

We run 4 req/s. maker-lab settled at **0.3 req/s** and its config carries
`API_BAN_COOLDOWN_MIN = 5`, `API_BAN_COOLDOWN_MAX_MIN = 40` and the comment
*"18/min; we measured 2.5/min"* — a project that got rate-limited and backed
off hard. We have made ~2,500 placements at 4 req/s without a ban, but this is
an untested edge we are sitting well past.

### Unresolved measurements

- **`live` vs `day_of` split of the $70.92.** The venue exposes no period
  field (confirmed against the published reference). Fill-timing cannot
  recover it — half the money came from markets that never filled. This
  decides whether a pregame-only executor can reach those numbers at all.
- **maker-lab's $40.09 ledger gap** and liquidity-bot's unexplained **+$0.64**
  on 2026-08-13. Both still unexplained. Neither blocks a small live test;
  both block any profitability claim.
- **The 0.5 reward haircut** still has no evidence behind it. The 2x cost
  margin was removed and then the whole gate made advisory, but the haircut
  remains an arbitrary number.

---

## Process notes for whoever picks this up

**Deploy without `kill -9`.** Fix item 3 first; every forced kill creates the
recovery noise in item 2.

**Mutation-check every fix.** Revert the change and confirm the test fails.
Three tests written during this session passed against the unfixed code —
they asserted on a helper rather than on the value the caller actually reads.

**The gate lives in two places.** `_evaluate_rewards` in the runner *and*
`evaluate_market_opportunity` in the selector. Changing one alone does
nothing, because a market that never becomes viable in the selector is never
bound for the runner to see. This cost a deploy cycle to discover.
