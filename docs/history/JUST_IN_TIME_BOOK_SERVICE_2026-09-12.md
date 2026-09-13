# September 12: normal public-book request coalescing

## Scope and stopped state

The operator approved fixing normal request demand before restarting. This
continues the [public-book 429 containment](PUBLIC_BOOK_BACKOFF_2026-09-12.md),
which is pushed but not deployed. Deployment/restart confirmation was requested
separately; no restart or venue mutation has been performed in this repair.
At 22:27:06-22:27:10Z, authenticated complete reads returned zero orders and
zero positions. The old supervisor/runner were absent. The read-only ledger
still held one unresolved intent and zero unknown orders. Exact positive
terminal reconciliation and fresh whole-account proof remain startup gates.

## Proven local mechanism and repair

The run loop serviced quiet books across the scheduled slate, drained streams,
performed additional account/selection work and only then made owner decisions.
At 32 slots, serial book requests can themselves exceed the five-second book
age limit. Earlier successfully refreshed books therefore need another read
in the existing stale-book recovery. A book-only 429 circuit prevents retry
amplification after throttling; it does not remove these healthy redundant reads.

Normal bulk service now retains lifecycle/contract/economic work but defers
quiet-book REST reads. The same guarded decision sweep services the exact
owner after account/equity proof and before its plan. Its existing service
timers, failure accounting, retirement isolation and fresh economics are reused.
The REST urgent-position phase uses the same ordering. No parallel executor,
shared snapshot authority, new source switch or polling-rate increase is added.

Important exceptions and boundaries:

- An economic replacement dwell is already a book consumer before the decision
  sweep, so bulk service retains its ordinary refresh, including before the
  deadline that may cross during other service. Old economic refusal
  cannot be used to release an owner that the new book makes viable.
- The fast entry-pause cancellation pass acquires no additional price read.
- Unknown-intent and backed-off owners do not fetch a decision book. Held
  positions still use their existing priority/proof/exit path, not the entry cap.
- Stop during service returns before planning. A position-only pass that
  discovers flatness during book service cannot become a new entry pass.
- Fresh streamed books retain their original receipt, and can suppress REST.
  None/future/stale receipts remain untrusted; quiet checks remain three seconds
  and planning age remains at most five seconds. Deferred reads renew nothing.
- Inactive/closed waiting owners keep their original early return. Startup,
  shutdown, selection validation and recovery keep their explicit book reads.

## Test evidence

The initial affected commissioning/REST group passed 477 tests. The new
deterministic 32-owner regression simulates three seconds of account proof per
owner and half a second per book response. It proves zero ordinary bulk book
reads, exactly 32 decision reads, fresh receipts and computed economics at each
decision, in book/decision order. It does not measure live latency or predict
a percentage reduction in production traffic.

Further focused cases cover fresh stream delivery during account proof,
unknown/backoff/stop, stop during service, economic replacement dwell, pause
cancellation, missing/future and 2.999/3/5.001-second receipt boundaries,
scoped service failure and terminal ownership invariant propagation. The
11-test REST exit scheduling group passed; its flat-during-service test now
also covers flatness during the moved book read.

Two deliberate reversals proved the main regression detects removed repairs:
disabling bulk deferral failed with 32 reads where zero were expected; removing
the decision refresh failed the fresh-book assertion. Both were restored.
The first complete run caught two variants of the same position-exit regression:
the initial implementation repeated contract service at the decision boundary,
overwriting the fractional-lot fixture metadata. The correction moves only the
book/economic helper, not contract reads. Both unchanged position cases now pass
in the 145-test account-source/position/exit group. Eight new focused regressions
pass, including fast pause and receipt boundaries. Final complete-suite and
documentation results are recorded below when finished.

The next 1,565-test run had only an outdated mock-call assertion: the HARD
pause test expected the old call without the new explicit `service_books=False`.
The assertion now verifies that cancellation bypasses extra book service; no
HARD implementation or limit was changed. All 12 safety tests pass. A final
complete run follows that fixture correction.

