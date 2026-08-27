# Next session — outstanding fixes

> This is a detailed work/history log, not the current runtime state. Every AI
> session starts with [AI_OPERATOR.md](AI_OPERATOR.md), [STATUS.md](STATUS.md)
> and [DECISIONS.md](DECISIONS.md). “ACTIVE” below is scoped to its dated
> section unless `STATUS.md` says it is active now.

## DEPLOYED AND ACTIVE — provisional hard stop and exact August 26 account row

Production evidence shows the August 26 marked sensor crossed $80 with open
inventory, while the later flat ET account result was `-$64.4870` and completed
non-trading cash was zero. The historical Discord row's pair estimate was only
`-$48.65`.

Local code now blocks entries immediately on a marked breach but waits for a
breach-time position to close and for a newer adjusted balance before clearing
or confirming it. Realized loss can still pause while another position is open.
The reward report uses adjacent proven-flat ET boundaries and completion-time
cash classification for historical account truth; `~` marks fallback pair
estimates. Six mutations were killed and all 651 local tests pass in 41.735s.

The operator authorized deployment and restart while flat. The old runner
gracefully proved `flat=true`; post-stop venue and ledger checks were clean.
All 15 hashes matched and all 651 server tests passed in 42.288s. The `auto 10`
runner restarted on marker `a974d68` at 17:08:31Z, bound 20 markets, and the
17:10Z acceptance snapshot showed 18 resting orders across 18 markets, zero
positions, zero unresolved intents and zero `UNKNOWN_ABSENT` rows.

---

## DEPLOYED; BOT STOPPED: dashboard unavailable state

The 2026-08-27 maintenance audit found a clear operator-safety reporting
defect. `scripts/dashboard.py` initializes venue orders, positions and equity
to zero. If an authenticated account read raises, it correctly adds `venue
unreachable` but passes those default zero values into the slot and risk
sections. The resulting display can say `positions 0 (flat)`, zero resting
orders and empty slots even though current venue exposure is unknown. During
the observed outage, the retained local ledger still had 31 working rows and
one unresolved intent; neither local state nor a prior successful snapshot is
proof of current venue state.

The local fix commits order, position, balance and quoted-market values only
after all three account reads succeed. Any first or later read failure keeps
every venue-backed terminal and Discord field `unknown`, omits `flat`, and does
not calculate empty slots. Two dashboard tests cover failure at all three read
stages and the Discord payload. This remains reporting-only.

## DEPLOYED; BOT STOPPED: duplicate maintenance tracebacks

The local fix keeps the full traceback for attempt 1 and every 30th attempt.
Every probe still emits its concise numbered runtime line plus the maintenance
health/stable marker. Poll cadence, three-pass recovery, failure reset, active
tuple and mutation behavior are unchanged.

## DEPLOYED; BOT STOPPED: closed owners parked 20-slot capacity

The 2026-08-27 13:20Z capacity audit found queue scope `markets=20`, but three
flat, order-free and unresolved-free WTT owners remained bound after empty-book
and `not open` evidence. Alternating `unrecoverable_book_AdapterError` and
`unrecoverable_book_stale` reasons advanced each owner to round ten, and the
30-minute retirement cooldown kept them non-quoting while the selector still
had 77 viable alternatives. The other 17 owners continued operating; this was
not an account-wide recovery stall.

An operator-authorized flat restart at 13:34Z cleared the session-local
symptom: fresh selection excluded the three WTT slugs, queue scope returned to
20, initial actions reached 19 distinct markets, and the venue held 24 orders
across 19 markets with zero position. This is a workaround and live evidence
for the diagnosis, not closure of the code gap.

The full flow review found two mechanisms. Equivalent permanent book failures
were compared by exact string, so AdapterError/stale alternation advanced ten
rounds and extended cooldown. They now compare by permanent failure family.
Separately, top-up did not recognize explicit closed/not-open metadata as a
release reason. It now schedules that candidate only when ownership state is
flat and clear, performs complete account reconciliation, freshly reads the
exact market again, and aborts before tuple/stream changes if the read fails or
the market reopened. Working orders, `UNKNOWN_ABSENT`, unresolved intents and
positions all retain the owner. Tests cover exact-cap replacement, position,
working order, missing status, reopen-at-commit and no cancellation of retained
orders.

Four deliberate reversions were detected and all 660 local tests pass in
39.768s. Commit `dc26c96` was pushed; all 11 server file hashes matched and all
660 stopped-server tests passed in 36.304s. The deployment marker is
`dc26c96`, and a final process check proved the supervisor and runner remained
stopped. Remaining work is fresh authenticated venue proof, explicit operator
authorization to restart, and later live acceptance of the 20-slot behavior.

## HISTORICAL: August 26 recovery/slot fixes and transient location-denial recovery

These fixes remain included in the newer deployed marker `a974d68`. Their
original source commit was `5825db8`, documentation commit `b738d8c`, and final
stopped-server acceptance marker `0b79a1f`.

Local source commit `5825db8` fixes five production defects found in the full
August 26 timeline:

1. private/both-stream recovery now listens before quarantine cancels and
   drains buffered terminal events; shutdown drains the same proof;
2. incremental top-up strictly reads only owners it releases, rechecks release
   proof before commit and distinguishes `[top-up-plan]` from
   `[top-up] ... committed=yes`;
3. the exact authorizing reward row can positively prove loss of eligibility
   after an event is rescheduled outside the six-hour window;
4. a stale book that immediately refreshes no longer increments an error or
   emits a false failure diagnostic;
5. an ambiguity-free, flat owner abandoned at round ten stops book refreshes it
   can never use.

The exact 20-slot diagnostic is now documented in FINDINGS. Ask to **"run the
20-slot scheduler audit"** and verify queue `markets=20`, active owners, slot
buckets, top-up plan versus committed lines, and unresolved/UNKNOWN_ABSENT age
and progress by owner. Raw order count is not capacity.

Focused regressions pass, six deliberate reversions were killed and all 638
local tests pass in 40.584 seconds. Commits `5825db8` and `b738d8c` are pushed;
all 13 deployed hashes matched and the stopped server passed all 638 tests in
58.686 seconds.

The operator then authorized a restart. The first run proved queue
`markets=20` but Polymarket rejected five creates with an explicit
VPN/proxy/location `PermissionDeniedError`; all five were recorded `UNCERTAIN`.
The supervisor was stopped immediately. After two authenticated whole-account
snapshots proved zero orders and positions, the blocked cleanup runner was
force-stopped. A retry correctly refused the unresolved ledger. The reviewed
`resolve_orphan_places.py` utility, while stopped, independently proved zero
venue orders and positions and changed exactly those five no-ID PLACE intents
to `FAILED`, leaving zero unresolved.

The operator rebooted the server and authorized one clean retry. Supervisor
`1074` / runner `1083` started at 22:36Z. Queue telemetry at 22:37:13Z proved
all 20 measurement slots; creates immediately returned exchange order IDs. At
the 22:38Z ledger checkpoint there were 20 open orders, zero nonzero positions
and no new uncertain placement. The same host/IP working after reboot means
this was not evidence of an account ban or permanent droplet block. If the
exact permission error returns, stop promptly, prove venue state
authenticated, and never resolve or force-stop from absence or ledger state
alone. Do not buy/migrate a droplet on this incident alone.

