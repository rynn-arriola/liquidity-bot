# Findings — liquidity-bot

Empirically established facts, with the measurement that produced each one.
Nothing here is a plan or an intention. If a number appears without a source,
it does not belong in this file.

Companion to `docs/SAFETY_INVARIANTS.md` (rules the code must obey),
`BUGFIXES.md` (bugs that reached production: symptom, mechanism, fix) and
`docs/SCALE_REVIEW.md` (what is and is not proven). This file is the evidence
those two rest on.

**Standing rule: anything critical we establish goes in here immediately.**
Its companion is in `BUGFIXES.md`: every bug that reaches production gets an
entry there when it is fixed, with symptom, mechanism and fix.
Not at the end of the session, not "if it comes up again" -- when it is
measured. A fact that has to be re-derived costs hours and usually comes back
wrong the second time; the exit-anchor question and the per-market reward
breakdown were each researched twice before this rule existed. If it changes a
decision, or it took real work to learn, it belongs here with its measurement.

---

## Reward periods can end while markets still occupy all twenty slots

Established from the live `73d5c73` run at 2026-08-25T13:07Z using only the
existing commissioning log and read-only ledger. The capacity repair was
working: the runner and supervisor were alive, queue measurement had reported
`markets=20`, and no top-up or stream-recovery failure had reduced the set.
The account state was also healthy: zero positions, zero unresolved intents
and zero `UNKNOWN_ABSENT` orders. Yet only 18 working orders across nine
markets remained.

The gap reconciled exactly. At 13:00Z, **10 distinct bound markets** changed
from `eligible` or `target_not_met` to `no_incentive`; most transitions had
held their prior state for 320.7 seconds, matching selection shortly before
12:55Z followed by the reward boundary at 13:00Z. None of those ten appeared
in the current working-order set. Nine other markets each held a bid and offer
(18 orders total). The remaining bound market was cycling through
`target_not_met` and `resting_price_left_the_touch`. The most recent selector
evidence still showed 96 viable opportunities, so replacement supply was not
the constraint.

This is distinct from the free-capacity bug fixed in `73d5c73`. That repair
detects `len(active_slugs) < max_markets`; here the length remains exactly 20.
`_top_up_active_markets` releases only markets that are past game kickoff and
flat. A pregame market whose reward period ends is economically dead but still
counts as bound, so it can occupy a slot until kickoff while viable rewarded
alternatives remain outside the set. Full rotation still cannot normally run
because resting orders make `_safe_to_scan()` false.

The selector also explains why ten soon-ending rows could win the original
ranking. `select_applicable_period` checks an incentive's explicit `starts_at`
and `ends_at`, so each row was genuinely active at 12:54Z. But
`project_order_reward` then defines the day-of projection end as game kickoff,
and `_opportunity_key` contains no explicit reward-time-remaining key. Thus a
program ending at 13:00Z can be projected and ranked as though its pool remains
available until kickoff. The lifecycle gap keeps it bound after that optimistic
selection assumption becomes visibly false.

Detection signature:

```text
[queue-measurement] markets=20
working-order markets materially below 20
many [eligibility] ... to=no_incentive at one reward boundary
those no_incentive slugs absent from the working-order set
positions=0, unresolved=0, UNKNOWN_ABSENT=0
viable alternatives >= 20
no later [top-up] replacing the no_incentive slugs
```

The local repair now preserves the identity of the exact reward program that
authorized a slot. A missing incentive row, a different ended program, or an
ordinary refresh failure is not release proof. Only a successful fresh
incentive response that contains that same program ID with an explicit elapsed
`ends_at` confirms the terminal reward boundary. The market immediately stops
being economically authorized through the existing evaluator, but its slot is
released only after its position, working orders and unresolved intents are all
zero. A position near its forced-flatten deadline still defers discovery.

Confirmed reward end requests an incremental top-up immediately instead of
waiting for the normal selection interval. The top-up retains every healthy
bound market and its venue orders, excludes released/barred/retained slugs,
and slices additions to the exact free count. The selector and live evaluator
now both use the effective reward interval:

```text
start = max(game_start - 6h, explicit starts_at)
end   = min(game_start, explicit ends_at)
```

This fixes the false duration used to value a soon-ending program; it does not
add a league filter, reduce the six-hour candidate horizon, change quote size,
or change the 20-market cap. The repaired log identifies the lifecycle split
directly as `[top-up] ... expired=N reward_ended=N ...`.

Local proof: all 598 tests pass. Dedicated regressions cover explicit program
bounds at both pure reward and integrated selector layers, missing/different
program data, one-time exact-program confirmation, immediate scheduling,
working-order/position/unresolved ownership, exact-cap replacement and the
existing empty-set, no-replacement, near-deadline and failed-bind cases. Seven
deliberate mutants were killed: ignoring explicit end, failing to record the
authorizing ID, omitting reward-ended capacity, accepting a different program
ID, removing immediate scheduling, diverting ordinary safe full rotation and
removing unresolved-intent ownership.

Production deployment acceptance on commit `2e3c677`: all 11 deployed hashes
matched and the stopped droplet passed all 598 tests in 56.392 seconds. The old
runner stopped normally with `operator_sigterm` and `flat:true`; no force kill
was used. The unchanged `auto 10` supervisor/runner restarted at about
13:34:05Z. Independent queue telemetry reported `markets=20` at 13:35:18Z.
At 13:38:41Z the read-only ledger had 32 working orders across all 20 markets,
every working original quantity exactly 10, with zero unresolved intents, zero
`UNKNOWN_ABSENT` orders and zero positions. No new risk stop, runner failure,
maintenance pause, bound-stream recovery or top-up error appeared. A natural
post-deployment reward-end boundary has not occurred yet, so the new
`reward_ended=N` marker remains pending live observation; it was not forced on
a funded runner.

---

## Fewer orders can mean an underfilled active set even when viable supply is abundant

Established from the live 2026-08-25 size-10 run without mutating venue state.
The startup log showed that the selector chose 20 candidates, then preflight
correctly dropped two that had become non-open and bound 18. The queue
measurement independently reported `markets=18`. At 12:30:52Z the read-only
ledger held 16 working orders across 10 markets, three unresolved intents
across three markets, one 6.04-contract position, and zero `UNKNOWN_ABSENT`
orders. Those order counts were an instantaneous execution result; the
separate capacity fact was 18 bound markets under a configured maximum of 20.

Supply was disproved as the cause by one live-policy selector diagnostic:

```text
catalog                         12,466
inside the six-hour horizon        193
carrying rewards                   193
refused for tight book              83
eligible pregame                    110
books read                          100
deferred by scan cap                 10
VIABLE                               97 of 20 slots
```

The diagnostic's final, separate authenticated open-order read received one
Cloudflare 1015 because it competed with the funded runner. The runner stayed
alive and completed a CANCEL mutation at 12:31:31Z. Do not repeat
`usable_markets.py` beside a live runner merely to re-prove supply; use its
existing result, commissioning logs and read-only SQLite. This is another
example of why stacked authenticated diagnostics are prohibited.

The scheduler defect was exact: `_refresh_bound_books` may safely drop a
startup candidate, but the periodic incremental top-up branch was entered only
when an already-bound market was past kickoff and flat. Merely having
`len(active_slugs) < max_markets` was not work, so the two free slots could
remain empty until some different market expired. The six-hour horizon,
selector, quote size and venue supply were all functioning.

The reviewed repair treats either a releasable expired market or free capacity
as top-up work. It retains every non-expired market and every resting venue
order, excludes retained/expired/barred slugs, and slices replacements to
exactly `max_markets - len(keep)`. An underfilled startup schedules this scan
immediately instead of after the ordinary 900-second refresh. No replacement
means no bind and no stream disturbance. The existing near-flatten-deadline
inventory guard, strict stream rebind, 4 req/s limiter and 20-market hard cap
remain unchanged.

### Proper detection signature for the next 20-slot investigation

Do not use raw order count as slot count. Run a **20-slot capacity audit** and
correlate these layers from one run boundary:

```text
configured capacity     CommissioningConfig.max_markets (must be 20)
bound/subscribed set    [queue-measurement] ... markets=N
refill attempt          [top-up] released=R kept=K added=A free_slots=F
scheduler reach         distinct market= slugs in later [action] lines
instant venue result    working orders/markets, positions and unresolved state
universe supply         one live-policy viable scan only when not already known
```

For a startup drop, the expected positive marker is
`[top-up] released=0 kept=18 added=2 free_slots=2`, followed by a
`[queue-measurement] ... markets=20` observation or an equivalent exact
active/subscription tuple and actions across the newly added slugs. If supply
is at least 20 but `markets=N<20` persists beyond a due top-up, inspect the
top-up error/backoff and stream-rebind markers. If `markets=20` but working
orders are 12-14, investigate per-market decisions, blockers, positions and
in-flight mutations instead; the slot scheduler is full.

A concise prompt for a future reviewer is: **“Run a liquidity-bot 20-slot
capacity audit. From one exact run boundary, compare configured max, ordered
active/subscribed slugs, queue-measurement market count, top-up released/kept/
added/free values, distinct action markets, working-order markets, positions
and unresolved owners. Use the existing viable-supply result before making
another authenticated scan, and determine whether the loss is supply,
capacity refill, stream bind, or per-market planning.”**

Local proof before deployment: all 585 tests pass. Three deliberate mutants
were killed: restoring the expired-only trigger, restoring the 15-minute
startup delay, and allowing the replacement slice to exceed free capacity.

Production acceptance on commit `73d5c73`: all six deployed hashes matched,
the stopped droplet passed all 585 tests in 54.013 seconds, and the size-10
supervisor/runner started at 12:53:39Z/12:53:40Z. Startup itself found 20
healthy candidates, so no refill was needed; the independent
`[queue-measurement]` marker reported `markets=20` at 12:54:56Z. By 12:56:26Z
the ledger had 20 PLACE intents across 11 markets, 18 working orders across
nine markets, all original quantity 10, and zero unresolved intents,
`UNKNOWN_ABSENT` orders or positions. This is the intended distinction: the
active/subscribed scheduler is full at 20 while the instantaneous economically
resting subset is smaller.

---

## A failed top-up bind can look like a healthy twenty-slot process while every slot is stopped

Established from the live 2026-08-24 21:31Z incident by comparing the process
tree, commissioning log, and read-only ledger. The supervisor and runner were
both alive, so process health alone was a false signal. The decisive sequence
was:

1. `[top-up]` changed the active set;
2. `_open_streams_for_active_market` failed during REST reconciliation and
   deliberately closed both newly-created streams;
3. `[runtime-recovery] component=bound_streams attempt=N` kept increasing;
4. `[quarantine] called_by=_recover_bound_streams` repeated, with no later
   `[bound-stream-recovery] ... resumed=yes` and no new `[action]` lines;
5. the ledger contained acknowledged CANCEL intents joined to
   `UNKNOWN_ABSENT / missing_from_complete_snapshot` orders.

That combination means the 20-slot scheduler is not merely between orders:
the whole active set is waiting inside dual-stream recovery. `Working orders =
0` does not disprove it and must not be read as flat; the same inspection found
two nonzero position rows (+2.7700 and +0.3200). `Viable now`, slot count and
process PID likewise cannot distinguish this state.

For the next investigation, use this compact signature first:

```text
[top-up] or failed active-set bind
component=bound_streams attempt increasing
[quarantine] called_by=_recover_bound_streams
ACKNOWLEDGED CANCEL + UNKNOWN_ABSENT/missing_from_complete_snapshot
no [bound-stream-recovery] resumed=yes and no later [action]
```

The cause was not the queue-depletion measurement. Its isolated public-trade
socket was absent from the failing stack; the failure ran through top-up,
dual-stream bind, REST reconcile and terminal quarantine. It was also not a
reduction from 20 slots: twenty markets remained bound, but none could pass an
account visibility gate while both trading streams were closed.

The reviewed repair makes `[bound-stream-recovery] resumed=yes
scoped_ambiguous=N markets=...` the positive recovery marker. After that line,
each listed owner must remain `state_known=False` until positive terminal
proof, while other owners may produce `[action]` lines. If attempts reach 12
without that marker, the runner returns a bounded failure; shutdown still has
to prove the entire account flat before a supervisor restart is authorized.

Deployment acceptance on commit `bec8a83` preserved the scheduler: the
22:01:08Z runner reported `queue-measurement ... markets=20`, then created 18
placement intents across 18 distinct markets. The read-only checkpoint showed
17 working orders across 17 markets, no position, no `UNKNOWN_ABSENT` row and
one ordinary acknowledged PLACE in flight. That is startup convergence across
the twenty-slot set, not a reduced slot cap. All 573 production tests passed.

---

## Ten contracts and the $70/$80 account stops do not alter the twenty-slot flow

Established from the 2026-08-24 operator-authorized configuration review and
the full 551-test suite. Quote size is restart-scoped, so the live size can
return from 5 to 10 without editing the scheduler. At size 10 the existing
formulas mechanically produce an $8 early-exit cross cap, $90 per-market open
exposure, and $3 session execution-fee cap. `max_markets` remains 20, the
private-stream layout remains 8 + 8 + 4, the selection horizon remains 21,600
seconds, and the shared REST ceiling remains 4 requests/second.

The operator separately raised the realized session stop from $50 to $70 and
the marked-equity hard stop from $60 to $80. The $40 per-market stop is
unchanged and therefore remains the first loss bound for one bad market. These
are account-level daily budgets, not size-derived values. Three consecutive
full session-stop days can now consume about $210 before the existing terminal
consecutive-day bound requires intervention.

The review found one brittle regression test, not a production defect: the
same-UTC-day restart test simulated fixed $40 + $25 losses, which no longer
crossed the new hard stop. It now derives two individually sub-limit losses
from the active hard ceiling whose combined drawdown exceeds it by $1. This
preserves the invariant that a restart cannot hand back the UTC day's equity
budget. Deliberate mutations of the session stop, hard stop, and 20-market
lock were all refused; all 551 tests passed after the correction.

Production acceptance matched all eleven deployed file hashes and passed the
same 551 tests. The normal runner began at 2026-08-24T16:56:06Z after a
recovery-only process flattened one residual size-5 position. A venue read
found 23 initial orders across 16 markets, all with original and remaining
quantity exactly 10, and no position. The bound slate still totalled 20. After
convergence, the read-only ledger showed 33 working orders across 18 markets,
quantity 10 only, with zero unresolved intents, unknown-absent orders or
positions. Eighteen quoted markets is an instantaneous resting count, not a
reduction of the twenty bound slots.

The acceptance itself caused one avoidable transient: `dashboard.py` plus a
second authenticated order/position check ran outside the live process's REST
governor. The runner's next execution-backfill read received one Cloudflare
1015 `RateLimitError` at 16:57:50Z, recovered at attempt 1, and continued
placing. Subsequent acceptance used SQLite `mode=ro` only. Do not stack
authenticated diagnostics on a live startup; use one venue read for quantity,
then observe convergence from the ledger and logs.

One operational finding is separate from the configuration change: the prior
graceful stop command returned success although the runner's final JSON said
`flat: false`. The script waits for process disappearance, not flat proof. The
next startup's fail-closed recovery prevented new risk and flattened the
residue before the normal runner started, but a successful stop command alone
must not be cited as venue-flat evidence. This is recorded as an open item in
`NEXT.md`.

---

## The standalone dashboard can show a spent stop while the runner still has durable headroom

Established immediately before the 2026-08-24 size-5 restart. At 16:13Z the
standalone dashboard displayed `$60.97 / $50` for SESSION and repeated the same
`$60.97 / $60` on HARD. The account was flat. A direct authenticated balance
read and read-only ledger query showed:

```
UTC opening equity/cash   380.4631
current equity/cash       333.4311
durable drawdown           47.0320
session headroom            2.9680  (cap 50)
hard-stop headroom         12.9680  (cap 60)
```

The discrepancy is explained by the dashboard code: it reconstructs matched
pairs from today's ledger fills, then draws that same estimate for both SESSION
and HARD. The runner does not. SESSION reads current settled cash against the
durable UTC opening cash; HARD reads marked equity against the durable UTC
opening equity. The dashboard calls its number directional, so this is not a
trading defect, but the repeated HARD bar is not evidence that the emergency
stop has fired and can provoke an unnecessary manual shutdown. Confirm with
the durable baseline and venue balance before acting on that standalone bar.

---

## Five contracts reduces fill exposure without reducing the twenty-slot scheduler

Established from the size-scaling analysis already recorded below, the live
configuration dependency graph, and the 2026-08-24 pre-deployment test run.
The operator chose to return quote size from 10 to 5 contracts because size is
leverage, not edge: the prior measurement found reward and unwind cost both
scaling approximately linearly. Smaller orders therefore reduce the dollars at
risk in a fill but are not expected to improve the reward-to-loss ratio by
themselves.

The current size-dependent values are now:

| setting | before | after | reason |
|---|---:|---:|---|
| `quote_size` | 10 | 5 | halve each resting order and fill |
| `max_early_exit_loss_usd` | $8 | $4 | preserve the size-5 crossed-loss calibration |
| `max_market_exposure_usd` | $90 | $45 | preserve nine-times per-market headroom |
| `max_session_execution_fees_usd` | $1.50 | $1.50 | old size-10 value was not scaled; now `0.30 × size` |

The initial patch still represented those first three values as independently
editable fields. The operator's follow-up requested one dynamic setting, which
exposed a fourth dependency: the execution-fee cap was documented as enough
for 20 peak-fee exits but had stayed $1.50 at size 10, enough for only 10. Quote
size is now the only setting. Whole values 5-10 are accepted at startup, and
the other three values are computed properties (`0.8 ×`, `9 ×`, `0.30 ×`).
The current persisted/default size is 5. It cannot hot-change inside a session.

The account-level limits remain $40 per market, $50 per session and $60 marked
equity because they protect the account rather than one order. Portfolio
exposure remains $340 for the same reason.

**The twenty-slot flow does not change.** `max_markets` remains 20, the private
stream remains split 8 + 8 + 4, the REST ceiling remains 4 requests/second,
and the selection horizon remains 21,600 seconds (six hours). The regression
test asserts both `max_markets == 20` and `quote_size == 5`; the isolated
production-environment suite exercised all twenty recovery owners and passed
551 tests after the final change.

One deliberate selector consequence must not be misdiagnosed as a scheduler
failure: catalog admission requires `minimum_trade_qty <= quote_size`. A market
whose venue minimum is 6-10 contracts qualified at size 10 and will not qualify
at size 5. That can reduce the viable universe, but it cannot reduce the slot
ceiling. Diagnose the distinction with `scripts/usable_markets.py`: compare its
viable count with the venue's distinct quoted markets and the configured 20.

The bot was gracefully stopped before this change. Its final record at
2026-08-24T15:52:48Z reported `flat: true`, and neither runner nor supervisor
remained alive.

## The slot diagnostics were selecting with different parameters than the live runner

Established by tracing all `RewardSelectionPolicy` constructors during the
five-contract review. The live runner built its selector from
`CommissioningConfig`, but `scripts/usable_markets.py`, `scripts/dashboard.py
--markets`, and `scripts/period_mix.py` called the generic constructor. Before
this fix that meant diagnostics used 1 contract, 25 detailed books and a 2x
cost multiple while production used the configured size, 100 books and 1x.
The economic gate is currently disabled, so the 2x difference did not remove
live opportunities, but quote-size minimum-quantity admission and the 25-book
cap could make the diagnostic funnel disagree with the runner.

`CommissioningConfig.reward_selection_policy()` now owns this mapping. The
runner and all three operator diagnostics call it, while
`diagnostic_defaults()` deliberately carries false authorization flags and
cannot arm trading. The launcher persists the chosen restart-scoped size under
`data/`, and diagnostics read it. Regression tests prove the live and
diagnostic policies are equal and pin the chosen size, six-hour horizon, 100
candidates and the 1x factor.

---

## Private recovery resumes the process but mass-cancelled owners time out and remain barred for 30 minutes

Established from the live 2026-08-24 runner, its ledger and the exact two
private-disconnect timelines. This is an availability defect, not evidence of
an unsafe position or a telemetry-triggered venue mutation.

The private stream disconnected twice in the same session:

| disconnect | private recovery resumed | scoped owners | CANCEL intents | terminal after 60s | markets retired |
|---|---:|---:|---:|---:|---:|
| 07:20:02Z | 07:20:38Z | 20 | 29 across 19 markets | 26 / 29 | 18 |
| 11:47:30Z | 11:48:10Z | 20 | 29 across 18 markets | 27 / 29 | 19 |
| 12:28:09Z | 12:28:46Z | 19 | 31 across 18 markets | 30 / 31 | 17 |

The second incident is the source of the operator's retirement alerts. Global
private-stream quarantine correctly canceled positively-known working orders,
and the scoped recovery correctly restored the private stream without waiting
for every cancel. The remaining cancel proofs were then repaired through the
background owner-at-a-time path. Their terminal latencies were 388.85s median
and 695.16s maximum in the 11:47 incident, far beyond the fixed 60-second
unresolved-mutation threshold. The timeout therefore retired nearly the whole
bound slate even though the ambiguity was attributable and was still
converging. The 07:20 incident had the same shape: 346.27s median and 683.29s
maximum terminal latency.

All 19 markets from the 11:47 incident subsequently logged `released=flat` by
12:00:03Z. At 12:03Z the read-only ledger showed zero nonzero positions, zero
unresolved intents and zero `UNKNOWN_ABSENT` orders; the runner and supervisor
were still alive and new PLACE intents were RECONCILED. However,
`_release_retired_if_flat` removes the active retirement without clearing the
1,800-second `_retired_until` bar. Those owners therefore remain unable to
build quotes until roughly 12:18-12:19Z. The `[selection] retired=36` value is
the session-cumulative `_retired_ever` counter, not 36 currently unsafe or
actively retired markets. The alert text saying an unresolved-mutation owner
"will not quote again this session" is also inaccurate for this transient
reason: after flat release it can quote when the 30-minute bar expires.

The telemetry audit did not cause the incident. The 11:47 private disconnect
was logged at **11:47:30.179Z**, about 26 seconds before the read-only audit
began at 11:47:56Z, and the same mass-retirement pattern had already occurred
at 07:20Z with no audit running. The report opened SQLite with `mode=ro`, made
no venue/API call, and the observation path is excluded from CANCEL intents.
At the 12:03 check observation coverage had advanced to 574 required / 574
captured with zero missing. The deployed observation code therefore remains
disproved as the trigger; the open defect is the interaction between global
recovery cancellation, slow owner-at-a-time terminal repair, the 60-second
timeout and the 30-minute post-resolution bar.

**Third recurrence, 12:28Z:** the earlier bars expired as predicted and broad
quoting resumed without a restart: `session_creates` rose from 677 at 12:15Z
to 711 at 12:21Z, with placements across the recovered slate beginning at
12:19Z. Only nine minutes later the private stream disconnected again. Fresh
private visibility returned in 37.48s, but 17 owners retired from 12:29:21Z
through 12:29:52Z. All 31 recovery CANCEL intents were RECONCILED by the
12:31Z audit, at 151.91s median and 155.49s maximum; 30 exceeded the 60-second
threshold. Unknown-absent and unresolved counts had returned to zero, but one
market still held +3.1100 contracts and three active orders remained. This
proves the cooldown does self-clear and the scheduler repopulates afterward,
but repeated private disconnects can erase that recovery again before it has
earned for even ten minutes. Leaving the defect to recurring cooldowns is not
an acceptable operating state.

### Repair validation and the exact diagnostic for next time

Local review established that terminal proof does not require another complete
account snapshot for every owner. The normal ten-second account reconcile
continues to provide complete order and position snapshots; the unresolved
path can independently ask GET-by-ID for a bounded group of positively-owned
intents. Five concurrent owner scopes still enter the existing shared 4 req/s
limiter one request at a time, while their independent NotFound retry sleeps no
longer serialize readable owners. A synthetic twenty-owner recovery set gained
terminal proof in four fair passes. In a mixed five-owner set, one id remained
NotFound while the other four resolved and entered cancel-confirmed priority
replan; the lagged id did not hold them behind it.

The quote bar is now reason-specific. `unresolved_mutation_timeout` releases
only after the owner has positive terminal intent proof, no working order and a
zero position, and that release clears its transient `_retired_until`. A
ten-contract positioned owner remained retired and barred in the regression
test. Existing book/plan/action cooldown tests and all twenty-slot scheduling
tests remain unchanged and pass. The complete local suite is 539 tests.
Mutation checks proved each new assertion detects its corresponding defect:
one-owner batching exceeded the four-pass bound, serial reads failed the
mixed-404 overlap assertion, and preserving the stale cooldown failed the
transient release assertion. Conflicting market owners for one exchange id
remain account-wide; removing that check failed its regression test.

Production acceptance on commit `a4836b5` passed the same 539-test suite on
the droplet. The runner restarted at 13:01Z and reached 20/20 quoting by
13:04Z. The 13:02:54Z reconcile listed exactly twenty distinct active markets;
the account was flat with zero `UNKNOWN_ABSENT` orders, and seven ordinary
in-flight placement/cancel intents at the first dashboard read converged to
zero unresolved. This proves the deployment did not reduce twenty-slot
capacity and normal intent convergence remains healthy. It does not simulate
the repaired trigger, so live recovery acceptance remains the next naturally
occurring private disconnect; do not manufacture one on a funded account.

Recognize this exact failure from one run boundary, not from an instantaneous
order count:

```bash
grep -aE 'component=private_stream|\[private-recovery\]|\[quarantine\]' \
  logs/commissioning.log | tail -80
grep -aE '\[unresolved-repair\]|unresolved_mutation_timeout|released=flat' \
  logs/commissioning.log | tail -120
grep -a '\[action\]' logs/commissioning.log | tail -60
```

- `[private-recovery] resumed=yes scoped_ambiguous=N` proves visibility
  recovered; it does not prove the named cancel owners are terminal.
- `[unresolved-repair] owners<=5` proves the focused batch is running. Watch
  `resolved`, `remaining`, `failures` and `elapsed` across consecutive lines.
  Readable owners must progress even when one batch reports a failure.
- `unresolved_mutation_timeout` followed by terminal/order-free/flat proof must
  produce `released=flat` without another thirty-minute quote bar. A nonzero
  position correctly prevents that release.
- Count distinct `[action]` markets after releases and compare them with the
  exact active set and independent per-market plans. Do not divide one working
  order snapshot by twenty, and do not use the cumulative dashboard `retired`
  value as the number currently barred.

A concise prompt for another reviewer is: **“Audit liquidity-bot post-private-
disconnect terminal repair and twenty-slot recovery. From the current run
boundary, correlate quarantine CANCEL owners, `[private-recovery]`, each five-
owner `[unresolved-repair]` batch, terminal/UNKNOWN_ABSENT state,
`unresolved_mutation_timeout`, `released=flat`, current quote bars and distinct
action markets; verify one NotFound owner cannot block readable owners and a
flat resolved ambiguity retirement has no leftover 1,800-second bar.”**

---

## Prospective adverse-fill observation — live collection began 2026-08-24

The existing ledger can describe what happened after a fill, but it did not
persist the decision-time book and reward facts needed to learn which resting
orders are most likely to fill. A read-only study of the mutable 2026-08-21
snapshot found:

- 5,054 post-only resting orders, 833.7 order-hours and 344 maker-filled
  orders;
- 1,964 matched entry/exit pairs across 180 balanced markets, with gross
  trading P&L of -$99.47, $10.66 of fees and net trading P&L of -$110.13;
- passive/maker-only exits averaged about -$0.0064 per pair, while any taker
  exit averaged -$0.0632 to -$0.0652 per pair;
- a market-cluster bootstrap put the taker result at -$0.0632 per pair with a
  95% interval of [-$0.0811, -$0.0474], maker-only at -$0.0064 with
  [-$0.0269, +$0.0084], and their difference at -$0.0568 with
  [-$0.0803, -$0.0310].

This is correlation, not proof that refusing taker exits is safer. LONG versus
SHORT, broad price, recent touch-cancel count, re-entry delay and prior
same-side price range did not produce a stable monotonic temporal separator.
The 15-minute-plus placement-to-fill bucket was the worst validation bucket,
so the evidence also does not support a universal cooldown. League was not a
stable causal signal and must not be used as a filter.

The missing prospective inputs were exact price-time queue ahead, Target Size
surplus/cutoff, spread and depth imbalance, touch velocity, projected reward,
stressed-exit result and expected net at placement. The local observation
branch now records those values atomically with the exact building PLACE
intent/client order ID. It never writes observation fields into the venue
payload and reducers are excluded. Missing telemetry is marked for coverage
reporting and never blocks a valid order.

One concrete model discrepancy was found while tracing that path: the current
fill-hazard input calls best-touch size `queue_ahead`, including when the quote
is backed off from the touch. Under price-time priority, the observed queue at
that planned price includes every better level plus the full same-price level.
Both numbers are now recorded separately. The live model was deliberately not
changed before held-out evidence exists.

Public trade-volume fields remain NULL because commissioning subscribes to
books, not public trades; using our own fills as market volume would be false.
No trade subscription, REST call, extra placement-time ledger lookup,
selection criterion, league gate, scheduler input, slot limit or strategy
decision was added. The observation JSON is
about 1,245 bytes; at the historical high placement rate the rough additional
ledger growth is about 15 MB/day, or 45 MB for the proposed three-day sample.
On a local 1,000-intent comparison, baseline durable intent preparation took
1.157s and intent-plus-observation took 1.642s: about 0.49ms incremental work
per building order, including observation construction. The checkpointed
SQLite files used about 2.15KB additional space per observed order after table
and index overhead. This is a synthetic local measurement, not a production
latency percentile.

The acceptance rule is prospective: discard the partial startup day, collect
three complete UTC-day cohorts and about 300 maker-filled observed orders, use
the first two complete days for training, and keep the third complete day held
out. Any candidate rule must first run in would-refuse shadow mode. No
production defect is claimed fixed by this measurement work.

Commit `2acf544` was pushed and deployed on 2026-08-24. All 18 deployed file
hashes matched, a disposable copy of the real 78.0MB ledger migrated without
changing its 19,850 order or 1,366 fill rows, and all 534 production tests
passed in 40.460s. Graceful stop exposed the already-documented parked-position
recovery deadlock; telemetry created no building risk during recovery. After
the operator manually canceled/closed the remaining state, the normal runner
started and the first observation landed at **2026-08-24T02:51:31Z**. At
02:53:52Z it had 37 required observations, 37 captured and zero missing. The
partial August 24 cohort is warm-up; August 25–26 are training, August 27 is
held out, so the earliest review is **2026-08-28T00:00Z**, and later if fewer
than roughly 300 maker-filled observed orders exist then.

