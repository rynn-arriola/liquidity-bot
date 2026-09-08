# Liquidity commissioning

This file defines the live execution lifecycle; it does not state whether the
bot is running now. AI operators must begin with
[AI_OPERATOR.md](../AI_OPERATOR.md), [STATUS.md](../STATUS.md) and the human
operator's choices in [DECISIONS.md](../DECISIONS.md).

This is an operator-accepted data-collection exception, not evidence that the
strategy is profitable and not permission to scale.

## Fixed execution envelope

- join every executable active sports market to its liquidity rewards and
  expose the account to the validated restart capacity: 20 by default and at
  most 32 only for an explicitly authorized measurement canary;
- refresh selection every 15 minutes, deeply evaluate at most 100 books per
  pass through the shared governor, and round-robin deferred lower-ranked
  programs on later passes;
- admit only `SPORTS_MARKET_TYPE_MONEYLINE`; two-sided futures, props, totals,
  spreads, and drawable outcomes remain discovery-only;
- 5 contracts per quote by default across 20-32 concurrently bound markets;
  widths above the loaded 24 are reviewed canary inputs, not durable defaults
  (one bid and one offer may rest per market while flat). Size is a
  restart-scoped whole number from 5 through 30; the current reviewed canary
  uses size 10. `quote_size.py` is the single
  quantity authority; `slot_capacity.py`
  independently validates breadth, and a bare launcher invocation always
  restores the 20-slot baseline rather than inheriting a prior canary;
- require at least a 2-cent full-book spread for fresh market selection; if a
  selected market's live book later tightens to exactly one tick, quote both
  touches (49/50 stays 49/50) instead of backing only the offer away; keep the
  non-crossing 49/51 back-off for a locked 50/50 book; retain the separate
  1.5-cent resting hysteresis and never spread-gate inventory reduction;
- continue computing the 30-second normalized-score reward model with a 50%
  haircut and conservative first-fill probability, but do not enforce that
  experimental cost gate for entry;
- report the venue's $1 aggregate payout floor, but never apply it to one
  market or one order;
- use no projected spread or maker rebate until live evidence supports it;
- cancel the opposite building order immediately after the first fill;
- work the exact reducing quantity passively at the active touch with no
  cost-basis floor; a basis floor selectively exits winners and strands losers;
- for an exact venue-aligned `0.01`–`0.09` contract residue, allow five passive
  minutes before cashout; `0.10` and larger retain the 900-second inventory
  clock, while the 300-seconds-before-kickoff deadline overrides both;
- when any of those exit deadlines arrives, cancel one working order at a time, position-building side
  first, and require positive terminal proof for that exact submitted cancel
  before selecting another. Only after every working order is terminal may the
  runner flatten the exact position with a stressed-depth, price-capped IOC;
  allow at most three confirmed-terminal IOC
  attempts within 30 seconds and 10 seconds to confirm an individual attempt.
  An early cross costing more than `0.8 × quote size` ($8 at next-run size 10) is refused
  and the position keeps resting
  -- but the kickoff deadline overrides that cap, because nothing may settle
  (corrected 2026-08-16; this read "at 30 seconds");
- count risk-reducing taker exits as evidence only, with no session ceiling;
  independently, measure gross owned taker fees against the cent-rounded reference
  `$10 × (quote size / 10) × (slots / 24)` ($10 at size 10 / 24 slots).
  Neither count nor fee observation can block/cancel quotes, pause/stop trading
  or change an exit path. Crossing the fee reference logs/reports once;
- one breadth-scaled marked-account-equity HARD stop is the sole money-loss
  authority. It is $56.25 at size 10 / 20 slots, $67.50 at 24 and $90 at 32. The ET
  risk day resets at midnight `America/New_York`, including DST. Positively
  classified completed rewards, deposits, withdrawals, bonuses and transfers
  are subtracted from the adjusted mark; the bounded cash-only snapshot must
  be current and proven or risk freshness expires and new risk fails closed;
- confirmed HARD cancels entries and immediately evacuates all inventory using
  sequential cancel-confirm-exact-IOC. The durable daily pause survives restart
  and recovered equity; resume requires a later ET day plus fresh flat account,
  current adjusted risk and normal maintenance/stream/entry gates. A provisional
  open-position breach still uses the existing cohort-close confirmation;
