# Current operator status

This is the only Markdown file that states the current operational handoff.
It is deliberately short. Historical “current” or “active” headings elsewhere
describe their dated event, not the bot now.

## Last verified

- **Time:** 2026-08-27 18:41 ET / 2026-08-27 22:41 UTC
- **Bot:** STOPPED at the operator's request; no supervisor or runner process
- **Stop quality:** FORCED after graceful shutdown could not obtain venue
  proof. Current orders and positions remain UNKNOWN and may be unmanaged.
- **Maker-lab:** stopped
- **Deployed marker:** `a974d68`; hard-stop/report implementation is `34ff5db`
- **Quote size:** 10 contracts
- **Capacity:** 20 market slots
- **Latest queue proof:** `markets=20` at 17:09:37 UTC
- **Effective slot state:** latest authenticated sample had 18 quoting markets;
  queue scope remains 20
- **Venue proof:** UNKNOWN now. `GET /v1/orders/open` still returned HTTP 5xx
  at 22:21 UTC. The last successful account proof predates this outage and
  must not be used to call the account flat.
- **Rewards API:** pending `$0.00`; bot-era paid `$634.97` when checked on
  2026-08-26
- **Repository handoff:** three August 27 fixes are reviewed locally and not
  deployed: dashboard unavailable-state truth, bounded maintenance tracebacks,
  and positive closed-market slot replacement. Four deliberate reversions were
  detected and all 660 local tests pass in 39.768s.

The 17:10 authenticated acceptance checkpoint held 18 resting orders across 18
markets, zero unresolved intents, zero `UNKNOWN_ABSENT` rows and zero positions.
Raw order count is not slot count; queue telemetry independently proved all 20
markets bound.

Local ledger state during the outage last showed 31 working rows, one
unresolved intent, zero `UNKNOWN_ABSENT` rows and zero nonzero positions. It is
retained ownership/recovery state, not current venue proof.

## What just happened

The operator requested a stop during the maintenance outage. The supervisor
stopped and sent SIGTERM at about 22:24 UTC. Graceful shutdown retried the same
owned market fourteen times, but authenticated account reconciliation kept
returning `AuthenticationError`; it therefore could not prove flat. At the
operator's explicit direction, verified runner PID `16638` was force-killed.
At 22:31 UTC no liquidity supervisor or runner process remained. Because the
venue never supplied a complete account snapshot, no one may describe this as
a flat stop; possible venue orders are unmanaged until a later authenticated
reconciliation.

The reviewed local fixes now preserve `unknown` through every dashboard field
when any account read fails; keep one full maintenance traceback at attempt 1
and every 30 attempts while retaining every concise attempt/health marker; and
let incremental top-up replace an owner only after exact market metadata is
freshly re-read as closed/not-open plus complete position/order/intent proof is
clear. Equivalent `unrecoverable_book_*` observations now remain one
retirement family instead of advancing to abandonment. The exact 20 cap,
retained orders, near-flatten guard and stream transaction boundary are
unchanged. These changes are not yet deployed and no restart is authorized.

Polymarket US account service became unavailable during the current run. The
runner entered its maintenance pause at 20:13:34 UTC and remained dormant
through probe attempt 129 at 22:21:48 UTC, with `stable=0/3`. No restart is
authorized while exposure cannot be proved. The process is behaving safely:
it will require three consecutive complete authenticated account checks and
then the normal reconciliation/stream recovery before it can quote again.

Today's audit found a separate dashboard defect: when the authenticated venue
read raises, the dashboard labels the venue unreachable but still renders its
default counters as `positions 0 (flat)`, zero resting orders and empty slots.
That is false certainty while exposure is unknown. It is reporting-only; the
runner itself remains fail-closed. No code fix has yet been authorized or
applied.

At 17:42 UTC Polymarket rejected trade and market WebSocket startup with HTTP
503 during a slot top-up. Both streams recovered in-process. Seven flat markets
temporarily retired for `unresolved_mutation_timeout`; positive terminal proof
released all seven by 17:46. At 18:00 one later canceled order was briefly
`UNKNOWN_ABSENT`, so the dashboard correctly labeled rotation blocked. The
exact-order reread resolved it automatically by 18:01. The runner never stopped,
continued placing orders, held zero positions, and had no loss-stop event.

The August 26 hard-stop incident was reconstructed. Marked equity crossed $80
while positions were open, but the adjacent flat ET account boundaries were
`$772.7878` and `$708.3008`, and a bounded cash-only activity scan proved zero
non-trading cash. The completed trading result was therefore `-$64.4870`; the
Discord row's `-$48.65` was the incomplete balanced-pair estimate.

