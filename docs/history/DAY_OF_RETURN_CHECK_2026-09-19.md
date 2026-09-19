# Day-of availability check — September19, 2026

At14:13:07Z (10:13 ET), one bounded authenticated exact-symbol incentives
check through the shared0.25/s report lane returned all three requested
markets with day_of and live periods: darts Luklit/Natasp, Setka Dirvit/Coviur,
and WTT Flacot/Tiali. This proves day_of is available on these markets now,
not universal restoration or a permanent reward-policy promise.

The live runner's most recent complete market-measurement record at14:06:01Z
contained20 evaluated candidates, all day_of. This is the evaluated-book
subset, not a count of every venue reward market. Local read-only order
attribution at14:13:07Z showed20 working entry orders labelled day_of; this
is ledger evidence, not a fresh authenticated order-count proof.

Yesterday's16:44:58Z September18 sample had136 daily_event and zero day_of
reward rows in138 six-hour moneylines. That observation never proved endpoint
failure, permanent removal, or that daily_event was newly introduced. Today's
exact results show the day_of supply is present again and the existing
day_of-first policy can already use it without a code change or restart.

The official changelog was rechecked; its latest entry was the September17
notice for September18 maintenance, not a day_of restoration announcement.
The live status page could not be retrieved through the web reader, so no
status-page health claim is made. No orders, positions, settings or running
code were modified. Pending summary cosmetic fixes remain next-restart-only.

## Broader league check beginning14:18:23Z (10:18 ET)

The existing read-only `scripts/period_mix.py` ran once through the shared
report lane:596 in-horizon moneylines within the24h discovery window;
495 markets returned incentive rows.101 had no returned incentive record,
not proof of an API outage or permanent reward absence. Among returned rows,
397 markets carried day_of;98 college-football markets carried live only.
167 markets had an applicable pregame period at check completion, all day_of;
this is reward timing, NOT full book/lifecycle/entry eligibility. The remaining
advertised day_of rows need not already be within their six-hour window.
No returned row carried daily_event in this snapshot.

| League | Markets advertising day_of | Live-only |
|---|---:|---:|
| cfb |26|98|
| setkameua |114|0|
| atp |34|0|
| setkamemd |31|0|
| itfwo |28|0|
| cs2 |22|0|
| itfme |22|0|
| setkamecz |19|0|
| lol |15|0|
| mlb |15|0|
| wta |13|0|
| ufc |12|0|
| atpdb |11|0|
| nhl |7|0|
| modus |6|0|
| pdcdarts |5|0|
| dota2 |3|0|
| setkawoua |3|0|
| wtadb |3|0|
| wnba |3|0|
| valorant |2|0|
| odcup |1|0|
| t20icr |1|0|
| wtt |1|0|

All24 league codes represented in the incentive responses had at least one
day_of market. This does not cover leagues with no upcoming in-horizon games
or only missing incentive responses. No blanket every-league restoration
claim is warranted. No trading/source/risk change, extra book scan or restart.