---

## PUSHED, DEPLOYED AND ACTIVE: non-trading cash cannot mask session loss

At 2026-08-26T02:10Z the authoritative current-day result was -$44.5193 from
466.0899 opening cash, 774.8506 current cash, +353.2800 completed non-trading
cash and zero open basis. The table row still showed pair estimate -$26.41,
and LIVE's raw-cash session sensor saw +$308.7607. Today's row/net now use the
account value. Historical rows remain pair estimates.

LIVE now shares the bounded completion-time cash classifier, subtracts proven
non-trading cash and requires two equal settled-cash reads around the scan.
Startup proves it before the action driver exists; runtime refreshes at most
every 15 seconds through the shared 4 request/sec governor. Unknown transfers,
moving cash, malformed pagination, four-page exhaustion or an ET-day crossing
cannot renew risk freshness. The marked-equity hard stop remains separate and
account-wide. Both economic defects were mutation-checked and all 634 tests
pass, including the audited legacy-boundary and exact fresh-boundary startup
regressions.

Deployment intentionally does not restart the bot. The hourly report reads the
new script on its next run; the LIVE safety correction activates only on the
next operator-authorized restart.

Commit `325f64b` is pushed and deployed. All 13 changed files matched, all 634
tests passed on the server, and a production report printed -$44.52 trading and
+$128.38 finalized net for August 25. Supervisor/runner 2631716/2631877 kept
their original 01:29 UTC start times through deployment, proving no restart
occurred. The operator then requested a graceful stop; it completed at
02:39:34 UTC with `flat=true`, and both processes stopped.

After review, the operator authorized restart. Supervisor 3454654 and runner
3454861 started at 02:41 UTC from deployed commit `325f64b`; startup completed
and post-only quoting resumed without cash-classification, session-loss-sensor
or startup-refusal errors.

---

## PUSHED, DEPLOYED AND ACTIVE; NO BOT RESTART: account-realized today

At 2026-08-26T00:53Z the webhook showed -$24.40 for the current ET day, while
venue cash plus the exact basis of four open positions proved realized trading
of **-$30.98**. Cash was down -$51.55 and $20.56 was still position basis. No
reward or funding transfer occurred after the ET boundary. Two one-sided
positions resolved for -$7.54 and were excluded by the balanced-leg report,
which explains most of the $6.58 understatement.

The seven historical rows remain the same balanced-pair calculation and are
now labeled `Pair est.`. A separate current-day value uses ET opening cash,
current settled cash, completed non-trading cash changes and exact venue
position basis. The first guarded legacy row is accepted only when its stored
cash is exactly 478.6998 and then uses the positively reconstructed 466.0899;
later days require a native baseline within two minutes of ET midnight.

The reporter brackets the positions with two equal balance reads, filters the
activity endpoint to five cash-only types, requires explicit EOF within four
pages and shares one 1 request/sec limiter across all report calls. The first
live dry run caught that the feed orders `createTime`, not cash-effect
`updateTime`; the correction filters on completion time and never treats the
creation order as an ET-boundary proof. It prints `Unavailable` on any
incomplete or inconsistent proof. Six focused tests and all 629 tests pass;
reversing transfer subtraction kills the focused test.
Production schema was checked read-only. Commits `b1fd50e` and `435a5e9` are
pushed and deployed; all eight final hashes matched and all 629 production
tests passed in 77.579 seconds. The acceptance read rendered account realized
-$44.73 versus pair estimate -$26.66 from opening cash 466.0899, current cash
421.3568, zero open basis and zero non-trading transfer. The hourly process
loads it automatically. Its deployment did not require a runner restart; the
later 01:29Z restart below was for already-deployed LIVE telemetry.

---

## PUSHED, DEPLOYED AND ACTIVE: Discord uses seven-day-table resting hours

The operator meant the `Resting side-hours` column from the seven-day reward
table, not contract-weighted reward score divided by 3,600. A live read at
2026-08-25T22:06Z proved the difference: 416.65 resting side-hours / 23.01
average sides for the current ET day, versus the displayed 17.82 score-hours.

The prepared change shares the exact report calculation with LIVE Discord,
where it runs in a worker thread against SQLite `mode=ro` at the existing
five-minute summary boundary. It adds no venue/API request and has no order,
selection, scheduler, stream or risk input. LIVE says `Resting side-hours`;
SHADOW continues saying `score-hours this run`. A read failure is reported and
falls back to 0 without breaking summary or shutdown. All 623 local tests pass.
Source commit `39e8ddb` is pushed and deployed; all 12 hashes matched and all
623 reduced-priority server tests passed in 74.516 seconds. Deployed code read
422.21 current-day side-hours from the production ledger in 0.632 seconds and
rendered the correct label. At 01:28:42Z the operator-authorized restart gate
proved venue orders/positions and ledger working/unresolved/absent all zero.
Graceful shutdown returned `flat:true`, and the 01:29:32Z post-stop gate remained
clean. The unchanged `auto 10` start launched supervisor `2631716` / runner
`2631877`, which has loaded this correction. Queue telemetry proved 20-market
scope; by 01:33Z it held 29 working orders in 19 owners with zero unresolved or
unknown-absent rows and no position at the venue checkpoint. Raw quoted-owner
count is not slot count; a bound slot may temporarily have no authorized side.

---

## FIXED, DEPLOYED AND RESTARTED: make one-slot top-up transactional

The 2026-08-25 21:04Z top-up committed one replacement and closed both healthy
streams before strict GET-by-ID proof. Three owned NotFound responses then
forced correct global recovery, which canceled 32 orders and retired 18 owners.

The prepared fix strictly reconciles and asserts ownership before changing the
active tuple or streams, then uses complete post-bind snapshots and fresh
private state with per-owner ambiguity containment. A failed preflight retains
the exact old tuple, old open streams, connected gate and all resting orders.
It also checks operator stop between markets so a sweep already underway cannot
continue placing after SIGTERM. All three deliberate mutants fail their new
regressions; all 621 local tests pass.

Commit `466ef9c` is pushed and deployed; all eight hashes matched and all 621
production tests passed. Immediately before start, both venue and ledger were
clean. The size-10 restart reached queue scope 20 and actions in 18 distinct
markets, then 18 working orders across 18 markets with zero positions and
unknown-absent rows. Six acknowledged mutations at the checkpoint were fresh
and progressing. No new bound-stream/quarantine/retirement/runner-failure
marker appeared. Observe the next natural top-up for a preflight refusal or a
successful replacement; do not induce stream failure on a funded account.

## HISTORICAL: restart-persistent ET-day score-hours (LIVE display superseded)

This deployment correctly repaired session reset and ET-day splitting for the
reward-score diagnostic, but the operator later clarified that LIVE Discord
should show the seven-day table's resting side-hours instead. The durable score
accounting remains telemetry; the prepared replacement above supersedes it as
the LIVE display.

LIVE Discord now renders `Score-hours today` from a durable ET calendar-day
total instead of displaying the current process's raw score-seconds. Accrual
uses the same `America/New_York`/DST day splitter as reward reporting, is
flushed at the existing five-minute summary boundary and on orderly shutdown,
and continues across restarts. SHADOW is still run-scoped and says so.

