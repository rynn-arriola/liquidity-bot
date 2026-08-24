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

## 2026-08-24

### FIXED LOCALLY — private reconnect terminal repair serialized the slate, then a stale cooldown kept proven-flat owners barred

**Symptom.** Three private disconnects at 07:20Z, 11:47Z and 12:28Z recovered
fresh account visibility in 36-40 seconds, but each global quarantine created
29-31 CANCEL intents across 18-19 markets. Between 17 and 19 markets then
retired for `unresolved_mutation_timeout`. Even after the ledger returned to
zero unresolved intents, zero `UNKNOWN_ABSENT` orders and flat positions, most
of the recovered slate stayed unable to quote for thirty minutes. The alert's
claim that a retired owner would not quote again that session was inaccurate.

**Mechanism.** Scoped private recovery was correct: it resumed unaffected
owners without weakening the rule that absence is never cancellation. The
background repair path then asked for one owner per pass and repeated complete
open-order, position and execution snapshots on every pass. A disconnect-sized
set therefore took 2.5-11.6 minutes to acquire terminal proof. The fixed
60-second containment threshold retired owners that were still converging.
`_release_retired_if_flat` later removed `_retired`, but left the generic
1,800-second `_retired_until` value, so positive terminal and flat proof did
not restore availability.

**Fix.** Attributable unresolved intents now receive focused GET-by-ID repair
in round-robin batches of five owners. Reads within one batch are independent
and concurrent, while the existing shared limiter still starts no more than
four REST calls per second. This removes repeated full-account snapshot work
from the per-owner path and prevents one order's NotFound retry ladder from
hiding other owners' terminal answers. Complete account snapshots remain on
their normal cadence. Unattributable ambiguity still uses strict account-wide
reconciliation and fails closed.

An `unresolved_mutation_timeout` owner remains unable to quote while its intent
is unresolved, an order works or a position is nonzero. Once all three tests
clear through positive venue evidence, release also clears only that reason's
transient cooldown. Book/plan/action retirements retain their generic cooldown,
positioned owners retain exit planning, and the alert now says exactly when an
ambiguity retirement resumes. `[unresolved-repair]` reports owner/id counts,
resolved and remaining intents, failures, elapsed time and the five-owner
scope, so future diagnosis does not rely on the dashboard's cumulative
`retired` count.

**Review and regression proof.** The focused suite covers twenty simultaneous
recovery cancel owners, terminal plus NotFound owners in the same batch,
repeated successful private recovery while one owner stays ambiguous, a
positioned retired owner that cannot release, unchanged building-PLACE refusal
for an unresolved owner, conflicting exchange-id ownership that remains
account-wide, and the existing twenty-slot freshness tests. All
539 local tests pass. Mutation checks were performed: batch size five to one
fails the twenty-owner four-pass test; concurrent to serial reads fails the
mixed-NotFound overlap test; retaining `_retired_until` fails the transient-bar
release test; removing the ownership-conflict check fails its account-wide
scope test. Production deployment and live reconnect acceptance are pending.

**Cost.** The three measured recurrences retired 18, 19 and 17 markets and
repeatedly reduced quoting for roughly a half hour. No unsafe flat inference
was found. The last run was gracefully stopped only after the operator closed
the remaining position and canceled all orders; shutdown proved flat.

---

## 2026-08-23

### FIXED AND DEPLOYED — private-stream recovery waited for every cancel, then unrelated order 404s serialized every market

**Symptom.** The runner stayed alive but stopped trading after a private
WebSocket disconnect at **21:00:01Z**. Recovery reached attempt 35 at
21:24:18Z while every attempt reported an unresolved cancel in one market.
It eventually recovered without intervention and began placing again at
21:25:48Z, but the recovery had held all twenty markets dark for about
24 minutes. The new placements then exposed a second form of the same
head-of-line bug: single-market `execution_state_unknown` recovery spent
roughly 17–33 seconds re-reading 3–8 unrelated order IDs that returned
`NotFound`, then moved to the next market.

**What was disproved.** The cancel intents were not permanently dead, and the
GET-by-ID repair path was not missing. Individual `GET /v1/order/{id}` calls
eventually returned terminal states—the count fell from 29 to 14 to 6 and the
loop escaped. The defect was requiring the slowest market's terminal proof
before restoring account visibility for every other market.

**Mechanism.** `_recover_private_stream` called global quarantine, whose
terminal postcondition rejected any unresolved cancel. A complete open-order
snapshot deliberately cannot turn absence into cancellation, so ordinary
venue consistency lag kept the loop closed even though the ambiguity was
owned by known markets. Separately, `_rest_reconcile` retrieved every owned
working/unresolved exchange ID and treated every read failure as account-wide.
Therefore `_recover_strategy_state(slug)` could not repair `slug` until
unrelated markets' NotFound retries completed too.

**Fix.** Private recovery now cancels what has positive working-order evidence,
obtains complete REST order/position snapshots, reconnects the private stream,
and resumes unaffected markets after fresh private state. Unresolved intents,
`UNKNOWN_ABSENT` rows, and attributed GET failures continue to close only their
own market's execution-state gate until positive terminal proof arrives.
Single-market quarantine is strict for its requested market but does not issue
individual reads, absent repairs, or uncertain-cancel retries for other
positively-attributed markets; complete account snapshots still update every
market. Default/global reconciliation stays strict, and unattributable or
conflicting ownership always remains account-wide fail-closed. Absence is still
never cancellation.

Genuine private reconnect failure is capped at 12 attempts. Exhaustion returns
a runner failure; shutdown then preserves invariant 53: only a separately
proved-flat account exits restartable, while any open or ambiguous state exits
terminal and is not restarted.

**Proof.** Focused regressions reproduce both production shapes: a foreign
market's GET-by-ID NotFound does not block the requested market's quarantine,
and a cancel accepted then omitted from the complete snapshot while GET-by-ID
still returns NotFound leaves its owner unknown but restores the neighboring
market and creates no new orders during recovery. The default strict-sweep
regression still raises, and a permanently failing private stream stops after
exactly 12 attempts without claiming flat. Two final quarantine edge tests
prove that a complete snapshot's positive OPEN evidence still licenses the
cancel when the first ID read lags, and that one unresolved owner cannot stop
known orders in later markets being canceled before terminal failure. Full
local suite: **517 tests passing**.

The first deployment passed all 517 production tests in 38.286 seconds, then
live acceptance found one incomplete part of the repair. At 21:53:55Z one
market entered `execution_state_unknown`; `_rest_reconcile` correctly tolerated
two unrelated read failures but did so only **after spending 20 seconds** on
their GET retry ladders (`scoped_order_read_failures` at 21:54:15Z). Tolerating
the result was not enough—the serial scheduler still paid the latency. The
final local revision skips those unrelated individual reads entirely and logs
`scoped_order_reads_skipped`; target terminal proof and account snapshots stay
mandatory. Routine ten-second and mid-sweep freshness renewal now uses complete
snapshots without individual order reads, and the background unresolved repair
reads one owning market per pass in round-robin order. Thus a persistent 404
cannot monopolize either shared freshness or the repair queue. The intermediate
runner was stopped for this correction.

The dedicated twenty-slot tests still prove all twenty slots are planned and
both shared equity and execution-state freshness renew mid-sweep. Slot cap,
selection, decision cadence, and economics are unchanged. Final local suite:
**518 tests passing in 28.137 seconds**, including round-robin unresolved-owner
coverage. Source commit `501c743` matched all six deployed file hashes and all
**518 production tests passed in 37.105 seconds**. The old runner first proved
flat at 22:09:48Z; the final runner started at 22:11:14Z. Live acceptance saw
`scoped_order_reads_skipped=8` at 22:12:07Z and new actions at 22:12:10Z, so
the former 20-second foreign-ID pause is gone. The current slate had only seven
economically viable markets; it rested 11 orders across all seven with zero
positions. That is supply below the unchanged 20-slot capacity, not scheduler
starvation.

---

## Terminal cancels waited behind the full market slate before replacement

**Symptom.** After the 2026-08-23 private-stream recovery fix, the bot was live
and moving across the slate but an instantaneous sample often showed only
12-14 orders. Markets repeatedly disappeared between a touch-change cancel and
their replacement.

