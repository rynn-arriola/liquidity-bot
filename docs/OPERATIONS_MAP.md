# Operations map

One picture of what runs, what starts what, which file means what, and who may
restart the bot. It is a technical reference written on 2026-09-26 (architecture review finding 7). For
the live state, `STATUS.md` wins.

## Where things run

| Place | What |
|---|---|
| WSL `Ubuntu-24.04`, user `polybot`, `/home/polybot/liquidity-bot` | The production checkout. The trader, supervisor, TV dashboard, systemd timers and log rotation run here. Its HEAD can be ahead of `DEPLOYED_COMMIT` after restart-free (report-only) deploys. |
| Windows `C:\Users\rynna\Bots\liquidity-bot` (`/mnt/c/...` in WSL) | The development checkout and GitHub remote. The watchdog, power guard, flat gate and status-scheduler scripts run from here. |
| Claude desktop app (Windows) | Two scheduled tasks: the Polymarket "Bot status scheduler" and the Kalshi status scheduler. They only work while the app is open. |

## Processes and schedules

| What | Started by | Does |
|---|---|---|
| `scripts/supervise_commissioning.sh` | `scripts/start_commissioning.sh <target> <size> <slots> <source>` (today `auto 15 32 hybrid`) | Launches the runner and reacts to its exit code (below). Logs to `logs/supervisor.log`. |
| `python -m liquidity_bot.live_commissioning` (the runner / trader) | The supervisor | Trades. stdout/stderr are appended (`>>`) to `logs/commissioning.log`. |
| `python -m liquidity_bot.tv_dashboard` on port 8787 | `scripts/start_tv_dashboard.sh` | Read-only TV page. It reads only the last 128 KB of the log. |
| Windows logon task "LiquidityBot Host Recovery" → `scripts/watch_host.ps1` | Windows sign-in (installed by `scripts/install_host_watchdog.ps1`) | Every 15 s it runs `host_recovery tick` in WSL. It also holds the power guard. |
| systemd `liquidity-reward-table.timer` | Hourly at :07 UTC | Posts the hourly Discord reward table. |
| systemd `liquidity-changelog.timer` | Hourly at :23 UTC | Posts Polymarket changelog changes. |
| systemd `liquidity-ledger-backup.timer` | Daily at 04:17 UTC | Backs up the ledger. |
| systemd `liquidity-scheduled-probe.timer` | Disabled | Not in use. |
| `polybot` user crontab: `scripts/rotate_logs.py` | Daily at 03:17 local | logrotate decides weekly. `commissioning.log` → `commissioning.log-YYYYMMDD`, compressed from the second rotation, kept for 520 weeks. Uses `copytruncate`, so the runner never needs a restart. |
| `polybot` user crontab: `scripts/reward_pulse.py` | Daily at 05:10 local | **Reward Pulse**: grades the last 3 market days, each league and each market Hot/Good/Average/Low/Dead against our own 3-week weighted normal, with pool/crowd/share drivers and an unpaid-despite-share list. Read-only (no API). Output `data/reward-pulse/<day>.md|json` and `latest.md`. |
| Claude "Bot status scheduler" | Wakes at :13/:43 | Runs a full check only when the last check and the operator's latest chat are both ≥ 115 min old. Writes to `C:\Users\rynna\Bots\bot status 2 hour check.md`. Its guarded scripts are in `.local-tools/status-check/` (machine-local). |

## Runner exit codes (supervisor response)

| Code | Meaning | Supervisor |
|---|---|---|
| 0 | Clean stop | Disarms host recovery; no restart |
| 2 | Startup refused | Attention; no restart |
| 3 | Exited without proving flat | Attention; no restart. Check venue exposure. |
| 4 | Runtime or strategy integrity failure | Attention; no restart |
| 5 | Recoverable failure, proven flat | Restarts |
| other | Unexpected death | Restarts with backoff |

