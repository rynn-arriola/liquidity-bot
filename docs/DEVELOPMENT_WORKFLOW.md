# Development model workflow

The operator chose this workflow for new development work. Each model hands off
and stops; the operator switches models manually. Do not silently continue into
the next phase or delegate that phase to another agent.

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