All 618 original tests pass, including a forced persistence failure that remains
reporting-only and cannot interrupt shutdown. No order, selection, 20-slot,
risk, REST or WebSocket path was added or changed. Source commit `b052e62` is
pushed and deployed. The reviewed `466ef9c` restart loaded it at 21:51Z after
621 stopped-server tests passed. The first activation date is partial
because prior processes did not store daily score; complete days begin at the
following ET midnight. Aggregate score-hours sum concurrent eligible sides and
can exceed 24. A hard kill can omit only the increment since the preceding
five-minute flush. Deliberate mutations that removed persistence or allowed
its exception to escape were both detected by the new tests.

---

## DEPLOYED AND ACTIVE: visually accepted hourly Discord table

The dedicated report reuses `scripts/reward_table.py`; `--post-discord --quiet`
routes the complete seven-day ET table to `DISCORD_REWARDS_WEBHOOK_URL` without
printing the credential. `scripts/post_hourly_reward_table.sh` runs it under a
nonblocking file lock, 90-second timeout and reduced CPU priority. It is
read-only apart from Discord: one account-wide earnings request, SQLite
`mode=ro`, no discovery/book/order/position/balance or mutation calls, and no
runner control path.

Measured on the stopped live server and its 73 MB ledger: 2.86 seconds elapsed,
28% of one CPU, 45,516 KB peak RSS and 1,162 output bytes. The first real post
at `2026-08-25T18:06:15Z` proved routing but its wide pipe table was visually
unacceptable. The revised embed uses two narrow fixed-width tables and asserts
exact 53-character and 34-character rows. A revised real post succeeded at
`2026-08-25T18:18:42Z` and the operator visually accepted it. The minute-7
hourly crontab entry is active; no bot restart was needed.

During first acceptance, `/root/maker-lab` unexpectedly became absent; no
cause was established. The four original credential assignments were restored
from the local sibling copy, the reward webhook assignment was securely
re-added, and modes were reset to directory `0700` and file `0600`. Before and
after the accepted manual post, the directory, file mode and assignment name
were verified without printing any secret and remained intact. Continue to
avoid exposing credential values in diagnostics.

Local review is complete: all 609 tests pass. Deliberate mutations were killed
for wrong-webhook routing, the seven-day visual-layout cap and removal of the
non-overlap lock. The exact-row-width assertion separately prevents the header
misalignment found during local preview.

The pre-restart dependency review found and removed one remaining coupling:
rewards URL validation ran for every shared-secret caller and could block
trading startup on a malformed report-only value. Validation is now mandatory
only for the reporter. Commit `26b11b2` matched all 15 deployed files, passed
all 612 stopped-server tests, and the normal trading startup subsequently
reached all 20 markets. No reporting-only dependency remains on that path.


## DEPLOYED AND ACTIVE: ET risk day and $80/$80 limits

The operator authorized midnight `America/New_York` as the durable reset for
the per-market/session/hard loss budget and one $80 threshold for the two
account sensors. The realized sensor still pauses new risk; the marked-equity
sensor remains the emergency hard stop and wins when both fire together. The
$40 per-market limit is unchanged.

This is not a string-only timezone change. Baselines, pause dates,
three-consecutive-stop-day tracking, logs, alerts and the dashboard summary
label now share the DST-aware ET calendar. New ET-keyed ledger tables leave the
old UTC rows intact for audit. On the first ET-aware startup only, the bot
copies the baseline the UTC process was already enforcing; it does not stamp
current equity and cannot forgive cutover-day drawdown. If the bot remains
stopped across UTC midnight, the latest prior UTC baseline is copied rather
than granting a fresh budget during the UTC/ET gap. Every later day ignores
legacy rows and opens at the first balance after ET midnight.

Verification is complete locally: all 606 tests pass. Four deliberate
mutations were killed: restoring the UTC date reset failed the midnight
boundary test; removing the legacy-baseline copy failed the cutover-budget
test; requiring an exact current UTC row failed the stopped-across-midnight
test; and rejecting equal session/hard limits failed the independent-sensor
test.

The final `26b11b2` review matched all 15 changed server files and all 612 tests
passed there in 59.027 seconds. The bot restarted at about 18:36Z. Its first ET
row (`2026-08-25`, source `legacy_utc_cutover`) exactly preserves the legacy
opening equity/cash `478.6998` and opening realized `-207.39180000`. The live
dashboard reported `ET DAY` and `$80 / $80`. Acceptance then found 36 open
orders across all 20 markets, every quantity 10, with zero positions,
unresolved intents, `UNKNOWN_ABSENT` orders or new critical fault markers.
Three later cancels briefly entered the expected acknowledged/absent proof gap;
the automatic repair reconciled every one without intervention, returned
absence to zero and left 38 open orders across 20 markets. Escalate only when
the same rows fail to progress across reconcile cycles or reach
`unresolved_mutation_timeout`, not from one instantaneous absent count.


## 00. FIXED, DEPLOYED AND RESTARTED: replace flat markets whose reward period has positively ended

The live `73d5c73` run proved the remaining low-order state is not 20-slot
capacity loss: 20 remained bound, but ten markets changed to `no_incentive` at
13:00Z and held no orders. Nine other markets held two orders each; one was
temporarily blocked by target/price movement. There were 96 viable alternatives
and zero position, unresolved or unknown-absent state.

The local implementation records the exact authorizing program ID, confirms
end only from a successful response carrying that same ID and an explicit
elapsed `ends_at`, requests immediate incremental top-up, and releases only a
flat/order-free/unresolved-free owner. Missing rows and different program IDs
cannot release. The existing near-flatten-deadline guard, retained orders,
strict stream rebind/backoff and exact 20 cap are unchanged.

Projection now intersects the nominal day-of interval with explicit program
bounds at both selector and live evaluation call sites. This corrects the false
duration without adding a remaining-time ranking preference, league filter or
shorter horizon.

All 598 local tests pass and seven deliberate mutants were killed. Deployment
acceptance must prove:

1. graceful stop obtains venue-backed flat proof before files change;
2. deployed hashes match and the complete droplet suite passes stopped;
3. restart is still `auto 10`, `max_markets=20`, six-hour horizon and 4 req/s;
4. an elapsed reward row logs `reward_ended=1`, retains healthy slugs and adds
   no more than its exact free count;
5. the replacement active/subscribed tuple remains at 20 when supply permits;
6. no ownership, unknown-absent, recovery-loop, loss-stop or position defect
   appears. Raw working-order count may still be below 20.

Deployment is complete as `2e3c677`. All 11 hashes matched and all 598 tests
passed on the stopped droplet in 56.392 seconds after a graceful `flat:true`
shutdown. The unchanged size-10 runner restarted at about 13:34:05Z;
`[queue-measurement]` reported `markets=20`, and by 13:38:41Z it held 32
working orders across all 20 markets, all original quantity 10, with zero
unresolved intents, zero `UNKNOWN_ABSENT` orders, zero positions and no new
critical fault marker. Still observe the first natural `[top-up] ...
reward_ended=N` event and confirm exact retained/added counts; do not induce a
reward outage or boundary on the funded bot solely for acceptance.