- use that same adjusted ET-day marked loss to ratchet the account-wide
  flat-entry order ceiling: 30 at 50% of HARD, 25 at 70%, 20 at 80%, 15 at
  90%, and 10 at 95%. HARD at 100% remains the sole loss stop. The tightest reached tier
  survives restart and cannot loosen until the next ET day. Unresolved creates
  reserve capacity; excess owned entries drain one cancellation at a time from
  the lowest-ranked markets. Position-market orders, reducers, IOC exits and
  shutdown never consume or obey this entry ceiling;
- independently cap pending flat-market entries at 30 from 00:00 inclusive to
  09:00 exclusive `America/New_York` and 40 during the rest of the ET day.
  Combine that clock band with the durable loss tier and physical two-orders-
  per-slot capacity by taking the smallest cap. At 09:00 only the clock band
  changes; it never resets a loss tier or changes selection priority. These
  values load from strict restart-scoped `config/entry_order_policy.json`;
  malformed or loosening-shaped configuration refuses before credentials or
  venue access. Position markets, reducers, IOC exits, recovery and shutdown
  retain the same exclusions;
- run until an operator stop or terminal safety/loss condition, then remain
  alive until every owned order is terminal and the exact position is flat;
- one shared REST governor capped at 4 requests/second; live books and account
  state use WebSockets. Owned SDK clients use a 15-second REST timeout, and
  every private shard plus the market socket has an independent 180-second
  silence deadline.

The five-minute LIVE Discord summary reports conservative capital guidance
without another API call. The comfortable balance is the larger of the
portfolio exposure cap or `quote size × configured slots`, plus seven daily
hard-stop budgets for reward-payment delay. A withdrawal ceiling is published
only from fresh, ambiguity-free account evidence and is
`max(0, min(buyingPower, currentBalance - comfortable balance))`, rounded down
to cents. Pending rewards are never treated as cash. This is observation only:
it neither submits a withdrawal nor changes entry, exposure or stop behavior.
A routine post whose fresh balance is blocked only by current order ambiguity
may wait up to 60 seconds for local terminal proof, with ledger-only rechecks
every two seconds. Stale evidence is reported immediately and persistent
ambiguity remains `UNAVAILABLE`; shutdown and critical summaries do not wait.

The runner measures marked account equity as the API's USD `currentBalance`
plus every open position's documented `cashValue`; Retail `assetNotional` has
been observed as zero with nonzero positions and is not used. It never
substitutes `buyingPower` for equity. Buying
power gates only a flat market's new post-only entry and is reserved locally
after successful submissions so a multi-slot sweep cannot reuse stale capacity;
it never delays a reducer, cancel or IOC exit.

## Startup gates

The command refuses before order entry unless all of these are true:

1. `--arm-live`, `--accept-one-contract-risk`, `--exclusive-api-key`, and
   `--maker-lab-stopped` are all present.
2. The runner is explicitly armed for operator-controlled graceful stop.
3. Either an explicit market is named, or automatic discovery selects an open
   rewarded market in a documented lifecycle applying now and before event
   start.
4. The configured quantity is a whole 5-30 contracts and meets the current
   venue minimum.
5. The whole account has no open order, nonzero position, unresolved mutation,
   or unowned state.
6. The balance schema is complete and marked equity is known.
7. REST reconciliation and the private account WebSocket are fresh.
8. Normal and critical Discord webhooks are configured.
9. Authenticated earnings for the active liquidity program can be read and
   persisted separately from execution P&L.

