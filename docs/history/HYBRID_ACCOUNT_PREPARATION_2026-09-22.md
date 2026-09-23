# Hybrid private assistance preparation — September 22, 2026

## Scope and deployment boundary

Operator authorized private WebSocket assistance with REST retained as fallback,
then stopping the independent local WebSocket availability measurement. No
deployment, mode change or restart was requested. The live runner remains on
`5bbfa5550`, explicit `rest`, size15/32; no trading/risk configuration changed.
Current authenticated exposure was not read during implementation and is unknown.

## Design and full-flow review

Reviewed startup, private reader/decoder, ledger projection, REST backfill,
pre-mutation admission, source switching, catalog scans, recovery and shutdown.
See [architecture](../ACCOUNT_SOURCE_ARCHITECTURE.md). Fully authoritative private
mode still needs balance delivery; reducing REST cadence before proving stream
completeness would weaken position/exit safety. The chosen restart-scoped
`hybrid` mode instead adds one optional global private listener to the existing
REST authority and the same serial trading owner. Existing modes/defaults remain.

- Positive exact order updates/fills reuse the existing ownership/idempotence
  projector. Empty or partial snapshots and position messages are hints only.
- Hints are drained before complete REST proof; even an event already held by
  the outer event task is applied first, once, in sequence.
- Pending/unreconciled observations bar order POSTs at final admission, including
  reducers whose quantity could be outdated. Exact cancels remain available.
- Quiet/Pong-only sockets never renew account proof. Disconnect/schema errors
  fall back and reconnect with bounded exponential backoff; ledger/integrity
  conflicts still propagate under existing fail-closed handling.
- A bounded reader queue applies backpressure rather than silently losing fills.
  Close joins the background reader, including connection establishment.
- Balances, complete positions, history and periodic REST reconciliation remain.
  No strategy/ranking, caps, quote size, settled-loss policy or exit clock changed.
- Launcher and host-recovery accept an explicit saved hybrid selection. In-process
  switching into/out of hybrid is refused; reviewed stop/start gates are required.

The review caught an amplification in the initial draft: revoking the full REST
certificate on every normal event disabled the unchanged-flat bulk optimization,
potentially requiring thirty exact reads per notification. Normal events now use
a separate create-admission latch while retaining eligibility for the existing
two-list proof; gaps or failed REST/schema proof still revoke readiness. Tests
prove stale creates remain barred and the unchanged-flat shortcut remains valid.

## Tests and dedicated edge audit

Isolated native WSL staging uses the production virtualenv interpreter/dependencies,
not live files, credentials or ledger. Complete suite: 2,018 tests passed in
192.794 seconds; nineteen new hybrid tests cover success/failure, silent streams,
connect/close, disconnect/reconnect, bounded backlog, task-held ordering, exact
terminal proof, missing-terminal REST fallback, empty snapshots, position hints,
partial fill then cancel, duplicate delivery, integrity/schema failure, failed
REST proof and events arriving during a proof. The final source rerun completed
2,018 tests in200.240s: all runtime tests passed, but the documentation test
found the staging copy lacked this new history file and retained an older NEXT
line count. Copied the exact corrected documents and reran the documentation
test successfully; workstation audit also passes. Source/test directory comparison
matches staging (excluding ignored egg-info/cache). Launcher shell syntax and
final diff checks pass. No test sends live orders or induces faults.

Deliberate reversals in isolated test harnesses both failed as expected:

1. Bypass the hybrid drain before REST proof: exact-terminal saved-read regression
   fails, demonstrating the shortcut really depends on the implementation.
2. Bypass final pending-observation admission: stale-create regression fails.

Final edge review preserves unknown-not-flat, one ledger writer/mutation owner,
intent-before-network, strict identity, deduplicated fills, bounded resource use,
cancel availability and all prior admission guards. Ordinary notifications force
fresh proof, so they can add REST work as well as eliminate exact terminal reads;
live performance parity/savings are not established. No rate-limit guarantee.
Rollback requires reviewed flat/clear gates; select explicit `rest` and verify
persisted source instead of hot-switching or retaining an unsupported saved mode.

## API scope

Reopened the official Retail private-WebSocket guide and changelog. Endpoint,
camelCase subscriptions, global orders and optional position market scope remain
compatible with the adapter. Status-page fetch failed; no outage inference made.
Sources: https://docs.polymarket.us/api-reference/websocket/private and
https://docs.polymarket.us/changelog .

## Measurement stopped; trading process untouched

Verified diagnostic PID/PGID/SID190964 identified the independent availability
monitor and its read-only child, not the trading group. Its TERM trap did not exit
the loop, so the exact verified diagnostic group was terminated and only its stale
matching PID file removed. JSONL history under ignored
`data/private-ws-availability/` is preserved. Follow-up process inspection found
no monitor/probe and the unchanged supervisor121616/runner121634 still running
`auto 15 32 rest` at22h06m uptime. No account API requests were made to stop it.

