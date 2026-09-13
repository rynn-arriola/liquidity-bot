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

## Subsequent authorized restart: normal startup still receives429

After the successful low-rate test, the operator asked to run the bot. Fresh
03:58:29Z account/ledger proof was completely flat and clear. Parity verified
the stopped ledger backup at03:58:40Z (388288512 bytes, SHA256 prefix
`0ec8bc10f6cb`); all187 deployed source/test/script/web/config hashes matched
the already reviewed marker `4a166336a`. Its unchanged1565-test native acceptance
was reused. No source/configuration or ledger repair was needed.

The repeated03:59:08.929550Z gate proved zero venue orders/positions, zero
ledger working orders/positions and zero unresolved/unknown state. The existing
launcher started `auto 10 32 rest` around03:59:10Z, supervisor2251387 and
runner2251414, retaining $90 HARD and max30 flat pending REST entries excluding
position orders/reducers/exits. Log suffix begins at byte71117517.

At04:00:13Z, both processes remained alive, but two startup deferrals and no
actions or ordinary API-budget windows had been logged. Shared limiter state
proved another book429 at04:00:02.308369Z with a ten-second endpoint cooldown;
the safety-lane cooldown did not advance. Thus the low-rate test passed while
normal startup did not. This demonstrates different observed outcomes at the
two workloads, not the exact Cloudflare rule or a proven account ban. No rate,
source, risk, freshness or retry-policy change was performed; the running bot
retains its existing safe preflight retry, with no manual restart loop.

At04:01:07–04:01:11Z, independent complete authenticated acceptance confirmed
zero orders, zero positions, zero unresolved intents and zero unknown orders.
Both processes remained alive after2:02; the run suffix contained four startup
deferrals, no actions and no ordinary API-budget windows. Quoting is not accepted.

## Latest operator stop

After the normal startup kept failing, the operator said "Close bot again."
The standard stop script ended supervisor2251387 and gracefully signaled
runner2251414. Both are absent; the final log reports operator stop during API
admission backpressure. No force-kill was used. Complete authenticated reads at
04:15:58–04:16:02Z proved zero orders, zero positions, zero unresolved intents
and zero unknown orders. Startup deferrals continued through04:15:19Z.
The bot remains stopped with source/configuration/risk unchanged. The low-rate
test was not representative of normal startup capacity; future investigation
must not present it as that proof or reuse old restart authority.
