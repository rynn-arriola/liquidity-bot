# AI operator contract

This project is designed, implemented, tested, documented and operated with AI
agents. The human operator is the product owner and risk decision-maker. Do not
assume the operator writes code, reads stack traces or knows which command or
file to inspect.

## Mandatory start of every AI session

Before diagnosing, changing, deploying, stopping or starting anything:

1. Read [STATUS.md](STATUS.md) for the latest handoff.
2. Read [DECISIONS.md](DECISIONS.md) for choices currently in force.
3. Read the safety and evidence rules in [CLAUDE.md](CLAUDE.md), regardless of
   which AI product is being used.
4. Read the task-specific source: [FINDINGS.md](FINDINGS.md) before forming a
   theory, [BUGFIXES.md](BUGFIXES.md) for prior production defects,
   [NEXT.md](NEXT.md) for unfinished work, or [WATCHLIST.md](WATCHLIST.md) for
   measurement-only ideas.
5. Verify live facts when the question is operational. Markdown is the latest
   handoff, not the venue. Process state comes from the server, deployed code
   from `DEPLOYED_COMMIT` plus file hashes, configuration from the running
   command and `CommissioningConfig`, and orders/positions from authenticated
   venue reads.

Do not silently continue from the last chat message. Chats compact and end;
these files are the durable shared memory.

The tracked root `AGENTS.md` is the automatic Codex entry point, `CLAUDE.md`
is the automatic Claude entry point, and the top of `README.md` routes other
agents. A workstation may also have an ignored `AGENTS.local.md`; read it when
present, but never commit it.

## How to work with the operator

- Lead with the result and current bot state.
- Explain technical findings in plain language before naming implementation
  details. Translate an error into what it means for orders, positions,
  rewards and risk.
- Never ask the operator to edit code or interpret raw logs. The AI owns code
  reading, implementation, tests, mutation checks, documentation and command
  execution.
- Present a recommendation with its benefit, downside and operational risk.
  Clearly separate measured fact, inference and proposal.
- The operator decides business behavior and risk: strategy rules, quote size,
  slot count, loss limits, capital, deployment timing, and stop/restart timing.
  Do not turn a watchlist idea into trading logic without that decision.
- Read-only diagnosis is allowed without repeated permission. A live mutation,
  strategy/configuration change, deploy, restart, force-stop or ledger repair
  must be within an explicit operator request and must satisfy the safety
  preconditions.
- If the bot is live, say so. If it is stopped, say so. If exposure is unknown,
  say `unknown` and verify it; never fill the gap from the ledger.

## Standard explanation format

For a meaningful finding or proposed change, cover these five questions in
normal conversational language:

1. What is the bot doing now?
2. What happened, and what evidence proves it?
3. Why does it matter to orders, rewards or losses?
4. What do you recommend, including the tradeoff?
5. What decision, if any, belongs to the operator?

Avoid dumping internal class names, stack traces or database rows unless they
are evidence the operator asked to see.

## Required operating loop

1. Establish server process state and, when exposure matters, authenticated
   venue orders and positions.
2. Search the existing findings before proposing a cause.
3. Inspect the complete affected flow, including failure, recovery, shutdown,
   restart and 20-slot interactions.
4. Explain the diagnosis and recommendation clearly. Preserve the operator's
   decision point when the change affects business behavior or risk.
5. For a fix: add focused regressions, deliberately reverse the fix to prove a
   test fails, restore it, run the appropriate complete suite, and review edge
   cases and the final diff.
6. Push before deployment. Deploy only reviewed files, verify hashes and tests
   on the server, and restart only when authorized and safe.
7. Verify the actual live outcome rather than equating a successful command
   with a successful bot.
8. Update the durable handoff before ending the task.

## Documentation ownership

| File | Single purpose |
|---|---|
| [STATUS.md](STATUS.md) | The one current operational handoff; newest facts only |
| [DECISIONS.md](DECISIONS.md) | Active choices made by the human operator |
| [CLAUDE.md](CLAUDE.md) | Permanent engineering, safety and evidence rules |
| [NEXT.md](NEXT.md) | Work that remains, plus detailed implementation handoffs |
| [WATCHLIST.md](WATCHLIST.md) | Measurement-only ideas that are not authorized behavior |
| [FINDINGS.md](FINDINGS.md) | Measured evidence and corrected interpretations |
| [BUGFIXES.md](BUGFIXES.md) | Production defect history: symptom, mechanism and fix |
| [README.md](README.md) | Product overview and supported commands |
| [docs/COMMISSIONING.md](docs/COMMISSIONING.md) | Live execution envelope and operating lifecycle |
| [docs/SAFETY_INVARIANTS.md](docs/SAFETY_INVARIANTS.md) | Rules no fix may weaken |
| `docs/PHASE*_ACCEPTANCE.md` | Historical acceptance evidence, never current status |

Other files under `docs/` are technical references. They must not compete with
`STATUS.md` for the meaning of “current.” A historical heading containing the
words “current” or “active” is true only for its dated section.

## End-of-task handoff definition

When anything changes operational state, deployed behavior, configuration,
active decisions or the understanding of an incident:

- update `STATUS.md` in the same task;
- update `DECISIONS.md` if the operator made or reversed a policy choice;
- add measured facts to `FINDINGS.md` immediately;
- add a production code defect to `BUGFIXES.md` when fixed;
- update `NEXT.md` for remaining work;
- commit and push tracked documentation so another machine sees it;
- state explicitly whether code changed, whether the bot restarted, and the
  verified order/position state.

Do not put secrets, webhook URLs, credentials, private keys or server addresses
in tracked Markdown.