Final result: all **1,565 tests passed in 217.187 seconds** using the production
WSL virtual environment against the reviewed Windows source tree. Documentation
audit passed (127 Markdown files, 961 mandatory lines, zero issues), and final
diff/edge-case review found no remaining in-scope regression. No test failure
was waived. Latest 22:42:51-22:42:55Z authenticated reads again proved zero
orders/positions; the old processes remain absent, one ledger intent remains
unresolved and zero orders are unknown. No ledger or venue mutation occurred.
This is prepared-code acceptance, not deployed-runtime or natural-load proof.

## Dedicated edge-case and release audit

- Success/load: one fresh read per ordinary owner in the 32-slot slow-proof
  simulation; no invented receipt or shared cross-market cache. Existing
  request/category telemetry can measure the natural change after release.
- Empty/full/boundaries: missing book obtains an actual read; exact quiet and
  stale/future boundaries are tested. Unknown owners and inactive waits remain
  fail-closed. The existing full suite retains empty/thin-book and full-cap tests.
- Timing/ownership: proofs precede the moved read; original downstream book,
  account, position and network-admission validation remain intact. No absent
  order becomes terminal and no unknown create is resent. Stop/flat checks are
  repeated after the added await. Private projection stays serial.
- Failures/recovery: existing service catches and retirement bounds are reused;
  unowned/invariant failures stay terminal. The separately reviewed shared
  book429 circuit still blocks fan-out without delaying account/mutation lanes.
- Risk/cap: size 10 / 32, $90 HARD and durable ET tiers are unchanged; manual
  REST's 30 pending-entry ceiling excludes position orders/reducers/exits.
  Price rules, reward ranking, pregame guard, inventory clocks and IOC bounds
  are unchanged. No added REST read precedes fast pause cancellations.
- Shutdown/restart/rollback: explicit shutdown fresh-book work is unchanged.
  The patch has no schema/config migration and does not alter the production
  ledger. Rolling back source restores extra reads, not an older ledger or
  risk baseline. Restart needs current terminal/flat/clear evidence and review
  of this patch together with the already pushed book429 circuit.
- Limits: no live stress test, key/IP change or limit increase. This fixes local
  waste, not Polymarket's undocumented matched Cloudflare rule. Sustained live
  request/exit/reward performance and the external private stream remain unproven.

## Authorized deployment and restart

The operator subsequently said **Restart once all clear**. Read-only proof at
23:06:43Z confirmed all 21 retained ledger orders CANCELED, including the one
acknowledged PLACE intent. Parity captured and verified the stopped ledger at
23:07:11Z (385,630,208 bytes, SHA-256 prefix `713ce12c114b`). Normal stopped
reconciliation, with venue mutation entry points disabled, then cleared the
historical working orders/intent from positive exact venue proof. No SQL repair,
create, cancel or position-close request was used.

The combined reviewed change from WSL `6b17c5416` to `4a166336a` was fast-forwarded
through a local git bundle after its GitHub push. All 187 normalized
source/test/script/web/config hashes matched. The exact deployed native WSL
suite passed **1,565 tests in 154.420 seconds**; the deployment marker now names
`4a166336a340f99003c6e4d5810283f02e363eda`. No source change was added during
release. The 23:11:54.924582Z fresh authenticated gate proved zero orders,
positions, unresolved intents, unknown orders, ledger working orders and ledger
positions. The existing launcher started size 10 / 32 in REST mode at about
23:12:05Z: supervisor 1789434, runner 1789455. Risk and policy files are unchanged.
Startup/live acceptance follows; the prestart gate is not later exposure proof.

### Startup acceptance: public-book throttling persists

The process remained alive, but preflight repeatedly deferred discovery before
placing any order. Logs at 23:12:37, 23:13:22, 23:14:02 and later recorded
`component=startup delay=10.0s requests_sent=0`. That local message describes
the deferred call, not zero requests across the entire scan.

Read-only observation of the host limiter proved actual fresh public-book 429s:
`book_last_429_at` advanced to 23:14:41.597610Z and 23:15:22.794350Z with
10-second book-only cooldowns. A successful recovery probe cleared the circuit
between them, then a subsequent book request was throttled again. Safety-lane
cooldown did not advance with these book failures. This demonstrates circuit
containment, not restored venue access or sustainable quoting. No manual API
stress, key/IP rotation, limiter-state edit or strategy bypass was performed.

