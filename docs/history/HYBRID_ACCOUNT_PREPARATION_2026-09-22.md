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

## September22 stopped repair and historical behavior comparison

Operator authorized fixing/verifying the regression, keeping the bot stopped,
and requested comparison with the earlier fully-private implementation. Reviewed
`v1.2.0` / `a3defc014` private event projection, session gate, state refresh and
terminal-cancel priority. Normal order updates did not invalidate account-wide
readiness; valid messages refreshed private evidence, and confirmed cancellations
woke their exact owner. That relied on a fully established private source. We
must not copy its readiness assumptions onto the current incomplete stream or
restore its historical size/$90 risk policy. No rollback was performed.

Chosen bounded repair, rather than bypassing final pending-message protection or
making this incomplete stream the sole position authority:

1. An exact known no-fill OPEN/NEW acknowledgement is projected normally but
   retains normal REST polling cadence. Identity, quantity, price and cumulative
   fill amount must match an owned order or exact pending create. Missing fields,
   unknown event type, new/changed fill quantities or unknown ownership retain
   full proof. It cannot clear an earlier pending fill/hint or renew gate age.
2. Successful existing scoped position reconciliation records a single-use
   reducer-only certificate after exact owner reads plus complete orders,
   positions and mandatory execution history. Final submission rechecks original
   proof age<=30s, source generation/scope, identical nonzero position, exact
   reducing direction/size, no working owner order, no absent order or other
   unresolved intent, and no pending private frame. Only the matching unsent
   PLACE may remain. The existing REST gate/driver must independently pass.
3. A new observation, another reconciliation, failed proof, changed quantity,
   source/scope, pending event or expiry removes/refuses that certificate.
   It never renews entry authority/global poll time and is consumed once.
4. Existing hybrid telemetry adds `ack_only` and `scoped_admissions`; these count
   scheduling/proof reuse, not guaranteed avoided requests or executed orders.
   No additional venue call or independent measurement process is introduced.

The old global hint latch remains for entries; cancel, strategy, pricing,
inventory/tiny/kickoff deadlines, settled-loss rules, size/caps and ordinary
REST/WebSocket modes remain unchanged. Disconnect/schema failures retain strict
REST recovery. Pure REST still supplies every needed observation if private
messages are absent; there is no automatic selected-mode change.

Verification uses fake venues/temporary ledgers in isolated native WSL snapshots,
the production virtualenv interpreter, and no live credentials or order mutation.
The focused run passed248 tests. Every existing REST reducer-continuity test also
runs in hybrid, covering retained-touch quotes, partial/flat transitions, cancel
failure, throttle, scheduling and shutdown boundaries. New tests cover hint-racing
cancel replacement before flat work, exact residual quantity, stale/future proof,
wrong size/direction/market/source generation, expired gate, entry refusal,
one-use admission, failed scoped proof, events during proof and during admission,
and no-fill acknowledgements that cannot clear older uncertainty. Both deliberate
reversals produced assertion failures, not harness errors. Full-suite result is
recorded below when complete.

Dedicated edge review: one serial owner/projector remains; identity pre-check
does not adopt unbound orders; durable intent precedes mutation; positive
terminal proof and idempotence are unchanged. Pending frames still veto after
limiter waits; no await separates the final checks from submission. Wrong or
malformed reducer payloads fail closed. Partial/empty snapshots cannot erase
exposure. No unknown create retry, ledger deletion, live risk migration, API
budget increase or restart is authorized by this repair. Startup/shutdown retain
their existing proof. Restart requires reconciliation of manually closed stale
ledger records and a fresh flat/clear gate. Live latency and aggregate REST
savings remain unproved until a separately authorized restart and observation.

Authenticated03:17:16Z again proved zero venue orders/positions with no trader
running. The stopped ledger still has23 working rows, three positions and one
unresolved intent; it was not edited or reconciled by the test suite.

### Final verification and review

