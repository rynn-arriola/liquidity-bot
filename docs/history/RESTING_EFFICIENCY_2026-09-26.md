# Resting-time efficiency release — September 26, 2026

The operator asked for a flow-by-flow review, then said "Ok do all recommendation from earlier and recommendation for this 5 minute issue". Pushed; the trading changes need Command 1.

## Review evidence (MEASURED, 05:31–13:05Z, `bd1a754`)

All flows are healthy:
- 0 tracebacks;
- 32/32 slots filled on 35 of 37 top-ups;
- 3,814 post-only placements;
- 26.8 average sides resting (09-25: 23.3; 09-24: 18.7);
- 95% of order proofs from the bulk listing;
- 0 reconcile failures or 404 stalls;
- API 2.26/s average, 1 rate-limited read;
- hourly Discord report on time.

Two resting-time losses were found:

1. **Replacement after expiry.** About 2,340 entry expiries (300 s GTD). The bot learns of an expiry within 0.1 s, but replaces it after a median 27 s (p75 122 s, p90 322 s). Gaps of 60 s or less alone total 5.97 side-hours per 7.5 h.
   - Tracing shows the market is woken correctly and a deferred create is rescheduled after 1 s.
   - The wait comes from the deliberate hybrid rule that any terminal order notification blocks new POSTs until the next REST account proof, plus 20–30 s decision passes (decision latency p95 about 20 s).
2. **Contract batches losing event evidence.** 18 whole 8-market batches raised "event pregame state is unavailable" and cleared reward eligibility for about 60 s. `fetch_markets_by_slugs` already refreshes event states first, but swallowed the refresh failure, so the cause is UNKNOWN.

Fill timing does not argue against a longer lifetime. Of 3,565 entry first fills since 09-19, 37% came in minute 1, falling to 10% in minute 5, so fresh quotes fill most.

## Changes

- **Entry lifetime 600 s.** `config/entry_expiry_policy.json` is 600; the validator ceiling rises from 300 to 600 s, and the default stays 300. Entries are still shortened to the pregame flatten boundary, and reducers stay GTC. This halves expiry-driven churn, proofs and replacement gaps. Worst case: an unattended entry now rests at most 10 minutes instead of 5.
- **Event evidence diagnostic.** `[event-evidence-unfresh]` (at most once a minute) names the refresh failure reason, retry window, or missing evidence for a contract batch. Behavior is unchanged. A fix follows once the cause is measured.
- **Power guard** in `scripts/watch_host.ps1`, the existing 15-second Windows watchdog:
  - On battery at 30% or less, it saves the launch settings to ignored `data/power-guard-stop.json` and runs the ordinary graceful `stop_commissioning.sh` (cancel, flatten, reconcile, disarm).
  - Back on AC at 40% or more for 8 consecutive checks (2 minutes), it starts with the saved settings and clears the marker.
  - An invalid marker is refused.
  - `-SimulatePower battery:25|ac:80` prints the decision without acting.
  - The scheduler check shows "POWER GUARD STOP ACTIVE", and its restart script refuses while the marker exists.
  - Battery cannot protect against crashes or internet loss; the entry lifetime remains the backstop.

## Not changed (operator decision needed)

Faster replacement needs one of two approaches:
- (a) treat a no-fill expiry of the bot's own GTD entry as not requiring a whole-account proof before the next create. This is a safety-rule change; the existing comment cites an incomplete private stream that may race fills.
- (b) reduce decision-pass latency, which is a larger engineering change.

## Verification

- Expiry policy tests: the 600 ceiling, 601 rejected, the production config at 600, and the flatten boundary kept.
- Event diagnostic test: the reason is logged once a minute, stays quiet when fresh, and the entry decision is unchanged.
- Power guard dry runs:
  - battery 25/30% → stop; 31% or AC → none;
  - marker with AC 39% → none; 55% → restart (auto 15 32 hybrid);
  - battery with marker → none; malicious marker → refused.
- Full WSL suite result: see the commit.

## Command 1 activation (13:47Z)