Automatic discovery pages active/open sports events over the six-hour strategy
horizon plus a one-day clock-skew buffer, flattens their nested market metadata,
and applies the market-level clock and V2-moneyline gates before requesting
rewards only for those exact symbols. The wider event window retains delayed
or rescheduled games whose event `startTime` lags their market
`gameStartTime`. Structured market `gameStartTime` remains required. If that
same market's generated question contains an exact earlier `scheduled for ...
UTC` timestamp, the earlier value is the effective pregame deadline; question
text can never extend or replace structured time. It never walks
the account-wide active market or incentive universe, and a full tenth event
page fails closed rather than silently truncating discovery.
Each successful full selection emits `[selection-scan]` with event pages used,
the configured page ceiling and the event/executable/rewarded/book/viable
counts. Treat rising page usage as capacity evidence; the metric is
observational and never authorizes entry or blocks order service.
`marketSides=2` is not used as a structure test because rewarded futures are
also two-sided. Metadata BBO can reject a known-tight row before a full-book
request, but only a fresh full book can authorize entry.
Only documented early/day-of/live lifecycle names enter validation, and only
the currently applicable pregame `day_of` period can authorize this executor.
Lifecycle names not implemented by the pregame executor—including documented
`daily`/`daily_event` and observed but undocumented
`tournament_with_cutoff`—remain read-only and are reported in Discord; they do
not trigger book fan-out or order entry. Reward amount changes ranking
and does not exclude a program from discovery.

Reward eligibility alone never authorizes an order. The runtime computes the
proposed order's share of all discounted score inside the target-size cutoff,
reserves no more than half of the pool for either independently normalized
side, and projects only the documented six-hour `day_of` period. Periods whose
per-market duration cannot be proven fail closed. The process can therefore run
normally with zero working orders; that is an economic refusal, not a fault.
Discovery and flat-rotation durations are reported in Discord so REST scan and
cancel/flat/rebind downtime can be measured before any concurrency change.

During runtime, an explicitly recoverable private, market, or order adapter
error enters a dormant recovery state instead of ending the process. The runner
records the error and stack. A heartbeat-silent private shard is overlap-
replaced while decisions are frozen; each replacement must deliver private
balance, any order pages are scope-checked/buffered, and REST balance, orders
and positions are refreshed before quotes are preserved. A confirmed 429 on
those reads receives a slower bounded retry while private events keep draining,
replacement liveness is checked and no decision or cancel can run. All-shard
silence, non-429/stale/exhausted proof, and other private recovery establishes
replacement visibility, cancels known quotes, applies buffered terminal events,
reconciles the whole account, backs off exponentially, and only then permits
new risk. Unknown strategy conditions default to terminal. Ownership, cross-market identity,
exact-position and unknown planning failures end maker quoting and enter
cancel/flatten shutdown; they are not downgraded to warnings. Named per-owner
exit failures retain slow bounded rounds even after ten rounds while inventory
or orders remain. Re-arming never erases ambiguous IOC proof. Temporary startup
conditions and classified transport/5xx/429 recovery wait without a lifetime
retry limit, using the existing API governor and three complete healthy probes.

Every cooperating WSL API client uses `data/api-rate-limiter.state`, so the
runner, dashboard and scheduled reports cannot each spend an independent REST
allowance on the same laptop. All lanes consume one aggregate 4/second clock.
Account safety reads, cancellations, passive reducers and IOC exits use the
safety lane; maker entry and cached top-up revalidation use an independent
4/second entry lane; catalog discovery, earnings and reports use a 2/second
background lane. A 429 cools only its originating lane, with safety capped at
one second, and never authorizes a mutation retry. Background/entry reads fail
promptly after publishing cooldown rather than sleeping inline; only safety
reads may retry inline within their short bound. Later background/entry work
with more than one second of cooldown remaining defers before network IO;
deferred entry intents are exact `FAILED` records and do not quarantine orders.
The remaining cooldown also gates later entry plans before another intent is
created, so one cooldown cannot churn the ledger across every market pass.
File locking and state writes run off the event loop, no limiter lock sleeps,
and a first stop request wakes an existing limiter/read-retry wait while leaving
later shutdown calls usable. A mutation interrupted during pre-network
admission is durably `FAILED`, never left submitting or uncertain.
Native Windows authenticated execution fails closed because it cannot join the
POSIX lock. Every executable gateway still requires an explicit limiter.
Event-driven lifecycle, reward-end and post-flat top-ups first attempt bounded
revalidation of a complete selection no older than 120 seconds, avoiding a new
catalog scan immediately before stream-handoff position proof.
If a complete top-up fails, every event-driven and ordinary trigger observes
one shared retry-not-before deadline. The triggering state is delayed rather
than converted into market failure, and order/position service continues
between attempts.

Structured entry rejections are also scoped rather than terminal: expected
post-only races wait one second in that market, balance/risk failures wait 15
seconds and unknown validation failures wait 60 seconds. These delays apply
only while the market is flat and cannot delay inventory reduction.

Recent portfolio activity is positive backfill evidence, not a complete
history contract. Trade identity and lifecycle are tracked separately from an
execution leg. If the venue later marks a linked trade
`TRADE_STATE_BUSTED`, the ledger reverses that fill exactly once and excludes
it from position basis, P&L and reporting; a newer authoritative venue position
prevents double reversal.

Incremental slot top-up takes complete account snapshots and strictly proves
every owner it will release before it changes the active tuple or closes
healthy streams. A failed proof leaves the old tuple, streams and resting venue
orders untouched. A lagging ID in a retained, positively-owned market bars only
that retained owner. Release predicates are checked again immediately before
commit. A closed/not-open owner additionally requires a fresh exact metadata
read at that boundary; missing status, read failure, reopened state, position,
working/unknown order or unresolved intent retains it. After that pre-commit
proof, the socket handoff still requires complete account snapshots and fresh
private state; missing/conflicting/unowned/out-of-scope state stays terminal.
Logs distinguish `[top-up-plan]` from an actual `[top-up] ... committed=yes`
transition and include the positively closed release count.

When the final position inside the pre-kickoff safety margin becomes positively
flat and mandatory capacity is reclaimable, the runner requests top-up before
the next ordinary configured-capacity service sweep. A bound market's positively observed
kickoff also requests one immediate top-up instead of recurring as an expected
`no longer pregame` runtime error; startup selection still rejects an already-
started candidate. Neither trigger scans while urgent inventory remains. A full
selection observed no more than 120 seconds earlier may shorten discovery only
after fresh bulk metadata/incentive reads and a fresh full-book viability check
for every proposed replacement; any stale, failed or insufficient refresh runs
the normal bounded selector. This fast path cannot accelerate optional
economic-idle eviction, which remains on the normal 900-second scan.
`[top-up-request]`, `[top-up-cache]`, `post_flat_wait_seconds`,
`lifecycle_wait_seconds` and `post_flat_underfilled_slot_minutes` expose the
result without changing authorization.

A still-open pregame owner may also be replaced after both sides continuously
fail named current-book economics for 10 minutes. This is optional replacement,
not lifecycle disposal: the normal 15-minute scan must provide a different
viable market for every such owner released. Any authorized side, non-economic
or unknown evaluation, position, working/`UNKNOWN_ABSENT` order or unresolved
intent resets or blocks the release. Oldest idle owners go first, retained venue
orders are never cancelled, and short replacement supply cannot reduce the
active market count. Logs expose both `economic_idle_candidates` and the number
actually released as `economic_idle`.

Shutdown carries its own three-attempt flatten budget, independent of whatever
the quoting loop already spent, so reaching the runtime ceiling can never stop
the terminal path from trying. While it cannot prove flat it repeats a critical
alert every 300 seconds, and after 900 seconds it exits non-flat with code 3
rather than holding a position silently.

After a process death, a restart may enter recovery-only mode only when every
working order and nonzero position belongs to this persisted ledger and the
identified recovery scope fits configured capacity. Automatic selection first
follows those ledger owners rather than selecting something new. It
cancels/flattens and exits; it does not resume quoting.
An existing durable HARD hold instead keeps this same strictly scoped startup
recovery in process, obtains fresh private state and continues evacuation;
next-day quoting still requires fresh whole-account flat/known-state proof.

There is one narrower availability exception. If a valid Retail response says
that an exact bot-owned post-kickoff position has no bids and no offers, while
the account is order-free and ambiguity-free and basis exactly matches the
authoritative position, that owner enters settlement wait. It remains barred
from quoting, reserves one configured slot and its full portfolio risk, stays
inside private/account monitoring and retries the book read only every 60
seconds. Remaining capacity may quote independently proven markets. A returned
book resumes normal recovery immediately; positive zero-position proof releases
the slot. Transport/schema errors, pregame positions, unknown basis, working or
absent orders and unresolved intents never qualify. A stop with only these
owners left reports nonflat and exits without pretending settlement occurred.
Unowned or out-of-scope state refuses without mutation.

## Server commands

Install once after cloning:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
chmod +x scripts/*.sh
```

