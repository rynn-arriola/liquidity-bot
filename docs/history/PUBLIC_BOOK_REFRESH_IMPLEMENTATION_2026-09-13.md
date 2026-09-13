# Public-book renewal implementation — September 13, 2026

## Authority and release boundary

The operator explicitly requested implementation after the stopped mechanism
probe and the bigger-picture design review. That stage was source preparation only:
no production checkout deployment, marker change, order mutation or restart.
The last authenticated flat/clear proof remains17:25:11Z; implementation probes
check process absence but do not make account reads. Current exposure is not
re-proven by those process checks. Remote workstation parity is unverified.
The subsequent authorized deployment/restart is recorded at the end of this file.

Pre-work Windows/GitHub matched `f61e20242`. WSL remained at `7c7e6f21f`, with
loaded marker `927676d14`; the parity checker refused its newer instruction-file
update as requiring review. No runtime files were copied to bypass that refusal.
The verified private ledger recovery copy was captured17:48:06Z.

## Chosen design and alternatives

The [design inventory](PUBLIC_BOOK_REFRESH_DESIGN_2026-09-13.md) covered selection,
startup, normal decisions, rotation, recovery, settlement wait and shutdown.
The earlier collection-only release reduced scans but left routine quiet-book
REST traffic, which produced three429s in live validation.

The chosen design renews the existing public full-book subscription on demand.
It avoids a second permanent socket/cache owner and avoids using progressively
slower whole-slate REST polling as the normal book source. The tradeoff is a
bounded unsubscribe/resubscribe gap and reliance on venue snapshot delivery;
missing depth never becomes permission to use fabricated freshness. Account
REST and order execution remain separate and unchanged. No automatic account
source switching or inactive private-stream collector was introduced.

## Implementation

- `RefreshingMarketStream` supplies both the normal event consumer and a
  non-consuming fresh-book reader. At most one pure transport task renews one
  full-book subscription for the complete active scope, capped at100 markets.
  The three-second minimum renewal interval is independent of market count.
- Exact current request ID and market scope fence old frames. Full depth is
  parsed with the existing adapter, retaining actual UTC receipt times. Pong,
  timer expiry, direct reads and dequeuing cannot renew timestamps.
- A bounded latest-per-market queue/cache prevents backlog. Invalid/valid
  alternation cannot accumulate duplicate queue tokens. Missing-owner timeout
  is shared across the batch with a three-second negative cooldown, so each
  missing owner cannot trigger another full wait/subscription immediately.
- Connect is bounded to5s; control send and fresh-book waits to3s; close to5s.
  Teardown cancels/joins the renewal task. A canceled caller cannot steal another
  caller's snapshot. Checkpoints run on callers, never transport workers, and
  can service safety work while awaiting a batch without a recursive lock.
  Caller safety checkpoints retain their own existing deadlines; book-wait
  bounds do not interrupt that safety work or promise a three-second complete
  trading decision when account/lifecycle proof itself is slow.
- Gateway reads resolve the current runner stream every time. Active-owner
  reads use it; failure does not silently cause runtime REST fanout. Rotation
  during a read invalidates its result. Selection and flat startup use the
  same renewable class in their disposable scopes.
- Explicit recovery-owner startup, unbound candidates whose collector cannot
  provide proof, and reads before startup/after teardown retain the existing
  bounded/paced REST path. Positive empty-book settlement proof is preserved.
  This is **not** a claim that every book path is permanently REST-free.
- Empty or malformed owner data revokes that owner's usable proof. The negative
  signal survives resubscription until valid depth arrives; the runner uses its
  existing stale-book recovery and entry-admission refusal instead of an older
  cached price. Other valid owners remain usable. One-sided depth still supports
  existing reducing logic. Disconnect/error revokes the whole socket's cache.
- Consumer-held old-generation books cannot overwrite current snapshots.
  Weak identity-based provenance retains valid same-generation deliveries
  without keeping discarded books alive; a busy feed does not reject every
  task-held update merely because another frame arrived before consumption.
  The runner separately rejects receipt-time regression against accepted state.
  Reconnect's first book is routed to its actual market, not the primary slot,
  and keeps its receipt rather than being timestamped when dequeued.
- Shutdown freshness checkpoints do not inherit discovery's operator-stop
  refusal. Cancels/reducers/exits keep their existing authority. Real typed
  transport causes survive wrapping for the existing outage classifier.

The historical `quiet_book_rest_refresh` recovery-counter label is retained
for compatibility; its presence alone no longer proves a REST request occurred.
Use actual API telemetry to attribute REST calls.

## Preserved business behavior

