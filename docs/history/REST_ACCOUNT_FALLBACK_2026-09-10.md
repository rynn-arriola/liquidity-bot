# Temporary REST account source — September 10, 2026

## Authority and current scope

The operator requested preserving the current reviewed version and building a
temporary alternative while private account WebSocket messages are missing.
They additionally required accuracy comparable to the existing implementation.
This authorizes implementation, not a restart, live trial, capacity change,
ledger reset or position intervention. Bot remains stopped. At September 11
00:36:28Z authenticated reads showed zero orders and zero positions; the earlier
278.8900 position is no longer present. This task did not close it.
Later evidence supersedes that flat reading: at 00:47:18Z the account had zero
orders and `+410.3000` in `caoc-f4354f0027dc3eba`. Ownership/origin is unknown;
this task did not create, modify or close it. No restart may use the old gate.

`v1.2.0` preserves `a3defc0148baf0c95e09ae0073e811e8be82af91`, before any
fallback code. It is a reviewed rollback reference, not proof that the private
feed works. Source corresponds to installed `3625927fb`, already tested at
1,228/1,228 in both environments. Other tags remain unchanged.

## Implementation and limits

- Default remains `websocket`, including genuine private-snapshot startup and
  transport checks. No automatic switch is made on an outage.
- Explicit `--account-state-mode rest-fallback` selects a separate runner
  subclass using the same planner, ledger, risk, event guard, maintenance,
  reporting, exit and shutdown implementation. The private socket is disabled;
  its idle scheduler placeholder emits no events or synthetic heartbeats.
- The fallback checks every owned working/ambiguous order individually, complete
  open orders, exact decimal positions and execution history. Scoped recovery
  cannot omit those individual order reads in this mode. An absent order is
  still unknown, never cancellation proof; uncertain creates are not retried.
- Execution-history failure or conflict is blocking in fallback mode. Ordinary
  WebSocket mode retains its existing optional history-backfill behavior.
- A failed/cancelled proof revokes account authority immediately. The complete
  read interval counts against the existing 30-second state bound; a late
  successful response cannot turn an old poll into fresh proof. Creates and
  exact reducers recheck this proof after API admission, before network I/O.
  Owned cancels remain available when the proof has expired.
- After slow REST work, consume real queued public-book updates immediately
  before planning. An older book held by the scheduler cannot overwrite a newer
  one; an empty queue never refreshes an old timestamp. A public-source failure
  revokes the gate and requests the existing complete rebind path.
- Decision polls are due five seconds after the previous completed proof;
  the inherited idle account task is due every ten seconds. These are targets,
  not fill-latency guarantees. A serial read or other scheduler work adds delay.
  Every REST call uses the existing shared rate governor. Forty working orders
  require at least forty individual reads plus snapshots/history, so full-width
  sustainable throughput and natural fill detection are **not yet accepted**.
- Loss calculations, deposit/withdrawal adjustment, size, pending-order ceilings,
  entry ranking, 70% quality filter, actual-live guard and $90 HARD at size 10 /
  32 are unchanged. A source change cannot reset daily loss state or ownership.

REST cannot guarantee the private stream's timeliness or detect a venue serving
internally consistent but silently stale data. Identical arithmetic is not
identical real-time visibility. This mode must not be advertised as equally
safe or production-equivalent without live acceptance. Failed proof pauses
new risk; it cannot make an unavailable cancel/exit API execute an order.