Start, inspect, and stop:

```bash
./scripts/start_commissioning.sh
./scripts/start_commissioning.sh MARKET-SLUG
./scripts/start_commissioning.sh auto 7
./scripts/status_commissioning.sh
./scripts/stop_commissioning.sh
```

After the UI-maintenance entry gate has been loaded by an authorized restart,
new entries can be paused or resumed without stopping the process. Positions,
cancels and exits remain live; resume cannot override an active UI-maintenance
hold:

```bash
PYTHONPATH=src python -B scripts/entry_pause.py pause
PYTHONPATH=src python -B scripts/entry_pause.py status
PYTHONPATH=src python -B scripts/entry_pause.py resume
```

No manual command is needed for scheduled production maintenance. The loaded
runner polls the exact official production Statuspage component once per minute
and atomically retains verified windows in
`data/polymarket-maintenance-schedule.json`. At `scheduled_for - 30 minutes`,
flat entries are withdrawn and held positions use the ordinary sequential
cancel-confirm/exact-IOC exit machinery. New risk stays blocked until one hour
after the later of `scheduled_until` or positive official completion, followed
by three complete authenticated open-order/position/balance cycles. Existing
stream, reconciliation, UI and risk gates still apply. If the feed becomes
unknown, a known hold cannot clear; if notice arrives less than thirty minutes
before start, the bot tightens immediately but cannot recover the missing lead.

