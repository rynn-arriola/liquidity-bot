# API usage audit — September 26, 2026 (read-only)

The operator asked whether the architecture could use fewer API calls: are we using the Polymarket endpoints and WebSocket well? This matters for the 50-order request, since there is about 1.5/s headroom under the shared 4/s host ceiling.

## Where the calls go

Sources: 24 h of `[api-budget]` category counts (201,159 calls, **2.41/s**) plus 3 days of rates (average 2.61/s, peak 3.78/s, 34% of minutes above 3/s, 499 rate-limited calls).

**Account state over REST, about 2.0/s (83%):**

| Call | Calls/s | Share |
|---|---|---|
| `order_retrieve` (exact single-order reads) | 0.47 | 20% |
| `open_orders` | 0.42 | 17% |
| `positions` | 0.39 | 16% |
| `execution_history` | 0.39 | 16% |
| `balance` | 0.30 | 12% |
| `cash_history` | 0.04 | 2% |

**Other traffic:**
- Market/contract/reward data (incentive, market and event batches, discovery): about 0.25/s (10%).
- Mutations (create 0.12/s, cancel 0.01/s): about 0.14/s (6%).
- Order books: zero REST calls; they arrive via the public WebSocket.

## Why REST is still the authority

- **Hybrid mode** (`authority=rest`): the private WebSocket delivers order and position updates reliably. Today it showed `gaps=0`, and the only private-stream recoveries were the manual-close ownership stops.
- **Proof cadence:** POST admission still waits for REST proof, which runs every 5 s and **every 1 s while any position or unresolved intent exists** (`config/rest_polling_policy.json`; the validator keeps normal at 3–5 s).
- **Operator decision (September 22):** after a hybrid incident the operator required REST-baseline behavior.
- **Venue gaps** (PRIVATE_WEBSOCKET_RESEARCH_2026-09-26): balance frames never arrive, and the bot does not subscribe to `ORDER_SNAPSHOT`.

## Options, largest saving first (estimates; each is a timing/safety change needing approval)

1. **Relax the position-time proof cadence from 1 s to about 3 s while the private stream is healthy.**
   - Fills already trigger an immediate ("urgent") REST proof from the WebSocket notice.
   - This is expected to cut the proof trio (open orders, positions, history) by about a third: roughly **0.4/s** saved.
   - Risk: a fill the WebSocket misses is noticed 2 s later.
2. **Balance every 10 s instead of about every 3.4 s** (equity freshness is 15 s): about **0.2/s** saved. There is no WebSocket substitute because the venue sends no balance frames.
3. **Fewer exact order reads.**
   - Investigate why `order_retrieve` is 0.47/s when about 95% of proofs come from the bulk listing: reducer/hard-exit checks, terminal confirmation, and the `covered_fill` shortcut, which covered 0 of 67 order events today.
   - Possibly **0.1–0.3/s**.
4. **Subscribe to `ORDER_SNAPSHOT`** on connect/reconnect, and review the SDK 1.0.2 upgrade. This improves WebSocket recovery; it does not directly save REST calls.

**Together, options 1–3 could save about 0.7–0.9/s (30–35%)**, enough to cover 50 orders (+0.2/s) with margin.

## Deep check (operator: "their documentation is wrong, don't trust that")

The official docs were compared with our code and **measured** behavior only.

| Area | Docs claim | Measured / our code |
|---|---|---|
| Real venue HTTP 429s | 20/s per key | Only **4** `[api-429]` responses Sept 24–26, all account reads on the safety lane with retry-after 0; average 2.6/s, peaks 3.8/s. The `rate_limited=` counters in `[api-budget]` (499 in 3 days) are mostly our own shared-governor throttling, not venue refusals. **Our self-imposed 4/s cap is the real constraint.** |
| Private WS orders/positions | Real-time | Reliable today: `gaps=0`; fills arrive and trigger urgent proofs. |
| Private WS balance | Snapshot + updates | **Never received** (September 19 test, 60 s). A 60 s window may simply have had no ledger change, so a longer test spanning a fill is needed before concluding. |
| `ORDER_SNAPSHOT` | Initial open orders | Never subscribed by the bot, so untested. |
| Modify / batched create/cancel/modify | Up to 20 per call; modify = cancel-replace | **Unused.** Each reprice is cancel → confirm → create, which costs 2 calls and leaves a gap with no order resting. Untested live. |
| Activities filters (`types`, `marketSlug`, sort) | Available | We read the newest activity page every proof (0.39/s). |

**Evidence-based plan** (each a separate, reviewable change; test live before trusting any doc claim):
1. **Raise the internal API cap gradually** (4 → 5/s), watching real 429s. This alone covers 50 orders; it is a config/code constant.
2. **Relax the with-position proof cadence from 1 s to 3 s** while `gaps=0`; the WebSocket already delivers fills immediately. About 0.4/s saved.
3. **Balance every 10 s instead of about 3.4 s:** about 0.2/s saved.
4. **Read-only WebSocket test** of `ORDER_SNAPSHOT` and balance over a period that includes fills (needs operator approval). Only if proven should the WebSocket replace some REST reads.
5. **Modify for reprices:** a flagged live trial measuring outcomes via the order stream. This saves a call per reprice and removes the resting gap.
6. **Investigate the `order_retrieve` sources (0.47/s):** why exact reads happen despite 95% bulk-listing proofs.
