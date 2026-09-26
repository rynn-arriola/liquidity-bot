# Event-evidence margin for contract reads — September 26, 2026

The operator asked: "Do all remaining with same process". This is item 2, "game-status drop-outs". Pushed; it loads at the next Command 1.

## Evidence (MEASURED, production log September 26)

- 3,924 `[runtime-recovery] component=market_incentive_refresh:<slug> … event pregame state is unavailable` lines today. They arrive in whole contract batches of up to 8 markets (for example 14:43:08Z ×8 and 15:25:08Z ×8).
- The `[event-evidence-unfresh]` diagnostic (live since 13:49Z) printed **0 lines** over the same period. It runs inside `fetch_markets_by_slugs` right after `refresh_event_states`, and logs any requested market whose evidence is not fresh and pregame.
- The API queue was quiet at the failures: `wait_max` ≤ 0.71 s, 0 rate-limited.

## Cause (INFERRED by elimination)

The runner gate `MarketMetadata.event_allows_entry(now)` and the diagnostic's `event_entry_allowed` test the same things: not started, proven pregame, the market is a verified event member, and evidence under 30 s old. The only difference is the clock:
- the diagnostic checks at refresh time;
- the gate checks at `completed_at`, after the further `fetch_incentives` await.

`refresh_event_states` reuses evidence up to 29.99 s old, so a batch that reuses evidence near the limit passes the refresh but fails the gate a moment later. A failed batch clears reward eligibility and backs off its contract refresh (60 s, growing to 900 s on repeats). That fits the earlier finding of whole batches losing eligibility for about 60 s.

About 490 of the roughly 5,760 daily 60-second batch refreshes failing (about 8.5%) implies about 2.5 s between the reuse decision and the gate, on average. The diagnostic cannot see this interval directly.

## Fix

- `refresh_event_states(…, min_remaining_seconds=0.0)`: evidence is reused only while it is younger than `30 - min_remaining_seconds`.
- Contract reads (`fetch_markets_by_slugs`, `fetch_market`) pass `CONTRACT_EVIDENCE_MARGIN_SECONDS = 10`, so evidence older than 20 s is refetched first.
- The decision-pass guard refresh and the zero-I/O admission recheck are unchanged. The **30 s freshness rule at use is unchanged**, and the fix adds no new entry authority.
- Cost: at most one extra `events.list` batch call (≤100 parents) per 8-market contract batch whose evidence is 20–30 s old. That is roughly 0.02 calls/s against a 2 calls/s budget.
- The error now names the failed condition: `(event_started | no_event_evidence | not_proven_pregame | evidence_age=Ns)`. The text prefix is unchanged for existing greps.

## Verification

- `test_contract_reads_refetch_evidence_that_would_expire_before_use`:
  - decision-pass reuse of 25 s evidence makes no extra call;
  - a batch read refetches it and stays valid 9.9 s later;
  - 5 s evidence is reused, and 21 s evidence is refetched.
- `test_entry_block_reason_names_the_failed_condition`: all four reasons.
- Deliberate reversals:
  - margin 0 (old behavior) fails 1 test;
  - applying the margin to every refresh (more API calls) fails 1 test.
- Full WSL suite: 2,360 PASS.

## Acceptance after Command 1

`event pregame state is unavailable` lines should fall by more than 90%. Any left over name their reason, and a reason other than `evidence_age` points to a different cause.
