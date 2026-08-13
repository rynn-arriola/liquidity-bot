# Phase 2 acceptance

Implemented and checked on 2026-08-12 ET. This is an implementation and
read-only transport acceptance, not the multi-day profitability gate.

## Offline acceptance

- 61 deterministic tests passed.
- One shared asynchronous REST gate is used for every attempt.
- Default REST pace is 4 requests/second; configuration above the documented
  20 requests/second ceiling is rejected.
- Only confirmed HTTP 429 responses receive bounded exponential backoff.
- Continuous full books and public trades use one WebSocket event stream.
- Missing incentive programs invalidate eligibility instead of retaining stale
  reward terms.
- Hypothetical fills start behind the visible touch queue and retain their
  `fill_signal` label in SQLite.
- Exit replays test 15/30/60/120 seconds against the full book with a 50%
  depth haircut and taker fee.
- Only attributable `PAID` earnings can calibrate reward yield. `PENDING`,
  `SKIPPED`, and shadow-only account earnings cannot pass that gate.

## Bounded live read-only acceptance

Duration: 45 seconds. Markets were auto-discovered from active pregame
incentive programs.

- Selected rewarded markets: 3
- Full-book snapshots: 57
- Candidate quote observations: 114
- Hypothetical eligible score-seconds: 548.7018
- Public trades observed: 0
- Hypothetical entry signals: 0
- WebSocket reconnects: 0
- Order create/modify/cancel calls: 0

No public trade occurred in this short sample, so no fill or exit result was
claimed. Longer collection is required to measure fill hazard and compare
inventory-age policies.

A final post-rotation regression observed two rewarded markets, 28 additional
full-book snapshots, 56 candidate observations, 165.78492 score-seconds, zero
reconnects, and zero orders. No public trade printed in that short sample.

## Gate status

The Phase 2 evidence gate remains open. It requires several complete reward
days and, because shadow orders cannot receive venue payouts, attributable
`PAID` reward observations from a later tightly controlled live trial. The
current account totals (`PAID` $32.06, `PENDING` $5.39, `SKIPPED` $0.39) were
stored as unattributable context and cannot justify scaling.
