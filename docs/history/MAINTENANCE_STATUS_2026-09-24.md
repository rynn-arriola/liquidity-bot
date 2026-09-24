# September24 maintenance status check

## 08:22 ET follow-up

The same supervisor/runner remain alive. Authenticated12:22:54Z reads now prove
zero venue orders and positions, superseding the earlier unknown state below.
Runner health checks succeeded07:59/08:00/08:01 ET and passed3of3 at08:01:26.
The original06–08 maintenance completed08:00:08 ET, but fresh official summary
lists a follow-up08:00–08:30 ET maintenance, status verifying. Runner schedule
at08:16:21 confirms MAINTENANCE for `djw62m9hf7sl`, resume_at unknown.
Earliest policy resumption is08:45 if completed by08:30, later if extended,
and only with all existing health/ownership/stream/UI gates.

Latest completed API window12:22:40Z:1.750rps,105 reads, zero mutations,
failures or429s. Two execution-history429s at12:12 recovered; subsequent
windows were clean. Several held-market contract refreshes report unavailable
pregame state; those markets fail eligibility. That is not proof of a runner
crash or an additional platform-wide incident. Natural post-maintenance
replacement/reselection remains unverified; no behavior change is authorized.

Read-only status task: no trading code/config/marker change or restart.
Official source: https://status.polymarketexchange.com/api/v2/summary.json .

## 06:42 ET historical check

At10:42:02Z (06:42 ET), WSL supervisor21730/runner21744 remained alive with
the existing auto size15/32 hybrid command. Log writes continued through
10:42:05Z; minute recovery probes returned HTTP503/code14 for orders/open,
with earlier portfolio/positions503. Recovery health remained stable0/3.
Stack-trace context did not mean the runner had stopped: its PID and fresh
recovery logs prove continued operation.

The official Statuspage summary endpoint freshly reported Service Under
Maintenance, scheduled maintenance in_progress September24 06:00–08:00 ET,
and no separate incident. Source: https://status.polymarketexchange.com/api/v2/summary.json
and https://status.polymarketexchange.com/incidents/dwx62n7l81gv .

Current orders/positions are unknown: the bot's own authenticated probes are
failing503, and no redundant diagnostic requests were added. Last night's
position counts are historical, not current flat proof. No claim is made here
about successful pre-maintenance evacuation.

Preserved policy gives08:15 ET as the earliest scheduled resume only if
maintenance ends08:00; an extension moves that boundary. Three healthy reads
and the existing ownership/UI/stream gates still apply. Natural recovery is
pending; no guarantee of trading at08:15 and no restart was performed.

This task changes documentation only, not code/config/marker or venue state.