**Live integrity checkpoint, 2026-08-24T11:47:56Z:** 569 building PLACE
intents required observations and all 569 had one; missing, orphaned, duplicate
and intent/client/market linkage mismatches were all zero. A second-pass audit
found zero mismatches in nested wire price, quantity, BUY_LONG/BUY_SHORT side
mapping, post-only flag, immutable JSON hash, observed-queue arithmetic,
30s-versus-120s touch ordering or observation/intent clocks. Book age was
0.001226s median, 4.991020s maximum and never exceeded the 5s decision bound.
All 569 intents were RECONCILED; 38 had exact maker-fill labels, 502 were
terminal unfilled and 29 remained open. The capture already contained 105
orders where model and observed queue differed. There were zero
`[quote-observation]` errors. Public trade volume remained NULL by design.
The read-only report correctly returned `COLLECTING`, not an enforcement
recommendation.

---

## Almost-front queue measurement — deployed and collecting (2026-08-24)

Exact exchange queue rank remains unavailable, but a conservative interval is
measurable. The local implementation starts each acknowledged building order
from the already-recorded `depth_better_than_quote + depth_at_quote`. Better
displayed depth is always ahead; public trades at our price definitely consume
same-price priority; new same-price orders are behind us; and cancellations
remain ambiguous, widening lower versus upper bounds. Reducers, IOC exits and
cancels never enter the study.

One edge defect was found during review before deployment: books and trades
arrive on separate sockets and have no shared delivery order. Subtracting a
trade from a full book that already reflected it counted consumption twice and
could falsely declare `almost_front`. The reviewed estimator instead retains
the decision-time queue as descriptive context, then establishes its
conservative lifetime baseline from the first full book received after
acknowledgement. From there, same-price upper depth is a nonincreasing full-book
cap: later same-price additions are behind us, while drops may be ahead or
behind. Public prints remain definite consumption labels but are not subtracted
independently from that cap. Better-price depth also comes from the current
full book. The shadow trigger is evaluated only on a full-book boundary, never
on an independently ordered trade callback. A dedicated reordering test fails
if double subtraction is restored.

A second pre-deployment review defect was also removed: the condition used to
persist the first public trade stayed true while the count remained exactly
one, so a quiet market could rewrite stream health after every one-second
timeout. The collector now latches that first proof once and returns to its
five-minute health cadence. Its integration test forces repeated timeouts and
requires exactly one first-trade update.

The review also found two placement-latency boundaries. A book can be received by
the live socket while the POST is in flight, then remain held by the runner's
task until after acknowledgement registers the tracker. Applying that older
book would describe a time when our order was not resting. Queue books are now
ignored when their socket receipt precedes the acknowledged measurement start;
a focused regression test pins this ordering. Also, same-price orders can join
during POST latency and stand ahead of us; therefore the older decision queue
cannot cap the acknowledged order. A second test grows same-price depth from
200 to 260 during that window and requires the first post-ACK full book to
retain the larger conservative upper bound.

Business isolation is explicit:

- the collector uses a separate WebSocket and no REST call;
- its queue is bounded at 4,096 prints; overflow/decode/reconnect gaps are
  persisted and suppress shadow signals for affected orders;
- failures are logged and swallowed outside the planner/action path;
- there is no cancel/repost method and no observation feeds the scheduler;
- history appends only START, post-ACK baseline, relevant trades, queue-band transitions,
  coverage gaps, shadow signals, fills and CLOSE; summary rows checkpoint no
  more often than every five minutes;
- a local 40-active-order no-change benchmark processed 100,000 book callbacks
  in 0.7000s, about 7.00 microseconds per callback. This is a local CPU sizing
  number, not production latency acceptance.

The predeclared shadow candidate is minimum dwell 60 seconds and conservative
queue-ahead upper bound <= one order quantity. It emits
`WOULD_QUEUE_RESET`; it does not touch the order. Stream sessions separately
persist scope, trades received, drops, decode failures, reconnect/error status
and health timestamps so a quiet or failed collector cannot masquerade as
clean coverage. `scripts/queue_measurement_report.py` opens SQLite `mode=ro`
and remains league-neutral.

All 571 local and production tests pass. A fail-open integration test proves a broken tracker
does not suppress or duplicate the authorized PLACE and leaves `max_markets`
at 20. A second integration test proves the collector makes zero trading/REST
calls. Mutation checks specifically kill post-PLACE registration and the
cross-socket ordering protection. No production bug is claimed fixed.

Deployment acceptance: commit `6085ca5` restarted at 18:49:40Z after the old
runner flattened one size-10 position and emitted `flat: true`. The collector
announced `status=collecting markets=20`. At the acceptance checkpoint it had
38 measured orders, all 38 with post-ACK baselines, two clean shadow reset
signals and one active stream session, with zero coverage gaps, dropped prints,
decode failures, errored sessions or `[queue-measurement] status=degraded`
lines. No public trade happened in that short checkpoint, so trade-label
coverage remains a natural-live-event watch, not yet a proved production
observation.

---

## Current status — 2026-08-15

**`max_markets` is 16** across two private connections -- set by the REST
governor, not the subscription cap (§7, finding 8). Thirty was tried live and
produced zero placements. Six production bugs fixed today, all
mutation-checked; see `BUGFIXES.md` for symptom and mechanism on each.

**Critical facts established 2026-08-15:**

1. **The $40 stop was reading one-sided round trips.** `realized_pnl` returned
   **+3.26** for a trade that lost **$1.55**. The formula is right; the closing
   fill was simply never recorded. Only two paths wrote fills -- the
   order-create response (inline executions only) and the private WebSocket --
   and a shutdown IOC satisfies neither, because it returns before its match
   completes and the fill then lands on a socket shutdown is closing. Three
   closes were lost this way. Fixed by replaying `portfolio.activities` during
   `_rest_reconcile`; `apply_execution` dedupes on `execution_id`.

2. **Fills ARE stored in an ambiguous price frame.** An earlier version of
   this finding claimed the opposite and was wrong; it is replaced rather than
   edited, because it was used as grounds not to investigate.

   The venue prices an execution against a specific outcome
   (`marketMetadata.outcome`), and `normalize_execution` stores `lastPx`
   without recording which outcome it was quoted on. The frame is then
   unrecoverable. Measured on `aec-valorant-ff-ep`: both legs priced against
   *Enterprise Esports* at 0.39 and 0.67, where 0.67 means 0.33 in Fire Flux
   terms. The ledger computed **+$1.34** on a trade that actually lost about
   **$0.30**.

   This matters because `realized_pnl` is what the $40 stop reads, and the
   error runs in the direction that hides losses. See task 24; the exact rule
   is NOT established and two theories have already been wrong.

3. **The shutdown flatten had no price cap.** It asked only whether
   `cross_exit` was complete, never what it cost, then fired an IOC at
   `worst_price`. The runtime hard exit refuses above `max_exit_loss`;
   shutdown had no equivalent, so every redeploy was an uncapped liquidation of
   any open position. Observed: a break-even short crossed out at 0.96 against
   a 0.65 entry.

4. **Tick prices must be rounded off the grid multiplication.** `41 * 0.01` is
   `0.41000000000000003`, and `build_order_payload` validates in `Decimal`.
   Nine of the ninety-nine cent prices land this way: **35, 41, 47, 57, 69, 70,
   82, 83, 94**. An unaligned price is rejected outright and retires the
   market.

5. **`book is not two-sided` is the most common error the bot produces** --
   2,121 occurrences across 21 of 77 markets ever bound, two of them retried
   about 1,100 times each at three-second intervals. Such a market can never
   quote, so the retries are pure cost.

6. **The venue publishes bids only.** The offer on one outcome is one minus the
   best bid on the other, so `best_offer - best_bid` is exactly the gap an
   operator reads between the two outcomes' bids. This is why the quoting rule
   is stated as "two cents in the middle".

7. **The REST rate cannot be exceeded by adding markets.** Every call awaits a
   single shared `AsyncIntervalLimiter` (`adapters.py:334`) that sleeps until
   the minimum interval elapses, so more markets queue rather than raise the
   rate. `from_rate` also refuses anything above the documented 20/s retail
   ceiling. What scales with market count is *freshness*, not rate: 30 quiet
   markets at 4 req/s is a 7.5s cycle against a 5s staleness bound, so quiet
   markets age out and stop quoting. That is safe, and the lever if it bites is
   a longer quiet-refresh interval -- never a higher request rate, since
   maker-lab was rate-limited and settled at 0.3 req/s.

8. **The REST governor caps market width at ~16, not the socket cap.**
   Measured live 2026-08-15T13:48Z at `max_markets = 30`: **29 markets bound,
   58 `book_stale` events, and ZERO placements.** Not one market could quote.

       N markets / 4 req/s must stay under max_book_age_seconds (5.0)
       30 / 4 = 7.5s cycle  ->  every book stale before its turn
       N < 20; 16 leaves a second of margin

   Subscriptions were never the limit -- 30 markets across four connections is
   subscribable and the sockets came up fine. Width past this point is not
   merely wasted, it is **negative**: extra markets starve the ones that would
   otherwise have quoted. Raising `rest_requests_per_second` is not the lever
   (maker-lab was rate-limited and settled at 0.3; we are at 4). Going wider
   needs a longer quiet-refresh interval or a higher staleness bound first,
   measured deliberately.

9. **The market-data WebSocket stops delivering somewhere between 16 and 29
   slugs on one subscription.** Measured 2026-08-15 with a standalone script,
   40 seconds per trial, one `subscribe_market_data` call carrying all slugs:

       subscribed  8  ->  books from  8 slugs, 249 messages
       subscribed 16  ->  books from 16 slugs, 256 messages
       subscribed 29  ->  books from 20 slugs,  20 messages

   At 29 the stream effectively dies: ~1 message per slug and then silence,
   against ~16 per slug at the smaller widths, and nine slugs never deliver at
   all. **This, not the REST governor, is why 30 markets produced zero
   placements.** Books never arrive over the socket, every market goes quiet,
   REST becomes the only path, and everything reports `book_stale`. The
   earlier `30 / 4 req/s = 7.5s` arithmetic described the overwhelmed fallback,
   not the cause -- it is superseded, and finding 8 above should be read with
   that correction.

   Also observed in the same run: `book is not two-sided` surfaced as a
   *stream error* out of `next_book`, killing the whole stream rather than
   skipping one market. One malformed book can stop book delivery for every
   market on that socket.

   **A second run did not reproduce it**: 29 slugs then delivered 27 markets
   in 415 messages and stayed healthy, while 16 delivered only 152. So width
   is not the variable at all -- the feed dies when a malformed book happens
   to arrive, and more markets only raises the odds. One run is noise; measure
   three times before believing a width.

   Fixed by skipping unusable books instead of raising them (streams.py).
   Scope violations and structural decode failures stay fatal.

   **Re-measured after the fix, three runs per width, 25s each:**

       width 16 -> 16/16 slugs,  82- 91 msgs, 0 dropped
       width 20 -> 20/20 slugs, 153-165 msgs, 0 dropped
       width 24 -> 22/24 slugs, 184-202 msgs, 0 dropped
       width 29 -> 27/29 slugs, 202-226 msgs, 0 dropped

   Twelve trials, **zero deaths**. The socket sustains 29. The two slugs
   missing at 24 and 29 are the same two in every run -- `zhuole-hohser` and
   `horana-shevik` simply do not publish. Message volume rises with width, as
   a healthy feed should.

10. **Market width is set by what REST can carry ALONE, not by the socket.**
   The socket sustains 29 (finding 9). The binding question is what happens
   when it drops, because a held position must still be exitable: REST
   refreshes N markets in N/4 seconds against a 5s staleness bound, so 20 sits
   exactly at the limit and 24+ does not fit. Above 20 a feed drop leaves every
   book stale, the exit unpriceable, and inventory riding toward settlement --
   which is how maker-lab lost $443.88.

   **max_markets = 20.** Worst-case exposure $100 against the $340 cap, so the
   risk budget is not binding. This supersedes finding 8, which reasoned from
   REST arithmetic alone before the feed fault was known.

10. **A position in a market whose book stops parsing can NEVER be exited.**
   It rides to settlement with certainty. Measured 2026-08-15 on
   `aec-setkamemd-chevic-scovla`: 254 log mentions, of which exactly **one**
   is an order action -- the entry quote at 15:51. After the fill at 18:52 the
   bot never placed another order on that market. The other 253 lines are
   `book is not two-sided` / `book_stale`.

   The chain: `parse_book_response` requires BOTH sides populated, so a
   one-sided book fails; the book then never refreshes and goes stale; the
   reconciler returns `book_stale` as a critical *before* planning anything;
   and `_inventory_plan` never runs. The passive reducer, the hard exit AND
   the pre-kickoff flatten all live inside that path, so all three are lost at
   once.

   **This is not a late flatten, it is no flatten.** About one market in four
   publishes a one-sided book (finding 5), so this is common, not exotic.

   The asymmetry that makes it fixable: exiting a long only needs the **bid**
   side to sell into. A one-sided book with bids prices that exit perfectly
   well. We reject it because the parser demands both sides before returning
   anything -- a requirement that belongs to *quoting*, not to exiting.

11. **Fills are recorded in OUR frame, and match our own orders exactly.**
   Measured 2026-08-15 across all 54 fills, joining each fill to the order that
   produced it: **every MAKER fill has fill price == our order price**, without
   exception. A maker fills at its own limit, so this is the strongest
   available check. The only mismatches are TAKER fills, where a better price
   than the limit is exactly what should happen.

   **So there is no frame inversion in the fills table**, and the earlier
   entry claiming otherwise (superseded below) was wrong.

   The discrepancy is one level up: what our order PRICE MEANS for a
   `BUY_SHORT`. That is the known intent inversion recorded in
   `live_orders.py` -- a short quote submitted at 0.36 rests on the venue at
   0.64. Our ledger is internally consistent; whether our submitted price
   equals the economic price for both intents is the open question.

   For `aec-valorant-ff-ep` the ledger says bought 0.39, sold 0.67 (+$1.34);
   the venue app showed 1.94 in and 1.72 out. 5 x 0.39 = 1.95 matches the buy.
   5 x 0.67 = 3.35 does not match the sell, while 5 x (1 - 0.67) = 1.65
   roughly does. So the BUY_LONG leg reads true and the BUY_SHORT leg does
   not.

   **The decisive test is now cheap**: the session stop reads settled cash
   (finding 12), so comparing cash delta against the ledger's computed P&L on
   any round trip settles which leg is wrong, with no inference.

12. **SOLVED: `BUY_SHORT` is a PURCHASE, not a sale. realized_pnl takes its
   sign from the venue's side label instead of the intent, which flips a loss
   into a gain.**

   Both intents we send are BUY intents and the price goes unchanged --
   documented in `live_orders._intent` and validated by maker-lab across 201
   fills. So `BUY_SHORT` at 0.67 does not sell at 0.67; it BUYS the opposite
   outcome for 0.67. Holding both sides nets the position out, because the
   pair settles at exactly 1.00.

   `aec-valorant-ff-ep`, worked through:

       BUY_LONG   5 x 0.39  =  paid 1.95
       BUY_SHORT  5 x 0.67  =  paid 3.35
       paid for 5 pairs        5.30
       5 pairs settle at       5.00
       realised                -0.30      <- matches the venue app exactly

   `realized_pnl` books the BUY_SHORT as cash IN of 3.35 because the venue
   labels it `ORDER_SIDE_SELL`, and computes +1.34. The label describes the
   outcome being taken, not the direction of our cash.

   **The fix is the sign, not the price.** Cash out for both intents, plus
   1.00 per netted pair. Prices are already correct (finding 11) -- fill price
   equals our order price on every maker fill.

   The session stop is already immune: it reads settled cash (finding 13).
   What is still wrong is per-market attribution and reporting.

13. **SUPERSEDED -- the fill price frame is not derivable from
   portfolio.activities.**
   Attempted 2026-08-15 across every trade on the account. Two obstacles:

   - every leg returned carries `aggressor: true`, and `makerExecutions` is
     empty on all of them. So the feed shows the COUNTERPARTY's order, not
     ours, whenever we were the maker -- which is most fills.
   - the submitted price and the execution price differ by a uniform **0.10**,
     positive for `BUY_SHORT` and negative for `BUY_LONG`
     (0.30->0.42, 0.57->0.65, 0.28->0.38 / 0.52->0.42, 0.59->0.49,
     0.58->0.50). Too consistent to be price improvement, and unexplained.

   Until our own maker legs can be read, any conversion rule would be inferred
   from someone else's order. Two attempts at reasoning this out have already
   been wrong. **Do not write arithmetic on top of it** -- realized_pnl feeds
   the $40 stop.

   What is now available that was not: fills record `outcome` (commit
   4bfadde), so once our own legs are obtainable the rule can be derived
   rather than guessed.

12. **The session loss stop reads settled cash, not a total rebuilt from
   fills.** Cash moves only when a trade actually executes, so there is no
   price frame to decode, no fill that can be missed, and nothing that can
   disagree with itself. Every P&L defect found on 2026-08-15 came from
   rebuilding a number the venue already knows: counterparty legs recorded as
   ours, closing fills lost on a restart, prices quoted against the opposite
   outcome.

       session realised = (cash now - cash at session start) + position x basis

   Open inventory has already spent cash, so it is added back at its own
   basis, and it falls back to the fill ledger whenever cash or a basis is
   unavailable -- a stop that raises is worse than one computed the old way.

   **Settled cash, not marked equity.** The earlier decision to reject marked
   equity (320 contracts move $38 on a twelve-cent mark) does not apply: cash
   does not move on paper price changes. The two were conflated in the test
   that covered this, which set `currentBalance` while reasoning about paper
   movement.

   Per market stays fills-based -- cash is account-wide -- and being wrong
   there costs one market rather than the account.

13. **Reward pools are per market** (`reward_pool=20000.0` on a single market's
   incentive period). More markets reaches more pools; more size only buys a
   larger share of the same one. This is the argument for raising market count
   rather than quote size, and it matches maker-lab's 30 markets at size 5.

---

## Current status — 2026-08-14

**Bot: stopped.** Account verified flat at the venue (0 open orders, 0 nonzero
positions). Deployed commit recorded in `/root/liquidity-bot/DEPLOYED_COMMIT`.

**Multi-market support is complete**, all 9 steps, 281 tests passing.
`max_markets` is now **8** — the measured private-WebSocket ceiling, see
§7. Quote size is unchanged at one contract: breadth expanded, per-market
exposure did not. Not yet committed, deployed or run.

**The open question is not correctness, it is whether the opportunity exists.**
The machinery works. A backtest over 24 hours of real books authorized one
quote worth about three cents. See §6.

---

## 1. Fee and rebate structure

Verified against live commission data on our own fills, 2026-08-13.

```
taker fee   = 0.06   x qty x p x (1-p)      charged
maker rebate= 0.0125 x qty x p x (1-p)      credited (negative commission)
```

**Rounding is asymmetric against small size.** At one contract, p=0.81:

```
taker fee    $0.0092  ->  rounds UP   to $0.01
maker rebate $0.0019  ->  rounds DOWN to $0.00
```

Measured directly: `passiveExecution` commission was **0.0000 on all four**
maker fills on 2026-08-13. Per-contract economics are roughly **37% worse at
size 1** than at size 10. maker-lab only ever observed the rebate because it
filled 4–537 contracts at a time.

The rebate is maximised at p = 0.50: `p(1-p)` is 0.25 there versus 0.154 at
p = 0.81, so mid-priced markets pay **62% more rebate per contract**.

## 2. Exit behaviour

### The passive-vs-crossed comparison is a selection artifact

maker-lab live data, 2026-08-11/12, 101 exits:

```
kind      n     pnl        fees      winners   losers
passive  61   +$20.16     $0.00        60         1
cross    40   -$34.81     $6.05         2        38
settle   16   -$88.20        -          2        14
```

Per event that reads as **passive +$0.33 vs crossed -$0.87** — and it is
**not a comparison of exit policies.** maker-lab ran
`FLATTEN_NEVER_AT_A_LOSS = True` (`config.py:396`), so its reducing quote was
floored at cost basis. A floored quote can only fill when the market rises to
meet it. Winners therefore exit passively; losers cannot fill at all and leave
only by crossing or by settling.

maker-lab documented this themselves, in `live_runner.py:1609`: the settlement
pool is *"79% losers BY CONSTRUCTION."* The 60-1 and 2-38 splits above are that
construction, measured. Any strategy with a basis floor produces this table
regardless of whether passive exits are actually better.

**So the floor does not avoid losses, it defers and concentrates them.** In
this session it routed -$88.20 into settlement — 86% of the session loss —
while booking the winners as passive-exit profit. maker-lab measured the same
effect at larger scale: 234 positions settled for -$443.88 where crossing at
T-15 would have cost -$64.18, **a +$379.70 swing with 78% of positions
improving** (`live_runner.py:1604`).

**Conclusion: the reducing quote must rest at the touch, with no basis floor.**
liquidity-bot already does this. This is not the code disagreeing with the
safety rule — it is the rule carrying maker-lab's most expensive defect.
See `docs/SAFETY_INVARIANTS.md` invariant 5.

### The 30-second window captured almost none of them

112 position episodes reconstructed from fills and exits:

```
passive latency  p25 13.3s   median 100s   p75 653s   p90 1367s
```

Cumulative share of passive exits captured by timeout:

```
  30s -> 26%      300s -> 31%      900s -> 46%     1800s -> 50%
```

The median passive exit took **100 seconds** — more than three times the
window that was in place. `max_inventory_age_seconds` was raised 30 -> 900 on
this evidence. Caveat: these latencies are times-to-fill for quotes that were
basis-floored, so they describe how long a *winning* position took to come
back. An unfloored quote at the touch should fill faster, not slower, which
makes 900s a conservative bound rather than an optimistic one.

### Not crossing is also wrong

maker-lab set `FORCE_EXIT_BEFORE_START_MIN = 0` because holding measured
~3.13c/contract cheaper than crossing. The residue then reached settlement:

```
unpaired positions settling  -$88.20   (86% of that session's loss)
16 settlements, every one payout = 0.0
```

Neither extreme is right. Current policy: wait 900s for the free passive exit,
then flatten unconditionally 300s before kickoff. Settlement is never allowed.

### liquidity-bot's own exits, 2026-08-13

Four round trips, **every one a taker exit at the 30-second deadline**, passive
0-for-4. Holding times 28s, 31s, 31s, 30s — the timer won every time.

```
entry maker 0.81  ->  exit taker 0.81  ->  realized -$0.01 (the commission)
```

Three of four were price-flat: the loss was purely the crossing fee. The
exit was never a bad price, it was an unnecessary crossing.

### A thin exit book used to end the whole session

Measured 2026-08-14 against `cross_exit`: at one contract with the 0.5 stress
haircut, the hard exit cannot complete whenever **exit-side depth is under
2.0** contracts.

```
exit-side depth   1.0  -> incomplete
                  1.5  -> incomplete
                  2.0  -> completes
```

That is not a corner case. The universe sample in section 5 contains live books
with exit-side depth of exactly `1.0`. Entry is gated on exit depth existing,
but depth can vanish during the 900-second hold.

It used to be terminal: `hard_exit_insufficient_stressed_depth` returned
`critical:`, which exits 4, and the supervisor does **not** restart exit 4. One
thin book on one market ended the session and left the process down until an
operator noticed -- while the other seven markets were healthy.

Now the inventory clock defers instead (`hard_exit_deferred_thin_book`,
recoverable). Refusing to cross into a thin book is still correct; ending the
session over it was not. The pre-kickoff flatten at T-300s remains the real
deadline and still re-prices against displayed depth.

More generally, **every condition that describes one market's inventory or exit
now retires that market rather than stopping the session**: the hard-exit
family, the inventory clock and basis checks, and the exit-confirmation
timeout. A retired market keeps its streams and reconciler so its exit is still
worked, but never quotes again -- not even after reaching flat, and not through
a later scan. Seven healthy markets no longer pay for one bad book.

The bound matters as much as the mechanism. Retirement resets the exit budget,
so a market whose exit can never complete would be replanned forever, including
after kickoff when nothing can be done. Ten failed rounds abandons it, shutdown
makes the final attempt, and more distinct retirements than `max_markets` is
treated as systemic and does stop the session.

## 3. Spread regime

maker-lab markout at 900s, by the spread the fill landed on:

```
under 2c   n=856   -0.00868/contract   <- the only losing regime
2-4c       n=403   +0.00530
4-7c       n=267   +0.01893
7-12c      n=149   +0.00399
12-25c     n=110   +0.05841            <- best
over 25c    n=33   +0.04561
```

`minimum_entry_spread = 0.02` rests on this. It is the best-sampled number in
either project. It is also expensive: it rejects **88% of market-minutes** (see
§6). Wide books are the best fills — do not add a maximum spread.

**Markout is positive at every horizon** (+0.0041 to +0.0078/contract, n=168–197
at -1s/60s/300s/900s). Fills are not adversely selected. This does *not* mean
spread capture is guaranteed revenue: maker-lab's paired markets still netted
**-$15.62**, which is why `spread_capture` stays 0.

## 4. Reward mechanics

Score is `discountFactor ** ticks_from_best x size`, accumulated outward until
`targetSize` is reached. **Spread does not enter the formula** on this venue —
the quadratic-in-spread concern in maker-lab's notes came from the `.com`
scheme and does not apply here.

Reward pays for **resting**, not trading. maker-lab measured 37 of 153 minutes
with nothing on the book — **24.2% of the clock scoring zero**.

Observed programme parameters, 2026-08-13:

```
setka*   pool  $35   df 0.50   target  5,000
itf*     pool  $30   df 0.35   target  7,500
dota2    pool $100   df 0.35   target 15,000
atp      pool $200   df 0.30   target 15,000
```

Period pools on one market: `day_of` $200, `early` $200, `live` $1,600. **The
live period carries 8x the pool** and is the one period the pregame design
forbids.

Only `day_of` can be projected: other periods have no reliable per-market
start/end clock, so the runner can only quote in the **6 hours before start**.

The venue's $1 minimum payout is an **aggregate settlement threshold**, not a
per-market entry requirement. Applying it per side per market was wrong and
was removed.

## 5. Universe shape

Measured 2026-08-13:

```
active liquidityProgram markets      13,707
with a day_of period                  7,443
period mix   live 8,488 | day_of 7,443 | early 4,364
             daily_event 4,474 | tournament_with_cutoff 2,576
```

**Those counts are the whole programme, not this bot's addressable universe.**
Quoting the 13,707 on its own is misleading. Measured 2026-08-14T05:0xZ, the
funnel to what the runner can actually act on:

```
catalog rows                    17,254
active and not closed           17,254
supported moneyline              1,037     -94%  most rewards are spreads/totals/props
still pregame                    1,004
  starting within  6h              218     -78%  only day_of has a projectable clock
  starting within 12h              471
  starting within 24h              659
rewarded (of the 218)              199
after the 2c catalog spread filter  33     -83%  matches the backtest's 88% rejection
```

**The addressable universe is roughly 200 markets at any moment, and 33 after
the spread filter.** The +6-12h bucket held 253, so this is not a dead-of-night
trough: the count stays near this all day as games roll forward. Widening the
six-hour window is not a hidden win either -- it would mean quoting periods
whose reward cannot be projected.

Rewarded pregame **moneylines** do exist — setka, itf, atp, wta, cs2, lol,
dota2 — the same leagues `fill_hazard.py` is calibrated on. An earlier sample
suggesting otherwise (40/40 futures) was biased by pagination order.

Books are **wildly one-sided**: `bid 23,388 / ask 1.0`, `bid 31,168 / ask 17`.
Since the gate scores each side independently, a thin side on a deep book is a
real opportunity — but the *opposite* side is where you must exit, so a thin
side usually means a hopeless passive exit.

Discovery cost, measured: per-slug metadata for 7,443 markets is **31 minutes**
at 4 req/s. The bulk catalog join is ~39 requests, about 10 seconds.

## 6. Backtest — liquidity-bot's gate over maker-lab's books

Replay of the exact live gate over 2026-08-11/12, 9,306 in-window
market-minutes across 712 markets. Programme parameters assumed from §4
(historical incentive rows are a single later snapshot), everything else
measured.

```
market-minutes sampled          9,306
  spread >= 2c                  1,077   (11.6%)
  reward-eligible sides           691
  ECONOMICALLY VIABLE               1   (0.14%)

best gate_ratio    1.2389        median   0.0034
```

**One authorized quote in 24 hours.** It ran 749 seconds on an ATP market
($200 pool, 20 contracts at the touch), never filled, and accrued **$0.032**.

Rejection causes: `spread_below_2c` 8,229, `target_not_met` 1,463,
`reward_below_weighted_exit_cost` 690.

`target_not_met` is a trap worth naming: a book too thin to reach `targetSize`
earns **nothing at all**, so "thinner is better" has a floor. Too deep destroys
your share; too thin and the programme never activates.

Authorized uptime: **749s of 86,400 = 0.87%.** The reward *rate* was fine —
$0.154/hour, ~$3.70/day if authorized continuously. Uptime is the constraint.

### Quote size

Sweep over the same books:

```
size    authorized market-minutes
   1                            1
   2                            0
   5                            0
  10                            0
  25                            0
```

But on a book measured directly, **reward scales linearly with size** (25x size
-> 24.4x reward) and unwind scales linearly too, so `gate_ratio` is nearly
size-invariant (0.0310 -> 0.0305 across 25x). Size changes **how much you earn**
on a qualifying book, not **whether** it qualifies. The single qualifying book
passed at 1.24, too thin a margin to survive any increase.

## 6b. The gate, not the search, is what rejects everything

First live census, 2026-08-14T04:57Z, read-only over the whole universe:

```
executable 212 | rewarded 199 | candidate books 33 | viable 0
coverage_complete: true          best_gate_ratio: 0.392
```

**Only 33 markets are candidates at all.** The `day_of` period filter and the
catalog spread filter remove the rest, so the runner's 25-book window is not
leaving a large unexamined pool behind it -- the census fetched all 33 and
found nothing viable. Widening the search is not the lever.

Where the rejections actually land, per side:

```
projected_reward_does_not_cover_probability_weighted_exit_cost   62
spread_below_entry_minimum                                        2
target_not_met                                                    2
```

**94% fail the economic gate itself.** The best side anywhere in the universe
scored `gate_ratio` 0.392 against the 1.0 needed, so nothing was close.

