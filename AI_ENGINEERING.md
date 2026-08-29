# Permanent AI engineering and safety rules

This file contains only durable rules. It never states current bot status,
deployment state or temporary work. Those belong in [STATUS.md](STATUS.md),
[DECISIONS.md](DECISIONS.md) and [NEXT.md](NEXT.md).

The project trades real money. Unknown state fails closed, and convenience is
never evidence of safety.

## Authority by question

| Question | Authority |
|---|---|
| What must every AI do? | [AI_OPERATOR.md](AI_OPERATOR.md) |
| What is true right now? | live server/venue checks, summarized in [STATUS.md](STATUS.md) |
| What has the operator authorized? | [DECISIONS.md](DECISIONS.md) |
| What work remains? | [NEXT.md](NEXT.md) |
| What is being measured only? | [WATCHLIST.md](WATCHLIST.md) |
| What was measured or fixed before? | root indexes plus `docs/history/` |
| What code is deployed? | server `DEPLOYED_COMMIT`, runner command and content hashes |
| What orders/positions exist? | authenticated venue reads, never the ledger alone |
| Exact enforced limits | `CommissioningConfig` plus the running command |

GitHub `main` is the tracked-file source of truth. The server is the source of
truth for the process that executes, and the venue is the source of truth for
exposure. These are different questions; never collapse them.

## Core safety rules

Read [docs/SAFETY_INVARIANTS.md](docs/SAFETY_INVARIANTS.md) before changing
execution, ownership, reconciliation, recovery, shutdown, risk, scheduling or
market selection. At minimum, preserve these rules:

- No live start without the explicit operator authorization and documented
  flat/known-state gates.
- An unknown create is never retried. An absent order is never inferred
  canceled. Only positive venue terminal proof resolves ownership.
- Any unowned working order is account-wide terminal evidence.
- Unknown order, position, fill, book, incentive, balance or transfer state
  blocks new risk.
- Durable intent is written before every mutation; fills remain idempotent.
- After a fill, building risk is canceled and only exact reducing quantity is
  worked. Reducers never use the entry-price field and entries never use the
  exit-price field.
- The pre-kickoff flatten deadline overrides the inventory clock and reward
  economics.
- A fault's blast radius must match its evidence. Per-market ambiguity may bar
  that market but must not silently weaken account-wide ownership protection.
- A stop command completing is not flat proof. Verify the final runner record
  and authenticated venue state.
- Never repair the ledger, force-stop, deploy, restart or alter business risk
  outside explicit authorization and the documented preconditions.

## Evidence rules

1. Search [FINDINGS.md](FINDINGS.md) and its routed archives before proposing a
   cause. Search [BUGFIXES.md](BUGFIXES.md) before designing a repair.
2. Current server and venue facts outrank Markdown. Markdown is durable handoff,
   not live proof.
3. Distinguish measured fact, inference and proposal. Date every money or
   performance claim and state its scope.
4. This bot's ledger and live history outrank maker-lab. Maker-lab may be a
   labeled provisional prior only when this bot has no applicable evidence.
5. Raw working-order count is not 20-slot health. Use queue scope, active owner
   state, eligibility, unresolved ownership and resting-time evidence.
6. `book_stale` event count is not severity. Use escalation depth, retirement,
   recovery progress and effect on useful work.
7. Pending rewards are not paid cash. Account equity is not bot-only P&L.
8. The ledger stops changing when the runner stops and cannot prove current
   venue exposure.

## Change and verification rules

- Inspect the whole affected flow: success, failure, timeout, recovery,
  shutdown, restart and 20-slot interaction.
- Add focused regression coverage for every fix.
- Deliberately reverse the important repair and prove the test fails; restore
  it before the final suite.
- Run focused tests while iterating, then the complete local suite.
- Review the final diff for business-policy drift, ownership weakening,
  unexpected API calls, credential exposure and stale documentation.
- Push before deployment. Deploy only reviewed files, verify local/server
  hashes and run the complete suite in the server virtual environment.
- A successful deploy command is not live acceptance. Verify the process and
  behavior after an independently authorized restart.

## Multi-machine and secret handling

- Run `python -B scripts/check_parity.py --sync ...` from each workstation.
  One workstation cannot prove the other's uncommitted files.
- A clean behind-only `main` may fast-forward. Dirty, ahead or diverged work is
  preserved for review; timestamps never choose a winner.
- Production credentials, webhook URLs, private keys and host addresses never
  enter tracked files, patches, logs or chat summaries.
- Read ignored `AGENTS.local.md` when present. Never commit it or local backup
  artifacts.

## Resource and API discipline

- The production REST budget is shared. Do not stack authenticated diagnostics
  during startup or loop expensive probes.
- Prefer read-only SQLite URI mode for diagnostics. Never copy a live WAL
  database directly; use the reviewed online backup path.
- Reporting/measurement failures must remain isolated from trading behavior
  unless the operator explicitly authorizes a strategy gate.
- A watchlist measurement is not permission to change live behavior.
- Operator displays should prefer local read-only telemetry. They must expose
  source and age, preserve unknown values, redact secrets, provide no mutation
  route and fail independently of trading startup. A local 20-slot scope is not
  authenticated current order or position proof.

## Historical routing

The pre-compaction engineering/status narrative is preserved at
[docs/history/AI_ENGINEERING_THROUGH_2026-08-27.md](docs/history/AI_ENGINEERING_THROUGH_2026-08-27.md).
Do not load it by default. Search it only when a task concerns an older incident
or rationale not indexed by the current root files.
