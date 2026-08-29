# Desktop cold-backup setup — 2026-08-28

The operator confirmed the liquidity bot was already running on the laptop.
No bot, authenticated venue check, Discord report, trading service, timer or
automatic failover was started on the desktop during this work.

## Prepared runtime

- Windows 11 desktop `RYNN-PC`: Intel i9-10900K, 20 logical CPUs, 31.9 GB RAM.
- Ubuntu 24.04 WSL2 with systemd and default unprivileged user `rynn`.
- Python 3.12 virtual environment and editable project dependencies in the
  Linux filesystem at `/home/rynn/liquidity-bot`.
- Git authentication uses Windows Git Credential Manager's documented WSL
  bridge; no token was copied into the repository or shell history.
- AC sleep and hibernation are disabled. DC sleep behavior is unchanged.

## Inactive recovery material

The last stopped-server execution database, scalp database, restricted
credentials, recovery archive and diagnostic archive were copied from the
Windows recovery area into `/home/rynn/liquidity-bot/data/cold-backup/`.
Every copy matched its Windows source byte-for-byte. Both SQLite databases
returned `PRAGMA quick_check = ok`; credentials compile without exposing their
contents; both compressed archives pass integrity checks. Files and the
`COLD_BACKUP_ONLY` marker are owned by `rynn` with mode `0600`.

These are historical recovery copies, not the running laptop's current ledger.
The active `data/commissioning.db`, active maker-lab credential path and API-key
environment variable are absent. No matching systemd service/timer or Python
trading process exists on the desktop.

## Verification and handoff rule

The desktop passed all 787 offline tests and every shell launcher passed
`bash -n`. Windows and WSL checkouts matched GitHub before the setup change.
The dashboard's Windows browser bridge is available, but it was not started.

The desktop must remain inactive while the laptop is live. A future handoff
requires a graceful laptop stop, authenticated venue/account proof, a fresh
stopped-runtime laptop ledger snapshot with hash and SQLite validation, explicit
activation of runtime paths, preflight, and a separate operator start decision.
