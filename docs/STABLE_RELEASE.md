# Stable release and rollback anchor

## First stable baseline

`v1.0.0` is the first immutable known-good production checkpoint. The tag
points to commit `744b7c17ee509e7b5e24efa2b9968aed938d5775`. It preserves the
size-10, 20-slot, six-hour liquidity-bot implementation before the proposed
dynamic quote-size work.

The checkpoint passed all 745 tests locally and on the production host,
compilation and documentation audit. Desktop, GitHub and server parity passed;
the laptop must run its own independent parity command. The server deployment
marker matched the tag when it was created. Creating the tag did not stop,
restart or alter the live bot.

Stable means the tracked implementation is a reviewed rollback reference. It
does not mean the strategy is proven profitable, every natural recovery path
has completed acceptance, or live exchange/account state can be rolled back.

## What the tag can restore

- tracked source, scripts, tests and documentation;
- the exact startup validation and size-10 operating envelope;
- a known comparison point for reviewing every later change.

It cannot restore or undo venue orders, positions, rewards, credentials,
server-local databases, logs, runtime setting files or schema migrations.

## Required rollback procedure

Never use a destructive reset of `main` and never equate a code rollback with
a flat account. When the operator requests a rollback:

1. Preserve all dirty workstation/server files and identify the suspect commits
   with `git diff v1.0.0..HEAD`.
2. Verify live process, orders, positions and unresolved ownership. Stop or
   restart only with the operator's authorization and the normal flatness rules.
3. Audit every database migration after `v1.0.0`; older code must not open a
   newer ledger until compatibility is positively proved.
4. Review the tag in a separate worktree or revert the suspect commits with new
   commits on `main`; do not rewrite shared history.
5. Run focused regressions, deliberate reversal where applicable, the complete
   suite and documentation audit. Push before deploying reviewed files.
6. Update `DEPLOYED_COMMIT`, verify server hashes and tests, then restart only
   when separately authorized and safe. Verify venue state after startup.

`v1.0.0` remains immutable. Later stable checkpoints receive new version tags;
never move or reuse this tag.
