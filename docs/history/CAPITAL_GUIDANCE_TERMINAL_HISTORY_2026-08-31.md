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

The repair is reviewed and committed locally but is not yet pushed, deployed
or loaded. The
laptop supervisor and runner were not stopped or restarted.