No risk thresholds, configuration, database schema or strategy economics
changed. The reconciler has an additive public-book-validity input at its
existing book guard, preserving cancellation and ownership-check precedence.
In particular, a HARD/maintenance pause can still cancel flat orders even when
depth is explicitly invalid. Preserve $90 HARD, size10,
capacity32, manual REST account mode and max30 flat pending entry orders,
excluding positioned-market orders, reducers and exits. Lower time/loss caps,
candidate scan limit100, reward ranking, eligibility, price/size calculations,
900-second inventory clock, kickoff deadline, exact ownership and mutation
proof remain enforced. Improved data acquisition does not grant trading authority.

## Verification

Focused regressions cover32 concurrent owners with a single renewal, independent
event/direct readers, old/missing request IDs, partial batches, missing/empty/
malformed/one-sided/future books, scope violations, disconnect, renewal failure,
nested checkpoints, canceled waiters, teardown, reconnect, scope changes,
selection stop propagation, stopped-runner book refresh, exact recovery routing
and negative-proof entry refusal. Six deliberate source reversals failed as
expected: disabled renewal, removed request-ID fence, disabled runtime gateway
route, restored wrong-owner recovery, removed negative-proof guard and
over-strict latest-only delivery under a busy feed. All
repairs were restored. An intermediate suite caught a mutation-test restoration
mismatch in the negative-book guard; exact-context restoration and diff review
corrected it before the final complete rerun. Final complete-suite result is
recorded below at release.

The exact final source passed **all1,626 tests in224.592s**, including the
busy-feed provenance regression and negative-book cancellation-precedence
checks. The documentation audit and staged diff whitespace checks passed.
No native deployment test is claimed because this source was not deployed.

### Stopped production-class probe

The exact production class was exercised with the installed SDK and original
credentials, with an HTTP hook refusing every request and no ledger/mutation
code. The initial17:56Z strict nonempty assertion found six usable books and
three response-backed empty books, on both initial subscription and reconnect.
This was not silence or a transport error. The diagnostic was corrected to
distinguish positive empty evidence from missing data; production parsing was
not weakened.

The17:57:05–17:57:25Z bounded repeat ran six rounds3.2s apart plus reconnect.
Every round returned six usable books and three positively empty books; there
were zero missing/transport failures, zero HTTP attempts and zero mutations.
There were12 control frames total and the minimum subscription gap was3.258s.
Batch elapsed times were41.37,62.15,61.24,61.76,61.35 and62.23ms. Total20.02s;
process-absence checks passed throughout. This verifies the production refresh
mechanism and empty-book isolation, not live trading or exit performance.

After the last review changes and the1,626-test pass, the exact final class
was rechecked18:18:56–18:19:16Z: six rounds plus reconnect again returned six
usable and three positively empty books each, with zero failures, HTTP attempts
or mutations. It used12 controls, minimum subscription gap3.254s, total20.06s;
batch times61.66,63.47,84.04,41.63,62.55 and41.11ms. Stopped checks passed.

### Dedicated final edge-case audit

Reviewed success/failure, empty and maximum scopes, current/old generations,
queue/memory/provenance bounds, busy same-generation delivery, receipt clocks,
missing/partial data, recursive safety
checkpoints, partial send/disconnect, stop/cancel/close, replacement streams,
position/startup/settlement/shutdown consumers, API/control load, account source
independence, unchanged strategy and rollback. No worker writes the ledger or
executes orders. Genuine empty proof remains distinct from no response.

Rollback is a reviewed source deployment back to `927676d14` from a stopped,
known-state gate, not an automatic source flip. That earlier release still has
the documented routine REST-book429 limitation. No code rollback may reset risk
day/loss state or active ownership.

## Remaining acceptance

The operator subsequently authorized restart; deployment and startup evidence
is recorded below. Continue observing scans, useful order progress, public
renewal/API load, maintenance and natural position exits. Do not induce fills
or faults. The nine-market20-second
probe plus synthetic32-owner tests cannot establish full-day/32-market venue
capacity, eliminate external429s, prove private WebSocket recovery or promise
WebSocket-era end-to-end latency.

## Authorized deployment and restart, September 13

The operator requested "Ok restart the bot". The graceful stop script confirmed
the old bot stopped. Authenticated gate18:29:43Z proved zero venue orders and
positions, zero ledger working orders/positions, unresolved intents and unknown
orders. Pre-work parity recorded the expected reviewed-source drift; a private
ledger recovery copy was verified. The remote workstation remains unverified.

