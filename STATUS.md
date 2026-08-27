# Current operator status

This is the only Markdown file that states the current operational handoff.
It is deliberately short. Historical “current” or “active” headings elsewhere
describe their dated event, not the bot now.

## Last verified

- **Time:** 2026-08-27 09:04 ET / 2026-08-27 13:04 UTC
- **Bot:** LIVE and quoting after automatic maintenance recovery
- **Supervisor / runner:** active as `8058` / `8064`; current run began
  2026-08-27 04:02:57 UTC
- **Maker-lab:** stopped
- **Deployed marker:** `0b79a1f`; reviewed recovery source is `5825db8`
- **Quote size:** 10 contracts
- **Capacity:** 20 market slots
- **Latest queue proof:** `markets=20`, `active_orders=21` at 12:50:48 UTC
- **Venue proof:** 19 open orders across 10 markets and zero positions at
  13:04:37 UTC
- **Rewards API:** pending `$0.00`; bot-era paid `$634.97` when checked on
  2026-08-26

The 13:04 UTC ledger sample converged to the same 19 working orders across ten
markets, zero unresolved intents, zero `UNKNOWN_ABSENT` rows and zero nonzero
positions. Nineteen orders does not mean nineteen slots: current queue
telemetry positively binds all 20 slots, while only ten markets presently have
eligible/resting sides.

## What just happened

The same runner survived Polymarket maintenance from 08:41 UTC. Account health
first succeeded at 12:20, failed once and correctly reset to `0/3`, then passed
three consecutive complete checks at 12:22, 12:23 and 12:24. The gate recorded
`state=health_gate_passed` at 12:24:50, normal reconciliation and fresh-stream
recovery completed, and quoting resumed without a process restart. This is the
first natural live acceptance of both halves of maintenance-aware recovery.

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
- The 20-slot scheduler is working; order count is not slot count.
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
filter, $80 realized-session and $80 marked-equity account stops, and no
automatic queue-reset behavior.

## Next AI operator

Start with [AI_OPERATOR.md](AI_OPERATOR.md), then verify server status before
reporting that this snapshot is still true. For a low-order concern, ask to
“run the 20-slot scheduler audit.” For a production incident, search
[FINDINGS.md](FINDINGS.md) and [BUGFIXES.md](BUGFIXES.md) before theorizing.

No live action is implied by this handoff. The human operator makes the next
business, risk, deployment and restart decision.