**Mechanism.** The reconciler correctly cancels first and refuses replacement
until terminal proof. Confirmation was fast (1.22s median over 80 current-run
pairs), but a confirmed owner then waited behind periodic account work,
per-market REST service and the next full decision sweep (39.45s median after
confirmation, 118.63s p90). Twenty-one replacements returned at the exact
cancelled price and 48/80 moved no more than one tick, so that dark time often
bought no durable price improvement.

This did **not** originate in the preceding recovery patch. Before the 21:00Z
outage the comparable median cancel-to-place gap was worse at 122.4s. The
recovery patch fixed the distinct 25-minute all-market deadlock and remains in
place.

**Fix.** Terminal CANCEL transitions from private or REST state queue only the
positively-owned market for immediate re-service/replan ahead of the full
slate. Ready stream events are drained after priority REST work and economics
are refreshed again before planning. Unresolved intents, unknown ownership,
stale execution/equity/book state, and uneconomic quotes still refuse exactly
as before. Rebind removes released owners, sets deduplicate, and an ordinary
sweep consumes a marker it already serviced. `[priority-replan]` records the
elapsed fast-path time and owners for production verification.

**Verification.** Focused tests cover private terminal proof, REST terminal
proof, one-owner decision scope, released-market cleanup, stream draining and
both twenty-slot freshness boundaries. Mutation-checking removal of the queue
write makes the terminal-private regression fail. Full local suite: **522 tests
passing** before deployment. Source commit
`abbbd7379728e3184ac73247dc5cc713214d1005` matched every deployed file hash,
and all **522 tests passed on production in 38.392 seconds** before restart.

Live acceptance observed three priority batches covering seven owner
occurrences in **2.6s, 6.6s and 2.8s**. The first six same-side replacements
landed **19.3-33.7 seconds** after touch cancel, versus the prior 40.60s median
and 120.09s p90. Fresh economics also proved the safety boundary: one priority
owner cancelled its now-inappropriate opposite order rather than blindly
recreating the cancelled side. The live account showed zero positions, zero
`UNKNOWN_ABSENT` orders and no runner failure. A sample reached 21 working
orders across the 11 currently viable/bound markets; `max_markets=20` and its
dedicated scheduler tests were unchanged.

---

### FIXED AND DEPLOYED — the 20-slot scheduler let shared freshness and one market's state starve the slate

**Symptom.** Restoring the six-hour selection horizon produced abundant supply
but almost no resting orders: 98 viable markets, 20 bound slots, 40 eligible
sides, and 0/20 quoting. At the live inspection around 03:31Z, every one of the
exact twenty bound markets produced a valid placement when evaluated alone,
but placements had reached only four markets (22 total, distributed 14/5/2/1)
and only one order was then working.

**Mechanisms.** These were independent forms of head-of-line blocking:

- balance was read before serial REST service, while equity freshness expired
  after 15 seconds; later frames silently lost entry eligibility;
- any unresolved mutation caused an account-wide `continue`, although the
  ledger and private-state gate already scope it to the owning market;
- the maximum recovery delay from one market slept the whole runner;
- queued WebSocket data was timestamped when dequeued, not received, and full
  books accumulated without bound;
- selector minimum-size and price-floor gates differed from execution;
- the summary counted impossible universe demand and classified empty plans as
  recent quote activity;
- shutdown accepted an empty venue snapshot despite unresolved/absent creates.

This does **not** revive the disproved claim that raw `book_stale` counts prove
REST failure. The measured stale recoveries usually succeed on attempt one.
The defect is the time those successful operations consume before later slots
reach the separate equity and state gates.

**Fix.** The runner now refreshes equity at response receipt and again before
the decision freshness margin expires; plans all due markets through a
per-market scheduler; skips only the market owning an unresolved intent; and
stores recovery deadlines per slug without a global sleep. Full market books
coalesce to the latest pending snapshot per slug, market/private events retain
socket receipt timestamps, and current private backlogs drain in wire order
before REST work. Empty plans no longer update `_last_action_at`.

Selection now shares the configured 0.10–0.90 entry floor and accepts a market
minimum quantity up to the authorized quote size. Shutdown flat proof refuses
unresolved intents and `UNKNOWN_ABSENT` orders. The Discord unmet count is
capped by the twenty-slot authorization while the full viable universe remains
reported separately.

**Proof.** New tests exercise each regression, including a simulated twenty
slot sweep in which every slot is planned and equity refreshes halfway through,
an unresolved intent that skips only its owner, and a 30-second delay that does
not stop the unaffected slot. Full local suite: **506 tests passing**. The same
**506 tests passed on the droplet in 42.065 seconds** after the first deployment.
The final edge review then found one remaining timing window: a stream task can
consume the first event while serial REST service is running, leaving it held
outside the adapter queue. Draining only the queue after service would process
newer private mutations before that held event, while waiting for the next loop
could make the decision gate stale. `_consume_ready_stream_tasks` now applies
the held result first, drains the complete finite backlog in order, and runs
both when the loop wakes and immediately after REST service. Market books drain
as latest-per-slug snapshots; private mutations retain wire order. Two new
regressions bring the full suite to **508 passing locally and on the droplet**
(40.514 seconds on the production virtual environment).

The 2026-08-23T04:37:47Z live acceptance then proved the scheduler repair:
**33 placements reached all twenty distinct markets** instead of the old four.
At one snapshot, 26 orders were working across 16 markets with zero unresolved
intents, absent/unknown orders, or positions. The run sent 37 orders before the
operator stopped it flat at 04:45:13Z.

That broader sweep exposed a final execution-state freshness edge. The private
or REST proof is valid for 30 seconds, but serial service can now legitimately
span that boundary. Live `execution_state_unknown` quarantines appeared at
04:40:39Z, 04:42:00Z, 04:42:50Z, and 04:44:28Z. This was fail-closed and scoped
to each owning market, but over time it could cancel otherwise healthy tail
slots. The runner now refreshes account execution state at a five-second
pre-expiry margin, stamps REST freshness when the multi-call response finishes,
and makes at most one failed account-wide refresh attempt per sweep. A failed
refresh never marks state fresh; the 30-second gate still refuses entries.

Five new boundary/failure/slow-response/twenty-slot regressions bring the full
suite to **513 passing locally and on the droplet** (40.881 seconds in the
production virtual environment). Source commit `2eed575` is deployed. The
monitored restart remained alive beyond the prior failure window: at 10m18s it
had 64 actions across 18 action-producing markets, 26 working orders, zero
`execution_state_unknown` quarantines or runner failures, and zero unresolved
intents, absent/unknown orders, or positions. The bot remains running.

**Not changed.** The six-hour horizon, 4 req/s REST ceiling, twenty-slot cap,
quote size, exposure limits, and 90-second reward-eligibility dwell. The last
one remains a measured open question rather than a guessed policy change; see
`WATCHLIST.md`.

---

### `minimum_entry_price` lives only in the reconciler, so the selector binds markets that can never be quoted

**Symptom.** Slots bound and silently idle. At 2026-08-23T00:18Z the venue held
**1 working order across 20 slots** while 17 markets were bound and the
selector reported 24 viable. Nothing was faulted: rotation unblocked, 0
unresolved intents, 0 `UNKNOWN_ABSENT`, 0 positions, no risk stops. The log
gives no reason at all, because a market the reconciler declines to act on
produces no `[action]` line.

**Mechanism.** `minimum_entry_price` (0.10) was added to `ReconcilerPolicy` on
2026-08-21 to refuse opening near either end of the book. It exists in exactly
one place:

```
$ grep -rn minimum_entry_price src/ scripts/
src/liquidity_bot/reconciler.py:90:    minimum_entry_price: float = 0.10
src/liquidity_bot/reconciler.py:648:        floor = self.policy.minimum_entry_price
```

`market_selector.py` does not model it. So `viable_sides` — which is
`expected_net_value > 0`, and knows nothing about the floor — marks an extreme
book viable, the selector binds it, and `_flat_plan` then drops every side with
`drop_cause = "price_too_extreme"` on every sweep, forever. The market holds
its slot until the next rotation without ever placing an order.

Measured at 00:22Z against the 17 bound markets:

```
market                              bid    ask   gate
aec-itfme-yukmoc-shilin-2026-08-23  0.920  0.970  BOTH sides price_too_extreme
aec-itfme-lukmcm-edwmar-2026-08-23  0.820  0.910  offer price_too_extreme
aec-itfwo-yukmat-jiapan-2026-08-23  0.850  0.940  offer price_too_extreme
aec-itfme-kehun-chuhou-2026-08-23   0.090  0.270  bid price_too_extreme
```

One slot fully dead, three half-dead, out of seventeen.

**Why it is invisible.** `price_too_extreme` appears **zero times** in the
entire log. `drop_cause` only reaches a log line through
`side_no_longer_desired:<cause>`, which requires a *working order to cancel* —
and this gate fires before any order is ever placed. Compare
`side_no_longer_desired:reward_ineligible`, which has 2,340 occurrences,
purely because that condition can arise after a placement.

So the gate is doing its job (it was worth ~$40 of the 2026-08-21 loss
buckets) and is simultaneously unobservable and un-costed.

**Fix.** Superseded by the 20-slot scheduler fix above. The selector now shares
the configured floor with the reconciler and regression coverage proves an
extreme market is rejected before binding. The second observability request
remains useful but is no longer required to identify this known gate. Two
parts, and the second matters more than the first:

1. Teach `market_selector.py` the same floor so an unquotable market is never
   bound. **A gate that lives in two places is one where changing a single
   side does nothing** — this is that ground rule again, in the other
   direction: a gate that lives in *one* place is one the other side cannot
   respect.
2. Log the drop, so a bound-and-idle slot says why. Today the runner records
   only what it *did*; a sweep that decides to do nothing writes nothing, which
   is the same blind spot `usable_markets.py` was built to fix for
   `[selection]` and `book-stale-why` was added to fix for stale books.

**Cost.** Unmeasured, and small — roughly one slot in seventeen at the sampled
moment. Recorded because the mechanism is general: every future reconciler-only
gate will bind markets it cannot quote, and none of them will leave a trace.

---

## 2026-08-21

### A live match sat in the pregame universe, and cost $9.29 at 7 cents

**Symptom.** One position settled without ever being closed --
`aec-cs2-ter-yl-2026-08-21`, short 10, opened 17:20:09 and gone by the time
anything looked. The venue confirmed no exposure afterwards; the money was
already lost.

```
market       aec-cs2-ter-yl-2026-08-21
status       MARKET_STATUS_RESOLVED
outcomes     ["TrafficPills Esports", "Younglings"]
prices       ["0", "1"]            <- Younglings won
our fill     17:20:09  BUY_SHORT  10 @ 0.07  MAKER   outcome=Younglings
ledger cost  9.29                            -> total loss
```

**Mechanism.** The venue reports `gameStartTime` **15:30**. The bot bound the
market at **16:33** -- an hour after that -- which the selector only permits
when kickoff is in the future, so the catalog was still advertising a later
start. `gameStartTime` is corrected after the fact, and until it is, a live
match sits in the pregame universe looking perfectly quotable.

What followed is the ordinary quoting loop doing its job on a market it should
never have seen:

```
17:03  SELL 10 @ 0.19
17:06  SELL 10 @ 0.11     <- chasing the touch down
17:12  SELL 10 @ 0.11
17:16  SELL 10 @ 0.08
17:20  SELL 10 @ 0.07  FILLED
17:22  book empty on both sides
       market resolves
```

That collapse from 0.19 to 0.07 was one side winning in real time.
`resting_price_left_the_touch` followed it all the way down and got filled on
the loser at 7 cents.

**Scope.** 2 of 53 markets with fills that day were filled after their
(corrected) kickoff -- this one 110 minutes late, `aec-itfwo-gioped-liszaa`
71 minutes late. The other 51 were correct. So ~4%.

**What does NOT fix it.** Trusting the clock harder. The clock is the thing
that is wrong, and it is wrong in a direction no local check can detect: the
bot cannot know that a start time will later be corrected backwards. Comparing
the bulk catalog against `retrieve_by_slug` was tested and they agree exactly
(25 of 25 in-horizon markets, zero disagreement), so there is no second venue
opinion to consult either.

**Fix applied 2026-08-21.** Refuse to OPEN within 10 cents of either end.
Measured across 321 markets, bucketed by the most extreme price each traded at,
in the frame we actually paid in:

```
0.00-0.05     8 markets   -13.65   -1.71 each
0.05-0.10    28 markets   -41.05   -1.47 each
0.10-0.15    26 markets   -21.41   -0.82 each
0.15-0.25    45 markets   -20.61   -0.46 each
0.25-0.50   214 markets   -38.28   -0.18 each
```

Loss per market rises about **9x** toward the ends, monotonically across all
five buckets -- so this is a gradient, not a curve fitted to one trade.
Everything under 0.10 is **11% of markets and 40% of all losses**. Section 1
says the same thing from the fee side: the maker rebate is
`0.0125 x qty x p x (1-p)`, so it peaks at 0.50 and pays about a quarter of
that at 0.07 -- the least reward available exactly where adverse selection is
worst.

The gate would have refused this fill on its own economics, without knowing
anything about the clock. That is why it is the right shape: it does not depend
on the broken signal.

**Entry only, and structurally so.** The gate sits inside `_flat_plan`, which
is reached only through `if frame.position == 0`. It cannot run while inventory
is held, so it can never strand an exit -- invariant 42, which keeps reduction
and the bounded hard exit available at every price. Proved twice: a test that a
held position still produces exit work on a 0.05/0.07 book, and by the call
graph.

Three tests, mutation-checked. Removing the gate fails both end tests; guarding
only the low end (`price < floor` instead of `min(price, 1 - price) < floor`)
fails the far-end test, which matters because the opposite side pays
`1 - price` and one comparison has to cover both.

**Not deployed with this commit.** The runner was mid-session at the time and
the change is queued for the next deliberate restart.

---

## 2026-08-20 (evening)

### The unresolved-mutation stop took twenty markets down over one intent

**Fourth outage from the same guard**, and the one that finally got it fixed.
2026-08-17 (10s timeout), 2026-08-18 (60s), and twice on 2026-08-20.

**Symptom.** The bot stopped at 21:16:56Z after 9h41m and did not come back:

```
"stop_reason": "risk_stop:critical failure: order mutation unresolved for more than 60 seconds"
"flat": false
[supervisor] process exited without proving flat; not restarting
```

**Mechanism.** `_oldest_unresolved_age_seconds` reads the ledger
**account-wide**. One intent past the timeout armed a terminal critical stop
for every bound market. Shutdown then tried to flatten a -5 position, could not
prove flat while its own intent was still unresolved, and returned exit 3 --
which the supervisor never restarts.

Nineteen markets were healthy. The venue, checked directly afterwards, was
already flat: 0 open orders, 0 positions. The three intents left unresolved all
carried exchange ids, so a restart resolved them from terminal venue states in
under a minute.

**Cost.** ~20 minutes of not quoting, found by the operator rather than by the
bot. No money lost -- the account was flat throughout, which is exactly the
point: the stop fired on an account that was safe.

**Fix applied 2026-08-20.** `_stuck_mutations` splits overdue intents into
per-market and unattributable. A market that owns one is **retired** rather
than the account being stopped: it keeps its exit path (invariant 62), and
`place()` already refuses while its own market has an unresolved intent, so it
stops building risk without the other nineteen paying for it.

Retirement carries its own systemic bound, so a fault that really is
account-wide still ends the session through `market_retirement_limit_exceeded`.
An intent with no market to blame keeps the terminal behaviour -- fail closed.

**What deliberately did NOT change.** Ownership stays account-wide: a `venue:`
order or an unexplained absence still stops everything, because those are
statements about the account rather than about one market. And the timeout is
untouched -- it was 10s and cost an outage, 60s and cost another, and the
measured venue stall is 131s. The timeout was never the variable that mattered.

Four mutation checks. The one that nearly shipped unverified: removing the
`age <= limit` guard survived the first three tests, because all of them used
an intent that was already overdue. Without it every routine in-flight cancel
would retire its market -- and FINDINGS measures the account spending 35% of
wall-clock time with one outstanding, so the whole slate would leave the
session within minutes. `test_a_mutation_inside_the_timeout_retires_nothing`
covers it now.