Official Retail references reviewed:
[open orders](https://docs.polymarket.us/api-reference/orders/get-open-orders),
[positions](https://docs.polymarket.us/api-reference/portfolio/get-user-positions),
and the canonical [API reference](../POLYMARKET_API_REFERENCE.md).

## Operation and rollback

After separate reviewed-deployment and restart authority, the existing launcher
accepts an explicit fourth argument, for example
`bash scripts/start_commissioning.sh auto 10 32 rest-fallback`.
The supervisor keeps that mode across its authorized crash-recovery relaunches.
Omitting the fourth argument selects `websocket`; there is no hidden persisted
mode file or hot switch. Normal exclusive-owner and fresh account-state startup
checks still apply. Do not execute this example merely to test parsing.

To return to the normal source, use a separately authorized stop/restart with
the `websocket` mode after genuine private-snapshot readiness is verified.
Code rollback follows [stable release procedure](../STABLE_RELEASE.md), never
a destructive reset or replacement of the execution ledger.

## Verification

- Focused tests exercise source selection, real REST-only fake-client startup
  and placement, exact fill/cancel/terminal-proof/reducer flow, absent/unknown
  create/cancel ownership, mandatory/conflicting history, failed/cancelled/slow
  snapshots, clock boundaries, post-admission expiry with a FAILED (not
  UNCERTAIN) intent, source rebind, book ordering/failure/empty queues and
  bounded decision-poll cadence. Selected permanent regressions are repeated
  against the alternate source: live-game protection, exact exits, unowned
  refusal, hard loss, non-trading cash, fill P&L and shutdown IOC limits.
- Deliberately disabling mandatory history and post-admission proof caused four
  focused failures. Disabling old-book rejection caused the queued-book test to
  fail. Every reversal was restored before the final full suite.
- At 00:45:54Z read-only source validation completed in 12.887s using an isolated
  temporary ledger. At 00:47:58Z the same proof completed in 14.884s on a fresh
  SQLite online copy of the actual production ledger. Both used the slower
  report lane with all mutations forbidden and observed zero working orders /
  one position. Production ledger and process were not changed. These prove
  read-path compatibility, not safe startup around an unknown position or
  loaded-slate throughput.
- Shell syntax and CLI help checks passed without starting the bot. The final
  complete review suite passed **1,257 tests in 124.914 seconds** in the pinned
  WSL virtual environment against Windows review source, including 29 fallback
  cases. Documentation audit and whitespace checks passed. No deployment or live
  trading acceptance is claimed; native installed-source testing awaits an
  authorized deployment. Production remains on `3625927fb`.
- Implementation `2b7e2e4de` and immutable tag `v1.2.0` were pushed atomically.
  Final parity confirmed clean Windows/GitHub equality and a verified private
  ledger recovery copy. WSL intentionally remains on its prior installed source:
  parity reports pending reviewed deployment, not permission to deploy. The PC
  peer was offline/pending and was preserved. At 00:57:05Z the supervisor/runner
  were still stopped; authenticated reads showed zero orders / one position and
  the production ledger had zero unresolved, unknown or working orders.

## Dedicated final edge-case audit

Success and empty-account paths require positive complete reads. Failed,
partial, cancelled, contradictory and over-age reads revoke proof; unknown
working ownership remains account-wide. Nonzero positions retain normal
owned-basis/recovery gates, not a new exception for manual positions. Both
private absence and public failure were checked: no synthetic snapshot can arm
the source, while public errors still trigger rebind. Polls preserve terminal
cancel proof, durable no-retry creates and exact reducing quantities; source
and admission waits cannot reset the ET loss latch. Repeated proof dirties the
bounded slate, including partial fills with unchanged position membership.

Recovery and active-set rebind repeat proof; recovery-only startup also receives
the REST requirement. Shutdown retains owned cancellation and bounded exact
IOC handling, and non-flat/unknown state cannot be labeled flat. Default source
and default recovery report wording are unchanged. The opt-in flag survives a
supervisor relaunch but cannot silently persist into an ordinary new launcher
invocation. No schema, ledger, risk config or unrelated strategy change exists.

API/load risk remains explicit: strict per-order reads add O(working orders)
requests, so a 32-slot session is not proved sustainable. The 4/s shared governor
is unchanged and may reduce availability through stale-proof gates. No capacity
or risk ceiling was lowered to conceal this. Source freshness is bounded local
observation age, never proof against a silently stale venue. Rollback preserves
the ledger and requires the normal separate authority/proof procedure. Secret
files, snapshots, probe helpers and databases stay ignored and are not pushed.

## Authorized deployment and restart — September 10 ET / September 11 UTC

After the rate-budget discussion, the operator said to restart. This supersedes
the earlier implementation-only restriction for this launch; it does not
authorize changing loss/cap/size settings, resetting the ledger or assuming an
external position. The 02:15:34Z authenticated gate was flat/order-free with
zero unresolved/unknown/working ledger rows. The old runner and supervisor were
already stopped.

The native WSL checkout fast-forwarded to pushed `e04df8ec3`, containing reviewed
implementation `2b7e2e4de`. All **170** source/test/script/web/config normalized
hashes matched review. The native complete suite passed **1,257 tests in 91.475
seconds**; the marker was updated only after verification. No code was changed
in this restart task.

A second fresh gate at **02:18:33Z** again proved zero venue orders/positions and
zero unresolved/unknown/working ledger rows. The normal launcher started
supervisor **2860106** and runner **2860114** with explicit
`auto 10 32 rest-fallback`; $90 HARD and existing entry caps/filter remained
unchanged. The existing dashboard service was reused. First discovery at
02:19:26Z used two event pages and found 66 viable markets in 44.4 seconds.
Live order/capacity acceptance is recorded after observing the running loop.

At **02:20:03Z** the September 10 ET adjusted account-loss sensor read
**$93.04225 / $90 (103.38%)** and tightened the loss tier to ten entries.
At **02:20:12Z** the unchanged daily HARD policy durably latched its hold;
entries remain disabled regardless of that count tier. The REST startup marker
and healthy/normal maintenance schedule had already passed. This is a money
stop, not another missing-private-snapshot pause. No daily state was reset.

At **02:21:00Z** authenticated reads proved zero venue orders/positions and zero
unresolved/unknown/working ledger rows. The bot remains online in the daily hold.
It may resume no earlier than September 11 **00:00 ET / 04:00 UTC**, after fresh
flat, adjusted-equity and all other gates pass. This is account-level adjusted
trading loss, not a finding that all $93.04225 belongs to bot-managed trades.
Startup/HARD-pause acceptance passed; no new quote or full-width request-load
acceptance can be claimed while the existing loss rule prohibits entry.

## Return to original WebSocket path and preserve alternative

The operator next requested returning to the old version while saving REST,
then explicitly confirmed restart. The immutable tag
`rest-fallback-2026-09-10` was pushed at `e1c704f06`, preserving the complete
experimental implementation and evidence. Original `v1.2.0` is unchanged.

This uses the original path already retained in the reviewed executable, not a
source/ledger reset: the explicit launcher source changes from `rest-fallback`
to `websocket`. The alternative code remains disabled and available. There are
no schema migrations to undo, and no daily loss state is cleared.

Graceful stop removed supervisor 2860106 and ended runner 2860114 at
**02:24:50Z**, with terminal `flat=true`, zero session creates and
`stop_reason=operator_sigterm`. Two paused-run API windows had 428/197 reads,
zero mutations/failures/429s and queue depth one; they do not prove capacity
under resting orders or fills. Post-stop authenticated proof at 02:25:44Z was
flat/order-free and ledger-clear. All 170 file hashes still matched, so the
existing complete-suite evidence remained valid without repeating unchanged
code tests.

At **02:26:11Z** a fresh flat/clear gate passed, and the launcher started
supervisor **2862401** / runner **2862413** with explicit
`auto 10 32 websocket`. Installed marker remains `e04df8ec3`. The dashboard was
reused. The September 10 HARD latch remains intact; normal stream readiness is
again required and the original feed incident is not presumed resolved.

At **02:27:58Z** the first normal-mode attempt reported
`startup_unavailable` with `order_eof=0/4 account_balance=0/4`. The runner stays
in its existing safe startup retry loop; switching back did not repair the
private feed. At **02:28:28Z** authenticated reads again proved zero orders and
positions, with zero unresolved/unknown/working ledger rows. No quoting
acceptance is claimed and the prior daily HARD latch was not cleared. Both
the private readiness gate and the normal daily loss policy remain required.

## Laptop reboot and authorized rerun

After the operator rebooted the laptop, the September 11 check proved both
supervisor and runner absent. Current-workstation/GitHub/WSL parity passed;
the offline peer remains pending. All 170 normalized deployed file hashes
matched the reviewed source, preserving the existing full-suite evidence.
At **02:56:00Z** authenticated reads proved zero orders/positions and the
read-only ledger gate proved zero working/unknown/unresolved rows.

The explicitly authorized launch restored **supervisor 726 / runner 734**
with `auto 10 32 websocket`; independent dashboard PID 759 also started.
No executable, configuration policy, ledger baseline or daily HARD latch was
changed. REST remains disabled. The first new selection at **02:56:59Z**
found 69 viable markets; selection is not private-feed or quoting acceptance.

At **02:57:50Z** the new runner reported `startup_unavailable` with
`order_eof=0/4 account_balance=0/4`. It remains alive in the safe retry loop.
At **02:58:05Z** authenticated orders/positions and ledger working/unknown/
unresolved counts were all zero. Reboot did not repair missing private data.
The preceding independent read-only socket check at **02:51:29–02:52:14Z**
had also completed its 45-second balance/order observation with successful
handshake, initial/final awaited Pongs and zero application frames.

## September 11 morning diagnostic: private silence plus stopped trader

At **11:04Z / 07:04 ET**, a read-only check found supervisor 726 alive but
runner 734 absent. Logs end near **08:48:20Z / 04:48 ET** with repeated
`GET /v1/incentives (symbol-filtered)` HTTP 500/code 2 failures during bound
contract refresh, followed by `commissioning refused: no bound market has a
usable book`. The supervisor repeatedly reports `ATTENTION_REQUIRED` with
`reason=startup refused`; it deliberately does not relaunch exit code 2.

Source inspection confirms `_refresh_bound_books` catches ordinary exceptions
as per-market drops, then raises `CommissioningRefused` when none survives.
Thus a transient server failure can lose its recoverable classification and
stop the trader. This is a diagnosed local recovery gap, not an implemented fix
or proof that every market's book was actually empty. Repair/restart requires
authorization and full normal review; no source or trading changes were made.

The independent read-only private probe at **11:04:16–11:05:01Z** subscribed
to balance and orders, completed 45 seconds, received both awaited Pongs and
zero application frames, with zero decode failures. Private recovery is still
unproven independently of the stopped runner. Authenticated account proof at
**11:04:51Z** showed zero orders/positions and zero ledger working/unknown/
unresolved rows. No orders were sent or canceled by this diagnostic.

Official changelog remained v0.0.88 announcing September 11 04:00–07:00 ET
Retail/Institutional maintenance; the stop fell within that announced window.
The live status page could not be retrieved in this check, so actual completion
and causal attribution to maintenance are not independently confirmed.

## September 11 authorized temporary REST activation

The operator explicitly requested running the saved temporary fallback. Old
attention supervisor 726 was gracefully stopped; no trader was alive. Parity
passed on the available workstation/GitHub/WSL, with the offline peer pending.
All 170 normalized source/test/script/web/config hashes matched, preserving
both 1,257-test suite results. No executable or policy change was needed.

Authenticated gate at **11:09:25Z** proved zero venue orders/positions and zero
ledger working/unknown/unresolved state. The launcher started **supervisor
6274 / runner 6282** with `auto 10 32 rest-fallback`, reusing the dashboard.
First selection at 11:10:39Z found 51 viable markets; a bound candidate that
was no longer pregame was correctly dropped at 11:10:51Z.

At **11:11:15Z** the overnight pending-entry cap was 30 with positions/reducers
excluded; the runner emitted explicit `mode=rest-fallback private_socket=disabled`.
At **11:11:23Z**, the runner's own official Statuspage check proved completed
maintenance and entered `RESUME_HOLD` until **12:00:07.486Z / 08:00:07 ET**,
plus all existing health gates. This live source supersedes the preceding
diagnostic browser's inability to retrieve status; it does not attribute the
earlier HTTP failures beyond their timing.

At **11:11:36Z**, September 10 HARD released through normal
`fresh_next_day_flat_account` proof. No manual baseline/latch reset occurred.
At **11:11:48Z** authenticated account/ledger again proved flat/order-free/clear.
Startup and maintenance-hold acceptance passed, not normal quoting/full-width
capacity acceptance. The startup server-error classification gap remains
unfixed and shared by both modes. Source marker remains `e04df8ec3`; $90 HARD,
size 10 / 32, entry quality, event guards, exits and API limits are unchanged.

## Same-account scope comparison at September 11 11:29Z

The operator asked whether the private failure is account-specific. Bounded
read-only probes used the same runtime credentials, laptop and raw WebSocket
library, bypassing bot subscription/decoding behavior for the wire comparison.
No additional account/key, credential rotation, egress change or mutation was
used; REST fallback supervisor 6274 / runner 6282 remained unchanged.

- **Private:** 11:28:50–11:29:35Z, complete 45-second observation after balance
  and all-order subscriptions: handshake and initial/final Pongs succeeded,
  zero application frames and zero decode failures.
- **Markets:** 11:29:00–11:29:11Z, one current bound-market full-book subscription:
  handshake/Pongs succeeded, three real `marketData` frames, zero error frames;
  probe closed at its three-message cap. Envelope keys were `marketData`,
  `requestId`, `subscriptionType`, matching the Retail docs.
- **REST:** 11:29:28Z authenticated orders/positions returned zero, and read-only
  ledger working/unknown/unresolved counts were zero. The runner's preceding
  187-request window had zero failures/429s and zero mutations.

This evidence disfavors a general invalid credential, total WebSocket block
or general laptop/network outage. It does not distinguish account/key-specific
private permissions/routing from a wider private-service defect or prove server
subscription acceptance. Another authorized account or Polymarket's server-side
key/account/subscription trace is required for that distinction. No new key or
second account was accessed. Official status summary at 11:24:50Z reported all
systems operational with empty active-incident/maintenance lists; that public
summary does not override the measured private silence. No code/deploy/restart.

## Authorized isolated second-key test

After creating a new key, the operator explicitly authorized a read-only test
despite being warned that the pasted secret should be treated as exposed.
The new credentials were supplied only through the diagnostic process's
environment into WSL, not installed in any credential/configuration file.
Values are intentionally omitted here. The child shell removed its overrides
after the probe; the running fallback and original runtime key were unchanged.

At **September 11 11:38:30–11:39:15Z / 07:38–07:39 ET**, the raw private
socket probe confirmed both key and secret environment overrides present.
Handshake, initial Pong and final Pong succeeded. Balance and order
subscriptions produced **zero application frames over 45 seconds**, with zero
decode failures. This reproduces the original-key symptom and makes a defect
limited to that old key less likely. It does not prove application-level
subscription acceptance or distinguish same-account permissions/routing from
a broader private-service problem. No second account was accessed.

Supervisor 6274 / runner 6282 remained alive with original credentials and
unchanged size 10 / 32 REST mode. Its latest inspected 11:38:22Z budget window
had 185 reads, zero mutations/failures/429s. No code, config, ledger, restart or
venue order mutation accompanied the test. The operator should revoke only the
chat-exposed test key; no revocation was performed by this task.
