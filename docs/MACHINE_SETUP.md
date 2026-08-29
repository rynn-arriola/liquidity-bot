# Working from a second machine

Every AI session on every machine begins with
[AI_OPERATOR.md](../AI_OPERATOR.md), [STATUS.md](../STATUS.md) and
[DECISIONS.md](../DECISIONS.md). `STATUS.md` is the handoff snapshot, but the
the one active runtime and venue remain authoritative for live state.

> **Current transition (2026-08-28):** the former DigitalOcean server was
> destroyed. The laptop is the selected next primary and the desktop is cold
> standby. Exactly one machine may hold the writable live ledger and run
> authenticated processes. Old droplet commands below are historical only.

## Laptop live view

The supported local runtime is Ubuntu under WSL2 on the Windows laptop. After
the WSL launcher proves the bot stayed up, it automatically starts the isolated
TV dashboard and asks Windows to open `http://localhost:8787` in the default
browser. Dashboard failure cannot fail the bot start. Press `F11` for a
full-screen display or mirror the laptop over HDMI.

The display uses only the local read-only ledger/log/process table and zero
venue calls. See [Liquidity TV dashboard](TV_DASHBOARD.md). A second LAN device
may use `http://<laptop-LAN-IP>:8787` only when WSL networking and Windows
Firewall permit private access; never expose that port through the router.

The goal: open a laptop, desktop or phone and pick up exactly where the last
session stopped. Tracked state travels through git; credentials and the writable
ledger travel only through a deliberate stopped-runtime handoff.

## What is portable, and what is not

| | how it travels |
|---|---|
| code, AI instructions, findings and handoffs | `git pull --ff-only` |
| what is actually loaded | `DEPLOYED_COMMIT` on the one active runtime |
| authoritative trading history, logs, ledger | the one active runtime; ignored copies elsewhere are recovery only |
| Polymarket API keys, Discord webhooks | the one active runtime only; never GitHub or file sync |
| retired droplet SSH | no current endpoint; historical instructions only |

During the current laptop migration, only the selected laptop may receive the
ignored credential file and become live. The desktop copy is disaster recovery
and must not run concurrently. The destroyed server cannot be a fallback.

## Retired droplet access procedure (historical—do not run)

The host described below no longer exists. This section explains older records;
it is not a current setup or recovery procedure.

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

There is no safe way for a command running on the desktop to inspect arbitrary
uncommitted files on the laptop, or vice versa. GitHub is therefore the meeting
point: each workstation proves that its clean HEAD equals `origin/main`, and
then independently proves that production matches its deployed marker.

From each Windows workstation run:

```powershell
python -B scripts/check_parity.py --sync --host root@<DROPLET_HOST> --key "$env:USERPROFILE\.ssh\makerlab_droplet"
```

You can keep the connection details in local environment variables instead:

```powershell
$env:DROPLET_HOST = "root@<DROPLET_HOST>"
$env:DROPLET_KEY = "$env:USERPROFILE\.ssh\makerlab_droplet"
python -B scripts/check_parity.py --sync
```

Never commit those values. The command safely updates only one state: a clean
`main` checkout that is strictly behind GitHub is fast-forwarded. Otherwise it
reads git/server hashes. It does not query Polymarket, start or stop a process,
deploy code, or alter the ledger. A successful result means:

- this workstation is clean and exactly at GitHub `main`;
- server `src/` and `tests/` content match `DEPLOYED_COMMIT` after harmless
  Windows/Linux line-ending normalization;
- GitHub has no newer runner/test change waiting for deployment.

Documentation and analysis utilities can be newer than the deployed runtime;
that is reported but is not code drift. Run the command on both machines before
saying all four locations match. On a dirty or diverged checkout, stop and
preserve the local work. Only use `git pull --ff-only` on a clean checkout that
is simply behind.

**Start every coding agent from inside the repo directory.** Codex discovers
`AGENTS.md` and Claude discovers `CLAUDE.md`; both are thin pointers to the
same `AI_OPERATOR.md`. Launching elsewhere can create a session with no project
context.

```powershell
cd <path>\liquidity-bot
git pull --ff-only
claude
```

**Pull first, push last, every session.** `FINDINGS.md`, `BUGFIXES.md` and
`NEXT.md` are append-heavy and written from both machines, so working from a
stale copy conflicts in exactly the files that exist to stop work being
repeated.

AI product memory is **per-machine and does not sync**. Nothing carries between
machines except what is committed, which is why the rules, measured facts and
queue live in tracked files rather than product memory.

To confirm context loaded on a new machine, ask the session what to check
before theorising about a bug. The answer should route through
`AI_OPERATOR.md` and mention searching `FINDINGS.md` first.

## First five minutes on a new machine

```bash
git pull --ff-only
python -B scripts/check_parity.py --sync --host root@<DROPLET_HOST>
ssh -i ~/.ssh/makerlab_droplet root@<DROPLET_HOST> \
    'cd /root/liquidity-bot && cat DEPLOYED_COMMIT && ./scripts/status_commissioning.sh'
```

Then read `AI_OPERATOR.md` and follow its routing. `AGENTS.md` and `CLAUDE.md`
only make different agents discover that same source. Also read an ignored
workstation `AGENTS.local.md` when present.

Do not require `DEPLOYED_COMMIT` to equal the newest GitHub HEAD: a docs-only
commit may correctly be newer. Trust `PARITY OK`, which verifies the deployed
file contents against the marker and separately detects newer runner/test
changes. Nothing writes the marker automatically, so its hash proof remains
mandatory.

## Backups

`scripts/backup_ledger.py` runs nightly at 04:17 UTC and keeps 30 dated
snapshots in `backups/daily/`. They live on the same droplet and **do not
survive losing it**. Pulling one down is manual:

```bash
scp -i ~/.ssh/makerlab_droplet \
    root@<DROPLET_HOST>:/root/liquidity-bot/backups/daily/commissioning-*.db .
```
