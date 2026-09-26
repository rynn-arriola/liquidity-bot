# Private WebSocket research — September 26, 2026

Operator asked why the private WebSocket is only partially working. Read-only
research: project records, live log since the 23:54Z restart, installed SDK,
official docs, changelog, status page and the official SDK repository. No
diagnostic socket, venue mutation, deployment or restart.

## What "partial" means now

Live run 23:54Z–01:4xZ: `[hybrid-account] state=receiving`, 1,614 order events,
71 position hints, 0 gaps; none of 22 `[runtime-recovery]` events concerned the
private stream. Orders and positions arrive; **no order snapshot** and **no
balance** frames arrive (September 19 raw test: balance-only subscription, 60 s,
zero frames of any kind). Past full-silence episodes exist (September 13 retest
zero application frames; August 29 false silence). REST remains authority.

## Findings

1. **Order snapshot — explained, ours to fix.** The order subscription carries
   updates only; a snapshot requires a separate `SUBSCRIPTION_TYPE_ORDER_SNAPSHOT`
   subscription ending with `eof: true` (official private docs; SDK commit
   `83128f4` 2026-09-17 "correct order snapshot subscription types" and README).
   The installed SDK 0.1.2 and the bot subscribe only `ORDER`, `POSITION`,
   `ACCOUNT_BALANCE`, so no snapshot is ever requested.
2. **Graceful server close was silent in SDK 0.1.2.** Commit `87385ad`
   (2026-09-23, SDK 1.0.2) adds `close` emission when the message loop ends
   normally; before it, a graceful close emitted neither `close` nor `error`.
   The bot also relies on its own transport Ping every 60 s (10 s timeout), so
   such a close is detected within about a minute, not 180 s. Upgrading to
   1.0.2 needs review: 1.0.0 carries a BREAKING market response type change.
   Commit `c04cb1a` (websockets ≥13 compatibility) does not affect our
   websockets 17.1.
3. **Balance — venue side, not explained publicly.** Current SDK `private.py`
   sends the same camelCase subscription and maps the same balance keys; the
   bot parses every raw message including all balance key variants (camel and
   snake case). No SDK commit, changelog entry (through v0.0.92, 09-21) or public
   issue addresses balance delivery. Status page: 09-11 "High latency on balance
   ledger API calls" (~19 h) and 09-13 "Position streaming delayed" (~10 min),
   the latter matching the 09-13 zero-frame retest.
4. **Heartbeats.** Server sends `{"heartbeat": {}}`; the official SDK only
   emits an event and never replies, so no reply is required. ALB idle timeout
   is 10 minutes; the bot's 60 s Ping keeps traffic flowing.
5. **Docs inconsistency persists:** the overview says snake_case
   (`request_id`, `subscription_type`); detailed pages and every SDK version send
   camelCase. Subscriptions for orders/positions work in camelCase.

## Options (operator decisions)

- Keep hybrid with REST authority (current, correct given venue behavior).
- Add an `ORDER_SNAPSHOT` subscription after connect/reconnect (bot change,
  small; REST proof stays authoritative).
- Upgrade SDK 0.1.2 → 1.0.2 after a compatibility review, or port the close
  fix locally.
- A bounded read-only diagnostic (needs approval; the private-availability
  collector is stopped by operator request): 60 s `ORDER_SNAPSHOT`, and balance
  subscribed in camelCase and snake_case.
- Operator contacts Polymarket support about balance delivery with timestamps.

Sources: docs.polymarket.us/api-reference/websocket/private and /overview;
docs.polymarket.us/trader-guide/connection-issues; docs.polymarket.us/changelog;
status.polymarketexchange.com; github.com/Polymarket/polymarket-us-python
(commits 83128f4, c04cb1a, 87385ad; README; private.py); pypi.org/project/polymarket-us.
