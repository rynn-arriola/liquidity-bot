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

**Change if:** the rate stays near 400/hour and markets still reach
`attempt=12`. Then the replan is not landing and the remaining candidate is
exempting `book_stale` from exponential backoff entirely — the 30s cap alone
exceeds the 5s freshness budget.

**Do not change if:** rate drops materially and `attempt=` values stay low.

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

**Answered NO on 2026-08-16 — recorded here because the answer is
conditional on a number the venue controls.**

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

**Re-open if:** the common `discount_factor` rises above ~0.8, or trading
losses grow to a similar order as reward accrual. Neither is true today.

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

**Change if:** a bucket shows reward earned below expected loss. Then raise the
entry gate to that boundary.

**Do not change if:** losses are spread evenly across time-to-kickoff. That
would mean fill timing is not the driver and the answer is in selection or
exit instead.

## 6. Which markets and sides precede the losing fills?

**Not yet measured.** This is the version of question 4 that does not cost
reward: losses come from *which* market and side we quote and *how the
position exits*, not from queue position. Queue position is what the venue
pays for; selection and exit are free to change.

**Decide when:** enough round trips to split by market characteristics —
current sample is 29, which is too few to slice.

**Measure:** for each losing round trip, the market's reward-band width,
book depth at entry, distance from midpoint, time to kickoff, and which side
filled. Compare against the winning round trips.

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
