# September 12 account-source/performance release attempt

## Authority and reviewed package

The operator authorized loading today's prepared changes and checking live
readiness, superseding the earlier deployment/restart hold. After shutdown
started, the operator reported manually closing exposure. Keep REST selected,
size 10 / 32, max 30 flat pending entries with position/reducer exclusions,
the shared 4/s governor and every existing strategy/risk rule.

Reviewed package: `8ab1738bf`, including the whole-bot R1–R5 repairs,
final-entry readiness, ordered identity-lookup index, interruptible candidate
scans, single-flight position wakeup, serial identity projection and isolated
execution timing. Existing exact-source evidence is reused: 1,525 passing tests,
restored deliberate reversals and dedicated edge-case review. The combined
source/script diff against loaded `b826ff11e` was reviewed again before release;
No configuration/risk change was introduced. Two additional release-blocking
issues found during shutdown are addressed below.

## Shutdown and gates

- Supervisor 649355 stopped and runner 649372 received graceful SIGTERM around
  15:34:56Z. No force-kill, source switch or direct venue mutation was issued.
  Normal runner shutdown owns cancellations/exits.
- Parity found clean Windows/GitHub `8ab1738bf`, WSL `f9f9d8c4` and loaded
  `b826ff11e`. The private online backup at 15:35:04.618045Z was verified:
  368,947,200 bytes, SHA-256 prefix `06596de350d7`. The desktop peer is offline.
- Authenticated 15:36:19Z observation: zero orders, +10 `stolud-lopvla` and
  +10 `chlpaq-marcap` positions. At 15:37:12Z the strict start gate refused:
  venue zero orders/two positions; ledger zero unresolved/unknown/working but
  three nonzero positions.
- After the operator's manual closure, 15:38:09Z authenticated proof showed
  zero venue orders/positions, zero unresolved/unknown/working ledger state,
  but three stale ledger positions. Restart remains refused until normal
  reconciliation proves the full known state and the old owner is absent.
- The old runner repeatedly logged `GET /v1/portfolio/activities`
  `APITimeoutError` before and during shutdown. Its shutdown IOC plans for the
  final two owners were refused locally with `ApiAdmissionDeferred`; a logged
  plan is not a submitted or executed exit. No missing history is bypassed.
- The official [changelog](https://docs.polymarket.us/changelog) was reopened;
  its latest entry remains the September 11 04:00–07:00 ET maintenance notice.
  The [status page](https://status.polymarketexchange.com/) fetch failed. This
  does not establish a currently declared outage or maintenance.

## Additional release repairs and dedicated edge-case audit

Direct, bounded read-only tests isolated a request-shape difference, rather
than proving all REST down:

| Request | UTC start | Result |
|---|---|---|
| Unfiltered limit 100, descending | 15:38:51Z | APITimeoutError, 15.152s |
| Unfiltered limit 20, descending | 15:39:06Z | APITimeoutError, 15.147s |
| Unfiltered limit 20, default sort | 15:40:15Z | APITimeoutError, 15.154s |
| Trade-only limit 20 | 15:40:30Z | 20 rows/continuation, 0.207s |
| Trade-only limit 100, descending | 15:43:22Z | 100 rows/continuation, 0.284s |

The Retail [activities contract](https://docs.polymarket.us/api-reference/portfolio/get-activities)
documents the type filter, descending order, cursor and EOF. Execution-history
collection already ignores non-trade records: it now requests
`types=[ACTIVITY_TYPE_TRADE]` on every page. No cash/settlement/account-mark
reader is changed. Trade lifecycle including BUSTED, exact account-leg choice,
deduplication, complete required coverage and immutable conflict handling remain.
The normal prepared parser, with read-only durable anchors, proved two-page
coverage over 200 executions and 200 trade states at 15:45:41Z in 4.253s,
including three new execution IDs. It used the production shared report lane
and made no ledger write. An earlier isolated-source probe refused before any
request because its default credential path differed; explicit production file
and limiter paths corrected that diagnostic without copying/exposing secrets.

Shutdown had incremented the per-owner IOC counter before admission and kept
the charge when exact local NOT-SENT refusal occurred. Only
`ApiAdmissionDeferred` now refunds that attempt. Every sent, acknowledged,
uncertain, rejected or other failure retains the prior conservative accounting;
the overall shutdown deadline, backoff, three real attempts, exact quantity,
ownership and fresh proof are unchanged. Nothing retries an unknown create.

Focused regressions passed, then both repairs were deliberately reversed:
the filter assertion errored and the shutdown test showed only three local
refusals with no remaining send capacity. Both were restored. The initial
1,527-test full run exposed six failures from one existing fake portfolio
assuming every type-filtered request was cash-only. Its response now includes
trade records when the documented trade type is requested; all existing loss
and account-leg assertions are preserved. The corrected full suite passed
1,527 tests in 143.799s. Four additional shared lifecycle replays then passed
in 1.015s across strict REST, optimized REST, permanent REST and permanent
WebSocket. The final combined suite is recorded below.

- Empty/full/page boundaries: empty EOF remains no synthetic fill; an empty
  non-EOF first page continues; filters/cursors persist through every page.
  Complete history coverage, duplicate/fill conflicts and BUSTED remain tested.
- Timing and load: no new endpoint, faster cadence, timeout change, independent
  limiter or added request. Trade-only pages can improve useful coverage without
  accepting partial evidence. A locally deferred IOC waits normal backoff and
  a new full reconciliation; it cannot reset the overall shutdown clock.
- Ownership and uncertainty: exact local refusal yields a FAILED durable
  intent; four local refusals still permit only three real IOCs in the test.
  Sent/unknown outcomes never refund, reuse a client ID or trigger blind retry.
- Recovery, shutdown, restart and rollback: one serial owner remains; no live
  patch or process replacement before stopped proof. Source modes share these
  repairs; no auto-promotion or persisted risk/position-clock reset. Old source
  remains schema-compatible but restores these defects and is not a safe
  automatic rollback.
- Business policy: entry/reward/price/size, exact exits, HARD and pending-order
  exclusions are unchanged. External history/stream or liquidity failure can
  still block safe operation. Final diff/secret/whitespace checks required.

## Release status

Final combined isolated suite: **1,531 tests passed in 141.495s**. All 185
source/test/script/web/config normalized hashes match the tested copy. Final
source/test diff, dedicated edge-case, whitespace and documentation audit passed.

The old runner eventually completed its original unfiltered reconciliation and
exited **15:50:06.469082Z, flat=true**, without force-kill or ledger repair.
Thus the history symptom was intermittent/query-dependent, not proof of a
permanent endpoint outage. The 15:51:02.728903Z authenticated gate independently
proved zero venue orders/positions and zero ledger unresolved/unknown/working/
nonzero-position state; runner and supervisor were absent. A stopped online
backup at 15:51:21.598339Z passed integrity, 368,975,872 bytes, SHA-256
`79de0c0dae19878ed89a984bdd2a931232afc2e82d75a325c8bfca3ae0f26d89`.

Push/parity, exact deployed hashes/tests and a fresh start gate remain before
the authorized REST restart. Natural position/exit/API observation is required;
offline passing tests do not establish WebSocket-equivalent live latency.
