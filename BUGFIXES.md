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

## 2026-08-16 (second incident)

### A book with no bids killed the runner, and the supervisor would not restart it

Observed live 2026-08-16T19:01:34Z on `a02bbfe`, after two hours of healthy
quoting. Unrelated to the stall below — a pre-existing hole that a one-sided
book finally reached.

**Symptom:** the process is **gone**, not stuck. `status_commissioning.sh`
shows supervisor and runner both stopped, and `supervisor.log` ends with:

```
[supervisor] process exited without proving flat; not restarting
```

**Mechanism:**

```
File "live_commissioning.py", line 1146, in _evaluate_rewards
    spread = state.book.best_offer.price - state.book.best_bid.price
File "models.py", line 90, in best_bid
    return max(self.bids, key=lambda level: level.price)
ValueError: max() iterable argument is empty
```

`best_bid`/`best_offer` are `max`/`min` over a list, so an empty side **raises**
rather than returning `None`, and `_evaluate_rewards` computed the spread with
no book guard at all. One book with every bid pulled took the whole runner down
as `runner_failure:ValueError`.

`Reconciler.plan` has always declined to quote a one-sided book — "quoting needs
BOTH touches, the two-cent rule is defined between them". That rule simply never
reached the reward-evaluation path, which runs earlier.

**Why the bot then stayed down.** Shutdown parked a live position rather than
crossing at a loss (kickoff was 43 minutes out), so it exited non-flat, and the
supervisor deliberately refuses to restart from unproven state. Correct
behaviour — but it converts a one-line crash into an outage lasting until an
operator notices.

**Cost:** ~79 minutes down. No capital lost: the venue was flat when checked
directly (0 open orders, 0 positions) — the parked position's resting sell
resolved on its own while nothing was running.

**A caution about the ledger, recorded because it produced a wrong call
during this very incident.** `data/commissioning.db` stops updating the moment
the runner dies. Reading it after a crash shows the position and working order
*as of the crash*, which is easily mistaken for current exposure — it was, here,
and prompted an unnecessary restart recommendation. **After a crash, query the
venue, not the ledger.** Read-only, no runner required:

```python
from liquidity_bot.probe import client_from_environment
from liquidity_bot.adapters import AsyncReadOnlyGateway
from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
# fetch_open_orders / fetch_positions
```

**Fix:** `_evaluate_rewards` returns early on `not state.book.is_two_sided`,
setting `book_one_sided` on both sides so the market shows up in
`_idle_market_reasons` instead of vanishing. Exits are unaffected — the
reconciler plans those, needs only the side being sold into, and uses no
economics.

Regression test: `test_a_one_sided_book_does_not_take_the_runner_down`.
Mutation-checked — disabling the guard reproduces
`ValueError: max() iterable argument is empty` exactly.

**Second fix, same day: the blast radius.** The one-sided book was the trigger;
the reason it cost 79 minutes is that *any* exception in servicing ended the
session. `_plan_and_apply` already retires a market whose `reconciler.plan`
raises, precisely so one market cannot stop the others — but that guard sat
only around the `plan` call, and everything servicing does *around* it was
unprotected. `_evaluate_rewards` is one of those, which is how a book with no
bids reached the top of the run loop.

The servicing loop now lives in `_service_bound_markets`, which gives every
market the treatment planning already had: report, retire, keep going.
Retirement stays bounded — ten rounds per market, and a full slate of
retirements still stops the session — so a local defect is absorbed while a
systemic one still surfaces. `RuntimeInvariantViolation` is deliberately NOT
absorbed: an ownership violation or a loss stop is about the process, not the
market, and remains terminal.

Tests: `test_a_servicing_failure_retires_one_market_not_the_session` and
`test_a_runtime_invariant_still_stops_the_session` — the second exists so the
guard can never be widened into swallowing a safety event. Mutation-checked:
narrowing the `except` reproduces the original crash.

**Still not fixed:** nothing alarms when the process is up and resting nothing.
Both of today's outages were invisible until someone looked. Same theme as the
"up but quoting nothing" alarm in FINDINGS — the failures that cost the most
are the ones nothing announces.

---

## 2026-08-16

### One order we lost sight of wedged every stream bind and stopped all quoting

