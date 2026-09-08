# Pregame clock conflict repair — September 8, 2026

## Incident and evidence

At approximately 11:20–11:23 ET, the bot was still treating
`aec-lol-vfb-cgn-2026-09-08` as pregame. The Retail primary moneyline row said
`gameStartTime=2026-09-08T16:00:00Z`, but its generated question said the event
was scheduled for **15:00 UTC**. Sibling spread/total markets and the public
event page also used 15:00 UTC. The primary and event-wrapper structured clocks
were therefore one hour late.

Execution evidence proves this was an entry-authority defect rather than an
old position being reduced: the bot bought 10 at 0.18 at 15:04:18Z, sold 10 at
0.18 at 15:21:20Z, then placed a new buy for 10 at 0.18 at 15:23:05Z. Existing
code consumed only the later structured clock.

The bot was gracefully stopped before modification. Its terminal record was
flat, and authenticated post-stop proof at 15:29:33Z found zero venue orders,
zero positions, zero unresolved intents, zero unknown orders and zero local
working rows. No force kill or manual state fabrication was used.

## Repair and scope

The market adapter recognizes only the venue's exact generated English UTC
schedule form, parses it without locale dependence, and uses it only when it
is earlier than the still-required structured `gameStartTime`. The question
clock can never extend the pregame window or substitute for missing structured
metadata. A recognized but invalid calendar/clock value fails closed.

This single normalized `MarketMetadata.game_start` feeds initial discovery,
exact contract refresh, reward-phase selection, runtime entry eligibility and
the existing pre-kickoff exit deadline. The repair adds no API request,
subscription, mutation, persistent state or concurrency. Size 10 / 32, reward
priority, the 70% entry-quality filter, $90 HARD, pending-order tiers, exposure,
pricing, quantity, ownership and exit rules are unchanged.

The conservative tradeoff is intentional: after a genuine postponement where
the question remains stale and structured time moves later, the bot stops at
the earlier timestamp and may give up reward time. It cannot use the conflict
to trade longer or enter a possibly live game.

## Verification and review

Six focused regressions cover the exact VfB/CGN direct-fetch incident,
event-based discovery, an earlier structured clock, absent structured time,
invalid recognized date, and both 12:05 AM/PM conversions. Reversing the
effective clock from `min` to `max` made the exact incident test fail, then the
restored implementation passed. Adapter and selector suites passed 101 tests;
the final pinned Python 3.12 suite passed **1,208 tests in 118.817 seconds**.

The edge review covers earlier/equal/later and missing question clocks,
malformed recognized values, midnight/noon, exact-kickoff rejection, empty and
full selection, partial/unknown order state, ownership, reducer and shutdown
authority, restart reconstruction, API load, capacity and rollback. The
adapter is pure and stateless, so restart derives the bound again from fresh
market responses. Removing this repair reintroduces the proven incident;
older rollback code is unsafe for a row with this exact clock conflict unless
that market is excluded or trading remains stopped.

## Deployment and live acceptance

Reviewed release **2193e0e85** was pushed before the stopped WSL checkout was
fast-forwarded. The exact native checkout then passed all **1,208 tests**; only
after that success did `DEPLOYED_COMMIT` advance to the same full hash.

The fresh 15:59:00Z authenticated start gate proved zero venue orders,
positions, unresolved intents, unknown orders and local working rows. Normal
`start_commissioning.sh auto 10 32` launched supervisor **1922974** and runner
**1923002** with every configured strategy/risk setting unchanged.

The first live selector found 33 viable markets, then ordinary lifecycle
replacement retained the exact 32-owner scope. A production read-only probe at
16:02:34Z derived `game_start=15:00Z` and `phase=live` for the known VfB/CGN
market. At 16:05:57Z authenticated acceptance found 38 venue orders across 29
other markets, two positions under normal management, zero unresolved/unknown
state and 38 matching local working rows. The offender had zero open order and
no ledger event after its 15:28:57Z shutdown cancellation. Thus ordinary
trading resumed while the known live market remained excluded.

The first 182-second API window completed 445 attempts, including 29 creates,
at 2.445/s with zero failures, 429s or cooldown and maximum queue depth one.
No fill, live game or venue failure was induced. Future clock conflicts and
genuine postponements remain natural observation paths.

## Follow-up: agreeing schedules do not prove pregame

At 16:16:41Z a read-only audit compared 62 entry intents across 32 markets
against fresh Retail metadata, with no entry at or after those timestamps.
All 120 deployed source/test files matched `2193e0e85`. This proves adherence
to the supplied clocks, not that the games had actually not started.

