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
