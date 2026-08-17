# Next session — outstanding fixes

## 0. A parked position bricks the runner until the kickoff window (2026-08-17)

Hit live during a routine deploy restart; full write-up in `BUGFIXES.md`.

Shutdown parks a position rather than dumping it into a thin book, which is
right. Startup then enters `RECOVERY_ONLY`, lives ~18 seconds, cannot fill a
passive exit in that time, parks again, and returns exit 3 — the code the
supervisor never restarts. The only mechanism that clears the position is the
300s pre-kickoff flatten, and reaching it requires staying alive. So the bot is
down until the window opens or the market settles. A $2.80 position took the
session offline for ~15 minutes.

Worked around by hand: start inside the pre-kickoff window and recovery
flattens through the normal ladder.

**The fix is one of two, and it is a judgement call:**

1. Let `RECOVERY_ONLY` stay alive until it can flatten, bounded by the kickoff
   deadline it already honours. Closest to intent; means a recovery process can
   live for hours holding one position and quoting nothing.
2. Give "known position, cannot flatten yet" its own exit code that the
   supervisor retries with long backoff, keeping exit 3 for genuinely
   unexplained state.

(2) is smaller and keeps exit 3 meaning what it says. Do not weaken exit 3
itself — an unexplained position must still stop the account.

## 0b. One-off deep backfill of 2026-08-14..16 (2026-08-17)

The `isAggressor` fix repaired the ledger from 13:20Z on 2026-08-17, and it has
converged — 300 fills, stable. Today is clean. **The three days before it are
not**, and they never will be without a deliberate backfill:

```
2026-08-17   181 fills   101 maker (56%)   <- the real split
2026-08-16    58 fills    22 maker (38%)   <- passive legs still missing
2026-08-15    55 fills    37 maker
2026-08-14     6 fills     3 maker
```

`fetch_executions` reads two pages (200 activities) per sweep by design, so it
repaired recent history and stopped. Everything older was written by the broken
code and still counts one leg of two.

Separately, **26 markets from 08-14/15 carry no `outcome` at all**, so their
settled positions cannot be priced even in principle — that is the $19.00
`accounting.py --settlements` reports as unpriceable.

**Until this is done, no all-time P&L figure is decision-grade.** Equity from
the venue and anything after 13:20Z 08-17 are fine.

**Do it with the bot stopped.** Paging full venue history is precisely what
triggered rate limiting before; analysis shares the one 4 req/s governor with
the live runner. A stopped bot has the whole budget and nothing to lose from
spending it. Raise `max_pages` for that single run only — do not raise the
default, and do not put history paging in `accounting.py`.

**ATTEMPTED 2026-08-17 AND REVERTED — read FINDINGS before retrying.**
`scripts/backfill_executions.py` exists and works, and running it bare was
still wrong. Two blockers, neither fixable with a flag:

1. Venue history is the **account's**. It reaches back to 2026-06 and imported
   ~700 markets of maker-lab trading; no field distinguishes the two bots.
2. `apply_execution` rebuilds **positions**. Replaying a settled market's fills
   left the ledger holding 345 positions the venue does not have, and a runner
   that believes it holds inventory plans exits for it — real orders against
   phantom positions. Caught pre-restart; ledger restored from the snapshot the
   script takes automatically.

**The prerequisite is a fills-only write path** that does not touch
`execution_positions`, plus a date floor to exclude the maker-lab era. Until
both exist, `--dry-run` only. The script now warns loudly if a run leaves
non-zero positions behind.

This is historical reporting only. Going-forward accuracy is already fixed.

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

## 2. Book stability is probably the real selection lever (2026-08-17)

**Promoted from "sides that never quote", which was measured on a 25-minute
window and is superseded by the 3.8-hour numbers below.**

Resting fraction, markets present for >=80% of a 228-minute window, dividing by
each market's own active span (`scripts/resting_fraction.py`):

```
92.4%  sirvea-salnic  bid    29 orders    3 price-cancels
85.2%  chitsa-yinhua  bid    30 orders   21 price-cancels
68.3%  yujkim-ziyzho  bid    58 orders
41.9%  reikom-alejon  offer  23 orders
 3.7%  zhixia-ryunak  bid    13 orders   19 price-cancels

mean 62.8% (13 sides)
```

The spread is **between markets, not between busy and quiet sides**. Order count
barely predicts resting fraction. What does track is how often the touch moves:
`zhixia-ryunak` logged 19 `resting_price_left_the_touch` cancels and rests 3.7%;
`sirvea-salnic` logged 3 and rests 92.4%, its cancels being
`side_no_longer_desired` from the economic gate instead.