484 tests pass.

---

## 2026-08-20

### The HARD meter drew the session stop's number against the hard stop's cap

**Found by the operator, who said the calculation looked wrong.** It was.

**Symptom.** The stop-loss block in the five-minute Discord summary:

```
session  |||||-------  $12.00 / $40.00
HARD     |||||-------  $12.00 / $45.00      <- same number, different cap
```

Both bars move together, always, because they were the same figure.

**Mechanism.** The HARD bar was drawn from `session_loss`, which is
`-session_realized_pnl_usd`:

```python
session_loss = max(Decimal("0"), -_money(summary.session_realized_pnl_usd))
...
"HARD     " + _meter(float(session_loss), float(hard_cap))
```

Those are two different stops reading two different quantities:

| stop | fires on | sees a settled position? |
|---|---|---|
| session $40 | `-realized_pnl`, matched pairs only | **no** |
| hard $45 | `starting_equity - current_equity` | **yes** |

So the bar labelled HARD could sit at zero while the stop it names was about
to fire -- a position that settles worthless moves marked equity and is
invisible to `realized_pnl`. That is the reassuring direction, which is the
dangerous one. The mutation test prints it exactly:
`HARD |||| $0.00 / $45.00` on an account $44 into a $45 ceiling.

`equity_change` was already computed two lines away and passed to the summary
as `trading_realized_usd`; the meter simply never used it.

**Fix.** A `today_pnl_usd` field carries marked equity against the UTC day's
opening equity -- the exact input to `hard_stop_reached` -- and the HARD meter
reads it. The session and worst-market bars are unchanged.

Mutation-checked: pointing the meter back at `session_loss` fails
`test_the_hard_meter_reads_equity_not_the_session_number`.

**This is the third instance of one pattern**, and it is worth naming. 
2026-08-18: the dashboard's loss bar summed only losing markets and read 4.6x
high, on a screen whose header already showed the right number from a
different path. Same day, the lesson written down was *"when a dashboard shows
the same thing twice, make them the same computation or name them
differently."* This is that again -- one number under two labels -- and it
survived because the fix was applied to `scripts/dashboard.py` and never to
`discord_summary.py`.

---

### Both loss stops baselined per PROCESS, so a restart forgave the day

Queued as `NEXT.md` item 00 since 2026-08-18 and fixed here.

**Symptom.** The operator was down on the account while both stop meters read
near zero. Measured 2026-08-18:

```
window                fills   matched-pair
THIS SESSION            37        -5.51     <- what the stop showed: 14% of $40
TODAY                  162       -12.47
ALL TIME               529       -46.55
```

**Mechanism.** `SessionRiskGuard` is constructed once in preflight with
`starting_equity` stamped from the balance at that instant, and
`_roll_realized_baseline` kept `_baseline_day` on the instance. Both therefore
reset with the process. Ten sessions started on 2026-08-18, so the $40 budget
was handed back ten times.

**The ratchet is the dangerous half, and it is the hard stop.** Lose $30 of
equity, crash, restart: `starting_equity` re-stamps at the *lower* equity, the
$30 is forgiven, and it takes another full $45 from the new floor to fire. The
account can walk down indefinitely with every individual step inside the
limit. That is the shape that matches "account is down, stops look calm".

**Fix.** The day's opening marks now live in the ledger, which survives a
restart:

```sql
CREATE TABLE execution_day_baselines (
    utc_date TEXT PRIMARY KEY, opening_equity TEXT NOT NULL,
    opening_realized TEXT NOT NULL, opening_cash TEXT, created_at TEXT NOT NULL)
```

`INSERT OR IGNORE` is the whole mechanism: the first process of a UTC day
writes the marks and every later one reads them back. A new UTC date has no
row and does get a fresh budget, which is the only reset intended.
`SessionRiskGuard.adopt_day_baseline` re-points the hard stop at the stored
opening equity, and preflight calls it before any order can be placed.

**A test was deleted on purpose.**
`test_losses_from_earlier_sessions_do_not_spend_this_budget` asserted the old
behaviour by name. Its underlying concern is real -- a LIFETIME tally halted
the runner 73 seconds after every start on 2026-08-15 -- so it was split
rather than dropped: `test_a_new_utc_day_absorbs_every_earlier_loss` keeps it,
and `test_a_restart_inside_the_same_day_inherits_its_drawdown` covers the
ratchet.

Mutation-checked twice, both reproducing the production shape exactly
(`Decimal('70') != Decimal('100')` -- the depressed equity adopted as the
baseline): removing the `adopt_day_baseline` call, and relaxing
`INSERT OR IGNORE` to `INSERT OR REPLACE`. 475 tests pass.

**Not fixed here:** `hard_stop_reached` still returns False on equity older
than 15s, so during a venue outage the hard stop is not evaluating at all.
That is deliberate -- it refuses to fire on numbers it cannot trust -- and the
answer to a bad venue coinciding with a bad day remains the pre-kickoff
flatten, not a stop firing on stale data. See FINDINGS.

---

## 2026-08-19 (diagnostics)

### The tool that answers "is this normal?" listed rejects it had never filtered

**Not a runner bug -- no bad order, no exposure.** It is in `usable_markets.py`,
which `CLAUDE.md` names as the answer to "why is it quoting 3 markets and not
20". It produced two wrong diagnoses in one session, one of them the operator's
question and one of them mine.

**Symptom.** `--rejects` prints a block headed `MARKETS DROPPED FOR A TIGHT BOOK
(catalog BBO)` and fills it with month-out MLB championship futures:

```
tec-mlb-nlchamp-2026-09-27-nym                 spread 0.0010
tec-mlb-champ-2026-09-27-atl                   spread 0.0060
... (truncated at 30)
```

Read literally, that says the bot is passing on a pile of long-dated futures --
which is reassuring, because it is not the kind of market this strategy wants.

**Mechanism.** The listing had no connection to the funnel above it:

```python
catalog = await gateway.list_sports_markets()   # all 21,285 rows
for market in catalog:
    ...
    if spread + 1e-9 < policy.minimum_entry_spread:
        print(...)
```

No horizon filter, no moneyline filter, no active/open filter, and no reference
to `catalog_spread_filtered_markets` -- the counter it claimed to explain. Every
row it printed had been discarded by the **horizon** gate long before the spread
gate ran. Truncation at 30 in catalog order then guaranteed `tec-*` filled the
screen, because it sorts ahead of `aec-*`.

Measured against the real gate at 2026-08-19T23:29Z: of 21,285 catalog rows, 43
were in the 2h horizon and **zero** were `tec-mlb-*` futures. The in-horizon set
was that evening's actual games -- WNBA, MLB, valorant, ATP, ITF, setka.

**Cost.** Two wrong conclusions on 2026-08-19. The operator asked why the market
count was falling; this output said "month-out futures, nothing real is being
refused" and both of us believed it. The truth was the opposite -- the refused
markets were that night's games (see FINDINGS, the live-period drain). It also
means the FINDINGS entry from 2026-08-19T03:25Z that characterised the rejects
as `tec-mlb-*champ-*` rests on this same output; its **counts** came from the
selector and are fine, its **description of what the rejects are** is not
supported and is corrected there.

**Fix.** `_print_rejects` now builds its list from the selector's own methods
(`_catalog_market_is_executable`, `_catalog_spread_can_enter`,
`select_applicable_period`) in the selector's own order, so the listing cannot
disagree with the funnel. It classifies every in-horizon market into the gate
that actually dropped it, and sorts the tight group widest-first so near-misses
are visible -- the only rows that speak to whether the 2c minimum holds anything
back.

**The general shape, worth carrying.** A diagnostic that re-derives its own
filter instead of calling the one it is explaining will drift from it silently,
and it drifts in the worst direction: it stays confident. This is the same
"a gate that lives in two places" failure this file already records twice for
production code -- it applies to the tools as well.

---

## 2026-08-19

### An absence whose terminal state was already on file could never be repaired

**Symptom.** Rotation blocked for hours. 2 markets quoting, **15 bound and
silent**, 3 free, on a bot that had been up 13h53m with no risk stops.

```
rotation     1 orders UNKNOWN_ABSENT -- BLOCKED
quoting              2
bound, not quoting   15   wasted slots
```

