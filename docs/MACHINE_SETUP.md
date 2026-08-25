# Working from a second machine

The goal: open a laptop, desktop or phone and pick up exactly where the last
session stopped. Most of that already works through git. The one thing that does
not travel is SSH access to the droplet.

## What is portable, and what is not

| | how it travels |
|---|---|
| code, `CLAUDE.md`, `FINDINGS.md`, `BUGFIXES.md`, `NEXT.md` | `git pull` |
| what is actually deployed | `DEPLOYED_COMMIT` on the droplet |
| trading history, logs, ledger | droplet only — never copied to a workstation |
| Polymarket API keys, Discord webhooks | **droplet only — no workstation needs them** |
| SSH access to the droplet | per-machine, set up once (below) |

**No workstation needs the API credentials.** The bot runs on the droplet, which
already holds them. Deploying, testing, starting, stopping and querying the
ledger are all done over SSH. A workstation only needs to be able to log in.

## Authorising a new machine

Generate a key **on the machine that will use it**. Never copy a private key
between machines — that is the thing that makes keys unsafe. Copying the
`.pub` half is fine; it is public.

On the new machine — **Windows / PowerShell** (both workstations here are
Windows). Step one needs an elevated prompt, the rest do not:

```powershell
Set-Service ssh-agent -StartupType Automatic; Start-Service ssh-agent   # admin, once
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\makerlab_droplet -C "desktop-2026-08"
ssh-add $env:USERPROFILE\.ssh\makerlab_droplet
Get-Content $env:USERPROFILE\.ssh\makerlab_droplet.pub
```

macOS or Linux:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/makerlab_droplet -C "desktop-2026-08"
ssh-add ~/.ssh/makerlab_droplet          # --apple-use-keychain on macOS
cat ~/.ssh/makerlab_droplet.pub
```

**Set a passphrase.** This key is the only thing between a stolen machine and a
server running a live trading bot with funded API keys. The agent above means
you type it once per boot rather than once per command.

The cost is real and worth knowing: **an encrypted key breaks non-interactive
SSH** (`BatchMode=yes`) unless the agent holds it. Every deploy and status
command in this repo is non-interactive, so if they suddenly fail with
`Permission denied`, the agent is not loaded — run `ssh-add` again.

Then, **from a machine that already has access**, append that public key:

```bash
ssh -i ~/.ssh/makerlab_droplet root@<DROPLET_HOST> \
    "echo 'ssh-ed25519 AAAA... desktop-2026-08' >> ~/.ssh/authorized_keys"
```

Verify from the new machine:

```bash
ssh -i ~/.ssh/makerlab_droplet -o IdentitiesOnly=yes root@<DROPLET_HOST> \
    'cd /root/liquidity-bot && cat DEPLOYED_COMMIT && ./scripts/status_commissioning.sh'
```

The `-o IdentitiesOnly=yes` matters: without it SSH offers every key it knows
and the server can refuse after too many attempts.

**Do this before you need it.** As of 2026-08-16 `authorized_keys` held exactly
one entry. If that machine dies you have a live trading bot on a server you
cannot reach.

### Revoking a machine

Delete its line from `/root/.ssh/authorized_keys`. Each key carries the comment
from its `-C` flag, which is why the flag should name the machine and month.

## Phone

An SSH client (Termius, Blink) generates its own keypair — export the public
half and authorise it exactly as above. That covers operations: status, logs,
stop, start.

`claude.ai/code` gives Claude Code in a browser against the same repo. Untested
here: it runs in a sandboxed cloud environment, so it may not be able to reach
your droplet over SSH. Confirm that before relying on it for anything
operational.

The dedicated hourly reward report reads `DISCORD_REWARDS_WEBHOOK_URL` from the
same droplet-only `maker-lab/credentials.py` file as the status and error
webhooks. Keep that file mode `0600`; never paste its values into a tracked file,
cron line, command-line argument, log, or Markdown document.

## Keeping machines in sync

**Start Claude Code from inside the repo directory.** `CLAUDE.md` is read from
the working directory at session start; launching from anywhere else, or
`cd`-ing in afterwards, gets a session with no context.

```powershell
cd <path>\liquidity-bot
git pull
claude
```

**Pull first, push last, every session.** `FINDINGS.md`, `BUGFIXES.md` and
`NEXT.md` are append-heavy and written from both machines, so working from a
stale copy conflicts in exactly the files that exist to stop work being
repeated.

Claude's memory files are **per-machine and do not sync**. Nothing carries
between machines except what is committed, which is why the rules, the measured
facts and the queue all live in tracked files rather than in memory.

To confirm context loaded on a new machine, ask the session something only
`CLAUDE.md` would know — for example what to check before theorising about a
bug. The answer should mention grepping `FINDINGS.md` first.

## First five minutes on a new machine

```bash
git pull
ssh -i ~/.ssh/makerlab_droplet root@<DROPLET_HOST> \
    'cd /root/liquidity-bot && cat DEPLOYED_COMMIT && ./scripts/status_commissioning.sh'
```

Then read `NEXT.md` for what is queued and why. `CLAUDE.md` loads automatically
and carries the rules and the traps.

If `DEPLOYED_COMMIT` does not match `git rev-parse --short HEAD`, the droplet is
running something other than what you are reading. Nothing writes that file
automatically, so it goes stale silently.

## Backups

`scripts/backup_ledger.py` runs nightly at 04:17 UTC and keeps 30 dated
snapshots in `backups/daily/`. They live on the same droplet and **do not
survive losing it**. Pulling one down is manual:

```bash
scp -i ~/.ssh/makerlab_droplet \
    root@<DROPLET_HOST>:/root/liquidity-bot/backups/daily/commissioning-*.db .
```
