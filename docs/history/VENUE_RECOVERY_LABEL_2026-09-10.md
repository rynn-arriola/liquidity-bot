# Venue recovery reporting — September 10, 2026

## Scope and incident evidence

The operator authorized the reporting fix and restart, then asked to continue
after an interrupted stop. Only presentation changes are authorized here.
Generic API/stream recovery was logged as `maintenance-pause` and its successful
account-health gate was announced as maintenance. That is not proof of an
official maintenance window or of the underlying outage cause.

The previous runtime repeatedly lacked private initial snapshots
(`order_eof=0/4 account_balance=0/4`) after a disconnect. Successful REST health
checks did not satisfy private-stream startup proof. The relationship to any
separately reported funding/balance-ledger incident is unproven; do not present
that possible association as the established cause or an IP-ban diagnosis.

The graceful stop ended at 2026-09-10 21:34:50Z with `operator_sigterm`,
`flat=true`. At 21:36:35Z independent authenticated reads proved zero orders
and positions; the ledger had zero working/unknown orders and unresolved
intents. Both supervisor and runner were absent. Parity matched Windows,
GitHub and native WSL at `ded9cee3`; the inactive backup PC was unavailable.

## Repair and dedicated edge-case review

- Unplanned coordinator output uses `venue-recovery-pause` and venue recovery
  wording; `planned=True` retains scheduled-maintenance messages and tags.
- Enter, failed probe, successful probe, and health-gate completion use the
  same selected label. The success message still explicitly says quoting is
  blocked until stream and exact reconciliation gates pass.
- The read-only API capacity report recognizes the new tag as runtime recovery.
  Existing log tags remain supported. Historical ambiguous maintenance labels
  are not retroactively proof of a scheduled outage.
- Inspected every coordinator caller: only the existing scheduled coordinator
  sets `planned=True`; startup and private/market/bound recovery use the default.
- Success/failure and three-read streak behavior, empty/full account snapshots,
  stop/timing races, unknown ownership, API pace, recovery, shutdown, restart,
  configured size/capacity and risk persistence are unchanged. This diff has
  no new venue call, mutation, schema, state reset, gate or classifier change.
- Rollback affects wording only. No database migration or reconstruction is
  needed; reverting the label must also preserve its report consumer.

## Verification

The focused reporting regression covers both planned states and a failed probe
followed by three complete reads, preserves no-create/no-cancel assertions,
and checks that health success cannot claim resumed quoting. Reversing the
label selection failed the unplanned subtest. The capacity consumer regression
also failed when recognition was removed; after restoration all 20 capacity
tests pass. Final pinned-venv review-worktree suite: **1,228 tests passed** in
117.270 seconds. Documentation audit and final diff/edge-case review passed.

## Release acceptance

- Pushed and deployed `3625927fb`; all 168 normalized source/test/script/web/
  config hashes match. Native complete suite: **1,228 passed** in 89.087s.
- Windows/GitHub/WSL and installed marker match; inactive backup PC remains
  pending/offline. No strategy, cap, size, HARD, entry-quality or live-event
  protection changes are included.
- Fresh authenticated 21:45:16Z gate again proved zero orders/positions and
  zero working/unknown/unresolved ledger state. Normal `auto 10 32` launch
  started supervisor `2819379`, runner `2819387` at approximately 21:45:17Z.
- First selection found 29 viable markets in three event pages. Startup then
  dropped one market whose pregame lifecycle could not be proved. At 21:46:42Z
  startup again failed private-state proof with `order_eof=0/4
  account_balance=0/4`; the same runner remains alive and retrying without entry.
- Independent authenticated 21:47:17Z acceptance still found zero orders,
  positions, working/unknown orders and unresolved intents. Restart is accepted
  as a running fail-closed retry, **not restored trading**. New-label emission
  awaits the natural longer recovery coordinator; it was regression-tested,
  not induced live. The underlying stream failure is not repaired by wording.

## Read-only API-structure check at 21:53Z

The operator asked whether Polymarket changed the API because the app appeared
healthy. Rechecked the official [changelog](https://docs.polymarket.us/changelog),
[Retail private WebSocket contract](https://docs.polymarket.us/api-reference/websocket/private),
Python SDK documentation, installed socket/decoder code and live PyPI metadata.
No announced relevant private-stream breaking change was found. The latest
published SDK remains `polymarket-us==0.1.2` (January 22 upload), matching the
installation. The documented subscribe wrapper, order/position/balance enums,
endpoint and balance/order snapshot names are already supported by the bot.

An isolated signed, read-only raw WebSocket bypassed both the SDK message loop
and bot decoder. It subscribed only to account balance and account-wide orders,
used one connection/two subscriptions, had no ledger or order-mutation access,
and closed after its bounded observation. At 21:52:41–21:53:26Z the authenticated
handshake succeeded and explicit initial/final protocol Pongs succeeded, but
**zero application frames** arrived over 45 seconds; therefore no frame failed
JSON or bot decoding. This independently reproduces silence with the documented
request format; it is not evidence of a renamed incoming field being rejected.

The public status feed still reported partially degraded service and the
investigating [balance-ledger latency incident](https://stspg.io/xfr2rjt0vds1).
Its relationship to Retail private-stream silence remains unproved. The test
does not rule out an undocumented server-side change or account-specific
subscription fault, nor does handshake success prove trading permission.
If silence persists, venue support needs these UTC times, endpoint and safe
probe results to investigate delivery. No support message, production edit,
SDK upgrade, gate bypass, order mutation or restart was performed for this check.

## Operator stop around 23:25Z

The 22:42:14–22:42:59Z repeat raw probe also received zero application messages
despite authenticated handshake and initial/final Pongs. At 22:42:35Z the
account was independently flat/order-free. Startup remained blocked through
attempt 40 at 23:24:09Z, still missing all four private balance snapshots.

The operator requested stop, then force stop while the command was finishing.
The normal stop script stopped supervisor `2819379` and sent SIGTERM to runner
`2819387`; the next check found both absent, so no force kill was needed or used.
The last log line was a transport failure retrieving `caoc-073e1f995509080b`
(404/code 5), not a new successful flat shutdown record.

Authenticated 23:25:31Z reads found zero orders and one nonzero position, with
zero ledger working/unknown orders or unresolved intents. The 23:25:59Z repeat
identified `+278.8900` contracts in `caoc-073e1f995509080b` and zero orders.
This supersedes earlier flat evidence. The position's origin/ownership is not
established; no position mutation or ledger repair was attempted. The operator
was told that it remains. Bot and supervisor stay stopped, configuration/source
unchanged, and no restart is authorized by the stop request.
