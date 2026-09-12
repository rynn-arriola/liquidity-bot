# September 12: normal public-book request coalescing

## Scope and stopped state

The operator approved fixing normal request demand before restarting. This
continues the [public-book 429 containment](PUBLIC_BOOK_BACKOFF_2026-09-12.md),
which is pushed but not deployed. Deployment/restart confirmation was requested
separately; no restart or venue mutation has been performed in this repair.
At 22:27:06-22:27:10Z, authenticated complete reads returned zero orders and
zero positions. The old supervisor/runner were absent. The read-only ledger
still held one unresolved intent and zero unknown orders. Exact positive
terminal reconciliation and fresh whole-account proof remain startup gates.

## Proven local mechanism and repair

The run loop serviced quiet books across the scheduled slate, drained streams,
performed additional account/selection work and only then made owner decisions.
At 32 slots, serial book requests can themselves exceed the five-second book
age limit. Earlier successfully refreshed books therefore need another read
in the existing stale-book recovery. A book-only 429 circuit prevents retry
amplification after throttling; it does not remove these healthy redundant reads.

Normal bulk service now retains lifecycle/contract/economic work but defers
quiet-book REST reads. The same guarded decision sweep services the exact
owner after account/equity proof and before its plan. Its existing service
timers, failure accounting, retirement isolation and fresh economics are reused.
The REST urgent-position phase uses the same ordering. No parallel executor,
shared snapshot authority, new source switch or polling-rate increase is added.

Important exceptions and boundaries:

- An economic replacement dwell is already a book consumer before the decision
  sweep, so bulk service retains its ordinary refresh, including before the
  deadline that may cross during other service. Old economic refusal
  cannot be used to release an owner that the new book makes viable.
- The fast entry-pause cancellation pass acquires no additional price read.
- Unknown-intent and backed-off owners do not fetch a decision book. Held
  positions still use their existing priority/proof/exit path, not the entry cap.
- Stop during service returns before planning. A position-only pass that
  discovers flatness during book service cannot become a new entry pass.
- Fresh streamed books retain their original receipt, and can suppress REST.
  None/future/stale receipts remain untrusted; quiet checks remain three seconds
  and planning age remains at most five seconds. Deferred reads renew nothing.
- Inactive/closed waiting owners keep their original early return. Startup,
  shutdown, selection validation and recovery keep their explicit book reads.

## Test evidence

The initial affected commissioning/REST group passed 477 tests. The new
deterministic 32-owner regression simulates three seconds of account proof per
owner and half a second per book response. It proves zero ordinary bulk book
reads, exactly 32 decision reads, fresh receipts and computed economics at each
decision, in book/decision order. It does not measure live latency or predict
a percentage reduction in production traffic.

Further focused cases cover fresh stream delivery during account proof,
unknown/backoff/stop, stop during service, economic replacement dwell, pause
cancellation, missing/future and 2.999/3/5.001-second receipt boundaries,
scoped service failure and terminal ownership invariant propagation. The
11-test REST exit scheduling group passed; its flat-during-service test now
also covers flatness during the moved book read.

Two deliberate reversals proved the main regression detects removed repairs:
disabling bulk deferral failed with 32 reads where zero were expected; removing
the decision refresh failed the fresh-book assertion. Both were restored.
The first complete run caught two variants of the same position-exit regression:
the initial implementation repeated contract service at the decision boundary,
overwriting the fractional-lot fixture metadata. The correction moves only the
book/economic helper, not contract reads. Both unchanged position cases now pass
in the 145-test account-source/position/exit group. Eight new focused regressions
pass, including fast pause and receipt boundaries. Final complete-suite and
documentation results are recorded below when finished.

The next 1,565-test run had only an outdated mock-call assertion: the HARD
pause test expected the old call without the new explicit `service_books=False`.
The assertion now verifies that cancellation bypasses extra book service; no
HARD implementation or limit was changed. All 12 safety tests pass. A final
complete run follows that fixture correction.

Final result: all **1,565 tests passed in 217.187 seconds** using the production
WSL virtual environment against the reviewed Windows source tree. Documentation
audit passed (127 Markdown files, 961 mandatory lines, zero issues), and final
diff/edge-case review found no remaining in-scope regression. No test failure
was waived. Latest 22:42:51-22:42:55Z authenticated reads again proved zero
orders/positions; the old processes remain absent, one ledger intent remains
unresolved and zero orders are unknown. No ledger or venue mutation occurred.
This is prepared-code acceptance, not deployed-runtime or natural-load proof.

## Dedicated edge-case and release audit

- Success/load: one fresh read per ordinary owner in the 32-slot slow-proof
  simulation; no invented receipt or shared cross-market cache. Existing
  request/category telemetry can measure the natural change after release.
- Empty/full/boundaries: missing book obtains an actual read; exact quiet and
  stale/future boundaries are tested. Unknown owners and inactive waits remain
  fail-closed. The existing full suite retains empty/thin-book and full-cap tests.
- Timing/ownership: proofs precede the moved read; original downstream book,
  account, position and network-admission validation remain intact. No absent
  order becomes terminal and no unknown create is resent. Stop/flat checks are
  repeated after the added await. Private projection stays serial.
- Failures/recovery: existing service catches and retirement bounds are reused;
  unowned/invariant failures stay terminal. The separately reviewed shared
  book429 circuit still blocks fan-out without delaying account/mutation lanes.
- Risk/cap: size 10 / 32, $90 HARD and durable ET tiers are unchanged; manual
  REST's 30 pending-entry ceiling excludes position orders/reducers/exits.
  Price rules, reward ranking, pregame guard, inventory clocks and IOC bounds
  are unchanged. No added REST read precedes fast pause cancellations.
- Shutdown/restart/rollback: explicit shutdown fresh-book work is unchanged.
  The patch has no schema/config migration and does not alter the production
  ledger. Rolling back source restores extra reads, not an older ledger or
  risk baseline. Restart needs current terminal/flat/clear evidence and review
  of this patch together with the already pushed book429 circuit.
- Limits: no live stress test, key/IP change or limit increase. This fixes local
  waste, not Polymarket's undocumented matched Cloudflare rule. Sustained live
  request/exit/reward performance and the external private stream remain unproven.
