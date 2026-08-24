# WATCHLIST

Open questions that **cannot be answered yet** because there is not enough
data. Each entry records the baseline as it stood when the question was
raised, exactly what to measure, and the rule that decides the change — so
revisiting it is a measurement, not a fresh argument.

**The rule:** when something is uncertain and the honest answer is "we need
more data", it goes here with a decision rule attached. It does not go in
FINDINGS (that is for things we established) or BUGFIXES (that is for things
we fixed). An entry leaves this file when it is decided — moved to FINDINGS
with the number that decided it.

Write the decision rule **before** the data arrives. A threshold chosen after
seeing the result is not a test, it is a rationalisation.

---

## 0. Should reward-ineligible orders get a longer dwell or same-price suppression?

**Decide when:** at least several hours of `[eligibility]` transitions produce
a materially larger sample than the current nine completed ineligible spells,
split by `target_not_met` and `outside_reward_cutoff`.

**Baseline (2026-08-23):** 451 `reward_ineligible` cancel/re-place cycles; 87%
returned at the identical price, median dark gap 71 seconds, about 1,082
market-minutes dark. In the first new instrumented session, nine spells returned
eligible: six `target_not_met`, three `outside_reward_cutoff`; five of nine
lasted longer than the current 90-second dwell. This sample proves the path is
real but not which policy dominates.

**Measure:** for every `from=<reason> to=eligible`, the ineligible `held`
duration and whether the quote price stayed identical through the spell.

**Change if:** most completed spells cluster just above 90 seconds—raise the
dwell beyond that measured cluster. If long spells have a broad tail but quotes
mostly return at the same price, suppress only the same-price cancellation.

**Do not change if:** the larger sample shows most >90-second spells return at a
different price or remain ineligible through kickoff/rotation; then cancellation
is removing genuine dead quotes. Do not disable the gate from nine observations.

---

## 1. Is the 900s inventory deadline cutting off passive exits?

**Decide when:** one clean session on `418aa54` or later, ideally 3+ days.
Tracked as task #29.

**Baseline (2026-08-16, 92 fills / 29 round trips):**

```
                                  n   total    per-contract median
passive exits                     9   +$1.05          +0.0380
taker exits                      20   -$4.76          -0.0740
  taker <900s (pre-kickoff)       8   -$2.51          -0.0750
  taker >=900s (inventory clock) 12   -$2.25          -0.0590

passive share of exits: 9/29 = 31%     (maker-lab ran ~60%)
holding times, MAKER: 6 58 101 156 366 | 1019 1074 1108 | 3246
holding times, TAKER: 21 48 105 314 336 399 473 689 | 901 906 911 914
                      995 1013 1034 1074 1095 1144 | 2282 3263
```

**Measure:** passive share of exits, and the holding-time distribution of
taker exits. Scripts `/tmp/exit29.py` and `/tmp/hold29.py` on the droplet;
recreate from the FINDINGS entry if the scratch files are gone.

**Change if:** passive share stays near 30% **and** passive exits still
complete in the 900–1200s band. Then raise `max_inventory_age_seconds`
900 → ~1200. It is hard-locked in `commissioning.py:266-271`, so the invariant
check moves with it.

**Do not change if:** passive share climbs to 50–60%. That means churn was the
constraint, not the deadline, and `4ed03e3` already fixed it.

**Settled already, do not re-litigate:** do NOT extend toward kickoff. The
pre-kickoff group was the worst measured (-0.075 vs -0.059), consistent with
adverse selection into thinner pre-game books.

---

## 2. Did the `book_stale` replan actually stop the loop?

**Decide when:** first full hour of steady state after startup on `4ed03e3`+.
Warm-up does not count — every market reports stale until its first book
arrives, and comparing warm-up against a session average is how the REST
contention theory got believed in the first place.

**Baseline (2026-08-16, pre-fix, 7h09m session):**