This puts the weight on open question 3. `reward_projection_haircut` 0.5 and
`reward_cost_safety_multiple` 2 multiply to a **4x** margin, and neither number
has any evidence behind it. At 0.392 the best market clears comfortably without
that 4x and clears nothing with it. Before changing either, sample `gate_ratio`
across many passes and measure how often a market would clear at 3x, 2x and 1x
-- the census does this read-only and at no risk.

Touch depth across the same sample: median 141 contracts, minimum 0.02. Depth
is not the scarce thing; the margin is.

## 6c. First stable multi-market run, 2026-08-14 05:15-10:58Z

**5h43m on a single process, no restarts.** Every prior attempt died in 22-27
seconds.

```
PLACE 1,817 | CANCEL 1,817 | all CANCELED | unresolved 0
fills 0 | positions 0 | markets quoted 16 | retirements 0
```

The place -> cancel -> reconcile cycle that deadlocked the ledger on its very
first attempt now completes 1,817 times with nothing stuck. That question is
closed.

### Reward at one contract is arithmetically negligible

Measured programme parameters on the markets actually quoted:

```
day_of   pool $35   discount 0.5   targetSize 5,000
1 contract at the touch = 0.020% of target = $0.007 for the whole 6h period
```

The $1 payout floor is **aggregate across all markets and it accumulates**, so
the number that matters is the accrual rate, not any single market. Eight slots
running continuously keep covering new six-hour windows as games approach:

```
$0.007 per market per 6h period  =  $0.001167 per market-hour
8 slots x 24h                    =  192 market-hours/day
                                 =  $0.224/day  ->  $1 every 4-5 days
```

Venue earnings after 5.7 hours: **$0.00**, consistent with the arithmetic
rather than a defect.

Two things make $0.224/day a **ceiling, not an expectation**. It assumes 100%
resting uptime, and this run had `viable=0` for long stretches plus 1,817
place/cancel cycles. It also assumes a clean 1/5,000 score share: score
accumulates outward from the touch until `targetSize` is reached, so on a deep
book the target is filled by orders at or inside our price and we earn nothing
at all for that period.

**This is the scale problem, not a bug.** The machinery works; one contract
earns on the order of a dollar every few days at best. Note that section 6 measured `gate_ratio` as nearly size-invariant
(0.0310 -> 0.0305 across 25x), so size changes what a qualifying book pays
without changing whether it qualifies.

### A cancelled order is briefly unreadable

246 `GET /v1/order/{id}` 404s in 5.7 hours. Every id that 404'd returned a
proper terminal state when retried later, so the window is eventual
consistency, not absence.

It was expensive. The generic retry in `_call` covers only HTTP 429, so a 404
left the CANCEL intent unresolved, `risk_state_known()` went false, and the
runner treated it as `execution_state_unknown`: **539 strategy-state
recoveries, each quarantining every working order and cancelling resting
quotes.** Reward pays for resting time, so this churn attacked the only thing
that earns. `retrieve` now waits the window out, bounded at four attempts --
which assumes nothing about the order's state, unlike reading absence as
cancellation.

## 6d. Rewards are real and material -- correcting section 6c

Queried account-wide from the venue 2026-08-14 (`GET /v1/incentives/earnings`,
no market filter). **This account has earned $70.92 in liquidity rewards.**

```
PAID $32.06 | PENDING $38.80 | SKIPPED $0.06

2026-07-12   $0.01
2026-07-16   $0.05
2026-08-10  $32.06
2026-08-11   $5.39
2026-08-12  $33.41
```

166 distinct markets earned. Best single markets: **$5.38, $4.85, $4.02,
$3.22, $2.95**.

**Section 6c's arithmetic was right and its conclusion was wrong.** $0.007 per
market per period is correct *for one contract*, and I presented it as the
strategy's ceiling while this same account had already earned $33 in a day. The
error was only ever querying earnings per market for the markets bound right
now, never account-wide. Always query account-wide.

Three differences compound, and size is the smallest of them:

```
                maker-lab (earning)      liquidity-bot now
quote size      ~10-25 contracts         1
markets         166                      capped at 8
period          included live ($1,600)   pregame only (day_of, $200)
```

Roughly 15x x 20x x 8x. **Breadth and period dominate size.**

### The failure was settlement, not the reward mechanism

2026-08-12: rewards **+$33.41**, session net about **-$102**, of which
**-$88.20 was settlement** (section 2). Rewards were never the problem. The
residue reaching settlement was, and the 300-second pre-kickoff flatten
(invariant 6) is the fix for exactly that leak.

That reframes the open question. It is not "can this strategy earn" -- measured
yes, $32-33 on a good day. It is whether it can earn without giving it back at
settlement, which has never been tested with the flatten in place.

### Live versus pregame is UNRESOLVED, and fills cannot resolve it

The `live` period carries the 8x pool and the pregame design forbids it
(section 4), so this split decides whether scaling the current design is worth
doing. **The venue does not expose it.** `GET /v1/incentives/earnings` accepts
only `start_date`, `end_date`, `market_slug`, `program_type` and returns only
`reward`, `programType`, `marketSlug`, `date`, `status` -- confirmed against
the published reference on 2026-08-14. There is no period field to fetch.

An earlier note here claimed "two thirds pregame" from maker-lab's aggregate
fill timing. **That was wrong and is retracted.** Attributing all 166 earning
markets by fill timing:

```
                                              amount   share
no fills at all (pure resting)                $34.68     49%
fills only AFTER game start (live)            $28.40     40%
fills only BEFORE game start (pregame)         $3.42      5%
fills on both sides                            $4.36      6%
                                              ------
                                              $70.86
```

**Fills are a bad proxy for reward attribution**: half the money came from
markets that never filled, because reward pays for resting, not trading. The
aggregate pregame share was high only because pregame fills clustered on
markets that earned little; the largest earners ($5.38, $4.85, $4.02, $3.22)
had *zero* pregame fills.

What can be said: at most 5-11% is clearly pregame, 40% is clearly
live-associated, and 49% is unattributable by this method. That leans against
a pregame-only executor reaching these numbers, and it does not settle it.

Settling it needs resting time per period, which neither the venue nor
maker-lab's schema records. The practical answer is to run the pregame
executor at size and measure what it earns.

Raw data: `data/venue_reward_earnings_2026-08-14.csv`, 168 rows, the full
per-market per-day breakdown behind every number above.

### Order of work this implies

1. Fix the place/cancel churn. Reward pays for resting time and the runner is
   currently cancelling its own quotes about twice a second, so any size
   increase multiplies risk without multiplying reward.
2. Breadth before size. Breadth is the larger lever (20x against 15x) and the
   safer one: more markets at one contract concentrates no more risk per
   market. Connections are independent and each carries its own
   10-subscription quota (section 7), so 16 markets is two connections.
3. Size last, bounded by capital. Exposure is roughly `size x markets` dollars
   at two sides near $0.50. Against sub-$100 capital that is size 6 at eight
   markets, or size 3 at sixteen. Breadth and size trade against each other.

## 6e. The place/cancel churn: our own quote makes the side uneconomic

1,817 place/cancel cycles in 5.7 hours, all at the same price, roughly two per
second -- the loop tick. Reward pays for resting time, so this attacked the
only thing that earns. Diagnosed 2026-08-14 by logging the plan action, its
reason, and the per-side economics; none of it was recoverable from the ledger,
which records that an order was cancelled but never why.

The cancel reason is **`side_no_longer_desired`**: the side leaves
`desired_sides` the instant our order becomes visible, and re-qualifies about
250ms later once it is gone.

```
CANCEL          side_no_longer_desired      working=1
PLACE_POST_ONLY reward_positive_candidate   price=0.74  working=0
CANCEL          side_no_longer_desired      working=1
PLACE_POST_ONLY reward_positive_candidate   price=0.74  working=0
```

The mechanism is in `rewards.py`:

```python
if not order_already_resting:
    depth[order_price] = depth.get(order_price, 0.0) + size
```

Eligibility depends on cumulative depth reaching `targetSize`. Flat, our size
is added; resting, it is not, because it is assumed already in the streamed
book. **So our own contract decides eligibility whenever the book sits on that
boundary**, and the side oscillates once per pass.

Measured margin at the moment of a place: `net={'offer': 0.002705}` -- three
tenths of a cent. At that thinness any asymmetry between the flat and resting
calculations flips the sign, so this is not a rare edge.

### Fixed and confirmed live

`rewards.py` now counts our size in both cases. The public book does contain a
resting order of ours, so skipping it avoids double-counting in principle --
but the book is only eventually consistent, and the window before it catches up
is exactly the window the runner evaluates in, leaving depth short by our own
size. Counting it always makes "worth placing?" and "worth keeping?" answer the
same question. The residual error is over-counting one contract against a
targetSize in the thousands, which biases toward leaving a quote resting: the
safe direction, because resting is what earns.

Measured before and after on the same runner:

```
before   place -> cancel -> place at the loop tick, ~2 per second
after    a quote rested 4 minutes (12:19:27 -> 12:23:25) before any cancel
```

and the cancel that ended it was legitimate -- the bid's blocker had genuinely
become `projected_reward_does_not_cover_probability_weighted_exit_cost`, the
economics had moved, rather than the eligibility flipping on our own presence.

The reproduction lives in `test_eligibility_does_not_flip_on_our_own_resting_order`:
nine resting against a target of ten, our one contract deciding it.

## 6f. First fill, 2026-08-14T11:48:39Z

```
SELL 0.5200 @ 0.7400   fee 0.0000 (maker)   intent ORDER_INTENT_BUY_SHORT
position -0.52 on aec-wtt-manwan-yuxqin-2026-08-14
```

**Partial**: 0.52 of a 1-contract quote. Worth noting because the exit path had
only ever been reasoned about for whole contracts.

Fee was **0.0000** -- consistent with section 1, where the maker rebate at this
size rounds to zero rather than paying.

The reducing bid then chased the touch (0.55, 0.56, 0.68, cancel/replace each
pass), which is `replace_reducing_at_active_touch` rather than the eligibility
oscillation above -- a second, distinct source of churn on the inventory path.

### The exit machinery works, first end-to-end proof

```
11:48:39.081  FILL       SELL 0.52 @ 0.74   MAKER  fee 0.0000
11:48-12:03   passive reducer chases the touch, never fills
12:03:39.300  CANCEL     hard_timeout_cancel_confirm_first
12:03:39.436  PLACE_IOC  hard_inventory_timeout  bid @ 0.85
12:03:39.604  FILL       BUY  0.52 @ 0.85   TAKER  fee 0.0000
              position FLAT, unresolved 0
```

The 900-second clock fired to the second (11:48:39 + 900s), cancelled and
confirmed **before** crossing as invariant 6 requires, and cleared the
position. Nothing stuck.

```
ROUND TRIP P&L  -$0.0572
```

**The loss is entirely adverse price movement, not cost.** Both legs charged
zero: the maker rebate rounds down as section 1 predicts, and the taker fee did
too -- `0.06 x 0.52 x 0.85 x 0.15 = $0.004`, rounded to zero. Section 1 measured
that fee rounding *up* at size 1 and p=0.81, so rounding cuts both ways
depending on size and price.

The market moved 11 cents against us across a 15-minute hold. That is the cost
of the 900-second window, and it is the risk maker-lab's positive 900s markout
said was acceptable. **n = 1**: one sample decides nothing, and it is the first
real evidence either way.

## 6g. One market's in-flight mutation blocks all eight

Diagnosed 2026-08-14, **not fixed**, needs an explicit decision.

`_frame` sets `state_known` from

```python
state_known = self.gate.ready(self.ledger, now=now) and self.ledger.risk_state_known()
```

and `risk_state_known()` begins `if self.unresolved_intents(): return False` --
**account-wide**. So an unresolved intent in *any* market makes *every* market
report `execution_state_unknown`, which the runner recovers from by
quarantining. With eight markets placing and cancelling there is nearly always
a mutation in flight somewhere, so the markets interfere with each other
continuously. At one market this was invisible; at eight it serializes the bot.

The log shows the cross-market shape plainly -- a recovery in one market
failing on another market's cancel:

```
strategy_state_recovery:...vorvol-yurart  attempt=10
    "runtime recovery cancel is not yet reconciled in ...ivaana-buiedu"
```

Measured over 40 minutes after the churn fix:

```
recovery events          37
execution_state_unknown  25
actual plan actions      14
markets affected          2 of 8
```

Recovery outnumbers real work 2.6 to 1, about one spurious unknown-state every
96 seconds.

**Sampled directly, it is far worse than that ratio suggests.** Polling the
three inputs to `risk_state_known()` once a second for a minute:

```
state KNOWN    39/60 samples
state UNKNOWN  21/60 samples          -> 35% of wall-clock time
during unknown: unresolved=1, unknown_absent=1
```

That pairing is the **ordinary cancel lifecycle**, not an anomaly: a cancel is
issued so its intent is unresolved, the order stops appearing in the open-order
snapshot so it is marked `UNKNOWN_ABSENT`, and both clear once `retrieve`
confirms the terminal state. `risk_state_known()` fails on either condition and
both are account-wide, so **every routine cancel blanks quoting for all eight
markets, about a third of the time.**

This is why placements do not survive. The observable signature is a run of
`PLACE_POST_ONLY` at the same price with `working=0` and *no* `CANCEL` in the
action log -- the cancels come from `_quarantine_working_orders`, which acts
directly and never goes through the planner, so it leaves no action record.

Safety is unaffected throughout: zero open orders at the venue, position flat,
no accumulation. The cost is opportunity, and reward pays for resting time.

### Fixed, in maker-lab's style

maker-lab already solved this and we did not copy it. Its quarantine is scoped
to **one side of one market**, and the loop then carries on:

```python
_quarantine_other_side(run_id, slug, side, ...)
    "**This side is durably blocked**"
stats["failed"] += 1
continue                      # skip this side, keep every other one running
```

liquidity-bot instead blanked the whole account on any unprovable state. That
is the difference between 201 fills and $70.92 of rewards on one side, and 2
fills on the other.

The fix scopes the unresolved-intent test to the market being planned and keeps
the ownership tests account-wide:

```
per market   unresolved intents for that slug
account-wide UNKNOWN_ABSENT orders, venue:-prefixed working orders
```

The durable-intent design is per order and per market, so what we know about
market B genuinely does not change because a cancel is in flight in market A.
Ownership violations are different and must stay global -- a `venue:` order
anywhere means something else is trading the account.

The risk is that this widens when the reconciler is willing to build new risk,
which is a safety property, so it wants review rather than a quiet deploy.

## 6h. Where the reward actually comes from: thin books

Breaking the $70.92 down by league, from
`data/venue_reward_earnings_2026-08-14.csv`:

```
setkameua   $20.59   45 market-days   avg $0.458   29.0%
setkamemd   $15.11   14               avg $1.079   21.3%   best per market
itfme       $12.94   35               avg $0.370   18.2%
itfwo        $8.63   31               avg $0.278   12.2%
wtt          $5.27   16               avg $0.329
valorant     $2.48    4               avg $0.620
setkamecz    $2.28    8 | cs2 $2.24 10 | setkawoua $1.36 3
atp          $0.01    1
mlb          $0.01    1
```

**Table tennis and lower-tier tennis are 93% of everything paid.** `atp` and
`mlb` -- deep, liquid, heavily quoted books -- paid **one cent each**.

That is the reward formula, not luck. Share is our score over the competing
score up to `targetSize`, so a thin book with little competing maker size makes
one contract a real fraction, and a deep book makes it a rounding error. It
also caps the size lever: past some point our own size eats the share it is
trying to buy.

**liquidity-bot is already hunting in the right place.** The 25 markets it
quoted on 2026-08-14 were `setkamemd`, `setkameua`, `itfwo`, `itfme` and `wtt`
-- exactly the leagues that produced the 93%.

### The ranking prior disagreed with the payouts

`_candidate_key` sorts on `historical_reward_yield_prior`, which was built from
2-28 shadow observations per league. Against 168 market-days of actual payout,
after the existing shrinkage:

```
league       effective prior  rank    measured rank
cs2                   0.0608     1                9    <- last
setkamecz             0.0406     2                7
wtt                   0.0396     3                6
valorant              0.0372     4                2
setkameua             0.0366     5                3
setkamemd             0.0355     6                1    <- best
```

`cs2` ranked first on a sample of two and measured last of nine; `setkamemd`,
the best earner, ranked sixth. Recalibrated from the payout data, normalised so
the corpus mean matches the existing global constant.

**Units caveat, deliberately tolerated**: the new numbers are reward per
market-day, not per contract-hour, because we cannot recover how long or how
much maker-lab rested in each market. The value is a ranking key only, so
relative order is what matters. Replace it with true contract-hours if resting
time is ever recorded.

## 6i. The venue inverts the price on SELL intents

Confirmed live 2026-08-14. Holding a short of 5 entered at **0.36**, the
reducing quote was sent as `ORDER_INTENT_SELL_SHORT` at **0.36** and the venue
rested it at **0.64**, shown in the app as `64c 0/5 filled`.

```
0.64 = 1 - 0.36
```

At that price the order **adds to the short instead of closing it**. The
position can never flatten, and every reducing quote is a new position in
disguise.

maker-lab hit this and documented it in `live_orders.to_intent`: *"the exchange
read our 0.29 as the YES price and charged us the NO side of it"* -- about 41
cents per contract given away on the one fill it got, after *"three wrong
diagnoses before the arithmetic gave it away"*. Their note also warns that
`preview` does not catch it: it echoes which OUTCOME an order takes and says
nothing about which FRAME its price is in.

**Their resolution, and now ours: two intents, never four.**

```python
bid   -> ORDER_INTENT_BUY_LONG     price unchanged
offer -> ORDER_INTENT_BUY_SHORT    price unchanged
```

`SELL_SHORT` and `SELL_LONG` are exactly the two intents maker-lab never sends,
and they are the two whose price convention was never validated here. The venue
nets a BUY on the opposite outcome against an open position, so reduction needs
no separate intent -- maker-lab ran this for days across 201 fills and 101
exits.

The `reducing` flag is retained in `OrderRequest` and deliberately ignored when
choosing the intent. It still governs post-only, time in force, and the
exact-position invariants.

**Note the earlier round trip that appeared to work.** On 2026-08-14 a short of
0.52 entered at 0.74 was closed by `SELL_SHORT` at 0.85 and the position did go
flat. Under inversion that order rested at 0.15, not 0.85 -- deep enough
through the bid to fill immediately and close the position, which is why it
looked correct. It closed at a far worse price than the books recorded, the
same way maker-lab's `+$0.02 close` was really nearer `-$0.82`.

## 7. API limits

**Private WebSocket: 10 subscriptions per connection.** Measured by adding one
at a time; number 11 returns `max subscriptions per connection reached`. The
subscribe call itself does *not* raise — the error arrives asynchronously on
the socket, so a naive client believes it succeeded.

This constrains multi-market directly:

```
1 x orders    all slugs      (order events always carry marketSlug)
N x positions per-market     (positionSubscription OMITS marketSlug)
1 x balance   account-wide
-------------------------------------------------
N + 2 <= 10   ->   N <= 8 markets per connection
```

`PrivateReadOnlyStream.start` refuses above 10 rather than let the venue drop
the overflow silently. Positions must be scoped one market per subscription,
because the projector may only infer an omitted slug when the request covers
exactly one market. A single
multi-slug positions subscription re-creates the defect that ended the
2026-08-13T05:39Z session on its first fill.

**Public market data has no such problem.** Measured 2026-08-14: one
`subscribe_market_data` call carrying 8 slugs was accepted and delivered books
for **8 of 8** within 12 seconds, zero errors. The public side needs one
subscription regardless of market count; only the private side is constrained.

**REST budget** on the shared 4 req/s governor:

```
account-wide      ~0.40 req/s   (balance 1/5s, reconcile 2/10s)
per market        ~0.033 req/s  with an active WebSocket book
per market        ~0.333 req/s  when the book goes quiet and falls back to REST
```

Worst case (every book quiet) supports ~10 markets. REST is not the binding
constraint; the WebSocket subscription cap is.

**The cap is per connection, and connections are independent.** Measured
2026-08-14: `ws.private()` returns a fresh socket object per call, and two
sockets each held 10 subscriptions simultaneously with zero errors. Two
consequences:

- A reconnect gets its own quota, so recovery never contends with the closing
  connection's subscriptions. Eight markets at exactly 10 subscriptions needs
  no spare.
- More than 8 markets is reachable with additional connections, at the cost of
  recovering, reconciling and freshness-gating each socket separately.

## 8. Accounting

**Unresolved.** maker-lab carries a **$40.09** balance-versus-ledger gap that
was never closed. liquidity-bot's 2026-08-13 trades include an unexplained
**+$0.64** on a shared account, where the venue computed realized P&L against a
cost basis that was not from that round trip.

This does not block a one-contract safety test. It **does** block any
profitability claim or scaling decision.

---

## Open questions

1. **Does a book exist that clears the gate with margin?** The one that did
   passed at 1.24. Everything about size and market count depends on the answer.
   `reward_census.py` measures it read-only; it has not been run in anger.
2. **Can a passive exit fill inside a usable window on *our* books?** maker-lab
   says 46% by 900s on theirs. liquidity-bot is 0-for-4 at 30s. The 900s change
   is untested live.
3. **Is the 50% reward haircut and the 2x cost margin justified?** Neither has
   any evidence. Together they are 4x. **Now the binding constraint**: the
   2026-08-14 census measured 94% of all sides in the universe failing on this
   one test, with the best at `gate_ratio` 0.392 -- which clears 1.0 without the
   4x and clears nothing with it. See section 6b.
4. ~~Does the venue accept multiple private connections?~~ **Answered**
   2026-08-14: yes, and each connection carries its own 10-subscription
   quota. See §7.
5. ~~Should the reducing quote rest at the touch or at break-even basis?~~
   **Answered** 2026-08-14: the touch, no floor. A basis floor sorts winners
   into passive exits and strands losers until they settle. See §2.

## Session-lifetime counters cannot bound a churning system

Measured 2026-08-16. Markets are consumed continuously: a game kicks off, its
book goes quiet, `book_stale` exhausts `MAX_RECOVERY_ATTEMPTS`, and the market
retires permanently. That is correct behaviour — a live game must not be quoted
— but it means **retirement is the normal end of a market's life, not an
exception**. Observed rate: 31 markets over 7h09m, ~4.4/hour, 30 of them
`unrecoverable_book_stale`.

Any health bound measured against a cumulative session total therefore fires on
longevity rather than on sickness, and fires *later* the healthier the session
is — the opposite of what a guard should do. Bounds on this system must measure
a **rate or a burst inside a window**, never a lifetime count.

Corollary, and the reason a window alone is not sufficient: sports markets kick
off in **synchronized slates**, so a whole slate of markets can retire within
minutes of each other with nothing wrong. Any burst detector must exclude
markets whose `game_start` has passed, or the morning slate reads as a cascade.

## `book_stale` is self-perpetuating, and REST contention is not the cause

Measured 2026-08-16 across a 7h09m session, 7,535 `book_stale` events.

The obvious theory was REST starvation: the refresh trigger is 3s
(`live_commissioning.py:1506`), the stale threshold is 5s
(`reconciler.py:258`), and 20 markets needing a refresh every 3s is 6.67 req/s
against a hard 4 req/s cap. **That theory is wrong.** Tested against the data:

```
hour | markets quoting | book_stale | per market
 00  |      19         |    411     |    21
 02  |       8         |    395     |    49
 04  |       6         |    251     |    41
 05  |       6         |    290     |    48
```

Staleness is flat regardless of load, and per-market staleness *rises* as
markets drop. Contention would show the opposite. The market stream was also
healthy the whole session -- all 10 `component=market_stream` errors in the log
are from 2026-08-14 -- and zero books were dropped.

**The actual mechanism is the recovery path feeding itself.** `plan()` returns
`book_stale` BEFORE anything else, then `_handle_strategy_critical` refreshes
the book and returns, and the caller applies `_recovery_delay` -- exponential
backoff to a 30s cap. So the refresh is immediately followed by a wait longer
than the 5s freshness budget it just satisfied. Measured interval between
consecutive `book_stale` events on one market: **34-75s, clustered at 45-60s.**
Every visit fails, refreshes, backs off, and expires the refresh before the
next visit. The refresh is always wasted work.

This burns exactly the quiet markets -- which are the ones with the least
competition for reward share, since reward is paid for RESTING and not for
trading. The 5-second budget assumes a continuously-updating book; an illiquid
market legitimately has nothing to send, and being quiet is not being wrong.

**Fixed** by replanning once on the refreshed book before conceding the
backoff, bounded to a single extra attempt per sweep so a book that never
arrives still yields. The success path then clears `strategy_state:{slug}`, so
a market that recovers stops marching toward MAX_RECOVERY_ATTEMPTS.

## Crossing at 900s vs at the pre-kickoff flatten: no case for waiting

Measured 2026-08-16 against the live ledger (92 fills, 29 reconstructed round
trips). P&L is computed in the venue's own `side` frame -- SELL proceeds minus
BUY costs on a matched pair -- which is unambiguous and avoids the intent
inversion that produced three wrong diagnoses earlier.

```
                                  n   total    per-contract median   winners
passive exits                     9   +$1.05          +0.0380          7/9
taker exits                      20   -$4.76          -0.0740          4/20
  taker <900s (pre-kickoff only)  8   -$2.51          -0.0750          2/8
  taker >=900s (inventory clock) 12   -$2.25          -0.0590          2/12
```

Sub-900s taker exits can only be the pre-kickoff flatten, because the inventory
clock has not fired yet -- that is what makes the split possible without book
snapshots we do not have.

**The inventory-clock crossings were slightly BETTER per contract than the
pre-kickoff ones** (-0.059 vs -0.075). That is the opposite direction from
"holding is cheaper", and it is consistent with the adverse-selection argument:
what survives to the kickoff deadline is the inventory the market moved away
from, exiting into thinner pre-game books.

**This does not settle it.** n=8 against n=12 with per-contract variance far
larger than the 1.6c difference, and the two groups are confounded -- the
sub-900s group is near-kickoff *by construction*, so it faces worse books for
reasons other than timing. The honest reading is: **no evidence supports
waiting past 900s, and weak evidence says it costs.** Keep the current policy.

Caveat on the passive row, repeating section 2: which exits resolve passively
is partly construction, not policy. Do not read +0.038 vs -0.074 as the gain
from a policy change -- read it as why resting time is worth protecting.

## Reward is concentrated in the hours before kickoff

Measured 2026-08-16 across 48 live candidate markets, using the selector's own
projection (`projected_reward` summed over eligible sides).

```
mins to start     n    median reward    share paying > 0
15-30            11       0.000313            100%
30-60             5       0.000327            100%
60-120           10       0.000204             90%
120+             21       0.000022             67%
```

The 15–30 minute bucket pays roughly **14x** the median of the 120+ bucket, and
a third of markets beyond two hours pay nothing at all. (A 0–15 bucket existed
with a much higher value but n=1, so it is not read here.)

**This settles the entry-gate question against widening.** Stopping quotes at
T-15 or T-30 would surrender the best-paying window to avoid fills that cost
about a cent per contract. The T-300s gate is the right size: it removes only
fills that were guaranteed to be force-crossed (BUGFIXES, entry/exit
incoherence) and gives up almost no reward.

**Caveat, and it matters.** This is *projected* reward at a single snapshot,
not realized earnings, and **every market passes through every bucket** as its
game approaches — today's 120+ market becomes a 30-minute market later. So the
claim is NOT "distant markets are worse to hold". It is "reward is available in
the hours before kickoff and largely absent before that", most plausibly
because distant markets have thin or absent books to score against.

**What it implies for effort.** Near kickoff is simultaneously the best-paying
and the most adversely-selected window, so the answer is to keep resting there
and manage fills, not to avoid the window. Combined with the scale of the two
sides — reward ~$25/day against trading ~-$1.2/day, so losses are about 5% of
reward — effort spent increasing RESTING TIME is worth more than effort spent
improving fill quality.

## The `book_stale` replan works, and the event COUNT is the wrong metric

Measured 2026-08-16, comparing the pre-fix session (00:18-07:27) against the
post-fix one on `bae9f41`.

```
escalation depth on book_stale

NEW (65 min)              OLD (7h09m)
  attempt=1   2350          attempt=5    80
  attempt=2     11          attempt=6    56
  attempt=3     20          attempt=7    51
  attempt=7      1          attempt=8    43
                            attempt=9    39
  max reached: 7            attempt=10   37
  retirements: 0            attempt=11   34
                            attempt=12   32  <- permanent retirement
```

**99% of staleness now resolves on the first attempt**, and nothing reaches
`MAX_RECOVERY_ATTEMPTS`. Pre-fix, markets climbed the full ladder and hit 12
thirty-two times, each one a permanent `unrecoverable_book_stale` retirement --
the mechanism that burned twenty slots in forty minutes and ended the session
on `market_retirement_limit_exceeded`.

**The raw event count went UP, from ~400/hour to ~2,190/hour, and that is not
a regression.** The fix removes a backoff that escalated to 30 seconds, so a
market that previously failed once per 45 seconds now re-checks promptly. More
events, each resolving. Counting `book_stale` lines measures how often we LOOK,
not how often we are broken.

**Use escalation depth, not event count.** `attempt=N` is the metric that maps
to harm, because retirement is what costs a slot and only sustained failure
reaches it.

**Not fully closed:** the alternative reading is that this session's books are
genuinely quieter and the fix is being credited for it. Distinguishing them
needs the resting-fraction data from the summary work -- if markets rest a
healthy share of wall-clock time, the churn is benign.

## The same predicate, asked scoped and unscoped, is a place bugs hide

Measured 2026-08-16 from a live 36-minute stall in which the bot quoted
nothing while looking completely healthy.

`risk_state_known(market_slug)` applies its `UNKNOWN_ABSENT` test **only when a
slug is passed**. That scoping was itself a fix (§6g): account-wide, one
market's ordinary in-flight cancel blanked all the others and every market
quarantined its resting quote.

But the scoping only helps callers that pass a slug. `_frame` does.
`_wait_for_initial_private_state` does not -- it asks `gate.ready()`
account-wide, which is defensible for a fresh bind. So a single absent order
in a market that had *already been released* was simultaneously:

- harmless to every per-market decision, and
- fatal to every stream bind in the process.

Both readings were true at once, which is exactly why the wrong one survived
scrutiny. The absent order was explicitly considered and dismissed early --
"that test is scoped, and that market is gone" -- and the dismissal was correct
about the scoped callers and wrong about the whole system.

