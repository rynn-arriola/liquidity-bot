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
