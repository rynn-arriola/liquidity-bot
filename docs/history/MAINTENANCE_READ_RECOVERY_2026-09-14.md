# Maintenance read and idle-book recovery — September 14, 2026

## Scope and evidence

The operator accepted both log-review recommendations with "Ok lets do it":
implement/review outage-read cooldowns and maintenance book recovery. Later
instructions authorize stop/restart after verification and immediate stop
while working. Laptop runner693316/supervisor693287 stopped successfully;
authenticated12:51:02.784125Z proves zero venue orders/positions/unresolved/unknown
state. No force-kill was needed. Prior loaded source was `2a867a581`; review began clean
at `f3de96717`. Available-machine
parity passed; offline PC skipped. Diagnostics made no venue mutations; the
authorized restarted bot later resumed its ordinary trading actions.

Authenticated12:42:58.290401Z: zero venue orders, positions, unresolved intents
and unknown orders; ledger agrees. The existing scheduled hold has a resume
boundary13:00:13.197Z followed by three authenticated health checks. The schedule
and ledger alone cannot prove flatness.

The [official changelog](https://docs.polymarket.us/changelog) was reopened:
v0.0.90 announces September14 maintenance05:00–08:00 ET. Direct web-tool status
fetch failed; the bot's dated official completion observation12:00:59Z remains
the available operational evidence, not a fresh global service-health claim.

The09:26:23Z API window reported230 attempts/61 seconds and199 failures,
including170 open-order503s. Later windows repeated open-order/position503s;
09:30:30Z included a positions429. Inspection confirms only429 opened the
endpoint circuit; account-proof503s could continue into per-owner recovery.
12:29–12:32Z windows were1.594–1.649 requests/s with zero failures/429s.
Those flat paused windows do not establish active-trading performance.

12:14–12:23Z logs show positively empty books increasing per-market recovery
counts through12 attempts during the hold. Retired owners later continued
stale-book diagnostics. Stale counts do not prove a broken public socket;
empty depth is market-specific. A shared renewal failure12:15:14Z already used
the coordinated recovery path. No further transport replacement is justified.

## Design and implemented behavior

Extend the existing per-account-endpoint circuit. HTTP500/502/503/504 and typed
network failures receive2/4/8/16/30-second spacing, Retry-After bounded to60s,
and one20-second recovery lease across cooperating lanes/processes. No inline
retry. Cash/trade history share the activities circuit. Only actual429 counts
as rate-limited telemetry. A current successful probe clears the circuit;
older success cannot clear newer failure, and cancellation expires the lease.
Schema, auth,404, arbitrary local errors and implicit context do not qualify.

Typed account outages survive exact-reader aggregation and yield the decision
sweep instead of launching recovery on each market. Other endpoints and mutation
admission remain available; exits still require all fresh exact evidence. The
bounded delay reduces retry amplification but cannot guarantee zero future429s.

During a known scheduled evacuation/maintenance/resume-health hold, skip price
refresh/planning only when the account gate is ready and the owner is flat,
working-order-free, absent-order-free and unresolved-intent-free. Account and
stream monitoring, lifecycle checks, positions, cancels and ambiguity recovery
remain active. Keep the book due time, so release immediately returns through
ordinary fresh-book acquisition/planning. Never re-stamp old depth, release a
real retirement, reset a cooldown or bypass post-maintenance health checks.

Rejected alternatives: generic retries amplify traffic; treating every error
as transient hides integrity defects; full-slate REST books recreate the public
book dependency; clearing retirements at resume erases real faults. No new
source, subscription, poller, rate, strategy, size, cap, baseline or schema.
Manual REST, size10/32, max30 flat entries excluding position orders, time/loss
tiers, exit deadlines and $90 HARD are preserved.

## Verification and dedicated edge audit

Tests cover each server status, raw/wrapped network errors, schema/auth refusal,
cross-process/lane exclusion, mixed429/5xx backoff,32 recovery callers, older
success fencing, canceled lease, malformed state and independent mutation
admission. Existing reducer tests additionally cover503/504/timeout fanout,
typed exact-read propagation and ownership-conflict precedence.

Maintenance regressions cover32 empty owners across scheduled phases, REST and
private-mode fixtures, post-health fresh-book recovery, outstanding orders,
absent orders/intents, failed account proof, stop and a late fill reactivating
the unchanged maintenance exit. The prior70-test related slice passed; final
suite evidence is recorded below.

Four reversal groups load real baseline functions in memory. Old result
accounting fails server/network suppression, old pressure classification fails
the32-owner fanout regression, and old book scheduling fails the no-request
regression. All are assertion failures; import/setup errors are rejected.
Patches restore immediately; production and tracked source remain untouched.

Final dedicated review: empty/full32 scope, phase/health boundaries, late
exposure/ambiguity, original receipt freshness, stop, leased-probe cancellation
and old-completion races retain existing proof rules. Normal operation adds
only a false short-circuit predicate, with no ledger read. Held owners remain
unconditional; all account projection/mutation stays on the serial owner.
Shared state adds only an optional timestamp and remains readable by rollback
code, though rollback lacks these repairs. No ledger migration/risk reset.

## Release boundary

Final reviewed native WSL copy passed all1,681 tests in167.701 seconds. An
earlier full run exposed one old test expecting503 aggregation; its assertion
now checks the intended typed503 propagation while preserving other-reader
completion,404 handling and invariant precedence. No production fix was changed
to satisfy that expectation. Four reversal groups, normalized source hashes,
final diff review, `git diff --check` and the documentation audit passed.
The12:50:46.180420Z terminal record says `operator_sigterm`, `flat=true`, followed
by the independent12:51:02Z authenticated flat/clear proof above.

Stop/deploy/restart is authorized after verification. Future natural
acceptance must prove bounded outage reads, no per-owner fanout or empty-owner
book retirement during maintenance, fresh-book trading after all holds, and
proper exits/cancel proof with exposure. No fault or fill may be induced.

## Deployment and initial live acceptance

Pushed `c7b6762d71433b640e4468bacaf00f32f8726ba8` before deployment. The clean
WSL runtime fast-forwarded through a local git bundle;197 normalized tracked
source/test/script/config hashes matched. Its exact installed full suite passed
all1,681 tests in167.598 seconds. DEPLOYED_COMMIT records that release.
Available Windows/GitHub/WSL parity passed with the offline PC explicitly skipped.

Fresh13:01:32.058822Z authenticated orders/positions and ledger ownership were
all zero. Authorized restart13:01:40Z launched supervisor2305886/runner2305915
with manual REST, quote size10 and32 slots. The scan completed in6.7 seconds,
with46/46 streamed scan books and23/23 final books, no REST-book fallback.
Entries began normally. At13:03:05Z the runner observed one natural +10 position;
authenticated13:07:33.976985Z proves29 flat entry orders plus one OPEN SELL10
covering it. Unknown orders were zero; one new in-flight intent was present.

The first five API windows contain1,035 attempts and13 failures:11 individual
order404s and two real429s (balance in13:06:18Z window, positions in13:07:20Z
window). Rates were3.034/2.922/3.206/3.095/2.972 requests/s. The balance cooldown
had cleared, while positions showed its bounded2-second hold in that window.
There was no per-owner runtime-recovery fanout or retirement; ordinary
cancel-terminal observations and intent repair continued. This is not a
zero-error or WebSocket-speed claim, and low flat-paused load is not comparable.
No natural hard exit or new maintenance outage occurred in this initial sample.

Seven-minute follow-up: both processes remain running. The13:08:21Z window had
one execution-history429, one order404 and213 attempts/60.9s (3.500/s), including
83 exact-order lookups. Admission max5.511s and decision p95 17.444s prevent a
latency-parity claim; all reported cooldowns cleared. Six windows total1,248
attempts,15 failures (12 order404s/3 account429s), with no per-owner recovery.
Authenticated13:08:56.459883Z proves28 flat entries plus the same exact SELL10
covering +10, zero unresolved intents and zero unknown orders; ledger agrees.
The7-minute acceptance confirms continued operation/coverage, not complete
elimination of venue throttling or a natural timed exit. Sustained account-read
load and future maintenance acceptance remain observational open items.

## Read-only status follow-up, September14 16:43Z

Same supervisor2305886/runner2305915 remains running after3h41m on the same
deployed marker, manual REST size10/32. Authenticated16:43:09.707090Z returned
36 venue orders:30 flat-market entries and six exact reducing orders covering
all six positions (including1.36- and6-contract residuals). No unresolved
intents or unknown orders. The ledger read taken after the venue reads had35
working orders; these active reads are not atomic and do not prove a lost order.

Fifteen completed API windows since16:27Z contain3,018 attempts,13 failures and
zero429s; latest rates3.280/3.371/3.395/s with no cooldown. Individual-order404s
remain visible; max admission wait in the slice5.274s. No runtime-recovery
fault appeared. The16:42:28Z scheduler reported decision p9515.217s, so smooth
WebSocket-equivalent latency is still not established. The Tomgen/Alawaz
hard_inventory_timeout IOC16:33:05.800852Z was followed by observed flat at
16:33:09.474600Z (3.674s). Another natural flat was observed16:29:45Z. Current
position coverage and these completions do not prove every future deadline.
No code/configuration change, venue mutation by diagnostics, or restart.

## Automatic public-stream recovery, September14 20:05–20:08Z

At20:05:38.981178Z bound_streams attempt1 reported public book renewal failure.
At20:05:59.779400Z shared recovery quarantined working orders, followed by
positively observed terminal cancellations20:06:26Z. Source inspection confirms
rebind, quarantine and reconciliation precede normal decisions. This explains
the disappearing quotes. Underlying transport cause is not established by the
wrapper error. Earlier exact pregame-state-unavailable events are separate guards.

Same processes remained up7h05m. Entries resumed20:06:44Z without restart.
Authenticated20:07:22Z:12 flat entries/five covered positions;20:08:14.300573Z:
29 flat entries plus five exact reducers for +10,+4,-1.77,-10,+10, zero
unresolved/unknown orders, ledger34 working agrees. This proves recovery and
rebuilt coverage, not uninterrupted exit coverage. The Naghan residual exit
acknowledged20:06:35Z at a logged34.173s past deadline, then flat20:06:37Z;
Konrom exit acknowledged20:07:02Z at10.357s past deadline, then flat20:07:04Z.
Recovery scheduling delay needs narrow diagnosis; eventual flat is not on-time proof.

Fourteen completed windows since19:50Z through20:04:48Z had3,259 attempts,
18 failures and zero429. These precede recovery and cannot prove zero rate
limits across the entire event. Observed trigger is a public-stream failure,
not a logged429. Changelog reopened: newest maintenance announcement remains
September14 05–08ET; live status web fetch failed, so no global-health or
maintenance attribution. No diagnostic venue mutation, code/config change or
restart. Existing $90 HARD and30-flat-entry cap remain unchanged.

### Cause trace

The immediate public-feed trigger is local and specific: `fresh_book()` started
the shared renewal worker because its usable snapshot was stale. `_subscribe()`
allows three seconds total for the SDK `unsubscribe(previous)` and subsequent
`subscribe_market_data(new_id, slugs)` sends. The worker caught a failure from
that bounded control operation at20:05:38Z and permanently marked the shared
stream failed. Because neither SDK call awaits an application acknowledgement,
the available evidence proves a send/control operation failed or exceeded the
three-second local timeout; it does not prove which send stalled or whether the
underlying cause was laptop networking or venue transport. The generic
`public book renewal failed` wrapper did not print its nested exception, which
is a diagnostic gap. A socket close/error callback would instead have stored a
disconnect/subscription-failure label, so neither was the directly observed path.

The exit delay has a stronger causal trace. `bound_streams` recovery is awaited
inside the single decision owner. It refreshed account state, rebound both
streams, then globally quarantined and reconciled working orders before returning
to the ordinary loop. Recovery started20:05:38Z, before the Naghan position's
approximately20:06:01Z hard deadline, and did not return to urgent action until
after the bulk cancellation proof around20:06:26Z. Its IOC acknowledged
20:06:35.098344Z,34.173 seconds after the recorded deadline. Therefore the
late exit was caused by serial recovery work taking priority over a deadline
that became due mid-recovery—not by a429 and not by failure of the IOC itself.
The IOC then produced flat proof2.166 seconds later. Fix design must retain
fail-closed stream quarantine while allowing already-owned urgent exposure to
preempt or interleave safely; no such behavior change is authorized here.

## Prepared public-recovery exit-priority repair

The operator then authorized the repair and explicitly said not to restart.
The selected design changes only shared public-feed recovery while manual REST
account state is active. `_open_streams_for_active_market()` already completes
a fresh account-wide REST reconciliation before recovery can continue. The
prepared path reuses that exact proof instead of immediately repeating it,
checks positioned owners before flat owners, preserves only one order whose
side and remaining quantity exactly reduce the current proved position, and
runs the existing serial position-only service before and between flat-order
cancellations. New entries remain impossible until shared recovery finishes.

A wrong-side, wrong-quantity, duplicate, absent or unresolved order cannot be
preserved. An uncovered/due position enters the existing planner, executor,
durable-intent and proof path; there is no parallel mutation owner and no new
IOC authority. A stream failure during a checkpoint aborts into the existing
bounded recovery loop. The final reconciliation and per-market ambiguity gates
remain. Private WebSocket account recovery does not use the exception because
its lost account transport cannot independently prove whether a fill landed;
it retains the original whole-slate quarantine. This fault-evidence boundary
is why a general "never cancel reducers" design was rejected.

The public renewal wrapper now includes the underlying exception type/message
while retaining exception chaining. This adds no request and does not claim
whether a future failure belongs to the laptop or venue; it makes that cause
observable instead of replacing it with a generic label.

Focused regression covers REST-only activation, unchanged private recovery,
exact reducer qualification, wrong side/quantity, duplicate, absent and
unresolved state, positioned-owner ordering, position checkpoints between
flat cancellations, coordinated429 recovery and nested renewal diagnostics.
The related recovery/account-source/position group passed270 tests. Deliberate
reversals proved both activation and nested-error regressions fail when their
repairs are removed, then the fixes were restored. The complete Windows run
executed1,685 tests with only the four expected POSIX-lock refusals; the native
WSL run then passed all1,685 tests in390.864 seconds.

Dedicated edge audit: success retains exact reducers and resumes only after
fresh proof; empty and flat owners add no preservation; full32-slot ordering
is stable within positioned/flat groups; partial fills make a changed reducer
quantity fail qualification; ambiguity cannot authorize mutation or reuse;
stop strings return to the existing shutdown owner; a second public failure
re-enters bounded recovery; private mode and rollback retain strict global
quarantine. API load removes one duplicate complete reconcile only on the
already-proved REST path and adds no cadence/poller/subscription. Quote size,
slot count, entry-order ceiling, reward rank, entry quality, entry/exit prices,
inventory clocks, hard-exit bounds, maintenance rules and $90 HARD are unchanged.

The distinct market-only recovery route was also reviewed and deliberately
left strict. It has no fresh replacement public book until after quarantine,
so servicing positions earlier there would rely on stale prices; reconnecting
first while old entries remain live would weaken fail-closed behavior. The
observed renewal failure requested shared bound recovery, where the new narrow
REST-account exception has both fresh account proof and replacement books.

The live bot was not stopped or restarted and this source was not copied into
the production checkout. Therefore none of the prepared behavior is active;
the running process remains on `c7b6762d7` until a later operator instruction.

## Command 1 deployment, September14

The later Command 1 authorized the complete stop/deploy/restart workflow.
Supervisor2305886 was stopped and runner2305915 received SIGTERM. Its final
record at20:57:46.295651Z reports `operator_sigterm`, `flat=true`. Independent
authenticated20:58:08.568896Z proof found zero venue orders/positions, zero
unresolved intents/unknown orders and zero ledger working orders/positions.
The graceful shutdown flattened its remaining exposure through existing exits;
there was no force-kill or manual ledger repair.

Windows/GitHub were clean at `2166dd2634582934e42c6f9fec0d4845ac2297d7`.
Pre-deployment parity verified a private stopped-ledger recovery copy captured
20:58:32.641178Z,430,809,088 bytes, SHA-256 prefix15b94f71376d. The offline PC
was skipped. The clean WSL checkout fast-forwarded from `1b8b187d4` through a
local git bundle;194 normalized source/test/config/script hashes matched both
the reviewed commit and Windows. Existing regression/reversal/full-local-suite
evidence was reused; the final deployment diff is the same reviewed change.
No source or configuration was changed during this deployment task.

The exact installed runtime passed all1,685 tests in241.884 seconds and all194
hashes were verified again afterward. DEPLOYED_COMMIT was set to `2166dd263`.
The fresh21:04:10.946602Z authenticated gate again proved zero venue and ledger
orders/positions, zero unresolved intents and zero unknown orders. The authorized
launcher then started supervisor3353887/runner3353918 at21:04Z with size10,
32 slots and explicit REST account mode. Existing $90 HARD, max30 flat-entry
ceiling, position-order exclusion and durable loss state were preserved.
Acceptance reads start from log byte82,338,858 so older errors cannot be
misattributed to this run. Native test output is in the ignored runtime file
`logs/release-2166dd263-tests.log`.

At21:07:05.710637Z authenticated proof showed30 flat entry orders and two
exact BUY10 reducers covering two -10 positions, with zero unresolved/unknown
orders. The first reducer acknowledged21:06:07.992460Z,0.083823 seconds after
the position was observed. That position naturally flattened21:09:29.183834Z.
Authenticated21:10:03.315747Z then showed30 flat entries plus one exact BUY10
covering the remaining -10 position, zero unknown orders and one in-flight
create. The log positively resolved that create at21:10:04.770672Z. Venue and
ledger reads during active trading are not atomic; their one-order difference
does not itself show missing ownership.

The first five completed API windows through21:10:03Z contain1,065 attempts,
17 individual-order404s and zero429s. Rates3.284/3.251/2.952/2.930/3.052 per
second; all reported cooldowns zero, maximum admission wait1.217 seconds.
No runtime-recovery failure or retirement appeared. The latest scheduler
decision p95 was8.699 seconds, compared with37.496 during startup; these are
observations rather than a WebSocket-performance equivalence claim. Both
processes remained up at5m55s. No shared renewal failure, hard exit or new
maintenance occurred, so natural recovery/deadline acceptance remains open.
No fault or fill was induced and no further restart was needed.
