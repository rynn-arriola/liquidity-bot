# Optional book load control — September 13, 2026

## Authority and scope

After the [daytime comparison](DAYTIME_RELEASE_LOAD_COMPARISON_2026-09-13.md),
the operator said “Ok lets build from that.” This authorizes the targeted
repair and review, not deployment/restart. Production stays stopped on
`4a166336a`; retain $90 HARD, size 10 / 32, explicitly selected REST and its
maximum 30 flat pending entries. Positioned-market orders and reducers/exits
remain excluded. No ledger, policy, credential or supervisor change.

The earlier REST run averaged 1.767 requests/s versus 3.550/s after the daytime
release. This is measured load change, not proof of the venue's specific quota
or that one commit caused its first rejection. Keep the CPU index, corrected
trade-history query and safety fixes. Do not restore faults to slow execution.

## Repair

- The production shared governor admits an optional public book read at most
  once every four seconds across cooperating processes. Aggregate admission
  stays 4/s; metadata, mandatory account proof and mutation policy are unchanged.
- Discovery and startup candidate books use that allowance. A long selection
  wait continues the existing stop/position/deadline checkpoint outside every
  lock; no future aggregate/safety slot is reserved while waiting.
- Routine flat-owner book service and its immediate stale-book recovery use
  the same allowance but defer instead of sleeping through four seconds per
  owner. Local deferral neither cancels resting orders nor increments broken-
  market failure counts, renews a receipt, or authorizes a stale-price entry.
- A bounded process-local FIFO of live demand prevents the first quiet owner
  from repeatedly taking every available slot. At most 128 names are held;
  inactive demand expires after max(5 seconds, optional interval + 1). This
  list is scheduling only, not persisted ownership or a host-slot reservation.
- Held positions, ambiguity, recovery/retirement, economic replacement dwell,
  shutdown and actual non-stale-book recovery retain the safety path. Safety
  book reads do not move the optional clock and cannot starve it by extending
  that clock. A confirmed public-book 429 still applies its existing endpoint
  circuit to all book readers; a safety label cannot bypass unavailable depth.
- One successful recovery probe clears the active book circuit but no longer
  erases recent rejection history. Alternating success/429 retains bounded
  adaptive backoff; the existing 60-second quiet interval resets the streak.
- Selection recalculates time-dependent reward/pregame eligibility at completion
  of paced reads. These observations remain selection hints; execution still
  requires independently fresh book, lifecycle and complete account evidence.

## Performance limits and tradeoff

Fresh public WebSocket books remain immediately usable under their original
receipt times. Optional REST-only refill and quote refresh can be slower: 32
continuously quiet owners require about 124 seconds between first and last
optional read, plus other latency. This is not a four-second position polling
interval or a relaxation of the five-second execution-book limit. Account
polling stays completion-based 5/1 seconds with unchanged complete proof.

The earlier stopped [bounded paced scan](PRE_REST_RATE_INVESTIGATION_2026-09-13.md)
completed 100 books and 12 catalog requests without a 429 in 412.69 seconds.
It predates the final runtime/nonwaiting/fairness code; it is not acceptance of
this release, a controlled A/B quota test, or proof of live 30-entry capacity.
No new venue requests or trading mutations were made for this implementation.
Reduced optional pressure cannot guarantee no future 429 or WebSocket-equivalent
entry/fill/exit latency; mandatory safety/account traffic remains essential.

## Verification and dedicated final edge audit

Focused runner/selector/endpoint suites: 507 tests passed in 70.528 seconds.
Six deliberate repair reversals each produced the expected assertion failure
and zero errors, then restored automatically, including removal of full-width
fairness. The final complete suite passed **1,580 tests in 212.769 seconds**
using the production Python 3.12 environment against the reviewed Windows-mount
source. Temporary-directory ResourceWarnings were nonfatal; no test failed.
After completion, the final diff/edge review confirmed no additional defect
requiring a code change. Documentation audit: 134 Markdown files, 961 mandatory
lines, zero issues; whitespace diff check passed.

- Success/empty/full: real gateway plus offline fake clients; local and POSIX
  shared admission; configured factory; full 32-owner fairness simulation with
  safety position/account reads on every tick; 128-name bound/expiry.