```
book_stale events        7,535 total, ~250-470/hour, FLAT vs market count
retirements              31 markets, 30 of them unrecoverable_book_stale
consecutive book_stale on one market: 34-75s apart, clustered 45-60s
```

**Measure:** `grep -c "error=AdapterError: book_stale"` scoped to one steady
-state hour, and the distribution of `attempt=N` values — the fix works by
letting a market SUCCEED, which clears `strategy_state:{slug}` and stops the
march toward `MAX_RECOVERY_ATTEMPTS` (12).

**ANSWERED 2026-08-16 (FINDINGS): the replan works.** 99% of staleness now
resolves on attempt=1, nothing reaches MAX_RECOVERY_ATTEMPTS, and retirements
are zero against 32 pre-fix.

**The decision rule above was written wrong** and is kept as written so the
error is visible: it expected the event RATE to fall. The rate rose, ~400/hour
to ~2,190/hour, because removing a 30s backoff makes a market re-check far more
often. Event count measures how often we look, not how often we are broken.
Escalation depth is the metric that maps to harm.

**Still open:** whether this session's books were simply quieter. Resolve with
the resting-fraction data from task #32.

---

## 3. Does retirement still consume the slate?

**Decide when:** first 2 hours of a session on `32cfdcd`+.

**Baseline (2026-08-16, pre-fix):** 20 markets barred inside the first 40
minutes; distinct markets quoting per hour fell 19 → 11 → 8 → 7 → 6 while the
venue still had candidates and selection never reported a shortfall.

**Measure:** distinct markets quoting per hour, and distinct markets retired
per hour.

**Change if:** the slate still decays toward single digits with candidates
available. `RETIREMENT_COOLDOWN_SECONDS` (1800) is the lever — shorten it.

**Do not change if:** the slate holds near 20. Note a genuine dip is expected
overnight when fewer games exist; compare against candidate count, not against
20, or you will repeat the mistake of reading availability as starvation.

---

## 4. Should we quote a tick behind the touch to avoid bad fills?

**Answered NO on 2026-08-16, then REOPENED on 2026-08-24 because trading loss
grew to the same order as reward.** The original decision remains below so the
new measurement is compared with what changed rather than argued from scratch.

```
discount_factor across 263 live reward periods:
  0.30 -> 151 periods    0.35 -> 41    0.50 -> 70    0.20 -> 1
```

Score is `discount ** ticks x size`, so one tick back keeps **30%** of score on
a typical market. Payout is `your_score / total_score`, so share falls by
roughly the same 70% while competitors at the touch keep theirs.

```
reward accrual   ~$25/day      (08-15 settled $25.78, ACCOUNT-WIDE)
trading P&L      ~-$1.2/day    (-$3.71 over 29 round trips)
```

Stepping back one tick costs ~$18/day to avoid at most ~$1.2/day. About 15x
negative, wide enough to survive large errors in the estimate.

**Re-open condition met:** the common `discount_factor` is still far below
0.8, but August 23 produced $79.45 reward against -$42.88 recorded trading.
That does not authorize moving every quote back one tick; it authorizes the
narrower liquidity-cliff and queue-reset studies in 4a/4b.

---

## 4a. Should entry refuse a severe post-fill liquidity cliff?

**Prospective capture is active; decide with the same three-day adverse-fill
study as item 6.** This targets the operator-observed case where a resting
order costs about $5, fills, and the next executable book values the position
near $3. It is narrower than refusing all fills and narrower than re-enabling
the old expected-net gate.

The runner already calculates the relevant counterfactual before every
building PLACE: remove the entry level that must have been consumed for our
order to fill, halve the remaining displayed depth, and price an immediate
exit. The value is stored as `stressed_exit_pnl`, but the reward-cost gate is
advisory, so a severe result does not currently block the order.

**Baseline, 2026-08-24 at 0.62 observed days:** 1,033/1,033 observations,
64 maker fills and zero missing capture. Among those fills, stressed immediate
exit P&L was -$0.46 median and -$1.10 mean; 13 were at or below -$2 and the
worst was -$6.41. Across resolved observations, 200 were projected to retain
less than 50% of entry value and 12 of those filled. These are decision-time
counterfactuals, not yet actual post-fill markouts.

