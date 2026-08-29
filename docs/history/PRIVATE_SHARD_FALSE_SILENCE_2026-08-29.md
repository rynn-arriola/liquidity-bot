# Private-shard false-silence diagnosis — 2026-08-29

## Scope and live safety

The laptop remained the sole live owner. Supervisor PID 1649, runner PID 1656
and dashboard PID 1681 were observed without stopping, restarting, deploying,
cancelling, flattening or making venue API requests. The repair was made only
in the tracked Windows checkout and is not loaded into the running process.

## Evidence

Across roughly eight hours of the current run, logs contained about 123
`private WebSocket shard silence` detections and 120 recovery records covering
133 shards. Every recovery reported `cancels=0`. The same interval had no
associated HTTP 403/429, global quarantine, risk stop, `UNKNOWN_ABSENT` or clock
reversal. Repeated recovery of otherwise healthy, quiet shards was therefore a
systematic liveness-classification problem rather than an order-cap, access,
rate-limit or ownership failure.

The pool's deadline used only decoded private JSON events. The pinned
`polymarket-us==0.1.2` wrapper exposes those application events, while its
underlying `websockets` connection independently uses protocol Ping/Pong.
Consequently a shard could receive no balance/order/position JSON for 180
seconds even though its transport remained connected and answered Pings.

## Repair and safety boundary

Each private connection now performs a bounded protocol Ping every 60 seconds
and advances a transport clock only after awaiting its Pong. Pool liveness uses
the newer of positive application and transport evidence. Missing SDK
internals, Ping failure or Pong timeout raises `AdapterError` into the existing
fail-closed stream recovery. REST reconciliation remains a state backstop and
never certifies WebSocket liveness. No undocumented JSON heartbeat response was
added.

## Verification

- Focused stream tests: 23/23 passed.
- Focused private-pool tests: 16/16 passed.
- Deliberate reversal: removing the Pong contribution made
  `test_positive_transport_pong_prevents_false_quiet_shard_alarm` fail with the
  expected shard-silence error; restoring it returned both focused suites to
  green.
- Full offline suite with the live WSL environment and tracked Windows source:
  810/810 passed in 62.079 seconds.

Natural acceptance remains pending until the next separately authorized safe
restart. It requires a quiet interval beyond 180 seconds without false handoff,
and retention of bounded recovery when positive Ping/Pong proof actually fails.
