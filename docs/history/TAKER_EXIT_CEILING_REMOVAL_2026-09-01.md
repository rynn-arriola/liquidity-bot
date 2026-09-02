# Taker-exit ceiling removal — 2026-09-01

## Incident

The size-10/32-slot runner started at `2026-09-01T18:54:45Z` and ended at
`2026-09-02T00:55:26Z` with `commissioning_taker_exit_limit` after 811 venue
orders and 40 distinct bot-owned taker-exit orders. The supervisor did not
restart it because shutdown ended `flat=false`.

The last pre-stop marked trading observation was `-$36.2075`, well inside the
`-$128` marked-equity HARD limit. Nearby API windows reported zero failures,
zero confirmed 429s and queue depth one. This was therefore the configured
session count ceiling, not Cloudflare, maintenance, supply or a loss stop.

Shutdown canceled every flat-market order, flattened two urgent positions and
left three nonzero positions with one reducing order each. Those reducers were
the only three authenticated venue orders in the post-stop snapshot.

## Operator decision

The operator made marked-equity HARD the sole money-loss authority.
Risk-reducing taker count, gross fees, realized session P&L and per-market
realized P&L may not terminate the process, pause entries or change an exit.
Gross taker fees remain scaled, durable, one-notice telemetry only. The
stressed-exit entry-quality check, exposure limits, ownership/freshness gates,
pre-kickoff flatten and explicit operator stop are unchanged.

## Prepared correction

The next-run source deletes:

- `max_taker_exit_orders_per_contract` and its derived configured ceiling;
- the per-pass session-fill history query in `_stop_reason`;
- the `commissioning_taker_exit_limit` return;
- the startup banner's obsolete count-cap claim; and
- tests and current documents that treated `4 × quote size` as authority.

The same change removes the realized-session and per-market loss blockers, the
three-consecutive-day escalation, their announcement/state path and their two
Discord meter rows. The former `4 × size` value remains under the explicit
name `max_stressed_exit_loss_usd` solely as a pre-entry stressed-close quality
check; it cannot stop an already-running session.

The regression writes 80 distinct owned taker-exit orders—twice the removed
size-10 threshold—and proves `_stop_reason` remains clear. A partial-fill
variant proves there is no hidden order/fill-count stop. A separate regression
injects arbitrarily large realized ledger loss and proves new risk remains
allowed while adjusted marked equity is inside HARD; moving adjusted equity
one dollar beyond HARD still returns `EMERGENCY_EQUITY_HARD_STOP`.

Deliberate reversals restored each removed authority in isolation and made its
new regression fail exactly. The complete WSL suite passes 1,048/1,048, the
documentation audit reports zero issues, and `git diff --check` is clean. The
standalone dashboard now uses the same adjusted account mark and renders only
HARD; its redundant realized fill-history replay and obsolete session/market
meters are gone.

## Deployment and live acceptance

Commit `d3de7bd7756693f6f3d411bf2922eee388fe603f` was pushed, fast-forwarded
into the clean WSL runtime and recorded in `DEPLOYED_COMMIT`. The native WSL
checkout passed all 1,048 tests before the marker advanced. Authenticated
pre-start proof found zero open orders and zero positions, after which the
runner restarted at the persisted size 10 / 32-slot configuration.

The runner rebuilt 35 authenticated orders across 30 markets and had one live
managed position at the acceptance read. The first `[api-budget]` window
covered 578 attempts in 267.2 seconds (`2.163/s`), including 63 mutations,
with zero failures, zero confirmed 429s, zero cooldown and maximum queue depth
one. No runtime-recovery or traceback appeared in the new-run log segment.
