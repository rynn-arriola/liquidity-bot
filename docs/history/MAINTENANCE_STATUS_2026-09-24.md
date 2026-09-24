# September24 maintenance status check

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