- Timing/boundaries: exact four-second admission, no claim while waiting,
  corrupt/future timestamps bounded, checkpoint interruption, clock crossing
  game start/reward end, independent streamed receipts and unchanged 3/5-second
  quiet/fresh-book guards. Source selection remains explicitly manual.
- Failure/unknown/ownership: actual 429 remains endpoint-scoped; local refusal
  sends zero requests and preserves order/failure/receipt evidence; flat stale
  recovery cannot evade the allowance. Nonzero/absent/unresolved/recovery owners
  bypass optional pacing. Complete account proofs and no-absence inference
  remain unchanged; no mutation retry or ownership adoption was added.
- Shutdown/restart: operator stop interrupts waiting discovery; later shutdown
  safety remains available. The optional shared timestamp is bounded and
  additive; old code ignores it, process-local demand is disposable. Rollback
  must still follow normal stopped/known-state deployment gates. No database
  migration, durable clock reset, source promotion or risk/cap adjustment.
- Load/priority: optional reads cannot reserve later aggregate slots; checkpoint
  reads stay in safety and do not recursively call the checkpoint. Stale queued
  demand expires. Safety bypass affects optional pacing only, never venue 429,
  account freshness, IOC attempt/deadline, price/depth or exact quantity guards.
- Final diff reviewed for policy drift, secret exposure and scope: only the
  governor, read classification and selector clock repair plus tests/docs.

## Remaining release gates

No restart/deploy is authorized by this work. Obtain explicit release authority,
fresh authenticated flat/known ownership proof, reviewed deployment hashes and
the exact deployed suite before restart. Then monitor natural request-category
rates, 429s, admission waits, entry fill-up and reducer/IOC timing without induced
fills or load tests. Do not claim sustained acceptance from a stopped probe.
Desktop parity remains pending while offline; production intentionally stays
behind the newly prepared source.

The final process check around 05:45Z found no commissioning runner/supervisor;
the deployed marker was still `4a166336a340f99003c6e4d5810283f02e363eda`.
Last authenticated account proof remains zero orders/positions at 04:22–04:23Z,
and last ambiguity proof zero at 04:16Z. Neither is represented as a fresh
exposure query. Pre-work parity found laptop/GitHub/WSL documentation HEAD
`b4b3211b9`, intentional local source drafts and offline desktop; its verified
private ledger recovery copy was captured 05:33:19Z, 388,657,152 bytes,
SHA-256 prefix `63c773d6be11`. No copy became a writable ledger.

## Subsequent authorized deployment — September 13

The operator subsequently said **“Ok restart bot”**, superseding the earlier
hold for this reviewed release only. Keep every persisted business/risk choice,
explicit REST mode and flat/known-state startup gate; no automatic source switch.
At **05:50:40.093071Z**, the fresh read-only account/ledger gate proved zero
venue orders, positions, unresolved intents, unknown orders and ledger exposure.
Pre-work parity captured a new verified stopped-runtime recovery copy at
05:50:26Z (388,657,152 bytes, unchanged hash prefix `63c773d6be11`); desktop was
offline/pending. No recovery copy was activated.

After GitHub push, native WSL fast-forwarded via a local git bundle to
`bd85a88023dbcc85cdb6a7eeeddbac8214c69fa2`. All **188** normalized tracked
source/test/script/web/config hashes matched the reviewed Windows checkout.
The deployment diff contains exactly the previously reviewed governor, runner
read-classification, selector clock repair and tests; no additional source,
configuration or launcher changes. The full native suite and final fresh
start gate remain required before launching; live results follow below.

The native WSL suite passed **1,580 tests in 151.350 seconds**. The marker was
then installed as `bd85a88023dbcc85cdb6a7eeeddbac8214c69fa2`; the independent
**05:54:02.873020Z** final gate again proved zero venue orders/positions and
zero unresolved/unknown/ledger exposure. At approximately **05:54:08Z**, the
normal launcher started supervisor **2461591** and runner **2461614** with
`auto 10 32 rest`. Saved REST selection, max30 flat entries and completion-based
5/1 polling were retained. The dashboard was already healthy and remained
separate/read-only. Acceptance reads begin at log byte offset `71121015`;
no authenticated diagnostic was stacked onto the initial selection scan.

### Live acceptance failed: completion-time lifecycle expiry

