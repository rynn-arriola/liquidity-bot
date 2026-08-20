# Next session — outstanding fixes

## 00. FIXED 2026-08-20: a restart forgives the day's drawdown

**Landed.** Both stops now baseline on the UTC day, from a durable ledger row
(`execution_day_baselines`), so a mid-day restart inherits the drawdown and
only a new UTC date grants a fresh budget. The hard stop's ratchet is closed
with it. Full write-up and the two mutation checks in `BUGFIXES.md`.

**Needs a deploy and a restart to take effect** -- it is runner code.

### Still open, found while fixing it

- **The summary's reward line is dead, twice over.** `paid_observed` is
  computed in `_send_summary` and only ever reaches the `[selection]` log line,
  so `attributable_paid_rewards_usd` sits at its `Decimal("0")` default and
  `ACCOUNTED NET` is really `equity delta + open mark + 0`. And even if it were
  passed, `_refresh_earnings` queries `fetch_earnings(market_slug=slug)` **per
  slug**, which FINDINGS records as returning $0 even for markets that plainly
  earned -- it must be account-wide. Fixing it also needs the bot-era boundary
  in `src/`; `_bot_era.py` lives in `scripts/` and the runner cannot see it.
- **`hard_stop_reached` does not evaluate on stale equity** (>15s). Deliberate,
  and unchanged. Do not "fix" it to fire on numbers it cannot trust.

### The original write-up, kept for the mechanism



**Above the orphan-PLACE item below.** An availability bug costs quoting time;
a stop that forgives losses costs money. Raised by the operator, who was down
on the account while both stop meters read near zero.

### What is wrong

Both loss stops baseline **per session**, and a session is one process. Ten
sessions started on 2026-08-18, so the day's budget was effectively handed
back ten times.

`SessionRiskGuard` is constructed once, in preflight:

```python
self.risk_guard = SessionRiskGuard(
    starting_equity=self.balance.marked_equity,   # stamped at process start
    max_session_loss=..., hard_stop_loss=...)
```

- **`max_session_loss` ($40)** trips on `-realized_pnl`, which is
  matched-pair only. `_rebaseline` *does* carry day-aware logic for it
  (`_baseline_day`), but that state lives on the instance, so a new process
  re-baselines regardless of what the day already lost.
- **`hard_stop_loss` ($45)** trips on `starting_equity - current_equity`.

Measured 2026-08-18:

```
window                fills   matched-pair   unmatched cost (unpriced)
THIS SESSION            37        -5.51        4.59 across  1 market
TODAY                  162       -12.47        9.35 across  3 markets
ALL TIME               529       -46.55       67.38 across 29 markets
```

The stop was reading -$5.51 of a $40 budget -- 14% -- on a day that had lost
$12.47 by that measure and $46.55 lifetime.

**The ratchet is the dangerous part.** Lose $30 of equity, crash, restart:
the hard stop's baseline re-stamps at the *lower* equity, that $30 is
forgiven, and it takes another $45 from the new floor to fire. A bad day can
step down indefinitely with every individual step under the limit. That is
the shape that matches "account is down, stops show nothing".

### Correct one thing first

An earlier note in this file said settled positions are invisible to "the
stop". That is true of the **$40 session stop** and false of the **$45 hard
stop**: the hard stop is equity-based, and a position settling worthless
moves marked equity, so it does see it. The settlements gap and this item are
therefore separate problems -- fixing the baseline does not need the
settlements table first.

### Fix shape

Baseline both stops on the **UTC day**, not the process:

1. Persist the day's opening equity and realised baseline (the ledger is the
   natural home -- it already survives restarts) instead of stamping
   `starting_equity` from whatever the balance happens to be at preflight.
2. On startup, adopt the stored baseline when it belongs to the current UTC
   day, and only stamp a fresh one at the day boundary.
3. Keep the existing per-day re-baseline for a process that runs past
   midnight; that half already works.

Needs a test that a restart mid-day inherits the day's drawdown rather than
resetting it, and one proving the day boundary still grants a fresh budget.

### Also note, do not "fix" by accident