Windows/GitHub source `b2b0d017ea2c3617a48c4d9d168b2bcf7d8334af` was fast-forwarded
into the clean WSL checkout from `7c7e6f21f`. All192 normalized tracked
source/test/script/web/config hashes matched, before and after the native
production suite: all1,626 tests passed in160.177s. The deployment marker was
then updated to that reviewed source. Fresh authenticated gate18:33:51.708994Z
again proved all seven venue/ownership/ledger counts zero.

The standard launcher started supervisor4022391 / runner4022412 at18:33:58Z
with explicit `auto 10 32 rest`. No strategy, config, risk or ledger repair was
made in this restart task. HARD remains$90, flat pending-entry ceiling30
excluding positioned-market orders, reducers and exits; existing lower caps
and manual REST account choice remain. Public-book renewal is not private
account WebSocket recovery.

Initial scan18:34:12Z returned26/26 books through the public stream with
zero REST fallbacks in12.2s; startup16/16 also used the stream with zero REST.
First entry action18:34:50Z proves useful order progress, not just a live PID.

First API window18:36:08Z:322 attempts over130.0s (2.476/s),292 reads,
30 mutations, zero rate limits, zero REST-book requests and zero cooldown.
Four failures were `order_retrieve@404`, not book429s; subsequent ownership
repair passes reported remaining0. Admission maximum4.299s; response
maximum11.955s. Initial scheduler decision p95 was45.396s, action p95 1.786s;
this is not evidence of full end-to-end private-WebSocket performance parity.

Second API window18:37:40Z:243 attempts/92.1s (2.638/s),12 mutations,
four `order_retrieve@404`, zero rate limits/REST-book requests/cooldown.
Maximum admission5.833s and response8.891s; scheduler p95 decision43.200s,
action1.202s. Several18:37:43Z incentive refreshes temporarily reported
unavailable pregame proof; their safety gates remain intact. Do not classify
that as a venue outage or change live-event rules to fill capacity.

A natural+10 Vysmar/Kocmar position was observed18:36:42.969Z; the bot
cancelled/repriced its reducing order, with first reducer acknowledged4.153s
after position observation and0.140s after cancellation confirmation. REST
reported flat18:36:59.560Z. These times start at observation, not venue fill.
Authenticated18:37:40.356217Z state:26 flat-market orders across17 markets,
zero positions and zero ledger unresolved/unknown state. The original26
scan-book count is not an imposed26-order cap. Candidate period/spread/lifecycle
filters precede detailed scoring, and per-side entry-quality/economic checks
can leave pending entries below30. No filter or cap was changed.

The ledger confirms both legs match local PLACE intents: BUY10 at0.48,
18:36:37.224777Z, then SELL10 at0.50,18:36:58.500420Z. Both MAKER and
not reversed;21.276s venue-fill interval, +$0.20 gross before fees/rebates.
This was normal passive reduction, not a HARD/time-expiry exit. The hold
limit is a deadline, not a minimum holding period. Another SELL10 at0.49
filled18:38:41.143976Z; REST observed -10 at18:38:45.134Z and acknowledged
the new BUY reducer3.403s later. The later position is a separate episode.

Third API window18:38:42Z:132 attempts/61.9s (2.132/s), seven mutations,
one `order_retrieve@404`, zero rate limits/REST-book reads/cooldown.
Authenticated18:39:34.993259Z:28 venue orders across19 markets, comprising
27 flat-market entries and one position-market order, one open position,
zero unresolved intents/unknown orders; ledger agrees28 orders/one position.

Fourth/fifth API windows18:39:45Z/18:40:46Z:194/175 attempts in62.7/61.1s,
12/four mutations, two individual-order404s each; zero REST-book requests,
rate limits or cooldown. Across all five windows:1,066 attempts,13 failures,
all `order_retrieve@404`; no book429 recurrence during the seven-minute check.

The second episode also closed through its bot-owned passive BUY10 at0.49,
filled18:40:56.072256Z,134.928s after its SELL10 at0.49 (zero gross spread
before fees/rebates). Authenticated18:41:00.190427Z reported25 flat-market
orders across17 markets, zero venue positions and zero unresolved/unknown
orders. The non-atomic ledger read still showed26 orders/one position at that
instant; the runner logged `flat_observed` at18:41:00.781151Z,0.591s later,
then resumed entries. This was a naturally resolving observation race, not a
ledger repair or evidence that a combined snapshot is atomic.

The same runner remained up beyond seven minutes. Initial order/scan/passive
exit/API acceptance passed; continued hard-exit, reconnect/maintenance,
full-capacity and long-duration performance acceptance remains observational.
No runtime source was edited during release, and no additional restart,
manual order, induced fill/failure, private-source flip or risk reset occurred.