Observed live 2026-08-16 15:33Z on `efbd332`. Fixed same day.

**Read the mechanism below before the fix.** The first diagnosis of this bug
was wrong in a way worth recording: it concluded the bot "never retried" the
failed bind. It retried four times. Every retry failed *identically*, for a
reason one layer down — and a fix aimed at the retry alone would have shipped a
loud infinite loop instead of a silent hang, and been called done.

**Symptom:** the process stays up and healthy-looking — supervisor and runner
both alive, REST still working, account flat — but it places **nothing**, for
hours. `scripts/status_commissioning.sh` shows a runner pid and the log is an
endless two-line loop, one pair every ~7s, across *every* active market:

```
[runtime-recovery] component=strategy_state:<slug> attempt=N error=AdapterError: execution_state_unknown
[quarantine] called_by=_recover_strategy_state:2554
```

The tell that separates it from the §6g churn: **`book_stale` stops appearing
entirely** at the moment it starts. `Reconciler.plan()` tests `state_known`
*before* book age, so once the gate is dead the book check is never reached.
On this run: 997 `book_stale` between 15:07 and 15:36, then zero, and 144
`execution_state_unknown` from 15:38 onward with no `[action]` line after
15:36:31.

**Mechanism, in the order it actually happened.**

**1. An order goes absent and nothing ever asks about it again.** At 15:33:50Z
`BXFF5TKQGDK5` (`aec-itfwo-annlaz-frapar-2026-08-16`, 1.66 of 5 filled) left a
complete open-order snapshot and was marked `UNKNOWN_ABSENT` /
`missing_from_complete_snapshot`. That status is deliberately *not* a working
state — it records our failure to see an order, not a venue transition. But
`_rest_reconcile` re-reads exactly two sets from the venue: `working_orders()`
and orders behind `unresolved_intents()`. An absent order is in neither, and
its intent was already `RECONCILED`. **So it was never asked about again**, and
`UNKNOWN_ABSENT` became permanent.

**2. One absent row makes the account-wide gate false forever.**
`risk_state_known(market_slug)` scopes its absence test *only when a slug is
passed*. `_wait_for_initial_private_state` calls `gate.ready(self.ledger,
now=now)` with **no slug** — account-wide — so it counts that one row and can
never return true.

**3. Every stream bind therefore times out.**
`_open_streams_for_active_market` ends in `_wait_for_initial_private_state`,
which loops until `gate.ready()` or its 15s deadline. Readiness could no longer
arrive, so `private_stream.next_event(timeout=remaining)` ran out and raised a
bare `asyncio.TimeoutError` — which is why the log line carries no message.

**4. A failed bind closes both streams and disconnects the gate.** Its `except`
runs `gate.on_disconnect()`, closes both streams, re-raises. The caller logs it
as an ordinary recoverable error, backs off 60s, and continues the run loop —
now holding two closed streams with `gate.connected = False`. Every market
reports `execution_state_unknown`, and the answer to unknown state is to
quarantine.

**5. It retried, and the retries could not have worked.** This is the part the
first diagnosis got wrong. Top-up re-ran three more times and each bind failed
identically:

```
15:37:42  component=market_top_up attempt=1 error=TimeoutError:
15:41:28  component=market_top_up attempt=2 error=TimeoutError:
15:56:28  component=market_top_up attempt=3 error=TimeoutError:
16:03:44  component=market_top_up attempt=4 error=TimeoutError:
```

Four attempts, four identical failures, ~19-21s apart from their `[top-up]`
line — `_rest_reconcile` plus the 15s wait. No retry could ever succeed while
step 1 held. Neither `_recover_private_stream` nor `_recover_market_stream`
fired either: both trigger on a `next_*` task *raising*, and reading a stream
we closed deliberately never raised — the session logged not one
`component=private_stream` line. The supervisor could not help because the
process never exited.

**Ruled out** — do not re-chase these:

- *Unresolved intents (§6g again).* All 12,052 rows in `execution_intents` are
  `RECONCILED`.
- *Unowned `venue:` orders*, the one genuinely account-wide test left in
  `risk_state_known()`. Zero rows in any working state.