Final unchanged shipping source/tests passed the complete2,051-test suite in
211.384s using the production virtualenv in isolated WSL. A prior run's sole
failure was the100-line BUGFIXES context budget (101 lines); the index was
compacted, the documentation checks passed, and the complete suite was rerun
successfully. Earlier2,049-test runs passed before the two additional boundary
tests; the final209-test focused run also passed. Both deliberate reversals
failed their assertions as expected. Source/test directory comparison between
the final stage and workstation is exact (excluding generated cache/egg-info).

The final diff/edge review found no remaining issue within this repair: unrelated
REST/WebSocket source paths and strategy/config files are unchanged; long/short
size15 and fractional0.04/-7.54 cases retain exact reducing quantity; another
unresolved intent, a new frame during limiter admission, failed/stale proof and
entry requests remain refused. No polling or budget increase, parallel mutation,
new credential path, loss reset or historical policy restoration. Documentation
audit and diff whitespace checks pass. Tests establish offline behavior, not a
guarantee of live latency, REST savings or absence of future429s.

### Second edge-case review

A subsequent independent pass found and corrected four boundary weaknesses in
the prepared-only implementation. Terminal cancel frames no longer share the
OPEN/NEW cadence exception: a cancel can race a fill or position update that the
currently incomplete private feed omits, so it always schedules REST proof. The
existing local cancel-confirm-replace path still performs one scoped exact-owner,
orders, positions and history proof before replacement, preserving the intended
latency improvement without trusting the terminal frame alone. An actual private
terminal-cancel race is covered by the continuity test.

If a notification arrives while scoped REST position proof is running, both the
hybrid certificate and the base REST position-confirmation cache are now refused;
the next priority pass cannot reuse a proof that straddled new evidence. Reducer
direction admission now accepts only the canonical BUY_SHORT for positive
inventory and BUY_LONG for negative inventory used by the existing order model;
legacy SELL encodings cannot use the exception. Finally, the one-use certificate
is consumed only after every later local admission callback passes, so a local
TIF/source/strategy refusal cannot strand an uncovered position by burning proof
before any request is sent. There is no await between the recheck/consume and the
network mutation.

Focused hybrid coverage increased to55 tests and passed. Four deliberate
reversals independently reproduced terminal-cancel cadence reuse, stale base
cache retention, legacy SELL admission and early certificate consumption as
assertion failures. Business policy is unchanged: size15,32 slots, four$30
settled-loss windows, pending-entry caps, market selection, pricing and exit
timers are untouched. Full-suite evidence follows the final unchanged-source run.

The restored shipping source passed the complete2,054-test production-virtualenv
suite in333.135seconds after the four reversal checks. Diff whitespace and the
100/100/80/120-line root-document budgets pass. No live credentials, venue
mutation, deployment, process start or risk migration was part of verification.
Reviewed source commit `c10b6dfc3` is pushed on `main`. Post-push parity proves
the workstation clean and identical to GitHub. The WSL checkout/executable remain
intentionally behind at `6d8cc336`/`a46f5f995`; peer PC is unavailable/pending.
This is prepared source, not a deployed or loaded runtime claim.

Authenticated03:35:25.925085Z again proves zero venue orders and positions; the
former runner/supervisor are absent. One unresolved intent/23 working rows/three
positions remain only in the stopped stale ledger, which was not modified.
This repair is prepared/pushed only. Production still has `a46f5f995` executable
content; no deploy/restart or resumed WS measurement. A later authorized rollout
must resolve stale ledger state through normal proof and repeat every start gate.

## September 23 live activation

The reviewed release was subsequently deployed as `85f29c83b`. Normal stopped
reconciliation and a fresh authenticated release gate proved zero venue orders,
positions, unresolved intents, unknown orders and ledger exposure before start.
The laptop WSL supervisor/runner started at 04:31Z in hybrid mode with size15 and
32 slots. The 00:00–06:00 ET risk row retained settled loss $0.7554, hard $30,
cap30 and no hold; open orders and positions remain excluded.

The first selection consumed 54 public-WebSocket books with zero REST book
fallback and found 38 viable markets. At 04:41:12Z authenticated evidence showed
31 venue orders across23 markets and three positions. Each position had exactly
one correctly directed reducer for its exact remaining quantity; unresolved
intents and unknown orders were zero. Private hybrid events supplied order and
position observations, while REST remained the final authority.

