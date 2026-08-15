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