- **Fast deaths:** repeated fast deaths trigger `state=COOLDOWN`, a 15-minute wait, then a retry, for up to 12 cycles. After that the supervisor goes to attention.
- **Recovery-only mode (after a host loss):** exit 0 plus a successful `host_recovery resume` switches to normal trading. Any other result pauses in `recovered` or `attention`.

## State files (production `data/` unless noted)

| File | Meaning |
|---|---|
| `commissioning.db` (+ `-wal`, `-shm`) | The durable ledger and the single source of truth for owned orders, intents and positions. Only the runner writes it; gates read it read-only. |
| `host_recovery.json` | Watchdog arming. The status is one of:<br>• `armed`: bot started normally, and the watchdog may relaunch it after a host loss;<br>• `recovering`: a relaunch is in progress (at most 3);<br>• `disarmed`: the operator stopped it, so nothing may restart it;<br>• `attention`: a human is needed;<br>• `recovered`: flat, paused for review. |
| `host_control.lock` | flock taken by start/stop/host recovery so they never overlap. |
| `commissioning.lock` | The runner's single-instance lock. |
| `warm-restart-scope.json` | Markets the last runner was bound to. A restart readmits these first. |
| `commissioning_quote_size`, `commissioning_slot_capacity` | Persisted start settings (15, 32). |
| `account-source-selection.json`, `account-source-status.json` | Hybrid/REST account-source choice and health. |
| `api-rate-limiter.state` | Shared API budget across processes. |
| `tv-live-pnl.json`, `tv-gain-snapshot.json`, `tv-active-scope.json`, `settlement-resolutions.json` | Report and dashboard caches. They are safe to lose; they are rebuilt. |
| `logrotate.state` | Last log rotation time. |
| Windows `data/power-guard-stop.json` | The power guard stopped the bot on battery ≤ 30%. The watchdog restarts it with the saved settings after 2 minutes on AC at ≥ 40%. Nobody else may restart while it exists. |
| `DEPLOYED_COMMIT` (checkout root) | The commit the running trader was started from. Only a restart deploy (Command 1 or the scheduler's crash-fix deploy) changes it. |

**Leftover files (noted 2026-09-26, not deleted).** These `.tmp` files come from interrupted atomic writes and are harmless. Delete them only with the operator's approval:
- `.tv-active-scope.json.2862413.tmp` (Sep 10);
- `account-source-status.json.7331acc1….tmp` (Sep 23);
- `account-source-status.json.bb58ac1b….tmp` (Sep 22).

`scalp.db` (1.35 GB, last written Aug 28) belongs to the inactive scalp experiment.

## Who may stop or restart the bot

| Who | When | Guard |
|---|---|---|
| Operator | Any time. "Command 1" = full stop → flat gate → deploy → flat gate → restart → acceptance (see `AI_OPERATOR.md`). | `scripts/stop_commissioning.sh` disarms first, so nothing else restarts it. |
| Supervisor | Exit 5 or an unexpected death | Never after exit 2/3/4. Crash loops are bounded by cooldowns. |
| Windows watchdog | WSL or the bot vanished while armed (reboot, crash) | At most 3 relaunches; recovery-only mode must prove flat before normal trading resumes. |
| Windows power guard | Battery ≤ 30% (stop); AC ≥ 40% for 2 min (start) | Graceful stop only; the marker blocks every other restart. |
| Claude status scheduler | CRASHED or STUCK, or a critical code fix | `pm_restart.sh` refuses when any of these hold:<br>• disarmed;<br>• power-guard marker;<br>• cooldown;<br>• dirty or undeployed code;<br>• not flat (`scripts/release_gate.py --require-flat`);<br>• it already restarted within 6 h.<br>Deploys only its own shipped commits and rolls back on failure. |

**Flat gate:** `scripts/release_gate.py --positions --require-flat`, run from the production checkout. It refuses unless the venue has zero orders and zero positions, and the ledger has zero unresolved intents, unknown orders, working orders and positions.

**Log readers.** Status and diagnostic scripts read the tail of `commissioning.log` plus the newest uncompressed rotated file. Older weeks are in `logs/commissioning.log-*.gz` (`zgrep`).