`hard_stop_reached` deliberately returns False when equity is unknown or
older than `max_equity_age_seconds` (15s). That is correct -- it refuses to
fire on stale data -- but it means the hard stop is **not evaluating at all**
during a venue outage. On 2026-08-18 plain reads failed 4 of 6 probes for a
stretch. Do not make it fire on stale equity; the exposure is that a bad
venue and a bad day can coincide, and the answer to that is the flatten
deadline, not a stop firing on numbers it cannot trust.

## 00b. The place/cancel churn -- FIXED 2026-08-19, verify it holds

**Landed.** The defect was `.get(side, 0.0)` in `reconciler.plan_market`: a
side the runner had not evaluated that pass has no `expected_net_by_side`
entry, and the default turned "we did not look" into "this is worthless",
cancelling the resting order and overriding the 90s eligibility dwell built to
prevent exactly that. Absence is now skipped and left to the dwell; an enforced
gate refusal records `min(expected_net_value, 0.0)` explicitly so a missing key
means one thing only. See FINDINGS and BUGFIXES.

**Measured effect, 3h41m of live data:**

```
resting fraction   57.3% (179 sides)  ->  85.6% (36 sides)
expected_net_not_positive             ->  0 in the cause counts
places : cancels   ~1:1               ->  18:7
bound, not quoting 7-18               ->  0-1
```

### CONFIRMED 2026-08-19T20:43Z over a full session

```
resting fraction   84.7%  (5h29m, 43 sides)   vs 57.3% baseline
expected_net_not_positive   absent from the cause counts
bound, not quoting          0
uptime                      5h29m, no supervisor bounce since 15:14
```

The 3h41m reading was not a fluke. Reward posting is still the outcome to
check -- see WATCHLIST.

### Loose end: 23 cancels still come back untagged

`side_no_longer_desired` with **no cause suffix**, 23 of them this session. The
claim that "every removal point names itself" is not yet true. Two paths in
`plan_market` clear `desired_sides` without recording a cause:

```python
desired_sides = ({"bid", "offer"} if market_open else set())
if not frame.market.is_supported_moneyline:
    desired_sides.clear()
```

Both are legitimate -- a market that closed or is not a supported moneyline
should drop every side -- and they are almost certainly what these 23 are. But
a bare `side_no_longer_desired` is precisely the opaque string that made this
churn undiagnosable for weeks, so tag them (`market_not_open`,
`unsupported_market`) the next time this file is open. Reporting only, no
behaviour change, but it needs a restart to take effect and was not worth
breaking a clean 5h run for.

### What still needs confirming

- **Re-run `resting_fraction.py` over a full day.** 3h41m and 36 sides is a
  real signal but not a season. Baseline to beat is 57.3%.
- **Check the reward posting for 2026-08-19 and after.** Resting fraction is
  the input; paid reward is the outcome, and it lags by days. Bot-era reward
  was +90.16 at ~57% resting -- if the ratio holds, ~86% should show up as
  materially more.
- **Watch the trading side.** More resting means more fills, and fills cost
  about 5c/contract (see the leg-to-leg movement entry). Reward runs about 2x
  the trading loss, so the trade should be favourable, but it is an estimate
  until the numbers arrive.

### If it does not hold

The cause counts are still tagged and still the right tool -- seven named
causes, readable with:

```bash
grep '\[action\]' logs/commissioning.log | grep -o 'reason=[a-z_:]*'   | sort | uniq -c | sort -rn
```

Three hypotheses died before the real one; they are recorded below so nobody
re-runs them.

## 00b-history. The three dead hypotheses (do not re-run)

**Ranked below the loss-stop baseline above (that one loses money silently)
and above everything below it.** This is the largest continuous drag on
earnings: reward is paid for RESTING, and the bot spends most of its time not
resting.

### The symptom, measured

2026-08-19 13:33Z, before any change:

```
19 markets bound, 2 orders resting at the venue
143 orders since 12:30 -- 141 CANCELED, 2 OPEN
87 PLACE_POST_ONLY reward_positive_candidate vs 80 CANCEL side_no_longer_desired
13-17 placements per market per hour
```

One market, same side, same price, no blockers, for hours:

```
13:17:31 CANCEL bid       13:18:25 PLACE bid 0.45
13:20:41 CANCEL bid       13:21:23 PLACE bid 0.45
13:24:04 CANCEL bid       13:24:54 PLACE bid 0.45
```