At **06:01:02.300417Z**, the first scan finished in **411.6 seconds**:
3 event pages, 22,767 event markets, 373 executable, 361 rewarded, 100 books
ranked and **zero viable**. The process entered normal market-wait; no order
action or startup exception was recorded. Shared governor reads through
06:01Z showed no new book429 or active book cooldown; the last book429 timestamp
remained the earlier 04:34:55Z incident. This is not proof of sustainable
full-trading capacity.

Offline reproduction at 06:02Z identified a local release defect. The production
runner builds `EventAwareGateway`, whose market records require positive event
status younger than 30 seconds. `_refresh_opportunity_times` now calls
`_applicable_period` at scan completion using those original records. After a
411.6-second scan, every original event receipt has expired. An otherwise valid
two-hours-pregame fixture gives viable=1 without the production guard but
viable=0 with that guard; the scheduled kickoff is still in the future in both.
The old test gateway did not enable the production guard. The earlier clean
read-only paced scan likewise did not establish this integrated lifecycle path.
Passing 1,580 tests therefore missed this integration case; this release is
**not live accepted** and the zero result must not be attributed to bad market
economics or an API ban.

Do not remove the actual-live guard or renew its timestamp locally. The next
repair must refresh genuine lifecycle evidence at the appropriate scan boundary
or separate non-executable ranking from fresh live authorization, cover the
production EventAwareGateway path with a greater-than-30-second scan regression,
and revisit full-scan latency/position interruption before another deployment.
No such additional source fix or restart was performed during this diagnosis.

At **06:03:12.981224Z**, a fresh authenticated read-only check after the scan
proved zero venue orders/positions and zero unresolved/unknown/ledger exposure.
Supervisor2461591/runner2461614 remain alive but idle in market-wait; the next
normal startup retry waits 900 seconds. Full scans are not startup-only: normal
safe rotation/top-up is scheduled on the 900-second cadence, and urgent
lifecycle/post-flat replacement can scan sooner when bounded recent-cache
revalidation is unavailable. Existing per-owner book refresh is separate.

## Prepared repair after failed acceptance

The operator requested an immediate stop and authorized completing the repair
and restarting after all gates pass. The supervisor was already stopped; the
remaining runner PID was force-ended rather than represented as a graceful
shutdown. A fresh authenticated 06:08:35Z read then proved zero venue orders,
zero positions and zero unresolved/unknown/working ledger state. Process status
also proves both runner and supervisor stopped. This post-stop proof, not the
force end itself, establishes the safe implementation boundary.

The repair separates two different kinds of book work. A bounded full selection
is background ranking and returns to the existing 0.5-second lane, so 100 books
have a nominal admission floor near 50 seconds rather than 400. Startup-bound
and routine flat-owner books are entry work and share a one-second allowance.
Held positions, recovery, ambiguity, cancels and exits keep their safety path.
The host-wide 4/s aggregate ceiling and endpoint-specific 429 circuit remain.

Selection completion still rechecks actual kickoff, reward period/end, active
state and durable positive started evidence. It no longer treats age alone on a
previously positive pregame receipt as proof of bad economics. That result is
ranking evidence only: after final startup books, the runner fetches one genuine
exact-parent lifecycle batch and updates every bound owner before streams and
entry planning. Each service/decision/action path repeats the same guard. A
failed, missing, stale or positively live lifecycle response therefore still
blocks/cancels entry and never blocks reduction.

Regression coverage uses the real `EventAwareGateway` and advances a full scan
411.6 seconds. The candidate remains rankable while its old receipt cannot
authorize entry; a genuine later lifecycle refresh restores authority. Startup
coverage advances the clock beyond 30 seconds during its final book pass and
requires a lifecycle refresh before private-stream startup. POSIX shared-limiter
coverage proves background books neither read nor write the entry allowance,
the production factory uses one second, safety bypass remains, and flat-owner
fairness remains bounded. The affected group passed 513 tests. Nine deliberate
reversals produced expected failures, including restoring four seconds,
reapplying expired-event rejection and removing the final startup refresh.
The complete production-Python suite then passed **1,584 tests in 212.684
seconds** against the reviewed Windows-mount source; only the suite's known
nonfatal temporary-directory cleanup warnings appeared.