**Hypothesis:** a book that moves every 40 seconds cannot be rested in, so its
projected reward is never realised however good the projection was. Selection
ranks on projected reward and takes no account of whether the book holds still
long enough to collect it.

**This is a hypothesis from two markets, not a result.** Before changing any
selection code, correlate touch-move rate against resting fraction across a
full slate. If it holds, rank on stability alongside projected reward.

Also still open from the earlier measurement: 13 of 40 sides never quoted at
all, sitting on `target_not_met`. Confirm whether that is stable across slates
or a property of one late-evening card. Same gate as item 7.

## 1b. Root cause of the untrustworthy accounting, and the order to fix it

Found 2026-08-17. Everything below TOP PRIORITY depends on this, because every
strategy question is judged against P&L and P&L is currently wrong by 3-10x.

**The mechanism is one filter.** `AsyncAccountSnapshotGateway.fetch_executions`
drops any execution whose resolved client id starts with `venue:`:

```python
if str(execution.client_order_id).startswith("venue:"):
    continue
```

That filter is CORRECT and was added for a real bug -- a trade has two legs, the
venue reports both, and without it every trade was recorded twice, which made
realised P&L read flat on a market holding risk (2026-08-15, valorant-ff-ep).

The defect is that `resolve_client_id` also returns `venue:` for **our own**
fills when we have no order record for them -- an operator's manual close, or an
order whose record was lost. So "the counterparty's leg" and "our leg we cannot
attribute" are indistinguishable, and both are discarded.

**Everything traces back to it:** the -$22.41 of markets with an entry and no
exit; `realized_pnl` reading -$143.59 against a real -$48.69; and the
riding-vs-crossing comparison, where 40 of 57 "ridden" markets had a TAKER first
fill -- impossible for a post-only entry, so their entry was simply never
recorded.

### Order of work

1. **Identify our own leg without relying on our order records.** Needs one
   inspection of a real trade payload for an ownership marker. Cheap, but do it
   when the venue is healthy -- analysis shares the 4 req/s governor with the
   runner and paging history has already triggered rate limiting twice.
2. **Record every fill of ours, attributable or not.** Flag the unattributed
   ones rather than dropping them, so double-counting stays impossible while
   nothing of ours is lost.
3. **Snapshot positions and equity around settlement windows.** The venue
   publishes no settlement rows at all (240 activity rows, every one
   ACTIVITY_TYPE_TRADE), so settlement can only be derived. Account-level
   derivation already works in `scripts/accounting.py`; per-market needs
   position snapshots on both sides of a resolution.
4. **Replace `realized_pnl` with the matched-pair calculation.** Written and
   validated in `scripts/accounting.py`: a YES+NO pair settles at exactly
   1.0000, so a closed pair realises `1.00 - paid`, needing no direction,
   outcome or settlement data. Do this AFTER 1-3, and only once reconciliation
   confirms it -- it is the number the $40 loss stops read.
5. **Fail loudly when the model and the account disagree.** The 3x error sat
   there undetected for days. Nothing compared the ledger to the balance.

### Not to be done before this

Reverting the 2-hour horizon, changing the 900s inventory clock, and the
riding-vs-crossing question are all queued and all wait on this. Each was
measured tonight against P&L, and every one of those measurements is suspect
until the accounting is trustworthy. `scripts/accounting.py` recorded its first
baseline snapshot on 2026-08-17; the second run makes settlements exactly
derivable for the window between them.

## 2b. Markets can be very slow to place their first order (2026-08-17)

**Rewritten after the diagnostic. The original version of this item blamed
`book_stale` and was wrong** -- the fourth time in one session that reading
`book_stale` as a fault produced a false diagnosis. It is kept in this form
rather than deleted because the measurement underneath it is real.

**What is real.** Over the 21:47Z session, 7 of 34 markets serviced held a slot
for 54 to 130 minutes without placing a single order -- roughly 680
market-minutes earning nothing, while 20 markets were bound and 8 resting.

**What was wrong.** They were not incapable of quoting. `hanlu-badidr` and
`rocjan-listom`, two of the seven, placed their first orders at 02:02:09Z --
minutes after being measured as "never placed" -- with proper `liquidity-`
client ids, correctly recorded in the ledger. They were slow, not broken.

