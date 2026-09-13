# Public full-book selection preparation — September 13, 2026

## Authority and state

The operator accepted the public WebSocket-first book recommendation after
asking about the repeated REST book limit and supported order count. Prepare
and verify while stopped; no new deployment or restart is authorized by this
acceptance. Deployed source remains `7dec98869`. Last authenticated flat/clear
proof was September13 12:57:09Z; later stopped probes did not renew that proof.

Manual account source stays REST. Public market data is a separate service:
using its full-book stream does not promote the broken private account stream.
Preserve $90 HARD, size10/32, max30 flat pending entries excluding positioned
markets/reducers/exits, all lower time/loss caps, prices, ownership, exit
deadlines and lifecycle checks. No database/schema/credential/launcher change.

## Evidence and corrected interpretation

Current source at16:33Z and isolated stable `v1.2.0` (`a3defc014`) at16:45Z
each made twelve GETs: two event pages, four incentive pages, five successful
books, then sixth-book HTTP429 with `Retry-After: 10`. Latest private probes
still received no application frames despite successful Ping/Pong.

Stable reproduction proves today's new architecture is not necessary to
reproduce the public-book error. It does **not** exclude prior traffic or
IP-scoped server state, establish a permanent five-market limit, or identify
the venue's unpublished counting window. No quota-exhaustion test is needed.

The prepared source's stopped, GET-only normal-selection test ran
16:56:18Z–16:56:33Z:

- Six REST requests: two event pages and four incentive pages, all HTTP200.
- 47 candidate full books received through the public WebSocket; zero REST
  book requests, zero HTTP429, 15.54 seconds total including discovery.
- 47 evaluated opportunities, ten viable markets, one selection failure.
  The bounded probe recorded only the failure count, not its exact market
  cause; do not call this a zero-selection-error run.
- Zero mutation; stopped-process guards before requests and throughout book
  collection. This is selection acceptance only, not trading acceptance.

## Implementation

`SelectionBookSource` opens a disposable public `MarketReadOnlyStream` scope
for at most100 distinct candidate slugs. Existing full-depth parsing and
latest-per-market queueing remain authoritative. Selection and cached
revalidation retain their old ranking, incentives and event gates. Flat
startup uses the same collector; recovery startup bypasses it entirely to
retain direct safety reads and exact empty-book settlement evidence.

Only exact-scope full books with original receipt age in [0,3) seconds are
used. Connection setup is bounded at five seconds; the initial book wait is
one shared three-second deadline, not three seconds per market. Serial
read checkpoints remain active while waiting/consuming. A disconnect revokes
the cache; missing/stale/future data falls back through the normal gateway.
The runner preserves the original receipt rather than stamping old data new.
Scope exit cancels and joins workers, closes the socket with a five-second
bound and clears its cache. Unexpected programming failures propagate.

Optional background and entry REST books share one four-second allowance.
Non-book discovery retains background2/s and the aggregate host ceiling stays
4/s. Position/account safety is not paced by that optional allowance; actual
book-endpoint429 backoff still applies. First real429 or local admission
deferral ends selection/revalidation without retrying every remaining market.
The caller also propagates a cached-revalidation429 instead of launching a
larger discovery scan in response to the rate limit.
No inactive private source, auto-switch, mutation retry or new risk authority
is introduced. The normal runtime public stream remains unchanged.

Official Retail references consulted:
[market WebSocket](https://docs.polymarket.us/api-reference/websocket/markets),
[Python WebSocket](https://docs.polymarket.us/api-reference/sdks/python/websocket).
The100-market subscription bound is not an order capacity promise.

## Tests and dedicated edge-case audit

Focused regressions cover100 books with no REST, unchanged selector ranking
and gates, cached revalidation, original runner receipt, recovery bypass,
empty/101-market scopes, stale/future/missing data, disconnect revocation,
scope mismatch, latest-wins, concurrent-scope isolation, local backpressure,
429 propagation, checkpoint interruption, cancellation during connect/read,
programming errors and ordinary gateway fallback. Existing pacing tests cover
32 owners, shared allowance, safety bypass, cooldown and interruptibility.

Deliberately removing the gateway hook and four-second factory policy produced
three failures and one error in the13-test reversal run; repairs restored.
The receipt-only reversal failed on the exact two-second timestamp difference;
the new cached-revalidation429 test failed before the caller repair because
it fell through to a full scan. Both repairs restored, all59 focused tests
passed. The first complete suite passed1,601 tests in225.687s; the final suite
including the additional cached-revalidation regression passed all1,602 tests
in227.092s using the production Python3.12 virtual environment against the
prepared source. Documentation audit and final diff checks passed. These are
not exact-deployment tests: production files remain unchanged.

Workstation/GitHub/WSL began at `2ec08a706`, with deployed marker `7dec98869`.
Local parity preserved the owned preparation diff and verified an ignored
389,189,632-byte execution-ledger recovery copy captured16:59:10Z. The peer
was not verified in the bounded local check. New source on GitHub must remain
explicitly pending reviewed deployment, not be mistaken for loaded code.

Dedicated review: success/empty/full/boundary behavior preserves candidate
bounds; original timestamps and disconnect invalidation prevent freshness
renewal; task-local scopes cannot contaminate other consumers; readers own
only public books and cannot mutate/adopt ledger state. Recovery retains its
old safety route. Scan checkpoints continue position/stop preemption. Every
fallback keeps the host limiter, endpoint circuit and first-error boundary.
Public collection does not prove account, lifecycle or mutation readiness.
Shutdown joins owned workers; restart cannot reuse a previous scope/cache.
The diff contains no policy, secret, schema or execution-authority expansion.

## Remaining acceptance and rollback

Before any later deployment/restart, complete parity/review, exact deployed
hashes/tests and fresh authenticated account/ownership gates. Then observe
ordinary startup, sustained useful quoting, fallback traffic, real429s and
natural position exits without inducing a fill. Thirty pending entries is
the preserved ceiling, not a demonstrated sustainable result of this scan.

Quiet/failed public data can require slow REST scans (up to roughly400 seconds
for100 missing books before other latency). The fix does not bypass stale-book
refusals to hide that limitation. Venue account polling/exit latency remains
separate. Rollback requires a reviewed source revert while stopped and normal
deployment gates; no ledger repair or HARD baseline reset is involved.