- *That the absent order only affected its own market.* It was tempting —
  `annlaz-frapar` was released by the very top-up that failed, so it looked
  irrelevant to the other 19, and the absence test *is* per-market. The trap is
  that `_wait_for_initial_private_state` asks account-wide, where the scoping
  does not apply. Scoped callers and unscoped callers of the same function
  disagreeing is what made this hard to see.
- *A dead stream reader.* `fd=15` held a 39,772-byte `Recv-Q` backlog on first
  look, which looks exactly like a stalled consumer — it drained to 0 within
  three seconds. Those sockets are the REST client, still working.

**Cost:** no capital lost — the account was flat with zero open orders
throughout, and every order from the 15:07 cohort had already been cancelled by
15:36. The cost is pure downtime: reward accrues only for resting, and the bot
rested nothing for the entire window.

**Fix, in three parts.** Only the first is the root cause; the other two are
what turned a recoverable fault into 36 silent minutes.

**1. Re-ask about absent orders (the actual fix).** `_rest_reconcile` now
sweeps `ledger.absent_orders()` in addition to working orders and unresolved
intents, so an `UNKNOWN_ABSENT` row gets re-retrieved until the venue gives it
a terminal state. Absence is still never read as cancellation — only a real
terminal state clears the row, per §6c.

That sweep is deliberately **best-effort**, unlike the strict one above it.
Working orders are live risk and a failure there must stop the reconcile;
absent orders are already lost, and letting a persistent 404 abort the whole
reconcile would take down every bind that depends on it — swapping one
permanent wedge for another. Failures are reported under `absent_order` so
repeats escalate through the normal attempt counter instead of sitting silent.

**1b. Reconcile at startup when an absence is pending.** The same blind spot
sat in `preflight`, which only reconciled `if working_orders(owned_only=True)
or unresolved_intents()`. An absence is neither, so the sweep that clears it
never ran, and the absent row survives a restart in the ledger.
`ControlledTrialStartup` then blocks on "exchange order ownership or state is
unknown" — a blocker `_startup_recovery_is_scoped` does **not** allow — so
preflight raises `CommissioningRefused` and exits 2, which the supervisor never
restarts. **One order we lost sight of would have stopped the bot from ever
starting again.** Caught by mutation-checking before the restart, not after:
reverting this line reproduces `CommissioningRefused: exchange order ownership
or state is unknown` exactly.

**2. Close streams before replacing them.** `_open_streams_for_active_market`
now closes the outgoing pair first. `_rotate_reward_market_inner` already did
this itself; the top-up path did not, so **every top-up leaked a private and a
market socket** — four in this session alone, on an account whose private feed
is already split across several connections to stay under the per-connection
subscription cap. Doing it at the bind covers every caller. Best-effort: a
socket being discarded must never fail the bind replacing it.

**3. A failed bind now leaves a standing request to rebind**, and the run loop
honours it.

- `_open_streams_for_active_market` sets `self._streams_need_rebind = True` on
  its failure path and clears it on success. The rotation path composes
  correctly for free: when its first bind fails and the *restore* bind then
  succeeds, the flag ends up cleared, which is right — the streams really are
  healthy again.
- The run loop checks the flag at the top of each pass, before `asyncio.wait`.
  Waiting on closed streams can only time out, so it cancels both `next_*`
  tasks, recovers, and recreates them against the new stream objects.
- `_recover_bound_streams` is the new recovery. The two existing ones each
  repair a single side and assume the other is healthy — precisely the
  assumption a failed bind breaks — so this one retries the whole bind, with
  the same backoff, quarantine-first and stop-reason checks as its siblings.
  It also sends a Discord alert on entry, so the next occurrence is not silent.

Note the deliberate cost: it quarantines before rebinding, so a failed top-up
now cancels resting orders that the top-up path was specifically designed to
leave alone. That is the right trade — with the private stream down a fill can
land unseen, and quoting blind is what the gate exists to prevent.

Without part 1, parts 2 and 3 would have shipped a *loud infinite retry* — the
bind would have failed forever exactly as it did live, just with a Discord
alert attached. Worth remembering the next time a recovery looks like a fix.

