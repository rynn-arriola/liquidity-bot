# liquidity-bot

A clean, standalone Polymarket US liquidity-reward market maker.

This project uses `D:\polybot\maker-lab` as a pinned code/evidence reference,
not as an imported runtime. It does not copy maker-lab credentials, databases,
logs, server overrides, or live-order state. A narrow secret handoff parses
only four literal assignments from the sibling ignored `credentials.py` file:
the Polymarket key pair and the normal/error Discord webhooks. No maker-lab
module is executed.

## Current milestone

Phases 0-4 and the narrow Phase 5 commissioning path are implemented:

- select the incentive lifecycle that applies now;
- verify the full side reaches `targetSize`;
- calculate whether an order lies inside the reward cutoff;
- estimate the order's score using `discountFactor ^ ticks * size`;
- require conservative reward economics to cover expected unwind loss;
- reject unsafe live configurations;
- normalize paginated incentives, market metadata, full books, and earnings;
- decode private order, position, and balance WebSocket state;
- observe bounded market/private streams through a credential-safe probe;
- persist an order intent before any mutation and never retry ambiguous creates;
- reconcile exact-decimal orders, fills, and positions from private WebSocket
  state plus REST snapshots;
- enforce single-process ownership, stream freshness, session/market loss
  limits, and market-scoped shutdown.

The readiness gate remains `DO_NOT_START_TRIAL`: multi-day profitability and
paid-reward evidence are not satisfied. An explicit operator-risk override now
permits only a non-scalable commissioning run. It is hard-locked to one named
rewarded market, exactly one contract per quote, a 30-second inventory age, a
$2 market stop, a $5 session stop, a $15 emergency hard stop, and a 06:00
America/New_York risk cutoff. It cannot be armed without four explicit command
acknowledgements, an exclusive key handoff, a flat whole-account snapshot, and
fresh private state.

All REST access shares one asynchronous limiter. The default is 4 requests per
second, the configured hard maximum is 20, and continuous book/trade data uses
WebSockets instead of polling.

## Test

From PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -B -m unittest discover -s tests -v
```

## Bounded read-only probe

Environment variables take precedence. When absent, the probe reuses the
sibling maker-lab credential file without copying secrets. It reads named
markets and has no order endpoint:

```powershell
$env:POLYMARKET_US_KEY_ID = "..."
$env:POLYMARKET_US_SECRET_KEY = "..."
$env:PYTHONPATH = "src"
python -B -m liquidity_bot.probe --slug MARKET-SLUG --earnings --stream-seconds 5
```

The `--size` argument is hypothetical and is used only to calculate reward
score. The probe never submits it.

## Discord summaries and alerts

The shadow runner reuses maker-lab's normal and error webhooks. Every five
minutes and at clean completion it posts a liquidity-specific one-glance
summary: explicit SHADOW/DRY RUN/LIVE mode, readiness, reward-eligible sides,
score-seconds, fill signals, exit completion, inventory age, exact state/risk,
and stream/API health. Pending or unattributable rewards are visible but never
included in accounted net.

Unexpected termination uses the error webhook. Discord HTTP runs outside the
WebSocket event loop. Use `--discord-summary-minutes 0` to disable routine
summaries while retaining code-level critical-alert support. No webhook is
called by the test suite.

## Persistent shadow evaluation

```powershell
$env:PYTHONPATH = "src"
python -B -m liquidity_bot.shadow_run --minutes 1440 --max-markets 3 --rest-rps 4 `
  --discovery-refresh-minutes 15
python -B -m liquidity_bot.shadow_report --database data/shadow.db
```

The runner rotates into newly rewarded pregame markets every 15 minutes,
deactivates removed markets without accruing stale score, and idles safely when
none are currently available. An open inventory replay remains subscribed only
until all counterfactual exit horizons resolve.

Do not run this with another process using the same API key. A future controlled
trial requires an exclusive handoff: maker-lab stopped, one local process lease,
and a whole-account flat/open-order reconciliation before any new risk.

## One-contract commissioning

The server launcher is deliberately explicit:

```bash
./scripts/start_commissioning.sh MARKET-SLUG
./scripts/status_commissioning.sh
./scripts/stop_commissioning.sh
```

The launcher refuses to overlap maker-lab or another liquidity runner. Its
supervisor restarts only after an unexpected process death; identified prior
orders enter recovery-only mode, where no new risk is allowed. Clean cutoff,
startup refusal, caught runtime failure, non-flat exit, and risk-stop exits are
never restarted. See [commissioning operations](docs/COMMISSIONING.md).

See [the implementation plan](docs/IMPLEMENTATION_PLAN.md),
[safety invariants](docs/SAFETY_INVARIANTS.md), and
[maker-lab reference notes](docs/MAKER_LAB_REFERENCE.md). Read-only API
discoveries are pinned in [API contracts](docs/API_CONTRACTS.md). The complete
data and risk flow is in [architecture](docs/ARCHITECTURE.md), and the live
read-only checks are in [Phase 1 acceptance](docs/PHASE1_ACCEPTANCE.md) and
[Phase 2 acceptance](docs/PHASE2_ACCEPTANCE.md).

Phase 3 and Phase 4 evidence is in
[Phase 3 acceptance](docs/PHASE3_ACCEPTANCE.md) and
[Phase 4 acceptance](docs/PHASE4_ACCEPTANCE.md). The current controlled-trial
and scale verdict is in [the scale review](docs/SCALE_REVIEW.md).

Official references:

- https://docs.polymarket.us/api-reference/introduction
- https://docs.polymarket.us/incentives/liquidity
- https://docs.polymarket.us/api-reference/websocket/overview
- https://docs.polymarket.us/api-reference/websocket/private
- https://docs.polymarket.us/api-reference/websocket/markets