- Graceful stop at 13:47:07Z, flat, 0 shutdown failures. 29 markets were active; scope saved.
- Gate 1 at 13:47:13Z: all zero.
- `cb8f6a029` fast-forwarded; hashes match. Trading changes versus `bd1a754`: `config/entry_expiry_policy.json`, `entry_expiry.py`, `event_guard.py`.
- Production suite: 2,347 PASS.
- Gate 2 at 13:49:29Z: all zero. `DEPLOYED_COMMIT=cb8f6a029`. Supervisor 634499 / runner 634519.

Acceptance at 13:53Z:
- warm restart readmitted 29/29;
- 40 placements, all GTD, lifetime 233–600 s (shortened near kickoff as designed);
- 25 orders on 21 markets, 0 positions, 0 unresolved/unknown;
- 0 Traceback, critical, runtime-recovery or 404;
- API 1.40/s, 0 failures.

Next, compare resting side-hours per hour, expiry replacements per hour, fills per resting hour and loss per fill against 05:31–13:05Z (26.8 average sides; about 310 expiries/h; 125 fills per 7.5 h).

## 24/7 laptop host (operator, after Kalshi D-144; automatic Windows sign-in enabled)

Before this change, after a reboot or the loss of both processes the Windows watchdog ran recovery-only, proved flat, and then left trading paused "for operator review". Three fast crashes latched attention permanently.

- `host_recovery resume TOKEN`: only the exact live armed/recovering generation may resume. Manual stop (disarmed), attention or a newer launch refuses. The retry budget resets so the next reboot recovers too.
- `supervise_commissioning.sh`:
  - A recovery-only exit 0 plus a successful resume switches to normal mode. Every normal preflight and ownership gate runs again.
  - A refused resume keeps the old pause.
  - Three fast deaths now give `state=COOLDOWN`: 900 s, then retry, at most 12 cycles, then attention.
  - Refusal (2), unproven flatness (3) and integrity failure (4) still latch attention immediately.
- Scheduler: `SELF-HEALING` classification, and the restart script refuses during a cooldown.
- Tests:
  - resume after flat recovery;
  - refused resume keeps the pause;
  - 39 runs across 12 cooldowns, then bounded attention;
  - resume refuses stop, attention and superseded launches, and resets the budget.
  - Deliberate reversals each fail.

## Shutdown guard: built, then withdrawn (operator: "Ok remove it")

A shutdown guard was pushed in `7f505f92e`, never installed, and reverted in `77806cf57`. The reasons:
- Windows Update restarts can force-close it, so it would work only sometimes.
- Windows 11 Home lacks shutdown scripts.
- Reboots are already bounded by the 10-minute entry expiry and flat-proving host recovery, which resumes trading in `afb8497`.

Operator guidance instead:
- Set wide Windows Update active hours (e.g. 08:00–02:00), or pause updates.
- When restarting the laptop yourself, restart without stopping the bot. A manual stop disarms the watchdog, so the bot would stay off after the reboot.

## Command 1 activation of the 24/7 host (14:45Z)

- Graceful stop at 14:45:48Z, flat, 0 shutdown failures. 31 active markets; scope saved.
- Gate 1 at 14:45:55Z: all zero.
- `29773e246` deployed. Code changes versus `cb8f6a029`: `scripts/supervise_commissioning.sh` and `src/liquidity_bot/host_recovery.py`. Hashes match.
- Production suite: 2,351 PASS.
- Gate 2 at 14:48:07Z: all zero. `DEPLOYED_COMMIT=29773e246`. Supervisor 653525 / runner 653539 (new supervisor script).

Acceptance at 14:51Z:
- warm restart readmitted 31/31;
- 38 placements, GTD 206–600 s;
- 25 orders on 21 markets, 0 positions, 0 unresolved/unknown;
- 0 errors;
- API 1.33/s.

The Windows watchdog reports `process_present` against the new `host_recovery` (armed generation).

A real reboot has not been exercised yet. Suggested: one operator restart at a quiet time, without stopping the bot. Expect the watchdog recovery-only pass to prove flat, then "resuming normal trading".
