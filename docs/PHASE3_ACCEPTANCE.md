# Phase 3 acceptance

Implemented and fault-tested through 2026-08-13 ET.

- Every future mutation must have a committed pre-submit intent.
- Prepared, submitting, acknowledged, and uncertain intents survive restart
  and block new risk until reconciled.
- Event IDs are idempotent; reuse with different content fails closed.
- Orders, fills, fees/rebates, and positions retain decimal strings in SQLite.
- Economic direction comes from `ORDER_INTENT_*`, never raw `side` alone.
- Cumulative order updates and incremental executions cannot double count.
- A position snapshot watermark prevents late execution delivery from adding a
  fill already included in authoritative position state.
- Stale snapshots cannot reopen terminal orders or rewind positions.
- An order absent from a complete reconnect snapshot becomes `UNKNOWN_ABSENT`,
  not silently canceled.
- Private WebSocket snapshots are applied only after explicit `eof`.
- REST open-order and paginated position snapshots share the same asynchronous
  request governor used by all other REST reads.
- Crash after remote create but before local acknowledgement is recovered by
  an exact intent fingerprint without a duplicate create. A prepared intent
  that never reached submission cannot adopt a venue order.
- A cancel acknowledgement is non-terminal; a fill that wins the race
  reconciles the cancel and preserves the resulting inventory.
- Rounded legacy position state is rejected when exact decimal position state
  is unavailable.

The state-changing adapter boundary now exists for single create/cancel fault
tests, but no live command or runner can arm it. Mutations share the REST
governor, are durably recorded first, and are never automatically retried.
