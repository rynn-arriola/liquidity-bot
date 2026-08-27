# Current operator status

This is the only Markdown file that states the current operational handoff.
It is deliberately short. Historical “current” or “active” headings elsewhere
describe their dated event, not the bot now.

## Last verified

- **Time:** 2026-08-27 06:36 ET / 2026-08-27 10:36 UTC
- **Bot:** LIVE process, deliberately `MAINTENANCE_PAUSED`; no new risk
- **Supervisor / runner:** active as `8058` / `8064`; current run began
  2026-08-27 04:02:57 UTC
- **Maker-lab:** stopped
- **Deployed marker:** `0b79a1f`; reviewed recovery source is `5825db8`
- **Quote size:** 10 contracts
- **Capacity:** 20 market slots
- **Maintenance proof:** entered from private-stream recovery at 08:41:27 UTC;
  authenticated open-order health remains `failed`, `stable=0/3`
- **Rewards API:** pending `$0.00`; bot-era paid `$634.97` when checked on
  2026-08-26

The 10:36 UTC ledger sample contains 27 last-known working orders, eight
unresolved intents, zero `UNKNOWN_ABSENT` rows and one nonzero position. Those
are not current venue proof: Polymarket's authenticated open-order endpoint is
returning HTTP 500, so present exposure is unknown. Do not stop, restart,
repair or infer flatness from the ledger while the venue is unreadable.

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

- The maintenance-aware recovery is operating as designed: the process stays
  alive, makes no new trading decisions and probes account health once per
  minute. Every probe through 10:36 UTC failed before reaching the first of
  three required consecutive successes.
- When three complete authenticated order/position/balance passes succeed
  about one minute apart, normal reconciliation and fresh-stream gates must
  still pass before quoting resumes automatically. No manual restart is
  currently recommended.
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
