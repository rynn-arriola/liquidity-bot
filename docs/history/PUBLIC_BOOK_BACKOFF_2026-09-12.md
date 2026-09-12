# Public-book 429 retry amplification — September 12, 2026

## Authority and verified incident

The operator requested a one-time force-stop, took responsibility for manual
order cancellation/position closure, and authorized investigation and repair.
Leave the bot stopped: this request does not authorize deployment or restart.
The exact supervisor 1336728 and runner 1336746 were stopped through the
supported force-stop script; a later `ps` showed neither process. SIGKILL was
needed, so no graceful final-flat runner record is claimed.

Authenticated report-lane reads at **21:41:12–21:41:16Z** proved zero venue
orders and zero nonzero positions after the operator's cleanup. The read-only
ledger still had one unresolved intent and zero unknown orders; nothing was
edited or inferred terminal from absence. Before any later restart, normal
exact terminal reconciliation and fresh whole-account/ownership proof remain
mandatory. No venue mutation or ledger repair was performed in this task.

The previously loaded source was `a8888b5e5`; tracked Windows/GitHub/WSL were
`6b17c5416` before this repair. The PC peer was unreachable, explicitly pending.
The private online ledger recovery copy was verified by parity; it was not
activated or committed.

The first categorized book-failure window ended at **20:50:47Z**: 464 total
requests, 162 book reads and **99 `market_book@429`** responses in 199 seconds.
The next 216.3-second window had 192 book reads and 124 book 429s. The window
ending 21:18:51Z had 157 book reads, 98 book 429s and two exact-order 404s.
Failures continued through the last 21:33:08Z window. These are actual HTTP
failures, not a queue-depth interpretation. Gateway error bodies identified
Cloudflare 1015. A later balance 429 also occurred; not every read was healthy.

At 21:19:33–21:19:38Z the 24 visible orders were **22 flat-market entries plus
two position-market orders**, not a 24-order configured ceiling. Two exact
positions and one unresolved intent remained then. The REST flat-entry cap
was still 30; no September 12 loss-tier row had been recorded.

## Venue checks and limits of attribution

The canonical Retail reference, current
[changelog](https://docs.polymarket.us/changelog),
[Retail rate limits](https://docs.polymarket.us/api-reference/rate-limits)
and live status were rechecked. The latest changelog was v0.0.88 announcing
September 11 maintenance; the public Statuspage summary API returned
`All Systems Operational` during this investigation. Public status is not
account/endpoint proof. The documented 20/s ceilings do not identify which
rolling/endpoint/edge rule caused this first rejection. No IP/key/host rotation,
quota increase or undocumented endpoint workaround is part of this fix.

## Proven local defect and scoped repair

Runtime book reads inherited the safety lane's at-most-one-second cooldown
and inline 429 retries. That short lane limit exists to keep cancels/account
proof responsive, not to make repeated rejected public-book reads useful.
Quiet refresh plus immediate stale-book recovery and many slugs multiplied
requests against the same throttled endpoint. A per-owner error counter also
mistook deliberately deferred reads for additional broken-market evidence.

`AsyncIntervalLimiter` now has a separate **read-only book endpoint circuit**:

- Only confirmed HTTP 429 on `market_book` opens it. Other failures keep their
  existing classification. Book failures no longer cool account/mutation lanes.
- Adaptive cooldown uses the existing 2/4/8/16/30-second sequence, with bounded
  `Retry-After` up to 60 seconds. Book calls never retry inline.
- Every slug and traffic class joins the same host-file circuit, under the
  existing short POSIX lock off the event loop. The aggregate 4/s budget stays.
- At expiry only one normal caller acquires a recovery probe. Its 20-second
  lease outlasts the owned 15-second HTTP timeout and expires after a cancelled
  task/crashed process; there is no permanent background probe.
- Only the matching probe's HTTP success reopens the endpoint. An older
  in-flight success cannot clear a newer 429. A concurrent older failure
  invalidates the probe's recovery authority while retaining its bounded lease.
- Deferred requests do not count as network attempts, mutate orders, fabricate
  book freshness or retire a market. Actual book 429 logs are concise and omit
  gateway HTML/IP details. Genuine empty/schema failures retain escalation.

Fresh market WebSocket books still use the unchanged planning path; REST
account-source selection is independent and remains manual. A missing/stale
book still cannot authorize entry or an invented reducer/IOC price. Exits may
still wait for real price evidence when both valid stream and REST books are
unavailable. This is retry-load containment, not a promise of outage-proof
execution, faster fills or WebSocket-equivalent REST latency.

## Verification and dedicated edge-case audit

Thirteen focused regressions passed in WSL: no inline retry; all four lanes and
different slugs blocked before network IO; account/cancel/reducer admission
unaffected; 32 simultaneous callers yield one probe; current-token success;
late success/new failure ordering; failed/cancelled probes; bounded adaptive
and Retry-After delays; independent endpoint results; two limiter instances
sharing restart state; invalid timestamps; gateway result propagation; held
position/order/failure evidence preserved; no false retirement/quarantine;
fresh streamed books remain usable during REST backoff.

Deliberately disabling book admission made two dedicated tests fail (no local
deferral and no unique probe). The fix was restored before the complete suite.
The 75 existing adapter tests passed before the final additions.
The final complete WSL review run passed **all 1,557 tests in 209.027 seconds**.
The first full run completed 1,557 tests with only the documentation audit
failing: it scanned broken upstream documentation links inside the ignored
TypeScript diagnostic's installed dependencies. That generated diagnostic
folder was preserved outside the repository in the local diagnostics cache;
no bot dependency or trading file changed. The documentation audit then passed,
and the complete suite was rerun rather than waiving the failure. The final
documentation audit and whitespace/diff checks also passed. Review source was
run from the Windows checkout using the native WSL production Python; no
production source installation or live acceptance is implied by these tests.

Dedicated review covered normal and failed reads, empty/full 32-owner scope,
deadline/lease boundaries, task cancellation and late responses, shared-file
restart and corrupt-clock recovery, configured capacity, ownership and stale
evidence. Selection already stops a scan at local admission deferral; shutdown
catches owner-scoped adapter failure and continues other owners, and a failed
book read occurs before spending an IOC attempt. Freshness, exact terminal
proof, the independent shutdown deadline and IOC limits are not loosened.
File work remains off the event loop with no lock held during network waits.
Successful healthy reads avoid rewriting unchanged shared state.

Rollback is source-only through reviewed deployment: no database migration or
risk-policy file changed. Older source ignores the additive book-circuit keys;
rolling back loses this retry containment and needs explicit review. Unreviewed
source was never copied over the production checkout. No restart/live
acceptance is claimed.

Preserved: size 10 / 32, $90 HARD and durable ET state, 30 flat pending REST
entries excluding positioned-market orders/reducers/exits, manual account
source selection, ranking/entry quality, lifecycle, price/quantity, 900-second
inventory deadline, three-attempt/30-second IOC authority and shared 4/s.