## Remaining acceptance

An independently authorized deployment/restart must pass current flat/clear gates,
exact hashes and installed tests, then verify hybrid operation, authenticated
reducer coverage, unresolved/unknown state, fallbacks and API telemetry. Compare
representative busy/flat windows: exact terminal-read reductions alone do not
establish aggregate savings. The earlier projected2.5–2.9rps was unverified, not
an acceptance target or measured result. Do not restart the separate measurement.

## Command1 activation and subsequent reducer investigation

Later Command1 authorized deployment/restart. Authenticated stopped gates at
02:38:19Z and02:53:09Z on September23 proved zero venue orders/positions,
unresolved/unknown orders and working ledger exposure. Installed release
`a46f5f995136f6ec5e79afbf53141f1a0b11029c` passed all2,018 production-environment
tests in201.012s and225 normalized workstation/runtime artifact hashes. A stopped
ledger backup was captured02:38:42Z. Source marker was updated after these gates.
Supervisor407/runner421 started02:54:01Z (September22 22:54ET), explicit
`auto 15 32 hybrid`; size, slots, four$30 settled-loss windows and caps unchanged.
The WSL origin fetch stalled; deployment used the already-verified local Git
checkout as the fetch source. This delay was not test execution time.

Initial observation exceeded six minutes, but sustained acceptance did not pass:

- First three API intervals:928 calls/298.9seconds, about3.105rps. This is not a
  controlled before/after comparison; the prior claimed2–3% improvement and
  projected2.5–2.9rps are not demonstrated savings.
- Later intervals at03:01:37/03:02:56/03:04:21Z reported3.376/3.537/3.473rps.
  The first contained one balance429; the third two exact-order404s. Earlier
  zero429 windows do not establish an error-free run.
- By03:04Z the optional listener reported131 order events,17 hints and zero
  gaps. REST remained authority. This is not evidence of repeated transport
  fallback: normal notifications themselves clear the poll timestamp, revoke
  position confirmation caches and set the global create-admission latch.
- The latch bars every POST, including reducers, until full reconciliation;
  successful scoped position proof does not clear it. Logs show reducer
  ApiAdmissionDeferred attempts, including after terminal cancellation.
  Individual admission errors lack specific error-code telemetry, so the latch
  is a supported code-level mechanism, not proof of every observed delay.
- Pry dmy/Syk ole market `aec-setkameua-prydmy-sykole-2026-09-23` first observed
  inventory03:00:16Z and reducer acknowledgement03:01:44.828668Z,88.574754s
  later. This is an observed delay, not WebSocket-equivalent performance.
  Zavmyk/Pohmyk replacement was positively canceled03:03:21.317128Z,
  deferred03:03:24.960553Z and acknowledged03:03:30.429427Z (9.112299s gap).
- Authenticated03:04:13Z found25 orders:22 flat-market entries and three exact
  reducers covering positions+15,+2,+2; unresolved/unknown counts zero.
  Ledger26 working vs venue25 is a non-atomic live read, not certified parity.

The operator requires REST-baseline behavior. Before further rollout, reproduce
notification-driven proof amplification and cancel-confirm-replace admission
races, then compare coalesced/scoped alternatives without removing fresh exact
position/ownership proof or changing business rules. No repair is yet implemented.
Do not infer fill-to-exit latency from fast HTTP acknowledgement timings.

## Operator force stop

The operator explicitly requested force stop during the investigation. At about
03:08Z the supervisor was stopped, host recovery disarmed and runner421 force
killed through the approved stop script with zero grace. No restart performed.
Authenticated03:08:07.798394Z showed22 orders (20 flat-market entries), three
positions, one unresolved ledger intent and zero unknown orders:

| Market suffix | Position | Venue reducer |
|---|---:|---|
| lukpok-marzie | +15 | SELL15 OPEN |
| xirhan-alikac | -7.54 | None |
| zavmyk-pohmyk | +2 | SELL2 OPEN |

The operator was immediately warned that orders/positions remain and the stopped
bot cannot manage the uncovered position. Force stop is not flat proof; the
unresolved intent needs normal positive reconciliation before any restart.
No order cancellation, liquidation, ledger repair or risk reset was performed
by the diagnostic. The separate WS measurement remains stopped.

Operator subsequently reported manual closure. Authenticated03:10:01.130087Z
proves zero venue orders and zero positions; both former process IDs are absent.
The stopped ledger still has23 working rows, three position rows and one
unresolved intent (zero unknown orders). These are stale local records, not
current venue exposure; normal positive reconciliation must resolve them before
any later restart. No repair, ledger change or restart performed in this check.
