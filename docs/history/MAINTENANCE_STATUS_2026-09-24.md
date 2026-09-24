# September24 maintenance status check

## 09:17 ET trading acceptance

Same supervisor21730/runner21744 running. Scheduled phase became NORMAL at
12:45:08Z, with health requirement cleared12:45:28Z. Do not equate that with
uninterrupted quoting: account-read429s remained visible in09:09–09:14 windows
(balance,positions,execution history), with no mutations in those windows.
The13:15:59/13:17:04/13:18:06 windows had mutations and zero429, at3.180,
3.668 and3.520rps respectively; individual order-retrieve404s still occurred.

Authenticated13:17:26Z:29 pending entry orders, zero positions. A local
13:17:37Z in-flight intent was followed by zero unresolved/unknown/absent
orders at13:18:29Z. Latest hybrid telemetry receiving/pending0; gaps5 is
cumulative and not proof of five current unresolved problems. Touch-based
cancels/replacements are frequent; no new strategy/performance conclusion.

Read-only status check; no restart or code/config change. Background parity
SSH to the backup PC stalled for minutes; only the identified child SSH
diagnostic was stopped, never the bot. Peer remains pending. Local/GitHub/WSL
parity subsequently passed; final sync skips the unavailable peer.

## 08:41 ET follow-up

Fresh official summary: All Systems Operational, no incidents or maintenance.
Same supervisor21730/runner21744 remain alive. Authenticated12:41:42Z reads
prove zero orders and positions. Runner12:30:52Z schedule recorded completed
maintenance and RESUME_HOLD until12:45:07.375Z (08:45:07 ET). This is earliest
admission after the buffer, not proof that trading has resumed; normal health,
ownership, public-stream and UI gates still apply. Latest12:40:54Z API window:
1.909rps,115 reads, zero mutations/failures/429. An08:31 earnings timeout and
08:37 per-market pregame-unavailable refusals remain visible, so this is not
an error-free-all-endpoints claim. No code/config/restart change.

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
