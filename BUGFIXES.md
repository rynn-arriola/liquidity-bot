# Bug log

One entry per bug that reached production. Newest first. The point is that a
bug fixed once should never have to be diagnosed twice — so each entry records
the **symptom you would see**, the **mechanism**, and the **fix**, not just a
commit subject.

**Standing rule: every bug that reaches production gets an entry here, when it
is fixed.** Symptom, mechanism, fix -- and the cost if it cost anything. Not a
commit subject: the commit says what changed, this says what to look for and
what it turned out to be. Include the false alarms too, so nobody re-chases a
theory that was already disproved.

Its companion rule lives in `FINDINGS.md`: anything critical we establish goes
in there when it is measured. Between them, nothing has to be diagnosed or
derived twice.

Ground rules that came out of these:

- **Verify against real data before asserting.** Several entries below were
  first diagnosed wrong from a plausible-sounding theory.
- **Mutation-check every fix.** Revert it and confirm a test fails. Multiple
  tests here passed against the unfixed code.
- **A gate that lives in two places is one where changing a single side does
  nothing.** Cost a deploy cycle to learn, twice.
- **Anything critical goes straight into `FINDINGS.md`**, when it is measured
  rather than at the end of the session. Re-derived facts cost hours and often
  come back wrong.

---

## 2026-08-15

### Market selection ranked on a field that ties everywhere

**Symptom:** of the 20 markets bound, only **7** were in the best 20 by
projected reward. A slot worth `0.000014` was held while one worth `0.010063`
went unquoted — a 700x spread inside the same slate.

**Mechanism:** `_opportunity_key` led with `-best_expected_net_value`. That
value is floored to `1e-09` on every side whenever
`enforce_reward_cost_gate` is False — advisory since 2026-08-14. So every
market tied on the primary key and ranking fell through to tiebreakers that do
not predict earnings. **One field served as both the economic filter and the
sort key**, so switching the gate off silently switched ranking off too, and
nothing failed loudly.

**Fix:** `projected_reward_total` becomes the primary key. The old key is
demoted rather than deleted — it becomes meaningful again if the gate is
re-armed — and the remaining tiebreakers keep their order, since it was never
established they weren't protecting against something real.

**Coverage gap, stated plainly:** the unit tests do NOT discriminate this.
`maximum_remaining_reward` is already a tiebreaker and tracks projected reward
closely on a two-market fixture, so reverting the primary key still passes.
The divergence only appears across a live 80-market slate. Verify with
`/tmp/rank.py` on the droplet: chosen-20 vs best-20 overlap was **7/20**.

### Already-retired markets escalated themselves to abandonment

**Symptom:** the same market retiring over and over — round 1, 2, 4, 8 — then
abandoned. **50 markets retired and 30 abandoned in one session**, well past
the `max_markets` limit that stops the session outright.

**Mechanism:** bounding unparseable-book retries calls `_retire_market` once
the book-refresh error counter passes `MAX_RECOVERY_ATTEMPTS`. That counter
never resets, so every later failure re-entered and bumped the round — for a
market already retired and quoting nothing. The round counter exists to bound
**exit attempts**, not book-parse failures.

**Fix:** retiring a market that is already retired for the same *permanent*
fault is a no-op. Exit retirements still escalate, because that bound is the
whole point of them.

**Self-inflicted, same day** — introduced by the fix that bounded book retries.

### A position with no recorded fill could never be exited

**Symptom:** `demciva-hryole` short 5, `basis NONE`, no exit order and no
attempt at one. Its last logged activity was the two entry quotes. Same shape
on `ff-ep` earlier in the day.

**Mechanism:** `inventory_basis` rebuilds basis from fills only. When the fill
that opened a position never arrives, basis is `None` — and `_inventory_plan`
returns `inventory_basis_or_clock_unknown`, a *retiring* critical. The market
is retired, and a retired market's exit **still needs a basis**. Deadlock, and
the position rides to settlement.

**Fix:** fall back to the venue's own `cost` on the position snapshot —
`basis = cost / |netPosition|`. The clock uses `first_seen_at`, held from when
the position became nonzero and cleared when it goes flat; with no fill time
available that can only make the deadline *later*, never skip it.

