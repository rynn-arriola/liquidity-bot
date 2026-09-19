# Private stream recheck — September 19, 2026

Operator requested a read-only test. No account-source switch, mutation,
configuration change, deployment or restart was performed.

The existing bounded direct-wire diagnostic used original runtime credentials,
SDK0.1.2 authentication, plain JSON text and the documented private endpoint.

| UTC observation | Subscriptions | Result |
|---|---|---|
| 14:39:04–14:39:49 (45 seconds) | Balance, orders, positions; global scope, unique IDs | 4 frames: 3 order updates, 1 position update; zero decoding failures |
| 14:40:07–14:41:07 (60 seconds) | Balance only | Zero frames; zero decoding failures |

Both completed the handshake and answered initial/final Ping/Pong. No order
snapshot arrived in the first test; neither test delivered balance data. No
account activity was induced. This proves current order/position updates,
not full stream recovery or reconciliation. It supersedes blanket claims that
private positions currently emit nothing. The detailed private guide documents
position updates, not an explicit initial position snapshot promise.

At14:39:34Z production status remained REST/active, runner1211201; process check
confirmed it alive. Complete venue orders/positions were not queried and are
unverified in this check. The diagnostics have no mutation capability.

Keep REST selected. Before any future authorized source switch, verify balance
delivery or review an explicit REST-seeded/reconciled design, complete account
state and sustained event continuity. No hybrid implementation is authorized.

Source: https://docs.polymarket.us/api-reference/websocket/private

Changelog latest remains September17's September18 maintenance notice. The
status page could not be retrieved; no venue-wide health/cause claim is made.

Pre-documentation parity: Windows/GitHub88285b9e clean; WSL0be8b85a and loaded
3fc44a67. Drift is expected from queued next-restart image changes; no deploy.
Verified private recovery copy captured14:40:26Z.