**Candidate, shadow only:** require stressed immediate liquidation value to
retain at least 70% of entry cost. If the active-touch quote fails, test a
safer reward-eligible price and recompute; if no price passes, the side would
be refused. Reducers, the 20-slot scheduler, league selection and exit policy
must remain unchanged. The 70% candidate would refuse the $5 -> $3 example,
which retains only 60%; it is a starting hypothesis, not an authorized live
threshold.

**Measure:** after the complete August 25-27 UTC cohorts and roughly 300 maker
fills, compare the counterfactual with actual side-normalized post-fill
markouts and realized exits. Run a `would_refuse` replay and count avoided
trading loss, projected/paid reward surrendered, dark slot-seconds and viable
sides rejected. Keep August 27 held out.

**Change only if:** on held-out data, avoided realized loss exceeds reward
surrendered with a conservative margin, severe actual markouts concentrate
below the retained-value floor, and the rule does not starve the 20-slot slate.

**Do not change if:** stressed exit is merely pessimistic, affected positions
usually recover passively at basis, or the rule sacrifices more reward than
actual loss. Do not re-enable the broad expected-net gate to solve this narrow
problem; it previously rejected nearly the whole universe.

---

## 4b. Should an almost-front resting order cancel and re-place at the same price?

**Feasible as a bounded estimate, not an exact queue rank. Measurement is
deployed as `6085ca5` and collecting automatically.** Under price-time priority, canceling and
re-placing at the same price puts our replacement behind the quantity already
resting there while preserving the same reward ticks whenever the price
remains eligible. This could reduce fills without paying the typical 70% score
loss of moving one tick back. It also creates dark time and extra mutations,
and a sweep through the whole level can still fill the replacement.

At placement, every displayed contract already at better prices or the same
price is ahead of us; `observed_queue_ahead` captures that. After placement:

- a public trade consuming our side at our price definitely reduces queue
  ahead;
- a trade through our price exhausts it and can fill us regardless of a reset;
- a new same-price order is behind us;
- aggregate book depth disappearing through cancellation is ambiguous: the
  canceled quantity may have been ahead of us or behind us.

Therefore the tracker must retain lower and upper bounds for queue ahead. It
may declare `almost_front` only when the conservative bound is small; it must
never present an exact rank the venue does not publish. The installed market
SDK supports public `subscribe_trades`. The reviewed measurement uses a
separate bounded trade socket so telemetry failure cannot disconnect the live
book stream. Because books and trades have no shared delivery order, the
conservative upper bound never subtracts the same trade from both feeds; the
shadow trigger is evaluated only at a full-book boundary. The original
decision-time `trade_volume_30s` and `trade_volume_120s` fields remain NULL;
lifetime queue evidence lives in separate measurement tables.

**Measurement-only prerequisite:** deployed and initial live acceptance passed.
The 18:49:40Z restart bound all 20 markets; the acceptance checkpoint had 38
measured orders, all 38 with post-ACK baselines, and zero coverage gaps, dropped
prints, decode failures or errored sessions. It persists the decision-time queue, establishes a safe
lifetime baseline from the first post-acknowledgement full book, and records
definite trade consumption, ambiguous book-depth removals, new same-price
depth and the resulting interval. It adds no REST calls or mutation path. Before trusting the
sample, require zero stream gaps plus acceptable throughput, freshness and
ledger growth on the server.

**Candidate, shadow only:** after 60 seconds, when the conservative queue-ahead
upper bound is at most one order quantity, emit `WOULD_QUEUE_RESET`. This is
implemented as a durable signal only. After enough data, simulate cancel
confirmation plus same-price replacement,
including measured dark time and the actual priority-replan delay. Add a
cooldown and hysteresis so one level cannot churn repeatedly.

