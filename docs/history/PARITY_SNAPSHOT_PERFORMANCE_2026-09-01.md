# Parity snapshot performance repair — 2026-09-01

## Finding

The laptop parity capture ran `runtime_parity_snapshot.py` inside WSL but wrote
its output under `/mnt/c/Users/rynna/.liquidity-bot/parity`. The online backup
was correctly created and integrity-checked on native WSL ext4. After copying,
`create_snapshot()` called `verify_snapshot()` on the Windows destination, which
repeated `PRAGMA integrity_check` through the WSL/Windows 9p bridge.

The live diagnosis measured the same roughly 202 MiB snapshot at 0.78 seconds
for native WSL integrity, 1.74 seconds for native Windows integrity and 73.79
seconds when WSL queried the `/mnt/c` copy. Sequential hashing remained fast;
the cost was SQLite's random page access crossing 9p, not copy bandwidth.

The capture's `timeout=30` argument was also only an SSH `ConnectTimeout`. The
local PowerShell subprocess had no execution deadline. Separately, obsolete
test PID `521438` had consumed one core for roughly 44 hours. The exact current
test completed in 1.003 seconds, so that process was terminated without touching
the trading supervisor or runner.

## Repair

The fresh SQLite online backup still receives the full `PRAGMA integrity_check`,
table inventory, size and SHA-256 proof on native storage. After copying, the
destination manifest must exactly match the inspected manifest and the copied
database must match its size and SHA-256. Identical bytes inherit the already
proved SQLite integrity, so no second random-read database walk is performed
through `/mnt/c`.

An externally received source snapshot still receives a full integrity check
before installation. Its temporary installed copy is then size/SHA-verified;
the same-filesystem `os.replace()` is an atomic rename of those already verified
bytes, so a third full integrity walk on the renamed path was removed.

SSH connection timeout and PowerShell execution timeout are now separate. A
runtime capture has a real 180-second subprocess deadline and fails parity
closed if it expires.

## Verification

The focused parity/snapshot suites pass 30/30 and the complete pinned-WSL suite
passes 1,030/1,030. Tests prove that capture performs
one native integrity check, copied-byte corruption is rejected without a
destination SQLite walk, install integrity-checks the external source only,
connect and execution timeouts remain distinct, and subprocess expiration fails
closed.

The required deliberate reversal removed destination byte verification. The
copy-corruption regression failed because the damaged snapshot was accepted;
restoring verification made it pass.

A real live-safe capture of the 205,660,160-byte ledger through the same
`/mnt/c/Users/rynna/.liquidity-bot/parity` destination completed in 8.0 seconds,
returned integrity `ok` and destination SHA-256
`bedfdc5f5a51990e7f26362c56c5f5c73dbed62e8c548dd553a3bf1c2017f0fa`.
It made no venue request and did not stop or restart the bot or dashboard.
Native Windows installation of that same captured copy completed in 2.4 seconds
with the identical manifest and SHA-256.