**The general lesson:** when a predicate takes an optional narrowing argument,
its behaviour is really two predicates, and safety reasoning has to name which
one it is talking about. Enumerate the call sites of both forms before
concluding a state is contained. Here: `grep -n "risk_state_known(" src/` is a
five-second check that would have redirected the first hour of diagnosis.

**Corollary, on retries.** The same stall retried its failed operation four
times and failed identically each time. A retry that keeps failing is evidence
the fault is *below* the thing retrying -- not evidence that retry is the fix.
Shipping the recovery alone would have converted a silent hang into a loud
infinite loop and looked like progress. See BUGFIXES 2026-08-16.

## A manual close leaves a one-sided P&L record that eats the loss stops

Measured 2026-08-16 on `aec-setkawoua-melolh-kololh-2026-08-16` (Melnyk Olha),
after the operator recalled closing that position by hand during the previous
session.

What the ledger holds for that market:

```
fills:      1  BUY 5 @ 0.28, MAKER, 14:20:16Z   <- the entry
            (no sell fill, ever)
orders:    35 CANCELED, 1 FILLED, 1 REJECTED
position:  net_qty 0, cost 1.39
realized:  -1.39
```

The bot bought 5 at 0.28 and then walked a sell ladder down -- 0.43, 0.30,
0.29, 0.28, 0.27, 0.26, 0.25, 0.24, 0.21, 0.20, 0.19, 0.18 (rejected) -- and
**not one of those sells ever filled**. The position is nevertheless flat. It
was closed by hand at the venue.

**The exit is invisible to the ledger.** `fetch_executions` back-fills from
`portfolio.activities`, and the manual close carried no bot client-order-id to
attribute it to. So the entry is recorded, the exit is not, and the market
reads **-1.39 realized against a position that is actually closed**.

**Why that is not cosmetic:** `realized_by_market()` and `realized_pnl()` are
what drive the $2 per-market stop, the $5 session stop and the $15 hard stop.
A phantom -1.39 consumes 70% of that market's stop and 28% of the session stop
before a single real loss occurs. Loss stops are the only automated protection
on live capital, and this makes them fire early on money that was never lost.

This is a **second, independent cause** of the one-sided-fill defect already
recorded as NEXT.md's top priority. That one is about IOC exits never reaching
`execution_fills`; this one needs no bug at all -- a human closing a position in
the UI produces it by construction, and always will.

**Operational rule until the ledger can attribute unowned executions:** closing
a bot position by hand is not free. It is safe for the *account* -- the bot
re-reads positions from REST and stays flat -- but it silently debits the loss
stops. Prefer stopping the bot, or expect the stops to trip early and know why.

Worth noting what did NOT happen: no `venue:` order was ever recorded, in the
whole ledger. Manually *cancelling* or *closing* stays invisible to the
ownership test. Manually *placing* would not -- that trips
`ownership_is_known()` and is a terminal safety event that stops every market
at once, by design.

## Crossed exits are where the money goes, and the 900s clock pulls the trigger

Measured 2026-08-16 across the whole ledger, splitting every market by how its
exit filled.

```
exit was TAKER (crossed):   28 markets   net  -30.22
exit was MAKER only:        11 markets   net   -9.67
NO exit fill recorded:      12 markets   net  -22.41
                                        TOTAL  -62.30
```

**Taker exits are ~76% of real recorded losses.** Entries are almost entirely
MAKER; the damage is on the way out. Individual crosses:

```
bought 0.92 -> crossed out 0.79   (-13c/contract)
sold   0.65 -> bought back 0.96   (-31c/contract)
sold   0.36 -> bought back 0.46   (-10c/contract)
```

Hold time from entry to last exit, for markets that lost more than $1, clusters
exactly where you would expect if a timer were forcing the exit:

```
911s   -4.20   kundmy-tsokos
966s   -3.60   bornac-servas
```

`max_inventory_age_seconds` is **900**. Those are not positions that failed to
find a passive exit for their own reasons -- they are positions the clock
crossed out.

**The guard that should have stopped the expensive ones cannot fire.**
`_inventory_plan` refuses a cross costing more than `max_exit_loss` while
outside the kickoff window, and the comment is explicit that "before the
kickoff deadline a too-expensive exit can wait for a better book". But
`max_exit_loss` is wired to `max_market_loss_usd` = **$40**, the session stop.
Five contracts can lose at most about $5, so `pnl < -40` is unreachable and
every timeout crosses at whatever price the market has moved to.

**That number is doing two unrelated jobs.** `reconciler.py` uses it at the
exit (the cross cap) AND in `_flat_plan` (the entry gate: do not open a side
whose stressed close would cost more than this). Retuning it for the exit would
tighten entry by ~50x. It has to be split, exactly as the loss stops and the
exposure budgets were split for the same reason -- "stop after losing $40" and
"never pay more than $40 to leave one position" are different statements.

The `-22.41` with no exit fill recorded is not trading loss; it is the
one-sided-record defect (manual closes, IOC exits that never reach
`execution_fills`).

**Fixed so far:** exits now rest one tick inside the touch while still at or
above entry, to raise passive fill probability and reach the clock less often.
The `max_exit_loss` split is identified but NOT yet done.

## Re-pricing costs ~70 seconds of not resting, and that is where coverage goes

Measured 2026-08-16T21:47-21:56Z, first clean session after the day's fixes.

The Discord summary read **8 of 20 markets resting**, with idle reasons
`no_quote_yet 6` and `target_not_met 6`. `target_not_met` is the reward gate
declining correctly -- the venue pays only once a side reaches `targetSize`.
`no_quote_yet` is the interesting half: it means the market HAS a book, has NO
economic blocker, and still holds no order.

Those markets are not idle. They are being worked constantly -- 18 of 20 saw an
action inside three minutes -- but the orders do not stay. One market's bid
walked 0.69 -> 0.72 -> 0.75 -> 0.87 in six minutes, and every move produced a
`resting_price_left_the_touch` cancel followed by a replacement on a LATER pass:

```
21:49:49  PLACE  bid 0.69
21:50:10  CANCEL bid              rested 21s
21:51:24  PLACE  bid 0.72           <- 74s holding nothing
21:51:57  CANCEL bid              rested 33s
21:52:44  PLACE  bid 0.75           <- 47s holding nothing
21:54:22  CANCEL bid              rested 98s
21:56:06  PLACE  bid 0.87           <- 70s holding nothing
```

Across every market and side in that window:

```
cancel -> next place on the SAME side: 6 samples
  median 69.4s   mean 81.2s   min 40.0s   max 157.6s
  total dark time: 487s across 6 re-prices
```

**A re-price costs about seventy seconds of not earning.** The cancel is one
mutation and the replacement is another, the driver applies at most one
mutation per market per service pass, so the two land on different passes and
the gap is a full service interval. On a book that moves, a side spends roughly
half its life dark, and the resting count reads 8-9 out of 20 no matter how
healthy the process is.

**This is not churn in the 2026-08-14 sense** -- those quotes died two seconds
after placement to a state bug. These cancels are all correct: the touch really
did move, and an order off the touch earns a discounted score or nothing. The
loss is in the latency between deciding to re-price and having re-priced.

**Do not fix this by widening the cancel trigger.** Leaving a stale order at the
old price trades dark time for scoring at a discount, and the reward formula is
`discountFactor ^ ticks * size` -- the penalty compounds per tick, so a badly
stale order is worth little more than no order. The candidates worth testing
are (a) a shorter service interval, which shrinks every gap directly, and (b)
placing the replacement before cancelling the old, which removes the gap
entirely at the cost of briefly holding two quotes on one side -- bounded, but
it doubles that side's exposure for the overlap and needs its own risk check.

**Unmeasured:** the resting FRACTION over a long window, which is the number
that actually maps to reward. 8/20 is an instantaneous count and understates
coverage if the dark windows are staggered across markets.

## Re-deriving the REST-contention theory a second time (it is still wrong)

2026-08-16T22:00Z. Recording the mistake, because the theory is attractive
enough that it has now been derived twice from first principles and the
arithmetic looks compelling both times.

The pitch: `_refresh_quiet_book_if_due` re-reads a book over REST at 3s, the
reconciler refuses one older than 5s, `rest_requests_per_second` is hard-capped
at 4, so 20 markets want `20/3 = 6.7 req/s` against 4 and must starve.

**It is wrong, and the section "`book_stale` is self-perpetuating, and REST
contention is not the cause" above already disproves it with data.** Staleness
per market RISES as markets drop -- 21/market at 19 markets, 49/market at 8 --
which is the opposite of contention.

What made me believe it the second time was **using the event count as the
metric**, which the section "The `book_stale` replan works, and the event COUNT
is the wrong metric" also already warns against. Counting `book_stale` lines
measures how often we LOOK, not how often we are broken.

The correct metric, on the same window that produced the false alarm:

```
422  attempt=1
  5  attempt=2
  7  attempt=3
  0  markets retired
```

Nearly every event is one failure that recovers on the replan. Nothing
escalates, nothing costs a slot. A sustained 25/min of `attempt=1` is a healthy
bot visiting quiet books, not a starved one.

**Two rules that would have prevented this, both already written down:**
grep FINDINGS before deriving a resource-contention theory, and never reason
about `book_stale` from event counts.

**`max_markets = 20` is not unexamined.** Its code comment reasons about
exposure, which is what made it look capital-only, but the REST budget for 20
markets is worked through in the section beginning "REST budget on the shared
4 req/s governor" -- 0.033 req/s per market with an active WebSocket book, and
0.333 req/s only when a book goes quiet and falls back. The 6.7 req/s figure
assumes every market falls back at once, which is the overwhelmed-fallback case
that section explicitly distinguishes.

**Still open and genuinely unexplained:** the measured 70s median gap between a
`resting_price_left_the_touch` cancel and its replacement (see the section
above). That measurement stands -- it is timestamps, not inference -- but its
cause is NOT REST contention and is not yet established. Do not attach it to
this theory.

## The docs disagreed with the running config on four safety numbers

Audited 2026-08-16 from a second machine reading only the markdown, then
resolved against `CommissioningConfig` and the live command line.

**The runner takes no config overrides.** Its command line is
`--auto-select-rewards --database --lock-file --arm-live
--accept-one-contract-risk --exclusive-api-key --maker-lab-stopped` -- not one
of which sets a limit. So the dataclass defaults in `commissioning.py` ARE what
executes, and it is the only authority.

```
quote_size                       5
max_markets                      20
max_market_loss_usd              40
max_session_loss_usd             40
hard_stop_loss_usd               45
max_inventory_age_seconds        900
force_exit_before_start_seconds  300
max_early_exit_loss_usd          4
rest_requests_per_second         4
```

What the prose said instead:

| | claimed | live |
|---|---|---|
| loss stops | $2/$5/$15 (README, COMMISSIONING, PHASE5, SCALE_REVIEW); $30/$35 (invariant 25) | **40/40/45** |
| quote size | 1 contract (README, invariants 13/57) | **5** |
| max_markets | 8 (README, invariants 13/57, NEXT item 5) | **20** |
| inventory age | 30s (README, SCALE_REVIEW, COMMISSIONING) | **900s** |

Even the shorthand used throughout FINDINGS, NEXT and BUGFIXES -- "the $40
stop" -- is incomplete: the emergency ceiling is $45, not $40.

**Why this matters more than a typo.** Three of the four are risk limits. A
reader reasoning from "a $2 market stop" and "one contract" concludes the
downside is bounded at pocket change, when the live configuration risks 5
contracts across 20 markets against a $40 stop. Every one of those documents
reads as authoritative and none carried a date.

**The trap that produced it:** the numbers moved (1 -> 5 contracts, 8 -> 30 ->
16 -> 20 markets, 30s -> 900s, and the stops repeatedly), and each move updated
the code plus the file being worked in, never the rest. Nothing cross-checked
prose against config.

`README.md` and invariants 13, 25 and 57 are corrected. Invariant 57 keeps its
superseded text struck through rather than deleted, per the WATCHLIST rule --
"eight is measured, not chosen" was used as grounds not to investigate raising
it, and deleting the claim would hide why the ceiling stood for so long.

**All corrected 2026-08-16**: `README.md`, invariants 13/25/57,
`COMMISSIONING.md`, `PHASE5_ACCEPTANCE.md`, `SCALE_REVIEW.md`,
`COMMISSIONING_ACCEPTANCE.md`, `IMPLEMENTATION_PLAN.md` and NEXT item 5.

**One part of that audit did not hold.** It also reported FINDINGS carrying
duplicate section numbers -- two each of 10, 12 and 13 -- which would matter,
since other documents cite findings by number and WATCHLIST graduates entries
"to FINDINGS with the number that decided it". Checked exhaustively: FINDINGS
has 16 numbered headings, sections 1-8 plus 6b-6i, **no duplicates and no
sections 10, 12 or 13 at all**. `SAFETY_INVARIANTS.md` has 63 invariants, also
with no duplicates; 10, 12 and 13 exist there and are unique, which is the
likely source of the confusion. Recorded because a false structural claim is
worth closing explicitly -- otherwise it gets re-investigated.

**Rule going forward:** a safety number in prose is a copy, not a source. Cite
`commissioning.py` or quote the value with the date it was verified.

## Resting fraction, measured properly: 63% -- and the re-price penalty is small

Measured 2026-08-17T01:35Z over a 228-minute live window, 27 markets through 20
slots, 43 sides.

**The first version of this measurement was wrong.** It divided each side's
resting time by the whole window, and markets rotate as games kick off -- so a
market bound for ten minutes could score at most 4% however perfectly it quoted.
That reported a 30.0% mean which was almost entirely rotation, not performance.
`scripts/resting_fraction.py` now divides by each market's own active span and
reports the headline mean only over markets present for >=80% of the window.

```
 of span  of window   span  orders  market / side
   92.4%      91.3%   226m      29  sirvea-salnic    bid
   92.3%      74.7%   185m      24  urevad-sirvea    bid
   90.5%      89.4%   226m      12  chitsa-yinhua    offer
   86.3%      85.3%   226m      24  marshi-yujahn    offer
   85.2%      84.1%   226m      30  chitsa-yinhua    bid
   68.3%      67.4%   226m      58  yujkim-ziyzho    bid
   41.9%      41.4%   226m      23  reikom-alejon    offer
    3.7%       3.7%   226m      13  zhixia-ryunak    bid

mean over markets present >=80% of window: 62.8%  (13 sides)
```

**Re-pricing costs far less than the six-sample estimate suggested.** The side
with 30 placements rests 85.2%; the one with 58 rests 68.3%; the one with 29
rests 92.4%. Order count barely predicts resting fraction. NEXT item 3 was
written on a 25-minute window and a 6-sample gap measurement, and it
**overstates the case** -- a side re-pricing thirty times still rests 85%.

The real spread is between markets, not between busy and quiet sides: two
markets sit at 3.7% and 6.7% on a full 226-minute span, which is where the
coverage is actually being lost. That is worth understanding before optimising
the mutation path.

**Method note for whoever repeats this.** Denominator choice dominated the
answer: the same data reads 30% or 63% depending on it, and 30% would have
justified work that 63% does not. State the denominator whenever quoting a
resting number.

## Same-price re-place churn: looked obvious, measured at 10%

2026-08-17T01:40Z. Recorded because the sample that suggested it was vivid and
wrong, and the next person will find the same sample.

`zhixia-ryunak` rests 3.7% of a full 226-minute span across 37 placements, and
its action log reads like a textbook churn bug -- cancel for
`resting_price_left_the_touch`, then re-place at the IDENTICAL price, four
cycles in a row, ~40s dark each time:

```
01:33:32 PLACE  bid 0.43
01:34:38 CANCEL bid  resting_price_left_the_touch
01:35:26 PLACE  bid 0.43     <- same price
01:36:18 CANCEL bid
01:36:56 PLACE  bid 0.43     <- same price
```

The obvious reading is a flapping touch being chased, with a known fix shape --
the `reward_eligibility_dwell_seconds` hysteresis already exists for exactly
that failure on the eligibility gate.

**Measured across the whole session instead of that one market:**

```
re-prices after a price-left-the-touch cancel: 119
  came back to the SAME price:  12   (10%)
  genuinely moved:             107   (90%)
  dark time on same-price trips: median 49s, 14 min total
```

14 minutes of 228, across 43 sides. Adding dwell hysteresis to the re-price
trigger would recover a few minutes an hour at the cost of resting at a stale
price, and reward discounts per tick from the touch.

**What the data does support** is that the poor-resting markets are the ones
whose touch moves constantly. `zhixia-ryunak` logged 19
`resting_price_left_the_touch` cancels and rests 3.7%; `sirvea-salnic` logged 3
and rests 92.4%, with its cancels being `side_no_longer_desired` (the economic
gate) instead. A book that moves every 40 seconds cannot be rested in, so its
projected reward is never realised no matter how good the projection was.

That points at **selection**, not the order path: rank on whether a book holds
still long enough to rest in, not only on projected reward. Two markets is a
hypothesis, not a result -- it needs the touch-move rate correlated against
resting fraction across a full slate before anything is changed.

## 55% of our resting time is spent where reward is not

Measured 2026-08-17T02:20Z over the 21:47Z session: 42 markets, 56.4 hours of
total resting time, bucketed by how far from kickoff each resting second
occurred (`scripts/resting_by_kickoff.py`).

```
 mins to kickoff    resting   share    projected reward (section above)
            0-15      1.58h    2.8%    n=1, not read
           15-30      2.64h    4.7%    0.000313   100% pay
           30-60      5.42h    9.6%    0.000327   100% pay
          60-120     15.78h   28.0%    0.000204    90% pay
         120-240     21.50h   38.1%    0.000022    67% pay
            240+      9.47h   16.8%    0.000022    67% pay

inside the 0-120 window that pays: 45.1%
```

Reward accrues per second of resting, so a bucket's share of resting time is
directly comparable to its reward density. **The largest single bucket, 38% of
all resting time, is the worst-paying one.** More than half of everything the
bot did in four hours was spent where a third of markets pay nothing and the
median pays a fourteenth of the 15-30 minute window.

**This is the largest earning lever identified so far**, and unlike the five
theories that died during the same session it is a direct measurement of our own
behaviour rather than an inference about a mechanism.

**Why it happens.** `_opportunity_key` ranks on `projected_reward_total` first,
which should already prefer near-kickoff markets since that projection is what
falls off with distance. `game_start` appears in the key only as the **seventh
tiebreaker**, behind six continuous-valued fields that essentially never tie. So
proximity influences selection only through the projection, and the projection
is evidently not separating the buckets in practice.

**Two caveats that bound the conclusion.**

1. The reward figures are *projected* at a snapshot, not realised. The venue
   exposes no per-period earnings breakdown (no period field on
   `GET /v1/incentives/earnings`), so resting time cannot be attributed to money
   directly. This compares our time against a projection, not against income.
2. **Every market passes through every bucket.** Resting at 240+ costs nothing
   if that slot would otherwise sit empty. The cost is only real if
   near-kickoff candidates were passed over -- and with `viable=85` against 20
   slots there is a 4:1 oversupply, which makes it likely but not proven.

**The measurement that would close it:** the time-to-kickoff distribution of the
85 viable candidates at selection time. If most are inside two hours, distant
markets are displacing them and the ranking needs proximity promoted out of
seventh place. If most are distant too, the slate simply had nothing nearer and
nothing is being lost.

## ...and there is nothing nearer to rest in. Do not "fix" the ranking

Measured 2026-08-17T02:22Z, immediately after the section above, by running the
live selector read-only and bucketing its viable candidates by time to kickoff.

```
viable candidates: 74
        0-15      1
       30-60      4
      60-120      4
     120-240      8
        240+     57

inside the 0-120 window: 9 of 74  (12%)   against 20 slots
```

**Only nine candidates exist inside the paying window, for twenty slots.** The
55% of resting time spent beyond two hours is therefore not selection choosing
badly -- it is the overnight slate having almost nothing nearer. Promoting
`game_start` out of seventh place in `_opportunity_key` would take those nine
first and then fill eleven slots with distant markets anyway.

This confirms, rather than assumes, the caveat recorded with the previous
section: resting at 240+ costs nothing when the slot would otherwise sit empty.
The measurement stands; the action does not follow from it.

**What would change the answer:** the same scan run during a dense slate --
European or US afternoon -- when many markets are inside two hours. If near
candidates then outnumber slots and the ranking still fills up with distant
ones, proximity genuinely is mis-weighted. Overnight tells you nothing about
that.

**An unexplained discrepancy, left open deliberately.** The top-ranked distant
candidates here project `0.011986`, `0.011767`, `0.011682`, while the section
above records a 120+ bucket median of `0.000022` -- a 500x gap on what is
nominally the same quantity, `projected_reward_total` summed over eligible
sides. Either the two measurements are not the same field, or one is wrong.
Until that is resolved, **neither number should be used to justify a change to
selection**, because the whole argument for reweighting rests on projections
being comparable across buckets.

**Sixth theory this session that measurement stopped before it became a code
change.** The others: REST contention, re-price latency, same-price churn,
`book_stale` as a fault, and markets being unable to quote. The pattern that
caught all six was checking the specific claim rather than the plausible
mechanism.

## Trading losses now exceed reward. The 5% premise no longer holds

Measured 2026-08-17T12:0xZ over 203 fills and 93 markets.

This project's founding economics, recorded above, are "~$25/day in rewards
against about -$1.2/day in trading P&L -- losses are ~5% of reward", and nearly
every design decision follows from that ratio. **It is no longer true.**

Realised P&L attributed to the day of each market's first fill, split by whether
both sides of the round trip were actually recorded:

```
day        complete   P&L    | one-sided   P&L      reward that day
2026-08-14      2    -5.03   |     1     -2.93       5.48
2026-08-15     23   -11.22   |     5     -9.88      28.46
2026-08-16     14   -27.58   |     6     -9.60      20.25
2026-08-17     27   -51.03   |    15    -26.32      not posted
TOTAL          66   -94.86   |    27    -48.73
```

The `one-sided` column is the known bookkeeping defect (NEXT TOP PRIORITY) and
is not trustworthy. **The `complete` column is** -- both fills recorded, priced
in the venue's own frame by `realized_pnl`. It shows:

```
2026-08-15   reward +28.46   trading -11.22   net +17.24
2026-08-16   reward +20.25   trading -27.58   net  -7.33
2026-08-17   reward      ?   trading -51.03   (half a day)
```

**The bot went from profitable to loss-making around 2026-08-16**, and the loss
rate is accelerating: -5.03, -11.22, -27.58, -51.03. Per-market loss is growing
too, not just market count -- -0.49/market on 08-15 against -1.89/market on
08-17.

**The mechanism is crossed exits.** By category:

```
exit crossed (TAKER)   55 markets   -85.19   median -1.21   worst -8.33
NO EXIT RECORDED       27 markets   -48.73   (bookkeeping, not trustworthy)
exit passive (MAKER)   11 markets    -9.67   median -0.31
```

A passively-exited market loses a median of **31 cents**. A crossed one loses
**$1.21**, four times as much, and there are five times as many of them.

And the maker/taker mix inverted:

```
2026-08-15   37 MAKER   18 TAKER   33% taker
2026-08-16   13 MAKER   36 TAKER   73% taker
2026-08-17   25 MAKER   68 TAKER   73% taker
```

Entries are makers and exits are takers in the venue's frame, so 73% taker means
**almost every position is now being exited by crossing rather than resting
out**. Total fees flipped from a rebate to **+$3.46 paid**.

**Why size is NOT the lever.** Reward scores `discount ** ticks * size` and
losses scale with size too, so cutting `quote_size` scales both sides equally
and leaves the ratio unchanged. What changed the ratio is the taker share.

**What is not yet established:** why the taker share doubled between 08-15 and
08-16. Candidates, none tested: more concurrent positions (`max_markets` 8 ->
20) meaning more of them reach the 900s inventory clock; thin books that cannot
be exited passively at all (measured separately -- a 19c spread with our exit at
the touch and the only bid 19 cents away); and the pre-kickoff flatten firing
more often. **Do not act on any of these without measuring first** -- six
theories died during this session.

**The immediate implication is a risk decision, not an engineering one.** The
bot is currently losing more on trading than it earns in reward, on the numbers
that are trustworthy, and the trend is one-directional across four days.

## The 900s inventory clock now looks like the expensive one -- reversing an earlier read

Measured 2026-08-17T12:3xZ on 203 fills, against the 92 that produced the
earlier section "Crossing at 900s vs the pre-kickoff flatten: no case for
waiting". **That section's conclusion no longer holds on the larger sample.**
It is superseded rather than deleted, because it was used to keep the current
policy.

Method differs from that section and is stated so the two are not confused:
every fill the bot sends is now a PURCHASE (FINDINGS 6i -- two intents, never
four), so a matched YES+NO pair settles at exactly 1.0000 and per-contract P&L
is `1 - paid`, with no intent-inversion ambiguity at all.

```
bucket                              n   total/contract    median
crossed <900s (kickoff-driven)     12        -0.4705     +0.0500
crossed >=900s (inventory clock)   29        -2.1825     -0.1898
```

Crossing driven by the kickoff deadline has a **positive** median (+5c per
contract); crossing driven by the inventory clock has a **-19c** median. The
earlier reading had these the other way round at -0.075 against -0.059, on 8
and 12 samples.

**Why this is coherent with everything else measured today.** Splitting the same
49 completed pairs by how they were assembled:

```
both legs MAKER            7 mkts   paid 0.9714/pair   +2.9c per pair
mixed (one leg crossed)   40 mkts   paid 1.0620/pair   -6.2c per pair
```

A pair bought entirely passively is profitable; a pair completed by crossing is
not. The 900-second clock is precisely the thing that ends passive completion
early and moves a market from the first bucket to the second. 40 of 49 pairs --
82% -- were completed by crossing.

**What it does NOT establish.** The two buckets are confounded exactly as the
earlier section warned: the sub-900s group is near-kickoff by construction. That
confound was used to explain why sub-900s looked *worse*; it now looks *better*
despite it, which strengthens rather than weakens the reversal, but does not
remove it. n=12 against n=29 with per-contract variance far larger than the
difference in totals.

**This is not a tuning parameter.** `assert_authorized` pins
`max_inventory_age_seconds` to exactly 900 and `force_exit_before_start_seconds`
to exactly 300, and the bot refuses to arm otherwise. Changing it edits the
authorized commissioning envelope, and the 900s clock is a risk control as well
as an exit policy -- it bounds how long inventory is held at all. Removing it
means a position can be carried until the pre-kickoff flatten, which under the
current 2-hour selection horizon is up to ~2 hours, and up to ~6 hours if that
horizon is reverted.

**The decision, stated plainly:** the evidence now favours letting the passive
exit run to the kickoff deadline, and the cost of being wrong is holding
inventory longer. That is an operator risk decision, not a code change.

> **SUPERSEDED 2026-08-17T15:2xZ -- do not act on this section alone.** Measured
> on clean data, P&L degrades monotonically with holding time (+23.8c under
> 300s, falling to -23.5c past an hour), which is the opposite of what "give the
> passive exit more room" predicts. This section's comparison is confounded by
> exposure duration: kickoff-driven exits had little time to go wrong, clock
> driven ones had 900+ seconds first. The decision taken was to **leave the
> clock at 900**. See "Before relaxing the 900s clock" at the end of this file.

## Settlement, priced against the venue's own resolutions
## (first reading -$21.12 -- SUPERSEDED the same day, see the correction below)

Every earlier attempt to answer "what happens if we ride to settlement" was an
inference. This one is not. The venue publishes no settlement rows, but it
publishes the answer: a resolved market carries `outcomes` and `outcomePrices`,
and the winning outcome prices at exactly `1`. Our fills already record
`outcome` -- the side we hold. So for any position that went to settlement,
payout is `qty` if our side won and `0` if it did not. No model, no proxy.

`scripts/accounting.py --settlements`, run 2026-08-17 against the live ledger:

```
priced           45 markets  (14 won / 31 lost)
staked                98.14
returned              77.02
settlement P&L       -21.12
win rate              31.1%     (break-even 46.0%)

worst:  -8.15  Teodor Lefter     qty 10.00  paid 8.15 -> 0.00
        -4.64  Dominik Matic     qty  8.42  paid 4.64 -> 0.00
        -4.50  Maria Andrienko   qty  5.00  paid 4.50 -> 0.00
```

We paid 46c on average for these and they came in 31% of the time. That is a
21.5% loss on stake, and it is a real cash number, not `realized_pnl`.

**The sample is selected, and that is the whole caveat.** These 45 are not a
random slice of our trading -- they are the positions the exit ladder *failed to
close*. A position reaches settlement precisely when nobody would take the other
side at a price we would accept, which is disproportionately when it is moving
against us. So -$21.12 is what riding cost on the trades that were hardest to
exit; it is **not** the counterfactual for a policy of deliberately riding
everything, which would include the easy-to-exit winners too. Read it as an
upper bound on the damage, not as an estimate of the policy.

**What it does settle:** the "just let them ride" option is not free money.
Whatever the exit policy costs in crossed spreads, the positions that already
ride are losing more than that per dollar staked. Anyone proposing to hold
longer has to explain why the held sample would look different from this one.

### The cross-check that makes the number trustworthy

The report reconciles three independent sources and prints the gap:

```
equity_change = -(paid for fills) + settlements + rewards_paid
```

Marked equity is ground truth; fills and reward come from the account; that
leaves settlements derivable by subtraction. Derived-by-subtraction and
measured-from-resolutions must agree, and a gap means fills are missing from the
ledger. Both figures are *deltas between snapshots* -- the first version of this
compared a 25-minute window against an all-time total and printed a $31 "gap"
that was pure units error. A zero-length window now correctly reports a zero
gap.

Resolutions are final once published, so they are cached in `data/resolutions.json`
and only unseen markets cost a venue call. This matters: analysis shares the one
4 req/s governor with the live runner, and an earlier version of this analysis
triggered venue rate limiting by walking trade history while the bot was quoting.

**16 positions could not be priced**: 11 not yet resolved, 5 with no `outcome`
recorded on the fill. The second group is the remaining coverage hole.

## The on-droplet test suite is only a gate if `tests/` was deployed too

`check_sync.sh` compared `src/` and nothing else, so `tests/` drifted silently.
Deploying the `isAggressor` fix on 2026-08-17 then produced this:

```
FAIL: test_a_fill_only_the_venue_saw_still_reaches_the_stop
AssertionError: 0.0 != -3.05 within 2 places
```

