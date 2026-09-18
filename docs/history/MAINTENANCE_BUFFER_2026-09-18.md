# Fifteen-minute maintenance buffer — September 18, 2026

## Authorized scope and operating state

The operator requested a 15-minute post-maintenance buffer instead of one hour,
using 05:00–07:00 ET with 07:15 eligibility as the example. No restart was
requested. Production supervisor 432 and runner 446 retain their September 16
12:08:21/22 process start stamps. No process control, account API diagnostic,
order mutation, configuration rewrite or deployment occurred in this task.
Current authenticated orders/positions are unknown.

## Design and review

Change only `MAINTENANCE_RESUME_DELAY_SECONDS` from 3600 to 900. Keep the
single schedule policy shared by fresh Statuspage observations, cached windows
and runner recovery. A configurable/hot-reloaded policy would add unnecessary
state and migration work for this request. The current Python process retains
its imported old constant; source deployment alone cannot activate the change.

Release remains 15 minutes after the later of scheduled end and official
completion. Early completion cannot shorten the planned window; late completion
extends it. 07:00 completion permits schedule recovery from 07:15, followed by
three complete authenticated health cycles and all normal guards, so trading
is not guaranteed at exactly 07:15. The 30-minute pre-start evacuation remains.
Benefit: 45 minutes less intentional post-window delay. Tradeoff: less quiet time
after venue recovery, mitigated by unchanged fresh-feed/account/stream/UI gates.

Dedicated edge-case audit: exact boundary and one microsecond before; early,
on-time and late completion; active/unknown status after planned end; ET/UTC
equivalence; cache serialization/restart; missing exact record requiring 404
plus successful history; stale feed at the new boundary; three health cycles;
empty and positioned owners through existing maintenance-recovery coverage.
No change to order ownership, exits, pending-entry capacity, loss limits,
shutdown or API request rate. No live failure induced. Rollback changes only
the buffer back to 3600 and likewise requires authorized restart; no cache or
ledger migration is needed. Historical one-hour evidence remains historical.

## Verification

- Initial parity passed, local/GitHub/WSL HEAD `ebba9250`; offline PC skipped.
- 20 schedule/read-recovery tests and two runner-boundary tests pass.
- Replacing only the constant with 3600 in an isolated test process produces
  six expected assertion failures and zero errors across 14 schedule tests;
  the patch context restores 900 without changing source or the live process.
- Complete suite: 1,752 tests passed in254.947s, zero failures/errors, using
  production Python against the Windows review checkout. The initial run
  exposed one stale one-hour boundary fixture; it was corrected to15 minutes,
  targeted tests passed, and the entire suite was rerun successfully.
- Final diff/edge-case review, documentation audit and whitespace check pass.

## Subsequent restart authorization

The operator then authorized restarting after all work, including yesterday's
fixes. This supersedes the initial no-restart scope but does not waive safe
account/ownership proof. At 09:46:16Z the existing runner still reported
`GET /v1/orders/open` HTTP503/code14, with recovery health at0/3. One read-only
preparation gate was refused by the shared open-orders backoff before sending
a request. No limiter bypass or repeated diagnostic was used. Account exposure
remains unknown; do not stop its recovery management merely to attempt a start
that cannot yet pass its account gate. Deployment can proceed without reload.

Activation remains pending reviewed deployment and successful restart gates.
The earlier `ee500e4db` exit/receipt fixes remain installed but not loaded.

## Installed release; restart remains venue-blocked

Reviewed release `0e097b51a6f2df1aac71eb027682cfe1ccd46378` was pushed
before the clean native WSL fast-forward. A local Git bundle of that pushed
commit transported the change after WSL's direct fetch stalled; only that
diagnostic fetch process was terminated, never the supervisor or runner.

- All207 tracked source/test/script/config/web normalized hashes match.
- Exact installed production checkout: all1,752 tests pass in168.283s,
  zero failures/errors. `DEPLOYED_COMMIT` identifies this installed release.
- Yesterday's deferred-reducer and post-read receipt fixes are included.
- The official public summary still says `Service Under Maintenance`, with
  the05:00–07:00 ET maintenance `in_progress`. At09:50:17Z the running bot's
  orders read returnedHTTP503/code14, stable0/3. No repeated account probe
  or throttle bypass was added.
- No process stop/restart/reload, risk reset, ledger repair or venue mutation
  occurred. The old runner retains its imported one-hour buffer and old fixes.
  Authenticated account exposure remains unknown, so restart cannot yet pass.
- Continue the already-authorized restart only once fresh account/ownership
  evidence is available: graceful stop, positive post-stop flat/clear proof,
  verified stopped recovery backup, fresh startup gate and live acceptance.
  Do not misreport installed source or a maintenance hold as live acceptance.

## Authorized restart into the hold

At07:34 ET the official summary was operational and authenticated reads had
recovered, but completion at07:30:07 ET left the new hold active until07:45:07.
The operator explicitly requested restarting immediately so the bot would be
ready later. Fresh pre-stop proof at11:35:23Z showed zero venue orders,
positions, unresolved intents, unknown orders, working ledger orders or ledger
positions. The old supervisor432/runner446 then stopped normally with
`flat=true`; no force kill was used. Parity captured and verified the stopped
ledger backup. A second authenticated gate at11:36:27Z proved the same complete
zero state.

Supervisor136210 and runner136224 started at07:36:30 ET with persisted size10,
32 slots and manual REST mode. Startup loaded release `0e097b51a` and reported:

- official completed schedule, `resume_at=2026-09-18T11:45:07.368Z`;
- `ENTRY_PAUSED` for `scheduled_maintenance_resume_hold`;
- six-hour loss0, hard25, pending-entry cap30, no provisional/held state;
- REST account mode, private socket disabled, automatic source switch disabled.

This proves correct startup/hold behavior, not automatic release or quoting.
At the boundary the existing three authenticated health reads plus stream,
reconciliation, UI and risk gates must pass before entries can resume.

## Automatic release acceptance

The runner passed the release gates and resumed without another restart. At
11:58:53Z the authenticated account held27 flat entry orders across22 markets,
zero positions and zero unknown orders. One order intent was in flight during
that asynchronous snapshot; the ordinary repair loop reported remaining0 at
11:59:06Z. The process remained alive and active.

The first completed active API window covered77 seconds/291 attempts at3.782
requests per second: zero429s/cooldowns, six individual-order404 publication
delays and no account-read cooldown. Those404s reached normal terminal/repair
handling. Startup repricing produced heavy create/cancel activity and an early
decision-latency p95 of31.081 seconds, so this is successful release and risk
ownership acceptance, not sustained performance acceptance. No position had
yet naturally exercised yesterday's deferred-exit repair.
