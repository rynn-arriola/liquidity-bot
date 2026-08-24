# Liquidity commissioning

This is an operator-accepted data-collection exception, not evidence that the
strategy is profitable and not permission to scale.

## Fixed execution envelope

- join every executable active sports market to its liquidity rewards and
  expose the account to at most 20 selected rewarded markets at a time;
- refresh selection every 15 minutes, deeply evaluate at most 100 books per
  pass through the shared governor, and round-robin deferred lower-ranked
  programs on later passes;
- admit only `SPORTS_MARKET_TYPE_MONEYLINE`; two-sided futures, props, totals,
  spreads, and drawable outcomes remain discovery-only;
- 5 contracts per quote by default across at most 20 concurrently bound
  markets (one bid and one offer may rest per market while flat). Size is a
  restart-scoped whole number from 5 through 10; the live operator setting
  returned to 10 on 2026-08-24 without changing the 20-slot breadth.
  `commissioning.py` is the authority;
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
- work the exact reducing quantity passively at the active touch only when that
  is no worse than basis; otherwise wait at the break-even basis price;
- at 900 seconds of inventory age, or 300 seconds before kickoff whichever
  comes first, confirm passive cancels and flatten the exact position with a
  stressed-depth, price-capped IOC; allow at most three confirmed-terminal IOC
  attempts within 30 seconds and 10 seconds to confirm an individual attempt.
  An early cross costing more than `0.8 × quote size` ($8 at live size 10) is refused
  and the position keeps resting
  -- but the kickoff deadline overrides that cap, because nothing may settle
  (corrected 2026-08-16; this read "at 30 seconds");
- allow at most 20 taker exits, with a session fee cap of `0.30 × quote size`
  ($3 at live size 10), so the cap continues to cover twenty peak-fee exits;
- $40 per-market realized-loss stop, $70 realized session stop, and $80
  emergency marked-account-equity hard stop. These account-level loss limits
  do not scale down with quote size;
- run until an operator stop or terminal safety/loss condition, then remain
  alive until every owned order is terminal and the exact position is flat;
- one shared REST governor capped at 4 requests/second; live books and account
  state use WebSockets.

The runner measures marked account equity as the API's USD `currentBalance`
plus `assetNotional`. It never substitutes `buyingPower` for equity.

## Startup gates

The command refuses before order entry unless all of these are true:

1. `--arm-live`, `--accept-one-contract-risk`, `--exclusive-api-key`, and
   `--maker-lab-stopped` are all present.
2. The runner is explicitly armed for operator-controlled graceful stop.
3. Either an explicit market is named, or automatic discovery selects an open
   rewarded market in a documented lifecycle applying now and before event
   start.
4. The configured quantity is a whole 5-10 contracts and meets the current
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
records the error and stack, cancels known quotes, reconnects, reconciles the
whole account, backs off exponentially, and only then permits new risk. Unknown
strategy conditions default to terminal. Ownership, cross-market identity,
exact-position, planning, loss-stop, and unexitable-inventory failures end maker
quoting and enter cancel/flatten shutdown; they are not downgraded to warnings.

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
through 10 and is persisted in `data/commissioning_quote_size`. Omitting it
reuses the persisted value, or 5 if the file does not exist. The launcher
refuses while a runner or supervisor exists, so size can change only between
flat sessions. The supervisor and diagnostics read the same persisted value.

The launcher reads the existing sibling `../maker-lab/credentials.py` as
literal data. The credential file, environment files, databases, locks, and
logs are ignored by Git. Never copy secrets into this repository.

## Exit meanings

- `0`: clean operator/risk exit with flat state proven;
- `2`: startup refused; no automatic restart;
- `3`: flat state was not proven; no automatic restart and immediate operator
  inspection required;
- `4`: named critical strategy or ownership failure after cleanup; no automatic
  restart, because restarting would re-enter a known-bad state;
- `5`: unexpected runtime failure that still proved the whole account flat;
  the supervisor restarts it, since a fresh process re-runs the entire
  flat/ownership preflight before any order. Loss stops never reach this
  path — they exit `0` after proving flat;
- any other unexpected process death: bounded supervisor restart so the
  persistent ledger can recover identified state, with at most three fast
  deaths before the supervisor gives up.

Automatic selection waits rather than refusing when no market is economically
viable. An empty universe is the normal case, so the runner holds with no bound
market, no orders and no stream subscription, rescanning on the selection
interval until a market qualifies or an operator stops it.