Still running after the restart, 14 minutes: 28 places against 28 cancels.

This is the same churn `BUGFIXES.md` records for 2026-08-14 and marks **"not
diagnosed further"**. It has been costing reward for weeks.

### It is now attributable -- that is what shipped

`side_no_longer_desired` was one string covering seven distinct causes, which
is why it was never diagnosed. Every removal point in `reconciler.plan_market`
now names itself:

`expected_net_not_positive`, `expected_net_not_finite`, `reward_ineligible`,
`no_entry_price`, `worst_loss_budget`, `exit_incomplete`, `exit_loss`

Read them with:

```bash
grep '\[action\]' logs/commissioning.log | grep -o 'reason=[a-z_:]*' \
  | sort | uniq -c | sort -rn
```

### Three hypotheses, two already dead -- do not re-run them

1. **Stressed-exit gate** (`cross_exit` incomplete or over `max_exit_loss`).
   Looked obvious. **Wrong** -- once tagged, live cancels arrived bare, so the
   drop was happening further up. Do not start here again.
2. **`expected_net` floored on the wrong branch.** `live_commissioning` does
   `if not economics.should_quote: expected = max(expected, 1e-9)` -- the floor
   that exists to stop this flicker is applied when the gate says NOT to quote,
   and skipped when it says quote, so a wanted side can carry a value <= 0 and
   fail the reconciler's strict `> 0`. **Real inconsistency, reproduced in a
   unit test, but not what is firing live.** Worth fixing on its own merits;
   not the churn.
3. **`reward_ineligible`** -- what the bot reported in the first window, 24
   cancels. The side left the venue's reward band and the 90s
   `reward_eligibility_dwell_seconds` grace expired.

**The mix moves with the regime -- do not fixate on any one of these.** Two
hours later the same bot reported 10 `resting_price_left_the_touch`, 3
`expected_net_not_positive`, and **zero** `reward_ineligible`. Hypothesis 2,
which the previous window said was not firing, was firing. One window is not
evidence; that is the whole reason the counts exist.

### What each fix would actually cost

- **`resting_price_left_the_touch`** is the bot chasing the touch, which is
  ordinary market-making. Chasing less means quoting off the touch, and
  `discountFactor` is 0.5 per tick, so one tick back halves the reward score.
  Declined twice already on measurement; see section 3 and the 900s entries.
- **`reward_ineligible`** already has a 90s dwell, so it only fires when the
  side is genuinely outside the band for a minute and a half. Cancelling is
  **correct** there -- an order outside the band earns nothing by resting.
  Lengthening the dwell means paying fill risk for reward we are not earning.
- **`expected_net_not_positive`** is the inconsistency in hypothesis 2. Fixing
  it by flooring both branches makes the bot quote negative-EV sides, which is
  the EV-gate decision, not a bug fix.

### The structural fact that may make this irreducible

`quote_size` is **5** against a `targetSize` of **5,000** -- we are 0.1% of the
band we compete in, so whether we sit inside it is decided almost entirely by
other participants' depth. A meaningful share of this churn may not be ours to
remove at this size. Before tuning any constant, check whether the flip rate
tracks other makers' depth rather than anything we control.

### The work

**Gate it on data, not on this note.** Let a full busy session accumulate, then
read the counts.

- If `reward_ineligible` dominates a high-volume window, the dwell is too short
  and the fix is one constant, with a clean before/after: cancels per hour, and
  orders resting at the venue. Pick the value from the observed flip interval,
  not by feel.
- If the mix shifts under load, the dwell is not the answer and hypothesis 2
  becomes the candidate again -- but only with counts behind it.

Whatever changes, measure **orders resting at the venue**, not placements.
Placements went up while resting went down; that is the trap this whole
investigation started in.

### Do not

- Do not floor `expected_net` on both branches to "fix" it. That makes the bot
  quote sides with negative expected value, which is the EV-gate decision in
  this file, not a bug fix.
- Do not tune the dwell from a quiet window. The 14-minute sample after the
  restart showed 11 resting where the busy window showed 2; a constant chosen
  from the quiet regime will not hold.

## 00. START HERE (2026-08-18, third): resolve a PLACE that returned no id

