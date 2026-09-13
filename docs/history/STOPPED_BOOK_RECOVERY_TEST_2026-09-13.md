# Stopped public-book recovery test — September 13 UTC / September 12 ET

The operator authorized three different active-market book checks spaced thirty
seconds apart, followed by one bounded read-only startup selection scan only
if they passed. Stop on the first HTTP429; no trading restart or mutation.

## Scope and safeguards

Used the deployed Python SDK and existing shared host limiter, with diagnostic
report traffic capped at 0.25 requests/second. The production selector's
size-10 policy, six-hour horizon and maximum 100 book candidates were retained,
but this is deliberately slower than normal startup. This tests a single
selection scan, not full account/WebSocket preflight or trading capacity.
The standalone ignored helper imports no runner or execution ledger, permits
GET only, has one attempt per request, refuses if a runner/supervisor exists,
and is bounded by 130 requests / 600 seconds per invocation. Any gateway error
is promoted out of the selector's ordinary per-market continuation, and an
HTTP error also latches a request-hook refusal. No trading/configuration source,
credentials, network identity or rate limit was changed; the host limiter may
publish its ordinary response evidence, never a manual reset.

Two local helper setup mistakes were corrected: missing required diagnostic
configuration arguments failed before any venue call; a report-context override
was rejected before the scan's first request after the three probes. The helper
now retains its report default without overriding it. Only the not-yet-started
scan was invoked again; the three successful probes were not repeated. These
were diagnostic helper errors, not venue failures or production-code repairs.

## Spaced checks

Three successful event discovery pages identified 292 eligible active
candidates. The following decoded books all reported MARKET_STATE_OPEN:

| UTC time | Market | HTTP | Cache |
|---|---|---|---|
| 03:34:49 | `aec-setkameua-hrykos-marmak-2026-09-13` | 200 | HIT |
| 03:35:19 | `aec-itfme-huawei-junfu-2026-09-13` | 200 | HIT |
| 03:35:49 | `aec-wtt-miwhar-xinche-2026-09-13` | 200 | HIT |

This phase made six total GETs (three discovery pages and three books), zero
HTTP429s and zero mutations. A current metadata/book response is not a promise
that its contents cannot lag; no order was authorized from these samples.

## Single scan

The scan ran from 03:36:59Z through 03:44:26Z (446.89 seconds), with the same
production selection policy, still on the report lane. It completed 111 GETs:
three event pages, eight incentive reads and 100 book requests. Every response
was HTTP200; 100 books were evaluated, 75 markets were selector-viable and
there were zero selection failures, HTTP429s or mutations. Observed book cache
labels included both HIT and EXPIRED; all discovered event pages were MISS
and incentive responses were DYNAMIC. Captured Retry-After values were absent;
the diagnostic did not collect quota/remaining/reset headers.

Including the initial phase, the test completed **117 GETs, all HTTP200**:
103 book reads, six event pages and eight incentive reads. There were zero
automatic retries after HTTP failure (none occurred), no order requests, no
execution-ledger access and no bot restart. The helper's final process check
and an independent process listing confirmed no runner or supervisor.

## Interpretation

Even a completed successful scan proves only bounded low-rate access during
that interval. It cannot identify Polymarket's matched Cloudflare rule, prove a
permanent reset, certify private account WebSocket recovery, or establish
sustained full-speed quoting/exit performance. Keep the bot stopped unless the
operator separately authorizes a normally gated restart. The last authenticated
account proof remains September 13 02:27Z (zero orders/positions); this public
test does not renew that exposure proof.