**Mechanism.** `BZ9PPEV4RDK0` on `aec-itfwo-mardro-ilisag-2026-08-19` was
marked `UNKNOWN_ABSENT` with `missing_from_complete_snapshot`. The venue's own
view of it never changed after that:

```
state = ORDER_STATE_CANCELED   cumQuantity 3/5
lastTransactTime = 2026-08-19T10:53:44.728316Z
```

and that exact event had already been applied:

```
10:48:06  applied=1  order:BZ9PPEV4RDK0:...10:48:02:OPEN
10:53:49  applied=1  order:BZ9PPEV4RDK0:...10:53:44:CANCELED
```

`UNKNOWN_ABSENT` is not a working state and the order's intent was already
RECONCILED, so it fell out of the strict sweep -- correct, that is what the
absent sweep is for. But the absent sweep re-asked through `reconcile_order`,
whose event id is `order:{id}:{event_at}:{status}`. The venue kept answering
CANCELED at the same timestamp, so every re-ask rebuilt an identical id,
`_accept_event` rejected it as a replay, and `_upsert_order` never ran.

That last part is what makes it unrecoverable rather than slow: `_upsert_order`
holds `resolves_absence`, the guard written so a terminal state can outrank an
`UNKNOWN_ABSENT` row -- and it was unreachable from the one code path whose job
is repairing absences. `risk_state_known()` counts absences **account-wide**, so
one row stopped rotation for all twenty markets.

This is the same family as the 2026-08-18 fix that added `status` to the event
id. That fix was right and is still needed -- it handles a venue that changes
state without moving its clock. It does not help here, because the status is
unchanged too.

**Fix applied 2026-08-19.** The absent sweep now calls
`reconcile_absent_order`, which keys its event on the **observation** time
(`absent:{id}:{observed_at}`) rather than the venue's event time, so it always
writes. Safe because nothing is being inferred: it applies the venue's own
reported state, and `_upsert_order` keeps its ordering and terminal guards.
Absence is still never read as cancellation.

Mutation-checked: pointing the sweep back at `reconcile_order` leaves the row
absent and fails the new test. 463 tests pass.

**Cost.** ~14 hours of degraded quoting -- 2 markets instead of up to 20 -- on
a bot that otherwise looked healthy. No money lost directly; the loss is reward
not earned, and it was invisible because every other health signal was green.

---

## 2026-08-18 (second incident)

### A cancel that 500s is never re-issued, and the stuck intent then blocks the flatten

**Symptom.** The bot stopped at 2026-08-18T15:33:01Z, ~3.5h after the morning
restart, and did **not** come back:

```
"stop_reason": "risk_stop:critical failure: order mutation unresolved for more than 60 seconds"
"flat": false
[supervisor] process exited without proving flat; not restarting
```

Same stop reason as the morning outage, **different mechanism**. The morning
fix (`ad58bc2`) does not address this one and was never going to.

**Mechanism.** A cancel got an HTTP 500 from the venue:

```
intent   cancel-897243c8385746b49354482ba0637b80   operation=CANCEL
order    BYRYM21VADK0   market aec-itfme-agubadaleleg-lorgagrocval-2026-08-18
state    UNCERTAIN      at 15:16:52
error    POST /v1/order/BYRYM21VADK0/cancel outcome uncertain:
         InternalServerError: The server was unable to process your request.
```

`live_orders.cancel` catches the failure, moves the intent to `UNCERTAIN` and
raises `OrderOutcomeUncertain`. `UNCERTAIN` is an unresolved state, and a
CANCEL intent resolves **only** on a terminal order state -- correctly, since
an accepted HTTP response is not cancellation confirmation:

```sql
UPDATE execution_intents SET state='RECONCILED' ... WHERE exchange_order_id=?
  AND state IN ('SUBMITTING','ACKNOWLEDGED','UNCERTAIN')
  AND (operation='PLACE' OR (operation IN ('CANCEL','MODIFY') AND ? IN
       ('FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED')))
```

The reconcile re-read the order every 2s and the venue kept answering
`OPEN` -- the ledger row's `received_at` is 15:32:53, sixteen minutes after
the 500, still `status=OPEN`. **The cancel genuinely did not take effect, and
nothing ever re-issued it.** There is no retry path for an `UNCERTAIN` cancel;
the only thing that resolves it is a terminal state that will never arrive on
its own.

So the intent stayed unresolved, the 60s critical stop fired, and then the
same intent blocked its own market's flatten -- `live_orders` refuses to act
while `market_has_unresolved_intent` is true -- so shutdown could not prove
flat and exited 3, which the supervisor never restarts. The bot was down until
the operator noticed.

The position was real: `BYRYBVSGRDK5` had **FILLED 5/5 at 0.56** in the same
market at 15:14:51, and the shutdown log shows it parked for four minutes
(`decision=parked position=5.0000 seconds_to_kickoff=1014`) because the
pre-kickoff window had not opened.

**Cost.** ~3h of not quoting, and the operator closed the position by hand.
Checked at the venue afterwards, both orders were terminal
(`BYRYBVSGRDK5` FILLED, `BYRYM21VADK0` CANCELED) and the account was flat --
0 open orders, 0 non-zero positions -- so a restart self-heals: the startup
reconcile re-reads by exchange id and a terminal state resolves the intent.

**Fix applied 2026-08-18.** `_retry_uncertain_cancels` re-issues a cancel whose
intent is `UNCERTAIN` **while the order's freshly re-read state is active**.
Confirmed-active is positive evidence the cancel did not land, so re-sending it
assumes nothing -- the exact opposite of reading absence as cancellation. An
absent, unknown or terminal order is never retried; `order_is_active` answers
from `ACTIVE_ORDER_STATES` only, so a missing row and an `UNKNOWN_ABSENT` row
both read as "no". Throttled at `CANCEL_RETRY_SECONDS = 10.0`, which gives
roughly six attempts inside the 60s stop budget. Called from `_rest_reconcile`
straight after the sweep that refreshes order state, and deliberately before
that sweep's failure raise, so an unreadable order in another market cannot
postpone the repair.

**Two things this write-up first got wrong**, both corrected after reading the
code rather than assuming it:

1. `cancel()` does **not** refuse while `market_has_unresolved_intent` is true.
   That guard is on `place()`. What actually made the flatten unprovable is
   `kill_switch.status` (`live_session.py`), which counts unresolved intents as
   a blocker.
2. The retry does **not** need to supersede the stuck intent. `_upsert_order`
   resolves every intent matching the exchange id in one statement, so the
   stuck `UNCERTAIN` one and each retry clear together on the terminal state.
   Each retry therefore opens its own intent, which is correct -- the state
   machine forbids `UNCERTAIN -> SUBMITTING`, and reusing the id would
   misreport what happened.

Note what the fix does not do: it does not resolve the intent when our own
cancel request succeeds. Only the venue's terminal state does that, which
arrives on the following read. An accepted HTTP response has never been
cancellation proof and still is not.

Mutation-checked twice. Removing the call fails
`test_a_cancel_the_venue_500d_is_re_issued` on "a cancel the venue confirmed
did not land must be re-issued". Making `order_is_active` ignore status fails
`test_a_cancel_is_not_re_issued_while_the_order_is_unknown` on "an order we
cannot see must not be cancelled on a guess" -- it issued a cancel against an
order it could not see. 454 tests pass.

---

## 2026-08-18

### One unreadable order hid every order behind it, and killed the session

**Symptom.** The bot stopped at 2026-08-18T08:26:06Z after running 4h11m, and
stayed down about three hours until the operator noticed.

```
"stop_reason": "risk_stop:critical failure: order mutation unresolved for more than 60 seconds"
"flat": true
[supervisor] clean exit after 15077s; not restarting
```

`commissioning.log` shows nothing at all between the last placement at
08:23:23 and the shutdown at 08:24:37. That silence is part of the bug.

**Mechanism.** The strict half of `_rest_reconcile` asked the venue for
terminal state on every bot-owned working order and every unresolved intent:

```python
for exchange_id in sorted(exchange_ids):
    await self.executor.reconcile_order(exchange_id, observed_at=now)
```