**This is what breaks the automatic recovery**, so it goes above the
per-market work below. Measured tonight: the bot took two unattended
supervisor restarts correctly, then a third bounce left it refused and needed
a human.

`POST /v1/orders` can return InternalServerError **without an exchange order
id**. `LiveOrderExecutor.place` records the intent `UNCERTAIN`, and because
there is no id, `exchange_order_id` stays NULL. Every resolution path keys on
that id -- `_upsert_order` resolves intents `WHERE exchange_order_id=?` -- so
nothing can ever resolve it. It arms the account-wide 60s stop, and then
refuses the next startup with `execution ledger has unresolved intents`, which
is exit 2 and the one code the supervisor never restarts.

Seen three times on 2026-08-18: 15:16Z, 20:53Z, 21:08Z.

**The fix cannot be a client-order-id lookup.** Retail order entry exposes no
such field, so the id in the ledger is local and was never sent -- see the
docstring on `ExecutionLedger.resolve_client_order_id`. The exchange id from
create is the only ownership anchor, which is precisely why a create that
returns none leaves nothing to ask about.

**Fix shape.** Conclude it from the account, not the order. If the create had
succeeded, the order would appear in the venue's open orders under an exchange
id the ledger does not know, and the existing adoption path would record it as
`venue:<id>` -- which already trips the terminal account-wide stop, correctly.
So the only case left is "the create did not happen", and it is provable:

  1. a **complete** open-orders snapshot, taken well after the failed create
  2. no unowned (`venue:`) order in that market
  3. no position in that market
  4. no order row under the intent's client id

That is exactly what `scripts/resolve_orphan_places.py` checks by hand today.
Moving it into the runner is the work.

**The one judgement call is how long to wait.** A just-placed order can take
minutes to become visible -- measured 2026-08-18, `BYK0EWMPMDK1` was accepted
and then unreadable for 131 seconds -- so concluding too early would call a
live order dead. Require several consecutive complete snapshots, and keep the
delay well clear of that 131s. If the wait is wrong the failure direction is
still safe: the order shows up as `venue:` and stops the bot rather than
trading blind.

## 00. START HERE (2026-08-18): make the unresolved-mutation stop per-market

**Status 2026-08-19: running, healthy, resting fraction 85.6%.** The 2026-08-17
item that stood here -- stuck
`UNKNOWN_ABSENT` orders blocking all rotation -- was fixed and deployed in
`854c21a`; rotation cleared 20 absent rows to 0 and the bot went from 1-5
markets to 10 concurrent. Do not re-derive it; it is in `BUGFIXES.md`.

What is left is the account-wide blast radius of the stop that killed the
session on 2026-08-18. Full write-up in `BUGFIXES.md`, measured facts in
`FINDINGS.md`.

`unresolved_mutation_timeout_seconds` is a **terminal, account-wide** critical
stop armed by a single unresolved intent. The venue can accept a place, return
an exchange id, and then serve no state for it for **131 seconds** (measured),
during which the intent cannot resolve by design -- absence is never read as
cancellation. So one market's venue-latency artefact stops all twenty.

Three fixes shipped 2026-08-18 and none of them address that:

- the strict reconcile sweep no longer lets one unreadable order hide every
  order behind it (it was aborting the loop in `sorted()` order),
- the failure is reported instead of swallowed by a bare `except: pass`,
- the stop now exits 5 instead of 0, so the supervisor restarts it.

That turns a three-hour outage into a restart, which is why it was done first.
It does not stop the session dying in the first place.

**Fix shape.** Quarantine the market whose intent is stuck rather than stopping
the account, on the same argument the rotation fix used: the blast radius
should match the evidence. An unresolved intent in one market says nothing
about the other nineteen. It sits on an account-wide safety property, so it
needs real tests -- including one proving that an unresolved intent still
blocks *its own* market's quoting and its own shutdown cancel.

**Do not "fix" this by raising the timeout.** It was 10s and cost an outage on
2026-08-17; 60s cost another on 2026-08-18. The measured stall was 131s. The
timeout is not the variable that matters.

## FIXED 2026-08-20: a retired, flat, dead market refreshed its book forever

Measured 2026-08-20T10:55Z. Two markets whose games had finished:

