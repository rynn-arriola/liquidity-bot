# Current operator status

This is the only Markdown file that states the current operational handoff.
It is deliberately short. Historical “current” or “active” headings elsewhere
describe their dated event, not the bot now.

## Last verified

- **Time:** 2026-08-27 11:53 ET / 2026-08-27 15:53 UTC
- **Bot:** LIVE after an operator-authorized clean restart
- **Supervisor / runner:** active as `13125` / `13131`; current run began
  2026-08-27 13:34:15 UTC
- **Maker-lab:** stopped
- **Deployed marker:** `0b79a1f`; reviewed recovery source is `5825db8`
- **Quote size:** 10 contracts
- **Capacity:** 20 market slots
- **Latest queue proof:** `markets=20` at 13:35:42 UTC
- **Effective slot state:** fresh scope of 20; actions reached 19 distinct
  owners with no startup retirement or recovery error
- **Venue proof:** 24 open orders across 19 markets and zero positions at
  13:36:38 UTC
- **Rewards API:** pending `$0.00`; bot-era paid `$634.97` when checked on
  2026-08-26
- **Repository handoff:** commit `34ff5db` contains the provisional
  open-position hard-stop and historical account-P&L fixes; 651 tests pass;
  not deployed and not loaded by this runner

The initial 13:36 ledger checkpoint held 19 working orders across 19 markets,
zero unresolved intents, zero `UNKNOWN_ABSENT` rows and zero positions. The
venue count rose to 24 during normal startup convergence.

## What just happened

The August 26 hard-stop incident was reconstructed. Marked equity crossed $80
while positions were open, but the adjacent flat ET account boundaries were
`$772.7878` and `$708.3008`, and a bounded cash-only activity scan proved zero
non-trading cash. The completed trading result was therefore `-$64.4870`; the
Discord row's `-$48.65` was the incomplete balanced-pair estimate.

Local fixes now make an open-position marked breach provisional while blocking
all new entries, require a breach-time position close plus a newer balance, and
remove proven non-trading cash from the hard mark. Historical report rows use
adjacent proven-flat ET boundaries where possible and visibly mark pair
fallbacks with `~`. Six mutation checks and all 651 tests pass in 41.735s. These changes
are not deployed and the bot was not restarted. At 15:53 UTC supervisor 13125
and runner 13131 retained their original 13:34:15 UTC start time on deployed
marker `0b79a1f`; the commissioning log was still advancing through normal
reconciliation and quote actions.

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