## DEPLOYED AND RESTARTED: refill capacity rejected during startup validation

The 2026-08-25 run proved 97 viable markets under the live six-hour policy but
bound 18/20 because two selected candidates became non-open during preflight.
That safe drop exposed a scheduler hole: incremental top-up required an
expired-flat bound market and did not consider free capacity itself.

The local repair makes under-capacity an immediate top-up trigger, retains all
existing markets and resting orders, adds only the exact free count, and does
nothing to streams when no replacement exists. The 20 cap, 4 req/s limiter,
quote-size/risk economics, ownership gates and near-flatten-deadline inventory
guard are unchanged. All 585 tests pass; three deliberate mutations prove the
free-capacity trigger, immediate startup timing and exact cap.

Deployment acceptance must prove:

1. graceful shutdown reports/proves flat before files are replaced;
2. deployed hashes match and all 585 tests pass on the stopped droplet;
3. restart remains `auto 10` with configured `max_markets=20`;
4. startup or immediate `[top-up]` reaches an exact ordered 20-market active
   and subscribed set when viable supply remains sufficient;
5. no position, unresolved/unknown-absent ownership defect, risk stop or
   stream-rebind loop is introduced;
6. newly added slugs reach ordinary `[action]` planning. Working order count
   may legitimately remain below 20 and is not acceptance by itself.

Future keyword/prompt: **“run a liquidity-bot 20-slot capacity audit”** and
use the exact-layer checklist in the newest FINDINGS entry. Do not rerun the
heavy authenticated viable-market diagnostic while the funded runner is live;
the current result is already 97 viable for 20.

Deployment is complete as commit `73d5c73`. All six file hashes matched and
all 585 tests passed on the stopped droplet in 54.013 seconds. A single direct
venue snapshot proved zero open orders and zero positions after the operator
manually flattened; the stale shutdown PID was then terminated because twelve
canceled GET-by-ID reads remained NotFound despite that complete flat proof.
The size-10 supervisor/runner started at 12:53:39Z/12:53:40Z. Startup bound 20
without needing a refill and `[queue-measurement]` independently reported
`markets=20`. At the first converged checkpoint: 20 PLACE intents across 11
markets, 18 working orders across nine markets (all quantity 10), zero
unresolved intents, zero `UNKNOWN_ABSENT`, and zero positions. No risk stop,
runner failure or stream-rebind loop appeared.

## DEPLOYED AND RESTARTED: maintenance-aware automatic recovery

The 2026-08-25 maintenance incident exhausted the 12-attempt both-stream
recovery at 11:14:26Z. Shutdown could not prove the stale ledger flat, so the
runner correctly recorded `flat:false` and exited 3; the supervisor correctly
did not restart an account whose state was unproved. That safety combination
also meant the bot could not come back by itself after the venue recovered.

The local repair keeps the same process alive and dormant only for positively
identified transport/HTTP-5xx failures. It probes complete authenticated open
orders, positions and balance read-only. Three consecutive complete passes one
minute apart (about two minutes) are required; any failed pass resets the
streak. The normal component recovery then still has to cancel known quotes,
reconcile exact order/position state, bind fresh streams and pass the existing
private-state gates before quoting. Absence is never cancellation. A malformed
response, NotFound, 429, ownership conflict or runtime invariant is not called
maintenance and keeps its existing fail-closed path.

Startup uses the same in-process hold after three consecutive venue failures,
so maintenance no longer consumes the supervisor's three-fast-crash budget.
Private, market and both-stream runtime recovery use it after their ordinary
12-attempt bound. Logs and alerts use `[maintenance-pause]` with
`state=entered`, `health=success|failed`, `stable=N/3`, and
`state=health_gate_passed`.

The 20-slot scheduler, active-set selection, action planning, REST ceiling,
quote quantity and all risk/economic gates are untouched. A dedicated test
binds 20 slugs, drives maintenance through the private path and proves the same
20 are subscribed after recovery with zero creates during the pause.

Local verification: **581 tests pass** plus three deliberate mutation checks
(failed health must reset the stable streak; response-contract errors must not
be classified as maintenance; recovered private subscription must retain all
20 slots). Commit `feba761` was pushed; all eight deployed file hashes matched,
and the droplet's own environment passed all 581 tests in 54.764 seconds. The
size-10 supervisor/runner restarted at 12:18:55Z/12:18:56Z on 2026-08-25.

Initial live acceptance found two selected candidates had become non-open
during preflight, so the safe startup drop bound 18 rather than pretending the
slate still held 20. The unchanged capacity remains 20. By 12:26:15Z the run
had 37 placements across 17 markets and 23 working orders across 12 markets,
all at quantity 10, with zero unresolved intents, zero `UNKNOWN_ABSENT` rows
and zero positions. No maintenance, runner-failure or risk-stop marker appeared.
Natural acceptance completed on 2026-08-27. Private recovery entered
`MAINTENANCE_PAUSED` at 08:41:27Z while `GET /v1/orders/open` returned HTTP 500.
Supervisor and runner remained alive through the 10:36Z checkpoint, with
one-minute probes correctly held at `stable=0/3` and no post-entry trading
action. Health succeeded once at 12:20Z, failed and reset at 12:21Z, then three
complete passes at 12:22Z, 12:23Z and 12:24Z produced
`state=health_gate_passed`. The same runner reconciled, rebound fresh streams
and resumed safe quoting. At 13:04Z the venue and ledger agreed on 19 working
orders across ten markets, zero positions, zero unresolved intents and zero
`UNKNOWN_ABSENT`; queue telemetry still proved all 20 slots. Both halves of
the maintenance path are now naturally accepted. Do not induce future outages.

## DEPLOYED AND RESTARTED: dual-stream recovery no longer waits on one absent cancel

The 2026-08-24 21:31Z top-up bind failed while five owned GET-by-ID reads
returned NotFound. Both streams closed as designed, but the recovery path then
required terminal proof for every cancel and retried indefinitely. Four
acknowledged CANCEL intents remained `UNKNOWN_ABSENT`; two position rows were
nonzero. The process was alive but all twenty bound markets were dark.

The local repair brings this sibling path in line with private recovery:
cancel all positively-known working orders, apply complete account snapshots,
rebind both streams with scoped order reads, and let the existing per-market
gate hold only ambiguous owners. Ordinary binds remain strict. Genuine bind
failure is capped at 12 attempts and never grants flatness or restartability.

Review is complete: the production-shape ambiguous-cancel regression, the
12-attempt failure regression, normal strict-bind coverage, prior private
recovery tests, and all twenty-slot tests pass. Full local suite: **573 tests
in 36.801s**. Terminal quarantine, ignored scoped reads and an omitted retry
bound were each mutation-tested and each made its regression fail.

The old runner was stopped through the existing shutdown safety path. It spent
6m8s resolving venue consistency lag and then recorded `flat: true`; the
read-only ledger agreed with zero working orders, positions and unknown-absent
rows. Commit `bec8a83` was pushed, all seven deployment hashes matched, and all
573 production tests passed in 50.894s. The supervisor/runner restarted at
22:01:07Z/22:01:08Z with the existing size 10.