The operator subsequently identified Matteo Mutti / Daniele Pinto and Vincent
Picard / Tugay Yilmaz as live, then reported all held games live. Fresh raw
market responses give both named WTT matches `gameStartTime=21:00Z` and exact
question schedules of 21:00 UTC. Both therefore normalize to 17:00 ET, with no
question/structured disagreement for the existing repair to tighten. The
timestamp parser preserves UTC offsets and the selector compares aware UTC
datetimes; no local UTC/ET conversion defect was found. Actual live status is
operator-reported here; the raw market responses have no live/score field and
an independent actual-start source has not yet been verified.

Durable owned-entry fills prove Mutti bought 10 at 0.57 at 16:04:35Z, followed
by a non-owned sell of 10 at 16:12:55Z and another owned entry buy of 10 at 0.56
at 16:16:50Z. Picard sold 10 at 0.82 at 16:03:42Z, reduced flat at 16:17:06Z,
then made a new entry buy of 10 at 0.68 at 16:18:56Z. Non-owned closing fills
followed at 16:19:20Z. Thus manually closing an owner does not prevent re-entry
while its supplied start remains in the future. Event retrieval using the
market slug failed, so no event-level live signal was established by this
probe; correct event identity must be resolved before selecting a repair.

The operator requested stop, then force-stop and manual position handling.
The normal stop had already terminated supervisor and runner, with terminal
`operator_sigterm` / `flat=false` at 16:22:11Z; process absence was reconfirmed
at 16:22:59Z, so no force kill was needed. The 16:22:44Z authenticated snapshot
still had Lajovic/Landaluce -10, Vivarelli/Georgieva +8.62 and Mutti/Pinto +7.50,
with three corresponding closing orders. The operator handles those remaining
positions; this task made no order mutation beyond the requested normal
shutdown and did not restart or change trading code. Keep stopped pending a
reviewed actual-live detection remedy and new restart authorization.

## Critical root cause confirmed from official event state

A bounded catalog read resolved actual parent identities from nested market
membership (event slugs omit the market's `aec-` prefix in these samples):
Mutti/Pinto event 105657, Picard/Yilmaz 105909, Vivarelli/Georgieva 105913,
Lajovic/Landaluce 103491 and Vasyl/Yaroslav 106083. Never infer that prefix rule
universally; preserve observed parent identity.

At the event response update time **16:23:47Z**, the first three explicitly
reported `live=true` and `ended=false`:

| Event | Period | Score | Still-reported scheduled UTC start |
|---|---|---|---|
| Mutti/Pinto | S4 | 10-12, 11-3, 10-12, 8-6 | 21:00 |
| Picard/Yilmaz | S5 | 9-11, 11-9, 11-2, 9-11, 5-4 | 21:00 |
| Vivarelli/Georgieva | S3 | 11-9, 11-13, 8-4 | 22:10 |

The same positive signals exist on the event, `eventState`, and
`metadata.gameState`. In contrast, each `marketGroups[].live` was false, so
that unrelated field must not override actual event state. Lajovic's response
said period `NS` with no positive live signal; Vasyl's said `live=false`, empty
period and `0-0`. Those two were not independently proven live by this read.

The official [Retail Get Event By Slug contract](https://docs.polymarket.us/api-reference/events/get-event-by-slug)
documents event `live`, `ended`, `score` and `period` separately from schedule.
`AsyncReadOnlyGateway.list_upcoming_sports_markets` currently flattens each
event into `_market(row)` and discards all parent state. `MarketMetadata` has
no event identity or started/live field. Direct/batch metadata refresh and
`RewardMarketSelector._catalog_market_is_executable` therefore cannot use the
event signal; the gate tests only future clock plus normal market eligibility.
The runtime's pregame and exit deadline checks also use `game_start`, and
shutdown can park a reducer because the same incorrect time appears distant.

At **16:26:41–16:26:49Z**, fresh exact-event reads were passed through the
unmodified deployed `_market` and actual catalog eligibility method. All
three returned **accepted=true while event.live=true**, with S4/S5/S3 and the
same future starts. This is a read-only reproduction, not a live placement.
It confirms the bot defect independently of the operator's screen report.

Recommended repair scope: carry observed parent identity and validated event
state through discovery and refresh; actual started/live/ended evidence must
override future schedules; preserve started evidence through later missing or
regressed samples and restart; revoke/cancel building orders, prevent re-entry,
and invoke existing exact risk-reducing exit handling. Test fresh/ongoing
selection, cached selection, boundary transitions, missing/malformed/stale
fields, conflicting copies, shutdown and recovery, while bounding incremental
event reads under the existing shared API governor. A missing `live` field or
`0-0` score alone must not be treated as reliable not-started proof. The
operator's latest request is investigation; no trading implementation,
deployment, extra cancellation or restart was performed.

After the operator's manual closures, the **16:27:40Z** authenticated snapshot
proved zero open orders and zero positions. The bot remains stopped.
