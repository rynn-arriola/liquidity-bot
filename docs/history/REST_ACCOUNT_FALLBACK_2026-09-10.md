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
