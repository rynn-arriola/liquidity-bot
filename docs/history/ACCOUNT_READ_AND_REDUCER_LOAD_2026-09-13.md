# Account-read backpressure and reducer load review

September13 ET / September14 UTC, against Windows/GitHub `33711fb6e` and
loaded runtime `87ad2df28`. Initially authorized fixes/review only; the operator
subsequently authorized push, deploy and restart after completion. No source
selection, risk, cadence or credentials change is authorized.

## Evidence and scope

- Pre-work parity passed; offline PC skipped. Runtime supervisor346475 and
  runner346508 remained live with `auto 10 32 rest`.
- Live23:54:03/23:55:08/23:56:25Z windows recorded respectively222/222/269
  attempts over65.0/64.9/77.1 seconds,3.415/3.420/3.490 requests/s, and2/2/1
  account-read429s. Affected categories were balance, execution and cash history.
  These are actual HTTP errors, not order-latency protection rejects.
- At00:04:36Z the old runner still made212 requests in63.8s (3.321/s),
  including44 history,36 open-order,49 exact-order,29 position,21 balance,
  seven cash-history reads and16 mutations. That window had zero429s and two
  exact-order404s. Recovery in one window does not disprove earlier throttling.
- Nankaw and Valsav reducers repeatedly canceled/reposted, including repeated
  prices0.51 and0.87. Cancellation-time books were not retained in those lines,
  so do not attribute every replacement to the reproduced code defect.
- At00:21:45Z authenticated read-only state was28 orders:23 flat-entry orders,
  five position-market orders, six positions, zero unresolved/unknown state.
  The ledger had29 orders during the moving read; this is not an atomic match
  or proof that every position had continuous reducer coverage.
- The official Retail rate page still specifies20 authenticated requests/s/key
  and20 public requests/s/IP, confirmed429 backoff and streamed account data.
  The local4/s ceiling is unchanged; the exact venue/edge rule behind rejection
  at these averages remains unproven. Changelog/status were reopened; no
  account recovery or private-stream repair is inferred from public status.

## Bigger-picture decision

Keep one engine, ledger writer, mutation executor and selected account source.
Public full books already stream. The account snapshot reader already bounds
one collection in flight; moving reconciliation wholesale into a background
worker would introduce a second writer across awaits. General caching would
also risk reusing either side of the intentional order/cash proof brackets.

The chosen changes remove identified redundant work at its source. They do not
reduce the number of markets, weaken complete snapshots, shorten history depth,
slow the configured active poll or add speculative fill arithmetic.

### 1. Retain the reducer that already owns the touch

The real planner first improves a profitable long exit from0.52 to0.51. When
that owned order appears as the0.51 offer, the previous planner asks for0.50,
cancels0.51, then sees the restored0.52 book and reposts0.51. The short side has
the symmetric defect. Preserve an exact remaining-quantity reducer at the
current touch instead of improving against itself. An already matching improved
order retains prior preference; duplicate cleanup remains. Genuine better touch,
changed quantity, building-order cancellation and all exit deadlines still act.

### 2. Endpoint-scoped account-read recovery

Balance, positions, activities, open orders and exact-order reads get distinct
host-shared circuits. Cash/trade history share activities because it is one
wire endpoint. All requests still use the existing aggregate pacing/priority.

A confirmed429 ends that call immediately rather than doing three inline
attempts. Backoff grows2/4/8/16/30 seconds and honors a larger Retry-After up to
the existing60-second bound. Afterward one caller holds a20-second recovery
lease, across cooperating processes. Failed/canceled probes remain bounded;
older successes cannot erase newer failures. Other endpoints and mutation
admission are not cooled by this read circuit. Unknown/ambiguous mutations are
still never retried, and unavailable required account proof cannot authorize
trading. This reduces amplification; it cannot make a throttled endpoint serve
data or guarantee exits during an outage.

Typed exact-order backpressure now survives the collector after workers join
and successful positive results are projected. Ownership/integrity errors take
precedence. A throttled decision proof yields the sweep before market-specific
recovery can cancel reducers/retire owners. Normal recovery and shutdown remain.
Telemetry reports endpoint cooldowns without an additional API request.

### 3. Share immediate cancel confirmation, not stale account snapshots

A complete exact-owner cancel/position/history confirmation may serve the same
owner's immediate replacement consumer once. Require unchanged ledger write
version, active scope, source/generation, original proof start age<=30 seconds,
completion age<1 second, existing ready gate, nonzero position, zero owner
working orders and no account ambiguity. Any write, failure, stop, switch,
expiry or wrong owner invalidates reuse. Never renew the global poll clock,
entry gate or equity mark. Equity checks and network-boundary admission still
run. The separate previously reviewed hard-IOC proof path remains unchanged.

## Verification

- Focused229-test run passed, including adapter, manual source handoff and
  reducer-continuity tests.
- Real planner replay for both signs retains the first at-touch reducer over
  19 repeated observations. Reversing the fix produces assertion failures.
- The30-flat-entry fixture confirms cancel/replacement uses one scheduling
  account refresh instead of two, with two equity checks and unchanged global
  poll timestamp. This is a deterministic call-count result, not measured live
  request or latency improvement.
- Eight endpoint tests cover typed/raw429, cash/trade shared scope,32 competing
  consumers/one probe, cross-process exclusion, old success after newer failure,
  failed/canceled probes, malformed clocks and bounded Retry-After.