The deployed fixes now make an open-position marked breach provisional while
blocking all new entries, require a breach-time position close plus a newer balance, and
remove proven non-trading cash from the hard mark. Historical report rows use
adjacent proven-flat ET boundaries where possible and visibly mark pair
fallbacks with `~`. Six mutation checks and all 651 local tests pass in 41.735s.
At the operator's request, the old runner gracefully stopped `flat=true` at
17:05:44 UTC. Post-stop authenticated proof showed zero venue orders and
positions plus zero unresolved/unknown ledger state. All 15 deployed hashes
matched, all 651 server tests passed in 42.288s, and the unchanged `auto 10`
runner restarted at 17:08:31 UTC. It reached `markets=20`, ordinary live order
actions and the clean 17:10 acceptance state above without a risk or recovery
fault.

The same runner survived Polymarket maintenance from 08:41 UTC. Account health
first succeeded at 12:20, failed once and correctly reset to `0/3`, then passed
three consecutive complete checks at 12:22, 12:23 and 12:24. The gate recorded
`state=health_gate_passed` at 12:24:50, normal reconciliation and fresh-stream
recovery completed, and quoting resumed without a process restart. This is the
first natural live acceptance of both halves of maintenance-aware recovery.

The operator requested a restart once the account was flat. At 13:32:59 UTC an
authenticated pre-stop gate proved zero venue orders and zero positions.
Graceful shutdown ended `operator_sigterm`, `flat=true` at 13:33:49. A second
authenticated/ledger gate at 13:34:08 proved venue orders/positions and ledger
working/unresolved/unknown/positions all zero. The unchanged `auto 10` runner
then started, bound 20 markets and placed across 19 without a startup error.

The restart cleared the in-memory cooldown state that had parked three WTT
owners, and fresh selection did not choose those closed/empty markets. This is
an operational workaround, not a code fix for the retirement/top-up gap
documented in FINDINGS and NEXT.

The August 26 recovery/slot fixes are pushed and deployed. The first reviewed
restart initialized all 20 slots, but five order creates were rejected because
the venue temporarily classified the server connection as a VPN/proxy whose
location could not be verified. The bot conservatively recorded those creates
as uncertain and was stopped before it could churn through the slate.

Two authenticated venue snapshots proved zero open orders and positions. The
existing stopped-runner orphan utility then performed its own venue proof and
resolved exactly those five no-ID creates as failed. A full server reboot
cleared the transient classification. The same server and IP now receives real
exchange order IDs, so there is no evidence of an account ban, permanent IP
block or need for a new droplet.

## Current interpretation

- The 17:42 error was a transient venue HTTP 503 during WebSocket startup, not
  the newly deployed hard-stop/account-P&L code. Recovery restored streams,
  resolved every affected order and continued trading without restart.
- Maintenance-aware recovery is now naturally live-accepted end to end: the
  process stayed dormant during the outage, reset its success streak after an
  intervening failure, passed three consecutive checks, reconciled and resumed
  safe quoting without a restart.
- The restarted scheduler has a fresh 20-market scope and 19 owners already
  quoting. The previous three parked owners are gone. Raw order count still is
  not slot count; the open retirement/top-up availability gap remains unfixed.
- The proxy/location incident was operational and transient. No code was
  changed to weaken uncertain-mutation safety.
- If the exact permission error returns, stop promptly. Prove account state
  from authenticated venue reads before force-stopping or repairing any
  intent. Never infer safety from ledger absence.
- The server remains the production runtime. The attempted local-run idea was
  not carried out.

## Operator decisions currently in force

The concise authoritative list is [DECISIONS.md](DECISIONS.md). Key points:
size 10, 20 slots, six-hour horizon, 2-cent new-entry spread filter, no league
filter, an $80 realized-session sensor and an $80 marked-equity sensor whose
open-position breach is provisional until a position closes, and no automatic
queue-reset behavior.

## Next AI operator

Start with [AI_OPERATOR.md](AI_OPERATOR.md), then verify server status before
reporting that this snapshot is still true. For a low-order concern, ask to
“run the 20-slot scheduler audit.” For a production incident, search
[FINDINGS.md](FINDINGS.md) and [BUGFIXES.md](BUGFIXES.md) before theorizing.

No live action is implied by this handoff. The human operator makes the next
business, risk, deployment and restart decision.