It raised on the first id that failed, and `sorted()` made *which* id that is
deterministic rather than harmless. The event log proves it exactly -- one
sweep at 08:23:23.210 re-read eleven orders in ascending id order and stopped:

```
BYJZZ1244DKA … BYK0B4B0MDJV, BYK0EWMDPDK1    11 applied
BYK0EWMPMDK1                                  12th: raised
BYK0KVYDYDJR                                  13th: never asked
```

`BYK0EWMPMDK1` was a place from the 08:23:17-21 burst that the venue had
accepted -- it returned an exchange id -- but then would not serve state for
over two minutes, and had also gone missing from a complete open-orders
snapshot (`unknown_reason=missing_from_complete_snapshot`). Its intent
therefore stayed unresolved, which is correct and deliberate: absence is never
read as cancellation.

The caller runs that reconcile every 2s while any intent is unresolved, so the
sweep ran roughly sixty-five times and aborted at the same order every time.
`BYK0KVYDYDJR` sat behind it and was never asked once, even though it was
readable and would have resolved immediately -- both intents finally reconciled
in the same instant at 08:25:34, the first sweep that got past the blockage.

Two intents unresolved is what arms `unresolved_mutation_timeout_seconds`, a
**terminal, account-wide** critical stop. It fired at 60s and took all twenty
markets down over one order.

The second, unresolved intents also block their own market's cancel
(`live_orders.py`), which is the five `[shutdown] decision=failed
error=AdapterError` lines on `nikwal-timheg` during the flatten.

**Why nothing was logged.** The call site swallowed it whole:

```python
try:
    await self._rest_reconcile(now)
except Exception:
    pass
```

So the two minutes of failing re-reads that preceded a terminal stop left no
trace, and **what `retrieve` actually raised is simply gone** -- it cannot be
recovered from this incident. Only one `NotFoundError` was logged all day, for
an unrelated order in a different component.

**Fix applied 2026-08-18.**

1. The strict sweep now asks about **every** id, collects failures, and raises
   after the loop. Strictness is unchanged -- any failure still fails the whole
   reconcile, because a working order we could not re-read is live risk -- but
   one unreadable order no longer hides the state of every order behind it.
2. The call site reports through `_report_runtime_error` instead of
   discarding. Routine alerts fire at attempts 4/8/16, so a 2s cadence gives
   three Discord messages in the 60s before the stop rather than none, and the
   raised error is named in `commissioning.log` either way.

Mutation-checked: reverting (1) fails
`test_one_unreadable_order_does_not_hide_every_other_one` with
`'exchange-zzz' not found in ['exchange-aaa']` -- the production mechanism.

**Fixes 1 and 2 alone would not have prevented this, and that is the point.**
Had they been in place, `BYK0KVYDYDJR` would have resolved at 08:23:23 and the
shutdown cancels would not have failed -- but `BYK0EWMPMDK1`'s own intent was
created at 08:23:20.662 and was still unresolvable, so the stop would have
fired at 08:24:20 instead of 08:24:17. **Three seconds later.** Smaller blast
radius, same outage. That is why the third fix exists.

3. So the policy was fixed too, which is what actually recovers the hours. A
   `risk_stop:` reason fell through every branch of the exit-code block and
   returned **0** -- the supervisor reads that as a clean operator stop and
   never restarts, which is the whole three hours. This one stop now returns
   **5**, the existing recoverable code: the supervisor restarts with backoff,
   and its fast-crash counter still gives up on a genuine loop.

   Narrow by construction. It matches only
   `risk_stop:critical failure: order mutation unresolved`, and only when no
   comma appears in the reason -- `blockers()` are comma-joined, so a comma
   means something else tripped in the same breath. A loss stop, the session
   duration limit, ownership violations and every other risk stop keep their
   current behaviour, and any unexpected shape falls through to 0 and does not
   restart. `flat` is still checked first, so an unproven account still exits 3
   and never restarts. Mutation-checked: neutering the branch fails
   `test_an_unresolved_mutation_stop_restarts_but_nothing_else_does`
   with `0 != 5`.

The stop itself is unchanged: a genuinely stuck mutation still stops all twenty
markets, and the runner still sends a critical Discord alert from `_shutdown`
on the way out. What changed is that it no longer stays down afterwards. Making
the guard per-market rather than account-wide is the real long-term fix and is
deliberately not done here -- it weakens an account-wide safety property and
wants its own change. See `NEXT.md`.

**Cost.** ~3h of not quoting on a Monday session. No money lost: the account
proved flat (`"flat": true`), all thirteen working orders cancelled, and no
position was held.

---

## 2026-08-17 (accounting)

### 60% of our fills were never recorded: the parser read a key the venue does not publish

Found 2026-08-17 while trying to make P&L trustworthy. **This is the root cause
of every accounting symptom measured that day.**

**Symptom:** `realized_pnl` read **-$143.59** while the account had actually
moved **-$48.69**. 27 markets held an entry with no exit ever recorded
(-$22.41). Markets showed a TAKER first fill, which a post-only entry cannot
produce. A riding-vs-crossing comparison came out contaminated because 40 of 57
"ridden" markets were simply missing their opening fill.

**Mechanism.** `AsyncAccountSnapshotGateway.fetch_executions` read the legs of a
trade as:

```python
legs = [trade.get("aggressorExecution")]
maker = trade.get("makerExecutions")     # <- key the venue never publishes
if isinstance(maker, list):
    legs.extend(maker)
```

The passive leg is `passiveExecution`. `makerExecutions` does not exist, so the
list was always just the aggressor leg. Whenever WE were the passive side, our
execution was never read at all -- instead the counterparty's aggressor leg was
read and then discarded for not matching one of our orders.

**Passive is the normal case for a market maker.** Sampling 25 consecutive
trades: 15 were ours-as-passive, 10 ours-as-aggressor. So roughly 60% of fills
reached the ledger only if the private WebSocket happened to deliver them --
and the REST sweep exists precisely because the WebSocket does not survive a
restart or a socket gap. Anything lost there was lost permanently.

**The second half of the defect** was how ownership was decided:

```python
if str(execution.client_order_id).startswith("venue:"):
    continue
```

Correct in intent -- a trade has two legs, both are reported, recording both
made P&L read flat on a market holding risk (2026-08-15, valorant-ff-ep). But
`resolve_client_id` returns `venue:` for **our own** fills whenever we hold no
order record: an operator's manual close, or an order whose record was lost. So
"the counterparty's leg" and "our unattributable leg" were indistinguishable and
both were dropped.

**Fix.** The venue states ownership directly. `trade.isAggressor` selects which
leg is ours, and it is exact -- checked against our own order ids across 25
consecutive trades, True matched the aggressor leg 10/10 and False matched the
passive leg 15/15. Taking exactly one leg prevents the original double-counting
without needing the `venue:` test at all, so unattributable fills of ours are
now recorded instead of discarded.

**Cost:** every strategy question asked on 2026-08-16/17 was weighed against
P&L that was wrong by 3-10x, and several hours went into analysing numbers that
could not have been right. Test:
`test_only_our_leg_of_a_trade_is_recorded`, which asserts the passive leg is
kept and the aggressor leg is not. Mutation-checked -- reverting to
`aggressorExecution` alone fails it.

**Not yet done:** settlements still have nowhere to be recorded, because the
venue publishes no settlement rows at all. See NEXT item 1b for the remaining
order of work.

---

## 2026-08-17

### Preflight refused startup three separate times, each for something harmless

Three distinct outages in one night, all the same shape: an unguarded call in
`preflight` raised, `CommissioningRefused` propagated, and the process exited
**2** -- the one code `supervise_commissioning.sh` never restarts. Each time the
account was flat and nothing was actually wrong.

**Symptom, identical in all three:** the Discord sequence

```
1. Liquidity Bot supervisor started for one-contract commissioning.
2. Liquidity Bot LIVE runner failure: <something>
3. Liquidity Bot startup was refused. Nothing was launched.
```

and `[supervisor] startup refused; not restarting` in `supervisor.log`. The bot
stays down until a human notices.