**`book_stale` was not the cause.** A `[book-stale-why]` diagnostic added to the
critical path reports, on every one of these markets:

```
age=0.0  refresh_due_in=-2.3  fresh_enough=True  replanned=False
```

The book is **zero seconds old** by the time the critical is handled. The
planner saw a stale book, the handler refreshed it, and the replan fires
immediately -- the `d748f03` fix working exactly as designed. `book_stale` here
is the bot looking, not the bot blocked, which is what FINDINGS already says
about that signal.

**So the open question is narrower and economic:** why do some markets take an
hour or more before the economic gate first admits a side? The blockers seen on
idle markets are `target_not_met` and `outside_reward_cutoff`, both correct
refusals in themselves. What is not established is whether an hour of refusal
reflects the market genuinely not paying, or a gate that is too slow to notice
when it starts paying.

**Next step:** log the blocker on every pass for one slow market from bind to
first placement, and see whether the gate flips once or oscillates. Do not touch
`max_book_age_seconds` -- that bound is not involved.

**Note also:** a restart on 2026-08-17T02:05Z produced 13 markets quoting within
90 seconds, against 8 after nearly four hours on the previous selection. That
suggests slate composition matters more than anything in the order path, which
points back at item 2.

## 3. Re-price latency -- WEAKENED, probably not worth doing (2026-08-17)

Kept rather than deleted because the reasoning was sound and only the magnitude
was wrong; deleting it invites re-deriving it.

Written from a 25-minute window and a 6-sample gap measurement:
`apply_one` executes one mutation per pass, so a cancel and its replacement land
on separate visits, and the gap is two sweep intervals (median visit 35.2s,
median gap 69.4s, 582 and 6 samples respectively). That part still holds.

**What does not hold is the cost.** Over 3.8 hours the side that re-priced 30
times rests 85.2%, the one that re-priced 58 rests 68.3%, and the one with 29
rests 92.4%. The original claim -- roughly a third of earning time lost -- came
from a single 25-minute sample.

Same-price churn was also checked, since one market's log looked like a
textbook flapping-touch bug: 119 re-prices across the session, **12 returned to
the same price (10%)**, 14 minutes of dark time out of 228. Dwell hysteresis
would recover minutes per hour and cost resting at a stale price, which reward
discounts per tick.

**Do not start this.** If the mutation path is ever touched, the least invasive
candidate is revisiting a market right after it mutates rather than making it
wait a full sweep -- that weakens no gate and only reorders the queue.

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

## 9. Housekeeping carried out of 2026-08-16

- **Off-droplet backups.** `scripts/backup_ledger.py` runs nightly at 04:17 UTC
  and keeps 30 snapshots, but they sit on the same droplet and do not survive
  losing it. Pulling one down is still manual. The credentials file has no
  backup at all beyond the copy at `D:\polybot\maker-lab\credentials.py`;
  four values belong in a password manager.
- **`/root/maker-lab/credentials.py` is mode 644** -- world-readable on the
  droplet, holding a funded API secret. Should be 600.
- **Nothing starts the bot after a droplet reboot** (verified 2026-08-17).
  There is no systemd unit and no `@reboot` crontab entry -- the only cron line
  is the nightly ledger backup. `supervise_commissioning.sh` restarts the
  *runner*, but nothing restarts the *supervisor*, so a reboot leaves the bot
  down silently until someone notices it stopped earning. Deliberate is fine;
  unexamined is not, and auto-start has its own hazard (a boot into a non-flat
  account, which item 0 shows the runner handles badly). Decide and write it
  down either way.
- **Passphrase on the SSH keys.** Rynn-PC's `makerlab_droplet` key has none.
  `ssh-keygen -p -f ~/.ssh/makerlab_droplet`, then load ssh-agent, or
  non-interactive deploys break.
- **`MarketReadOnlyStream._dropped_books` is write-only.** Incremented on every
  unparseable book and never read by anything, though its comment claims "a
  rising number is visible". It should be in the summary; its absence forced a
  diagnosis by distribution on 2026-08-16.
- **`reconciler.py` has an unreachable `raise`** immediately after the
  `minimum_entry_spread` check -- dead code left from a removed spread-hysteresis
  validation.
- **Repo visibility unresolved.** `docs/MACHINE_SETUP.md` carries
  `<DROPLET_HOST>` rather than the address because it is not known whether the
  GitHub repo is public. If private, fill it in; if public, consider that the
  repo now documents the loss stops, safety gates and a live P&L defect.

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
