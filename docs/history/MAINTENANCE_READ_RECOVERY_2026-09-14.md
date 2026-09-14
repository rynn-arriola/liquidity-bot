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
