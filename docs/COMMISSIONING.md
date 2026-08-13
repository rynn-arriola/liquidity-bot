# One-contract commissioning

This is an operator-accepted data-collection exception, not evidence that the
strategy is profitable and not permission to scale.

## Fixed envelope

- one explicit rewarded pregame market;
- one contract per quote (one bid and one offer may rest while flat);
- cancel the opposite building order immediately after the first fill;
- work the exact reducing quantity at the active touch;
- at 30 seconds, confirm passive cancels and flatten the exact position with a
  stressed-depth, price-capped IOC; allow at most three confirmed-terminal IOC
  attempts within 30 seconds and 10 seconds to confirm an individual attempt;
- $2 per-market soft stop, $5 marked-account-equity session stop, and $15
  emergency marked-account-equity hard stop;
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
3. The named market is open, rewarded in the lifecycle applying now, and
   pregame.
4. One contract is aligned to the current venue tick and minimum quantity.
5. The whole account has no open order, nonzero position, unresolved mutation,
   or unowned state.
6. The balance schema is complete and marked equity is known.
7. REST reconciliation and the private account WebSocket are fresh.
8. Normal and critical Discord webhooks are configured.

During runtime, an explicitly recoverable private, market, or order adapter
error enters a dormant recovery state instead of ending the process. The runner
records the error and stack, cancels known quotes, reconnects, reconciles the
whole account, backs off exponentially, and only then permits new risk. Unknown
strategy conditions default to terminal. Ownership, cross-market identity,
exact-position, planning, loss-stop, and unexitable-inventory failures end maker
quoting and enter cancel/flatten shutdown; they are not downgraded to warnings.

After a process death, a restart may enter recovery-only mode only when every
working order and nonzero position belongs to this persisted ledger and the one
configured market. It cancels/flattens and exits; it does not resume quoting.
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
./scripts/start_commissioning.sh MARKET-SLUG
./scripts/status_commissioning.sh
./scripts/stop_commissioning.sh
```

The launcher reads the existing sibling `../maker-lab/credentials.py` as
literal data. The credential file, environment files, databases, locks, and
logs are ignored by Git. Never copy secrets into this repository.

## Exit meanings

- `0`: clean operator/risk exit with flat state proven;
- `2`: startup refused; no automatic restart;
- `3`: flat state was not proven; no automatic restart and immediate operator
  inspection required;
- `4`: caught runtime/strategy failure after cleanup; no automatic restart;
- any other unexpected process death: bounded supervisor restart so the
  persistent ledger can recover identified state, with at most three fast
  deaths before the supervisor gives up.
