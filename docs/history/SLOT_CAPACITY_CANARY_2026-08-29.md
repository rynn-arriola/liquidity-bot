# 22-slot capacity canary — 2026-08-29

## Authorization and boundary

The operator selected a controlled measurement at 22 slots while the live
runner was still configured for 20. This authorizes one flat-gated,
restart-scoped canary, not a permanent capacity increase. Quote size remains 13
and every risk multiplier remains unchanged. The $390 portfolio cap funds 30
quote-size units, so 22 fully utilized slots retain an eight-unit (36%)
cushion. A launcher invocation without an explicit capacity returns to 20.

The existing 20-slot process held a position during implementation and was
left untouched to exit naturally. Source was not deployed and the canary was
not started while that position remained.

## Pre-canary evidence

The loaded `859b891` runner produced 46 consecutive telemetry windows through
03:40Z: 6,064 attempts, zero failures, zero confirmed HTTP 429s, request rates
`1.459-2.736/s`, maximum governor wait p99 `0.223s`, response p99 `1.007s` and
maximum queue depth one. An earlier restart had 10 execution-history
Cloudflare `1015`/429 responses at only `1.813/s`, so the cause is not treated
as a simple total-rate limit and the canary stops on the first recurrence.

Offline model results for 22 were `1.70/s` under normal assumptions, `3.19/s`
under busy assumptions and `8.17/s` with the entire public feed silent. The
last scenario is intentionally not considered viable capacity. Per-owner REST
cadence, a fresh clock sample for each owner, book freshness checks and stream
recovery make it fail closed for new risk rather than manufacture fresh state.

## Implementation and verification

- A single `slot_capacity.py` authority accepts only whole values 20-22.
- The CLI, launcher and supervisor carry the exact restart-scoped value. The
  launcher default is deliberately 20; an unexpected supervised runner restart
  preserves the selected value for that same session.
- Contract reads and private subscriptions cover 22 owners in bounded `8/8/6`
  groups. Public scope remains below its documented 100-market limit.
- The local dashboard reads the validated runtime file and renders 20-22 slots
  without contacting the venue. Recovery archives include the setting.
- Focused tests passed. Deliberately restoring the old 20 maximum made the new
  22 acceptance/parser tests fail; restoring 22 made them pass.
- The complete Windows suite passed 849 tests in 58.255 seconds before the
  final timing/default assertions. The slot-focused final run passed 49 tests;
  the shared working tree then passed all 853 tests in 59.102 seconds, including
  four separately preserved taker-exit regressions that are not part of this
  canary commit. The exact deployed commit still requires the full WSL suite.

## Dedicated edge-case audit

| Area | Reviewed result |
|---|---|
| Success/boundaries | 20 remains default; explicit 20, 21 and 22 normalize; 19, fractional, non-finite and 23 refuse before credentials/network |
| Empty/full slate | Empty selection still refuses safely; 22 owners bind exactly once and shard `8/8/6`; underfill remains visible rather than fabricated |
| Timing/races | Every owner receives its own current service timestamp; stream events are drained before the decision sweep; aged books and stale account state block new risk |
| Partial/unknown state | Existing position, working/unknown order and unresolved-intent ownership gates are capacity-independent and remain fail-closed |
| API/load | Normal modeling passes; busy and silent-feed models do not. Live acceptance therefore includes bursts and rolls back on any failure/429, sustained queue or safety deadline |
| Exposure | Size and all risk caps are unchanged. Twenty-two slot-notionals fit inside the 30-unit portfolio cap with 36% cushion |
| Recovery/shutdown | Three private shards are bounded independently; maintenance cannot alter the active tuple; graceful shutdown retains independent cancel/flatten and authenticated flat-proof rules |
| Restart/rollback | The supervisor preserves 22 only within the selected session. A bare launcher invocation writes 20, providing an explicit safe rollback; no old 22 file is silently inherited |
| Reporting | CLI/TV capacity labels come from the validated runtime setting; local scope remains non-authenticated evidence and exposes no mutation route |

## Live acceptance and rollback

Start only after natural flatness, graceful stop, authenticated zero
orders/positions/unresolved/unknown, pushed reviewed source, matching deployed
hashes and the full WSL suite. Run `start_commissioning.sh auto 13 22` and observe
at least 30 continuous minutes covering startup and selection bursts.

Acceptance requires zero request failures and confirmed 429s, no limiter queue
above one in two consecutive 60-second windows, no safety deadline or stream-coverage loss, stable
supervisor/runner ownership, and zero unresolved/unknown account state. Any
breach requires the ordinary graceful stop and authenticated flat proof, then
`start_commissioning.sh auto 13 20`. Live results and final deployment evidence
must be appended here; until then 22 remains unaccepted.

## Live result

Commit `fdc426e` was pushed before deployment. After natural flatness, the old
runner stopped gracefully and the 04:08:50Z final record plus authenticated
dashboard proved zero resting orders, positions, unresolved intents and unknown
orders. The exact canary commit then passed 850/850 tests in the production WSL
virtual environment. `start_commissioning.sh auto 13 22` started supervisor
74539 and runner 74548 at 04:12:20Z; fresh 22-owner scope completed at 04:13:25Z.

The continuous acceptance interval covered 20 telemetry windows and 3,392 REST
attempts with zero failures and zero confirmed 429s. Window rates ranged from
`1.263/s` to `2.643/s`; maximum governor wait p99 was `0.226s`, maximum response
p99 was `0.919s` and maximum queue depth was one. The first startup burst made
453 attempts over 171.4 seconds with 49 mutations. A later selection burst made
195 attempts over 77.6 seconds, including 43 discovery and 124 book reads.
Repeated scans found 99, 99 and 97 viable candidates, and execution-history
reads continued without the earlier Cloudflare `1015` recurrence.

At 04:48Z the same runner remained healthy with 22 owners, 27 resting orders
across 20 markets, seven positions and zero unresolved/unknown rows. Its fresh
scope had received 27 trades with zero drops, decode failures or scope error.
A brief acknowledged cancel observed earlier cleared normally; there was no
persistent ambiguity, recovery event, safety deadline or stream-coverage loss.
The canary therefore passed. It remains a bounded measurement while 20 stays
the launcher default and permanent baseline.

Concurrent accounting/fill-role work advanced the checkout and deployment
marker to `6ae1335` without restarting these PIDs. The running process still has
`fdc426e` loaded; the staged business-logic repair is intentionally deferred to
a later authorized flat restart.
