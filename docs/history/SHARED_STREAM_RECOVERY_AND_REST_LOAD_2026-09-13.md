# Shared-stream recovery and REST-load review — September 13, 2026

## Authority and stopped state

While investigating REST usage, the operator chose **Stop and fix; do not
restart yet**, then stated that positions were closed manually and explicitly
authorized force-stop. The normal stop had already completed: supervisor168012
and runner168045 stopped with `operator_sigterm` at20:48:13.182885Z. No further
kill or restart was necessary. Do not attribute the operator's closure to a
successful bot exit.

Authenticated20:49:21Z and21:07:34.843108Z checks both prove zero venue orders,
positions, unresolved intents, unknown orders and ledger working/position rows.
Process inspection confirms the trading supervisor/runner are absent. The old
runtime source remains `b2b0d017e`; this repair is prepared, not deployed.
Read-only display/report services are not trading processes.

The operator additionally requested investigation to avoid recurring rate
limits. No size, strategy, risk, transport-selection or API-budget change is
authorized by this repair. Keep $90 HARD, size10/32, manual REST account mode,
max30 flat pending entries excluding all position-market orders/reducers/exits,
and every lower time/loss cap.

## Measured incident and causal path

The20:37Z restart initially traded. Around20:44–20:47Z the public full-book
stream latched `AdapterError("market WebSocket disconnected")`. A synchronous
REST-mode book drain observed it inside the decision sweep, disconnected the
gate and requested a stream rebind. The sweep's generic market-service catch
instead classified it as a separate failure for each owner, retired that
market, and continued. Subsequent owners repeated account/equity proof against
the disconnected gate and hit the same stored exception. Individual retirements
took roughly10–11 seconds; the outer recovery/logging loop could not run.
At20:47:21Z there were31 venue orders, including30 flat entries and one
position-market order, and one position; ownership was not ambiguous.

Re-raising the same stored exception also accumulated earlier consumer
tracebacks on that object. This amplified log size and obscured the one shared
fault. The exception object itself did not send REST requests.

The socket's original close cause is **unknown**. This local recovery defect
is proven from the flow and regression, not proof that Polymarket, a quota,
an account restriction or the laptop caused the disconnect. No live fault was
induced. The four completed API windows before the fanout contained zero429s;
that does not make the later stalled interval complete telemetry.

## REST comparison: measured attempts, not a controlled performance benchmark

Earlier September12 windows through08:06:51Z,08:08:04Z,08:11:58Z and08:15:17Z
total1,370 attempts over796.4 seconds. September13 windows through20:38:57Z,
20:40:05Z,20:41:09Z and20:42:09Z total907 attempts over289.9 seconds.
These samples include startup and different mutation/position workloads.

| Request group | September12 count / per second | September13 count / per second |
|---|---:|---:|
| Balance, positions, order lists, history and exact-order reads |660 /0.829|629 /2.170|
| Public REST books |428 /0.537|0 /0.000|
| Creates and cancels |62 /0.078|116 /0.400|
| Other metadata, cash history and earnings |220 /0.276|162 /0.559|
| Total |1,370 /1.720|907 /3.129|

Current account categories:104 balance,99 positions,117 open-order,
122 execution-history and187 individual-order attempts. All38 failed attempts
were individual-order404s, not429s. Even subtracting38 attempts mechanically
would leave about3.0/s; it is not a valid forecast of retry savings because
required confirmations and successful follow-up reads remain.

The earlier run already used REST account polling. Configuration still uses
five-second ordinary and one-second active/ambiguous polling, a completion-
relative poll clock, and the shared4/s admission governor. Removing serial
REST-book waits allows existing due checks to run more frequently; that is
code-supported explanation, not a controlled attribution of every extra call.
Cash-history pages appeared34 times in289.9s versus38 in796.4s, consistent with
more frequent equity proofs. Current mutations are also materially higher.
Every current window had max queued admission3–4 despite zero429s, so this
is not established sustained-capacity acceptance.

The equity cash/position/cash bracket and subsequent classified-cash validation
protect deposits/withdrawals and marked $90 HARD accounting. Do not remove
them as duplicates. Existing unchanged-flat bulk proof, completed-equity
coalescing, scoped hard-exit proof and bounded exact-order repair remain.
Aggregate categories cannot establish which individual-order reads can safely
be reused. Further steady-state savings require caller/reason evidence and
equivalent-proof tests, not arbitrary slowing of exit checks.