- Six deliberate in-memory reversals all failed assertions: self-chase retention,
  endpoint admission, inline retry suppression, immediate proof sharing,
  decision-sweep containment and typed collector propagation. Original methods
  restored after each case; no live process or source file was reversed.
- Initial full suite:1666 tests, one outdated mock-signature failure after the
  shared admission helper gained endpoint keyword arguments. The mock now
  accepts keywords; its concurrency assertions are unchanged.
- Final complete native suite:1,671 tests passed in233.401 seconds. The later
  strengthened30-flat-entry confirmation fixture also passed independently;
  runtime source was unchanged after the full-suite copy.

## Dedicated edge-case and final review

Success/failure, empty/full32 capacity, long/short/partial reducers, wrong size,
duplicate reducers, real price moves, stale/unknown proof, deadline cancellation,
fill during cancel, manual flatten during confirmation, stop, source generation,
ownership conflicts, expired/canceled leases, older responses, maintenance,
HARD, same-day limits, recovery and shutdown were traced against the retained
regressions. No fee/P&L, quote-size, risk-limit, clock, source-auto-switch or
position-cap policy changed. Position-market orders remain outside30 flat entries.

Per-endpoint backoff trades unsuccessful fast retries for bounded later probes;
it cannot preserve exit speed when required venue data is unavailable. Local
deferral is not flatness, a terminal cancellation, a venue outage declaration or
permission to restart. New shared-state keys are additive; old binaries ignore
them, so rollback restores the old retry weakness and needs reviewed deployment.
No ledger schema migration or cache persists confirmation authority across restart.

## Release boundary

Prepared; release is now authorized after final review. Push does not deploy.
Deployment still needs reviewed runtime installation, hashes/native tests,
safe stop/fresh known-state gates and natural live observation. Compare matching load/position cohorts,
not flat versus busy averages. Do not promise WebSocket-speed parity, full32
capacity or no further429s before that acceptance.

## Authorized release attempt: stopped, not flat

After the operator authorized push/deploy/restart, the existing stop script
terminated supervisor346475 and signaled runner346508. The runner ended at
00:30:40.825942Z with `operator_sigterm`, `flat=false`. Its existing shutdown
policy parks pregame positions with resting reducing orders; it is not a new
account-load change. At00:31:18.442482Z authenticated venue/ledger reads agreed:
three positions, three position-market orders, zero flat entries, unresolved
intents or unknown orders. The explicit flat/clear gate refused restart.

The operator chose to close manually after being offered the existing
price-capped procedure, manual closure or leaving exits resting. No shutdown-policy
override, manual ledger repair, deployment or restart has occurred. Remaining
owners in shutdown logs: Timder/Taiich(-10), Arusag/Jinlu(+2), Pesden/Binole(-10).
Amounts are the stopped log observations, not promises of unchanged exposure.

## Deployment and restart

The operator subsequently confirmed manual closure and requested restart.
At00:33:03.700782Z the venue was order/position-free; the stopped ledger still
held three historical working orders/positions. The reviewed stopped prestart
routine acquired exclusive ownership, proved all three exact orders terminal,
then ran normal reconciliation with venue mutations disabled. It finished
flat/clear with zero venue mutations; no inferred cancellation or manual row
repair was used.

Commit `2a867a5819ab41175e9f4af4808fed0031322371` was pushed, fast-forwarded
into the clean runtime, and all193 tracked runtime/test/config/script file
hashes matched the reviewed commit and Windows. The complete production suite
passed1,671 tests in156.394 seconds. `DEPLOYED_COMMIT` was updated only after
verification. The00:36:47.151632Z start gate proved zero venue/ledger orders,
positions, unresolved intents or unknown orders. Supervisor693287/runner693316
started with `auto 10 32 rest`. No risk, source, size, cadence or cap changed.

Post-start parity passed Windows/GitHub/WSL/marker; its online recovery snapshot
was captured00:37:06.846957Z,402788352 bytes, hash prefix3585fbc6ade9. Offline
PC skipped. Startup selection streamed87/87 books and its final32/32 with zero
REST fallbacks. Normal entry placements began; that alone is not sustained
exit/recovery or load acceptance.

## Five-minute natural startup acceptance

At00:41:52.527473Z the authenticated snapshot proved30 flat pending entries,
zero positions, zero unresolved intents and zero unknown orders. Supervisor
693287 and runner693316 were up5m05s/5m04s with the intended command.

Four complete API windows through00:41:21Z contained725 attempts, zero429s,
zero lane/endpoint cooldown and13 `order_retrieve@404` responses. Per-window
rates were3.114,2.597,2.812 and1.869 requests/s. The last window had113 calls
in60.5 seconds, two mutations, zero failures and0.459s maximum admission wait.
No REST-book category was present. These startup/flat cohorts are not comparable
proof of savings over the earlier busy-position cohort.

Four00:41:02Z incentive-refresh checks reported unavailable pregame state;
normal entry guards/recovery remained binding. Do not describe the run as
error-free. No natural position transition, passive reducer or hard IOC occurred
in this acceptance interval. Reducer continuity, exact confirmation reuse,
recovery-probe behavior under real429s and sustained32-owner capacity remain
natural-observation items, not induced live tests or reasons to weaken guards.
