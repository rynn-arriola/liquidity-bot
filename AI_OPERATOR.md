# AI operator contract

This project is designed, implemented, tested, documented and operated with AI
agents. The human operator is the product owner and risk decision-maker. Do not
assume the operator writes code, reads stack traces or knows which command or
file to inspect.

## Mandatory start of every AI session

Before diagnosing, changing, deploying, stopping or starting anything:

1. Read [STATUS.md](STATUS.md) for the latest handoff.
2. Read [DECISIONS.md](DECISIONS.md) for choices currently in force.
3. Read the shared safety and evidence rules in
   [AI_ENGINEERING.md](AI_ENGINEERING.md).
4. Read the compact task-specific index: [FINDINGS.md](FINDINGS.md) before
   forming a theory, [BUGFIXES.md](BUGFIXES.md) for prior production defects,
   [NEXT.md](NEXT.md) for unfinished work, or [WATCHLIST.md](WATCHLIST.md) for
   measurement-only ideas. Use the index's `rg` route into `docs/history/`
   when older evidence is relevant; never load every archive by default.
   For any Polymarket rule, API, SDK, venue behavior, venue error or official-
   documentation question, use the project's canonical go-to reference first:
   read [docs/POLYMARKET_API_REFERENCE.md](docs/POLYMARKET_API_REFERENCE.md)
   before deciding whether a guide applies to the Retail bot. For unexplained
   venue behavior, also re-open the current
   [Polymarket changelog](https://docs.polymarket.us/changelog) and its linked
   live status page before attributing the symptom to local code.
   For a rollback or stable-version request, read
   [docs/STABLE_RELEASE.md](docs/STABLE_RELEASE.md) before touching git,
   deployment files, databases or the running process.
   For the laptop live display, automatic browser behavior, or a visual 20-slot
   diagnosis, read [docs/TV_DASHBOARD.md](docs/TV_DASHBOARD.md). Its local
   snapshot is operational evidence with an age, never authenticated venue
   proof.
5. Verify live facts when the question is operational. Markdown is the latest
   handoff, not the venue. Process state comes from the server, deployed code
   from `DEPLOYED_COMMIT` plus file hashes, configuration from the running
   command and `CommissioningConfig`, and orders/positions from authenticated
   venue reads.

Do not silently continue from the last chat message. Chats compact and end;
these files are the durable shared memory.

`AI_OPERATOR.md` is the single agent-neutral entry and routing source. The
tracked root `AGENTS.md` is only a thin Codex auto-discovery pointer,
`CLAUDE.md` is only a thin Claude auto-discovery pointer, and the top of
`README.md` routes other agents here. Neither compatibility file owns separate
rules. A workstation may also have an ignored `AGENTS.local.md`; read it when
present, but never commit it.

## Desktop, laptop, GitHub and production parity

GitHub is the tracked-file synchronization point between desktop, laptop
Windows and the laptop WSL production checkout. The loaded process may be
older than a documentation/tooling-only GitHub commit. It must satisfy:

1. its `src/` and `tests/` content matches the commit named by
   `DEPLOYED_COMMIT` after expected Windows/Linux line-ending normalization;
2. GitHub has no newer `src/` or `tests/` change waiting to be deployed.

Before tracked work and again after the final push, run:

```powershell
python -B scripts/check_parity.py --sync
```

The command fetches `origin/main`, automatically fast-forwards a clean
behind-only `main` checkout, reads WSL/loaded-source evidence, and makes no venue
request or bot-control call. GitHub `main` is the source of truth for
tracked files. Commit ancestry, never timestamps, decides which copy is newer.

Machine-local `.local-tools/parity_peer.json` may name the other workstation's
SSH alias and repository path. When the peer is reachable, the command runs the
same check there and may fast-forward its clean behind-only `main`; when it is
offline, the current workstation can still pass but the peer is explicitly
`PENDING`. If either worktree is dirty, ahead or diverged, preserve the files
and resolve the discrepancy rather than pulling over them. Use
`git pull --ff-only` only from a clean behind-only checkout. Documentation or
scripts newer than the loaded runtime are allowed when runner source and tests
still match the deployed marker.

Machine-local `.local-tools/parity_runtime.json` names the laptop WSL checkout.
Parity may live-fast-forward only documentation, tests and its own parity tools;
any runner/config/launcher change is reported as requiring reviewed deployment.

When the operator says **"run parity"**, **"run parity check"** or **"run
three-way parity check"**, perform every applicable step below:

1. Run the command with `--sync` on the current machine.
2. If the SSH peer is reachable, check and safely update it in the same run. If
   it is offline, mark it pending; do not make its absence fail the available
   workstation or pretend the peer is current.
3. Inspect laptop WSL HEAD, `DEPLOYED_COMMIT` and tracked state. Create its
   execution-ledger copy through SQLite online backup, verify integrity/hash,
   and install it under ignored `data/cold-backup/laptop-runtime/` on each
   reachable workstation. It is a recovery copy, never writable authority.

The phrase authorizes those git fast-forwards and verified private ledger-copy
updates. GitHub receives tracked files only; databases, credentials, webhooks,
keys, logs, environments and caches never enter git. Parity does not use Drive,
overwrite dirty/diverged work, deploy trading code, activate the copied ledger,
hand off ownership, or start/restart the bot.

The separate phrase **"run weekly task"** authorizes one manual Drive recovery
run. Either Codex (primary AI operator) or Claude (backup AI operator) may use
its connected Google Drive access. Prefer the active laptop's newest safe
snapshot; if unavailable, label any older verified source and capture time
truthfully. Build consistent SQLite snapshots through the online backup API,
encrypt locally with `scripts/recovery_archive.py`, upload a new version to
`Liquidity Bot Private Recovery`, then read it back and verify its metadata and
hash. Never upload a raw SQLite/WAL file, unencrypted secret, recovery key or
SSH private key. Keep the recovery key outside repositories and every cloud-
synchronized folder. The weekly task is manual, not scheduled, and cannot
stop, start, restart or hand off the bot.

The unqualified instruction **"run the bot"** always means the laptop; only explicit **"run it on the computer/PC"** selects the desktop, still requiring the single-owner handoff, absent old runner, known account state, current stopped-runtime ledger snapshot and normal preflight.

**"Stop bot now," "stop it now,"** and **"just stop it"** mean immediately stop the supervisor and gracefully signal the current runner through `scripts/stop_commissioning.sh` before parity, diagnostics or authenticated reads so it cannot replace orders; never force-kill, then verify the final record and authenticated venue orders/positions, reporting unknown and leaving it stopped if proof fails. **"Restart now while flat"** means that same stop first, followed by positive post-stop flat/order-free, ownership, parity, deployment and startup gates; the operator's flat statement speeds sequencing but does not replace proof, and any failed gate leaves the bot stopped.

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
| [AI_ENGINEERING.md](AI_ENGINEERING.md) | Permanent engineering, safety and evidence rules |
| [NEXT.md](NEXT.md) | Work that remains, plus detailed implementation handoffs |
| [WATCHLIST.md](WATCHLIST.md) | Measurement-only ideas that are not authorized behavior |
| [FINDINGS.md](FINDINGS.md) | Measured evidence and corrected interpretations |
| [BUGFIXES.md](BUGFIXES.md) | Production defect history: symptom, mechanism and fix |
| [README.md](README.md) | Product overview and supported commands |
| [docs/COMMISSIONING.md](docs/COMMISSIONING.md) | Live execution envelope and operating lifecycle |
| [docs/SAFETY_INVARIANTS.md](docs/SAFETY_INVARIANTS.md) | Rules no fix may weaken |
| [docs/POLYMARKET_API_REFERENCE.md](docs/POLYMARKET_API_REFERENCE.md) | Canonical go-to reference for Polymarket rules, official sources, Retail/Institutional scope and audited implementation differences |
| [docs/STABLE_RELEASE.md](docs/STABLE_RELEASE.md) | Immutable stable tags, rollback scope and required recovery procedure |
| `docs/PHASE*_ACCEPTANCE.md` | Historical acceptance evidence, never current status |

Other files under `docs/` are technical references. They must not compete with
`STATUS.md` for the meaning of “current.” A historical heading containing the
words “current” or “active” is true only for its dated section.

The root AI files have enforced context budgets checked by
`python -B scripts/audit_docs.py`. Completed narratives move to dated files in
`docs/history/`; root `NEXT.md`, `WATCHLIST.md`, `FINDINGS.md` and
`BUGFIXES.md` stay compact indexes or active state.

## End-of-task handoff definition

When anything changes operational state, deployed behavior, configuration,
active decisions or the understanding of an incident:

- update `STATUS.md` in the same task;
- update `DECISIONS.md` if the operator made or reversed a policy choice;
- add measured facts to `FINDINGS.md` immediately;
- add a production code defect to `BUGFIXES.md` when fixed;
- update `NEXT.md` for remaining work;
- commit and push tracked documentation so another machine sees it;
- run `python -B scripts/audit_docs.py` before the final commit;
- state explicitly whether code changed, whether the bot restarted, and the
  verified order/position state.

Do not put secrets, webhook URLs, credentials, private keys or server addresses
in tracked Markdown.