The dedicated edge audit found no business-policy expansion: $90 HARD, quote
size 10, configured width 32, explicit REST account source, max 30 flat pending
entries and the exclusion of positioned-market reducers/exits are unchanged.
Old shared timestamps more than one second ahead are bounded as invalid rather
than imposing a leftover four-second delay. Position/stop checkpoints still
interrupt background scans; actual public-book 429 applies to every traffic
class; local admission cannot renew a book or event receipt; and source choice
remains manual. Push/deployment hashes, the exact deployed native suite, a fresh
start gate and natural live scan/order/429 observation remain required.

## Deployed repair and blocked live acceptance

Commit `7dec98869000a9bafd6a487fada416f235e1a031` was pushed to GitHub and
fast-forwarded into the clean tracked WSL checkout using the reviewed local
bundle path. All 188 normalized source/test/script/web/config hashes matched.
The exact deployed checkout passed all **1,584 tests in 151.457 seconds**; the
deployment marker then matched HEAD. The final 06:35:10.941045Z authenticated
start gate proved zero venue orders, positions, unresolved intents, unknown
orders, working ledger orders and ledger positions.

The normal launcher started supervisor **2542129** and runner **2542150** with
`auto 10 32 rest`, retaining $90 HARD and max 30 flat pending entries. The
runner did not reach a selection result during the first eight minutes. Its first
public-book attempt received HTTP429, and every later single recovery probe,
spaced roughly 28–49 seconds apart, received the same endpoint response.
Adaptive book delay rose from 10 to 16 and then 30 seconds. The runner and
supervisor stayed alive, no mutation/order was sent, and the endpoint circuit
continued self-recovery rather than restarting or busy-looping. Since the
current attempts are isolated single probes, this observation is not a startup
request burst; the exact external quota/block duration remains unknown.

At 06:43:25.348969Z, a fresh complete authenticated account and read-only ledger
check still proved zero orders, positions and ambiguity. The local lifecycle
misclassification is reviewed, tested and loaded, but the intended shorter
nonzero-viable scan and order buildup cannot be naturally accepted until the
public book endpoint permits a probe. Leave the bot running so its bounded
backoff can recover automatically; do not stack diagnostic book requests or
claim it is trading while venue orders remain zero.

## Stopped-bot endpoint recovery check

After the later graceful stop and flat/clear proof, one direct read-only
`markets.book()` request at **13:15:39.575073Z** returned HTTP200 with JSON
through Cloudflare (`CF-Ray a3a76c03e9b0d826-EWR`). No trading process was
present and no mutation was sent. This proves that the earlier Cloudflare 1015
block had cleared for that single request; it does not establish full-scan or
normal-running capacity. The bot remains stopped and must not be restarted
without separate authorization.

## Stopped startup-pattern reproduction

At **13:19:52Z**, the operator authorized one bounded read-only startup-pattern
test while the bot remained absent. The diagnostic used the production
background lane, allowed no mutation and stopped at the first failure. It made
14 GETs: two event pages, six incentive reads and six public books. The first
five books returned HTTP200; the sixth returned **HTTP429** with
`Retry-After: 10` and `CF-Ray a3a7728cf871ff90-EWR` at **13:20:07Z**. The
test immediately ended with zero mutation requests.

This disproves using the earlier single-book 200 as a restart gate. It also
gives support a current reproducible distinction: the failure is an HTTP429 on
the public book GET, not an order rejection labeled `Global Rate Limit
Exceeded`. The external threshold remains unknown, and the bot stays stopped.

## Fresh private WebSocket comparison

Two separate 45-second read-only tests followed while the bot remained absent.
The first opened the private socket directly with the official SDK's signing
helper and sent JSON text subscriptions for global account balance, orders and
positions. The handshake succeeded and both initial and final protocol Pings
received Pongs, but the socket delivered zero application frames, decode
failures or errors from 13:23:32Z through 13:24:17Z.

The second used the latest published official Python SDK end to end
(`polymarket-us` 0.1.2) and its `ws.private()`, `subscribe_account_balance`,
`subscribe_orders` and `subscribe_positions` methods. It connected and passed
both Pongs from 13:25:42Z through 13:26:27Z, but emitted only the local `open`
event: zero message, balance, order, position, heartbeat, error or close event.
The official status API reported All Systems Operational with zero incidents
and zero maintenance at 13:25:08Z. The current changelog still lists no private
subscription-format change. No credential, ledger, order or runtime state was
changed.
