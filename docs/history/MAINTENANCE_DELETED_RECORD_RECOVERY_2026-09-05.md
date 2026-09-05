# Deleted Statuspage maintenance-record recovery — 2026-09-05

## Incident and cause

The size-10/32 runner correctly evacuated before Polymarket US production
maintenance and stayed flat. It did not resume after the scheduled 06:00 ET
end plus the selected one-hour buffer. At 12:25 UTC the live process was still
in scheduled-maintenance pause with zero orders and zero positions even though
authenticated Retail reads were healthy.

The cached production row `srky0lxrfrqr` still said `scheduled`. Polymarket's
current upcoming and active endpoints returned successful empty/current data,
the exact row endpoint returned HTTP 404, and the successful complete history
endpoint no longer contained that ID. The old coordinator intentionally
treated any missing exact row as unknown, so it had no completion boundary and
could hold forever. This was a local recovery-state defect triggered by the
provider deleting a maintenance record, not an order-cap, market-selection or
account-risk refusal.

## Repair and safety boundary

The coordinator still prefers an exact row and any official completed update.
It now recognizes one narrower state, `record_absent_after_end`, only when a
previously verified cached window has reached its planned end, the exact
endpoint positively returns 404 and a complete history read succeeds without
the ID. Normal minute polls must also have completed the upcoming and active
reads. The planned end becomes the local boundary; the existing one-hour delay
and three complete authenticated order/position/balance cycles remain
mandatory. A later official completion replaces the local state and can only
extend the hold.

Before the planned end, a non-404 exact failure, an unavailable/malformed
history response or any failed current-list read remains unknown and cannot
resume entries. The public feed still cannot prove an order cancelled, a
position flat or the venue healthy.

## Verification

Focused coverage proves deleted-after-end recovery, deleted-before-end
failure, non-404 failure, preference for an official late completion, cache
round-trip and startup execution of all three authenticated health cycles. A
deliberate reversal that moved eligibility 365 days past the planned end made
the key regression fail, then it passed after restoration. The focused group
passed 13 tests. The complete POSIX run against the final review tree passed
all 1,121 tests; the native Windows run reached the same 1,121 tests with only
the four documented POSIX-lock constructor refusals.

The final code was also exercised read-only against the live public responses:
the deleted September 5 row became `record_absent_after_end`, retained its
10:00 UTC planned end, calculated 11:00 UTC resume eligibility and left the
announced September 6 row scheduled. No venue mutation was made by that check.

## Stop and deployment gate

The operator authorized repair and restart. Supervisor `3272085` and runner
`3272094` stopped gracefully. The terminal record reported `flat=true` and an
independent authenticated snapshot proved zero open orders and zero positions;
the ledger also had zero working-order rows, zero nonzero-position rows and
zero unresolved or unknown ownership.

Commit `4654479c198d67e008877a9b6ce2b2ec2f562a38` was pushed and fast-forwarded
into the exact WSL production checkout. Changed source and test hashes matched
the review tree, `DEPLOYED_COMMIT` was updated with mode 600 intact, and all
1,121 tests passed under the production POSIX runtime.

Fresh authenticated pre-start evidence at 12:47:05Z again proved zero open
orders and zero positions; the ledger proved zero working, unknown, unresolved
or nonzero-position rows. The explicit `auto 10 32` restart launched supervisor
`3797873` and runner `3797883`. Startup recognized the deleted row only through
the reviewed `record_absent_after_end` state and performed authenticated health
cycles 1/3, 2/3 and 3/3 before reporting `phase=NORMAL` and creating any entry.

Authenticated acceptance at 12:52:33Z found 29 open orders across 29 markets
and zero positions. A later exact ledger sample found 30 working orders with
zero `UNKNOWN_ABSENT`, unresolved-intent or nonzero-position rows. The first
316.7-second API-budget window completed 411 attempts, including 34 mutations,
with zero failures, rate limits or cooldown; queue depth never exceeded one.
