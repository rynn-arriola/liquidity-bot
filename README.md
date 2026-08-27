# liquidity-bot

> **AI operators:** Start with [AI_OPERATOR.md](AI_OPERATOR.md), then read the
> one canonical live handoff in [STATUS.md](STATUS.md) and the human operator's
> active choices in [DECISIONS.md](DECISIONS.md). The dated status language in
> historical files is not the current runtime state.

A clean, standalone Polymarket US liquidity-reward market maker.

This project uses `D:\polybot\maker-lab` as a pinned code/evidence reference,
not as an imported runtime. It does not copy maker-lab credentials, databases,
logs, server overrides, or live-order state. A narrow secret handoff parses
only five literal assignments from the sibling ignored `credentials.py` file:
the Polymarket key pair plus normal, error, and hourly-reward Discord webhooks.
No maker-lab module is executed.

## Current milestone

Phases 0-4 and the narrow Phase 5 commissioning path are implemented:

- select the incentive lifecycle that applies now;
- verify the full side reaches `targetSize`;
- calculate whether an order lies inside the reward cutoff;
- estimate the order's score using `discountFactor ^ ticks * size`;
- calculate conservative reward-versus-unwind economics as advisory telemetry;
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
catalog to reward programs and binds one executor across at most 20 selected
markets. The operative authorized limits are below. Quote size is read from the
restart-scoped runtime setting; the account loss limits remain hard-locked by
`CommissioningConfig`:

| limit | value |
|---|---|
| quote size | 10 contracts (restart-scoped, allowed 5-10) |
| concurrently bound markets | 20 |
| market loss stop | $40 |
| session loss stop | $80 realized trading loss |
| emergency hard stop | $80 marked equity |
| inventory age before a forced exit | 900s |
| pre-kickoff flatten deadline | 300s |
| early-exit cross cap | $8 at size 10 (`0.8 × size`) |
| per-market open exposure | $90 at size 10 (`9 × size`) |
| REST budget | 4 requests/second |

The daily loss budget resets at midnight `America/New_York`, including DST.
The two $80 rows are one dollar threshold with different sensors and responses,
not duplicate calculations.

Plus an operator-controlled graceful stop. **These numbers have moved several
times and older prose elsewhere in this repo still quotes superseded ones** —
one contract, eight markets, a 30-second inventory age, and $2/$5/$15 or
$30/$35 stops are all stale. `commissioning.py` is the only authority; it
validates these values at startup and refuses to arm if they are changed. Live
entry is nevertheless fail-closed on lifecycle, structure, spread, target
depth/cutoff, fresh state, exitability, exposure, ownership, and loss limits.
The 30-second reward/no-fill/stressed-exit model is still calculated and logged,
but its expected-value result is advisory and does not veto entry. The
$1 venue payout floor is aggregate reporting policy and never a per-market
entry gate. Fill risk uses the conservative upper bound of the applicable
2026-08-10 maker-lab paid-market calibration cells; projected reward is also
reduced by the no-fill probability. The
process may remain healthy and intentionally place no orders when the enforced
reward or safety evidence is absent. It cannot be armed without four explicit command
acknowledgements, an exclusive key handoff, a flat whole-account snapshot, and
fresh private state.

Authenticated earnings are snapshotted separately from execution P&L every 15
minutes. A passive reducer rests the exact quantity at the active touch with no
cost-basis floor. At 900 seconds of inventory age, or 300 seconds before
kickoff, the bounded IOC path may cross; maker quoting ends after 20 confirmed
taker exits or `0.30 × quote size` in session execution fees.

Recoverable market/private adapter failures no longer terminate the process.
The runner logs the stack, disables new risk, cancels known working quotes,
reconnects, and requires whole-account REST plus fresh stream state before it
can quote again. Sustained, positively classified transport/HTTP-5xx failures
enter `MAINTENANCE_PAUSED`: complete authenticated open-order, position and
balance reads must pass three consecutive times over about two minutes before
the normal reconciliation and stream gates are retried. These probes are
read-only and never interpret order absence as cancellation. Response-schema,
ownership and invariant errors are not maintenance. Confirmed loss limits and
unsafe/ambiguous ownership remain terminal safety events.
`stop_commissioning.sh` requests graceful cancellation, flattening, and exact
flat-state proof before exit.

