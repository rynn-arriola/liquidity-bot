# Stop and automatic recovery audit — September 7, 2026 ET

The operator asked what can stop the bot and requested a design that stays alive
through errors, pauses trading, resolves recoverable state and resumes as it
does for maintenance. This task reviews the current behavior and makes the
recommended changes concrete. No executable/configuration change, venue
mutation, restart, induced live fault or risk-baseline reset was performed.

## Current production evidence

- At **2026-09-08 03:36:57.339241Z / September 7 23:36 ET**, authenticated reads
  found **14 orders across 12 markets, one position, zero unknown orders and
  two unresolved intents**; 15 local working rows. Reads are sequential during
  trading, so differing counts are not proof that an order was cancelled.
- Same runner **816489**, supervisor **816479**, about 12 hours uptime, size
  10 / 32, installed `b089b3a6e`. Available Windows/GitHub/WSL parity passed
  at documentation HEAD `e0eb69d03`; backup PC offline/pending. Recent API
  windows through 03:35:47Z had no failures/429s/cooldown.
- Current-run schedule-feed timeouts at September 7 17:01, 20:10 and 20:26Z
  later returned to HEALTHY at 17:04, 20:13 and 20:28Z without a restart.
  This proves those feed recoveries, not acceptance of every outage path.

## What can end or disable useful work

| Trigger | Current behavior and source |
|---|---|
| Confirmed adjusted marked-equity HARD breach | Terminates; $90 at size 10 / 32. Open-mark breaches first block new risk and require the existing cohort-close/fresh-equity confirmation. `_stop_reason`, `runtime_safety.SessionRiskGuard`; preserve this business rule. |
| Operator SIGTERM/SIGINT/stop; cancelled process | Terminates through shutdown. Must never be automatically undone by recovery. |
| Missing risk guard, critical risk state, backward session clock | Terminal. The live duration is infinity, so the generic duration-limit capability is not a current scheduled stop. Missing/stale marked equity alone is already recoverable with entries blocked. |
| Unknown ownership, mismatched action/market, non-exact reducer/IOC, missing cancel ID, other runtime invariants | Named critical stop or startup refusal. A reconnected socket cannot repair these facts. Unrecognized strategy criticals default terminal. |
| Private/public/both-stream reconnect failures | Each has a **12-attempt** limit. Only at least three consecutive classified transport/5xx failures enter the indefinite maintenance hold. Other exhausted failures return `runner_failure:*_recovery_exhausted`. |
| Temporary startup condition | `CommissioningUnavailable` ends as refusal after **60 attempts**. Classified outages enter an indefinite health hold earlier; no-ID PLACE proof and local API deferral have distinct waits. Invalid configuration/ownership refuses immediately. |
| Error during an established maintenance hold | Transport/5xx and confirmed 429 reset the healthy streak and continue. Schema/other unclassified errors escape; invariants remain terminal. |
| One market repeatedly cannot exit | Retirement waits 60 seconds between rounds. At **10 rounds**, `_decide_for_market` skips all further planning even with a position; shutdown alone makes a later final attempt. Reads/scheduling can continue, which does not mean exits continue. |
| Many recent market faults | More than `max_markets` recent non-recovery, non-post-kickoff retirements within **900 seconds** returns `critical:market_retirement_limit_exceeded`. At size 10 / 32 this means more than 32, not 22 lifetime retirements in a summary. |
| Other caught runner exception | Goes through shutdown. Restarts only if the result proves flat. A generic planning failure can instead be classified critical; market-specific non-invariant failures normally retire that market. |
| Non-flat shutdown | Main returns **3**, and the supervisor exits permanently. Shutdown has a **900-second** window and **three exit attempts per market**; a returned process is not flat proof. |
| Supervisor outcomes | Exit 0/2/3/4 stops supervision. Flat recoverable exit 5 and unexpected deaths retry with 10/20/40/60-second backoff; **three consecutive runs under 120 seconds** stop the supervisor. A run lasting at least 120 seconds resets that counter. |
| Host/storage/credentials | Laptop sleep/power/WSL death, disk failure, corrupt database, invalid credentials or configuration can prevent any useful execution. A trading loop cannot guarantee repairing these automatically. |

Code routes: `live_commissioning.py` preflight 2927, stop 4750, abandoned owner
5498, reconnect 7651/7837/7967, classifier 8084, maintenance 8155, retirement
8384, action failures 8659, shutdown 8882, run 9182, main 9776;
`scripts/supervise_commissioning.sh`; `live_session.py` action checks.
Line numbers describe the audited release; method names are durable routes.