Initial acceptance is complete:

1. `DEPLOYED_COMMIT=bec8a83`, matching hashes and 573/573 production tests;
2. fresh startup with exactly 20 queue-measurement/bound markets;
3. 18 placement intents across 18 distinct markets, with 17 working at the
   checkpoint;
4. zero positions and zero `UNKNOWN_ABSENT` rows; one ordinary acknowledged
   PLACE remained in flight;
5. no runner failure or account-wide recovery loop.

Do not induce a both-stream failure on a funded account. On the next natural
failed bind, complete live acceptance by confirming
`[bound-stream-recovery] resumed=yes scoped_ambiguous=N`, actions on unaffected
owners, and no building action for any listed ambiguous owner.

## DEPLOYED AND RESTARTED: size 10 with $70/$80 account stops

The operator authorized quote size 10, a $70 realized session stop and an $80
marked-equity hard stop on 2026-08-24. Graceful shutdown completed before the
change. The $40 per-market stop stays unchanged. Size-derived limits become an
$8 early-exit cap, $90 per-market exposure and $3 execution-fee cap. The
20-slot scheduler, 8 + 8 + 4 private layout, six-hour horizon, 4 req/s REST
ceiling, reward economics and exit timing are unchanged. The same-day restart
drawdown test now derives its scenario from the configured hard stop. Focused
tests, three deliberate configuration mutations and all 551 local and
production tests pass. Commit `13bf261` was pushed and all eleven deployed
hashes matched. The first start recovered one residual size-5 position and
proved flat; the normal runner started at 16:56:06Z. Acceptance observed 20
bound slots, 18 instantaneously quoted markets, 33 working orders all at
quantity 10, and zero position, unresolved intent or unknown-absent order.

## OPEN: graceful stop reported success although the runner recorded `flat: false`

At 16:48:24Z the old size-5 runner received SIGTERM with two positions. It
canceled building orders and parked the reducing orders, then ended at
16:48:55Z with `stop_reason=operator_sigterm` and `flat=false`. The stop script
returned success because it checks only that the runner process disappeared;
it does not inspect the final flat proof. On the next start, recovery-only mode
canceled the remaining reducer, crossed the residual +5 position, proved
`flat=true`, and exited before the normal runner could quote. No size-10
building risk overlapped the residue.

This is now an operator-visible acceptance gap: never treat a successful
`stop_commissioning.sh` exit alone as flat proof. Read the final JSON record or
query the venue. Review whether the script should fail when the latest run is
non-flat, while preserving invariant 32's explicitly bounded non-flat exit.

## SUPERSEDED 2026-08-24: dynamic quote size, size 5 deployment

The bot was gracefully stopped and proved flat at 15:52:48Z. The authorized
current size is 5 contracts, producing a $4 maximum early-exit loss, $45
per-market exposure and $1.50 execution-fee cap. Quote size is now a single
persistent, restart-scoped whole setting from 5-10; those three limits derive
automatically. `max_markets=20`, the 8 + 8 + 4 private-stream layout, six-hour
selection horizon, 4 requests/second and $40/$50/$60 loss limits are unchanged.

The review also fixed a diagnostic discrepancy: the runner and the three
read-only selector tools now build policy from the same commissioning config.
This matters because lowering size can deliberately exclude a venue market
whose `minimum_trade_qty` is above 5; the diagnostic must apply the same rule
before calling an empty slot defective. Each of the three derived formulas
fails its mutation check, 52 focused tests pass locally, and all 551 tests pass
in an isolated copy using the production environment. Deployment and a
flat-gated restart completed at 16:15:26Z on `b3ad73e`. The first live dashboard reached
20/20 quoted markets; a venue read found only quantity-5 orders and zero
positions, unknown-absent orders or unresolved intents. Start at another
reviewed size with
`./scripts/start_commissioning.sh auto SIZE`; never change size under live
orders.

## WATCH: standalone dashboard risk bars are not the runner's durable stop inputs

At 16:13Z the dashboard's matched-pair estimate showed $60.97 for both SESSION
and HARD and appeared to exceed both caps. A direct venue/baseline audit showed
current cash/equity $333.4311 against the stored UTC opening $380.4631: actual
drawdown $47.032, leaving $2.968 before the $50 session pause and $12.968
before the $60 hard stop. `dashboard.py` deliberately labels its estimate
directional, but drawing the same matched-pair number on HARD is still capable
of a false alarm. The live runner is correct; reporting should eventually read
the durable baseline plus venue cash/equity the same way.

## FIXED AND DEPLOYED WITHOUT RESTART: reward dates are ET, not UTC

The earnings API groups reward at midnight America/New_York. Daily ledger
trading and report gap windows now use the same DST-aware calendar, and the
dashboard labels its joined number `ET DAY`. Reward totals do not change. The
durable session/hard-stop day remains UTC by design; changing that safety
budget is not part of this accounting correction. The reporting scripts,
shared calendar and tests passed the 547-test production suite and were
deployed without restarting or signaling the live runner.

## FIXED AND DEPLOYED: private reconnect mass-retired the bound slate, then kept resolved owners barred

The repair is implemented, reviewed and deployed as `a4836b5`. Focused terminal repair now rotates
five attributable owners per batch, overlaps their GET-by-ID work behind the
unchanged shared 4 req/s limiter, and does not repeat complete account
snapshots per owner. A positively terminal, order-free, flat
`unresolved_mutation_timeout` owner clears only that reason's transient quote
bar. The alert and `[unresolved-repair]` diagnostics describe the actual state.
All 539 local and 539 production tests pass and the four material changes were
mutation-checked. The bot restarted at 13:01Z. By 13:04Z the dashboard showed
20/20 quoting, zero positions and zero unknown-absent orders; the seven intents
observed during initial quote placement converged to zero unresolved. The
13:02:54Z reconcile named exactly twenty distinct active markets. Do not force
a private disconnect solely for acceptance: the focused recovery path's live
acceptance completes on the next natural disconnect using the diagnostics
below.

Two private-stream disconnects in the 02:50Z live session produced the same
availability failure. Recovery resumed in 36-40 seconds and correctly scoped
ambiguity to its owners, but the global quarantine created 29 CANCEL intents.
Owner-at-a-time terminal repair took 5.8-6.5 minutes at the two medians and
11.4-11.6 minutes for the slowest cancels, so the 60-second mutation threshold
retired 18 markets at 07:20Z and 19 at 11:48Z. The second batch was fully flat
and resolved by 12:00:03Z, but
the transient retirement still leaves `_retired_until` in force for 1,800
seconds. At 12:03Z the runner was healthy and flat with zero unresolved or
unknown-absent state, but only four active orders while the recovered owners
waited for their bars to expire.

Do not weaken the safety rule that absence is never cancellation, and do not
remove global quarantine on private visibility loss. Fix the availability
composition instead:

1. give recovery-created CANCEL owners prompt, rate-governed terminal-proof
   service after the fresh private bind, instead of letting a full-snapshot,
   one-owner background cadence push nearly all of them past 60 seconds;
2. once an `unresolved_mutation_timeout` owner has positive terminal proof, no
   unresolved intent, no working order and is flat, clear its transient quote
   bar rather than retaining the generic 30-minute cooldown;