## Chosen repair and alternatives

Use a typed `MarketStreamUnavailable` for shared public failure and a fresh
wrapper per consumer. Market-local empty/malformed book handling remains local;
the original causal exception is retained without repeated traceback growth.

Synchronous decision/book/stale-recovery consumers revoke the entry gate and
yield immediately to the outer owner. Pending recovery prevents another slate
pass/account-proof fanout. The outer owner joins old readers, applies already-
consumed private evidence before queued evidence, then invokes the existing
coordinated stream/account recovery. New reader tasks start only after success.
Critical ownership evidence remains terminal. A failed recovery remains held
or returns the existing stop result, never invented readiness or flatness.

Continuing per-market retirement would retain the defect. Letting a leaf
consumer reconnect directly would lose task-held private ordering. Falling
back to whole-slate REST books would recreate the rate-limited dependency.
The chosen route preserves existing recovery quarantine, positive order proof,
bounded retries/backoff and maintenance handling. It can cancel resting quotes
and incur one coordinated recovery's account traffic; it does not promise
uninterrupted quoting or a zero-request reconnect. No new endpoint, poller,
automatic source switch, rate increase or relaxed freshness proof was added.

## Verification and dedicated edge audit

- Thirteen focused tests pass:32-owner drain failure, JIT failure in both
  account modes, both bound-service boundaries, stale recovery, stop priority,
  reader joining/rearming, recovery failure, actual fake-venue rebind in both
  modes, held-before-queued private ordering, full run-loop recovery/stop,
 96 fresh exception wrappers, market-local isolation, a coordinated429 retry,
  and critical held-private evidence. Several tests cover multiple cases.
- Four in-memory reversals fail by assertions on the repaired behavior:
  restoring the old decision sweep, book-service catch, reader-consumer owner
  handoff, or reused failure object. Import/setup errors are rejected by the
  reversal harness; source is restored before the final suite.
- Initial complete suite passed1,637 tests in224.134s. Final13-test focused
  suite passed in5.285s. Final complete suite passed all1,639 tests in226.966s
  with the native pinned WSL Python environment against the Windows review
  source. Four final reversals passed against baseline484514499. Documentation
  audit and `git diff --check` pass. No live/deployed acceptance is inferred
  from offline tests.
- Full/empty boundary:32 owners are kept, zero owners cannot submit, and a
  pending handoff blocks further flat or position decision sweeps until the
  existing recovery proves safe state. Position ownership/quantity is not
  removed. Existing tests cover partial fills, reducers, IOC retries, lower
  pending-entry caps and unconditional position service.
- Timing/races: sync failure before task delivery, task-held versus queued
  events, second sweep before rebind, failed bind, and operator stop are
  covered. No leaf starts competing readers or writes through a worker.
- Unknown/ownership: unresolved cancels still require terminal proof; unknown
  creates are not retried. Held invariant failure cannot be swallowed into a
  successful rebind. Existing scoped ambiguity remains fail-closed.
- Load: one shared handoff replaces up to32 repeated local failure/proof paths;
  ordinary cadence and4/s pacing are unchanged. Existing limiter tests cover
  Retry-After, cooperative cooldowns, bounded safety retries, optional-book
  single probes and interrupted admission. The new429 test checks owner-level
  backoff, not a claim about Polymarket's actual counting window/cooldown.
- Shutdown/restart/rollback: user stop remains authoritative; no database
  migration or risk reset. Deployment requires reviewed hash/test proof and
  separate authority. Restart requires explicit authorization and fresh
  authenticated flat/clear gates. Reverting this additive code restores the
  known recovery defect and must not be called a proven healthy rollback.

## Remaining acceptance

No restart is authorized. Live recovery, sustained REST rate/queue headroom,
natural fill-to-reducer/exit timing and full-capacity performance remain
unaccepted for this repair. A venue429 cannot be guaranteed away by the4/s
governor or one clean run; undocumented endpoint/IP limits remain external.
Further account-read savings are a separate measured follow-up, not claimed
as achieved by this recovery repair.