**The pattern, for the third time today:** stop rebuilding what the venue
already knows. The same move fixed the loss stop (settled cash instead of
reconstructed fills). Quantity still comes from `netPositionDecimal` — cost is
used for basis only, never to infer size.

### A position lost its manager when selection moved on

**Symptom:** two open positions with **no exit orders and no attempt to place
any** — `aec-wta-kamrak-marsak` held 5 contracts from the *previous day*,
through many restarts. Nothing refused them; nothing looked at them.

**Mechanism:** `active_slugs` answered two different questions — *what to
quote* and *what to manage* — and selection decided it. The passive reducer,
the inventory timer and the pre-kickoff flatten all iterate the serviced set,
so a market dropped by rotation was never visited again. Rotation also
cancelled the exit already resting, leaving the worst pairing: position kept,
exit gone.

**Fix:** service the *managed* set — selected ∪ anything holding a position,
working order or unresolved intent (which `_shutdown_slugs` already computed
for shutdown, just never used at runtime). A managed-but-unselected market is
exit-only: barred from quoting, refreshed with `for_exit=True` so a started
game still yields a book to price against.

**Why today's other fixes missed it:** all of them assumed the bot was still
watching the market. This position never reached the decision loop at all.

**Coverage gap, stated plainly:** `managed_slugs()` and the quoting bar are
both mutation-covered, but the line that iterates it in the run loop is not —
no test drives that loop. Reverting it to `active_slugs` still passes.

### Adding a field to Execution broke the fill back-fill

**Symptom:** `LedgerConflict: event id was reused with different content`, the
reconcile sweep aborted, the runner exited non-flat and the supervisor refused
to restart. Bot down with a position open.

**Mechanism:** `_accept_event` dedupes by hashing the whole payload. Adding
`outcome` to `Execution` changed that hash, so replaying an already-recorded
fill read as a conflicting rewrite. The back-fill replays every sweep by
design, so this fired on the first reconcile after the schema change.

**Fix:** a replayed execution is skipped, not fatal. The execution id is the
identity — if we hold it, we hold it.

**Self-inflicted, hours after the field was added**, and the first test written
for it passed against the broken code: it counted fills without proving the
sweep continued past the conflict.

### realized_pnl booked a purchase as a sale, turning a loss into a gain

**Symptom:** `realized_pnl` reported **+$1.34** on `aec-valorant-ff-ep`, a
trade that lost about **$0.30**. The venue app showed 1.94 paid in and 1.72
back.

**Mechanism:** both intents we send are BUY intents with the price unchanged
(`live_orders._intent`, validated by maker-lab over 201 fills). So `BUY_SHORT`
at 0.67 *buys the opposite outcome* for 0.67 — it does not sell at 0.67. The
venue labels it `ORDER_SIDE_SELL` because of which outcome is taken, and
`realized_pnl` read that label as the direction of our cash.

    BUY_LONG   5 x 0.39 = paid 1.95
    BUY_SHORT  5 x 0.67 = paid 3.35
    five pairs settle at        5.00
    realised                   -0.30

**Fix:** the sign comes from the intent, not the side label; both intents are
cash out, and each matched pair credits one dollar. Prices were never wrong —
every maker fill equals our own order price.