## Reproduced gaps and existing test evidence

The ignored `.local-tools/recovery_stop_audit.py` uses only fake venue clients,
temporary ledgers and virtual sleeps. It reproduced, with zero creates:

1. Twelve HTTP 429 failures in both-stream recovery end as exhausted recovery
   with **zero calls** to the long-lived outage hold.
2. Alternating six HTTP 503 and six HTTP 429 failures have the same result;
   every 429 resets the consecutive-venue-failure streak.
3. A temporary unavailable startup condition becomes `CommissioningRefused`
   on attempt 60.
4. A known **10-contract** position at retirement round 10 reaches the decision
   function but invokes the exit planner **zero times**.

The fourth behavior also has retained live evidence: at September 6
**21:31:28.434862Z** and **23:22:43.032924Z**, logs recorded
`aec-itfwo-miczun-zoedol-2026-09-06 abandoned_after=10_rounds position=-10.0000`.
Do not attribute its original failed exit to maintenance without further proof.
One existing scheduler test checks that a positioned abandoned owner is still
scheduled; the early return inside the decision function is a separate layer.

**18 existing recovery/stop tests passed in 2.632 seconds** under the pinned
WSL Python runtime against the review checkout. Coverage includes reconnect
bounds, maintenance health streaks/429/schema behavior, startup waits/refusals,
no-ID proof waits, HARD/operator stop, UI pause, abandonment, retirement burst
and shutdown's final abandoned-position attempt. These tests confirm current
behavior; they do not accept a proposed fix or justify changing live rules.

## Recommended implementation, ordered by importance

1. **Keep position recovery active.** Replace terminal per-market abandonment
   with a timed recovery state. Always reserve its slot/exposure, keep account
   and execution proof current, and evaluate a possible exit at a capped
   cadence. Re-arm a bounded attempt round only from fresh evidence and exact
   terminal proof of previous mutations. Closed/empty books use the existing
   inactive/settlement waits. Never resend an ambiguous order or exceed exact
   remaining quantity. Preserve the inventory/pre-kickoff policies.
2. **Unify temporary transport/throttle waiting.** After the short reconnect
   budget, enter a persistent no-entry recovery mode for explicitly recognized
   temporary failures, including mixed 5xx/429 sequences. Classify by typed
   status/cause, retain bounded/jittered probes and the shared API limiter,
   consume private events in order, and require full account reconciliation,
   current risk, healthy streams and all maintenance gates before new entry.
   An arbitrary exception must not be reclassified as an outage.
3. **Separate waiting from defects at startup/supervisor.** Temporary no-market,
   feed and throttle conditions should wait without exhausting a process
   budget. Persistent unknown schema, credentials, ownership and storage errors
   need an explicit attention state. A lightweight supervisor may remain alive
   and alert without invoking broken trading code; it must not clear stops,
   reset loss state or repeatedly relaunch a deterministic defect.
4. **Turn a recoverable market-error cascade into an account recovery pause.**
   Halt selection/churn while diagnosing common feed/service health. Restore
   only owners that pass fresh proof. A count alone must not certify recovery;
   genuine corruption/invariants retain trading lockout.
5. **Make recovery visible.** Report the specific blocker, position/order
   certainty, next retry and last successful proof; alert on state changes and
   bounded reminders. A live PID is not proof of trading or active exits.

Acceptance must exercise errors lasting hours, mixed failures, partial fills,
cancel confirmation after timeout, no-ID creates, offline/one-sided/closed
markets, recovery while still holding inventory, 32 owners, pending-order caps,
HARD/operator stops, ET rollover, retained scheduled holds and a repeated crash.
Do not induce any live fault. Source changes require regressions/reversals,
appropriate complete suite, final edge review and an authorized safe deployment.

## Upcoming maintenance is already captured

The official [changelog](https://docs.polymarket.us/changelog) now lists v0.0.84:
**September 8, 02:00–06:00 ET**, affecting Retail and Institutional APIs.
The browser tool could not retrieve the linked Statuspage or its JSON during
this review; this does not establish a venue outage. The running bot's local
schedule cache, observed **03:37:21.266496Z**, contains exact scheduled record
`v02ykwvxzrh2` for **06:00–10:00Z**. Under the existing schedule code that means
evacuation from **01:30 ET**, no earlier than **07:00 ET** resumption, and later
if official completion/health/stream/ownership or other trading gates require.
This confirms ingestion and configured timing, not future flatness or resumed
trading. No new scheduler, restart or automatic monitoring task was added.