Correct new code, failing on the droplet and passing locally. The droplet's copy
of that test predated the fix and built its trades with no `isAggressor` key, so
the new `leg = aggressor if isAggressor else passive` read `passiveExecution`,
found nothing, recorded no fills, and reported `realized_pnl` of 0.00.

**The false failure is the benign direction.** The reverse is the dangerous one:
a stale test that no longer exercises the deployed path *passes*, and the gate
CLAUDE.md requires before a restart certifies nothing at all. There is no
warning when this happens -- the suite just prints OK.

`check_sync.sh` now diffs `tests/` as well and fails on drift. Sync `tests/`
whenever you sync `src/`; `scp` of individual files makes forgetting the default.

### CORRECTION, same day: -$21.12 became +$5.90 once the ledger was repaired

The number above was measured hours before the `isAggressor` fix reached the
droplet, so it was computed on a ledger that was missing about 30% of its fills
-- and the missing ones were overwhelmingly the **passive** side. Within twenty
minutes of deploying the fix and restarting, the same command reported:

```
                        before repair      after repair
ledger fills                    212               300
  MAKER / TAKER                        163 MAKER / 137 TAKER
  BUY_LONG / BUY_SHORT                 146 / 146  (exactly balanced)
closed pairs             49 mkts -12.65    89 mkts -32.60
settled positions        45 mkts -21.12    12 mkts  +5.90
```

**The mechanism is the whole point.** A missing passive fill is a missing *leg*.
Every position whose hedge never reached the ledger looked like a naked bet
carried into settlement, when the account had actually been flat on it all
along. Repairing the ledger moved 33 markets out of the settled bucket and into
closed pairs, and the settled bucket flipped from a $21 loss to a $6 gain.

**So the conclusion drawn above -- "riding is not free money, the positions that
already ride are losing" -- is not supported.** It was an artefact of counting
one leg of a two-legged position. The honest statement is that we do not yet
know what riding costs, because we have never had a complete ledger to measure
it on. Do not carry the -$21.12 forward, and do not carry forward the win-rate
comparison against break-even either; both were computed on half-positions.

**What survives.** The *method* is sound -- resolutions are published, the
winner prices at 1, our fills record the side we hold. The mechanical part was
never in doubt. What was wrong was its input.

**This is also the clearest possible demonstration of the fill defect's cost.**
The bot was not mis-trading. It was mis-*recording*, and every number built on
top -- `realized_pnl`, the loss stops, the settlement study, the case for
changing the exit policy -- inherited the error and pointed the wrong way.
Nothing measured before 2026-08-17T13:20Z on this ledger should be trusted.

**Still incomplete.** `fetch_executions` reads two pages of 100 activities per
sweep, so the backfill reaches back only as far as recent history; older gaps
persist. 7 markets holding $19.00 of cost still carry no `outcome` and cannot be
priced at all. Re-measure once the ledger stops moving -- and check that it has
stopped before quoting any figure from it.

## The venue's fill history is the ACCOUNT's, and replaying it forges positions

Attempted 2026-08-17 to repair the three days of fills written before the
`isAggressor` fix. Walking the venue's full activity history and replaying it
through `apply_execution` did work -- 300 fills became 1495 -- and it was still
the wrong thing to do, for two reasons that were not obvious beforehand.

**1. There is no field that says which bot placed a fill.** The history reaches
back to 2026-06 and is the *account's*, so it imported ~700 markets of maker-lab
trading. The two are trivially distinguishable by eye and not at all in code:

```
2026-07-03 .. 07-20   ~270 fills    0-4% MAKER    1000+ contracts/side  (maker-lab)
2026-08-09 onward     ~780 fills   50-100% MAKER     5-10 contracts/side  (this bot)
```

A liquidity-bot-only ledger cannot be reconstructed from venue history alone.

**2. Replaying fills rebuilds POSITIONS, and that is the dangerous half.**
`apply_execution` maintains `execution_positions`. Replaying the fills of a
market that has since settled leaves the ledger holding a position the venue
does not have. The result: **345 non-zero positions** in markets settled days
or weeks earlier, against a venue reporting zero.

```
aec-cs2-nexo-gel-2026-08-12       net_qty = -25.00     (settled 5 days earlier)
aec-wtt-giopic-chalut-2026-08-10  net_qty =  -6.57
```

That is not a reporting error. **A runner that believes it holds inventory
plans exits for it**, so restarting would have placed real orders against
positions that do not exist. Caught before the restart; ledger restored from
the pre-backfill snapshot, bot restarted flat and quoting.

**What this rules out.** "Just replay the history" is not available as a repair
for the pre-fix days. A safe version needs a mode that writes fills *without*
touching positions, which the ledger API does not expose today. `--dry-run` is
safe and tells you the shape of what is there; a real run is not.

**The loss stops were never at risk**, checked at the time: the stop reads
`ledger.realized_pnl() - self._realized_baseline`, and the baseline is
re-snapshotted at startup and daily, so imported history is absorbed rather
than tripping anything.

**And note what this does NOT block.** Fill recording going forward was already
fixed and verified. This only concerns repairing 2026-08-14..16 retrospectively,
which affects historical reporting and nothing the bot does next.

## Reward PAID/PENDING is not cash. The transfer history is

`/v1/incentives/earnings` reports a `status` of PAID or PENDING per row, and
`scripts/rewards_report.py` totals them. On 2026-08-17 that read PAID $37.91 /
PENDING $102.79, with nothing marked PAID after 08-10 -- which was read, by me,
as "the venue has not paid us in a week". **That reading was wrong**, and the
operator caught it by simply looking at their balance.

Actual payouts land as `ACTIVITY_TYPE_TRANSFER` rows in
`GET /v1/portfolio/activities`, carrying `accountBalanceChange` with
`description = "liquidity reward payout"`:

```
2026-08-11T15:35:07   $32.06  COMPLETED   liquidity reward payout
2026-08-17T14:20:17    $5.85  COMPLETED   liquidity reward payout
2026-08-17T14:26:23    $5.83  COMPLETED   liquidity reward payout
```

### ...and the correction to THAT, an hour later: they reconcile exactly

The paragraph above originally concluded the two views "do not reconcile and are
not meant to". Wrong, and worth keeping visible because the mistake is easy to
repeat. Watched for another twenty minutes, a third payout landed and the totals
matched to the cent:

```
2026-08-11T15:35   32.06
2026-08-17T14:20    5.85
2026-08-17T14:26    5.83
2026-08-17T14:33   42.77
                  ------
                   86.51   ==  the PAID column, exactly
```

**The `date` column is the date reward was EARNED, not the date it was paid.**
So "nothing PAID since 2026-08-10" never meant "no money has arrived since
08-10" -- it meant "reward earned after 08-10 has not been paid yet", which was
true when read and stopped being true while it was being read. PAID is cash
received and it is reliable; the trap is reading its date as a payment date.

Payouts also arrive in bursts, not on a daily schedule: three landed within
thirteen minutes on 08-17, clearing two full earning days at once. A single
reading of this table says nothing about whether payouts have stalled. Compare
two readings, or read the transfer history, which is what
`accountBalanceChange.description = "liquidity reward payout"` gives you.

The same activity stream is also the only place deposits appear:

```
2026-08-10  $50  COMPLETED  Polymarket Deposit
2026-08-11 $300  REJECTED   Apple Pay
2026-08-11 $400  COMPLETED  Apple Pay
```

Deposits are what make a true P&L possible at all -- `equity - deposits` needs
no ledger and no model. **Do not compute it from a partial page walk.** The read
above covers 8 pages; there was trading in 2026-06/07, so earlier deposits
almost certainly sit outside it, and a bottom line drawn from an incomplete
deposit list is worse than no bottom line.

**Cost of getting this wrong.** Paging 60 pages to find these rows tripped venue
rate limiting while the bot was quoting -- the second time in this project, both
times mine. Six pages was enough to find the rows; the query is bounded at eight
now. The runner was unaffected (uptime unbroken, escalation never past
`attempt=3`, the 120 concurrent errors were ordinary `book_stale`), but that was
luck rather than design.

**To do:** teach `rewards_report.py` to read the transfer history and show cash
received beside accrued, and pull the full deposit history at a moment when the
bot is stopped. See NEXT.

## True P&L without the ledger: equity minus funding. The bot is ~break-even

Measured 2026-08-17T15:00Z, from venue cash movements only -- no ledger, no
model, no settlement data. This is the one P&L calculation today that did not
collapse when the ledger turned out to be wrong, and it should be the default.

**Account-wide** (complete history; the activity walk reached `eof` in 20 pages):

```
equity now                420.23
deposits in              4408.00
withdrawals out          1798.50
remediation credits       224.60   (two goodwill payouts, 06-27 and 07-02)
net funding in           2834.10
reward received           117.77

all-in P&L              -2413.87
trading alone           -2531.64
```

That loss is almost entirely **pre-liquidity-bot**: maker-lab traded 06/07 at
1000+ contracts a side against this bot's 5-10.

**This bot alone.** Its era starts 2026-08-10, and the identity closes:

```
equity_now = equity_at_08_10 + funding_since + reward_received + trading_since
    420.23 = E + 450.00 + 91.94 + T
         T = -121.71 - E
```

`E >= 0`, so **trading since 08-10 has lost at least $121.71** -- far worse than
the -$29.88 the ledger reports for 08-14..17, which places roughly $90 of loss
in 08-10..13, the days the ledger does not cover at all. Independent
confirmation that the ledger understates losses, arrived at without using it.

Total reward earned 08-10..16 is $140.70, so:

```
net = T + reward = 18.99 - E
```

| E (equity on 08-10) | net result |
|---|---|
| 0 | +18.99 |
| ~19 | break-even |
| >19 | negative |

There were $250 of deposits over 08-05..09 and the account was trading into
08-10, so E was very probably well above $19. **The honest read is
roughly break-even, likely slightly negative** -- reward very nearly covers
trading losses but does not clearly beat them.

**Do not restate this as "+$24".** An earlier figure that afternoon compared
08-14..17 ledger trading against 08-14..16 reward and got +24.31; it missed the
worse 08-10..13 trading entirely because those fills are not in the ledger.

**E is not recoverable.** Equity on 08-10 cannot be read back from the venue,
which is precisely why `accounting.py` writes dated snapshots -- from now on
this question is a subtraction, not an inference. Take a snapshot before any
change whose effect you will want to measure.

## (SUPERSEDED 2026-08-22) Before relaxing the 900s clock: P&L degrades
## monotonically with holding time

> **Its table is withdrawn.** Measured on 2026-08-17 fills only, n=11 in the
> 0-300s bucket. Across 2,200 pairs of our own data the relationship is a U
> with its floor at 900-1800s, and the 0-300s bucket is -5.26c rather than
> +23.8c. See "Holding time is NOT monotonic on our own data" above. The
> DECISION it supported -- leave the clock at 900 -- happens to survive, for
> a different reason: 900 is where the U bottoms out.

Measured 2026-08-17T15:2xZ on **2026-08-17 fills only** -- the first day the
ledger recorded fills correctly, so the only day this can be asked of. Written
because the operator was about to relax `max_inventory_age_seconds` on the
strength of the reversal section above, and this cut argues the other way.

### First, the reversal's inputs check out

The reversal was measured at 12:3xZ, *before* the `isAggressor` fix reached the
droplet at 13:20Z -- the same broken ledger that forced the settlement finding
to be withdrawn. The obvious worry was that missing passive fills would make
pairs look crossed that were completed passively, manufacturing exactly that
conclusion. **That worry was wrong**, and it is recorded so nobody spends the
time again:

```
                    crossed share    per crossed pair
2026-08-15 (dirty)      63%              +4.4c
2026-08-16 (dirty)     100%              +1.4c
2026-08-17 (CLEAN)      95%              -6.1c      n=58
```

The clean day shows *more* crossing, not less, and reproduces the -6.2c the
dirty analysis reported to within a tenth of a cent. The crossed-pair penalty
is real.

(The clean day's `both legs MAKER` row is n=3 at 0.3633 paid/pair. Do not quote
it -- paying 36c for something that settles at 1.00 is a partial-pair artefact
of proportional cost allocation, not a 64c edge.)

### But holding time predicts loss, monotonically

```
pair completion time, 2026-08-17, n=61

     0-300s    n=11   +23.8c per pair
   300-900s    n=12    -5.1c
  900-1800s    n=23    -4.5c
 1800-3600s    n=9    -13.2c
   3600s+      n=6    -23.5c
```

**"Give the passive exit more room" predicts the opposite of this.** Pairs that
got more time should do better; they do steadily worse. Relaxing the clock moves
positions from the -4.5c bucket into the -13.2c and -23.5c ones.

### The likely reason the reversal looked convincing

It compared what *ended* the position -- clock-driven crossings (-19c median)
against kickoff-driven ones (+5c). But a kickoff-driven exit is a position
entered close to kickoff, so it had **little time to go wrong**; a clock-driven
one had 900+ seconds of exposure first. That comparison is confounded by
**exposure duration**, and the table above says duration is what predicts the
loss. The reversal may have measured "positions held longer lose more" and
attributed it to "crossing at the clock is expensive".

Both the original section and the reversal flagged this confound. Neither
resolved it. It is now the whole question.

### What would actually settle it

Not more history -- every historical cut is confounded the same way, because
what gets held longer is chosen by the market, not by us. **Randomise:** run
part of the book at 900s and part at 2700s, assigned randomly and running
concurrently, so both arms see the same hours and the same books. Compare
per-pair P&L by arm. Use a small treatment arm (~5 of 20 markets) to bound the
downside.

### Decision taken 2026-08-17: leave it at 900

Three reasons, in order of weight. The clock is a **risk control** -- it bounds
inventory age at all, and without it a position rides to the pre-kickoff
flatten (~2h at the current horizon, ~6h if that is reverted), with settlement
behind that and settlement measured as a loser. The evidence for relaxing it
points the wrong way on the only clean day available. And the account is near
break-even at roughly +/-$3/day, where a variance-increasing change swamps the
signal it is meant to reveal.

**Do not relax this parameter on historical analysis alone.** If it is revisited,
it is the A/B or nothing.

## Architectural audit, 2026-08-17: nine defects, all verified against the code

Written by the operator while planning the sibling Kalshi build, and checked
line by line here before recording. **All nine hold.** Line references are to
the state at commit 974c9ab.

1. **The EV gate is a no-op.** `enforce_reward_cost_gate: bool = False`
   (`commissioning.py:185`). The blocker is computed and then stripped from
   `economic_blocker_by_side` when the gate is off
   (`live_commissioning.py:1382`), so the reconciler's filter is vacuous. A
   carefully-built EV model whose output is discarded. The same field was also
   the ranking key, so disabling the gate silently disabled the ranking, and
   nothing failed loudly; it now ranks on `projected_reward_total`.
2. **`spread_capture = 0.0` and `maker_rebate = 0.0`, hardcoded** (invariant
   34) in both the runner (`live_commissioning.py:1339,1342`) and the selector
   (`market_selector.py:312,313`), because live evidence never arrived. The EV
   expression therefore carries one revenue term and two cost terms.
3. **One mutation per pass** -- a cancel and its replacement land on separate
   visits, so a re-price costs two sweep intervals of dark time, against a
   reward that is a time integral over resting orders. **Second-order in
   practice**, see below.
4. **`book_stale` as a CRITICAL is a category error.** "No new data" becomes a
   fault path with escalating backoff, and it selects against quiet books --
   precisely the low-competition, high-share markets. 30 of 31 retirements in
   one session were `unrecoverable_book_stale`. Staleness should gate new risk,
   not trigger a fault.
5. **Reward projection works for one of three phases.** `project_order_reward`
   returns `period_duration_unproven` for anything but `day_of`
   (`rewards.py:299`), because only that period has a provable six-hour clock.
   Two-thirds of the lifecycle is unprojectable.
6. **The fill-hazard model is the weakest empirical foundation here.** One day,
   a different bot, a different venue, censored at first fill, and a max over
   marginal Wilson upper bounds -- deliberately not a joint model (invariant
   43) -- so the estimate is whichever single bin is highest: blunt and nearly
   constant. It multiplies the unwind loss in the only term gating entry.
   `HISTORICAL_REWARD_YIELD_PER_CONTRACT_HOUR` admits its units are wrong and
   is fenced to ranking only.
7. **The 0.5 reward haircut has no evidence behind it** (`rewards.py:262`) and
   multiplies into every projection (`rewards.py:356`).
8. **No settlements table, and nothing writes one** -- confirmed, `settlement`
   does not appear in `execution_ledger.py`. Every resolved position books as a
   total loss of entry cost regardless of outcome, which on a binary venue is
   where the money is.
9. **Policy duplicated** between `_evaluate_rewards`
   (`live_commissioning.py:1197`) and `evaluate_market_opportunity`
   (`market_selector.py:227`). Changing one alone does nothing; that cost a
   deploy cycle to discover.

### Three corrections to the audit

**#1's root cause is now known and fixed.** The gate is off because P&L could
not be validated; P&L could not be validated because `fetch_executions` read
`aggressorExecution` plus `makerExecutions`, a key the venue does not publish.
~60% of fills are passive and none were recorded. Fixed 2026-08-17T13:20Z. So
the sharper statement is: **the gate was not disabled because the model was
wrong, but because the measurement apparatus was broken.** That is now
repairable, which makes #1 the keystone rather than a lament.

**#3 is second-order, on two independent slates.** `NEXT.md` item 3 measured
the re-price cost as smaller than feared; that was discounted as one slate.
Today's cancel reasons are a second, agreeing:

```
side_no_longer_desired          608   72.6%   <- the economic gate, a policy question
resting_price_left_the_touch    158   18.9%   <- re-pricing, what #3 is about
cancel_building_after_fill        8    1.0%
```

Re-pricing is a fifth of the churn. The dominant term is the gate declining
sides, which more throughput would not touch.

**The audit contains no economics, and that is the missing tenth item.**
Reward runs ~$20/day against trading losses of ~$16-17/day: **the strategy is
near break-even before any of the nine is fixed.** Nine clean fixes buy a
well-instrumented break-even bot. Weigh that before spending on them.

### A caution carried to the Kalshi build

The Kalshi pair edge cited alongside this audit -- $0.0379 median, positive in
98.2% of 22,030 snapshots -- is a **snapshot** measurement of what the book
displayed, not a realized measurement of what was captured. This repo is a case
study in that gap: entries rest as makers, exits cross as takers, **95% of
pairs complete by crossing**, and a crossed pair costs -6.1c. A displayed 3.8c
edge does not survive that. Treat it as an unvalidated input, not a starting
advantage -- which is the same caution #1 exists to teach.

## Why the bot quotes 3 markets and not 20: ~90% of books are tighter than 2c

Asked live 2026-08-17T18:11Z with 3 of 20 slots in use. The `[selection]` log
line could not answer it, so the selector was run by hand. Recorded because the
question will recur and the answer is stable.

```
catalog markets             16250
in the 2h horizon              45
carrying rewards               44
  - unsupported program         0
  - book too tight to enter    40    <- 91% of rewarded markets
  = eligible pregame            4
books actually read             4
  - deferred (book budget)      0
  - book read failures          0
  = VIABLE                      3    of 20 slots
```

**`_catalog_spread_can_enter` is the dominant filter by a wide margin.** It
drops any market whose catalog BBO spread is under `minimum_entry_spread` (2c),
before a book is ever read. That is the same rule FINDINGS already records as
the top rejection cause (`spread_below_2c`, 8,229 hits) -- now quantified as a
share: **it removes ~9 of every 10 rewarded markets.**

**The 20-slot limit has never been the constraint.** Neither has the 2-hour
horizon (45 markets were inside it), book staleness (0 read failures), the book
budget (0 deferred), or unsupported programs (0). Every market that dropped out
did so on a deliberate economic rule.

### How to tell "normal" from "broken" in one command

`scripts/usable_markets.py`. The five counters in the `[selection]` line cannot
distinguish the two cases; these can, and none of them are logged:

| counter | non-zero means |
|---|---|
| `failures` | markets lost to **book read errors** -- a real fault |
| `deferred_markets` | more candidates than `max_book_candidates` will price |
| `unsupported_programs` | reward programs this executor does not implement |
| `catalog_spread_filtered` | books too tight -- **normal**, and usually ~90% |

`executable_catalog_markets == 0` is a schedule trough, normal at quiet hours:
the same bot showed `viable=14` at 13:25Z and `viable=1` at 18:00Z the same day.
Compare against a busy hour before concluding anything from one reading.

**This is also the only real lever on market count.** Lowering
`minimum_entry_spread` would take eligible markets from 4 to ~44. It buys
thinner margins in exchange, against a measured -6.1c cost per crossed pair and
95% of pairs completing by crossing. Not obviously a good trade, and not to be
changed during a measurement week.

## Reward-by-day reconciles to cash within $0.24 -- venue dates are ET

Measured 2026-08-18T03:30Z, two independent venue sources:

```
sum of PAID rows by earning date (/v1/incentives/earnings)   140.94
cash actually received (transfer history)                    140.70
difference                                                    +0.24   (0.17%)
```

**Totals are trustworthy.** We read the venue's own `date` field rather than
inferring one, and the incentives ledger agrees with the money that arrived.

**Per-day figures are good to about +/-$0.25, not to the cent.** Observed
directly: a $5.83 payout against a $6.02 earning row, and $42.77 against
$42.58 -- each off by $0.19 in opposite directions, cancelling exactly. That is
reward shifting across a day boundary.

### CORRECTED 2026-08-24: the venue dates reward in Eastern Time