**Change only if:** held-out replay shows queue resets avoid more realized
adverse-fill loss than the reward lost during dark time, do not materially
reduce resting fraction, and keep mutation latency, unresolved state and REST
load inside current safety bounds.

**Do not change if:** fills are mainly sweeps through the whole level, queue
position does not separate losing from safe fills, or cancel/re-place dark time
costs more reward than the losses avoided. Never queue-reset a reducer or an
order with an unresolved mutation.

---

## 5. Should we stop quoting well BEFORE the flatten deadline?

**Decide when:** enough fills to bucket by time-to-kickoff.

Entry now stops at `force_exit_before_start_seconds` (300s), because quoting
inside the window the exit already flattens is incoherent (BUGFIXES). Whether
to stop *earlier* -- T-15min, T-30min -- is a real question and is NOT decided.

**Baseline (2026-08-16):** 8 of 9 markets resting quotes started within fifteen
minutes; the slate is dominated by near-kickoff markets. Pre-kickoff crossings
measured -0.075/contract against -0.059 for the inventory clock.

**The tension:** reward is paid for RESTING, so quoting a market that never
fills is free money and a wider gate throws it away. The risk is only on fills
-- and near kickoff is exactly when fill probability and adverse selection are
both highest. So the question is whether the reward earned in the last N
minutes exceeds the expected loss on the fills it attracts.

**Measure:** bucket fills by minutes-to-kickoff at fill time, and compare
realized P&L per contract against reward accrued while resting in that bucket.

**Evidence so far points AGAINST widening** (FINDINGS, 2026-08-16): the 15-30
minute bucket pays ~14x the median of the 120+ bucket, and a third of markets
beyond two hours pay nothing. Cutting at T-15 or T-30 would surrender the
best-paying window to avoid fills costing about a cent per contract.

**Change only if:** a time bucket shows reward earned BELOW expected loss on
realized fills -- not on projections. That has not been shown, and the current
ratio (reward ~$25/day against trading ~-$1.2/day) makes it unlikely.

**Do not change if:** losses are spread evenly across time-to-kickoff, or if
reward in the near buckets continues to dominate. Then fill timing is not the
driver and the lever is selection or exit instead.

## 6. Which markets and sides precede the losing fills?

**Prospective capture deployed 2026-08-24; collecting, not yet
decided.** This is the version of question 4 that does not cost
reward: losses come from *which* market and side we quote and *how the
position exits*, not from queue position. Queue position is what the venue
pays for; selection and exit are free to change.

Historical review found no stable monotonic separator in side, broad price,
recent touch-cancel count, re-entry delay or prior same-side price range. It
also did not justify a universal cooldown. League is diagnostic context only
and must not become a filter.

**Decide when:** discard the partial August 24 startup cohort, require zero
coverage gaps across the complete August 25–27 UTC cohorts and roughly 300
maker-filled observations. Train on August 25–26, keep August 27 held out, then
shadow the candidate as would-refuse before any enforcement. Earliest review is
2026-08-28T00:00Z; continue collecting if the fill target is not met.

**Measure:** for each losing round trip, the market's reward-band width,
book depth at entry, distance from midpoint, time to kickoff, and which side
filled. The capture additionally records actual price-time queue versus the
current model's best-touch-size approximation, Target Size/cutoff, spread,
depth imbalance, touch velocity, projected reward, stressed exit and expected
net. Compare against terminal unfilled orders and winning round trips using
the exact client order ID; do not join by market/time guesses.

**Queue-model discrepancy found 2026-08-23:** the current fill-hazard model
uses only the best-touch size as `queue_ahead`. That is close when our order is
at the touch, but incomplete when it is backed off. For example, if 100
contracts bid at $0.50 and 200 already bid at $0.49, a new $0.49 bid has 300
contracts ahead under price-time priority; the current model records only the
100 at $0.50. It can therefore estimate that a backed-off order is closer to a
fill than its observed queue position indicates. This is a discrepancy to
measure, not yet proof of a safer replacement model: a large incoming order
can sweep multiple levels. The capture stores both `model_queue_ahead=100` and
`observed_queue_ahead=300` so prospective results can decide which predicts
fills better. Do not change the live model from this example alone.

