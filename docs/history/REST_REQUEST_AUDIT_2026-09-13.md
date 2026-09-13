# REST request audit and support draft — September 13, 2026

## Scope and result

Operator approved auditing requests added since private WebSocket failure and
preparing the exact429 evidence for support. This is investigation, not renewed
implementation/deployment/restart authority. No source/test/config changes in
this audit; earlier uncommitted drafts remain untouched and unaccepted.
No trading API calls or mutations were made. Support draft below is NOT sent.

WSL process check at approximately05:15Z found no commissioning runner or
supervisor. Production marker4a166336a is unchanged. Pre-work parity matched
tracked laptop/GitHub/WSL02410236e; local draft drift is expected, desktop
offline/pending. Last account proof remains zero orders/positions04:22–04:23Z,
not a current account observation. Last ambiguity proof is04:16Z.

The audit does not establish an exact original cause or an undisclosed venue
quota. It DOES identify which extra requests replace private messages, which
apparent duplicates are safety boundaries, and which book amplification has
already been repaired in production source.

## Before versus current committed code

Compared pre-REST stablev1.2.0 `a3defc014` to committed source under02410236e;
paused working-tree changes are excluded from conclusions about production.

| Consumer | Before private-stream failure | Current REST mode | Audit conclusion |
|---|---|---|---|
| Account reconciliation | Private updates plus periodic10s complete REST backstop; decision freshness can also trigger a proof | Account-wide proof due5s after completion, or1s with a known position/unresolved intent; periodic proof coalesces when not due | Additional account traffic is real, but not one independent poll per entry/order. Intervals are not end-to-end detection latency. |
| Unchanged flat working orders | Healthy periodic WebSocket proof skips exact-ID sweep | Healthy REST proof uses two complete open-order reads and exact gets only for changed/missing/position/ambiguous orders | Second list checks for changes across intervening exact reads/cancel repair; do not delete it as a blind duplicate. Startup/failed proof/recovery stays strict. |
| Execution history | Recent trade backfill, normally up to2pages | Required descending retained-fill coverage, at least2pages if EOF is not reached earlier, continuing as needed to known-page overlap | Increased depth can be necessary without private fills. One collected batch is consumed once; no second history walk was found in the normal coordinator proof. |
| Equity and non-trading cash | Balance/positions/balance bracket, cash-history classification, final equal-cash check already existed | Same accounting authority; a recently completed bracket now coalesces with periodic work | Not newly introduced by REST. A position read outside the cash bracket cannot simply replace its marked-value read. No $90HARD/accounting change recommended. |
| Position watch during live discovery | Drain private updates | One complete position observation in flight, due5/1s after completion; observation only wakes strict reconciliation | Added traffic protects fills/exits from long scans. Not active in the stopped selector reproduction; do not remove it for speed. |
| Public books and discovery | Public stream plus quiet-book fallback; selector background2/s, up to100books | Public stream still enabled; same committed selector and public stream adapter; ordinary REST books now fetched just before owner decisions | Private failure did not move every public price update to REST. Book retries and prefetch aging were real local amplifiers, already repaired; startup scan pacing remains an unaccepted mitigation draft. |

Source routes: `rest_fallback.py` methods `_read_owned_order_states`,
`_fetch_rest_order_snapshot`, `_rest_reconcile`, `_rest_poll_due`;
`account_source_runner.py` methods `_replacement_candidates_for_top_up`,
`_collect_rest_snapshots`, `_fetch_execution_history`;
`account_snapshots.py` methods `fetch_executions`, `fetch_equity_snapshot`;
`live_commissioning.py` methods `_refresh_balance`,
`_refresh_periodic_balance`, `_refresh_execution_state_for_decision`,
`_refresh_quiet_book_if_due`, `_service_market_book` and `_decision_sweep`.
The public `MarketReadOnlyStream` section has no diff between the compared
commits; `streams.py` differences affect private queue/handoff handling.
The selector's identical Git blob is recorded in the linked prior comparison.

## Request counts, not a theoretical quota

The already-recorded108 completed pre-failure REST windows on September12
contain35,001 attempts over10,020.7seconds (3.493/s). This audit reuses that
dated log reconstruction rather than rerunning the bot:

| Category | Attempts | Share |
|---|---:|---:|
| Public books |13,303|38.0%|
| Exact order reads |6,292|18.0%|
| Execution history |3,770|10.8%|
| Open orders |3,449|9.9%|
| Positions |2,867|8.2%|
| Balances |1,951|5.6%|
| Creates plus cancels |821|2.3%|
| Other |2,548|7.3%|