3. make the retirement alert describe this transient behavior accurately;
4. preserve the existing per-owner gate while unresolved, exit planning while
   positioned, permanent book/plan/action retirement cooldowns, the systemic
   retirement bound, the 4 req/s governor and all twenty-slot tests.

Regression coverage must include 20 owners canceled by one private disconnect,
slow/NotFound GET-by-ID consistency, a mix of terminal and still-ambiguous
owners, a positioned retired owner that cannot release early, repeated private
disconnects, and proof that no building PLACE occurs for an unresolved owner.
Mutation-check both terminal-proof prioritization and transient-bar release.

The 11:47 telemetry audit was not causal: the socket disconnected 26 seconds
before it began, the audit was SQLite `mode=ro` with no venue call, and the same
failure already occurred at 07:20Z. See the newest `FINDINGS.md` entry for the
full measured timeline.

**Priority escalation at 12:31Z:** the first cooldown expired and the bot did
repopulate automatically (`session_creates` 677 -> 711 between 12:15Z and
12:21Z), proving a restart was unnecessary for that recovery. A third private
disconnect at 12:28:09Z immediately repeated the failure: recovery resumed in
37.48s, 31 CANCEL intents covered 18 markets, 30 exceeded 60 seconds and 17
markets retired. By 12:31Z every cancel had terminal proof and ambiguity was
zero, but a +3.1100 position remained under the bot's exit management. Do not
restart or terminate the runner while that position remains. The operator
subsequently closed the position and canceled all orders; graceful shutdown
independently proved flat. The bot remains stopped pending deployment of this
reviewed fix.

## CURRENT LIVE: collect prospective adverse-fill telemetry through 2026-08-27

The next approved restart can begin a measurement-only run. Each building
post-only PLACE intent will atomically retain its exact decision-time book,
actual price-time queue, the queue approximation used by the current model,
Target Size/cutoff, touch-change velocity, projected reward, stressed exit and
expected net. The exact client order ID later supplies the fill/no-fill label;
there is no market/time guessing. Reducers are excluded and an observation
failure is visible in coverage without suppressing the order.

This work does **not** change the 20-slot scheduler, six-hour horizon, market
selection, quote size, reward economics, risk limits, order payload or exit
behavior. The action and observation reuse one position snapshot, so it adds
no placement-time ledger lookup, REST call or public-stream subscription. It
does not filter or group strategy decisions by league. Public trade-volume
columns stay NULL until a separately reviewed trade-stream change exists.
The local 1,000-order benchmark measured about 0.49ms incremental preparation
time and 2.15KB of checkpointed SQLite storage per observed order. Treat those
as local sizing numbers; deployment acceptance must still verify scheduler
cadence, REST rate, book/private freshness, observation coverage and ledger
growth on the server.

Collection began at **2026-08-24T02:51:31Z**. Treat August 24 as a partial
warm-up day; collect August 25 and 26 as training cohorts and August 27 as the
held-out cohort. Review no earlier than **2026-08-28T00:00Z**, and continue if
roughly 300 maker-filled observed orders have not accumulated. Run the local,
read-only report with:

```powershell
$env:PYTHONPATH = "src"
python -B scripts/quote_observation_report.py --database data/commissioning.db
```

Require zero missing observations. Freeze the first two UTC-day cohorts for
training and evaluate the third held out; only then consider a would-refuse
shadow rule. Do not enforce a refusal rule from the historical correlations.
Commit `2acf544` passed 534 local tests and four deliberate mutation checks,
was pushed, and all 18 deployed hashes matched. A disposable migration of the
real 78.0MB ledger preserved all 19,850 order and 1,366 fill rows; all 534
production tests passed in 40.460s. The restart initially encountered the
already-documented parked-position recovery deadlock and created zero building
orders. After the operator manually cleared the remaining order/position, the
normal supervisor and runner started. At **2026-08-24T02:53:52Z** collection
showed 37 required observations, 37 captured, zero missing, zero positions and
the live process was placing across the slate. Do not restart merely to inspect
telemetry; the report opens the ledger read-only.

Live recheck at **2026-08-24T11:47:56Z**: 569 required / 569 captured / zero
missing, all exact links unique and all intents RECONCILED. The payload, side,
post-only, hash, queue-arithmetic, touch-window and timestamp integrity audit
found zero errors. Book age was 0.001226s median and 4.991020s maximum, with
none beyond the 5s decision bound. Labels were 38 maker-filled, 502 terminal
unfilled and 29 still open; 105 observations already distinguish actual queue
from the current model queue. The report verdict remains `COLLECTING` and no
strategy rule is authorized yet.

### DEPLOYED AND COLLECTING: queue-depletion measurement

The measurement-only prerequisite from WATCHLIST 4b is deployed as `6085ca5`.
It adds a separate public-trade WebSocket with a bounded 4,096-print queue;
the live book/private streams and 4 req/s REST limiter are unchanged. Each
building order retains its exact decision-time better-plus-same-price queue
for comparison, then establishes the conservative lifetime baseline from the
first full book received after PLACE acknowledgement. Reducers and exits are
excluded. A 60-second,
upper-bound <= 1.0x-order-size condition records `WOULD_QUEUE_RESET` only; no
cancel or replacement code exists.

The reviewed restart completed at 2026-08-24T18:49:40Z after the old runner
flattened its one size-10 position and proved `flat: true`. Server acceptance
found:

1. the runner stayed at quote size 10 and resumed normal PLACE actions;
2. `[queue-measurement] ... status=collecting markets=20` appeared with zero
   degraded messages;
3. `execution_queue_stream_sessions` shows one collecting session with zero
   drops, decode failures or errors; no public trade occurred during the short
   checkpoint, so first-print delivery remains a natural-event watch;
4. all 38 source and 29 test files matched the server byte-for-byte, and all
   571 production tests passed;
5. 38 measured orders and 38 post-ACK baselines at the acceptance checkpoint,
   with two clean `WOULD_QUEUE_RESET` signals and zero coverage gaps, drops,
   decode failures or errored stream sessions.

Continue watching book/private freshness, REST rate and ledger-growth slope;
history is banded and summary rows persist at most every five minutes rather
than once per book.

Read-only verification:

```powershell
$env:PYTHONPATH = "src"
python -B scripts/queue_measurement_report.py --database data/commissioning.db
```

Collect three full days and roughly 300 maker-filled measured orders with a
post-ACK baseline and zero coverage gaps. `READY_FOR_REVIEW` authorizes held-out analysis only, never an
automatic reset. All 571 local and production tests pass. Mutation checks prove that removing
post-PLACE registration loses the integration row and that double-counting a
trade already reflected by the independent book socket is caught.

---

## CURRENT: monitor deployed cancel-confirmed priority replan

The current 22:11:14Z run proved that CANCEL intents normally become terminal
quickly while their replacement waits behind the full slate. Over 80 joined
touch-change cycles, cancel confirmation was 1.22s median but the subsequent
replacement wait was 39.45s median and 118.63s p90; 21 returned at the same
price and 48 moved at most one tick.