The current [changelog](https://docs.polymarket.us/changelog) still ends with the
September 11 maintenance announcement; the [official live status](https://status.polymarketexchange.com/)
reported All Systems Operational at the 23:16Z check. Those public summaries
cannot override actual endpoint failures or prove an account/IP exemption.
The exact venue rule/threshold remains unknown. No regular API-budget window
has completed because preflight has not entered the trading loop; no live
performance reduction or exit acceptance can be claimed yet.

After 5:09 of process uptime, the 23:17:10-23:17:14Z authenticated account
check proved **zero orders, zero positions, zero unresolved intents and zero
unknown orders**. The run suffix contained seven startup backpressure records
and no actions, position transitions or normal API-budget windows. Both exact
processes remained alive. Leave the existing safe preflight retry active; no
manual restart loop or risk/freshness bypass was used. Deployed-code and
retry-containment acceptance pass, but quoting and live performance/exit
acceptance remain blocked by persistent public-book throttling.

### Later position refusal and renewed authorized restart

The first run subsequently observed `caoc-f434ee98d894e82d` +116.9400 at
23:23:58Z. Exact market lookup repeatedly returned404, and the supervisor
stopped relaunching after three fast process deaths. Authenticated23:37Z reads
confirmed that position and zero orders. One paced book200 (cached HIT),
metadata200 and balance200 did not prove sustained recovery. No code change,
position-close request or restart occurred during that diagnostic check.

After the operator again requested restart, complete authenticated reads at
September13 02:20:04Z showed zero venue orders/positions and one stale ledger
position. Stopped the attention-mode supervisor, verified a stopped-ledger
backup at02:20:32Z (387100672 bytes, SHA256 prefix `fa1c71da422c`), then ran
normal stopped REST reconciliation with all venue mutation entry points
disabled. It cleared the stale position and proved ledger flat/clear. This
does not attribute the original position or explain its external disappearance.

All187 source/test/script/web/config hashes still matched; the unchanged native
1565-test acceptance was reused. Fresh02:21:09Z proof found zero venue/ledger
orders, positions, unresolved intents and unknown orders. The existing launcher
started unchanged size10/32 REST around02:21:10Z, supervisor2085653 and
runner2085674. At02:22:20Z both remained alive, but two startup deferrals and
no actions were logged. Shared limiter state proved a fresh book429 at
02:22:07.928037Z, with a ten-second endpoint cooldown and no new safety-lane
cooldown. The position blocker cleared, but public-book access still prevents
useful quoting. No source/config/risk/rate change, raw SQL repair, limiter-state
reset or API mutation accompanied prestart reconciliation.

At 02:23:25–02:23:29Z, independent authenticated acceptance again confirmed
zero orders, zero positions, zero unresolved intents and zero unknown orders.
Both processes remained alive after 2:19. The run suffix had four startup
deferrals and no actions or ordinary API-budget windows. The bot is running
but not trading; this is not sustained rate-limit recovery acceptance.

### Operator stop after continued book throttling

The operator then said "Just stop the bot." The normal stop script stopped
supervisor 2085653 and sent SIGTERM to runner 2085674. It completed without
force-kill; the final log reports `operator stopped during API admission
backpressure`. Both processes are absent. Independent complete authenticated
reads at September 13 02:27:09–02:27:13Z confirm zero orders, zero positions,
zero unresolved intents and zero unknown orders. Startup deferrals continued
through 02:26:02Z; no trading action was recorded in this run.

The operator also asked where the persistent429 originates. Prior raw captured
evidence identifies HTTP429 / Cloudflare1015 on public gateway
`GET /v1/markets/<slug>/book`, not the private account WebSocket. Reopened
[Cloudflare1015 guidance](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1015/)
warns repeated requests in a short period may extend the restriction. This is
a possible explanation for persistence, not proof of this account/IP's matched
rule, reset duration or a permanent account ban. No additional venue probe,
code/config/risk change or restart was performed. Leave stopped.
