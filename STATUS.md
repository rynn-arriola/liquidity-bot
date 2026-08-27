# Current operator status

This is the only Markdown file that states the current operational handoff.
It is deliberately short. Historical “current” or “active” headings elsewhere
describe their dated event, not the bot now.

## Last verified

- **Time:** 2026-08-26 20:48 ET / 2026-08-27 00:48 UTC
- **Bot:** LIVE on the production server
- **Supervisor / runner:** active; current run began 2026-08-26 22:36 UTC
- **Maker-lab:** stopped
- **Deployed marker:** `0b79a1f`; reviewed recovery source is `5825db8`
- **Quote size:** 10 contracts
- **Capacity:** 20 market slots
- **Latest queue proof:** `markets=20`, `active_orders=31` at 00:41:57 UTC
- **Rewards API:** pending `$0.00`; bot-era paid `$634.97` when checked on
  2026-08-26

Working-order and position counts change continuously. At the 00:43 UTC
read-only ledger sample, quoting was active across the 20-slot scope. Do not
use that sample as proof of current exposure; query the venue whenever orders
or positions affect a decision.

## What just happened

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
