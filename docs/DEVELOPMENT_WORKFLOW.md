# Development model workflow

The operator chose this workflow for new development work. Each model hands off
and stops; the operator switches models manually. Do not silently continue into
the next phase or delegate that phase to another agent.

## Mandatory model recommendation

Before substantive work on every new operator request, read the applicable
project routing documents and state one line in the commentary channel:
`Recommended agent: Astra — <reason>` or
`Recommended agent: 5.6 Sol — <reason>`. The recommendation names the best
agent for the current phase, not merely the agent already selected. If the
current agent is not the recommended one, stop before development work so the
operator can switch. A recommendation never delays an urgent stop or other
authorized live-safety action; perform the safety action first and state the
recommended follow-up agent afterward.

Use this routing table:

| Task or phase | Recommended agent | Boundary |
|---|---|---|
| New feature, architecture, strategy change, unfamiliar cross-system problem, or end-to-end design | **Astra** | Analyze and design only; stop before implementation. |
| Difficult incident diagnosis where the cause is uncertain or spans account state, concurrency, recovery, API behavior, or trading risk | **Astra** | Diagnose and define the repair/acceptance plan; do not implement. |
| Polymarket/API research that may change architecture, strategy, risk, or compliance behavior | **Astra** | Establish the official contract and design implication first. |
| Implementation of an approved Astra plan | **5.6 Sol** | Implement the named phase, add tests, verify, then stop for Astra review. |
| Focused fix with an already-established cause and architecture | **5.6 Sol** | Implement and test without redesigning unrelated behavior. |
| Fixes requested by an Astra review | **5.6 Sol** | Preserve the reviewed architecture and close each finding. |
| Final review of a meaningful code diff, especially live-money execution, races, recovery, security, or edge cases | **Astra** | Review only; stop for Sol fixes if findings remain. |
| Routine read-only status/log/reward/report lookup or simple support draft | **5.6 Sol** | Complete the bounded task; no Astra handoff unless uncertainty reveals a difficult problem. |
| Reviewed deployment, parity, restart, or standard operational procedure with no new design decision | **5.6 Sol** | Follow all existing live gates and authorization boundaries. |
| Urgent stop or exposure-safety operation | **Whichever agent is active** | Safety is immediate; model switching must not delay it. |

### Usage-efficient selector

**5.6 Sol is the default.** Use Astra only when at least one of these is true:
the task must choose or change architecture, the cause of a difficult/high-risk
problem is genuinely uncertain, official research may change strategy or risk,
or a meaningful live-money diff needs adversarial review. Otherwise use Sol.

Apply the smallest sufficient path:

1. Routine read, known procedure, approved implementation, or focused repair:
   use **Sol** and finish the bounded phase.
2. New design or uncertain high-risk diagnosis: use **Astra** once to produce a
   concrete plan, then switch to **Sol** for implementation.
3. After implementation, return to **Astra** only for a meaningful/high-risk
   review. Trivial documentation, formatting, or operational-only diffs do not
   require an Astra review.
4. Do not switch models repeatedly inside one phase. Escalate a Sol task to
   Astra only when evidence reveals architectural uncertainty or material risk.

This table follows OpenAI's current [model-selection
principle](https://developers.openai.com/api/docs/guides/model-selection):
establish the accuracy target with the most capable model, then optimize cost
and latency with a smaller model that preserves that accuracy. OpenAI describes
[GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model) as its
most intelligent model for multistep software-engineering and professional
workflows, and [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
as a flagship model for complex professional work. For this project, that
makes Astra the architecture and difficult-review authority and Sol the
default implementation and routine-operations workhorse. This preserves Astra
usage for decisions where its added reasoning quality matters. Re-check current
official OpenAI documentation before changing this routing policy.

1. **Astra — architecture and design.** Analyze the requirements and existing
   repository. Specify the architecture, modules, data flow, interfaces,
   failure handling, safety invariants and implementation phases. Identify
   Phase 1 files, tests and acceptance criteria. Do not implement code. End
   the turn with the design handoff so the operator can switch to 5.6 Sol.
2. **5.6 Sol — implementation.** Implement Phase 1 according to the agreed
   architecture, interfaces and file structure. Add focused tests and verify
   the implementation. Stop and hand the current diff to Astra for review.
3. **Astra — difficult-problem review.** Inspect the implementation and current
   git diff for architectural violations, races, edge cases, security issues
   and incorrect assumptions. Give concrete findings and acceptance checks.
   Do not take over implementation. Stop for the Sol fixes handoff.
4. **5.6 Sol — fixes.** Implement the review findings while preserving the
   architecture, and rerun affected and complete verification. Repeat review
   and fix phases if necessary before release.

An Astra development request stops after planning even if it also asks to
build. The handoff should be specific enough for Sol to proceed without
redesigning the feature. Read-only diagnosis and explicitly authorized live
safety operations retain their existing rules; this workflow neither delays
an urgent stop nor relaxes review, deployment or restart gates in
[AI_OPERATOR.md](../AI_OPERATOR.md) and [AI_ENGINEERING.md](../AI_ENGINEERING.md).