One `portfolio/activities` execution-history read received Cloudflare HTTP429 at
04:33:54Z. Its enclosing API window averaged3.476 requests/s, below the unchanged
4/s host governor. The bot recovered and continued; the following observed API
windows reported zero429s. An earlier attempted start stopped safely on a public
metadata HTTP403. Bounded read-only probes later returned HTTP200, the release
gate was repeated, and the successful restart has not repeated that403.

The redundant order-proof optimization is active. In the latest reported minute
it proved471 unchanged orders from complete bulk snapshots and required48 exact
order reads; two changed rows correctly fell back to exact proof. This is direct
evidence of reduced redundant per-order reads, not a promise of lower aggregate
traffic or immunity from venue endpoint-specific limits. No pricing, selection,
position size, pending cap, exit timer or risk rule changed.

## September 23 rollback comparison and prepared follow-up

The operator rolled production back to explicit REST and saved the hybrid source
for offline repair. Production remains on the REST rollback; none of the work in
this section was deployed or restarted.

Comparable live log intervals do not support the claim that hybrid alone caused
HTTP429. The04:31–04:53Z hybrid interval covered1,291.0 seconds:4,419 attempts,
3,971 reads,448 mutations,29 failures and four429s (3.423 attempts/s). The
05:11–05:33Z REST interval covered1,297.2 seconds:4,321 attempts,4,040 reads,
281 mutations,32 failures and four429s (3.331 attempts/s). Conditions were not
controlled, so this cannot prove equal endpoint risk. It does prove that rollback
did not eliminate429s. The material hybrid difference was mutation volume, not a
large aggregate request-rate change.

Review found amplification in event classification. The live hybrid sample had
369 order frames and18 hints;194 were exact no-fill ACKs, leaving about175 order
events plus the hints able to restart immediate broad REST proof. Routine terminal
waves and optional disconnects therefore competed with normal REST cadence even
though REST remained authoritative.

The prepared follow-up narrows private WebSocket assistance to acceleration:

- fills/partial fills, position updates, malformed data, unknown event types,
  changed cumulative quantity, or changed order identity/economics wake complete
  REST proof immediately;
- exact NEW/no-fill ACKs retain the existing zero-extra-proof path;
- recognized terminal/no-fill order events project through the durable ledger,
  block new creates, and coalesce into the ordinary bounded REST proof cadence;
- snapshots are non-authoritative cadence hints;
- a listener gap falls back to normal REST timing without revoking a still-fresh
  REST certificate, while malformed/schema/integrity/REST failures remain strict;
- routine market-local evidence invalidates only that market's scoped reducer and
  exit caches, so unrelated evidence cannot strand a separately proved reducer.

The unchanged authority model remains: REST seeds startup, balance, positions,
history, absence and complete account proof. Private events never prove flatness
or balance and never release global entry authority. Pending/unproved nontrivial
events bar POST creates; cancellation remains available. Reducer exceptions remain
single-use, exact direction/quantity/owner/scope/generation/age certificates.

Focused hybrid coverage now has59 passing tests. The broader hybrid/REST account
regression set passed235 tests. New cases prove terminal-wave coalescing, immediate
fill wakeup, changed-price fail-closed handling and unrelated reducer-proof
isolation. A deliberate reversal that made every terminal event reset the REST
clock failed the cadence assertion exactly, then the repaired source was restored.
The final unchanged source passed the complete2,058-test production-virtualenv
suite in319.863 seconds. Documentation audit and diff whitespace checks pass.
This remains an isolated prepared branch, not live-performance acceptance or
deployment authority. No business/risk/selection/price/size/cap rule changed.

The subsequent [scoped performance review](HYBRID_REST_PERFORMANCE_REVIEW_2026-09-23.md)
adds a catalog-checkpoint repair and controlled REST/hybrid workload comparisons.
It does not attribute the earlier mutation difference to proof amplification.
