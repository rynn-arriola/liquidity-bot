# Stable release and rollback anchor

## Current stable baseline

`v1.1.0` is the immutable known-good production checkpoint selected on
2026-08-31 before work on capacity beyond 24 slots. It preserves the loaded
size-10, 24-slot, six-hour implementation, the class-aware host API admission
controls, corrected account/trading reporting, breadth-scaled capital and loss
controls, safe lifecycle refill, retained-value shadow measurement and
observe-only execution-fee telemetry.

The tag's executable `src/`, `tests/` and launcher content exactly matches the
loaded source marker `4b6e4e80b39ea1960053bddf16d3e2591193fb53`.
Commits between that marker and the tagged checkpoint are documentation-only.
The deployed source passed 973/973 isolated WSL tests, and the size-10/24-slot
canary passed 26 API windows and 2,774 attempts with zero failures or 429s and
maximum queue depth one. Laptop, GitHub and WSL parity passed when the
checkpoint was prepared; the offline desktop peer remained pending and was
not overwritten.

Creating this checkpoint did not stop, restart, deploy or otherwise alter the
live bot. The production ledger, runtime settings and venue state are not part
of the tag.

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

1. Select the intended immutable stable tag (normally `v1.1.0`), preserve all
   dirty workstation/runtime files and identify the suspect commits with
   `git diff <stable-tag>..HEAD`.
2. Verify live process, orders, positions and unresolved ownership. Stop or
   restart only with the operator's authorization and the normal flatness rules.
3. Audit every database migration after the selected stable tag; older code
   must not open a newer ledger until compatibility is positively proved.
4. Review the tag in a separate worktree or revert the suspect commits with new
   commits on `main`; do not rewrite shared history.
5. Run focused regressions, deliberate reversal where applicable, the complete
   suite and documentation audit. Push before deploying reviewed files.
6. Update `DEPLOYED_COMMIT`, verify server hashes and tests, then restart only
   when separately authorized and safe. Verify venue state after startup.

Every stable tag remains immutable. Later stable checkpoints receive new
version tags; never move or reuse an existing tag.
