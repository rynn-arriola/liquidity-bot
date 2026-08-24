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
permits only a non-scalable commissioning process. It joins the active sports
catalog to reward programs and binds a single executor to one selected market at
a time. The operative limits, **as configured and running** (verified against
`CommissioningConfig` and the live command line 2026-08-16 — the runner takes no
config overrides, so the dataclass defaults are what execute):

| limit | value |
|---|---|
| quote size | 5 contracts (restart-scoped, allowed 5-10) |
| concurrently bound markets | 20 |
| market loss stop | $40 |
| session loss stop | $50 |
| emergency hard stop | $60 marked equity |
| inventory age before a forced exit | 900s |
| pre-kickoff flatten deadline | 300s |
| early-exit cross cap | $4 at size 5 (`0.8 × size`) |
| per-market open exposure | $45 at size 5 (`9 × size`) |
| REST budget | 4 requests/second |

Plus an operator-controlled graceful stop. **These numbers have moved several
times and older prose elsewhere in this repo still quotes superseded ones** —
one contract, eight markets, a 30-second inventory age, and $2/$5/$15 or
$30/$35 stops are all stale. `commissioning.py` is the only authority; it
validates these values at startup and refuses to arm if they are changed. Live entry is nevertheless fail-closed: a
venue-eligible side must also pass a book-competition reward projection, a 50%
reward haircut, and a 2x probability-weighted stressed-exit-cost margin. The
$1 venue payout floor is aggregate reporting policy and never a per-market
entry gate. Fill risk uses the conservative upper bound of the applicable
2026-08-10 maker-lab paid-market calibration cells; projected reward is also
reduced by the no-fill probability. The
process may remain healthy and intentionally place no orders when that economic
proof is absent. It cannot be armed without four explicit command
acknowledgements, an exclusive key handoff, a flat whole-account snapshot, and
fresh private state.

Authenticated earnings for the active market are snapshotted separately from
execution P&L every 15 minutes. A passive reducer never locks in a loss: it
rests at no worse than inventory basis. If the 30-second emergency path still
needs a taker exit, the first such exit ends maker quoting so a one-cent test
cost cannot repeat indefinitely.

Recoverable market/private adapter failures no longer terminate the process.
The runner logs the stack, disables new risk, cancels known working quotes,
reconnects, and requires whole-account REST plus fresh stream state before it
can quote again. Confirmed loss limits and unsafe/ambiguous ownership remain
terminal safety events. `stop_commissioning.sh` requests graceful cancellation,
flattening, and exact flat-state proof before exit.

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

## Prospective building-quote observations

The commissioning ledger can retain a measurement-only snapshot for every
building post-only PLACE: exact price-time queue and current model queue,
Target Size/cutoff, BBO-change velocity, reward projection, stressed exit and
expected net. It is linked to the durable intent and later fill by the exact
client order ID. It is not sent to the venue and is not read by selection,
the reconciler, risk policy or the 20-slot scheduler. Reducers and IOC exits do
not record entry observations. There is deliberately no league filter.

Inspect collection coverage and fill/no-fill labels from the local ledger only
(the report makes no venue/API calls):

```powershell
$env:PYTHONPATH = "src"
python -B scripts/quote_observation_report.py --database data/commissioning.db
```

On the Linux server, use:

```bash
PYTHONPATH=src .venv/bin/python -B scripts/quote_observation_report.py \
  --database data/commissioning.db
```

Do not turn a diagnostic bucket into an entry refusal until coverage is
complete across three complete UTC-day cohorts, about 300 maker-filled
observations exist, and a candidate trained on the first two complete days
succeeds on the third held-out day. A partial deployment day is warm-up, not a
cohort. The candidate must then run in would-refuse shadow mode before
enforcement.

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

## Liquidity commissioning

Automatic selection is the default. It bulk-loads active sports metadata,
requires the venue's `SPORTS_MARKET_TYPE_MONEYLINE` structure enum, filters to
the proven six-hour pregame execution window, requests incentives for those
symbols in batches, deeply evaluates at most 25 books per 15-minute pass,
and round-robins the lower-ranked remainder so small pools are not permanently
hidden by the API budget. Lifecycle modes not yet
implemented by the pregame executor—including documented `daily` and observed
but undocumented `tournament_with_cutoff`—are counted and reported but cannot
authorize orders until their timing and pool allocation are modeled.

New building quotes require a current full-book spread of at least 2 cents.
An already-resting building quote is retained down to 1.5 cents to avoid
touch-noise churn and is canceled below that threshold. This rule never blocks
an inventory-reducing quote or emergency exit. Catalog best bid/ask values are
only a read-saving prefilter; the fresh full book is authoritative.

The server launcher remains deliberately explicit and also retains a static
market diagnostic mode. Quote size is a restart-scoped whole number from 5 to
10; when supplied as the second argument it is persisted under `data/` so the
supervisor and read-only diagnostics use the same value. Size never changes
under live orders. The early-exit cap, per-market exposure and session fee cap
derive automatically from it:

```bash
./scripts/start_commissioning.sh
./scripts/start_commissioning.sh MARKET-SLUG
./scripts/start_commissioning.sh auto 7       # persist and start at size 7
./scripts/status_commissioning.sh
./scripts/stop_commissioning.sh
```

Automatic rotation first cancels owned quotes and requires whole-account REST
proof of zero working orders, zero positions, known ownership, and zero
unresolved intents. Only then is the singleton private/public stream scope
rebound. The launcher refuses to overlap maker-lab or another liquidity runner. Its
supervisor restarts only after an unexpected process death; identified prior
orders enter recovery-only mode, where no new risk is allowed. Operator stop,
startup refusal, caught terminal failure, non-flat exit, and risk-stop exits are
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
