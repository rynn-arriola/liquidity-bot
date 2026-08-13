# Phase 1 acceptance

Completed read-only on 2026-08-12 ET using the installed `polymarket-us 0.1.2`
SDK. Credentials were injected for the process and were neither copied nor
printed.

## Offline contracts

- 39 tests passed.
- Incentive pagination follows `nextPageToken` and rejects repeated tokens.
- Empty or malformed collections are not treated as valid empty state.
- Market metadata uses the nested `market` wrapper and `gameStartTime` clock.
- Full books use the nested `marketData` wrapper and preserve complete depth.
- Earnings preserve `PAID`, `PENDING`, and `SKIPPED` separately.
- Private stream decoding preserves decimal positions and explicit empty snapshots.
- Unknown WebSocket schemas fail closed.

## Bounded live read-only check

Market: `aec-setkameua-kovvea-demciva-2026-08-13`

- Applicable phase: `day_of`
- Pool: `$35`
- Target size: `5,000`
- Discount factor: `0.5`
- Hypothetical touch size: `2` contracts
- Bid eligible: yes, score `2.0`
- Offer eligible: yes, score `2.0`
- Full-book market snapshots received: `1`
- Authenticated account-balance snapshots received: `1`
- Order submissions/modifications/cancellations: `0`

Authenticated earnings totals observed by status:

- `PAID`: `$32.06` across 86 rows
- `PENDING`: `$5.39` across 50 rows
- `SKIPPED`: `$0.39` across 4 rows

These figures validate adapter composition. They are not a claim that this
market or strategy has positive expected value.
