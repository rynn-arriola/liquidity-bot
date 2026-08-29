# Manual weekly recovery

This workflow is separate from parity. `run parity` checks only PC, laptop and
GitHub. The phrase `run weekly task` authorizes one Drive recovery run; it does
not schedule future runs or authorize bot control.

Codex is the primary AI operator and Claude is the backup AI operator. Either
may execute the same workflow through its own connected Google Drive access.

## Required flow

1. Prefer the active laptop's newest accessible runtime state. Record whether
   the bot is running, but do not stop or restart it.
2. Use SQLite's online backup API for `data/commissioning.db` and
   `data/scalp.db`; never copy their live files, WAL or SHM companions.
3. Run `scripts/recovery_archive.py build` with the machine-local key stored
   outside every repository and cloud-synchronized folder. Its pinned
   encryption runtime is installed by the normal `pip install -e .` setup.
   The archive includes a source/role/time/commit/hash/integrity manifest,
   small non-secret runtime state and bounded diagnostic log tails.
4. Run `scripts/recovery_archive.py verify` before upload. Both restored SQLite
   databases must return `PRAGMA integrity_check = ok`.
5. Upload only the `.tar.gz.enc` file as a new versioned object in Drive folder
   `Liquidity Bot Private Recovery`. Never overwrite the only prior archive.
6. Read the Drive object back, compare its exact byte size and SHA-256, then run
   the verifier on the downloaded ciphertext. Report source role, capture time,
   commit, deployed commit, hash and age.

Credentials, environment files, recovery keys and SSH private keys are always
excluded. The archive uses streaming AES-256-GCM so the large measurement
database does not need to fit in memory. If the source, key, encryption,
connector, upload or readback is unavailable, report the weekly task pending;
never fall back to raw or plaintext material.

## Commands

Create a machine-local key once:

```powershell
python -B scripts/recovery_archive.py init-key --key-file "$env:USERPROFILE\.liquidity-bot\drive_recovery.key"
```

Build and verify commands are run on the source runtime with an ignored output
directory and explicit source role. The AI operator owns the exact machine path
and Drive upload/readback; the human operator is never expected to edit code or
handle database internals.
