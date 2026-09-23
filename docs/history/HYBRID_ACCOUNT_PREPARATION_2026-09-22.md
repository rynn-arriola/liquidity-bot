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