After the retained-value shadow build is deliberately deployed and the runner
is restarted, verify its order-linked coverage locally on the laptop:

```bash
PYTHONPATH=src python -B scripts/quote_observation_report.py \
  --database data/commissioning.db
```

This report is SQLite read-only. The 60%, 70% and 80% labels never refuse,
cancel, replace, reorder or resize an order. Replacement evidence comes only
from an already-fetched selector snapshot, so collection adds no venue call or
socket. The retired queue collector's tables and report remain historical
evidence but the runner no longer appends them.

The optional second argument is quote size. It must be a whole number from 5
through 30 and is persisted in `data/commissioning_quote_size`. Omitting it
reuses the persisted value, or 5 if the file does not exist. The launcher
refuses while a runner or supervisor exists, so size can change only between
flat sessions. The supervisor, runner and diagnostics share one validator.
The active size remains 10 until the operator authorizes another safe restart.

The launcher reads the existing sibling `../maker-lab/credentials.py` as
literal data. The credential file, environment files, databases, locks, and
logs are ignored by Git. Never copy secrets into this repository.

The changelog monitor is a separate public-RSS process with no Polymarket key,
ledger or trading hook. Its wrapper provides a nonblocking lock and 30-second
timeout. Production schedules it hourly at minute 23; this is independent of
runner startup and restart:

```bash
bash scripts/post_polymarket_changelog.sh
```

## Mutation permission and location denial

A Retail HTTP 403 is a positive rejection of that request, not proof about any
other venue state. The exact PLACE or CANCEL intent becomes `FAILED`; timeouts,
connection loss, 5xx and malformed success remain `UNCERTAIN`. The ordinary
configured-capacity action loop then opens one account-wide 60-second new-entry circuit so
a location or permission failure cannot retire every market. Existing orders
and positions stay tracked. Cancels and reducers bypass an entry denial; if a
safety mutation itself is rejected, it receives a short 10-second retry
cooldown. One later desired entry probes entry recovery, and successful
mutations clear only their matching circuit. The first denial sends one
critical operations alert.

On startup, old rows are repaired only when their durable error contains the
reviewed triple `PermissionDeniedError`, `status=403`, and `code=7`. This is
stored positive response proof, not snapshot absence. Every near-match still
requires normal reconciliation or the stopped-only orphan resolver. Shutdown
retains its independent exit attempts and never treats either circuit as
flatness.

A PLACE that becomes `UNCERTAIN` without an exchange order id is never resent:
the local client id was not transmitted and cannot identify the possible venue
order. The runner now keeps that market dormant and automatically abandons the
mutation only after three complete open-order/position snapshots spanning at
least ten seconds all show no live order or position in the market, no local
order row exists, and no execution at or after the intent is recorded. Any
contrary evidence resets the proof. Startup and market-stream recovery wait in
the same process rather than spending a restart budget; operator stop remains
terminal and HARD retains its durable daily evacuation hold. `scripts/resolve_orphan_places.py` remains a stopped-runner
manual fallback and dry-run check.

## Exit meanings

- `0`: clean operator/risk exit with flat state proven;
- `2`: startup refused; no automatic restart;
- `3`: flat state was not proven; no automatic restart and immediate operator
  inspection required;
- `4`: named critical strategy or ownership failure after cleanup; no automatic
  restart, because restarting would re-enter a known-bad state;
- `5`: unexpected runtime failure that still proved the whole account flat;
  the supervisor restarts it, since a fresh process re-runs the entire
  flat/ownership preflight before any order. HARD stays in process and does
  not use this automatic-restart path;
- any other unexpected process death: bounded supervisor restart so the
  persistent ledger can recover identified state, with at most three fast
  deaths before the supervisor enters attention mode without another launch.

Exit codes 2/3/4 also leave the lightweight supervisor in `ATTENTION_REQUIRED`:
one alert and local heartbeat, no trading and no blind relaunch. A live supervisor
PID is not a live trader. Manual stop still terminates the supervisor and runner.

Automatic selection waits rather than refusing when no market is economically
viable. An empty universe is the normal case, so the runner holds with no bound
market, no orders and no stream subscription, rescanning on the selection
interval until a market qualifies or an operator stops it.
