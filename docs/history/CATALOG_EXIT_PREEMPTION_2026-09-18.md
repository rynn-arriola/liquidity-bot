# Scan/exit final review — September18 ET

The operator accepted the targeted pre-restart recommendation and explicitly
approved graceful stop, reviewed deployment and restart only after all tests
and flat/clear safety gates pass. Retain the prepared exact-coverage,
inter-position urgency and reducer-decision diagnostics. No reward ranking,
entry horizon, price, quantity, loss, source or API-limit change.

## Confirmed additional gap

The catalog watcher detects position changes, owner-generation changes, stop
and existing exit deadlines at request/admission/public-book checkpoints.
However, an in-flight optional REST read awaited the client directly. A completed
position observation could not interrupt that wait until the request returned
or reached its network timeout. Wider scans create more exposure to this gap,
but the gap predates rewards adaptation. No measured cancellation or loss is
attributed solely to it.

A fake-client regression stalls an event request after a new position becomes
observable. Before the fix it times out instead of yielding to account proof;
afterward it raises catalog deferral and joins the read worker. The observation
remains a wakeup only: no ledger position projection, selection commit, create
or cancel occurs in the watcher.

## Chosen implementation and tradeoffs

Only inside an explicit read-checkpoint scope, keep the raw SDK read in one
cancelable worker and run existing checkpoints on the serial owner at250ms
wait intervals. The worker inherits no ledger checkpoint. On interruption,
cancel and join it before ordinary complete proof/exit handling resumes.
No parallel mutation executor/projector. Unscoped safety reads retain direct
awaiting. Admission, network timeout, polling, pagination and proof unchanged.

Local checkpoint failures preserve their exception and bypass venue-error
accounting, cooldown publication and retry logic. Real response errors retain
existing classification. A late response after cancellation cannot become a
partial selection. This does not guarantee an exit within250ms: watcher cadence,
proof, cleanup, network and price/depth requirements still apply. A canceled
request may count at the venue; later discovery may repeat work. Measure load.

Prefer this over faster polling, reducing scan count, full reward rollback or
parallel execution. Tradeoff: abandon optional discovery work when safety needs
the owner, preserving the current horizon and ranking.

## Dedicated final review / edge cases

- Existing exact-coverage/urgency/diagnostic tests run with catalog, public
  selection-book and optional-read pacing coverage.
- New integration tests: fill during stalled read; covered long/short crossing
  the existing60s discovery-deferral boundary; stop, scope and generation changes
  in both REST and WebSocket modes.
- Worker callback cleared; checkpoints stay on serial owner; unscoped safety
  reads stay on that owner; cancellation waits for worker cleanup.
- Successful request remains one request/result. Real429 is recorded; local
  deferral/programming exceptions are not wrapped or counted as venue failures.
- Caller cancellation and a late-returning canceled worker cannot publish a
  selection. Failed/stale/future watch tests still revoke proof. No absence is
  treated as terminal order evidence; no new retry.
- Normalization/event-ledger projection remain outside the raw SDK worker.
  Driver-less startup bypasses watcher; handoff/shutdown retain safety gates.
- No schema, credentials, persistent state, config or dependency change.
  Rollback retains normal reviewed source and account gates.
- Repricing necessity awaits decision diagnostics. Passive pricing unchanged.

Focused100 tests passed in28.069s. In-memory reversal restoring direct waiting
caused exactly one expected assertion failure; disk source never reversed.
Complete production-environment suite against the Windows review checkout
passed all1,878 tests in295.068s. Final source diff and edge-case audit passed;
documentation audit passes. No source changed after the full suite. Installed
suite, deployment and natural acceptance follow separately.

## Pre-review live evidence

Same supervisor388103/runner388121 on `aae337658`. Authenticated September19
02:35:46.786317Z:31 orders,30 entries plus OPEN SELL10 for sole qihzho/bendud+10;
zero unresolved/unknown, local counts agree. Earlier unknown record cleared
naturally without repair. Not a flat restart gate. Latest checked02:32:27Z API
window:238 requests/69.8s=3.410/s, zero failures/429.

## Authorized stop; activation blocked by nonflat account

Reviewed source committed/pushed as `683cf4160`. Graceful stop disarmed the host
watchdog, stopped supervisor388103 and signaled runner388121. It exited at
02:44:20.473954Z with `stop_reason=operator_sigterm`, explicitly `flat=false`.
Both PIDs are absent. Shutdown canceled flat-market entries but its existing
park path retained three pregame positions and matching exit orders.

Authenticated02:44:36.868437Z:three orders/three positions, zero flat-market
entry orders, zero unresolved/unknown; ledger counts agree:

| Market | Position | Remaining exit |
|---|---:|---|
|pyrboh/bolole|+9|SELL9 OPEN|
|volyur/kryvit|+6|SELL6 PARTIAL|
|annfri/deskra|+10|SELL10 OPEN|

This fails the explicit flat/clear activation gate. No deployment, marker
update or restart occurred. WSL remains HEAD`67d11c0dd`, previous source marker
`aae337658`; Windows/GitHub match`683cf4160`. Existing exits remain at the venue
but no runner now manages them. Do not infer settlement, cancellation or safety
from process exit. Ask whether the operator will close manually or authorizes
the existing price-capped cancel/close procedure, which can realize losses.
Then repeat authenticated gates, stopped recovery backup, reviewed deployment,
hashes/installed suite and fresh restart gate. Do not bypass the nonflat state.