**Operation after an approved deployment:** collection is automatic whenever
the normal bot creates a building post-only PLACE; there is no separate
collector process, cron job or command. Schema migration is additive at normal
runner startup. Analysis is deliberately manual and read-only: run
`scripts/quote_observation_report.py` when checking coverage, and do not treat
its diagnostic buckets as an automatic strategy gate.

**Live acceptance:** commit `2acf544`; first observation
2026-08-24T02:51:31Z. The 02:53:52Z checkpoint had 37 required, 37 captured,
zero missing and no `[quote-observation]` errors. This is coverage proof only,
not enough outcome data to select a rule.

The **2026-08-24T11:47:56Z** recheck had 569/569 captured, zero missing or
link/payload/hash/arithmetic/timestamp errors, 38 maker-fill labels and 105
model-versus-observed queue differences. Freshness stayed inside the live 5s
bound (4.991020s maximum). The report remained `COLLECTING`; these early bucket
rates are not permission to filter any league or enforce any refusal.

**Change if:** a characteristic separates them cleanly. The lever is the
projection gate in `market_selector.py`, not the quoting rule.

---

## 7. Ledger growth as a bug signal

**Not a disk concern.** 24G volume, 18G free, ~7MB/day — roughly seven years
of runway. maker-lab's 2.4G was five full DB snapshots taken before risky
changes, not runaway writes.

**Baseline (2026-08-16):** `data/commissioning.db` 22.4MB, 47,784
`execution_events`, ~3 days old. Now reported on the Discord summary as
`Ledger on disk` (db + WAL sidecar).

**Watch for:** a jump in the *slope*, not the size. A sudden move from ~7MB/day
to much higher means something is writing far more than it should — a retry
storm, duplicated event rows, a loop that is not terminating. The pre-fix
`book_stale` churn was partly visible this way.

**Act if:** the live DB approaches a few hundred MB — not because the file
matters, but because that is when "take a backup before touching it" starts
costing real space. That is exactly how maker-lab reached 2.4G.

---

## 8. Is projected-reward ranking actually earning more?

**Decide when:** several days of settled earnings after `112d58b`.

**Baseline:** before the change, the selected twenty overlapped the best twenty
by projected reward on only **7**, and a slot worth 0.000014 was held while one
worth 0.010063 went unquoted. After deploying, the top 5 by projected reward
were all chosen and the worst chosen slot rose from 0.000013 to 0.000464.

**Measure:** settled PAID reward per day, not projected reward. Projection is
the thing we optimised; earnings are the thing we care about, and they are not
the same claim.

**Caveat that makes this hard:** earnings are **account-wide** and include
maker-lab's history, and the `date` field is a settlement stamp rather than an
accrual day (FINDINGS). Isolating liquidity-bot's contribution needs either a
period with only this bot running, or per-market attribution via `marketSlug`.

**Also unresolved:** the ranking change has no discriminating unit test — both
mutants survived, because `maximum_remaining_reward` is already a tiebreaker
and tracks projected reward closely on small fixtures. A refactor could undo it
silently. Live measurement is currently the only guard.

## 9. Do the tight-book rejects cluster at 1c, or far below it?

**Opened 2026-08-19T23:51Z**, after `usable_markets.py --rejects` was fixed
(BUGFIXES). This is the first hour anyone has seen the real reject list, and it
disagrees with the entry that closed the question.

**What is settled and must not be re-argued:** the counts in the 03:25Z FINDINGS
entry are fine. What is not supported is its *description* of the rejects, which
came from a listing that had no horizon filter and was showing month-out MLB
futures that were never candidates.

**Baseline (2026-08-19T23:51Z, 40 markets in the 2h horizon):**

