# AI agent entry point

This repository is operated by AI agents for a human product owner who makes
the business and risk decisions and is not assumed to write code.

Before every task, read these files in order:

1. `AI_OPERATOR.md` — permanent operating and communication contract.
2. `STATUS.md` — the single current handoff snapshot.
3. `DECISIONS.md` — active choices made by the human operator.
4. `CLAUDE.md` — engineering, evidence and safety rules that apply to every AI,
   not only Claude.
5. The task-specific evidence/history file routed by `AI_OPERATOR.md`.

Lead with the bot's current state and explain findings in plain language.
Verify operational facts from the server and venue; Markdown is a handoff, not
live authority. Do not convert a watchlist idea into behavior, alter business
risk, deploy, restart, force-stop or repair the ledger outside the operator's
explicit request and the documented safety preconditions.

If `AGENTS.local.md` exists, read it too. It contains workstation-only commands
that must never be committed, including the operator's manual daily backup
task.
