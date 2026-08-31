# Capital guidance terminal-history repair — 2026-08-31

## Symptom and live evidence

The size-13/22-slot Discord card showed a current settled balance but rendered
`Free to withdraw` as `UNAVAILABLE`. At 19:44Z the laptop runner was live and
its account mark was 13 seconds old. The ledger had zero unresolved intents
and zero `UNKNOWN_ABSENT` orders, but the summary reported 59 unknown orders.

All 59 were historical venue-imported rows with positive terminal state: 55
`FILLED` and four `REJECTED`. Their event times ranged from 2026-08-18 through
2026-08-31. There was no active unowned order in that set.

## Mechanism and repair

The summary counted every `venue:` client-order ID forever. That prefix records
where an order row came from; it does not make a positively terminal order
current account ambiguity. The trading ownership gate already had the correct
rule: only active `venue:` rows are unowned live orders, while every
`UNKNOWN_ABSENT` row remains ambiguous until terminal proof arrives.

The summary now consumes one ledger-owned count with the same boundary:

- count every `UNKNOWN_ABSENT` row;
- count `venue:` rows only in `OPEN`, `NEW`, `PARTIAL`, `PENDING` or
  `SUBMITTED` state; and
- ignore terminal `FILLED`, `CANCELED`, `CANCELLED`, `REJECTED` and `EXPIRED`
  venue history.

This changes only local Discord summary classification. It adds no API call,
schema write or mutation, and does not change P&L, withdrawal accounting,
entries, exits, 22-slot scheduling, ownership enforcement or risk gates.

## Verification and edge cases

Three focused summary regressions prove terminal venue history permits capital
guidance, an active unowned venue order still forces `UNAVAILABLE`, and an
owned `UNKNOWN_ABSENT` order still forces `UNAVAILABLE`. Deliberately restoring
the broad historical count failed the terminal-history regression exactly.

The focused execution-ledger, capital-guidance, summary-rendering and live
commissioning suites passed. The native-Windows complete run reached 964 tests:
960 passed and the four expected authenticated-collector constructors refused
native Windows because POSIX host locking is mandatory. The same complete
suite passed 964/964 in an isolated Ubuntu WSL environment on the inactive
desktop in 119.751 seconds.

The original repair was committed as `e02011f`; deployed commit `9001a4b`
includes it and the 22:34Z flat-gated restart loaded it. A live check at
22:39:53Z saw one genuine cancel intent and correctly stayed `UNAVAILABLE`.
After terminal proof arrived, the 22:40:26Z check saw zero unresolved and zero
unknown orders despite the retained terminal venue history, fresh settled cash
and buying power of `$1,406.26`, a `$1,012.00` comfortable balance and
`Free to withdraw $394.25`. The check used the same ledger predicate and
capital function as Discord; it made one separately governed balance read and
changed no trading state.

## Follow-up: scheduled-summary timing

The operator still saw `UNAVAILABLE` in the Discord webhook after the loaded
terminal-history repair. This does not contradict the clean `$394.25` sample.
The runner sends the card every five minutes, and its ordinary cancel flow is
intentionally unresolved until positive terminal proof arrives. A scheduled
post that lands inside those seconds truthfully sees ambiguity even though the
next local state is clean. Repeated order maintenance makes that timing
collision common enough to look permanent.

The follow-up repair leaves the capital predicate unchanged. A routine summary
with fresh balance evidence and current order ambiguity waits for local proof,
rechecking every two seconds for at most 60 seconds. It sends immediately once
proof is clean; at the bound it sends the same fail-closed `UNAVAILABLE` value.
Missing or stale balance evidence is never deferred, and shutdown/critical
summaries bypass the delay. Rechecks read the ledger only: no balance, order or
other venue request is added, and no strategy, ownership, risk, slot, reward,
shutdown or restart behavior changes.

Focused regressions cover a cancel that clears after two seconds and produces
a numeric card, ambiguity that survives the exact 60-second boundary and stays
unavailable, and stale balance evidence that reports immediately. The clean
path asserts the limiter count does not move. Deliberately bypassing the
deferral failed the brief-proof regression exactly. The complete native run
reached 973 tests: every 969 trading/reporting assertion passed and the same
four authenticated measurement constructors refused native Windows because
the required POSIX host lock is unavailable.

Exact staged laptop WSL verification passed 973/973. Commit `0245d31` was
pushed to GitHub, fast-forwarded into the laptop checkout and WSL deployment,
and written to `DEPLOYED_COMMIT`. Command 1 then stopped the old process
gracefully in about nine seconds; its final record said `flat: true`, and an
independent authenticated read at 23:44:39Z proved zero venue orders,
positions, unresolved intents, unknown absences and active unowned orders.

The restarted size-10/24 process is supervisor 1029753 / runner 1029761. It
published the exact 24-owner scope and its first natural scheduled summary ran
the loaded path at 23:52:12Z. The surrounding API windows were 1.270-1.484/s
with zero failures or 429s; the immediately following ledger observation had
zero unresolved and zero unknown rows. Discord's outbound message body is not
persisted locally, so the operator-visible field remains the final rendering
check. No manual webhook fire or extra live API probe was used for acceptance.