The $80 realized session sensor subtracts positively classified completed
rewards, deposits, withdrawals, bonuses and transfers from settled-cash change.
It shares the report's completion-time cash parser, requires explicit EOF within
four cash-only pages, and brackets each 15-second refresh with equal balance
reads. A cash snapshot that moves, cannot be classified, crosses the ET day or
exceeds the bound cannot renew risk freshness, so new risk fails closed. This
uses the existing shared REST governor and does not change the separate marked-
equity emergency sensor.

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

## Seven-day reward table

For one ET-dated table containing current account-realized trading, historical
balanced-pair estimates, paid and pending liquidity reward, finalized net, and
merged resting side-hours:

```bash
.venv/bin/python -B scripts/reward_table.py --days 7
```

The phrase **“run 7 day reward table”** means this exact command. The script
opens `data/commissioning.db` read-only. It makes one earnings request plus a
bounded account snapshot for `Account realized today (ET)`: two matching
balance reads around a complete position read and no more than four cash-only
activity pages, which must reach explicit EOF. Cash changes are assigned by
completion time because the endpoint orders them by creation time. These calls
share a separate 1 request/second limiter. The
account figure subtracts completed rewards/deposits/other non-trading cash and
adds current venue position basis; if the boundary or snapshot is not proved,
it displays `Unavailable`. Historical rows keep the balanced-leg ledger method
and can omit settled exits; the current ET row and its net use the account
figure instead. The script performs no market
discovery, book, order or mutation call. Do not tight-loop it or schedule it
more frequently than hourly. Pending and unposted reward are never counted as
net, and the current ET day is marked partial.

The server can post the same table hourly to a dedicated Discord channel with
`scripts/post_hourly_reward_table.sh`. The wrapper uses a nonblocking file lock,
a 90-second timeout and reduced CPU priority; it is a separate process and has
no order mutation path. Its `DISCORD_REWARDS_WEBHOOK_URL` lives only in the
server-side shared credentials file. Discord receives an embed containing two
narrow, fixed-width tables: daily P&L/rewards and daily liquidity supplied.
Every header, separator and row has the same measured character width, so the
columns do not depend on Discord's unsupported Markdown table rendering. The
layout is deliberately capped at seven days. The hourly cron is currently
active after visual acceptance of this revised format. It runs at minute 7 of
every hour and writes only redacted success/failure status to
`logs/reward-table-hourly.log`.

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
resting side-hours, fill signals, exit completion, inventory age, exact
state/risk, and stream/API health. LIVE `Resting side-hours` is the same merged
market-side interval calculation used by the seven-day reward table. It reads
the ledger in SQLite read-only mode on a worker thread, uses the current
midnight-to-midnight `America/New_York` day including DST, and can exceed 24
when several sides rest simultaneously. It survives restarts because the order
intervals are durable. SHADOW remains explicitly scoped to its current run and
continues to show modeled score-hours. Pending or
unattributable rewards are visible but never included in accounted net.

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
symbols in batches, deeply evaluates at most 100 books per 15-minute pass,
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

The optional almost-front queue study is measurement-only and starts with the
normal runner once its reviewed code is deployed. It uses a separate bounded
public-trade socket and has no cancel/repost path. Inspect its coverage without
venue calls:

```bash
PYTHONPATH=src python -B scripts/queue_measurement_report.py \
  --database data/commissioning.db
```

`COLLECTING` is the expected result until three full days, roughly 300 maker
fills with post-ACK baselines and zero coverage gaps exist. Even
`READY_FOR_REVIEW` does not enable an
automatic queue reset.

Automatic rotation first cancels owned quotes and requires whole-account REST
proof of zero working orders, zero positions, known ownership, and zero
unresolved intents. Only then is the active private/public subscription set
rebound. Incremental top-up retains every healthy market/order and fills only
safe free slots. It also releases a flat owner when the exact reward program
that authorized it is positively observed no longer to apply after an event
reschedule. A lagging GET for a retained owner cannot freeze unrelated free-slot
replacement, but that owner remains locally barred until terminal proof. During
stream recovery the replacement private listener is established before
quarantine cancels, so their terminal events are observable. The launcher
refuses to overlap maker-lab or another liquidity runner. Its
supervisor restarts only after an unexpected process death; identified prior
orders enter recovery-only mode, where no new risk is allowed. Operator stop,
startup refusal, caught terminal failure, and non-flat exit are never
restarted. First- and second-consecutive ET loss-stop days pause in-process;
the third becomes terminal. See [commissioning operations](docs/COMMISSIONING.md).

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
