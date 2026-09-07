# Reporting, recovery and $90 HARD deployment — September 6 ET

The operator authorized restart with the prepared changes, then Command 1.
This superseded the earlier no-restart hold, without authorizing a loss reset,
force-stop, different strategy, ledger repair or automatic future restart.

## Stop and deployment gates

The stop helper signaled supervisor 394690 first and runner 394699 gracefully.
The runner exited `operator_sigterm`, `flat=true` at September 7 03:29:22Z.
Authenticated reads at 03:30:27Z proved zero open orders and zero positions;
the ledger had zero unresolved/unknown/working state. Old processes were absent.

Windows/GitHub were clean at `43e9db826e1ac00f513db72d788131893e84aeb0`.
Parity captured a verified stopped-runtime online backup at 03:29:37Z,
282,054,656 bytes, SHA prefix `988fda1e3b29`; the desktop peer was unavailable.
The clean WSL checkout fast-forwarded from `6cb64223e` through a Git bundle
to that exact pushed commit; the deployment marker now names it. Existing
untracked deployment/packaging artifacts were preserved.

The combined pre-deploy diff and dedicated edge audit retained the earlier
regression/reversal evidence. Reporting preserves missing-data refusal and
signed cash adjustments; private recovery preserves event ordering, ownership,
terminal proof and parent cancellation; the only risk-policy change is the
operator's $90 reference. No schema, baseline or unrelated strategy changed.
All 114 tracked source/test/report files matched Windows, WSL and committed
content after line-ending normalization. The native production suite passed
all 1,138 tests in 78.193 seconds, exit zero. No new code was written this turn.

## Restart and actual outcome

A fresh independent gate at 03:32:29Z again proved zero venue orders/positions
and zero unresolved/unknown/working ledger state. The exact configured HARD
was $90. The ordinary launcher restarted `auto 10 32`, supervisor 461139 and
runner 461149, with the independent dashboard retained.

Startup began at 03:32:35Z. Its scan found 87 viable markets in two event pages.
At 03:34:05Z it inherited the existing ET-day loss $104.2022 against HARD $90
and tightened the unchanged loss ratchet from 20 to 10. It then stopped for
`EMERGENCY_EQUITY_HARD_STOP`, with zero orders submitted and `flat=true`, at
03:34:14Z. The supervisor exited after 99 seconds and did not restart.
Both processes were independently absent afterward.

Final authenticated reads at 03:34:50Z proved zero orders, zero nonzero positions
and settled cash $1,086.6591; ledger unresolved/unknown/working counts were zero.
The day is **September 6 Eastern**, despite UTC being September 7. Its persisted
opening cash/equity is $1,090.8613. Completed non-trading cash is the $100 goodwill
credit. Thus $1,086.6591 - $1,090.8613 - $100 = **-$104.2022 trading**.
The freshly restarted account cache independently agreed, with open mark zero.
Restart did not erase losses or turn the goodwill credit into trading profit.

## Reporting and remaining acceptance

The updated shutdown summary at 03:34:13Z logged `capital_proven=True` and
`capital_reason=ok`; this accepts the fresh-capital path but is not proof of
external Discord rendering. The daily table fix is deployed for the independent
hourly reporter; historical messages were not edited. A normal later table
and routine five-minute summary still need natural operator-visible acceptance.
Unexpected-outage recovery remains natural-fault acceptance only; none was induced.

The bot is stopped, not paused awaiting midnight. HARD remains terminal and
the supervisor will not automatically resume on ET rollover. A later authorized
start must pass the ordinary fresh gates; no stop-policy change or delayed
restart automation was requested or created.
