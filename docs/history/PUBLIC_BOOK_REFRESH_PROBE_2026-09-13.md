# Stopped public WebSocket refresh probe — September 13, 2026

## Scope and authority

The operator asked whether ongoing book refresh could avoid REST, then
authorized a test. This authorizes read-only verification, not implementation,
deployment, trading, restart, source promotion or risk changes. The bot stayed
stopped on deployed `927676d14`; the last authenticated flat/clear proof remains
17:25:11Z. No account query was made by this probe.

The ignored `.local-tools/public_ws_refresh_probe.py` used the installed
official Python SDK0.1.2 on the laptop WSL runtime and original credentials,
with no secret output. It subscribed only to full public books for the nine
markets selected during the failed17:20Z validation run. Those symbols define
test scope, not permission to trade them or a market-count cap.

Before and throughout the probe, process checks required no bot supervisor,
runner or maker-lab trader. An HTTP request hook refused **every HTTP request**;
no REST discovery, account, book or mutation call was permitted. Overall timeout
was120s, connection timeout10s, per-round snapshot collection3s, teardown5s.

## Method

One public connection subscribed to the nine markets, then stayed open for12s
to observe quiet books. Five rounds unsubscribed the old request ID and sent
one fresh full-book subscription ID on that same connection, five seconds apart.
Only messages matching the new request ID and exact market scope counted.
Normal full-book parsing validated levels/state; depth fingerprints identified
unchanged books. Receipt timing began at each subscribe send, not connection
setup. After five rounds, the socket was deliberately closed; a new connection
subscribed to the same nine markets once. This is a clean reconnect test, not
an induced production outage or proof of all abrupt-disconnect cases.

The installed SDK implements
`unsubscribe({requestId})` and full-book `subscribe_market_data(requestId, slugs)`.
Use that observed camelCase interface and the exact
[Retail markets contract](https://docs.polymarket.us/api-reference/websocket/markets).
The generic [overview](https://docs.polymarket.us/api-reference/websocket/overview)
still shows inconsistent snake_case examples; do not copy that shape over the
working SDK/endpoint-specific Retail contract.

## Measured results

Test ran17:30:54Z–17:31:32Z, elapsed38.31s. All seven rounds returned all nine
books:63 required market/round results. After the initial12-second observation,
three markets had no book message within the previous five seconds despite
52 application book messages across the full scope.

| Round | Books | Quiet before / refreshed | Unchanged depth | Median / max response ms |
|---|---:|---:|---:|---:|
| Initial | 9/9 | 0/0 | — | 44.99 / 50.60 |
| Same socket1 | 9/9 | 3/3 | 8 | 48.78 / 49.48 |
| Same socket2 | 9/9 | 5/5 | 9 | 58.27 / 58.78 |
| Same socket3 | 9/9 | 5/5 | 8 | 44.47 / 47.87 |
| Same socket4 | 9/9 | 6/6 | 8 | 59.77 / 79.98 |
| Same socket5 | 9/9 | 6/6 | 8 | 58.45 / 58.77 |
| New connection | 9/9 | 0/0 | — | 36.20 / 36.59 |

There were two connections,12 outbound WebSocket control frames, zero stream
errors, zero HTTP attempts, zero REST book requests and zero trading mutations.
One late frame with a non-current request ID was ignored. Quiet/unchanged depth
was genuinely re-sent under the new subscription ID; no timer or Pong was used
to manufacture a fresh book. Both sockets/client closed; stopped checks passed.

## Interpretation and boundaries

This proves that bounded same-socket resubscription can obtain fresh full-book
responses for these nine markets, including quiet ones, without REST. It supports
investigating a batched public-book refresh service for active markets rather
than continuous REST book polling. It does not prove indefinite subscription
renewal is unlimited,32-market throughput, full-day stability, account-source
recovery, production exit latency or behavior through real venue maintenance.

Any implementation requires explicit authorization and full regression/edge
review: generation/request-ID isolation (one late frame was observed), bounded
subscription count/control cadence, partial/missing books, disconnect/timeout,
no old-receipt renewal, position priority, metadata/lifecycle independence,
shutdown joins, and unchanged entry/exit/ownership/risk authority. No fresh
book must remain no entry authority. Account/trading REST is separate and still
required under the selected REST account mode. No production code or config
changed and no restart occurred. Local/GitHub/WSL parity was clean before the
documentation update; the remote peer was not verified.
