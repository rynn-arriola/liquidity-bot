# Positioned inactive-owner top-up amplification — 2026-09-01

## Incident

After the inactive-market lifecycle repair loaded, lifecycle top-up requests
rose from the prior 1–8 per hour to 19, 23 and 24 in the 16:00, 17:00 and
18:00 UTC hours.  Across the later 16:00–18:00 sample, 28 of 60 top-up plans
added no market and 38 released no owner.  Raw PLACE/CANCEL activity also rose,
although that count is not normalized for available markets or bound side-hours
and therefore remains a warning rather than causal reward evidence.

The exact trigger was one non-open owner with a real `+10.0000` position.  Each
successful 60-second contract refresh requested an urgent lifecycle top-up
before placing that owner into inactive-market wait.  The scan could not
release the risk-bearing slot, so the same unchanged position repeatedly drove
full discovery.  This was an interaction inside the lifecycle repair, not a
32-slot API limit or a selector-supply failure.

## Repair

Positive non-open metadata now tries to establish the exact inactive-position
wait before requesting replacement work.  A positioned owner that meets the
existing basis, ownership, ambiguity and risk gates remains inside configured
scope, keeps one slot and full exposure reserved, and performs only its normal
one-minute metadata recovery probe.  It does not request discovery while its
slot cannot be released.

The other lifecycle transitions are unchanged:

- a flat non-open owner still requests immediate top-up and can be released
  only through fresh account, exact-market and replacement proof;
- authoritative position zero leaves inactive wait and requests immediate
  top-up;
- fresh open metadata leaves inactive wait and restores the existing reducer
  or IOC path;
- unknown or failed metadata never proves closure or releases ownership;
- ordinary under-capacity selection remains available on its normal schedule.

No pricing, size, ranking, reward economics, inventory clock, risk limit,
capacity, mutation authorization or API-lane rate changes.

## Verification and review

The focused three-transition slice passes.  Deliberately restoring the old
request-before-wait ordering makes the positioned-owner regression fail on the
unexpected lifecycle request; restoring the repair passes.  The complete
pinned WSL suite passes 1,053/1,053.

The permanent edge audit covered flat/nonflat, first/repeated refresh, reopen,
authoritative flat transition, missing/unknown metadata, working and unknown
orders, unresolved intents, exact basis, full/underfilled capacity, shutdown
and restart.  The cumulative post-`v1.1.0` review retains the previously proven
business-output boundaries: healthy-market selection, quote construction and
position exits use the same paths; this repair only removes discovery work that
cannot change scope.

## Live acceptance

Loaded commit `20812787e` started as runner `1254107` at 18:54 UTC.  The known
`+10.0000` owner entered inactive wait at 18:54:49Z with one slot reserved.
Through 18:59:03Z it produced no mutation intent and no lifecycle top-up; the
only refill was the ordinary scheduled scan at 18:56:40Z.  Startup recovery
flattened the two ordinary residual positions and retained only the known
venue-bugged owner.

The runner rebuilt 36 orders across 18 markets with zero `UNKNOWN_ABSENT` rows
or unresolved intents.  Its first two API windows completed 355 and 159
attempts at 2.460/s and 1.637/s, both with zero failures, 429s or cooldown and
`max_waiting=1`.  This accepts the exact amplification repair.  Natural reopen
and authoritative-flat release remain uninduced; the position must never be
called flat or removed from the ledger merely to free its slot.