```
quiet_book_rest_refresh:aec-setkamecz-radada-vysmar-2026-08-20  attempt=72
quiet_book_rest_refresh:aec-setkamecz-talfil-schfra-2026-08-20  attempt=70
error = AdapterError: book is empty on both sides
144 refresh calls in 10 minutes  (~0.24 req/s, 6% of the 4 req/s governor)
```

**Both halves are working as designed, which is why this is not a bug in
either of them.** The market retires correctly at `MAX_RECOVERY_ATTEMPTS`.
Invariant 62 then keeps its book refresh alive on purpose, so a retired market
holding inventory can still be exited. And `_release_retired_if_flat`
deliberately refuses to release a BOOK-fault retirement on flatness alone,
because releasing on flatness is what produced the ~45s retire/release churn
recorded in `BUGFIXES.md`.

Compose them and a market that is retired, flat, past kickoff and permanently
unparseable refreshes forever. Nothing releases it: not flatness (refused by
the churn guard), not the top-up (it releases from the ACTIVE set, and this
market is retired out of it).

**Cost is a slow leak, not an outage.** 6% of the REST budget per stuck
market, accumulating as games finish through a session, plus unbounded log
growth -- 3,701 `unrecoverable_book` lines so far. Nothing is blocked and no
slot is denied.

**Fix shape.** The exit argument for keeping the refresh does not apply to a
market with no position and no working order -- there is nothing to exit. Stop
refreshing when a retired market is flat AND order-free AND past kickoff. That
is strictly narrower than the flatness release the churn guard refuses: it
stops the REST call without releasing the retirement, so the market stays
barred and cannot churn back in.

**Landed.** `_book_refresh_would_be_pointless` skips the fetch when a market
is retired AND past kickoff AND flat AND order-free, backing its next check off
to `DEAD_MARKET_REFRESH_SECONDS` (300). It does not clear the retirement, so
the market stays barred and cannot churn back in.

Three tests, each mutation-checked, because all three clauses had to be shown
load-bearing:

- removing the guard -> the settled market burns the REST call again
- dropping the position check -> a retirement holding inventory loses its book,
  stranding the exit invariant 62 keeps it for
- dropping the kickoff check -> a market retired BEFORE its game stops being
  watched, and a book fault before kickoff can be transient (one market in four
  publishes a one-sided book) so its cooldown release would hand it a stale book

The kickoff mutant survived the first attempt: there are two call sites of
`_retired_past_kickoff` and the mutation landed on the other one. Worth
remembering -- a mutation that silently patches the wrong occurrence looks
exactly like a clause that is not load-bearing.

## Also from 2026-08-19: two things for the next restart batch

Both are runner changes, both are reporting-only, and neither is worth
restarting a healthy session on its own. Batch them with the next deploy.

- **Give the period drop a real counter.** `_applicable_period` returning None
  is a bare `continue` in `market_selector.py` with no increment, so a third of
  the funnel drained invisibly on 2026-08-19 (15 of 43) and the diagnostic
  blamed the spread filter instead. `usable_markets.py` now derives it by
  subtraction, which is exact but only available when someone runs the script.
  A `period_filtered_markets` field on `RewardMarketSelection` would put it in
  the object the `[selection]` line reads. See FINDINGS.
- **Reconcile the two `minimum_trade_qty` gates.** `_applicable_period` hardcodes
  `> 1`; `_catalog_market_is_executable` scales to `policy.quote_size`. The
  static method cannot see the policy, which is why it was missed when size went
  1 -> 5. Latent today -- every in-horizon market reports 0.01 -- but it is the
  "gate in two places" pattern that has already cost two deploy cycles.

## Also from 2026-08-17, smaller

- **`reject_reason` is never captured.** All 16 rejections that day recorded
  `None`, so a rejection says nothing about why. 0.6% of orders, not urgent.
- **A dead-man alarm outside the runner.** `_alarm_if_quoting_nothing` lives
  inside the process and sends only to Discord, writing nothing to the log, so
  a stall cannot be diagnosed from the droplet and a dead runner alerts nobody.
  Every fault on 08-17 was found by the operator, never by the bot.


## THE PLAN (agreed 2026-08-17). Read this before picking anything up