**1. Slot zero was exempt from the guard the other slots had.** `preflight`
refreshed every bound market's book best-effort and dropped the failures --
except the primary, which it refreshed unguarded first. That made slot zero the
single point of failure the rest of the loop existed to prevent. Its book came
back with no levels on either side, `parse_book_response` raised `book is empty
on both sides`, and one dud market out of twenty took the whole startup down.
`adapters.py` notes about one market in four publishes a one-sided book, so
drawing a bad slot zero is routine. Fixed by treating every slot the same
(`_refresh_bound_books`), keeping two deliberate refusals: a market we are
RECOVERING holds a position we were restarted to retire, and dropping every
market would bind an empty set.

**2. Recovery was decided from a ledger that had stopped updating.**
`_identified_recovery_markets` reads the ledger, and the ledger freezes the
moment a runner dies -- so after a crash it describes the account as it *was*.
It still held a working order and a 5-lot position in `dumvit-midmih` from the
19:01 crash while the venue held neither. Preflight bound that market for
recovery, its game had kicked off at 19:45 and finished, so its book was empty
and startup exited 2. **Twice, over a position that did not exist.** Fixed by
doing one whole-account REST read before any decision depends on what we think
we hold. `_rest_reconcile` gained a `tag` because two reconciles at the same
clock reading build the same snapshot event id from different content, which
the ledger correctly rejects as a replay.

**3. A venue 500 on an earnings fetch.** `GET /v1/incentives/earnings` returned
`InternalServerError` and refused startup. Earnings are reporting, not a safety
input: delayed by days, never authorizing an entry or an exit, read by nothing
downstream. The run loop already treated a failure here as recoverable and
`_refresh_earnings` was guarded there -- preflight simply did not have the same
guard.

**4. And the root of all three: a venue that did not answer was treated as an
account that was not safe.** `main` caught `CommissioningRefused` and
`AdapterError` in the same handler and exited **2** for both:

```python
except (CommissioningRefused, AdapterError) as exc:
    parser.exit(2, f"commissioning refused: {exc}
")
```

Those mean opposite things. `CommissioningRefused` says the account state is
unsafe to trade from -- open orders, a position, unresolved intents, ownership
we cannot explain -- and exit 2 is right, because a human must look before this
account trades again. `AdapterError` says the venue did not reply. It carries no
information about our state at all, and making it permanent means any 500
anywhere in preflight downs the bot until someone notices.

Transport failures now exit **5**, the supervisor's recoverable code: restart
with backoff, and still give up after `MAX_FAST_CRASHES` deaths inside
`FAST_CRASH_SECONDS`, so a venue that is genuinely down stops the session and
alerts rather than spinning. Guarding the three individual call sites above
was worth doing, but this is the fix that covers the ones not yet found.

Observed while deploying the fix for #3: within minutes the venue returned
`APITimeoutError` on `GET /v1/order/{id}`, `InternalServerError` with an HTML
error page on `GET /v1/portfolio/positions`, `APITimeout` on `GET /v1/incentives`,
and finally `InternalServerError` on `GET /v1/orders/open` -- which refused
startup again. The venue was simply having a bad night, and the bot had no way
to wait it out.

**The pattern, which is the actual lesson.** Every one of these is a guard that
existed somewhere else and did not cover one path: best-effort book refresh
that skipped slot zero, a venue reconcile that ran after the decision instead of
before it, an earnings guard present in the run loop and absent in preflight.
Six fixes tonight had this shape. **Preflight is the most dangerous place in
this codebase for an unguarded call**, because its failure mode is exit 2 and
exit 2 is permanent.

Worth auditing the rest of `preflight` for calls that can raise on transient
venue conditions and asking, for each, whether failing it should really cost the
session.

**Cost:** three outages, the longest 79 minutes, all with a flat account and no
real fault. Tests:
`test_a_dud_primary_market_is_dropped_not_refused`,
`test_startup_refuses_when_no_market_has_a_book`,
`test_a_recovering_market_still_refuses_rather_than_be_dropped`,
`test_a_stale_ledger_position_does_not_force_a_recovery_bind`,
`test_an_earnings_failure_does_not_refuse_startup`. All mutation-checked.

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

## A parked position bricks the bot until the pre-kickoff window opens

**Symptom.** A routine restart to deploy a fix took the bot down entirely. The
graceful stop reported success and exit 0, but the account was not flat, and
every subsequent start died within twenty seconds:

```
[shutdown] market=aec-cs2-nvo-mel-2026-08-18 decision=parked position=5.0000 working=1 seconds_to_kickoff=1190
{ "flat": false, "stop_reason": "startup_recovery_only" }
[supervisor] process exited without proving flat; not restarting
```

**Mechanism.** Two correct rules that deadlock when combined.

Shutdown will not dump a position into a thin book, so outside the 300s
pre-kickoff flatten window it *parks*: leaves one passive exit resting and
exits. `stop_commissioning.sh` reports success because it only waits for the
process to die, and the process died as designed.

Startup then finds leftover state, enters `RECOVERY_ONLY`, and immediately
requests stop. That run lives about eighteen seconds -- nowhere near long
enough for a passive exit to fill -- so it parks again and returns:

```python
if not result.flat:
    return 3          # the code the supervisor never restarts
```

Exit 3 is deliberate: a position nobody can explain must not trade again until
a human looks. But here the position is fully explained, and the only mechanism
that would clear it -- the pre-kickoff flatten -- requires the runner to stay
alive until the window opens. It cannot, because it exits before then. The bot
is down until either the window opens or the market settles.

**Cost.** $2.80 of exposure (5 contracts of "Noir Verse" at 0.56, a MAKER fill
carrying a 2c rebate) held unmanaged, and roughly fifteen minutes of not
quoting during an active session. The money was never the risk; the outage was.

**Fix applied 2026-08-17.** Operational, not code: start the runner *inside* the
300s pre-kickoff window, where recovery flattens through the normal exit ladder,
proves flat, and exits 5 so the supervisor starts a real session.

**Not yet fixed in code.** Recovery should either stay alive until it can
flatten -- bounded by the kickoff deadline it already honours -- or exit with a
code that says "known position, retry later" rather than 3. Parking during
shutdown is right; parking again on every restart is what turns a $2.80 position
into an outage. See NEXT.

## Order confirmations run at 90% of the 10s timeout; churn pushed one over

**Symptom.** The bot stopped at 2026-08-17T18:50:16Z and stayed down ~18
minutes. Nobody was alerted; the operator noticed.

```
"stop_reason": "risk_stop:critical failure: order mutation unresolved for more than 10 seconds"
"flat": false
```

**Mechanism.** Two things combined, and the second is the interesting one.

A reducing (exit) quote on `aec-setkameua-rakser-hosnik-2026-08-17` churned for
four minutes, cancelling and re-placing **at the same price** every ~20s:

```
18:46:50 PLACE 0.47   18:47:19 PLACE 0.47   18:47:58 PLACE 0.47
18:48:25 PLACE 0.47   18:49:27 PLACE 0.47
```

`_reducing` cancels any resting order failing
`_matches(order, price=passive_price, qty=qty)` -- exact price *and* exact
`remaining_qty` -- then re-places at `passive_price` on the next pass. Something
made an order at the right price fail that test repeatedly. **Not diagnosed
further**; the qty half is the remaining suspect.

The churn alone is only wasted budget. What made it terminal is the confirm
latency, visible in `execution_orders` as the gap between `event_at` and
`received_at`:

```
18:46:27 -> 18:46:36    9s
18:47:11 -> 18:47:20    9s
18:47:37 -> 18:47:49   12s
18:48:17 -> 18:48:26    9s
18:49:19 -> 18:49:28    9s
```

**Every mutation confirms in ~9 seconds against a 10-second timeout.** The bot
runs permanently at 90% of that budget, so the stop is not really "a mutation
hung" -- it is "enough mutations were attempted that one crossed a line we were
always sitting against". The churn supplied the attempts.

**Cost.** ~18 minutes of not quoting. No money lost -- the account was verified
flat at the venue (0 orders, 0 positions, balance $440.67) before restarting.

**Fixed?** No. Service restored by restart only. Three things remain:

1. **The confirm timeout has no headroom.** 9s against 10s is not a margin.
   Either the timeout is too tight for this venue or confirms are too slow;
   neither has been measured properly. This is the real fix and it is not done.
2. **The same-price re-place churn** in `_reducing` -- why an order at the
   correct price keeps failing `_matches`.