**Three wrong diagnoses first**, all recorded: fills stored in two frames
(false), the frame not derivable from `portfolio.activities` (true but beside
the point), and a uniform 0.10 offset (an artifact of reading the
counterparty's order). The sign, not the price, was the whole bug.

### A one-sided book stranded a position until it settled

**Symptom:** `chevic-scovla` — 254 log mentions, exactly **one** order action
(the entry). After the fill the bot never placed another order on that market.
The position reached settlement. Same shape on `ff-ep`.

**Mechanism:** `parse_book_response` raised when either side was empty. The
book then never refreshed, went stale, and the reconciler returned `book_stale`
as a critical *before* planning — and the passive reducer, the bounded hard
exit AND the pre-kickoff flatten all live inside `_inventory_plan`, so all
three were lost together. Not a late flatten: no flatten. About one market in
four publishes a one-sided book.

**Fix:** parse a book with one side; gate on sides at the point of use.
Quoting needs both touches (the 2c rule is defined between them). A passive
exit needs the side it joins. The deadline exit crosses into the *other* side —
which is precisely the side a one-sided book still carries, so a position is
plannable even when no passive quote can be priced.

**A wrong turn worth recording:** the first attempt guarded on the reducing
side at the top of `_inventory_plan`, which blocked the hard exit too — the
opposite of the intent. Selling a long *joins* the offer side; it is *crossing*
that needs the bid side.

### The reducing quote had a cost-basis floor the invariants forbid

**Symptom:** losing positions never exited passively. They sat unfilled until
they crossed the spread or reached settlement.

**Mechanism:** the passive reducer priced at `max(touch, entry)` for a long.
Invariant 5 has always said **no cost-basis floor** -- the code contradicted
its own documented rule. A floor can only fill when the market returns to
entry, so it exits winners and strands losers.

**Evidence:** maker-lab ran a floor over 101 exits -- passive 60 winners to 1
loser, settlement 2 winners to 14, which they documented as *"79% losers BY
CONSTRUCTION"*. At scale, 234 settled positions cost $443.88 where crossing at
T-15 would have cost $64.18: a **$379.70 swing, 78% of positions better off**.

**Fix:** rest at the touch. A loser can then leave passively, earning the maker
rebate instead of paying a taker fee.

**The tests locked in the defect** -- named `never_quotes_below_entry`, for the
behaviour rather than the rule.

### Retired markets looped through release and back

**Symptom:** `retired` / `released=flat` / `retired` every ~45s on
chevic-scovla.

**Mechanism:** `_release_retired_if_flat` frees any retired market once flat.
But a market retired because its book will not parse is no more quotable flat
than it was holding risk, so the same fault retired it again immediately.

**Fix:** only release retirements that being flat resolves -- the exit ones.
Book, plan and action failures stay retired.

**A test asserted the wrong field first:** `_is_barred_from_quoting` reads
`_retired_ever`, which is never cleared, so it passed against the broken code.
Assert on `_retired`.

### The fill back-fill recorded the counterparty's side too

**Symptom:** `aec-valorant-ff-ep` held a real position of +5 while its fills
read `SELL 5 @ 0.39` and `BUY 5 @ 0.39` at the same instant. Same shape on
`cherom-rezrom`. Offsetting phantom fills make realised P&L read flat on a
market that is holding risk.

**Mechanism:** `portfolio.activities` reports both legs of every trade --
`aggressorExecution` and `makerExecutions` -- and only one is ours.
`normalize_execution` succeeds on any well-formed execution regardless of
ownership, so a comment assuming foreign legs would fail to parse was simply
wrong, and every trade was recorded twice.

**Fix:** skip legs whose resolved client id carries the project's existing
`venue:` unowned marker, the same one the kill switch refuses to cancel.

**Self-inflicted, same day**, introduced by the fill back-fill hours earlier.
It corrupts the number the $40 stop trusts, which is the thing that fix existed
to protect.

### One unusable book killed the whole market feed

**Symptom:** 30 markets bound, every book stale, zero placements. Standalone
measurement: 28 slugs delivered 20 messages then silence, preceded by
`book is not two-sided`; 8/16/20/24 delivered 250-490 messages.

**Mechanism:** `_on_market_data` queued a parse failure as an exception,
`next_book` raised it, and the runner treated that as the socket failing. One
market's malformed book ended delivery for every market on that socket. About
one market in four publishes a one-sided book.

**Not a width cap.** A second run of 29 slugs was healthy at 415 messages while
16 delivered 152. Width only changes the odds of hitting a bad book.

**Fix:** skip and count unusable books. That market simply has no fresh data,
ages out, and `book_stale` already refuses to quote on stale data. Scope
violations and structural decode failures stay fatal.

**Two wrong diagnoses before this one**, both recorded rather than deleted: the
REST governor arithmetic (a symptom -- the overwhelmed fallback), and a
supposed subscription cap (disproved by the second run).

### Thirty markets starved every market of fresh books — `a5b20e9`..`HEAD`

**Symptom:** 29 markets bound, 58 `book_stale` events, **zero placements**.
The bot could not quote a single market.

**Mechanism:** the socket cap was lifted to 30 across four connections, but the
REST governor is the real ceiling. 30 markets at 4 req/s is a 7.5s refresh
cycle against a 5s staleness bound, so every book was stale before its turn.
Width past ~16 is negative, not merely wasted: the extra markets starve the
ones that would otherwise quote.

**Fix:** `max_markets = 16`. Raising the request rate is not the lever --
maker-lab was rate-limited and settled at 0.3 req/s against our 4.

**Caught in production, minutes after deploy.** The end-to-end review verified
subscriptions, sharding and exposure, and confirmed the rate could not be
exceeded — but never checked whether the rate could *service* 30 markets.

### Stuck-cancel grace measured lateness, not the cancel — `c2602df`

**Symptom:** `hard_exit_blocked_cancel_unconfirmed` firing on a ~60s cycle,
ten retirement rounds, market abandoned with the position still open.

**Mechanism:** `overdue` was measured from the exit deadline and only grows.
Once it passed `cancel_confirm_grace_seconds` every pass returned the critical
*instead of* issuing the cancel — and a cancel that is never re-sent can never
confirm. Not a race, a one-way door.

**Fix:** grace measures how long our cancel has been outstanding. First pass
past the deadline issues the cancel; only an outstanding cancel escalates.

**Cost:** turned a break-even exit into −$1.55 on `tkamak-tsokos`.

### Unparseable books retried forever — `c1f6f7b`

**Symptom:** `book is not two-sided`, 2,121 occurrences, the most common error
in the log. 21 of 77 markets; two retried ~1,100 times each.

**Mechanism:** the quiet-book refresh retried every 3s with no bound. The
market can never quote, so it held a slot and spent REST budget forever.

**Fix:** retire on the same `MAX_RECOVERY_ATTEMPTS` bound the strategy-state
recoveries use.

### Routine recovery alerts marked critical — `c1f6f7b`

**Symptom:** constant Discord criticals on healthy restarts (~15 on 08-14).

**Fix:** announced, not critical. Critical is reserved for loss stops,
ownership violations, retirement, abandonment, unexplained runner failures.
An alert that cries wolf on a normal state trains you to ignore Discord.

### The $40 stop could not see closed round trips — `7408d83`

**Symptom:** `realized_pnl` returned **+3.26** for a trade that lost **$1.55**.

**Mechanism:** only two paths wrote fills — the order-create response (inline
executions only) and the private WebSocket. A shutdown IOC satisfies neither:
it returns before its match completes, and the fill arrives on a socket that
shutdown is closing. Three closes were never recorded.

**Fix:** `_rest_reconcile` replays executions from `portfolio.activities`, the
only source that survives a restart. `apply_execution` dedupes on
`execution_id`, so replay is a no-op.

**False alarm, recorded so it is not re-chased:** fills were suspected of being
stored in two price frames. They are not — `normalize_execution` reads the
venue's `lastPx`.

### Backed-off quote price landed off the tick grid — `560f7a7`

**Symptom:** `OrderValidationError: price is not aligned to
orderPriceMinTickSize`, market retired for the session.

**Mechanism:** `41 * 0.01` is `0.41000000000000003` in binary floating point,
and `build_order_payload` validates in `Decimal`. Nine of the ninety-nine cent
prices land this way — 35, 41, 47, 57, 69, 70, 82, 83, 94 — so ~1 backed-off
quote in 11 burned a market slot. Only the tight-book back-off could produce
it; touch prices come from the venue already aligned.

**Fix:** round the grid multiplication. Test sweeps every cent price.

**Self-inflicted:** introduced by the 2c rule two hours earlier, and the
existing tests all happened to use prices that multiplied cleanly.

### Shutdown liquidated positions at any price — `fdd20cd`

**Symptom:** a redeploy crossed out a position at 0.96 against a 0.65 entry.

**Mechanism:** the shutdown flatten asked only whether `cross_exit` was
complete, never what it cost. The runtime hard exit refuses above
`max_exit_loss`; shutdown had no equivalent. With ~10 deploys in a day that is
an uncapped liquidation on every restart.

**Fix:** a market holding a position keeps its opposite-side exit resting and
is left alone; orders that could *add* are still pulled; kickoff still
overrides everything, because settlement is unbounded.

### A one-pass reward-eligibility flip cancelled every quote — `114df85`

**Symptom:** seven markets cancelling within 1.5s every ~60s, re-placing nine
seconds later, several at the identical price. Logged `blockers={}`.

**Mechanism:** `evaluate_order_score` and `project_order_reward` are separate
calls that can disagree. The score call is depth-based, so an incentive refresh
flipped every market at once. Nothing wrote a blocker on that path, which is
why it went unattributed for two days.

**Fix:** 90s dwell before a *resting* side is pulled; the score call now
records its reason.

### A tight book was refused instead of priced — `ae95565`, `6c1e711`

**Mechanism:** `minimum_entry_spread` refused any book under 2c rather than
pricing back to it. Also the runtime economics scored the touch while the
planner refused the book — the gate lived in two places that disagreed.

**Fix:** `entry_quote_price`, called by both. A book already leaving 2c is
quoted at the touch untouched; a tighter one has the 2c window placed on the
grid nearest the mid. 50/50 and 49/50 both quote 49/49.

### One market's fault cancelled all eight markets' quotes — `8457f9f`

**Symptom:** quotes re-placed with `working=0` while the `[action]` log showed
zero cancels.

**Mechanism:** the cancels came from `_quarantine_working_orders`, which does
not log as an action and cancelled every bound market, driven by a single
market reporting `execution_state_unknown`.

**Fix:** quarantine takes a slug. Global form kept only for genuinely
account-wide faults (stream reconnect, rotation). Plan and action exceptions
now retire one market instead of ending the session.

### Recovery could not load a market whose game had started — `dd753cd`

**Symptom:** `startup_unavailable` retry loop for 2h20m holding a position.

**Mechanism:** recovery loaded its market through the gates that exist to stop
us *opening* risk, so `no longer pregame` failed startup.

**Fix:** `_refresh_contracts(for_exit=True)` skips those gates; recovery-bound
markets are marked retired so they exit and never quote.

### A healthy 7-hour session stopped for outliving its markets — `PENDING`

**Symptom:** the session stopped at 07:27 UTC on 2026-08-16 with
`critical:market_retirement_limit_exceeded`, after 7h09m. It had sent 466
orders and the account was flat. Nothing was actually wrong.

**Mechanism:** the systemic bound compared `_retired_ever` — a set that only
ever grows — against `max_markets`. `unrecoverable_book_*` is permanent by
`PERMANENT_RETIREMENT_PREFIXES`, so a retired market never leaves that set.
Any session therefore trips the bound simply by outliving twenty of its
markets. Measured attrition that day was 31 markets over 7h09m (~4.4/hour), of
which **30 were `unrecoverable_book_stale`** — books going quiet as their games
kicked off, which retirement handles *correctly*, since a live game must not be
quoted. The bound's own comment says it exists to catch "one full slate of
retirements", i.e. a burst; it was measured against a session lifetime total.

The 07:24–07:27 cluster also shows the second half of the problem: a
synchronized morning kickoff retires a slate's worth of markets together, which
is a burst *and* is not a fault.

**Fix:** count only faults inside `RETIREMENT_BURST_WINDOW_SECONDS` (900), and
exclude markets whose `game_start` has already passed — those are the market
ending, not the bot failing. Unknown game time still counts as a fault, so the
last guard against churning every slot is never weakened by a gap.

**Coverage:** two tests, each mutation-verified. Restoring the cumulative count
fails both; dropping only the kickoff exclusion fails only the kickoff test.

### A permanent ban list starved a fixed slate — `PENDING`

**Symptom:** distinct markets quoting per hour fell 19 → 11 → 8 → 7 → 6 across
2026-08-16 while the venue still had candidates and selection never once
reported a shortfall.

**Mechanism:** `_retired_ever` barred a market from quoting *and* from
reselection for the rest of the session, and it is never cleared. Twenty
markets were barred inside the first forty minutes. With `max_markets` fixed at
20, a permanent ban list is a slow starvation: every retirement is a slot
spent, and slots are never returned.

Thirty of the retirements were `unrecoverable_book_stale`, which is 12
*consecutive* failed refreshes — the counter does reset on a good pass
(`live_commissioning.py:1587`), so those books really were persistently quiet.
But that describes the book **at that moment**. A book too quiet to refresh at
00:30 is routinely fine at 03:00, and banning it until 07:00 spends a slot to
avoid a condition that has already passed.

**Fix:** `RETIREMENT_COOLDOWN_SECONDS` (1800) replaces the permanent ban.
`_barred_slugs()` bars a market while its exit is still being worked, while its
cooldown runs, and permanently only for recovery-bound markets. Re-retiring
pushes the cooldown out from now, so a market that keeps failing keeps being
barred while one that recovers comes back. Book faults still refuse to release
on flatness alone — time resolves those, not flatness — which keeps the ~45s
retire/release churn closed.

**Coverage:** two tests, both mutation-verified. Restoring the permanent ban
fails the cooldown test; dropping the churn guard fails the release test *and*
the pre-existing `test_an_unquotable_market_is_not_released_just_for_being_flat`.

### `book_stale` recovery expired the refresh it had just fetched — `PENDING`

**Symptom:** 7,535 `book_stale` events in one 7h09m session, and 30 of that
session's 31 retirements were `unrecoverable_book_stale`.

**Mechanism:** `plan()` returns `book_stale` before doing anything else, so the
caller ran recovery, which fetched a fresh book and returned — and the caller
then applied `_recovery_delay`, exponential backoff to a 30-second cap. The
wait outlived the five-second freshness budget the refresh had just satisfied,
so the next visit reported `book_stale` again. Measured interval between
consecutive events on one market: **34–75s, clustered 45–60s**. Every refresh
was wasted, and twelve of them in a row retired the market.

**Ruled out first:** REST contention. Staleness ran flat at ~250–470/hour
whether 19 markets or 6 were active, and *rose* per market as markets dropped
(21 → 49); contention predicts the opposite. The market stream was healthy all
session and zero books were dropped.

**Fix:** replan once, on the book the refresh just fetched, before conceding
the backoff. `replanned` bounds it to one extra attempt per sweep, and the
replan is gated on the book actually being fresh, so a book that never arrives
still backs off instead of spinning. The success path clears
`strategy_state:{slug}`, so a recovering market stops marching toward
permanent retirement.

**Coverage:** two tests, both mutation-verified. Removing the replan fails the
first; dropping the freshness guard fails the second.

### The shutdown exit path left no record of what it tried — `PENDING`

**Symptom:** `🚨 Liquidity Bot exhausted its shutdown exit budget` on
`krovit-sukiho` (2026-08-15) with nothing to explain it. No prices tried, no
indication whether depth refused, whether the market was skipped for a working
order, or whether the send itself threw.

**Mechanism:** shutdown builds its own `PlanAction` and calls
`driver.apply_one` directly, so it never passes the `[action]` logging in
`_decide_for_market`. Every branch of the loop — parking, cancelling,
skipping, refusing on depth, flattening — was silent. Worse, the `except`
around the loop only prints a traceback when `alert()` fires, and `alert()` is
rate-limited by `shutdown_alert_interval_seconds`; inside that window a failure
produced **no Discord message and no local record at all**.

**Fix:** a `[shutdown]` log line mirroring `[action]`, at eight decision points
— `cancel_building`, `parked`, `cancel_working`, `waiting`, `budget_exhausted`,
`depth_refused`, `flatten`, `failed`. The flatten logs *before* the send, for
the same reason the attempt counter increments before it: a transport failure
after the send may still have reached the venue, so a record written only on
success would omit exactly the cases worth having. The failure line is written
unconditionally, independent of alert throttling.

**Coverage:** the line format is tested (it is parsed back when diagnosing).
The call sites themselves are straight-line logging and are **not** covered;
they get their first real exercise on the next live shutdown.
