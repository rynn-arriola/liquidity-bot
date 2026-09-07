# Liquidity TV dashboard

This is the laptop-first, read-only live view for the commissioning bot. It is
designed for a browser mirrored to a monitor or TV and is deliberately outside
the trading process.

## What it proves—and what it does not

The screen reads only four local sources:

- the SQLite execution ledger opened with `mode=ro` and `query_only`;
- the tail of `logs/commissioning.log`, with credentials and webhook URLs
  redacted; and
- the local process table.
- `data/tv-gain-snapshot.json`, atomically published by the existing isolated
  hourly account report after its bounded reads complete.

It makes **zero Polymarket API calls**, has no venue client, exposes no mutation
route and never writes the ledger. Its one-second refresh therefore does not
share REST allowance with quoting, selection, reconciliation or exits.
Refreshing the screen never reruns the hourly account report. A missing,
invalid or old gain snapshot stays visibly unavailable/stale rather than
falling back to a venue call or false zero.

## P&L and gain display

The top adds a separate two-card row for `Today P&L` and `Overall P&L`; the
existing four-card runtime row remains unchanged beneath it. Today prefers the
runner's latest already-proven local account mark: settled cash plus every open
position's Retail `cashValue`, less the ET opening equity and positively
classified non-trading cash. It displays the closed/open split only when the
day began flat and every open basis is known. The hourly snapshot remains the
fallback, and a cache from a previous ET day is rejected. Overall is explicitly
labeled an estimate from an incomplete ledger plus paid gain. It uses the
runner's established bot-only formula: lifetime
ledger trading plus finalized bot-era gain since 2026-08-14. Pending is context
only and never enters Today P&L, finalized Net or Overall P&L. The runner cache
is an atomic owner-only local file and adds no dashboard venue request; a write
failure is display-only and cannot block trading.

The additional table is ordered exactly as `Date ET`, `Trading`, `Gain`,
`Pending`, `Side Hours`, `Net`. Side Hours is merged resting time per market and
side, split at ET midnight, so several simultaneous sides can total above 24 in
one day. New report snapshots use marked account trading, including carried
positions, with non-trading cash matched to the actual snapshot interval;
missing boundaries stay unavailable. Delayed midnight snapshots display `~`
because their interval is not an exact calendar day; their daily net remains
pending instead of mixing that interval with a calendar-day paid amount.
Older snapshots may contain `~` estimates. Only proven completed trading/reward
days finalize net, never the current day. `Not posted`, `Pending`
and `Unavailable` remain distinct. The visible dashboard says Gain rather than
reward while preserving the underlying accounting meaning.

The slot map prefers an atomic runner-owned snapshot of the currently bound
markets. The runner refreshes it after every binding change and after successful
preflight, marks it `STOPPED` on orderly exit, and never reads it
back into trading logic. A `RUNNING` snapshot is usable only when its publisher
PID is the exact currently detected runner and its capacity matches the
validated launcher capacity. Binding age can grow while that same process
legitimately retains its scope; no periodic cache fsync is added to the runner.

If that snapshot is missing, stopped, stale, malformed or from a different
capacity, the dashboard uses only current working-order, position and ambiguity
evidence from its read-only ledger connection. Slots without such evidence are
shown `EMPTY`; a stopped historical queue-collector scope is never presented as
current. The retired queue scope is accepted only for compatibility when it is
still explicitly `COLLECTING`, unended and no more than 120 seconds old. This
map remains a local diagnostic rather than a current venue order snapshot. The
screen distinguishes:

- `RESTING`: the ledger has a working order for the scoped market;
- `OBSERVED`: a fresh runner scope binds the market without a local working order;
- `POSITION`: the ledger has nonzero inventory;
- `ATTENTION`: unresolved intent or `UNKNOWN_ABSENT` evidence exists; and
- `EMPTY`: no market appeared in that slot.

Unknown or unavailable fields render as `—`; they never become false zeros or
flat proof. For a live-risk decision, use authenticated venue state under the
normal operating procedure.

## Automatic startup

After `start_commissioning.sh` proves the runner remained up, it starts the TV
service and asks Windows to open the default browser when running inside WSL.
This ordering matters: a refused bot startup does not open a misleading live
screen. Dashboard or browser failure only prints a warning; it never stops or
restarts a successfully started bot.

```bash
./scripts/start_commissioning.sh auto 10
```

The browser opens at `http://localhost:8787`. Press `F11` for a full-screen TV
view. The dashboard remains running after the bot stops so it can visibly show
`OFFLINE`; starting the bot again reuses the service and opens the page again.

The page is bound to the browser viewport and does not require vertical
scrolling on a landscape laptop or TV. It automatically compacts spacing,
cards, slot layout and the seven-day table as screen height decreases. Recent
activity always keeps the newest rows that fit; resizing the window recomputes
that visible tail. The layout has been verified without page or panel overflow
at 800×600, 1024×768, 1280×720, 1366×768 and 1920×1080.

Manual lifecycle commands are:

```bash
./scripts/start_tv_dashboard.sh --open
./scripts/status_tv_dashboard.sh
./scripts/stop_tv_dashboard.sh
```

`LIQUIDITY_TV_HOST` and `LIQUIDITY_TV_PORT` may be set locally before startup.
The default bind is `0.0.0.0:8787` so a private LAN display can connect, but the
application rejects non-loopback/non-private client addresses. Do not add a
router port-forward or public reverse proxy. WSL networking and Windows
Firewall still determine whether a second LAN device can reach the page; an
HDMI-mirrored browser on the laptop needs no LAN setup.

## AI operator checks

When asked whether the 20 slots are working from this screen, an AI must check
all of the following rather than quoting one number:

1. `LIVE` plus runner and supervisor `UP`;
2. local log and ledger age;
3. observed scope size **and scope age**;
4. resting orders/markets, positions and attention count; and
5. recent slot/recovery events.

An aged 20/20 scope means only that the last recorded binding had 20 markets.
It does not prove the current runner has 20 active owners. If the screen is
stale, degraded or offline, diagnose from local logs/processes first and use
one coordinated authenticated venue check only when exposure truth is needed.