Regression tests. Root cause, in `LiveCommissioningTests`:
`test_rest_reconcile_reasks_about_an_absent_order`,
`test_preflight_clears_an_absence_left_by_a_previous_session`,
`test_an_unresolvable_absence_does_not_break_the_reconcile`. Recovery, in
`TopUpSafetyTests`: `test_a_failed_bind_marks_the_streams_for_rebind`,
`test_a_successful_bind_clears_the_rebind_request`,
`test_recovery_rebinds_both_streams_and_resumes`. All mutation-checked —
emptying the absent sweep fails the first two, flipping the rebind flag to
`False` fails two of the last three.

**Verified in production 2026-08-16T16:44Z**, restarting on `a02bbfe` with the
offending row still in the live ledger. Eight minutes in: **18 orders resting
across 18 markets**, one transient `execution_state_unknown` (the ordinary
in-flight cancel), and every other recovery line `book_stale` — which is the
normal startup state, fired once per market before its first book. Compare the
broken window: 144 `execution_state_unknown` and zero placements.

**The stuck row did NOT resolve, and the reason is worth recording.** The sweep
asked; the venue answered with the record it had already given us, whose
`event_at` is still `15:14:37.322033`. `_accept_event` keys on
`order:<id>:<event_at>`, so that answer deduped against the copy applied at
15:14:45 and changed nothing. And even without the dedupe it would not have
helped: that record is non-terminal, so `resolves_absence` would not fire. The
venue has dropped the order from its open-order snapshot while still serving a
working status for it on `GET /v1/order/{id}` — an inconsistency we cannot
resolve from our side.

So the residual risk is real and it landed. **It now costs nothing**, which is
the whole point of the containment work: `annlaz-frapar` is not bound (its game
started long ago) and the row blocks only that dead market. Before the
containment change this same row stopped all twenty.

If it ever needs actually clearing, the honest options are an operator
adjudication or letting a replayed venue record resolve an absence despite the
dedupe. The latter is a real change to event-identity semantics and should not
be made casually — a replay is normally exactly what dedupe exists to reject.
Neither is worth doing while the cost is zero.

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

### A reporting metric nearly stopped shutdown proving flat — `PENDING`

**Symptom:** caught in tests, not production. Adding a `resting_markets` count
to the summary broke `test_shutdown_parks_a_position_on_its_resting_exit`.

**Mechanism:** the count was built from `_working_orders()`, which raises
`RuntimeInvariantViolation` for a working order with no owned place intent.
The summary is also sent from inside the shutdown loop, whose `except` treats
any exception as a failure to prove flat — so a *reporting* metric could keep
a genuinely flat process from stopping, and a stop that will not complete is
what previously forced `kill -9` and left the state that produced recovery
runs on every restart.

**Fix:** read `ledger.working_orders(slug)` directly. The count needs only
whether rows exist, not the intent-to-side mapping that carries the invariant.

**The rule this is the second instance of:** anything that exists only to be
*reported* must not be able to raise. `_ledger_bytes_on_disk` was written that
way deliberately and this was not.

### Entry opened positions the exit was already flattening — `PENDING`

**Symptom:** measured 2026-08-16, 8 of the 9 markets resting quotes started
within fifteen minutes, and one was **3.6 minutes** from kickoff — already
inside the 300-second pre-kickoff flatten window.

**Mechanism:** the quoting gate in `_flat_plan` was `now < game_start`, so the
bot opened quotes in the same window `_inventory_plan` has already committed to
flattening *at any price* — no loss cap, no depth haircut. A fill there is
force-crossed almost immediately, so the position was taken on purpose and
closed at a taker price on purpose. FINDINGS measured pre-kickoff crossings as
the worst exit group at **-0.075/contract median**, against -0.059 for the
inventory clock.

**Fix:** gate entry on `seconds_to_event > force_exit_before_start_seconds`
rather than on kickoff. Emptying `desired_sides` also cancels anything already
resting, so the bot withdraws at the deadline instead of waiting to be filled
into it. `_flat_plan` only runs when flat, so held positions and their reducing
quotes are untouched.

**Still open:** whether to widen the gate further (T-15, T-30) is NOT decided.
Reward is paid for resting, so quoting a market that never fills is free money;
the risk is only on fills, and near kickoff is when fill probability and adverse
selection are both highest. That needs fills bucketed by time-to-kickoff — see
WATCHLIST.