The deployed fix queues terminal CANCEL owners from both private and REST proof,
re-services only those markets before the next full slate, drains stream events
that arrive during priority REST work, refreshes derived economics, and then
uses the unchanged guarded decision path. It adds `[priority-replan]` for live
measurement and does not alter `max_markets=20`, selection horizon, quote size,
REST rate, economics, or risk limits.

Commit `abbbd7379728e3184ac73247dc5cc713214d1005` passed the focused edge
tests, mutation check and all **522 local tests**; its deployed hashes matched
and all **522 production tests passed in 38.392 seconds**. The old runner
proved flat before deployment and the new runner started around 23:11:57Z.
The first three priority batches covered seven owner occurrences in 2.6s,
6.6s and 2.8s. Six observed same-side replacements arrived 19.3-33.7s after
touch cancel, below the old 40.60s median / 120.09s p90. The safety sample had
zero positions, zero unknown-absent rows and no runner failure.

Continue measuring the distribution over a longer run rather than claiming a
new percentile from six replacements. Also keep viable/bound-market count
beside working-order count: immediately before deployment selection reported
11 viable markets and the live account reached 21 working orders across 11
markets. Twenty remains capacity, not guaranteed supply.

---

## PREVIOUS: scoped private/order recovery deployed and live-accepted

At **2026-08-23T21:00:01Z** the private stream disconnected. The deployed
runner eventually recovered itself after attempt 35 and resumed placements at
21:25:48Z, but all twenty markets had been dark for about 24 minutes. The
resumed placements then hit GET-by-ID NotFound consistency lag; per-market
`execution_state_unknown` recovery waited on 3–8 unrelated IDs at a time and
again stopped useful scheduler progress. This is an availability failure, not
proof that any ambiguous order was terminal.

The fix restores private visibility after complete REST account
snapshots and fresh private state while leaving each ambiguous owner behind its
existing per-market gate. Single-market quarantine no longer waits on
positively-attributed read failures in other markets. Global/default reconcile,
unowned or conflicting state, positions, and flat proof remain strict. A
genuine private reconnect loop is capped at 12 attempts; only the independent
shutdown flat proof can turn that into restartable exit 5.

Verification completed locally:

1. focused production-shape regressions pass for accepted-cancel + omitted
   snapshot + GET NotFound, unrelated-market GET NotFound, strict default
   failure, and bounded reconnect failure;
2. the ambiguous owner remains `state_known=False`, the neighbor becomes
   ready, and recovery creates no orders;
3. `git diff --check` is clean apart from the repository's existing CRLF
   normalization warning for the test file;
4. two final edge tests prove snapshot-positive OPEN orders are still canceled
   when the first ID read lags, and later known markets are canceled before a
   global quarantine reports an earlier ambiguous owner;
5. full suite: **517 tests passing in 29.886 seconds**;
6. the dedicated twenty-slot regressions still pass: all twenty slots plan,
   equity refreshes mid-sweep, and execution state refreshes mid-sweep. The
   configured cap and all selection/strategy inputs are unchanged.

The supervisor was stopped and SIGTERM sent before deployment. Shutdown waited
for a real 0.0600 partial fill and its unresolved order rather than claiming
flat; venue reconciliation cleared both, and the runner stopped with
`flat: true` at **2026-08-23T21:47:28Z**. Next: final diff review, commit and
push, deploy source/tests/docs, run all tests on the droplet, restart, and
verify the new scoped-recovery log markers and broad action progress.

The first deployment of source `fe2e813` matched local hashes and passed all
517 production tests in 38.286 seconds. An accidentally-run probe then caused
Cloudflare Error 1015 at 21:51:55Z; the supervisor restarted safely and the new
runner placed 12 orders across six markets with zero positions and zero absent
orders. That probe failure is external and separate from the code defect.

The live run then found a real remaining latency edge: at 21:53:55Z one market
entered `execution_state_unknown`, and the new scoped path did not report its
two unrelated tolerated order failures until 21:54:15Z. It still performed the
foreign GET retry ladders before ignoring their results, pausing the serial
scheduler for 20 seconds. The final revision skips unrelated individual order
reads, absent repairs, and uncertain-cancel retries entirely while retaining
complete account snapshots and strict target proof. Routine/mid-sweep freshness
uses snapshots without individual reads, and unresolved terminal repair rotates
one owner per pass so no permanent 404 monopolizes the queue. The intermediate
runner was stopped gracefully. Focused recovery and twenty-slot tests pass, and
the full local suite passes **518 tests in 28.137 seconds**.

Final source commit `501c743` matched all deployed hashes and passed all **518
tests on the droplet in 37.105 seconds**. The intermediate runner reconciled
the two remaining acknowledged cancels and stopped with `flat: true` at
22:09:48Z. The final supervisor and runner started at 22:11:13Z/22:11:14Z.
Live acceptance emitted `scoped_order_reads_skipped=8` at 22:12:07Z and then
continued actions at 22:12:10Z, eliminating the measured 20-second foreign-ID
pause. It rested 11 orders across the seven currently viable markets with zero
positions. The configured 20-slot capacity and scheduling flow are unchanged;
the current unused slots reflect only the seven-market viable supply.

---

## PREVIOUS: twenty-slot scheduler repair deployed and accepted

The live bot was stopped flat at **2026-08-23T04:10:48Z** before changes. The
repair addresses the observed 0/20 state without changing the six-hour horizon,
quote size, slot cap, REST rate, or risk limits. It includes per-market intent
and backoff isolation, decision-time equity freshness, truthful stream receipt
times/book coalescing/private draining, selector gate parity, strict shutdown
flat proof, and corrected slot reporting.

The scheduler and task-held stream fixes passed **508 tests locally and on the
droplet** (40.514 seconds). The 04:37:47Z live acceptance then placed 33 times
across all twenty distinct markets and reached 26 working orders across 16,
proving the original starvation repair. It also exposed a 30-second
execution-state freshness expiry during the longer productive sweep. The final
local patch refreshes at 25 seconds, stamps freshness at REST completion, and
limits a failed account-wide refresh to one attempt per sweep while remaining
fail-closed. The expanded suite passes **513 tests locally and on the droplet**
(40.881 seconds in production). Source commit `2eed575` is deployed. The
operator stopped the prior run flat at **2026-08-23T04:45:13Z**. The final run
started around 04:56Z and passed the prior failure window: at 10m18s it had 64
actions across 18 action-producing markets, 26 working orders across 14 at the
sampled instant, and zero `execution_state_unknown`, runner failures,
unresolved intents, absent/unknown orders, or positions. It remains running.

Completed acceptance:

1. `DEPLOYED_COMMIT` is source commit `2eed575`;
2. source/test/doc hashes matched the reviewed local files;
3. all 513 production tests passed in 40.881 seconds;
4. the restart remained live beyond ten minutes with broad action distribution
   and no recurrence of the execution-state freshness failure;
5. ledger safety state was clean at acceptance.

Do not combine the restart with a reward-eligibility dwell change. That policy
remains under the explicit measurement rule in `WATCHLIST.md`.

---

## ROLLING BACK THE 2026-08-23 CHANGE -- read this first if things look wrong

**Two changes, one deploy, operator decision 2026-08-23.** Quote size was NOT
touched and stays at 10.

