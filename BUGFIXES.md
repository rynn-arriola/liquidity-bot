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