Rounded shares need not sum to exactly100%. These categories do not identify
which caller made each individual request, or count uncoordinated traffic on
the public IP. Comparing them to different historical WebSocket windows is not
a matched-market A/B test. No claimed percentage saving follows from this.

After the first book-failure window,1,400 of2,196 book attempts in the recorded
failure sample were rejected. The old inline retries amplified this. Current
committed adapters do not retry a book429 inline; non-safety429 also yields to
later scheduling. Safety account proof may still retry within its bounded
lane. Exact-order404 retries are distinct from book429, and missing orders
must never be treated as canceled just to save calls.

[Original reconstruction](PUBLIC_BOOK_BACKOFF_2026-09-12.md),
[book just-in-time repair](JUST_IN_TIME_BOOK_SERVICE_2026-09-12.md),
[normal-paced reproduction](STARTUP_PATTERN_REPRODUCTION_2026-09-13.md), and
[slower scan / paused draft](PRE_REST_RATE_INVESTIGATION_2026-09-13.md).

## What remains unproven and recommendation

The official [Retail rate page](https://docs.polymarket.us/api-reference/rate-limits)
reopened in this audit distinguishes20/s per authenticated API key from20/s per
public IP. Book GETs use the unauthenticated gateway. Therefore extra account
polling alone does not prove exhaustion of the documented public quota. The
local4/s limiter is shared for scheduling; it is not evidence that the venue
combines those resources into the same bucket.

The current [changelog](https://docs.polymarket.us/changelog) still has Sept10
v0.0.88 as its newest entry. Status summary at05:15:51.5437554Z reported All
Systems Operational, zero incidents/maintenance. That neither proves every
account/endpoint healthy nor explains the book429. The documented delayed
order rejection named `Global Rate Limit Exceeded` is a different case from
this actual HTTP429 on a public GET.

The isolated old-paced selector failed without account polling; the one slower
scan passed112GETs including100books. These differ in time/cache/rolling load,
so neither the original trigger nor4seconds as an optimal limit is proved.
No new live test, threshold hunt, key/IP rotation or cooldown reset was done.

Recommendation: preserve existing coalescing and all fresh position/cancel/risk
proofs; do not globally slow the bot or remove protective checks. Ask venue
engineering to identify the recorded edge rule. If proceeding before their
answer, review optional discovery/entry-validation pacing separately from
mandatory exits and explicitly accept slower startup/refill. Such pacing is
mitigation, not root-cause resolution or steady-state capacity acceptance.
No additional obvious safe-to-delete account read was demonstrated in the
audited normal flow; this is not a claim that the whole system is optimal.

## Offline verification

Tests ran in the unchanged WSL production checkout, using fake venue fixtures:

- `test_rest_bulk_polling.py`:35passed in5.706s.
- `test_account_source.PermanentBulkTests`, the40-decision single-poll case,
  two JIT book/stream-reuse cases and `test_book_backoff`:47passed in6.447s.

82 test executions (including inherited overlap), not82 new tests. The bulk
case proves30unchanged flat orders require2lists+1position+1EOF history page
and0exact GETs, with0mutations. It is a controlled count, not a live capacity
test or a general four-request bound when history paginates/positions change.
The cadence case invokes40decision checks:0proofs before due,1when due.
Safety/race cases retain exact partial/missing/position-owner proof. No final
suite or review acceptance is claimed for the separate paused draft.

## Support message — prepared, not sent

```text
Hi, please escalate this to the Retail API/Cloudflare team. Our bot is stopped, and we reproduced HTTP 429 using a read-only public-market scan with no trading or account polling.

UTC: September 13, 2026, 04:34:55.670260
GET https://gateway.polymarket.us/v1/markets/aec-setkameua-marand-solser-2026-09-13/book
HTTP 429; Retry-After: 10
CF-Ray: a3a4713999a11906-EWR

The scan used a 2 requests/second lane under our shared 4/second limiter. It made 18 GETs total: 3 event pages, 9 incentive reads, then 6 books. The sixth book returned 429; we immediately stopped without retrying. This is not a claim that six books is the quota.

A later single scan with books spaced 4 seconds apart completed 112 GETs, including 100 books, without 429. Authenticated REST checks passed separately. Private WebSocket checks at 04:22–04:25 UTC connected and returned Ping/Pong but no application messages over 45 seconds with either key; public market streaming delivered data.

Please identify the rule matched by that Ray ID: endpoint/IP/key scope, threshold, counting window and cooldown. Does an additional Cloudflare restriction differ from the published 20/second public-IP limit? Please also investigate the missing private-stream messages; they are why we currently need REST account polling.
```
