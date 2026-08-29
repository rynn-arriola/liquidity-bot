# Working from a second machine

Every AI session on every machine begins with
[AI_OPERATOR.md](../AI_OPERATOR.md), [STATUS.md](../STATUS.md) and
[DECISIONS.md](../DECISIONS.md). `STATUS.md` is the handoff snapshot, but the
the one active runtime and venue remain authoritative for live state.

> **Current transition (2026-08-28):** the former DigitalOcean server was
> destroyed. The laptop is the live primary and the desktop is a prepared but
> inactive cold standby. Exactly one machine may hold the writable live ledger and run
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
| Polymarket API keys, Discord webhooks | active path on one runtime only; encrypted/restricted recovery copies stay inactive and never use GitHub or file sync |
| retired droplet SSH | no current endpoint; historical instructions only |

Only the selected laptop may expose credentials and the ledger at the active
runtime paths. The desktop copies live under `data/cold-backup/`, are recovery
material only and must not run concurrently. The destroyed server cannot be a fallback.

## Desktop cold-backup state

The desktop has Ubuntu 24.04 WSL2, Python 3.12 and an isolated clone at
`/home/rynn/liquidity-bot`. It has no trading service or reporting timer. Its
active ledger and credential paths are absent; owner-only recovery material is
stored under `data/cold-backup/` with a `COLD_BACKUP_ONLY` marker. AC sleep and
hibernation are disabled so an explicitly authorized manual handoff can stay up.

The recovery databases came from the stopped former server, not the currently
running laptop. They are useful for disaster recovery but are not current enough
for an ordinary handoff. Before the desktop may trade: stop the laptop, prove
the account state, transfer and verify a fresh stopped-runtime laptop snapshot,
activate the files deliberately, run preflight, and obtain explicit start
authorization. See the dated [setup evidence](history/DESKTOP_COLD_BACKUP_SETUP_2026-08-28.md).

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
python -B scripts/check_parity.py --sync
```

The command safely updates only one state: a clean
`main` checkout that is strictly behind GitHub is fast-forwarded. Otherwise it
reads local/GitHub state. It does not query Polymarket, start or stop a process,
deploy code, or alter the ledger. A successful result means:

- this workstation is clean and exactly at GitHub `main`;
- the other workstation must still run the same command independently.

Documentation and analysis utilities can be newer than the deployed runtime;
that is not permission to restart the laptop. Run the command on both machines
before saying all three locations match. On a dirty or diverged checkout, stop and
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
python -B scripts/check_parity.py --sync
```

Then read `AI_OPERATOR.md` and follow its routing. `AGENTS.md` and `CLAUDE.md`
only make different agents discover that same source. Also read an ignored
workstation `AGENTS.local.md` when present.

Do not infer the laptop's live commit, ledger or process state from another
machine's parity result. Read those on the laptop; `DEPLOYED_COMMIT` remains its
loaded-source marker and does not update automatically.

## Backups

The laptop's read-only backup timer keeps dated snapshots locally. A second
machine copy is manual: stop the live runtime, prove account state, copy the
SQLite files without live file sync, verify hashes and `PRAGMA quick_check`,
and keep them under an inactive recovery path. A same-disk snapshot is not
protection against losing that machine.