**Coverage:** three tests, mutation-verified. Restoring the `now < game_start`
gate fails both the "opens nothing inside the window" and "withdraws at the
deadline" cases.

### The active set froze at startup and decayed all day — `PENDING`

**Symptom:** measured 2026-08-16 14:45 UTC. Of 19 markets serviced in a
20-minute window only 7 took any action, 4 got placements, and 5 held a resting
order — while **26 viable candidates sat unselected**. Four of the idle markets
had kicked off **45 minutes earlier** and were still being serviced, one book
refresh each per ~70 seconds, forever.

**Mechanism:** rotation is gated on `_rotation_blockers()` and
`_discovery_is_free()`, both of which require a **whole-account** flat state —
no working orders anywhere. Full rotation quarantines every resting order and
rebinds from scratch, so that gate is correct *for that operation*. But with
twenty markets something is almost always resting, so the gate is essentially
never satisfied. **Rotation ran exactly twice this session, both at startup**
(two distinct `scan_seconds` values across 13 summaries, then nothing). The set
was frozen from boot while its markets kicked off one at a time.

This is a second, independent cause of the 19 → 11 → 8 → 7 → 6 decay measured
the previous session, which was attributed solely to the retirement ban list.
The ban was real, but even with it fixed the set cannot refresh. It also
explains much of the post-fix `book_stale` volume (~2,190/hour): a live game's
book does not update in the pregame feed, so every service cycle on a dead
market reports stale.

Note what the earlier fixes did and did not do. The cooldown stopped kicked-off
markets being *fatal*, and `_retired_past_kickoff` stopped them counting as a
*fault*. Neither makes the **slot get reused**.

**Fix:** `_top_up_active_markets`, a narrower path than rotation. It releases
only markets that are past kickoff **and** flat, keeps every other market and
its resting orders untouched, and never quarantines. Streams restart because
they cannot subscribe incrementally, but a stream restart does not cancel
resting orders — those live on the venue and keep earning through it. That is
the whole difference from full rotation. It runs when the periodic selection is
due and full rotation is blocked.

**The safety property:** a started market still HOLDING inventory is never
released, because that is its exit path and releasing it would strand the
position with nothing working the exit.

**Coverage:** three tests, both guards mutation-verified. Removing the flatness
check fails the held-position case; removing the kickoff check fails the
pre-kickoff case.

### Review of the top-up found two ways it could kill the session — `PENDING`

Self-review of `112d58b..HEAD`, all five findings in code written the same day.

1. **Empty active set (high).** With every market expired and no replacements
   available, `_bind_markets(())` left `market_slug` raising RuntimeError and
   the next slug-less caller took the run loop down. Now the release is skipped
   entirely: holding dead markets beats holding none.
2. **Retry storm (high).** A failing top-up did not advance `next_selection`,
   so the branch re-entered every iteration, cancelling and rebuilding both
   stream tasks forever with no backoff. Now backs off 60s, matching rotation.
3. **Scan across a flatten deadline (medium).** The top-up runs precisely when
   `_safe_to_scan()` is False, which includes holding a position — and I did
   not think hard enough about *why* rotation refuses there. The blockers are
   not only about quarantining orders; they keep a 25-second blocking scan away
   from live inventory, during which `_inventory_plan` cannot issue the
   pre-kickoff flatten. Now defers while any held position is within twice the
   flatten window. Unknown game time counts as near.
4. **Predicate with a side effect (low).** `_book_is_fresh_enough_to_plan`
   called `state_for`, resurrecting `_markets` entries that `_bind_markets`
   deliberately discards. Reads `.get` now.
5. **Truncated idle counts (low).** The summary showed four reasons, so counts
   could fail to sum to `active - resting` on the field whose job is answering
   "are we at capacity". Folds the remainder into `other`.

**Coverage note worth keeping:** the first tests written for 1 and 2 were
**vacuous** — the runner fixture ships `auto_select_reward_markets=False`, so
`_top_up_active_markets` returned at its first line and never reached the
logic. Both mutants survived, which is the only reason it was caught. The
tests now arm auto-select explicitly and assert their preconditions.
