# Polymarket US UI maintenance entry pause — 2026-09-01

## Incident evidence

The official `https://polymarket.us/` application displayed both
`Maintenance Mode` and `Trading is paused right now` while the public status
page reported operational service and authenticated Retail API reads still
succeeded. During that overlap, one ordinary bot order filled. The API being
reachable therefore did not prove that ordinary application users could trade
against or away from the bot's resting quotes.

This is a participation asymmetry, not proof that the API, account, order book
or working-order state is invalid. It does not justify stopping account
monitoring or disabling exits.

## Reviewed behavior

The runner now observes the public US homepage independently of the credentialed
Retail gateway. The probe:

- sends no credential, cookie or venue mutation;
- accepts only the official Polymarket US hostname after redirects;
- requests gzip, limits decoded HTML to 4 MiB and times out after five seconds;
- identifies maintenance only from the exact product-scoped heading plus an
  exact trading-paused or platform-maintenance phrase;
- identifies healthy only from HTTP 200, the official product title and the
  absence of either exact pause phrase;
- treats every transport, redirect, status, size and unrecognized-page result
  as unknown.

One exact maintenance result immediately removes account-wide new-risk
authority. Flat-market quotes become undesired through the existing reconciler
and are canceled through the ordinary durable mutation path. Markets with a
position remain serviced every pass; passive reducers, cancel-confirm
sequencing, IOC exits, pre-kickoff flattening, private/account reconciliation
and hard stops are unchanged. Selection and top-up work is skipped while entry
is paused.

Unknown evidence never clears a pause. After maintenance or startup uncertainty,
three consecutive healthy observations one minute apart are required to resume.
A clean first startup observation can enable entry immediately; an unknown first
observation starts reduce-only. Periodic probes run in a worker so homepage IO
does not block socket processing or the event loop.

## Operator latch

The ignored machine-local file `data/operator-entry-pause.json` adds a second,
persistent reason to block new entries. It is controlled without a restart:

```bash
PYTHONPATH=src python -B scripts/entry_pause.py pause
PYTHONPATH=src python -B scripts/entry_pause.py status
PYTHONPATH=src python -B scripts/entry_pause.py resume
```

`resume` clears only the operator latch. It cannot override current or
not-yet-stably-recovered UI maintenance. The runner notices the local file in
its normal loop, marks the active slate dirty and publishes `ENTRY_PAUSED` to
the local dashboard cache. Discord receives one pause and one resume notice;
reason changes while already paused do not spam another alert.

## Review and edge cases

Focused tests cover exact and generic maintenance text, non-200 and unknown
pages, cross-site redirects, response-size bounds, gzip, probe exceptions,
atomic persistent manual pause, manual dominance, three-read recovery, flat
quote cancellation through the reconciler, held-position preservation and the
dashboard's live paused state. The affected runner/dashboard/probe suites pass
419 tests and the complete pinned-WSL suite passes 1,024/1,024. Deliberately
removing the frame's pause authority made the exact flat-quote cancellation
regression fail; restoring the gate made it pass again.

The code does not infer flatness, settlement, cancellation, account health or
venue maintenance from the page. It does not cancel a position reducer, retry a
mutation, change quote size/capacity/pricing/economics, or add a request to the
Retail API governor. Rollback removes the UI/manual entry gate and restores
active-scope status to RUNNING/STOPPED only; the existing API maintenance
coordinator remains independent.

## Rollout boundary

Commit `6a3cf279e` is pushed to GitHub and staged in the laptop WSL checkout.
The staged native-WSL suite passed 1,024/1,024. Deployment does not load this
runner change: the live process still names executable marker `0bcfbbc96` and
was not stopped or restarted. A separately authorized safe restart is required.
Until then the current runner cannot see the operator latch or homepage signal.

Backup-PC peer parity remains pending because its private configuration still
names the retired OneDrive checkout and overlapping peer checks were already in
progress. No remote configuration was overwritten as part of this rollout.
