# Whole-bot review repairs — September 12, 2026

## Scope and operational boundary

The operator authorized the confirmed fixes from the
[whole-bot review](WHOLE_BOT_REVIEW_2026-09-12.md), explicitly without restarting.
This patch prepares R1–R5 only; it neither deploys source nor changes the live
process. The separate background account-watch / interruptible catalog design
is not implemented here and no latency improvement is claimed for it.

The laptop runner remains `649372`, supervisor `649355`, started 08:02:00Z,
loaded marker `b826ff11ee739e61704ddba80533e089be84b111`, REST, size 10 / 32.
Authenticated shared-report-lane reads at **13:39:45Z** showed **24 orders and
three nonzero positions**: 22 orders in flat markets and two in position markets.
This is a sequential account snapshot, not proof that every position has a
working reducer or a live acceptance test of these unloaded repairs. An initial
read-only diagnostic used the wrong local position attribute; it made no
mutation and was corrected to `net_qty` before the reported snapshot.

No configuration, database schema, quote size, reward ranking, entry-quality
threshold, exit policy, $90 HARD policy, loss tiers, ET schedule or source-switch
policy changed. REST still permits at most 30 flat pending entries, subject to
lower existing caps; positioned-market orders, reducers and exits stay excluded.
No automatic WebSocket/REST promotion was introduced.

## Repairs

| Review item | Repair and safety boundary |
|---|---|
| R1 — suppressed fill conflict | REST reconciliation propagates genuine `LedgerConflict` in both modes and revokes proof. The ledger's existing identical replay and outcome enrichment remain valid; optional history does not license contradictory fill identity. |
| R2 — manufactured equity freshness | The risk mark retains the oldest bracket observation time. Publication still requires complete cash proof; a failed bracket leaves the previous mark unchanged. A successful but slow bracket cannot grant another 15 seconds of entry authority. |
| R3 — stale final-send authority | After governor admission and before network submission, flat entries recheck known position, stop/pause/risk state, session proof, market/event/book eligibility and effective cap. The current SUBMITTING intent counts once. Local refusal or validator failure is known NOT SENT, not ambiguous. Existing source-generation, event, REST-proof and exact reducer checks remain in force. |
| R4 — lost received private evidence | Teardown settles reader tasks and projects held private evidence before draining queued successors. One-at-a-time bounded drain retains a valid prefix before an error. Market failure cannot suppress a received fill; integrity errors still attempt normal safe shutdown. Recovery already in progress does not queue a redundant rebind. |
| R5 — misleading legacy accounting | The old accounting command labels its incomplete residual an unattributed remainder, explicitly not trading P&L or settlement proof. Paid-status reward changes are not called actual cash credits; resolution estimates are no longer falsely reconciled to that remainder. Canonical daily reports are unchanged. |

The canonical API reference now accurately describes permanent explicit account
modes, the already-loaded actual-live event guard, observe-only fee telemetry
and per-mark cash-proof demand. These are documentation corrections to existing
behavior, not newly introduced trading rules.

## Test and reversal evidence

All execution tests use fake clients and an isolated native WSL source copy,
without live credentials, databases or runtime state. The installed production
Python environment supplies dependencies only. The full suite exercises the
shared engine, REST and WebSocket paths, source switching, exact exit ownership,
reporting, maintenance, HARD, caps and deployment tooling.

- Added **19 regression tests** in `tests/test_review_boundaries.py`, including
  parameterized cross-mode and failure/boundary cases.
- Updated the old freshness expectation to retain data age. Two existing tests
  now isolate their intended layers: a genuinely sent ambiguous create uses a
  fresh fixture clock, and the REST-proof expiry test bypasses the new earlier
  validator solely in its fake gateway. Assertions were not removed.
- Deliberately restored the five original defective paths: six selected tests
  produced **11 expected failures** across subcases. All reversals were restored.
- Reversing the final recovery refinement produced **two more expected
  failures**, private-only and simultaneous silence. It was restored.
- Focused final repair module: **19 tests passed in 2.896 seconds**.
- The pre-refinement full suite passed **1,487 tests in 136.365 seconds**.
- Final exact-source complete suite: **1,488 tests passed in 139.848 seconds**.

## Dedicated final edge-case audit

| Dimension | Evidence and outcome |
|---|---|
| Success, replay and conflict | Both modes reconcile normal fixtures; immutable conflicting fill is rejected. Existing ledger replay/enrichment tests remain in the complete suite. |
| Timing and boundaries | Tests advance admission beyond equity/book age, tighten caps, request stop, pause risk and change position/source generation. Current cap-one reservation still permits exactly one send. No extra API call is added by the final check. |
| Partial and unknown state | Failed cash proof retains the old mark. Local pre-send failure is FAILED; genuinely sent-but-unanswered mutations retain existing ambiguity handling. No absent order is treated as canceled. |
| Position and ownership | A held real fill fixture is projected before its following position update, persisted once and resolves its original intent without another create/cancel. Reducers bypass the flat-entry validator; exact quantity, direction and terminal-proof paths are unchanged. |
| Empty/full queue and load | Empty disabled REST drains accept the same limit interface. Production queues are bounded; projection drains at most 16,384 batches and raises on overflow. No REST request, polling-rate increase, background ledger writer or mutation retry is introduced. |
| Recovery and shutdown | Market-task failure still projects private evidence and revokes entry proof. Shutdown attempts its existing cancel/exit procedure even after a teardown integrity error. Private-only/both-stream liveness recovery does not schedule a duplicate reconnect. |
| Source return and switching | Cross-mode regressions cover shared fixes; existing explicit handoff/generation tests remain. Returned order frames/Pongs cannot enable WebSocket trading without complete required proof. No switch was attempted against the venue. |
| Restart and rollback | No schema migration, new persisted latch or configuration dependency. A future authorized restart still requires fresh flat/clear ownership gates and exact deployed-source tests. Rolling back restores these known defects and requires review; no live rollback was performed. |
| Capacity and policy drift | Existing positioned-market/reservation exclusions are retained. The current entry's reservation is not counted twice; lower stop-loss/time caps still win. All size, risk, reward and exit settings remain unchanged. |
| Reporting | Deposit-like, withdrawal-like, zero and fractional mark changes cannot be labeled derived settlements. Resolution-priced estimates are independent, not a fabricated reconciliation. No cash history or historical P&L was rewritten. |

Final source/test diff review checked callback wiring through both gateway
wrappers, ledger single-writer ownership, teardown callers, reducer isolation,
no hidden source promotion, no new API load or secrets, and all source reversals
restored. Root handoffs preserve the distinction between prepared and loaded.

## Limitations and remaining work

These reproduced bugs are not proven causes of any specific live loss. The
final entry check can use only currently projected evidence; it cannot know an
unpublished exchange fill or make a network call atomic with venue state.
Honest data age may defer more entries during slow cash scans; reducers retain
their existing independent exact-proof path. It does not guarantee an exit when
the venue is unavailable, closed or has no executable depth.

The separately proposed single-flight account watch, pure collector/projection
split and interruptible catalog work remain next-phase work. Dedicated future
liquidity-credit activity shapes are a compatibility gap, not a demonstrated
live response; speculative cash classification was not added. Natural restored
WebSocket acceptance and real fault-path acceptance remain pending and must not
be induced.

Deployment/restart is deliberately withheld. After the code-only push, final
parity must explicitly retain the older live runtime and report deployment
pending; an unreachable peer workstation remains pending, not verified current.