```
- no applicable period (live-only)   15
- book too tight to enter (< 0.02)   25   <- 15 of 25 shown, ALL at 0.0100
= eligible pregame                    0

leagues in the tight group: setkameua, wta, valorant
```

Against the 03:25Z claim: *"2c -> 1c admits about 5 of 32, all at 1.0-1.4c ...
nothing sits just under the threshold ... 3x to 20x tighter than 0.02."*

At this hour they sit at **exactly half** the threshold, not 3-20x under it, so
2c -> 1c would admit ~25 rather than ~5. And `setkameua` is the league FINDINGS
6h measures as 93% of every reward dollar this account has earned.

**Measure:** run `scripts/usable_markets.py --rejects` on the droplet between
**13:00 and 17:00 UTC** (09:00-13:00 EDT), a busy slate. One run, ~50 venue
calls, never in a loop. Read the spread distribution of the `book too tight`
block. Midnight is not a sample: the same session had 98 executable markets at
15:19Z and 43 with zero ranked at 23:47Z.

### SAMPLE 1 of 2 taken 2026-08-20T14:12Z (10:12 EDT) -- a busy slate

```
in the 2h horizon             92
- no applicable period        67    <- 73% live-only
- book too tight to enter     20
= eligible pregame             5
= VIABLE                       4    of 20 slots
```

**The rejects cluster at exactly 0.0100 again** -- 15 of 15 shown, identical to
the midnight sample. So the numeric half of the rule below clears: busy hour,
clustered at 1c, and 2c -> 1c would admit all 20, well past the "more than 10"
bar.

**And it should still not be acted on, for a reason the rule did not
anticipate.** Look at WHICH markets are at 1c:

```
wta, modus (darts), lol, cs2, atp
```

FINDINGS 6h measures where reward actually comes from: setka table tennis is
**93% of every dollar this account has earned**, while `atp` and `mlb` -- deep,
liquid, heavily quoted books -- paid **one cent each**. The markets a 1c
threshold would admit are esports, darts and top-tier tennis: the deep-book
leagues already measured as paying essentially nothing. A tight book and a
worthless book are the same fact seen twice, which is why the 2c filter refuses
them.

So the rule as written is necessary but not sufficient. **Amended: also require
that the 1c rejects include the leagues that actually pay** (setka*, itf*).
Neither sample so far does.

**The finding that keeps growing instead** is the live-period drain: 81% at
10:55Z, 73% here. It is the dominant constraint at every hour measured, it is
larger than the spread filter in both samples, and unlike the spread minimum it
is not a tuning knob -- it is a whole lifecycle this executor does not
implement, carrying ~8x the pool (FINDINGS section 4). That is where the
earning lever is, if there is one.

**Change if:** rejects at a BUSY hour also cluster at 0.010-0.014, AND the
count admitted by 2c -> 1c is more than 10. Then the filter is genuinely holding
back real markets and the trade must be costed: extra reward from more resting
slots against the measured **-6.1c per crossed pair**, with 95% of pairs
completing by crossing. Costing it is a separate step -- clearing this rule
authorizes the calculation, NOT the config change.

**Do not change if:** busy-hour rejects scatter to 0.001-0.007. Then the 03:25Z
conclusion was right about the shape even though it was measured on the wrong
markets, there is no headroom, and this entry closes with the filter untouched.

**Do not read a single quiet hour as evidence either way** -- that is how the
original entry went wrong. Two busy-hour samples on different days before
anything moves.

---

## 10. Is the live-only period mix a daily cycle, or did something change?

**Opened 2026-08-20T21:5xZ**, immediately after a snapshot-based conclusion had
to be withdrawn six hours after it was written. A daily probe now feeds this.

### Why this exists

Order flow fell and I explained it as "the venue moved the leagues we earn in
to live-only". Two readings agreed. A third, nine hours later, said the
opposite:

```
10:55Z   81% of rewarded markets live-only
14:12Z   73% live-only; setkameua [live] x22, 0 markets quotable
21:49Z   setkameua [day_of,live] x16; 31 of 31 markets quotable
```

