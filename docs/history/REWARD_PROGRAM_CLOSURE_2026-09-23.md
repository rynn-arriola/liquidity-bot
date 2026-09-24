# Reward-program closure investigation — September 23, 2026

## Follow-up: replacement programs available by22:42 ET

This supersedes the low-supply counts below, not their timestamped validity.
At02:42:13Z the same runner's normal complete discovery found879 executable
markets,803 reward-linked,214 applicable periods,55 passing the catalog spread
gate and42 viable after book evaluation.588 were outside six hours, one live-only,
and76 had no reward record. No closed pregame rows or daily_event memberships
remained in this scan. No diagnostic full rescan or restart was triggered.

At02:45:06Z one six-symbol authenticated report-lane read confirmed replacement
active day-of/live programs for ITF, table tennis and doubles, with program IDs
ending `20260924`, start02:00Z and no end. UTR retained its active September23
program. This establishes replacement availability, not its exact publication
time or the reason for the earlier gap.

The public [rewards page](https://polymarket.us/rewards), inspected in the browser
around02:44Z, still listed the old ITF day-of program ($30,256 market memberships),
old table-tennis day-of ($35,374), and Politics MID daily_event ($100,108), including
both exact affected daily-event symbols. The page has no status/end column.
Its all-category counter showed151 programs/75,841 market memberships; those are
not deduplicated, active, supported or currently eligible bot markets. Website
listings and exact-market API responses were not synchronized in this observation;
the UI alone cannot prove current earning eligibility.

The [changelog](https://docs.polymarket.us/changelog) gave no matching explanation.
The [status page](https://status.polymarketexchange.com) was operational with no
September23 incident, and September24 maintenance scheduled06:00–08:00 ET.
The documented six-hour day-of and ET daily windows were unchanged.

At02:46:02Z the same supervisor21730/runner21744 were alive. Authenticated reads
showed16 orders and four positions, each with an exact-sized reducing order
(BUY15/BUY15/SELL8.72/SELL15). Positions and orders are sequential snapshots,
not an atomic ledger/ambiguity audit. This task changed only documentation;
no trading code, rules, deployment marker or restart. Remaining support question:
why old programs closed before replacements were visible and why the website
still exposed old IDs. Exact rejection diagnostics remain a proposal.

## Conclusion and scope

The sudden low order count is explained by venue-published reward-program
closures at **2026-09-24 02:00 UTC / September 23 22:00 ET**. Daily-event
memberships still exist but are closed too; their presence is not entry
authorization. No trading-code, policy, deployment or restart change was made.
The reason for the venue closures and availability of replacement programs
remain unknown. Do not call this a permanent program removal or confirmed outage.

The same supervisor21730/runner21744 remained running on release `ab3f7f352`.
The earlier statement that the bot stopped was incorrect: a process-name search
missed `liquidity_bot.live_commissioning`. Manual cancellations can be replaced
while this runner is live.

## Stored before/after evidence

Read-only discovery archives were decoded and their stored SHA-256 checked.
Counts are market memberships, not unique reward pools or guaranteed payouts.

| Complete scan UTC | Executable | Reward-linked | Period refused | Viable |
|---|---:|---:|---:|---:|
| September24 01:00:52 | 880 | 793 | Not reproduced here | 24 |
| September24 02:00:56 | 876 | 810 | 806 | 2 |
| September24 02:30:44 | 870 | 804 | 800 | 3 |

At01:00, all819 non-live program memberships had no end timestamp; the two
daily-event memberships were active. At02:00,834 non-live memberships had an
explicit02:00 end and six retained no end. Both daily memberships were closed.
Those two daily markets had also failed the2c spread gate at01:00, so closure
does not imply they otherwise would have been trading at that exact moment.

Exhaustive classification of the02:30 scan's870 executable markets:

- 797 had closed/nonactive pregame programs.
- Two had active day-of programs but were outside the six-hour entry window.
- One had only a live-game reward program.
- 66 had no reward record.
- Four had an applicable pregame period; one failed the2c spread gate, leaving
  three viable UTR markets.

Offline replay through the actual `select_applicable_period` matched all804
reward-linked records:800 refusals, four selections, zero unexpected mismatches.
The generic `no_applicable_period` reason combines distinct causes; it must not
be described as meaning every market is live-only or has missing reward data.

## Fresh raw API confirmation

At02:29:36Z, one authenticated read requested six exact market symbols through
the shared report limiter, with retries disabled. All six returned without a
next-page token. Only public reward-program fields were printed.

- `aec-utr-spoaid-gijnie-2026-09-24`: UTR day-of remained active, end null.
- `aec-itfme-inator-fautab-2026-09-24`: ITF day-of/live and
  `politics_mid_20260921` daily_event were closed, end02:00Z.
- `aec-wtt-cleche-anatof-2026-09-24`: table-tennis day-of/live and the same
  daily_event program were closed, end02:00Z.
- ATP doubles Cabtra/Sunzhazhi, ITF Arusag/Yinsun and table-tennis
  Steyar/Derdmy also returned closed day-of/live programs ending02:00Z.

The adapter preserved the raw UTC end/status exactly. This is not an ET/UTC
conversion defect or a six-hour loss-window boundary. Explicit program closure
overrides the ordinary daily-event entry horizon.

## Code and operational checks

Traced raw incentive pagination/batching in `adapters.py`, period selection in
`rewards.py`, and its caller in `market_selector.py`. Exact-symbol incentive
reads include closed rows; reward-linked count therefore is not active supply.
Installed runtime `test_rewards.py`:31 tests passed, covering active periods,
daily24h behavior, priority, end boundaries and ET/DST handling.

Authenticated account reads at02:33:15Z returned three venue orders: two
15-contract BUY entries and one BUY0.03 covering the sole-0.03 position.
This verifies venue exposure only, not a new ledger/unknown-state audit.
Recent02:28 API telemetry showed1.717rps with no failures/429s; earlier recovered
429s are not evidence that all of this run was error-free or caused the closure.

Official status summary checked near02:30Z reported operational/no incidents;
the scheduled September24 maintenance was06:00–08:00 ET, not active at22:30 ET.
The current changelog provided no explanation for the reward closure. Status
does not prove reward configuration is correct. The rewards webpage could not
be fetched in this check and is not corroborating evidence.

## Remaining recommendation

Ask Polymarket why these programs share a22:00 ET end and whether replacements
are scheduled. Preserve the active-period gate: bypassing it could trade with
no reward eligibility. A small future diagnostic improvement should separate
closed, not-started, outside-entry-window, live-only and missing-program reasons
using existing scan data, without extra REST or changing selection behavior.
`usable_markets.py` currently labels generic period refusals as live-only and
uses an overly broad healthy/economic-rule explanation; that wording warrants
correction, not a trading-rule bypass.