**The keystone is re-arming the EV gate.** Of the nine defects in the
architectural audit (FINDINGS, "Architectural audit, 2026-08-17"), item 1 is the
only one that changes what the bot does with money. The gate is off because P&L
could not be validated; P&L could not be validated because ~60% of fills were
never recorded. **That was fixed and verified in production on 2026-08-17T13:20Z**,
so the reason for disabling the gate no longer applies. Right now the bot quotes
whatever the venue calls eligible while a built EV model sits discarded.

It is also the only direct attack on the real problem: reward runs ~$20/day
against trading losses of ~$16-17/day, so **the strategy is near break-even**.

### Order of work

1. **Let it run.** 2026-08-17 is the first day of correct fill data. The gate
   cannot be re-armed against a model nobody can validate, and validation needs
   several clean days. Free, and already happening. Take the daily
   `accounting.py` snapshot so `equity - funding - reward` becomes a trend.
2. **Fix the restart deadlock** (item 0). Uptime protection, no risk-envelope
   change, no reward cost. It bricked the bot for ~15 minutes on 08-17 and will
   again on the next non-flat restart. Downtime is the one thing that reliably
   costs reward.
3. **Build the settlements table** (audit item 8). The data source is known --
   resolved markets publish `outcomes`/`outcomePrices` with the winner at
   exactly 1, and `accounting.py --settlements` already prices them. Until it is
   written, every resolved position books as a total loss of entry cost. This is
   what makes P&L *complete* rather than merely *correct*.
4. **Validate, then re-arm the gate.** With clean fills plus settlements the EV
   model can finally be checked against realized outcomes. At that point audit
   items 5 (projections work only for `day_of`) and 7 (the evidence-free 0.5
   haircut) stop being footnotes and become blockers, because they feed the gate
   you are about to trust.

### Explicitly not doing

- **Relaxing the 900s clock.** Evidence points the other way; see FINDINGS
  "Before relaxing the 900s clock".
- **Quoting one tick off the touch.** Scales reward and losses together, the
  same reason size was ruled out. Adverse selection likely worsens the ratio.
- **Any further deep backfill.** Attempted 08-17 and reverted; see item 0b.

### The question underneath, to be answered deliberately

The audit is nine architectural defects and **no economics**. Fixing all nine
buys a well-instrumented break-even bot. Given the sibling Kalshi repo, and that
several items (3's throughput, 5's clocks) are claimed not to exist there, the
nine may be worth more as a **lessons list for that build** than as a fix list
for this one. Decide it on purpose rather than by default.

### Small thing spotted 2026-08-17T15:4xZ

`market_incentive_refresh` retries `CommissioningUnavailable: ... is no longer
pregame` with escalating backoff to `attempt=14` before retiring -- 78 of them
in one afternoon. A match having started is **permanent**, not transient. Same
category error as audit item 4, cheap to fix, wastes REST budget shared with
quoting.

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

## 0a. Report reward CASH, not just accrual — and get the deposit history

`/v1/incentives/earnings` PAID/PENDING is an **accrual** view and does not track
cash. On 2026-08-17 it showed nothing PAID since 08-10 while two payouts landed
that same afternoon. Full detail in FINDINGS; the short version:

```
2026-08-11T15:35   $32.06  COMPLETED  liquidity reward payout
2026-08-17T14:20    $5.85  COMPLETED  liquidity reward payout
2026-08-17T14:26    $5.83  COMPLETED  liquidity reward payout
```

Cash payouts are `ACTIVITY_TYPE_TRANSFER` rows in `/v1/portfolio/activities`
with `accountBalanceChange.description = "liquidity reward payout"`.

**Two jobs:**

1. Teach `scripts/rewards_report.py` to show cash received alongside accrued,
   so "have we been paid" is answerable from one command. Bound the paging —
   8 pages found everything; 60 tripped venue rate limiting while the bot quoted.
2. **Pull the full deposit history with the bot stopped.** Deposits are the
   missing term in a true P&L: `equity - deposits - reward_received` is the
   trading result, and it needs no ledger, no model and no settlement data —
   it would sidestep the entire untrustworthy-ledger problem. Partial reads so
   far show $50 (08-10) and $400 (08-11), but 2026-06/07 trading implies
   earlier deposits outside the window. **Do not draw a bottom line until the
   deposit list is known complete.**

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