```
selection_horizon_seconds   7200 -> 21600     six hours, back to the full day_of period
max_session_loss_usd          40 -> 50
hard_stop_loss_usd            45 -> 60
max_market_loss_usd           40 (unchanged, now binds before the session stop)
```

**Known-good point: `horizon-2h-stops-40-45`, commit `f41566a`.** Two files
carry the whole change:

```bash
git fetch --tags
git checkout horizon-2h-stops-40-45 -- src/liquidity_bot/market_selector.py                                        src/liquidity_bot/commissioning.py
# deploy both, run the suite ON the droplet, then cycle the bot and write
# DEPLOYED_COMMIT by hand -- nothing does it for you
```

### State at the moment of the change, so "is it worse?" is answerable

```
marked equity            419.91  (was 467.46 at the size-5 tag on 08-20)
reward PAID (bot era)    141.35  + 31.06 PENDING
ET net if pending pays  08-18 +3.65   08-19 +4.35   08-20 +4.22   08-21 +2.27
ET trading/day at size 10   -24.19 to -30.52
resting fraction         53.8% span-weighted (08-22)
median viable markets    2-10 against 20 slots  <- the thing the horizon fixes
```

### Why, and what was argued against it

The horizon restore is well-evidenced: FINDINGS "Testing the horizon change
against realised reward" and the three entries after it. The narrowing rested
on `projected_reward` bucketed by time-to-kickoff, and that quantity is
`constant x (pool x share)` -- it has no term that varies with distance, so the
buckets compared different markets rather than different times. The venue's docs
give no distance penalty either.

**The stop raise was argued against and the operator decided to proceed.** The
case against, recorded so it can be checked rather than re-litigated: equity was
down $47.55 in three days, 2026-08-21 spent 88% of the old $40 budget on an
ordinary day, and raising a loss limit while the account is losing raises the
ceiling on the loss. Changing two things at once also means a bad week cannot be
attributed to one of them.

**So if things look wrong, roll back the HORIZON first** -- it is the change
that adds exposure. The stops do not cause losses; they bound them.

### What to watch

- **Net per day**, not reward per day. Reward will rise with the horizon; the
  question is whether trading loss rises faster. `scripts/daily_pnl.py`.
- **Whether the $50 session stop fires.** If it fires on a day that was not
  genuinely bad, the stop is too tight for this configuration. If it fires on a
  genuinely bad day, it is working.
- **Fill rate.** Correct ET buckets put 08-20 -> 08-21 at 404 -> 855 pairs.
  August 20 changed size mid-day, so that 2.1x move is a warning, not a clean
  size estimate. More markets in play can push it further. This is the
  unmeasured half of the original narrowing argument.

---

## ROLLING BACK THE SIZE CHANGE -- read this first if things look wrong

**Quote size was doubled 5 -> 10 on 2026-08-20 by operator decision.** If the
account starts losing faster than it earns, or anything about the change looks
wrong, go back here. Do not debug first -- roll back, then debug.

**The known-good point is tagged:** `size-5-known-good` (commit `b69293b`),
pushed to origin. It carries the full state in its message.

```bash
git fetch --tags
git checkout size-5-known-good -- src/liquidity_bot/commissioning.py
# then deploy just that file, run the suite ON the droplet, and cycle the bot:
scp -i ~/.ssh/makerlab_droplet src/liquidity_bot/commissioning.py     root@<host>:/root/liquidity-bot/src/liquidity_bot/commissioning.py
ssh ... 'cd /root/liquidity-bot && PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests'
ssh ... 'cd /root/liquidity-bot && ./scripts/stop_commissioning.sh && ./scripts/start_commissioning.sh'
# and write DEPLOYED_COMMIT by hand -- nothing does it for you
```

The revert is **one file**. Everything else in the change is tests and prose,
so `commissioning.py` alone restores the running behaviour.

### What the state was at the tag, so "is it worse?" is answerable

```
quote_size                5          reward PAID (bot era)   120.52, none pending
max_early_exit_loss_usd   4          matched-pair trading    -47.24 / 234 pairs
max_market_exposure_usd   45         unpriced uncertainty     44.45 / 20 markets
loss stops         40 / 40 / 45      marked equity           467.46
```

Seven days of bot era, about **-6.75/day** of trading against reward that ran
$14.60-$34.60/day. At size 10 expect roughly double both.

### What moved, and the one thing that deliberately did not

```
quote_size                5 -> 10    mechanical
max_early_exit_loss_usd   4 ->  8    MUST move -- see below
max_market_exposure_usd  45 -> 90    mechanical
loss stops         40 / 40 / 45      UNCHANGED, deliberately
```

`max_early_exit_loss_usd` is the one that would have bitten silently. The $4
was calibrated on size-5 crossed losses (median $2.32, p75 $3.23). At size 10
those become median $4.64 and p75 $6.46, so a $4 cap sits BELOW the median and
would refuse almost every early cross -- converting the exit policy into "ride
to the kickoff flatten" without anyone deciding to. FINDINGS measures P&L
degrading monotonically with holding time, so that is a real cost.

The **loss stops did not move** because they protect the account, not the
position, and equity did not double. $40 is ~9% of ~$467; $80 would be ~18%.
They keep about 3x headroom over a typical day at the new size, against 6x
before -- so they start being a guardrail rather than a formality.

### Judge it on a clean day, not on today

2026-08-20 is contaminated: the bot was stopped and started several times
deploying fixes, so its reward will understate. **2026-08-21 is the first clean
comparison**, and the 14:00Z probe is already collecting the supply series that
says whether any change in earnings is the size or the slate.

## 00. FIXED 2026-08-20: a restart forgives the day's drawdown

**Landed.** Both stops now baseline on the UTC day, from a durable ledger row
(`execution_day_baselines`), so a mid-day restart inherits the drawdown and
only a new UTC date grants a fresh budget. The hard stop's ratchet is closed
with it. Full write-up and the two mutation checks in `BUGFIXES.md`.

Historical note: this was the correct deployed state at the time. The newest
NEXT item contains the operator-authorized ET policy and guarded cutover; it is
not active until that item is deployed and restarted.

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

## A daily probe is scheduled. Remove it when its two questions close

Added 2026-08-20. `scripts/scheduled_probe.sh` runs at **14:00 UTC daily** from
the droplet crontab and writes `logs/probe-<stamp>.log` -- the reward-period mix
by league, then the selection funnel and its rejects.

It exists for two questions that a single reading cannot answer:

1. **WATCHLIST 9** -- do the tight-book rejects cluster at 1c on a busy slate?
   Sample 1 taken 08-20T14:12Z; the rule wants a second on a different day.
2. **Is the live-only period mix a program change or a daily cycle?** Readings
   at 10:55Z and 14:12Z said setka and itf carried only `live`; a reading at
   21:49Z said 31 of 31 in-horizon markets carried `day_of`. It varies through
   the day, and that withdrew a FINDINGS conclusion six hours after it was
   written. A series answers it; snapshots have now been wrong twice.

**It is TEMPORARY and nothing expires it.** ~50 venue calls a day through the
same 4 req/s governor the runner uses -- harmless, but it is not free and it
should not outlive its purpose:

```bash
crontab -e     # delete the probe line; the nightly ledger backup stays
```

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
