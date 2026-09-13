# Public-book renewal implementation — September 13, 2026

## Authority and release boundary

The operator explicitly requested implementation after the stopped mechanism
probe and the bigger-picture design review. This is source preparation only:
no production checkout deployment, marker change, order mutation or restart.
The last authenticated flat/clear proof remains17:25:11Z; implementation probes
check process absence but do not make account reads. Current exposure is not
re-proven by those process checks. Remote workstation parity is unverified.

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

Deployment and restart need fresh authority, exact-source hashes/native tests
and current authenticated account/ownership gates. Subsequently observe normal
startup, scans, useful order progress, public renewal/API load, maintenance and
natural position exits. Do not induce fills or faults. The nine-market20-second
probe plus synthetic32-owner tests cannot establish full-day/32-market venue
capacity, eliminate external429s, prove private WebSocket recovery or promise
WebSocket-era end-to-end latency.