The [official earnings API](https://docs.polymarket.us/api-reference/incentives/get-incentive-earnings)
defines each `date` as midnight-to-midnight Eastern Time, and the liquidity-
program documentation gives the same boundary. During
EDT an API row dated `2026-08-22` covers `2026-08-22T04:00Z` through
`2026-08-23T04:00Z`; during EST the boundary is 05:00Z. Posting or payment can
happen days later without changing that earning date.

The earlier 15-market inference that declared UTC is superseded. A fresh join
of the live 08-21/08-22 API rows to this bot's intent ledger found **13 rows
worth $3.14 dated 08-21 whose entire order histories occurred on 08-22 between
00:10Z and 03:59Z**. Those are direct ET-exclusive counterexamples: that UTC
interval is still the evening of 08-21 ET. The old result's mechanism was not
preserved well enough to repair; it must not overrule the documented boundary
and current discriminating rows.

Totals remain correct because timezone bucketing cannot change their sum.
Daily comparisons were not correct: `daily_pnl.py`, the dashboard's top-day
strip and `pair_pnl.py` assigned ledger trading by UTC while joining it to ET
earnings. They now use `America/New_York`, including DST. Historical tables in
this file that explicitly say `UTC day` retain their original measurement but
must not be used as exact reward-versus-trading daily joins; rerun the ET-aware
reports for that decision.

The **risk day remains UTC intentionally and was not changed**. Session and
hard stops consume a durable safety budget between UTC midnights; they do not
attribute venue reward. Moving that reset would change live risk authority and
could grant a fresh budget at a different instant, so correcting an accounting
calendar does not authorize it.

### Also settled 2026-08-18: pending reward does pay

Every pending dollar cleared. `140.94 received / 0.00 pending`, up from
`91.94 / 48.76` hours earlier. The earlier worry that pending might never
convert was unfounded -- payouts arrive in bursts, days after the earning date,
and one reading of the table says nothing about whether they have stalled.

### Payouts do not land on weekends -- operator fact, confirmed against the ledger

Told by the operator 2026-08-23 and verified the same hour against the full
transfer history. Recorded because the PENDING column looks identical to a
stall over a weekend, and this file already contains one wrong "the venue has
not paid us in a week" conclusion drawn from exactly that view.

Every reward payout this account has ever received, by weekday:

```
2026-08-11  Tue   32.06
2026-08-17  Mon    5.85  5.83  42.77  5.43  25.83  22.93     <- 6-payout burst
2026-08-20  Thu   35.73  14.85  15.50
2026-08-21  Fri   19.72
                                        TOTAL 226.50 account-wide
```

**Eleven payouts, all Mon/Tue/Thu/Fri, none on a Saturday or Sunday**, across
two full weekends (08-15/16 and 08-22/23). The Monday 08-17 burst of six
transfers totalling $108.64 is the catch-up after the first of those weekends,
which is the shape the rule predicts.

**What this means for reading the earnings table on a weekend:**

- PENDING not moving is expected, not a stall. On 2026-08-23 (Sunday) $31.06
  sat pending for 14+ hours unchanged; nothing was wrong.
- A Saturday or Sunday earning date showing **no rows at all** is also
  expected. 08-22 had none 16 hours after the day closed.
- So on a weekend the table cannot distinguish "not posted yet" from "earned
  nothing" *or* from "stalled" -- three states, one appearance. Do not judge
  reward, or any change made to chase reward, from a weekend reading.

**Check cash, not status.** `GET /v1/portfolio/activities`, rows with
`description = "liquidity reward payout"` and `status COMPLETED`, is the only
view that says whether money arrived. See the entry above,
"Reward PAID/PENDING is not cash".

**Consequence for the horizon A/B.** 2026-08-22 and 2026-08-23 are Saturday and
Sunday, so the first clean day of reward data on the six-hour horizon plus the
scheduler fix will not be readable until **Monday 2026-08-24 posts, at the
earliest**. The per-pair trading cost measured 0.082 on 08-23 against ~0.045 on
the five preceding days; that number is real now, but its income side is not
visible yet and must not be judged against a weekend blank.

## A just-placed order can be accepted, then unreadable for over two minutes

Measured 2026-08-18, from the incident in `BUGFIXES.md`. Order
`BYK0EWMPMDK1` on `aec-setkameua-mikole-hovser-2026-08-18`:

| time (UTC) | what the venue did |
|---|---|
| 08:23:20.662 | accepted the place and **returned an exchange id** |
| 08:23:2x | omitted it from a **complete** open-orders snapshot |
| 08:23:23 → 08:25:34 | `GET /v1/order/{id}` failed on every attempt, ~65 tries at 2s |
| 08:25:34.451 | answered, reporting `event_at` 08:23:20.909 — it had existed all along |

**An exchange id in hand is not proof the venue will serve state for it.**
Read-your-writes does not hold here, and the gap can be minutes rather than the
seconds every timeout in this repo is sized against. `apply_order_snapshot`
already guards the snapshot half of this (`snapshot_at < item["event_at"]`),
but that compares against the venue's *claimed* `as_of`, which this incident
shows is not a barrier either — the order was omitted from a snapshot whose
`as_of` was after its own event time.

Consequences to design against, not around:

- An unresolved intent is **not** rare-and-transient. Anything armed by one —
  `unresolved_mutation_timeout_seconds` above all — must survive a multi-minute
  venue stall or it will fire on a healthy, flat account.
- **Do not raise the timeout to cover it.** It was 10s and cost an outage on
  2026-08-17; it was raised to 60s and cost another on 2026-08-18. The measured
  stall was 131s, so 60 → 180 buys one incident and re-arms the same gun. The
  timeout is not the variable that matters.

### Confirm latency is bimodal; the percentile that gets quoted is meaningless

Same session, 642 orders, `event_at` → `received_at`:

```
p50 = 7.58s    p95 = 521.87s    p99 = 839.96s    max = 1017.71s
```

The p95 is not "a slow confirm" — the tail is dominated by `CANCELED` rows,
where `event_at` is the venue's cancel time and `received_at` is when we next
looked. Two different quantities share one column. **Do not size a timeout off
this distribution**; the 2026-08-17 fix did exactly that (10s ≈ the then-p95)
and armed the guard at the middle of normal operation. Measure the specific
transition you are bounding, on rows you have confirmed mean what you think.

## Venue maintenance makes the unresolved-mutation stop unrecoverable

Measured 2026-08-18T17:08-17:25Z, during a Polymarket maintenance window.
Mutations returned `InternalServerError` while reads kept working.

What that does to the bot, in order:

1. `POST /v1/orders` 500s and returns **no exchange id**, so the PLACE intent
   goes `UNCERTAIN` with `exchange_order_id = NULL`. Nothing can ever resolve
   it: the reconcile asks the venue *by exchange id*, and there is no id to
   ask about. This one intent alone arms the 60s critical stop permanently.
2. The stop fires and shutdown begins.
3. Shutdown's cancels 500 too. `_retry_uncertain_cancels` re-issued one cancel
   **31 times over 15 minutes** and got an identical 500 every time. The retry
   worked exactly as designed and could not possibly succeed.
4. Shutdown exhausts its budget with orders still resting, so `flat: false`
   and exit 3 -- which the supervisor never restarts.
5. On the next start, preflight refuses: `shared account has open orders;
   execution ledger has unresolved intents` -- exit 2, also never restarted.

**No client-side retry can fix a venue that is not accepting mutations.** The
useful question is not "how do we retry harder" but "how do we come back
afterwards", and today the answer was: we do not, without a human.

`_startup_recovery_is_scoped` exists for exactly these three blockers, but it
authorises only markets in the **current** active set. After a maintenance
outage the stuck orders sit in markets the selector will not pick again -- one
of the six was `aec-cs2-oldboyspl-navij-2026-08-11`, a week old and long
settled -- so the designed recovery path cannot claim them and the bot stays
refused. That is the gap to close, not the retry.

Also worth knowing: **29 of the 30 unresolved intents were retries of a single
cancel.** Each attempt opens its own intent, which is correct (the state
machine forbids `UNCERTAIN -> SUBMITTING`), and they all resolve together on
the terminal state -- but a raw count of unresolved intents badly overstates
how many distinct things are stuck. Count by `exchange_order_id`, not by row.

## The venue changes an order's STATE without advancing its event timestamp

Measured 2026-08-18, and it had been silently wedging rotation. Order
`BYTCVSJ4EDJT` on `aec-cs2-oldboyspl-navij-2026-08-11`:

```
18:11:30  read as PARTIAL   lastTransactTime 2026-08-18T18:08:27.222776Z
18:16     read as CANCELED  lastTransactTime 2026-08-18T18:08:27.222776Z
```

Same timestamp, different state. `reconcile_order` built its event id as
`order:{id}:{event_at}`, so the second read was byte-identical in identity to
the first, `_accept_event` rejected it as a replay, and `_upsert_order` never
ran at all.

That matters more than it looks. `_upsert_order` is where the ledger already
knows how to handle this -- `resolves_absence` deliberately lets a terminal
state outrank an `UNKNOWN_ABSENT` row even when `event_at` looks stale -- but
that code was **unreachable** through the reconcile path. The row could never
leave `UNKNOWN_ABSENT`, and `risk_state_known()` counts absences
**account-wide**, so one order stuck this way blocks rotation for every market.
The bot keeps quoting whatever it already holds and can never rebind.

**`lastTransactTime` is not a version.** Do not use a venue timestamp alone as
an idempotency key for anything whose state can change under it. The fix keys
the reconcile event on `(id, event_at, status)`; each still applies at most
once, and `_upsert_order` keeps its own ordering and terminal guards, so a
stale or backwards transition is still refused where it should be.

**A test that seeds the order some other way cannot catch this.** The first
version of the regression test applied the order through a `ws` event, so the
`rest_reconcile` event id was never consumed, no collision occurred, and it
passed against the unfixed code. The collision has to be built by calling
`_rest_reconcile` twice. (Two calls at one frozen clock reading also need
distinct `tag`s, or the snapshot event id collides for an unrelated reason.)

## The dashboard's loss bar summed only losing markets, overstating by 4.6x

Measured 2026-08-18T20:32Z. The bar read `$36.34 / $40 -- $3.66 left` on a day
whose net realised P&L was **-$7.88**. Roughly $32 was actually left.

The accumulator kept only the negative markets:

```python
if realised < 0:
    session_loss += -realised     # winners discarded
```

That is a gross loss total. No stop reads that number.
`runtime_safety.blockers` trips on `-realized_pnl >= max_session_loss`, which
is **net** -- winners included. Fixed to accumulate net and clamp at zero, so
a profitable day consumes none of the stop.

Two things worth carrying forward:

- **It erred toward looking dangerous**, not safe. The 2026-08-16 lesson in
  `CLAUDE.md` is about prose making the bot look ~20x safer than it was; this
  is the mirror image, and it is not harmless either. A bar reading "$3.66
  from a hard stop" is an instruction to shut a healthy bot off by hand, and
  hand-closing while it runs debits the real stops with a phantom loss.
- **The same screen already disagreed with itself.** The header read
  `TODAY -7.88` from the trustworthy equity path while the bar read `$36.34`
  from this one. Two numbers for one quantity on one screen, and neither was
  labelled as a different quantity. When a dashboard shows the same thing
  twice, make them the same computation or name them differently.

`worst market` is deliberately left negative-only and per-market, because that
is the shape of its own stop: `any(value <= -max_market_loss for value in
realized_by_market.values())`.

## Both loss stops baseline per session, so a restart forgives the drawdown

Measured 2026-08-18, after the operator reported being down on the account
while both stop meters read near zero.

`SessionRiskGuard` is built once in preflight and `starting_equity` is stamped
from the balance at that instant. **Ten sessions started that day**, so the
budget was handed back ten times.

```
window                fills   matched-pair   unmatched cost (unpriced)
THIS SESSION            37        -5.51        4.59 across  1 market
TODAY                  162       -12.47        9.35 across  3 markets
ALL TIME               529       -46.55       67.38 across 29 markets
```

The $40 stop was reading **-$5.51, 14% consumed**, on a day already down
$12.47 by the same measure.

**The ratchet is the real hazard, not the reset.** Lose $30 of equity, crash,
restart: the hard stop re-stamps its baseline at the *lower* equity, so that
$30 is forgiven and it takes another $45 from the new floor to fire. Each step
stays under the limit while the account walks down. That is the shape to look
for when the account is down and the stops look calm.

### The two stops do NOT have the same blind spot

Worth separating, because conflating them sends the fix to the wrong place:

| stop | trips on | sees a settled position? |
|---|---|---|
| `max_session_loss` $40 | `-realized_pnl`, matched pairs only | **no** |
| `hard_stop_loss` $45 | `starting_equity - current_equity` | **yes** |

A position that settles worthless moves marked equity, so the hard stop does
see it; only the matched-pair stop is blind. So the settlements table and the
baseline fix are independent -- the baseline can be fixed without it.

### `hard_stop_reached` does not evaluate on stale equity, by design

It returns False when equity is unknown or older than 15s. Correct: it refuses
to fire on numbers it cannot trust. But it means that during a venue outage the
hard stop is **not evaluating at all** -- and on 2026-08-18 plain reads failed
4 of 6 probes for a stretch. Do not "fix" this by letting it fire on stale
data; the coincidence of a bad venue and a bad day is what the pre-kickoff
flatten deadline is for.

## The 2c spread filter is a much weaker lever than "94% refused" suggests

> **PARTLY CORRECTED 2026-08-19T23:31Z -- do not read this alone.** The
> counts below stand; the claim that the rejects are long-dated MLB futures
> came from the `--rejects` listing, which was not filtering by horizon at
> all and printed markets that were never candidates. See "A third of the
> funnel drains through a counter that does not exist" below, and BUGFIXES.

Measured 2026-08-19T03:25Z with `scripts/usable_markets.py --rejects`.

The funnel reads alarmingly: 34 rewarded markets in the horizon, 32 refused for
a tight book, 1 viable. That invites the conclusion that relaxing the 2c
minimum would unlock 32 markets. **It would not.** The rejected spreads:

```
0.001 - 0.007    25 of the 30 sampled
0.009 - 0.014     5
>= 0.015          0
```

Nothing sits just under the threshold. These books are **3x to 20x tighter**
than 0.02, so:

- 2c -> 1c admits about **5 of 32**, all at 1.0-1.4c
- 2c -> 0.5c admits most of them, at spreads with essentially no room between
  entry and exit

**What the rejects actually are matters too.** Nearly all were
`tec-mlb-*champ-2026-09-27-*` -- MLB championship futures a month out, not the
pregame sports moneylines this strategy is built around. Long-dated futures are
tightly quoted by design. At 03:25Z the live sports universe has dried up and
this is the residue.

So "94% refused" is not a backlog of near-misses being held back by a
conservative setting; it is a different kind of market that the strategy does
not want. Do not read the percentage as headroom. If more markets are wanted,
the horizon and the hours the bot runs are the levers, not the spread minimum.

## Matched-pair P&L is leg-to-leg market movement, not spread capture

Measured 2026-08-19 from the liquidity bot's own fills, 949 contract-pairs
across 161 markets.

`1 - (p_long + p_short)` looks like the spread we quoted, because buying NO at
`q` is offering YES at `1-q`. **That identity only holds if both legs fill
against an unmoved book.** They do not, and the data says so plainly:

```
edge/contract    markets   pairs      P&L    mean
worse than -2c        76     487   -203.08   -0.41742
0 to +2c               3      12     +0.02   +0.00156
+2c to +4c             3      15     +0.30   +0.02000
over +7c              73     395   +153.19   +0.38757
                            ────   ───────   ────────
                             949    -48.12   -0.05073
```

A +/-40c "spread" on a 0-1 contract is impossible. The distribution is bimodal
at roughly +/-0.4, which is **market movement between the legs**. The bot
rests, one side is hit, it re-quotes as the book moves, and the other side
fills at a different level -- sometimes hours later. Consistent with 6i:
crossing an *unmoved* book lands at exactly 1.00, so any deviation is movement.

**Mean cost -0.0507/contract.** maker-lab's *losing* sub-2c bucket was
-0.0087/contract, so this is ~6x worse on the markets we do trade -- with the
caveat that maker-lab measured 900s markout and this is settled pair cost, so
they are not strictly comparable.

### Two things this rules out

- **The spread regime cannot be measured from this bot's data.** No spread is
  recorded anywhere -- not in `execution_fills`, not in `execution_orders`, not
  in the log -- and the 2c filter means there are zero sub-2c samples. You
  cannot measure the regime you refuse to enter. maker-lab's table in section 3
  remains the only evidence for the 2c rule.
- **Entry spread is second-order.** Whatever 1c versus 2c is worth, it does not
  touch a 5c/contract loss arising between the legs. Tuning the entry filter is
  not the lever on this; whatever governs re-quoting into a moving book is.

If the spread regime ever needs re-testing on this bot, the prerequisite is
recording the book spread at fill time. Nothing stores it today.

## The place/cancel churn: expected net is floored on the wrong branch

Traced 2026-08-19 from the symptom the operator kept seeing as "N slots not
working". It is not a slot-accounting problem.

**Symptom.** 19 markets bound, **2 orders resting at the venue**. Of 143 orders
since 12:30, 141 cancelled and 2 open. Actions ran 87 `PLACE_POST_ONLY
reward_positive_candidate` against 80 `CANCEL side_no_longer_desired`, 13-17
placements per market per hour. One market, unchanged price, no blockers:

```
13:17:31 CANCEL bid       13:18:25 PLACE bid 0.45
13:20:41 CANCEL bid       13:21:23 PLACE bid 0.45
13:24:04 CANCEL bid       13:24:54 PLACE bid 0.45
```

Reward pays for **resting**, so this earns almost nothing while holding slots.

**The chain.** `live_commissioning` sets the value the reconciler gates on:

```python
expected = economics.expected_net_value
if not economics.should_quote:
    expected = max(expected, 1e-9)      # floored ONLY on the "do not quote" branch
state.expected_net_by_side[side] = expected
```

and `reconciler.plan_market` wants a side only when
`expected_net_by_side[side] > 0`.

The floor exists precisely to stop this flicker -- the comment beside it
records an earlier round of it ("zero orders resting at the venue across
repeated samples, while 27 markets were quoted in 15 minutes"). But it is
applied when the gate says **not** to quote. When the gate says quote, the raw
value passes through, and it can sit at or below zero. So a side the gate wants
fails the reconciler's strict `> 0`, gets cancelled, and is re-placed at the
same price on the next pass.

`net={'bid': 0.0}` in the log is not zero data: `round(1e-9, 6)` is `0.0`.

**Reproduced**, not just read: setting `expected_net_by_side['bid'] = 0.0` on a
resting side yields exactly the bare `side_no_longer_desired` production shows.

**First hypothesis was wrong**, which is why the reason string now carries a
cause. The stressed-exit gate (`cross_exit` incomplete or over
`max_exit_loss`) looked like the obvious driver; tagging it showed the live
cancels arriving **bare**, meaning the side was dropped further up. Every
removal point now names itself: `expected_net_not_positive`,
`expected_net_not_finite`, `reward_ineligible`, `no_entry_price`,
`worst_loss_budget`, `exit_incomplete`, `exit_loss`.

**Not fixed here, deliberately.** The obvious change -- floor on both branches
-- would quote sides with a negative expected value, which is an economic
decision and not a bug fix. The two are inconsistent and which one is right is
the EV-gate question in `NEXT.md`. Do not "fix" this by flooring without
deciding that first.

### CORRECTION, same day: the live cause is `reward_ineligible`, not this

The heading above is what the code review implied and what a unit test
reproduces. It is **not** what the running bot reports. First window after the
tagging deployed:

```
9  reward_positive_candidate                    (places)
8  resting_price_left_the_touch                 (cancels, touch moved)
4  side_no_longer_desired:reward_ineligible     (cancels)
0  side_no_longer_desired:expected_net_not_positive
```

So the side is being dropped because it stopped being **reward-eligible** --
`frame.reward_eligible` no longer contains it and the
`reward_eligibility_dwell_seconds` grace has expired. That is an economically
sound cancel: a side outside the reward band earns nothing by resting.

Two caveats on this sample. It is ~8 minutes and 4 cancels, against the 80
cancels in 30 minutes that prompted the investigation, so it may simply be a
quieter regime rather than a different cause. And 8 orders were resting at the
venue during it, versus 2 before -- the churn was much milder in this window.

**The expected-net inconsistency above is real and worth fixing regardless**;
it is just not proven to be what was firing. Re-read the cause counts over a
full busy session before acting on either. That is what the tagging is for --
this is the third hypothesis in this investigation that the data has moved, and
the first two were mine.

## The churn's real defect: a missing dict key read as "uneconomic"

Traced 2026-08-19, after three wrong hypotheses. Baseline: **mean resting
fraction 57.3%** across 179 sides (`scripts/resting_fraction.py`) -- 43% of
bound time earning nothing, with the same side re-placed at the same price
every 2-3 minutes.

`clear_economics()` wipes `expected_net_by_side` before every pass. A side the
runner skipped that pass -- an overlapping book, or any early return out of
`_evaluate_rewards` -- therefore has **no entry**. The reconciler read it as
`.get(side, 0.0)`, turning "we did not look" into "this is worthless", and
cancelled the resting order.

That defeated the dwell. `reward_eligibility_dwell_seconds` is 90s and exists
so a side that momentarily stops qualifying keeps its quote instead of
flapping. A side inside that grace was killed anyway, by a dictionary default.

**Fixed:** absence is skipped and left to the dwell; a value that was actually
computed and came out at or below zero still drops the side.

### The trap in fixing it, and why the first attempt was a safety hole

Absence meant two different things. With `enforce_reward_cost_gate` **False**
(the live default -- the gate is advisory) the value is written for every
evaluated side, so absence means "not evaluated". With the gate **enforced**, a
refused side got no entry at all, so absence meant "the gate refused". Skipping
absence therefore placed an order the gate had forbidden -- caught by
`test_sub_dollar_reward_is_evaluated_but_risk_can_block_order`.

The fix is to stop overloading absence: an enforced refusal now records
`min(expected_net_value, 0.0)` explicitly, so a missing key means exactly one
thing. Mutation-checked both ways -- restoring `.get(side, 0.0)` cancels the
resting quote, and dropping the explicit refusal emits the forbidden place.

### Three hypotheses died first; do not re-run them

1. **Stressed-exit gate.** Disproved by tagging -- live cancels arrived bare.
2. **`expected_net` floored on the wrong branch.** The floor sits on the
   `not should_quote` branch, which looked like two components disagreeing.
   They cannot disagree: `OpportunityEconomics.should_quote` **is**
   `expected_net_value > 0`, the same expression. "Flooring both branches" is a
   no-op -- it changed nothing and moved no test.
3. **`reward_ineligible` as the dominant cause.** True in one window, zero in
   the next. The mix moves with the regime.

**Validate with `resting_fraction.py`, not with cause counts.** 57.3% is the
number to beat; reward accrues only while an order rests.

## A third of the funnel drains through a counter that does not exist

Measured 2026-08-19T23:31Z, by reproducing the selector loop with the missing
counter added. The arithmetic closes exactly, so this is not an estimate:

```
in horizon (executable)        43
carrying rewards               43
- unsupported program           0   COUNTED
- no applicable period         15   *** UNCOUNTED ***
- book too tight to enter (2c) 27   COUNTED
= candidates for a book read    1        43 - 0 - 15 - 27 = 1
```

`_applicable_period` returning `None` hits a bare `continue` in
`market_selector.py` with no counter increment. `eligible_pregame_markets` is
`len(candidates)`, so those 15 markets vanish between "carrying rewards" and
"eligible pregame" with nothing reported. The funnel `usable_markets.py` prints
therefore does not sum, and it was printed as though it did.

**Every one of the 15 had only `live` periods.** 45 period rows across the 15,
all named `live`. These are markets whose reward window has rolled into the live
phase, which the pregame executor may not quote -- invariant 39, and section 4:
only `day_of` has a provable per-market clock. **The drop is correct.** What was
wrong is that it was invisible.

### It removes the WIDE books preferentially

Measured in the same window: 11 of the 43 in-horizon markets had a catalog
spread >= 2c and would have passed the spread filter. The selector ranked
**one**. So ten of the eleven wide books were in the period-dropped group.

That is mechanically coherent rather than coincidental. Near kickoff the book
widens *and* the reward period flips to live, so "wide enough to quote" and
"too late to quote" arrive together. The markets the spread filter would have
admitted are exactly the ones the period gate has already taken.

### Why the miscount matters more than the count

The verdict blamed the spread filter, because that is the counter that exists:

> `64% of rewarded markets have books tighter than 0.02 ... This is the dominant
> filter ... It is also the lever if you want more markets`

Two different facts with two different levers, and the wrong one was named:

- **tight book** -- the market is unattractive; `minimum_entry_spread` is the
  lever, and this file already measures that as weak (the rejects sit 3-20x
  under the threshold, not just under it).
- **live-only period** -- the market is fine and this executor cannot address
  it at all. The lever is the lifecycle, not a spread constant. Section 4
  measures the live pool at **$1,600 against day_of's $200**, and section 6d
  attributes 40% of paid reward to live-associated markets against 5-11%
  clearly pregame.

So the uncounted drain points straight at the largest open strategic question in
the project, and the diagnostic was hiding it behind a filter that is not the
binding one at that hour.

**Fixed in the tool, not in the runner.** `usable_markets.py` now derives the
count by subtraction and names whichever drain dominates. Adding a real counter
to `RewardMarketSelection` is a runner change and wants a restart, so it is
queued rather than done -- see NEXT.

**Do not read this as "the bot is losing markets".** It is refusing markets it
is not authorized to quote, correctly. The finding is about what the operator
can see, and about which lever the evidence actually points at.

### Correction to "The 2c spread filter is a much weaker lever" (2026-08-19T03:25Z)

That entry's **counts** came from the selector and stand. Its **characterisation
of the rejects** -- "nearly all were `tec-mlb-*champ-2026-09-27-*` ... a
different kind of market that the strategy does not want" -- came from the
`--rejects` listing, which walked the whole catalog with no horizon filter and
printed markets that were never candidates (BUGFIXES, same date). At 23:29Z the
in-horizon set contained **zero** `tec-mlb-*` rows; it was that evening's real
games.

The entry's conclusion may still hold -- the rejected in-horizon spreads that
night were genuinely far under the threshold, not near-misses -- but the reason
given for it was measured wrong, and "the rejects are futures we do not want" is
not supported. Re-measure with the fixed tool before relying on it.

### A latent bug found while reading this path

`_applicable_period` hardcodes `market.minimum_trade_qty > 1` while
`_catalog_market_is_executable` uses `minimum_trade_qty <= self.policy.quote_size`,
with a comment saying it "scales with quote size rather than assuming a single
contract". `_applicable_period` is a `@staticmethod` and cannot see the policy,
so it was never updated when quote size went 1 -> 5. A market whose venue
minimum is between 2 and 5 would pass the first gate and be silently dropped by
the second.

**Not firing today**: every one of the 43 in-horizon markets reports
`minimum_trade_qty` 0.01. Recorded because it is the "gate in two places"
pattern `BUGFIXES.md` says has already cost a deploy cycle twice, and because
the drop would be invisible for the same reason the live-period drop was.


## The loss is in FAST pairs, not slow ones. Two of my own claims withdrawn

Measured 2026-08-22 on clean data only -- fills from 2026-08-18 onward, after
the `isAggressor` fix reached production at 2026-08-17T13:20Z -- and restricted
to markets whose legs BALANCE, so `paid` really is the pair cost and the
allocation error described below cannot arise.

```
bucket        markets   pairs   total P&L   per pair
0-300s             67     481      -27.68    -0.0575   <- 40% of all loss
300-900s           61     362      -13.01    -0.0359
900-1800s          40     289       -7.47    -0.0258   <- best
1800-3600s         25     291       -9.08    -0.0312
3600s+              9     140      -11.11    -0.0793
                                   ------
                                   -68.34
```

The U-shape survives clean data: 900-1800s is the floor, both tails are worse.
So `max_inventory_age_seconds` at 900 remains right, and shortening it is still
withdrawn.

**But the actionable bucket is the FAST one, not the slow one.** 481 pairs
completing inside five minutes account for 40% of the loss at -5.75c each. Both
legs filling that quickly means the book ran through both quotes -- being run
over in a moving market, twice, not failing to capture a spread.

### Two claims of mine from the same session, withdrawn

**1. "41% of losses sit in the 3600s+ bucket."** That ran over all history from
2026-08-14. Before the `isAggressor` fix roughly 60% of fills were never
recorded and the missing ones were overwhelmingly the passive CLOSING leg, so
pairs *appeared* to take hours because their completion was absent from the
ledger. On clean data the bucket is 140 pairs and **16%**. The share by day
shows the artefact plainly:

```
day        >1h share
08-16          66.7%      broken fill recording
08-17          50.7%      fix lands mid-day
08-18           1.9%      <- collapses here
08-19           6.1%
08-20           7.7%
08-21           5.4%
```

**2. "`max_early_exit_loss_usd` 4 -> 8 is independently the highest-value change
available."** It is not. The cap went live 2026-08-20T22:39Z and 08-21 shows
5.4% against 7.7% and 6.1% before it -- no measurable effect, because the
problem it targets had already been solved on 08-18 by the fill-recording fix.
The change is still correct for coherence with quote size (a $4 cap sits below
the median crossed loss at ten contracts) but it is not a lever on P&L.

**The method error worth keeping:** the first version of this analysis
allocated every fill's cost to the matched pairs, so any market with unbalanced
legs inflated the loss -- it summed to -135.92 against `accounting.py`'s -47.24
for the same period. Restricting to balanced-leg markets removes the bias
rather than papering over it. Do not bucket P&L without checking that the legs
balance.

### Where this points instead

Fast pairs are a book-stability problem, which is NEXT item 2 -- previously a
hypothesis from two markets and a resting-fraction argument. It now has a P&L
argument too: the markets whose touch moves fastest are both the ones we cannot
rest in AND the ones that run through us. Ranking selection on stability
alongside projected reward is the untried lever with the most support.

It still needs the touch-move rate correlated against per-market pair P&L
across a full slate before any code changes. Two measurements agreeing on a
mechanism is not the same as having measured the mechanism.

## Holding time is NOT monotonic on our own data. It is a U

Measured 2026-08-22 across **2,200 completed pairs** of this bot's own fills,
after the operator ruled that maker-lab evidence may not settle questions our
own ledger can answer (CLAUDE.md rule 4).

```
bucket        markets   pairs   total P&L   per pair
0-300s             91     591      -31.12    -0.0526
300-900s           77     420      -16.21    -0.0386
900-1800s          50     329      -10.34    -0.0315   <- BEST
1800-3600s         31     360      -22.50    -0.0625
3600s+             58     500      -55.75    -0.1115   <- worst
```

**This contradicts the entry "Before relaxing the 900s clock, read this: P&L
degrades monotonically with holding time."** That table reported +23.8c per
pair under 300s and a clean monotonic decline. It was measured on **2026-08-17
fills only**, with **n=11** in the top bucket. Across 591 pairs the same bucket
is **-5.26c**. The monotonicity was small-sample noise.

The real shape is a U with its floor around 900-1800s, and it has a mechanism:

* **Very fast pairs are bad** because both legs filling quickly means the book
  was moving through us -- we were run over on both sides rather than capturing
  anything.
* **Very slow pairs are worst** because the market drifts far between the legs.
  Section "Matched-pair P&L is leg-to-leg market movement" already measured
  that distribution as bimodal at roughly +/-0.4, which is drift, not spread.

### Two decisions follow, and they point opposite ways

**Do NOT shorten `max_inventory_age_seconds`.** It sits at 900, right at the
floor of the U. Shortening it moves pairs from the best bucket into worse ones.
This was proposed in session on the strength of the superseded entry above and
is withdrawn.

**The lever is the 3600s+ bucket: 500 pairs, -$55.75, 41% of all losses.**
Those are pairs that SURVIVED the 900s clock -- the clock fires, the cross is
refused as too expensive, and the position rides to the kickoff flatten. So the
thing to attack is not the clock but what happens when it fires.

`max_early_exit_loss_usd` was doubled 4 -> 8 on 2026-08-20 to keep pace with
quote size. On this data that change is independently the highest-value one
available: it lets crosses clear that previously rode. Worth measuring
specifically -- if the 3600s+ bucket shrinks over the next week, that is the
mechanism confirmed.

### Why the maker-lab numbers were wrong here

They ran a different system: 10-25 contracts, thirty markets, the live period
included, and `FLATTEN_NEVER_AT_A_LOSS` on. A basis floor changes which
positions complete at all -- they documented it themselves as "79% losers BY
CONSTRUCTION" -- so their completion-time distribution measures that floor, not
the physics of holding inventory. Our per-contract cost is about 6x their worst
bucket, which is the same warning from a different angle.

## Tight books are tight BECAUSE they are deep. That is what the 2c filter catches

Measured 2026-08-20T22:0xZ, testing an operator proposal: rather than skipping
1c-spread markets, quote one tick behind on both sides. That makes our own two
quotes 3c apart, so a matched pair pays 3c rather than 2c, and being behind the
touch makes a fill less likely. The reasoning is sound and the arithmetic is
right -- on a market currently earning nothing it is better on two axes.

**It fails on a third, and the third is decisive.** Reward score accumulates
outward from the touch and stops once cumulative depth reaches `targetSize`;
orders past that point score nothing. Depth on the 1c rejects:

```
market                    spread   targetSize   depth at BEST BID   inside cutoff?
atp-alamag-colwon         0.0100       15,000            34,364          no
atp-camcar-moeech         0.0100       15,000            36,163          no
atp-zizber-andpel         0.0100       15,000           166,752          no
lol-dog-mvu               0.0100       15,000            93,541          no
itfwo-monbarsohpar        0.0100        7,500            18,068          no
mlb-laa-hou               0.0050       25,000           914,201          no
```

**The depth at the single best price already exceeds targetSize** -- 2x on the
ATP markets, 37x on the MLB one. The cutoff is consumed inside the touch level
itself, so one tick back scores exactly zero. Ten of ten sampled were over.

Even AT the touch these are worthless: 5 contracts against 34,364 is a 0.015%
share, against 914,201 it is 0.0005%.

### The mechanism worth remembering

A book is 1c wide **because** it is heavily quoted. Spread and depth are the
same fact seen from two sides, so `minimum_entry_spread` is a better proxy than
its name suggests: it is not really refusing "no room to trade", it is refusing
"our size is a rounding error here". That is exactly section 6h -- thin books
make one contract a real fraction, deep books make it nothing, and `atp` and
`mlb` paid one cent each.

### What would make the proposal work, and it is worth watching for

A market that is tight **and thin** -- cumulative depth at the touch BELOW
`targetSize`. Then one tick back stays inside the cutoff, earns ~30% of touch
score (discount 0.30 typical) on a slot earning zero today, and the 3c pair
economics hold. The proposal is not wrong; it needs a book that does not exist
in this sample.

**The cheap test if it comes up again:** for each tight reject, compare
cumulative bid depth at the touch against the period's `targetSize`. Inside
means the idea is live for that market; outside means quoting it earns nothing
wherever it rests. Do not re-argue this from spreads alone -- the spread does
not carry the information that decides it.

### This also closes WATCHLIST 9

The question was whether the 1c rejects are near-misses worth admitting. They
cluster at exactly 0.0100, which looked like a near-miss on price, and are
2x-37x over target on depth, which is not near anything. Lowering
`minimum_entry_spread` would admit markets that cannot pay at any price we
would rest at.

## The live-only period mix is TIME-VARYING, not a program change

> **CORRECTED 2026-08-20T21:49Z, six hours after it was written.** The heading
> below read "The venue moved the leagues that pay us to live-only. That is the
> decline", and that conclusion is **withdrawn**. It was drawn from two
> readings taken nine hours apart on the same day, both of which happened to
> fall in the same regime.
>
> A third reading at 21:49Z shows the opposite:
>
> ```
> at 14:12Z   setkameua [live] x22          0 day_of
> at 21:49Z   setkameua [day_of,live] x16   31 of 31 in-horizon markets quotable
> ```
>
> `day_of` came back on its own. So the leagues were never "moved"; the period
> mix varies through the day, and a pregame executor's addressable universe
> varies with it. **The cause of the order-flow decline is therefore NOT
> established** -- what is established is that the decline is real (the
> measurements below stand) and that this explanation for it does not.
>
> The failure mode is worth naming because it is the third time this session:
> two samples agreeing is not evidence when both are drawn from the same
> unmeasured cycle. `scripts/period_mix.py` and a daily probe exist now so the
> next answer comes from a series rather than a snapshot.
>
> Everything below is kept because the MEASUREMENTS are sound and still need
> explaining. Only the interpretation was wrong.

## (withdrawn interpretation) The venue moved the leagues that pay us to live-only

Measured 2026-08-20T14:2xZ after the operator asked why order flow had dropped.
This is the largest single change to this bot's addressable universe since it
started, and it is not ours.

### What the bot places in, by league and UTC day

```
             itfme   itfwo   setkameua   setkamemd
08-17          999     933         385         197
08-18          594     376         349         174
08-19          526     489         312           -
08-20            0       0         278          22
```

ITF was the number one and two league by volume for three days and is now zero.

### Why: the reward programs changed underneath us

Periods offered by the 91 in-horizon moneylines, read live:

```
ONLY [live]                    [day_of, live]
  setkameua   x22                lol       x10
  itfwo       x10                modus      x6
  setkawoua    x8                atp        x5
  wtt          x7                cs2        x4
  itfme        x6                valorant   x1
  setkamecz    x6                wta        x1
  setkamemd    x5
```

Now put that against section 6h, which measured where every reward dollar this
account has earned actually came from:

```
setkameua  $20.59  29.0%     <- live-only now
setkamemd  $15.11  21.3%     <- live-only now
itfme      $12.94  18.2%     <- live-only now
itfwo       $8.63  12.2%     <- live-only now
wtt         $5.27   7.4%     <- live-only now
                   ------
                   93% of all reward earned

atp         $0.01            <- still carries day_of
mlb         $0.01            <- still carries day_of
```

**The venue has left `day_of` on the deep, liquid books that pay this bot
essentially nothing, and moved the thin books it actually earns in to `live`**
-- the one period a pregame executor may not quote (invariant 39: only `day_of`
has a provable per-market clock).

### The measured consequence

Median per selection scan, by UTC day:

```
day        rewarded  ranked  ranked/rewarded
08-16          113      38       33.6%
08-17           83      17       20.5%
08-18           54       7       13.0%
08-19           53       6       11.3%
08-20           93       1        1.1%
```

Conversion collapsed 33.6% -> 1.1% **while supply nearly doubled** on the last
day, so this is not a quiet slate. Same-hour comparison removes time of day as
an explanation entirely:

```
hour     08-17      08-19      08-20
09:00   135->23    142->21    108->0
10:00   104->19    134->21    104->0
13:00    98->18    126->24     94->3
14:00    95->14    111->17     96->4
```

At 09:00 with 108 rewarded markets available the bot ranked **zero**.

The gradual shape of the decline -- 38, 17, 7, 6, 1 -- suggests leagues
converting one at a time rather than a single switch being thrown.

### What this does and does not mean

**Not a bug.** Every filter behaved correctly; the bot is refusing markets it
is not authorized to quote. Nothing here is fixable by tuning
`minimum_entry_spread`, and this is the second measurement in two days pointing
at the lifecycle rather than the spread.

**It reframes the strategic question in NEXT.** "Should we implement the live
period?" was an optimisation about an 8x pool. It is now closer to an
existential question about whether a pregame-only executor has an addressable
universe at all: the leagues left to it are the ones already measured as paying
one cent.

**Unknown, and it matters: whether this is permanent.** One reading cannot tell
a program change from a transient. Re-measure the period mix on 08-21 and
08-22 with the script pattern above before concluding anything irreversible.
If `day_of` returns to setka and itf, this was a blip and the bot recovers on
its own.

## Reward by day, and a correction to "the outages cost a full day"

> **CORRECTED 2026-08-20T22:2xZ.** The entry below concluded 2026-08-18 earned
> **one cent** and that the outages therefore cost about $30. It has since
> settled at **$14.60**. The reasoning was explicitly wrong in a way worth
> keeping: it argued "the day has posted -- it has a row -- so this is not
> payout lag." A row is not a settled total. PENDING converts days later, and
> the same read that showed 08-18 at $0.01 showed 08-17 at $0.00 PAID with
> $33.83 PENDING; 08-17 has since settled at $34.60.
>
> **Rule: never read a recent day's reward as final.** Two readings days apart,
> or none. This is the second time a single reward reading has produced a wrong
> conclusion here -- the first was reading PAID's `date` as a payment date.

### Settled reward by day, read 2026-08-20T22:2xZ

```
08-14   $5.48
08-15  $28.46
08-16  $22.39
08-17  $34.60    <- peak
08-18  $14.60
08-19  $14.99
                  bot-era PAID total $120.52, PENDING $0
```

**Reward tracks quotable breadth, not effort.** Against the selection medians
and placement counts measured the same day:

```
day     markets quoted   median ranked   reward
08-17        193              17         $34.60
08-18        148               7         $14.60
08-19        128               6         $14.99
08-20         32 (partial)      1         not posted
```

Reward is paid per second an order rests, so it follows the number of markets
the bot can actually quote almost linearly. The halving since 08-17 is the same
decline recorded above as an order-flow collapse -- and its cause is still NOT
established, since the live-only explanation was withdrawn. See WATCHLIST 10.

**What this does settle:** the outage cost on 08-18 was much smaller than the
~$30 claimed. That day earned $14.60 against $14.99 the next day, on a day with
ten restarts. So availability mattered less than breadth, which points the
"what is worth fixing" question away from uptime and towards supply.

## (superseded) What the 2026-08-18 outages cost: a full day's reward, measured

Read from the venue 2026-08-19T20:45Z, by the API's earning date. The table was
originally described as UTC; those labels are ET, as corrected above:

```
2026-08-16   PAID 20.49  PENDING  1.90    72 rows
2026-08-17   PAID  0.00  PENDING 33.83   123 rows   <- best day so far
2026-08-18   PAID  0.01  PENDING  0.00     1 row    <- the bad day
```

**2026-08-18 earned one cent.** That day the bot restarted ten times, spent
hours with rotation blocked quoting 1-5 markets, and churned hard enough that
only 2 orders rested at the venue against 141 of 143 cancelled.

The day has **posted** -- it has a row -- so this is not payout lag. Compare
against 08-17's 33.83 from 123 rows: the outages and the churn cost roughly a
full day of reward, about **$30**.

This is the number to weigh availability work against. An hour of downtime is
not free and is not small: reward accrues only while an order rests, so a bot
that is up but quoting nothing earns exactly as much as one that is down.

Caveat kept deliberately: a day can post partially and convert PENDING later
(08-16 still carries 1.90). 08-18 showing PAID 0.01 with no pending makes a
late correction unlikely, but re-read before quoting this as final.

---

## "Bound but not quoting" has no diagnosis in the log, and one real cause

Measured 2026-08-23T00:18-00:22Z, on a healthy runner (up 5h18m, `f41566a`).

**The state.** 20 slots, 17 markets bound, and **1 working order at the
venue.** Every fault indicator was clean: rotation unblocked, 0 `UNKNOWN_ABSENT`,
0 unresolved intents, 0 positions, no risk stops, retirements releasing
normally. The whole slate had rotated 18 minutes earlier
(`[top-up] released=19 added=19` at 00:00:39Z), and 16 of the 17 new markets
had placed **no order at all** in those 18 minutes.

`usable_markets.py` at the same moment reported **24 viable, most both sides**.
So the selector and the reconciler disagreed about 20-odd markets, and nothing
recorded why.

**Why the log cannot answer it.** The runner logs `[action]` only when it
mutates. A sweep that plans and decides to do nothing writes nothing, so an
idle bound slot is indistinguishable from a healthy one that happens to have
nothing to do. This is the same blind spot twice fixed elsewhere and never
fixed here: `usable_markets.py` exists because `[selection]`'s five counters
could not separate "nothing is eligible" from "markets are being lost", and
`book-stale-why` was added because `book_stale` at attempt=1 could not say
whether the refresh fired, landed stale, or was never replanned on.

**What the causes actually are.** Two, established by fetching the live books
for the 17 bound markets directly:

1. `reward_ineligible` — dominant, and economic rather than faulty. Session
   totals for cancel reasons:

   ```
   2340  side_no_longer_desired:reward_ineligible
      5  side_no_longer_desired:expected_net_not_positive
      1  side_no_longer_desired:exit_incomplete
   ```

   This is the depth mechanism from WATCHLIST 9 in its ordinary form: the
   reward score is computed at the *backed-off* entry price, not at the touch,
   and `targetSize` is often reached before the quote gets there. Three of the
   17 bound markets had touch depth alone at or past the 7,500 target
   (`saualh-alejon` 15,700 on the bid; `yukmat-jiapan` 10,287 on the ask;
   `grilomkritam-taiichmasima` 11,204 on the bid), so only the touch scores at
   all. **A bot that is bound and not quoting is usually correct**, and this is
   the reason.

2. `price_too_extreme` — a real defect, recorded in `BUGFIXES.md` 2026-08-23.
   The floor lives only in `reconciler.py`, so the selector binds books it can
   never quote. **`price_too_extreme` occurs zero times in the entire log**,
   because the cause only reaches a log line via
   `side_no_longer_desired:<cause>`, which needs a working order to cancel —
   and this gate fires before any order exists.

**Do not read "1 of 20 resting" as broken.** Resting fraction over the same
session was 62.8% across 44 sides (down from the 85.6% of 2026-08-19, worth
watching but not a fault). An instant slot count cannot tell a wholesale slate
rotation from a stall — CLAUDE.md already says this about absent counts, and
this is the same trap with a different counter.

**The measurement to build, if this comes up again.** A per-sweep record of
`drop_cause` by market and side. Everything above took ~35 venue calls and two
throwaway scripts to establish, and it would have been one grep.

### Correction, same night: the cause above is WITHDRAWN, and the real number is worse

Written 2026-08-23T01:00Z, against the section immediately above.

That section named `reward_ineligible` as the dominant reason bound slots sat
idle. **Measured properly, that is wrong.** Replicating the runner's own gates
in order, against live books, for all 19 bound markets at 00:48-00:52Z:

```
applicable period      day_of for 19/19
reward score at the price it would actually rest at
                       32/38 sides ELIGIBLE (6 more eligible but under the
                       0.10 price floor)
stressed exit          38/38 complete, worst -7.30 against the 8.00 cap
worst-loss budget      nowhere near 90 / 340
```

So the economics are not refusing these markets, and the WATCHLIST-9 depth
mechanism is not what was holding the slots.

**But the replication is also wrong, and that is the finding.** It predicts ~32
resting sides. Reconstructing concurrent working orders from the ledger, the
bot's actual peak across the whole of 2026-08-22 was **11 orders**, with a
typical 4-9:

```
17:40-21:20    8-11 orders    the day's best stretch
21:30-23:50    2-5
00:00-00:20    0              slate rotation
00:30-00:50    1 -> 2         recovering
```

A replication that over-predicts by 3x against the bot's own best hour is
missing a gate, so **neither number can be used as evidence of a fault** --
which is exactly the mistake this correction is undoing. The 0-2 stretch after
00:00 is a decline into the overnight ITF slate plus a rotation trough, with
the same recovery shape the 16:00 and 19:00 rotations show.

**What is actually established, and it is not about tonight.** Nothing is
faulted -- no stop, no unresolved intent, no absent order, nothing retired that
should not be.

> **RETRACTED the same night.** This paragraph originally read "the bot rests
> orders in at most half its twenty slots even at its best, and usually a
> third... the standing gap worth measuring". That divides by **20 slots**, the
> denominator `WATCHLIST.md` item 3 forbids in writing ("compare against
> candidate count, not against 20, or you will repeat the mistake of reading
> availability as starvation") and that the method note above already warns
> dominates the answer. Measured span-weighted per day the bot runs at
> 46.9-71.8%, and 2026-08-22 is 53.8% -- ordinary. See "Resting fraction: the
> 85.6% is a filtered statistic" below for the measurement that replaces this.

**Method note.** Three candidate causes were proposed and killed by measurement
in one session -- reward depth, period selection, and a WebSocket collapse (the
`book_stale` rate per market is flat at 1.0-2.0/min all session, identical
across the rotation and across the hours the bot WAS placing 45-90 orders/hr).
Each was plausible from the code and none survived contact with the ledger.
The per-sweep `drop_cause` record proposed above would have settled all three
in one grep, and is now the first thing to build.

---

## Resting fraction: the 85.6% is a filtered statistic, the real number is ~54%, and it does not predict reward

Measured 2026-08-23T01:15Z, after three consecutive wrong conclusions in one
session about slot occupancy. This entry exists so the fourth one does not
happen.

### What I got wrong, and what caught it

I measured time-weighted slot occupancy at **16% of 20 slots** and called it
"the standing problem". `WATCHLIST.md` item 3 already forbids that number in
writing:

> Note a genuine dip is expected overnight when fewer games exist; **compare
> against candidate count, not against 20, or you will repeat the mistake of
> reading availability as starvation.**

And `FINDINGS` already recorded the general form:

> **Denominator choice dominated the answer:** the same data reads 30% or 63%
> depending on it, and 30% would have justified work that 63% does not.

Both were in the repo before I started. Reading them first would have saved
about eighty venue calls.

### The measurement that stands

Span-weighted: total resting time over total bound time, every side weighted by
its own span, **non-overlapping UTC days**. No coverage cutoff -- the cutoff is
what produces the artifact below.

```
UTC day         rest   sides  markets  side-hrs   reward
2026-08-14     15.3%      68       46      76.2     5.48
2026-08-15     71.8%     182      106     334.9    28.46
2026-08-16     53.6%     185      104     305.4    22.39
2026-08-17     46.9%     348      193     442.9    34.60   <- BEST reward day
2026-08-18     63.1%     271      149     273.1    14.60
2026-08-19     64.2%     228      128     236.0    15.13   churn fix deployed
2026-08-20     67.4%     156       82     148.8    22.21   size 5 -> 10
2026-08-21     59.7%     283      154     215.4    29.54   (28.43 still PENDING)
2026-08-22     53.8%     264      154     264.5    not posted
```

**Two things fall out, and both matter.**

### 1. `85.6%` is not the bot's resting fraction

`CLAUDE.md` and `NEXT.md` both carry 85.6% (and 84.7% "confirmed over a full
session") as the post-2026-08-19 state. Those came from
`resting_fraction.py --coverage 0.8` over 3h41m and 5h29m windows. That flag
keeps only sides whose market was bound for >=80% of the window -- and a market
bound that long is, by construction, one that kept quoting. Markets that bound,
failed to quote, and rotated out are **excluded from the average**.

The filter is not wrong for its purpose; treating its output as "the share of
time the bot rests" is. Measured span-weighted over the same period, 08-19 reads
**64.2%**, and the churn fix moved the daily number 63.1% -> 64.2% -> 67.4% --
a real but modest gain, well inside the 46.9-71.8% day-to-day range.

Confirming this: at coverage 0.8 over tonight's 372-minute window, **all 49
sides are excluded** and the script prints no mean at all, because nothing stays
bound for 80% of six hours when markets rotate every ~90 minutes. The 85.6%
was only computable because that window was short enough.

### 2. Resting fraction does not predict daily reward

Across eight bot-era days the relationship is absent, and where it appears it
runs the wrong way:

- **best reward day (08-17, $34.60) had the LOWEST resting fraction** of any
  mature day, 46.9%
- highest resting fraction (08-20, 67.4%) earned $22.21
- 08-18 and 08-19 rest 63-64% and earn 14.60 / 15.13; 08-20 and 08-21 rest
  67% / 60% and earn 22.21 / 29.54

Reward per resting hour swings 2.7x (0.085 to 0.230 $/hr) with no visible
relation to the fraction. That is expected once stated: reward is
`your_score / total_score x pool`, so resting longer earns nothing extra when
the pool is small or the competition is deep -- which is the same depth
mechanism WATCHLIST 9 measured.

**This bears directly on `NEXT.md` item 00b**, which calls resting fraction
"the largest continuous drag on earnings". That claim was made before any
outcome data existed. It is now measurable and **not supported**: eight days of
paired resting/reward show no relationship.

**Bounds on the claim, deliberately kept.** Eight days, one partial, with
confounds (08-20 changed size mid-day; slates and pool sizes differ daily).
This is enough to retire "resting fraction is the binding constraint" as an
assumption. It is **not** enough to conclude resting time does not matter --
only that it is not what separates a $15 day from a $35 one.

**The measurement that would settle it:** reward per market-hour-of-resting,
bucketed by pool size and by depth at the touch. If that is flat, resting is
genuinely not the lever and selection is. `scripts/rewards_report.py` gives the
numerator per market; the span data above gives the denominator.

### Tonight, for the record

2026-08-22 reads 53.8%, squarely inside the range every day since 08-15 has
run at. Nothing broke tonight. The 0-2 resting orders after the 00:00Z
rotation is an overnight slate plus a rotation trough, and 08-23 is 2.5
side-hours of data -- too little to read at all.

---

## The size doubling DID work: 1.89x per resting hour against a predicted 2.00x

Measured 2026-08-23T01:25Z. Corrects the reading given earlier the same night
("doubling the contracts did not double the reward"), which was true of the
daily total and wrong about the mechanism.

### The rule, re-checked at the source

`https://docs.polymarket.us/incentives/liquidity`, read 2026-08-23, agrees with
what `docs/API_CONTRACTS.md` already recorded and with `rewards.py`:

- `Score = discountFactor ^ (ticks from best) x order size` -- **linear in size**
- the exchange walks from the best price outward accumulating orders until
  `targetSize` is reached; **orders beyond that range score zero**, however
  close to the touch
- payout is **purely proportional to your share of total score**, with each
  snapshot normalising bid and ask independently, per second
- **no per-account cap** ("no ceiling on how much size you can have scored")
- rewards under **$1.00 are not paid out**

Nothing has changed, and nothing in the rule penalises size. Linear score with
no cap predicts that doubling quote size doubles reward per unit of resting
time, as long as our score stays small against the total -- which it does, with
7,500-15,000 contracts sitting at these touches against our 10.

### What actually happened

```
UTC day        size  rest hrs   reward   $/rest-hr
2026-08-15        5     240.4    28.46       0.118
2026-08-16        5     163.7    22.39       0.137
2026-08-17        5     207.5    34.60       0.167
2026-08-18        5     172.3    14.60       0.085
2026-08-19        5     151.4    15.13       0.100
2026-08-20    5->10     100.2    22.21       0.222   excluded, size changed mid-day
2026-08-21       10     128.6    29.54       0.230
2026-08-22       10     142.3       --   not posted

size 5   mean 0.121 $/rest-hr   (5 clean days)
size 10       0.230 $/rest-hr   (1 clean day)
ratio         1.89x              rule predicts 2.00x
```

**The rate nearly doubled, exactly as the rule says it should.** The daily
total looked flat only because resting hours fell over the same period:

```
08-15..08-19   187 order-hours/day
08-21..08-22   135 order-hours/day     -28%
```

08-21 still out-earned the size-5 daily mean (29.54 against 23.04, +28%). The
missing part of the 2x is entirely the lost resting time, not the size change.

**So the rollback question in `NEXT.md` has an answer:** the doubling is doing
what it was supposed to. Judge it on `$/rest-hr`, not on the daily total, and
do not roll it back on a flat daily figure.

### The question this opens, and a hypothesis with a name

Bound time roughly halved over the same window (side-hours bound: 08-17 442.9,
08-21 215.4, 08-22 264.5) on only ~20% fewer markets, so spans got shorter too.

**Candidate, untested:** `minimum_entry_price` (0.10) was added to the
reconciler on **2026-08-21** -- the same day the drop appears. It refuses sides
at extreme prices, and because the selector does not model it (see `BUGFIXES.md`
2026-08-23) those markets are still bound but never place. A market that never
places contributes nothing to resting hours while still consuming a slot, which
is the exact shape of this drop.

**This is a coincidence of dates, not a result.** It is written down so it gets
tested rather than assumed -- three hypotheses died by measurement earlier this
same night. **The measurement that would settle it:** count bound markets that
placed zero orders per day, 08-19 through 08-23. If that count jumps on 08-21,
the price floor is eating the slate and the selector fix pays for itself twice.

**Caveat on the headline, kept deliberately.** One clean day at size 10 against
five at size 5, and daily reward swings $14-$35 at constant size. 1.89x is
consistent with the rule; it is not yet independently established. 08-22 and
08-23 posting will make it three days.

---

## The 6h -> 2h horizon halved market supply, and that -- not the price floor -- is where the resting hours went

Measured 2026-08-23T01:35Z, from the `[selection]` counters already in the log.
Prompted by the operator remembering the change; it is the correct explanation
and it **withdraws the `minimum_entry_price` hypothesis** recorded an hour
earlier in this file.

`0b04c2b` "Stop selecting markets more than two hours from kickoff" narrowed
`selection_horizon_seconds` 21600 -> 7200, landing **2026-08-17T02:27Z**. The
effect on supply is large and immediate:

```
UTC day    scans   executable   viable      note
2026-08-16    78          113       36      6h horizon
2026-08-17   263           83       14      change lands 02:27Z
2026-08-18   204           54        7
2026-08-19   274           53        4
2026-08-20   270           61        2
2026-08-21   268           44        8
2026-08-22   275           51       10
```

`executable` (in horizon, supported) fell **113 -> ~50**, close to the 2/6
ratio the change implies. `viable` -- the count that actually maps to fillable
slots -- fell **36 to single digits**.

### This answers the question three separate investigations kept re-asking

**Median viable is 2-10 against 20 slots.** The bot is not failing to fill its
slots; on most days there is nothing to fill them with. Every "why is it only
quoting three markets" alarm since 08-17, including the one that consumed this
entire session, has this as its answer. `usable_markets.py` reports it live;
the per-day series above is the thing to check first.

### The price-floor hypothesis is withdrawn

The entry above proposed `minimum_entry_price` (added 08-21) as the cause of
the resting-hours drop, on a coincidence of dates. It does not survive: bound
side-hours peak on **08-17** (442.9) and decline through 08-18 (273.1), 08-19
(236.0) and 08-20 (148.8) -- **before** the floor existed -- then partially
recover on 08-21 (215.4) and 08-22 (264.5), which is the wrong direction. The
decline tracks the horizon change, not the floor.

The `minimum_entry_price` selector gap in `BUGFIXES.md` is still a real defect;
it is just not this one. Fourth hypothesis killed by measurement this session.

### What the two changes did together

```
horizon 6h -> 2h   resting hours   187 -> 135 order-hours/day   (-28%)
size    5  -> 10   reward rate     0.121 -> 0.230 $/rest-hr     (+89%)
```

They very nearly cancel on daily reward, which is why the size doubling looked
like it did nothing. **It did not do nothing; it was offset.**

### Open, and it is the live question

The horizon change was argued on projected reward -- the 120+ minute bucket at
a median 0.000022 against 0.000313 at 15-30 minutes -- and on distant slots
carrying fill risk for no reward. It was a reasonable call on that evidence.
**But it was made at size 5, before any outcome data existed, and it is now
testable against realised reward rather than projections.**

`selection_horizon_seconds` restores the old behaviour by config, not deploy.
**Do not change it on this entry alone.** Widening buys resting hours in the
window that was measured to pay least, and it buys fill risk with them -- and
trading loss now runs about -0.045/pair at any size (below), so more resting in
thin distant books could easily cost more than the reward it adds. The
measurement that would settle it is reward and trading P&L bucketed by
time-to-kickoff at size 10, which `scripts/resting_by_kickoff.py` already
produces for the numerator.

### Trading loss by ET reward day: aggregate per-pair cost stayed similar

```
day        pairs   trading   per pair
08-18        258    -10.95     -0.042
08-19        207    -10.78     -0.052
08-20        404    -17.99     -0.045   size changed mid-day
08-21        855    -30.52     -0.036
08-22        342    -24.19     -0.071
```

The two clean size-5 days aggregate to about **-0.0467 per pair**; the two
clean size-10 days aggregate to about **-0.0457 per pair**. That supports the
narrow claim that doubling size did not increase average loss per completed
pair, but the daily range (-0.036 to -0.071 at size 10) is not "flat".

Combined with current venue reward (PAID+PENDING), ET-day net if every pending
dollar pays is: 08-18 +3.65, 08-19 +4.35, 08-20 +4.22 and **08-21 +2.27**.
The old UTC join said 08-21 was -5.60 and the first negative size-10 day; that
conclusion was an artifact of mismatched calendars. August 22 was visibly
still posting during this correction -- pending rose from $9.22 to $25.63 --
so its net remains withheld until the earning rows settle.

---

## Testing the horizon change against realised reward: the premise does not hold up

Measured 2026-08-23T01:45Z, at the operator's prompting -- "we made that change
knowing 6 hours don't pay that much, can you check that". Checking it is the
point of this file, and the answer is not the one the change assumed.

### The evidence the change was made on, and the caveat attached to it

`0b04c2b` narrowed `selection_horizon_seconds` 21600 -> 7200 citing a median
**projected** reward of `0.000022` in the 120+ minute bucket against `0.000313`
at 15-30 minutes. Those numbers come from `44bfecd`, committed **nine minutes
earlier**, which says of them:

> Either the two measurements are not the same field, or one is wrong. Until
> that is resolved, **neither number should be used to justify a change to
> selection**, because the whole argument for reweighting rests on projections
> being comparable across buckets.

The 500x discrepancy that caveat describes has never been resolved. So the
change rests on a projection comparison this file had, minutes before, declared
unreliable. The action is not identical to the one `44bfecd` refused --
excluding distant markets is not reweighting toward near ones -- but the
evidence underneath is the same, and so is the objection.

### Realised reward, which we now have and did not then

Per-market reward from the venue (PAID+PENDING), against markets actually bound:

```
day        horizon  bound  earned  %earn  median   p25    p75  <$0.10   total
2026-08-15      6h    106      71    67%   0.060  0.020  0.260    55%   28.46
2026-08-16      6h    104      72    69%   0.110  0.040  0.340    43%   22.39
2026-08-17     mix    193     128    66%   0.090  0.040  0.230    54%   34.60
2026-08-18      2h    149      63    42%   0.020  0.010  0.080    78%   14.60
2026-08-19      2h    128      66    52%   0.040  0.010  0.150    61%   15.13
2026-08-20      2h     82      48    59%   0.165  0.040  0.580    42%   22.21   size 10
2026-08-21      2h    154      93    60%   0.110  0.040  0.520    44%   29.54   size 10
```

**The change predicted that cutting distant markets would RAISE the share of
markets that earn and RAISE the median, because what is removed is the
worthless tail.** Comparing like-for-like at quote size 5:

```
                        6h era (08-15,16)   2h era (08-18,19)
share of bound markets
  earning anything             68%                47%
median per-market reward      0.085              0.030
earning under $0.10            49%                70%
reward per resting hour       0.128              0.093
```

Every measure moved the wrong way. The 08-20/08-21 recovery is the size
doubling (score is linear in size), not the horizon.

### Why this is suggestive and not yet a verdict

**The two post-change days are the two worst operational days the bot has
had.** 08-18 restarted ten times; 08-19 ran the churn bug until the fix landed
mid-day. Attributing their weakness to the horizon rather than to those is not
safe, and two days against two days is a thin comparison however it is framed.

**And there is a genuine blind spot.** The change was argued on trading risk as
much as reward -- "a slot out there carries the full risk of being filled and
having to exit at a loss in exchange for approximately no reward". Clean
trading data starts **2026-08-18** (the `isAggressor` fix), which is *after* the
change, so the cost side of that argument cannot be compared before and after
at all. It may well have been right about fill risk even if it was wrong about
reward.

### What would settle it, and one correction

Run the 6h horizon for three days at size 10 and compare against 08-21/08-22/
08-23 at 2h and size 10, on **net** (reward minus trading), not on reward
alone. That is a real A/B and both sides are now measurable, which they were
not in August's first week.

**Correction to `0b04c2b`'s message:** it states the old behaviour is restorable
"by config rather than by deploy". It is not. `selection_horizon_seconds` is a
dataclass default in `market_selector.py` and is never passed from
`CommissioningConfig` or the command line, so changing it is a code edit, a
deploy and a restart. Wire it to the config before running the A/B, or the
experiment costs a deploy in each direction.

### The venue's rule says distance from kickoff does not reduce pay at all

Checked at `docs.polymarket.us/incentives/liquidity`, 2026-08-23T01:55Z, at the
operator's prompting. This is the part nobody checked before narrowing the
horizon, and it removes the mechanism the change assumed.

> **"Every second of the time period is equally weighted regardless of how much
> liquidity is on the book."**

The periods, each with **its own pool**:

```
early / pre-game    listing        -> 6h before event
day_of / pre-game   6h before      -> event start        <- the only one we quote
live                event start    -> settlement
daily               midnight -> midnight ET
```

**There is no time-decay within a period.** The `day_of` pool is spread evenly
across all 21,600 seconds of its six hours. An order five hours out earns
identically per second to the same order fifteen minutes out. The only quantity
that varies with time-to-kickoff is **competition** -- other makers' total score
-- because payout is purely `your_score / total_score` per second.

Two consequences, and they run against `0b04c2b`:

1. **Quoting only the last two hours makes us present for one third of the
   seconds the `day_of` pool pays across.** At the pool of 35 recorded in
   `docs/API_CONTRACTS.md`, roughly $11.67 of it is reachable by construction,
   whatever we do with the other settings.
2. **Early hours should pay BETTER per second, not worse.** The pool slice per
   second is fixed *regardless of book depth*, so an hour with few competing
   makers hands us a larger share of identical money. That is the opposite of
   the change's premise.

So "six hours don't pay as much" was never a statement about the rule. It was a
claim about competition, inferred from a projection that `44bfecd` had already
flagged as internally inconsistent by 500x.

**Three independent lines now agree** the narrowing was a mistake: the venue's
rule (no distance penalty, and a pool we are absent for two thirds of), realised
per-market reward (68% -> 47% earning, median 0.085 -> 0.030), and reward per
resting hour (0.128 -> 0.093).

**What still argues the other way, and it is not nothing.** The change was also
about fill risk -- distant thin books filling us and costing an exit. Clean
trading data begins 2026-08-18, after the change, so that half has never been
measured either way. Trading now runs about -0.045/pair, and more resting hours
means more pairs. The A/B must be judged on **net**, not reward.

**This also reopens `early`.** It is a separate pool, running from listing until
six hours out, that this bot has never touched -- excluded by invariant 39 for
want of a provable clock, not because it does not pay. Worth a look once the
`day_of` horizon question is settled; do not conflate the two.

#### Correcting the "one third of the pool" argument, written minutes earlier

The per-market statement is true and the portfolio conclusion drawn from it was
not. With a fixed twenty slots, a 2h horizon holds each market ~2h and a 6h
horizon holds it ~6h, so the narrow window covers roughly **three times as many
markets at one third the coverage each**. Total pool-seconds accessed is
approximately conserved. "We forfeit two thirds of the pool" is wrong as a
portfolio argument.

**The argument that survives is slot starvation, and it is stronger.** Supply
measured from the `[selection]` counters:

```
                    6h horizon    2h horizon
executable              113          ~50
viable (median)          36          2-10        against 20 slots
```

At 2h the bot **cannot fill its twenty slots** -- median viable is 2 to 10.
Slots sit empty, and an empty slot earns exactly nothing. At 6h there were 36
viable, comfortably more than capacity, so the ranking could choose and every
slot could be filled.

That is the mechanism, and it also explains the realised numbers without any
appeal to competition: reward per resting hour fell because resting hours fell,
and resting hours fell because there was nothing left to rest in.

So the two horizons are NOT "same seconds, different quality". They are "enough
candidates to fill the book" versus "not enough", and the venue's equal-weighting
rule means the seconds we lost were worth the same as the ones we kept.

### Root cause: `projected_reward` is time-invariant, so the buckets never measured timing

Traced 2026-08-23T02:05Z, answering "where did we get that 2h pays more".

The whole chain rests on one section, "Reward is concentrated in the hours
before kickoff" -- a **single snapshot of 48 markets on 2026-08-16**, bucketing
the selector's `projected_reward` by minutes to kickoff, showing 0.000313 at
15-30 minutes against 0.000022 at 120+.

**That quantity does not vary with time to kickoff.** From
`project_order_reward` (`rewards.py`):

```python
duration           = 6 hours, constant for every day_of market
dollars_per_second = pool * 0.5 * share * haircut / duration
projected          = dollars_per_second * min(horizon, remaining)
```

`horizon` is `reward_projection_horizon_seconds` = **30**, and `remaining` is
always above 300 seconds because the pre-kickoff flatten fires at T-300. So
`min(horizon, remaining)` is **always exactly 30**, and

```
projected_reward = 30 * pool * 0.5 * haircut * share / 21600
                 = a constant x (pool x share)
```

It contains no term that decays with distance from kickoff. It measures the
market's pool and our score share, and nothing else.

**So the buckets compared different markets, not the same market at different
times.** A market 120 minutes from kickoff at 22:00Z is a different event, in a
different league, with a different pool and a different book, from one 20
minutes out at 22:00Z. The 14x gap is a difference between those markets. It was
then read as a property of *when* rather than of *which*, and a selection rule
was built on it.

**The source section says so itself, and was ignored:**

> This is *projected* reward at a single snapshot, not realized earnings, and
> **every market passes through every bucket** as its game approaches -- today's
> 120+ market becomes a 30-minute market later. **So the claim is NOT "distant
> markets are worse to hold".**

That sentence is the exact inference `0b04c2b` made. Together with `44bfecd`'s
"neither number should be used to justify a change to selection" nine minutes
before the change, the evidence carried two explicit warnings against the use it
was put to.

**Also explains the 500x discrepancy** left open in `44bfecd`: if
`projected_reward` is `k x pool x share`, its magnitude is driven entirely by
which markets are in the sample, so two samples taken hours apart on different
slates have no reason to agree. They were never the same population.

**What this does NOT establish.** That distant markets are equally *good* -- only
that this measurement never showed they were worse. Thin distant books fail
`target_not_met` outright (a third of the 120+ bucket paid nothing), and that
part is real and is about the book, not about the clock. The open question
remains competition and depth by time-to-kickoff, measured on realised reward.

### Re-checked against the docs and the live payload before changing anything

2026-08-23T02:15Z, at the operator's request to verify 6h and 2h are equally
paid *before* acting.

**What the docs state.**

- day_of is *"From 6 hours before until the event starts"*
- *"Each snapshot is equally weighted regardless of how much liquidity is on
  the book"*
- payout is *"purely proportional to your share of the total score"*
- on any multiplier, boost, ramp or weighting that raises payout closer to the
  event, the docs are **completely silent** -- no such mechanism is documented

**What the docs do NOT state.** How a pool is sized, how many snapshots occur
per second, or explicitly that a period's pool is divided evenly across its full
duration. So the correct claim is **"no documented mechanism pays more near
kickoff"**, not "proven identical per second".

**What the live payload shows** (three markets at T-0.0h, T-5.6h, T-7.9h,
2026-08-23T02:10Z) -- identical regardless of distance from kickoff:

```
period=live    pool=100.0  target=5000.0  discount=0.5
period=day_of  pool= 35.0  target=5000.0  discount=0.5
starts_at=2026-07-21T02:00:00Z   ends_at=None
```

Two things fall out. The pool, target and discount **do not vary with time to
kickoff** -- a market 7.9 hours out advertises the same $35 day_of pool as one
at kickoff. And `starts_at` is a **program-level date from a month ago** with
`ends_at=None`: the venue publishes no per-market period window at all, which
is why `project_order_reward` derives it as `event_start - 6h -> event_start`
from the documented definition rather than reading it.

### The empirical check is impossible retroactively, and that is its own finding

The decisive test -- resting time bucketed by distance from kickoff against
realised reward, over the 6h era -- **cannot be run.** Kickoff times for every
past market are gone:

```
slug date    markets traded   still in catalog   coverage
2026-08-14              32                  0         0%
2026-08-15              88                  0         0%
   ...
2026-08-22             148                  0         0%
2026-08-23              11                 10        91%
```

The venue's catalog holds only current and future markets, and **the ledger
never records `game_start`**. So every question of the form "how did
time-to-kickoff affect this" is unanswerable after the fact. That is why the
2026-08-16 snapshot was a snapshot: it was the only form the measurement could
take, and it is still the only form available today.

**Fix this before running the A/B.** Recording `game_start` on the ledger when a
market is bound is a small, safe change, and without it the A/B can only compare
daily totals -- it will not be able to say *where* the reward came from, which
is the actual question. Twice now a selection rule has been argued from a single
snapshot because nothing durable was being written down.

### Standing answer to "are 6h and 2h equally paid"

**Per second of resting, the venue documents no difference.** Same pool, same
target, same discount, equal snapshot weighting, no ramp.

**Per second of resting, what we RECEIVE may still differ**, because payout is
share-based and competition is not documented and has never been measured by us.
That is the one live unknown, and it is the thing the A/B has to answer.

---

## The `reward_ineligible` cancel is churn: 87% come back to the identical price

Measured 2026-08-23T02:35Z over 2026-08-22T14:00Z onward, prompted by the
operator noticing "it will place it but also cancel it sometimes". It is the
answer to why so few orders rest at any instant, and it predates the horizon
restore.

**Split by cancel reason, because the existing measurement covers only one row:**

```
cancel reason                              cycles  same price  median gap   dark min
side_no_longer_desired:reward_ineligible      451         87%        71s     1082.3
resting_price_left_the_touch                   95         14%        51s      245.0
replace_reducing_at_active_touch               76         29%        22s       33.8
hard_timeout_cancel_confirm_first              11          0%        19s        5.4
```

`resting_price_left_the_touch` reads **14% on 95 cycles**, consistent with the
10% on 119 cycles measured 2026-08-17. That path is healthy and the churn fix
holds. **`reward_ineligible` was never measured, and it is 87% on 451 cycles.**

### Why it destroys value rather than protecting anything

An order at price P scores in any second where the reward cutoff includes P.
The cutoff moves when OTHER makers add or pull depth, so eligibility oscillates
while the correct price never changes. Cancelling turns "scores some seconds"
into "scores no seconds" for a median 71 seconds -- and then re-places at the
**same price**, so it does not even avoid the fill risk it appears to be
avoiding. The cancel buys nothing and costs the interval.

`reward_eligibility_dwell_seconds` (90s) exists precisely to stop this flapping.
At 87% it is not doing so: the side stays ineligible past the dwell, gets
cancelled, and flips back inside a further 71 seconds.

### Scale

1,082 market-minutes = **18 hours of order-time** dark, in a 12.5-hour window
across ~20 slots. At the measured 0.230 $/rest-hr that is about $4 over the
window, roughly **$8/day against reward of $22-30/day** -- on the order of a
quarter of the reward line.

Treat that as an upper bound on the recoverable amount: some of those seconds
would have scored zero anyway, because the cutoff genuinely had moved inside P.
The recoverable share is whatever fraction of the dark time the cutoff was back
outside P, which this measurement does not separate.

### Candidate fixes, none applied

1. **Do not cancel a flat-state entry quote for reward-ineligibility at all.**
   An ineligible resting order costs only fill risk, and the 87% figure says we
   re-accept that risk 71 seconds later anyway. Simplest, and the most
   aggressive.
2. **Raise `reward_eligibility_dwell_seconds`** well past 90s. Cheapest, but it
   only moves the threshold -- the oscillation period sets how much it helps,
   and that has not been measured.
3. **Suppress the cancel when the re-place price would be identical.** Most
   surgical, and it targets the 87% directly rather than the condition.

**Do not pick one from this entry alone.** The missing measurement is the
oscillation period of eligibility at a fixed price -- how long a side stays
ineligible before flipping back. That decides between (2) and (3) and bounds
what (1) is worth. It needs `state.economic_blocker_by_side` sampled per sweep,
which is the same `drop_cause` logging this file has now asked for three times.

**Relation to `NEXT.md` item 00b.** That item is about churn and was closed by
the 2026-08-19 fix, which addressed `expected_net_not_positive`. This is a
DIFFERENT cancel reason with the same shape, and it was not covered by that fix
or by any measurement since.

### The instrument is live as of 2026-08-23T02:45Z

`[eligibility]` now logs every change in a side's reason-to-quote, with how
long the previous state held. Deployed in `e8c23ff`. First lines, three minutes
after the restart:

```
market=aec-ufc-anther-grerod  side=offer  eligible -> outside_reward_cutoff  held=64.6s
market=aec-ufc-anther-grerod  side=offer  outside_reward_cutoff -> eligible  held=40.1s
market=aec-setkameua-khadmy-smyvas side=offer eligible -> target_not_met     held=109.2s
```

**This is the oscillation, measured rather than inferred.** The UFC market held
eligible for 64.6s, went ineligible for 40.1s, and came back -- at an unchanged
price, moved only by other makers' depth. The 40.1s spell is inside the 90s
`reward_eligibility_dwell_seconds` and correctly did NOT cause a cancel; the
109.2s spell would have.

**To choose the fix, collect a few hours and take the distribution of INELIGIBLE
spell durations** -- the `held` value on any `-> eligible` transition:

```bash
grep -a '\[eligibility\]' logs/commissioning.log | grep 'to=eligible' \
  | grep -oE 'from=[a-z_]+|held=[0-9.]+' | paste - -
```

- If most spells are well under 90s, the dwell is already doing its job and the
  451 cancels come from a minority of long spells -- raise the dwell.
- If most exceed 90s, raising it chases the tail forever and the same-price
  suppression (option 3) is the real fix.
- Splitting by `from=` also separates `outside_reward_cutoff` (competitors took
  the cutoff past our price) from `target_not_met` (the book thinned below
  target), which are different problems wearing one cancel reason.

Do not decide before the data exists. Five hypotheses died by measurement in
the session that produced this entry.

---

## The 20-slot scheduler, not the six-hour horizon, caused the 0/20 order state

Established 2026-08-23 from the exact live twenty-market set, then fixed and
covered locally before restart.

### Evidence

At inspection time the summary showed 98 viable markets, 20 bound, 40 eligible
sides and no working orders. Evaluating each bound market independently with
the same live state produced a placement action for all twenty. Actual live
placement history reached only four markets: 22 creates distributed 14/5/2/1,
with one order working at the sampled moment. A six-hour horizon therefore did
what it was meant to do—supply candidates—and was not the bottleneck.

The ordering explains the contradiction. The run loop refreshed balance,
serially serviced up to twenty markets through one 4 req/s limiter, then began
deciding. Equity aged out after 15 seconds. `_frame` correctly failed closed by
removing entry sides, but the dashboard counted the pre-risk eligibility set.
One unresolved mutation and one market's recovery delay each added separate
account-wide pauses. Full-book and private queues could also deliver delayed
events that the runner relabeled as current.

This is distinct from the earlier rejected theory that a high raw
`book_stale` count proves contention. The attempt-depth evidence still shows
that nearly all stale recoveries succeed immediately. The newly proven failure
is cross-slot scheduling around otherwise successful work.

### Deterministic findings and disposition

| Finding | Disposition |
|---|---|
| Serial service can expire 15-second equity before later decisions | Refresh at response receipt and within a five-second pre-expiry decision margin |
| A full-slate sweep can expire the 30-second execution-state proof | Refresh REST state at 25 seconds, stamp response completion, and attempt a failed refresh only once per sweep |
| One unresolved mutation pauses all twenty | Skip only its owning slug; unattributable state remains account-wide fail-closed |
| One market's backoff sleeps the runner | Per-slug next-decision deadlines; no sweep sleep |
| Queued books/private events are stamped fresh on dequeue | Socket receipt timestamps retained; full books coalesced per slug; the task-held event is applied first and both backlogs drain immediately after REST service |
| Shutdown ignores ambiguous creates | Unresolved and absent orders now defeat venue-flat proof |
| Selector omits 0.10 entry floor | One configured floor passed to selector and reconciler |
| Selector hardcodes minimum quantity 1 | Compare venue minimum with configured quote size 10 |
| `98 viable not quoted` exceeds capacity | Unmet count capped at twenty; `Viable now` still reports 98 |
| Empty plans look like re-quoting | `_last_action_at` updates only after a non-empty successful apply |

The local regression suite contains a twenty-slot timing test: all twenty are
planned, and the account equity observation refreshes at slot eleven rather
than silently starving the tail. A separate boundary test proves events that
arrive while REST service is running are applied before decisions, preserving
private-event order and receipt time.

The first repaired live run began at 04:37:47Z and placed on **all 20 distinct
markets**: 33 initial placements versus the old history's four-market
concentration. It reached 26 working orders across 16 markets with no unresolved
intent, absent/unknown order, or position. That acceptance also revealed
per-market `execution_state_unknown` quarantines once the now-productive serial
sweep crossed the private gate's 30-second freshness edge (04:40:39Z,
04:42:00Z, 04:42:50Z, 04:44:28Z). The condition was safe but reduced coverage
progressively, so the decision loop now renews the proof at 25 seconds. If the
renewal fails, it is attempted once—not once per remaining slot—and the stale
gate continues refusing new risk. REST and startup proofs are timestamped at
completion so slow responses do not begin partly expired. Full local suite
after this final edge review: **513 tests passing**.

### Proper way to tell whether all twenty slots are actually working

Do not answer this from one dashboard count. The four numbers describe
different layers:

- `Viable now` is universe supply and can exceed twenty;
- `Eligible sides` is pre-decision side eligibility, not proof of an attempted
  order;
- `Quoting`/working orders is an instantaneous resting result after decisions,
  cancellations, fills, and book movement;
- a **working slot** is a bound market that is actually reached by the decision
  scheduler on its cadence, even when its correct result is an empty plan.

Use this sequence:

1. Capture one exact-time snapshot: the ordered `active_slugs` set, balance and
   equity observation times, private/REST gate times, unresolved intents,
   per-market recovery deadlines, current books, working orders, and positions.
   Do not combine a dashboard from one instant with catalog or ledger data from
   another.
2. For every slug in that exact bound set, run the reconciler plan independently
   against the captured state without applying mutations. Record whether it
   returns `PLACE_POST_ONLY`, `CANCEL`, or no action, plus its blocker. This is
   the control group: it answers whether the market itself could be acted on.
3. From the same session start/bind boundary, group `[action]` lines by market
   and slot order—not just total creates. Also count unresolved ownership,
   `[runtime-recovery]` attempt depth, and quarantine reason. A total of twenty
   actions can still be one early slot churning twenty times.
4. Compare the independently plannable set with scheduler coverage and resting
   results:

   | Observation | Diagnosis |
   |---|---|
   | All/most slugs plan independently, but actions repeat on the first few | Cross-slot scheduler or shared-freshness starvation |
   | Actions reach the slate, but working orders disappear | Retention issue; split cancels by `reward_ineligible`, touch change, fill, or retirement |
   | Independent plans are empty with explicit blockers | Market economics/selection mismatch, not scheduler starvation |
   | One unresolved slug coincides with silence everywhere | Regression to account-wide intent blocking; only the owner should pause |
   | Raw `book_stale` is high but attempt one immediately succeeds | Normal best-effort refresh; inspect duration and attempt depth before blaming REST |
   | Equity age approaches 15s or execution-state age approaches 30s before tail slots | Shared freshness is expiring inside the serial sweep |

The decisive evidence in this incident was exactly that comparison: all twenty
captured slugs returned placement plans independently, while live placement
history reached only four markets and was distributed 14/5/2/1. That proves a
scheduler problem. Conversely, the accepted fixed run produced 33 placements
across all twenty markets, and the final restart produced 64 actions across 18
action-producing markets in its first 10m18s. The other two markets having no
action is not itself failure; an empty economic plan is valid. Coverage must be
judged against the independent plan/control result, not against `20` blindly.

For a fast live check, start with these server commands. Set `RUN_START` to the
current runner's UTC start time; the `awk` filter prevents old sessions from
contaminating the result:

```bash
./scripts/status_commissioning.sh
RUN_START='2026-08-23T04:56:00'
awk -v start="$RUN_START" '{
  for (i=1; i<=NF; i++) if ($i ~ /^at=/) {
    if (substr($i,4) >= start) print
    break
  }
}' logs/commissioning.log | grep -a '\[action\]' \
  | sed -E 's/.* market=([^ ]+).*/\1/' | sort | uniq -c | sort -nr
awk -v start="$RUN_START" '{
  for (i=1; i<=NF; i++) if ($i ~ /^at=/) {
    if (substr($i,4) >= start) print
    break
  }
}' logs/commissioning.log \
  | grep -aE 'execution_state_unknown|runner_failure'
```

The second command is a safety/freshness check, not a substitute for the
independent per-market plan comparison. At present, empty plans are not logged
as `[action]`; therefore action-log coverage alone can identify concentration
but cannot prove that every no-op slot was skipped. The dashboard also does not
list the ordered bound set. Use the captured-state independent plan comparison
for the definitive answer; this is the current observability limitation to
remember next time.

### Still open, deliberately not folded into this fix

`reward_ineligible` cancellation churn is an independent reducer of resting
orders after placement. Historical evidence (451 cycles, 87% returning at the
same price) proves material churn, but the current fixed-price ineligibility
spell sample is only nine completed spells, five longer than 90 seconds. That
is not enough to choose between a longer dwell and same-price suppression.
Changing it in the scheduler patch would mix a proven mechanical repair with
an under-sampled strategy decision.

---

## Private recovery eventually escaped, but account-wide waiting still stopped the twenty-slot scheduler

Established 2026-08-23 from the live runner, ledger, and exact log timeline.

The private stream disconnected at **21:00:01Z**. The runner remained alive,
but recovery attempts 2 through 35 rejected resume because one or more CANCEL
intents were not terminal. Attempt 35 was at 21:24:18Z; the first subsequent
placement was at 21:25:48Z. Therefore the bot did recover itself, but only
after roughly 24 minutes in which all twenty markets were dark.

This corrects the initial diagnosis: GET-by-ID terminal reconciliation already
exists and works. The evidence was the unresolved count falling from 29 to 14
to 6, and order `C24EPEEAYDJR` receiving a venue CANCELED state and reconciling
at 21:19:26Z. Absence from a complete open-order snapshot correctly remained
`UNKNOWN_ABSENT`; it was not interpreted as cancellation.

The recovery immediately exposed a second measured head-of-line case. From
21:25:48Z through 21:25:56Z the bot issued placements across the slate. Their
acknowledged IDs then encountered GET-by-ID NotFound consistency lag. At
21:34:46Z one market entered `execution_state_unknown`; its recovery did not
return until 21:35:19Z after waiting on **8 order IDs**, most belonging to
other markets. The next market waited 21:35:20–21:35:49Z on 7 IDs. At the last
pre-fix snapshot the runner was still alive and repeating this pattern with
3–8 IDs per recovery, but no new `[action]` lines had appeared after 21:25:56Z.

The established distinction is:

- terminal state is an order fact and must never be inferred from absence;
- ownership is a scope fact, so ambiguity positively tied to one market need
  not make nineteen unrelated markets unknown;
- a complete REST account snapshot plus a fresh private bind restores account
  visibility even while one owned order remains ambiguous;
- unowned, missing-owner, conflicting-owner, open-position, and failed account
  snapshot state cannot be scoped and remain account-wide fail-closed.

The implemented repair follows that boundary. Private recovery restores the
streams and lets the existing per-market gate contain ambiguous owners.
Single-market quarantine requires readable/terminal proof for its target but
does not issue individual reads for attributed IDs in other markets; complete
account snapshots still cover the whole account. Normal global REST reconcile
remains strict. Twelve failed private reconnect attempts bound the dormant
loop, but the cap never proves flat; restartability still comes only from the
independent shutdown flat proof.

Live acceptance disproved an intermediate implementation. It fetched all IDs
first and merely ignored unrelated failures afterward. At 21:53:55Z the target
market entered recovery; only at 21:54:15Z did the log report two tolerated
foreign failures. That 20-second gap still paused the serial runner. Scoping a
failure result is not scoping work: unrelated GETs, absent repairs, and
uncertain-cancel retries must not be performed on the scoped path at all.
The same rule applies to the routine ten-second account freshness refresh and
the mid-sweep 25-second renewal: complete snapshots renew visibility, while
individual ambiguity stays behind its owner gate. Background terminal repair
still performs GET-by-ID, but for one owning market per pass in round-robin
order so a persistent 404 cannot starve the other owners or all twenty slots.

Final acceptance deployed source commit `501c743` after the prior runner proved
flat at 22:09:48Z. All 518 production tests passed in 37.105 seconds. In the
new run, `scoped_order_reads_skipped=8` was emitted at 22:12:07Z and the next
action appeared at 22:12:10Z. The earlier implementation took 20 seconds just
to report two tolerated foreign reads; the final implementation renewed the
snapshot and continued the slate in about three seconds. At that observation
the venue had only seven economically viable markets, and the bot rested 11
orders across all seven with zero positions. Fewer than twenty active quoting
markets in that state is exhausted viable supply, not evidence that the slot
cap or scheduler regressed.

### How to recognize this exact failure next time

Use the session start boundary and correlate these signatures:

```bash
./scripts/status_commissioning.sh
grep -aE 'component=private_stream|\[private-recovery\]' logs/commissioning.log | tail -80
grep -aE 'execution_state_unknown|strategy_state_recovery' logs/commissioning.log | tail -80
grep -a '\[action\]' logs/commissioning.log | tail -40
```

- Increasing `component=private_stream attempt=N` plus `runtime recovery has
  unresolved order intents` means private visibility is waiting on terminal
  proof instead of reconnecting around scoped ambiguity.
- A `strategy_state_recovery:<one slug>` error that lists many order IDs from
  other slugs means account-wide order reads are serializing per-market repair.
- A live PID is not proof of trading. Compare the timestamp of the newest
  `[action]` with the newest recovery line and count distinct action markets
  since the current run start.
- After this fix, look for `[private-recovery] ... resumed=yes
  scoped_ambiguous=N` and `[rest-reconcile] ...
  scoped_order_reads_skipped=N markets=...`. Those lines mean unrelated
  individual reads were actually deferred—not merely tolerated after their
  latency—and only the named owners remain blocked.

A concise prompt for another reviewer is: **“Review liquidity-bot twenty-slot
liveness for account-wide head-of-line blocking. From the current run start,
correlate private-stream attempt depth, quarantine caller, unresolved and
UNKNOWN_ABSENT ownership by market, GET-by-ID failures, and distinct `[action]`
markets; verify ambiguous state blocks only its owning market and compare with
independent per-market plans.”**

---

## Cancel confirmation was fast; the replacement waited behind the slate

Established 2026-08-23 after the scoped private-recovery deployment, from the
current run's action log and execution ledger.

This is separate from the 21:00Z private-stream outage. That outage kept all
markets dark for about 25 minutes and the scoped-recovery changes fixed it. The
lower instantaneous order count after the restart prompted a second review of
the cancel/re-place path.

### Evidence and history check

From the 22:11:14Z run start, 80 completed
`resting_price_left_the_touch` cancel/replacement pairs could be joined to
their CANCEL intents and order rows:

```
CANCEL terminal confirmation     median 1.22s   p90 2.10s   max 3.77s
terminal -> next replacement     median 39.45s  p90 118.63s
cancel -> next replacement       median 40.60s  p90 120.09s
same replacement price           21 / 80
within one tick                   48 / 80
```

The exchange and ledger were therefore not the main wait. After positive
terminal proof, the owner still went through periodic account work and the
shared bound-market service path before the next full decision sweep. That
path regularly refreshed stale books through the single 4 req/s governor. A
market with no working order remained dark while unrelated slots were
serviced.

The operator correctly tied the visible change to the 21:00Z incident, but a
before/after history comparison disproved the recovery patch as the origin of
this latency:

```
                         touch cancels   completed pairs   median dark gap
16:00-21:00 before stop       122              119              122.4s
22:11+ after restart           85               83               41.1s
```

The post-recovery code was already materially better. The underlying
cancel-first behavior dates to 2026-08-13, the touch-change reason dates to
2026-08-14, and cancel/re-place cycling was measured on 2026-08-19. The restart
also rebuilt the slate while selection reported only seven to nine viable
markets; 20 is capacity, not a promise that twenty candidates exist.

### Fix boundary

When a CANCEL intent gains terminal proof from either the private stream or
REST, its positively-known owner is queued once in active-slot order. Before
the next full-slate service pass, only those owners are re-serviced and sent
through the existing decision path. The fast path does not infer cancellation,
does not place while an intent is unresolved, and does not bypass book,
execution-state, equity, economics, loss, or ownership gates.

Priority service can itself yield for REST, so ready market/private events are
applied and drained before the decision. The owner's derived book economics
are then refreshed once more (normally without REST) before planning. This
preserves the same fill-before-decision ordering as the full-slate path. A
released market is removed from the queue, multiple cancel confirmations for
one owner deduplicate, and a normal full sweep consumes a REST-created marker
so it cannot cause duplicate work.

The new live marker is:

```
[priority-replan] at=... elapsed=... markets=slug[,slug]
```

### Production acceptance

Commit `abbbd7379728e3184ac73247dc5cc713214d1005` was deployed and the
runner restarted at about 23:11:57Z. The first three independent priority
batches were observed at 23:13:29Z, 23:14:08Z and 23:14:59Z. They covered
seven owner occurrences (six distinct markets) and completed the scoped pass
in 2.6s, 6.6s and 2.8s respectively. The first six same-side replacements
arrived 19.3-33.7s after their original touch cancel, below both the prior
40.60s median and 120.09s p90 cancel-to-place gaps. A later owner correctly
cancelled its other side instead of blindly recreating the cancelled bid when
the refreshed normal plan no longer called for that order.

At the 23:15Z safety sample the runner and supervisor were both live, there
were zero positions, zero `UNKNOWN_ABSENT` orders and no runner failures. One
five-second-old acknowledged CANCEL was still unresolved and then belonged to
the next normal priority cycle; it was not an old or unattributed ambiguity.
The account had recently shown 21 working orders across 11 markets. Selection
immediately before deployment reported 11 viable markets, so the sample was
bounded by current viable supply rather than a reduced slot cap. The live
command still had no max-market override and the configured capacity remained
20.

To recognize this exact issue next time, compare each touch CANCEL with the
next `PLACE_POST_ONLY` for the same market and side, then split the elapsed time
at the CANCEL intent's `updated_at`. A short cancel-confirm interval and a long
terminal-to-place interval is scheduler latency. A long cancel-confirm interval
is venue/private/REST reconciliation instead. Also compare periods on both
sides of any deployment before attributing the change.

A concise prompt for another reviewer is: **“Audit liquidity-bot
cancel-confirmed replan latency. From one run boundary, join
`resting_price_left_the_touch` CANCEL actions to CANCEL intent `updated_at` and
the next same-market/same-side PLACE; report cancel-confirm and
terminal-to-place distributions, same-price returns, `[priority-replan]`
elapsed time, and verify all stream/equity/execution/economic gates run before
replacement.”**

---

## Maintenance recovery must preserve the process, not restart an unproved account

Established from the 2026-08-25 Polymarket US maintenance incident and the
runner/supervisor control flow. The last runner stopped at
`2026-08-25T11:14:26Z` with:

```
GET /v1/orders/open failed: InternalServerError
stop_reason = runner_failure:bound_stream_recovery_exhausted
flat = false
```

The durable ledger at that boundary still described 24 working orders across
14 markets, one -4 position and 10 acknowledged CANCEL intents with exchange
IDs. That was a stale incident record after the process stopped, not a current
venue snapshot; the operator later cancelled and flattened manually. The
important fact is that shutdown could not obtain the venue proof required to
replace that ledger state, so returning `flat:false` and exit 3 was correct.

### Why the previous recovery could not come back automatically

The runner already bounded both-stream recovery at 12 attempts. Exhaustion
entered shutdown. During maintenance, shutdown could neither confirm terminal
orders nor prove positions flat, so it correctly produced exit 3. The
supervisor never restarts exit 3 because a fresh process must not assume an
unproved account is safe.

Changing exit 3 to restart would be unsafe. Repeatedly restarting exit 5 would
also not solve startup maintenance: three fast preflight failures consume the
supervisor's crash allowance and it gives up. The safe recovery owner is the
same process, which retains the authorized active set and ledger ownership
context while taking no new risk.

The global `status.polymarket.com` page is not a sufficient gate for this bot:
the bot trades Polymarket US, and the incident's authenticated US endpoint was
the direct failing dependency. Recovery is therefore measured against the
actual authenticated account API, not an external status-page label.

### Implemented boundary

Only positively identified transport, WebSocket-disconnect or HTTP-5xx
failures qualify. Three consecutive qualified startup failures, or 12 ordinary
private/market/both-stream recovery failures whose final streak is qualified,
enter a dormant maintenance pause. NotFound consistency lag, 429, malformed
response/schema, ownership conflict and runtime invariants do not qualify.

The pause performs only complete authenticated reads of open orders, positions
and balance. It does not apply the order/position responses to the ledger and
therefore cannot turn absence into cancellation. It requires three consecutive
complete cycles one minute apart; a failed cycle resets the streak, giving a
minimum stable period of about two minutes. After that, the original recovery
path still must cancel known quotes, reconcile exact state, bind fresh streams
and pass the private-state gate before any quote is possible.

Startup uses the same in-process health gate, so a venue already down when the
runner starts no longer spends the supervisor's fast-crash budget. SIGTERM is
checked in five-second sleep pieces. A response-contract error during the
health gate escapes rather than being hidden forever as maintenance.

### The 20-slot scheduler is isolated

No selector, active-set, market-state, reconciler, action-driver or slot-cap
code changed. The maintenance path reads `active_slugs` and never writes it.
A production-shape regression binds 20 distinct slugs, forces 12 private
reconnect failures, passes the maintenance gate, and proves the replacement
private stream subscribes to the identical ordered 20-tuple. It also proves no
order create occurs during the pause. The complete local suite is 581 tests.

### How to recognize and verify this next time

From the current run boundary:

```bash
grep -aE '\[maintenance-pause\]|component=(startup_venue_unavailable|private_stream|market_stream|bound_streams)' logs/commissioning.log | tail -120
grep -aE '\[bound-stream-recovery\]|\[private-recovery\]|\[action\]' logs/commissioning.log | tail -80
./scripts/status_commissioning.sh
```

- `state=entered` means sustained service failure was classified and all new
  quoting is dormant.
- `health=success stable=N/3` means one complete read-only account cycle
  passed. Any `health=failed stable=0/3` must erase prior progress.
- `state=health_gate_passed` is not trading authority. It means only that the
  original stream/reconciliation recovery may try again.
- Acceptance requires the subsequent component recovery marker, then fresh
  `[action]` lines across distinct markets. Verify the bound/subscribed set is
  still 20 when viable supply permits; do not infer a slot defect from raw
  order count alone.

A concise prompt for another reviewer is: **“Audit liquidity-bot maintenance
recovery from the current run boundary. Correlate `[maintenance-pause]`
entered/failed/stable/health-gate markers with startup/private/market/bound
attempt depth, exact authenticated endpoint errors, the subsequent normal
stream-recovery marker, and distinct `[action]` markets. Verify no create in
the pause, no absence treated as cancellation, and the ordered 20-market active
set is unchanged before versus after recovery.”**

### Production deployment and initial acceptance

Commit `feba761` was pushed and all eight deployed hashes matched. The complete
droplet suite passed 581 tests in 54.764 seconds while both runner and
supervisor remained stopped. The existing size-10 supervisor/runner then
started at 12:18:55Z/12:18:56Z on 2026-08-25.

Two selected candidates became non-open during preflight and were explicitly
dropped, leaving 18 initially bound markets under the unchanged 20-slot cap.
This is candidate lifecycle, not a scheduler-cap change. At 12:26:15Z the new
session had created 37 placements across 17 distinct markets and held 23
working orders across 12 markets, every working quantity exactly 10. Unresolved
intents, `UNKNOWN_ABSENT` orders and nonzero positions were all zero. No
maintenance-pause, runner-failure or risk-stop marker appeared. The new outage
branch therefore remains pending natural live acceptance; forcing maintenance
or disconnecting a funded bot solely to exercise it is not justified.