3. **The bot exited non-flat, so the supervisor refused to restart** (exit 3),
   which is `NEXT.md` item 0 firing for the second time in one day. That is what
   turned a stop into an 18-minute outage.

**And nothing alerted.** `_alarm_if_quoting_nothing` lives inside the runner, so
a dead runner sends nothing. A dead-man alarm outside the process is the missing
piece that makes every fault above unbounded in duration.

## A skipped book refresh cleared the failure counter, so dead markets never retired

**Symptom.** The runner was up for 2h11m and had not placed an order in
**1h56m**. It looked healthy from outside: process alive, supervisor content,
log ticking, `[selection]` lines every five minutes, `retired=0` then `1`.

```
markets referenced 20:10-21:50Z
     86  aec-setkamemd-salvea-spiole-2026-08-17   <- book empty, game already started
      2  (five others, selection candidates only)

[top-up] events in that window:  0
component=reward_market_selection    attempt=79
component=quiet_book_rest_refresh   440 failures, escalation never past attempt=3
```

Nineteen of twenty slots sat free while viable markets existed.

**Mechanism.** `_service_market_state` clears the per-market failure counter
whenever `_refresh_quiet_book_if_due` returns without raising:

```python
state.next_book_refresh = await self._refresh_quiet_book_if_due(...)
self._runtime_error_counts.pop(f"quiet_book_rest_refresh:{slug}", None)
```

But that helper returns without raising in **two** cases -- a refresh that
succeeded, and a refresh that was never attempted:

```python
if now < next_refresh:
    return next_refresh          # not due; nothing fetched, nothing proven
```

Counting the second as success reset the very counter it then needed to
accumulate. Retirement fires at `MAX_RECOVERY_ATTEMPTS = 12` **consecutive**
failures, and the counter was being wiped every few seconds by a no-op return,
so it never passed 3 in 440 failures. `unrecoverable_book_stale` could not
fire, the dead market kept its slot, its failing refresh starved the loop, and
`reward_market_selection` escalated to attempt=79 -- so top-up never ran once.

**This is why "hundreds of `attempt=1` is a healthy bot" cut the wrong way
here.** That heuristic is in CLAUDE.md and it is right in general, but a market
whose game has started has a book that is empty *forever*, and the reset made
permanent failure look exactly like ordinary quiet.

**Cost.** ~2 hours of zero quoting on a live session. No money lost -- the
account was flat throughout (verified at the venue: 0 orders, 0 positions).

**Fix.** `_refresh_quiet_book_if_due` now records
`_refreshed_book_this_call`, and the caller clears the counter only when a
refresh actually ran. Consecutive failures accumulate, reach 12, and the market
retires as designed.

**Mutation-checked, and the first attempt was wrong.** The initial test
exercised the helper in isolation and passed with the fix reverted -- vacuous,
because the counter is cleared by the *caller*. Rewritten against
`_service_market_state`, reverting now fails with `None != 11`. 446 tests pass
locally and on the droplet.

**Still open:** the `_alarm_if_quoting_nothing` path did not visibly surface
this. It sends to Discord and writes nothing to the log, so whether it fired
cannot be determined from the droplet -- which is itself the problem. A silent
2-hour stall on a live money bot was found by the operator, not the bot.

## UNKNOWN_ABSENT orders never resolve, and permanently block market rotation

**This is the cause of the repeated stalls on 2026-08-17, and it outranks
everything else fixed that day.**

**Symptom.** The runner is up, healthy, flat, and quoting one market or none.
Slots stay empty for hours. `[selection]` keeps reporting viable markets it
never binds. Restarting appears to help for a few minutes, then it stalls again.

**Mechanism.** Measured 22:28Z:

```
19 orders in UNKNOWN_ABSENT   reason: missing_from_complete_snapshot
oldest 04:40:33Z -- nearly 18 hours earlier
unresolved intents: none      venue:-owned orders: 0
```

`risk_state_known()` is False while any order is `UNKNOWN_ABSENT`.
`_rotation_blockers()` therefore returns *"whole-account execution state is not
known"*, and `_rotate_reward_market_inner` raises:

```
AdapterError: reward-market rotation blocked: whole-account execution state is not known   (x11)
```

**Rotation is blocked outright.** The bot cannot rebind to new markets, so it
stays pinned to whatever it already holds while free slots sit idle.

**Why they never clear.** The absent sweep in `_rest_reconcile` does call
`executor.reconcile_order(exchange_id)` once a minute per absent order, and
that calls `gateway.retrieve`. But these orders belong to markets that have
since **settled and closed**, so the venue cannot return a terminal state for
them. The sweep's own comment anticipates it -- *"an order the venue will not
resolve"* -- and then treats the failure as a transient error to report rather
than a permanent condition to handle. The order stays absent forever.

The design is right: *absence is never read as cancellation* (CLAUDE.md), and
only a terminal state from the venue may resolve a lost order. The gap is that
**a market which has settled makes the question unanswerable**, and nothing
handles that case.

**This degrades monotonically.** Every order the bot loses sight of permanently
subtracts from its ability to rotate. Accumulation started by 04:40Z and by
22:28Z had reached 19. Restarts do not clear it -- the bot restarted four times
on 08-17 and the 04:40 order survived all of them, because the state is durable
in the ledger.

**Cost.** Most of an evening's quoting. Every "the bot is not trading"
investigation on 08-17 traces here.

**Not fixed.** Bot stopped flat pending a proper fix.

### The fix, and the constraint on it

A settled market is *affirmative* evidence, not absence: if the market is
closed, no order can be live in it, so resolving the order is not "reading
absence as cancellation". That distinction is what makes a safe fix possible.

Shape: when `reconcile_order` finds the venue does not know the order, check the
market's status (`retrieve_by_slug`; resolved markets carry
`MARKET_STATUS_RESOLVED` -- `scripts/accounting.py` already does this), and only
if resolved, write a terminal state. Never on a 404 alone; a 404 can be
transient, a settled market cannot un-settle.

Touches `live_orders.reconcile_order`, the sweep in `live_commissioning`, and a
terminal-state write in `execution_ledger`. It sits on `risk_state_known()`,
which is the one genuinely account-wide invariant, so it wants a full session
and real tests -- including one proving an absent order in an OPEN market is
still never resolved.

### FIXED 2026-08-17T22:51Z. First attempt failed; the data corrected it

`resolve_absent_order_in_settled_market` plus `_market_has_settled` are in and
tested (448 pass, both mutation-checked: an OPEN market no longer licenses a
resolution, and the ledger write refuses any row that is not `UNKNOWN_ABSENT`).

**It does not fire in production.** After a restart with the fix live:

```
UNKNOWN_ABSENT:               20  (unchanged)
resolved by the new path:      0
rotation blocked events:      14  (still blocking)
component=absent_order errors: 0  <- the sweep reports NOTHING
```

Zero errors *and* zero resolutions means the retrieve is not raising NotFound at
all, so **the premise that the venue cannot answer is wrong, or the sweep never
reaches those rows.** The rows do have `exchange_order_id` values, so that is
not it. Candidates, none tested:

- the sweep's `exchange_id in exchange_ids` skip is matching them
- `retrieve` succeeds and `apply_order_event` silently declines to apply
  (returns False on the staleness rule rather than raising)
- `_rest_reconcile` returns before reaching the absent sweep

**The answer, measured.** Every one of the twenty stuck orders sat in a market
the venue reported `RESOLVED` -- and `retrieve` answered for all of them without
raising. So the NotFound branch was simply the wrong place for the check:

> An order in a resolved market cannot be live, whatever `retrieve` returns.

Settlement is the whole test; the re-ask is irrelevant once it holds. Moving the
check out of the exception branch into the main sweep path fixed it immediately:

```
                        before      after
UNKNOWN_ABSENT             20          0
resolved by new path        0         20
rotation blocked (10min)   14          0
placements (10min)          -         47
```

Rotation was unblocked for the first time that day.

**Lesson.** The first version was built from an inferred mechanism ("the venue
cannot answer") rather than an observed one. Checking the actual market status
of the stuck rows took one query and would have pointed straight at the right
branch. The sweep is still silent per-row -- instrumenting it remains worth
doing, because that silence is why this cost an evening to locate.
