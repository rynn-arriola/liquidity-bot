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
  most 22 only for an explicitly authorized measurement canary;
- refresh selection every 15 minutes, deeply evaluate at most 100 books per
  pass through the shared governor, and round-robin deferred lower-ranked
  programs on later passes;
- admit only `SPORTS_MARKET_TYPE_MONEYLINE`; two-sided futures, props, totals,
  spreads, and drawable outcomes remain discovery-only;
- 5 contracts per quote by default across 20-22 concurrently bound markets
  (one bid and one offer may rest per market while flat). Size is a
  restart-scoped whole number from 5 through 30; the live operator setting is
  13. `quote_size.py` is the single quantity authority; `slot_capacity.py`
  independently validates breadth, and a bare launcher invocation always
  restores the 20-slot baseline rather than inheriting a prior canary;
- require at least a 2-cent full-book spread before a new building quote;
  retain an already-resting quote down to 1.5 cents and pull it below that
  hysteresis boundary, while never spread-gating inventory reduction;
- continue computing the 30-second normalized-score reward model with a 50%
  haircut and conservative first-fill probability, but do not enforce that
  experimental cost gate for entry;
- report the venue's $1 aggregate payout floor, but never apply it to one
  market or one order;
- use no projected spread or maker rebate until live evidence supports it;
- cancel the opposite building order immediately after the first fill;
- work the exact reducing quantity passively at the active touch with no
  cost-basis floor; a basis floor selectively exits winners and strands losers;
- at 900 seconds of inventory age, or 300 seconds before kickoff whichever
  comes first, confirm passive cancels and flatten the exact position with a
  stressed-depth, price-capped IOC; allow at most three confirmed-terminal IOC
  attempts within 30 seconds and 10 seconds to confirm an individual attempt.
  An early cross costing more than `0.8 × quote size` ($8 at live size 10) is refused
  and the position keeps resting
  -- but the kickoff deadline overrides that cap, because nothing may settle
  (corrected 2026-08-16; this read "at 30 seconds");
- allow at most `4 × quote size` taker exits (52 at live size 13), with an
  independent session fee cap of `0.30 × quote size` ($3.90 at size 13); the
  fee ceiling may stop an expensive session before the count is exhausted;
- $40 per-market realized-loss stop and one $80 account threshold measured by
  two sensors: realized session loss pauses new risk, while marked-account-
  equity loss is the emergency hard stop. The daily risk day resets at
  midnight `America/New_York`, including DST. These account-level loss limits
  do not scale down with quote size. The session sensor subtracts positively
  classified completed rewards, deposits, withdrawals, bonuses and transfers
  from settled-cash change; its bounded cash-only snapshot must be current and
  proven or risk freshness expires and new risk fails closed;
- run until an operator stop or terminal safety/loss condition, then remain
  alive until every owned order is terminal and the exact position is flat;
- one shared REST governor capped at 4 requests/second; live books and account
  state use WebSockets. Owned SDK clients use a 15-second REST timeout, and
  every private shard plus the market socket has an independent 180-second
  silence deadline.

The runner measures marked account equity as the API's USD `currentBalance`
plus `assetNotional`. It never substitutes `buyingPower` for equity. Buying
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

Automatic discovery bulk-loads active sports metadata, filters open markets to
the documented six-hour `day_of` window and the venue's V2 moneyline enum, then
requests incentives only for those symbols in batches. It does not retrieve
metadata once per reward slug. `marketSides=2` is not used as a structure test
because rewarded futures are also two-sided. Catalog BBO can reject a
known-tight row before a full-book request, but only a fresh full book can
authorize entry.
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
new risk. Unknown
strategy conditions default to terminal. Ownership, cross-market identity,
exact-position, planning, and unexitable-inventory failures end maker quoting
and enter cancel/flatten shutdown; they are not downgraded to warnings. A
first- or second-consecutive ET loss-stop day pauses new risk in-process while
exits remain active; the third consecutive day is terminal.

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
working order and nonzero position belongs to this persisted ledger and one
identified market. Automatic selection first follows that ledger market rather
than selecting something new. It cancels/flattens and exits; it does not resume quoting.
Unowned or cross-market state refuses without mutation.

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

After the queue-measurement build is deliberately deployed and the runner is
restarted, verify its isolated public-trade coverage locally on the server:

```bash
PYTHONPATH=src python -B scripts/queue_measurement_report.py \
  --database data/commissioning.db
```

This report is SQLite read-only. The collector never cancels or re-places an
order; `WOULD_QUEUE_RESET` is a shadow label only. Any drops, decode failures
or stream errors invalidate affected order coverage but do not stop quoting.

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

## Exit meanings

- `0`: clean operator/risk exit with flat state proven;
- `2`: startup refused; no automatic restart;
- `3`: flat state was not proven; no automatic restart and immediate operator
  inspection required;
- `4`: named critical strategy or ownership failure after cleanup; no automatic
  restart, because restarting would re-enter a known-bad state;
- `5`: unexpected runtime failure that still proved the whole account flat;
  the supervisor restarts it, since a fresh process re-runs the entire
  flat/ownership preflight before any order. Daily loss stops never reach this
  path: the first two consecutive ET days pause in-process, and the third is a
  terminal flat-proved exit rather than an automatic restart;
- any other unexpected process death: bounded supervisor restart so the
  persistent ledger can recover identified state, with at most three fast
  deaths before the supervisor gives up.

Automatic selection waits rather than refusing when no market is economically
viable. An empty universe is the normal case, so the runner holds with no bound
market, no orders and no stream subscription, rescanning on the selection
interval until a market qualifies or an operator stops it.