`day_of` returned on its own. **Two samples agreeing is not evidence when both
are drawn from the same unmeasured cycle** -- both of mine were the same day,
hours apart, and I read agreement as confirmation. Third time this session a
snapshot produced a confident wrong answer.

### What is measured

`scripts/scheduled_probe.sh`, from the droplet crontab at **14:00 UTC daily**,
into `logs/probe-<stamp>.log`. Two sections:

**A. `period_mix.py` -- reward periods by league.** The number that matters is
the last line, `markets offering day_of (quotable by this executor): N of M`.
A pregame executor may only quote `day_of` (invariant 39), so `N` is literally
the size of its addressable universe at that moment. The per-league breakdown
carries each league's share of lifetime earnings beside it, so a league going
live-only is read with its earning history attached.

**B. `usable_markets.py --rejects` -- the funnel and why each market dropped.**
Feeds item 9 above: the spread distribution of the `book too tight` block, and
which leagues those rejects belong to.

### Decision rule, written before the data

**After five consecutive 14:00Z readings:**

- **day_of present on >= 50% of in-horizon markets on 4 of 5 days** -> the
  14:12Z reading was an outlier, the addressable universe is normally fine, and
  the order-flow decline has a DIFFERENT cause that is still unexplained. Close
  this and reopen the decline as its own question.
- **day_of below 50% on 4 of 5 days** -> the mid-day trough is real and
  systematic. The lever is then the selection horizon (2h) or the hours the bot
  runs, NOT `minimum_entry_spread`. Do not implement live-period support off
  this alone: that is a much larger change and wants its own evidence.
- **Anything in between** -> keep collecting. Do not split the difference.

**Do not conclude from fewer than five readings**, whatever they show. That
constraint is the entire reason this entry exists.

### Also still unexplained, and do not lose it

The decline itself is measured and real -- rewarded->ranked fell 33.6% to 1.1%
across 08-16..08-20, and same-hour comparison rules out time of day (09:00 gave
21 ranked on 08-19 and 0 on 08-20). Withdrawing the explanation does not
withdraw the observation. If the period mix turns out to be normal, this is
still open.

---

## `_rest_reconcile` failures in the unresolved-mutation loop swallow everything

Noted 2026-08-18 while fixing the outage in `BUGFIXES.md`. The call site now
reports instead of discarding, but it still catches bare `Exception`, so a
`RuntimeInvariantViolation` raised out of `_rest_reconcile` would be logged as
a routine runtime error rather than ending the session. Pre-existing, not a
regression, and not changed with the outage fix because narrowing it creates a
new terminal path that wants its own mutation check.

Nothing reachable from that call raises it today -- only `live_commissioning`
and `live_session` raise it at all, and the strict sweep now re-raises it
explicitly. Worth narrowing to the exception types that are actually expected
(`AdapterError`, transport errors) next time this code is opened.

## Does the 85.6% resting fraction convert to reward?

Opened 2026-08-19. The churn fix moved resting fraction 57.3% -> 85.6% over
3h41m. Resting is the **input**; paid reward is the outcome and lags by days,
so the win is not banked yet.

Watch for:

- **2026-08-19 and 2026-08-20 reward postings.** Bot-era reward was +90.16 at
  ~57% resting. If the ratio holds, ~86% should show materially more. Re-run
  `scripts/rewards_report.py`; a missing day means "not posted yet" OR "earned
  nothing" and one reading cannot separate them.
- **A full day of `scripts/resting_fraction.py`.** 3h41m and 36 sides is a
  signal, not a season. Baseline to beat is 57.3%.
- **The trading side, which should get worse.** More resting means more fills,
  and fills cost about 5c/contract. Reward runs about 2x the trading loss so
  the trade should still be favourable, but confirm rather than assume --
  `scripts/accounting.py` carries both numbers.

Close this out either way: if reward does not move, the resting gain was
measured on the wrong quantity and the churn work needs revisiting.
