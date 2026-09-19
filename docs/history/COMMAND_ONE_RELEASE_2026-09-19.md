# Command 1 combined release — September 19, 2026

The operator issued Command 1 after the report-only rollout, authorizing the
normal graceful stop, reviewed pending deployment and gated restart. No new
strategy or risk choice is included. Release target is
`6a1032a6124627c3b445febe94f58aa1a1c6d66c`.

## Stop and deployment proof

The old runner received SIGTERM and stopped at12:18:35.258055Z with
`stop_reason=operator_sigterm`, `flat=true`. The supervisor and trader were
absent afterward. At12:21:35.205716Z authenticated complete reads proved zero
orders and positions; the read-only ledger also had zero working orders,
positions, unresolved intents and unknown orders.

Predeployment parity confirmed clean Windows/GitHub6a1032a61. A consistent
stopped-runtime recovery copy captured12:21:52.777554Z passed integrity and
destination size/hash verification:764,481,536 bytes, SHA prefix51d412bc4297.
The offline desktop was not contacted. No recovery copy became authority.

Only already-reviewed pending changes were deployed: scoped REST recovery,
optional selection provenance/offline validation, and the reward PNG report.
Prior complete suites, focused regressions, deliberate reversals and dedicated
edge audits were reused from their dated records. The final source diff was
reviewed again: no configuration, API allowance, pricing, cap, exit deadline,
source selection or settled-loss rule changed. Unknown state still fails closed.

The three exact report-only overrides matched the target release. They were
preserved in a path-scoped recoverable Git stash under the report lock, then
WSL fast-forwarded from704678807 to6a1032a61 using a verified bundle of the
already-pushed GitHub commit. No unrelated file or private data was replaced.
All219 source/test/config/script normalized hashes match Windows and release
objects; tracked runtime files are clean. The earlier report-only manifest is
historical and superseded by this full deployment.

## Acceptance

The exact installed Python environment passed all1,947 tests in193.485s.
The deployed marker was advanced only after the successful suite and hashes.
Fresh authenticated12:26:46.685931Z gate again proved zero venue orders,
positions, unresolved intents, unknown orders and ledger exposure. Normal
launcher `auto 10 32 saved` started supervisor1153551 and runner1153573;
the persisted source was REST. The TV observer was already running.
Authenticated12:31:22.228077Z acceptance proved25 pending flat entry orders
across20 markets, zero positions, zero unresolved intents and zero unknown
orders; the ledger agreed. The runner remained present at5m09s uptime.
The persisted06:00–12:00 ET window had$5.9871 settled loss/$25, cap25 and no
HARD hold; restart did not reset its budget or tier.

The first three API windows made559 attempts over277.1s, approximately2.017/s:
2.054,2.117,1.926rps. Zero429/cooldown, two startup individual-order404s, then
two failure-free windows. Initial31-book and22-bound-book reads used public
streams with zero REST fallback. A natural lifecycle top-up released one
expired flat owner, retained21 and added4, committing25 active markets without
cancelling retained orders. Its53.9s lifecycle interval and14.745s decision-age
p95 remain performance observations, not a WebSocket-equivalent claim.

At12:29:31Z all28 new quote rows had provenance, original ranks,30-second
model horizon and calibration identity;27 had current ranks. Missing current
rank remains explicit rather than being filled from original rank. No natural
position or scoped recovery occurred during this acceptance; urgent exit and
recovery performance cannot be certified from a flat sample.

Preserve size10/32, manual REST, public streamed books, four settled $25 ET
windows, durable30/25/20/15/10 entry tiers excluding positioned orders, and all
existing selection/exit rules. No loss baseline or tier is reset by deployment.

## Remaining natural evidence

Observe genuine scoped recovery, priority exit timing and comparable active-
position API load; unit-test savings are not a live latency or zero429 promise.
Verify new quote provenance coverage and the next natural hourly reward image.
Keep the saved measurement priorities; incomplete economic cohorts do not
authorize a new entry filter or model change. Do not induce fills or faults.
