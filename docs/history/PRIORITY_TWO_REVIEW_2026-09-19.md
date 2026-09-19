# Priority2 reliability/performance review — September19

The operator requested Priority2 after completing the recommended recovery
fix. That fix is pushed as `8d2e27d18`, passed both1,903-test native suites and
is NOT deployed. This review is read-only; the production runner remains
`582918`, loaded `1273aadd7`, manual REST, size10/32. No strategy, API limit,
deployment, restart or private-stream probe was changed or performed.

## Scope and current exposure

Log cutoff is September19 02:52:11UTC (September18 22:52:11ET startup).
The frozen analysis capture is10:18:17UTC; completed API windows end10:18:10Z.
Current-run figures therefore include the preceding ET evening, not only
September19's calendar day. Counters are runner-local; the governor is shared.

Authenticated10:20:03.829873Z read:30 flat entry orders plus one exactSELL10
for the sole+10 cmimar/bouluk position; unknown orders0, one in-flight intent.
The sequential order/position/ledger reads are not atomic: initial ledger
order count27 differed during live turnover. At10:20:31Z the local ledger
had31 working orders, zero unresolved intents and zero unknown orders. That
later ledger observation is not another venue snapshot or flatness claim.

The earlier10:18:58Z read crossed the fill/cancel/replacement transition and
still showed the prior BUY order. Logs prove position observed10:18:56.298845Z,
builder-cancel ACK10:18:56.359808Z, positive cancel terminal10:18:57.953134Z,
and exactSELL10 reducer ACK10:18:58.188449Z:1.889604s from observation.
The later authenticated snapshot independently proves the SELL working.
The position is not yet claimed closed; its ordinary exit clock still applies.

## REST usage and failures

| Scope | Windows | Calls | Weighted requests/s | HTTP429 |
|---|---:|---:|---:|---:|
| Current run |393|78,791|2.944|23|
| Latest60 windows /63.05min |60|8,692|2.298|0|
| No held position observed in window |118|18,016|2.365|0|
| Held position observed in window |275|60,775|3.175|23|

Position buckets reconstruct existing position/flat timing events and are
approximate workload groups, not identical-market controlled experiments.
They do not prove positions themselves cause throttling. This is all the old
loaded source; lower recent traffic is NOT acceptance of the new fix.

The808 failed calls comprise785 individual-order404s and23 account-read429s:
execution history9, cash history4, balance7, positions3. No other failed API
category appears in these completed windows.404 means order-not-found, not
another rate-limit response; positive terminal/ownership proof must remain.
These totals do not independently prove each historical404 resolved.

The last429-containing window ends08:51:57.688856Z (04:51:57ET). The runner
continued afterwards; the latest60 windows have no429 and current telemetry
has no account-read cooldown. Max observed window rate3.742/s remains below
the unchanged4/s governor, but an average rate cannot identify the venue's
actual counting window or establish that no429 can recur. Individual-order
reads are the largest call category. No REST market-book category appears.

The existing capacity report labels this **canary only / above comfort
budget**, not durable32-slot acceptance. API admission wait peaked7.339s;
the latest-hour peak is7.023s. The new strategy-recovery fix removes proven
duplicate reads in one path, not every account request or every throttle.

## Scheduling, scans and public recovery

-38 completed scans: median32.2s, maximum40.2s. Latest scan ranked48 books,
  found29 viable candidates and took28.0s. Public books remain streamed;
  event/incentive/account reads still use REST.
- Median of window decision-p95 values12.4375s; maximum window-p95 150.080s.
  This metric measures oldest dirty input to completed owner decision across
  the slate, including deliberate backoff/deferral. It is NOT a150-second
  position exit or event-loop freeze. At the largest06:07:39Z window, two
  top-up deferrals, a40.2s scan and two slot commits overlapped; exact
  per-owner attribution still needs investigation before another code change.
- Median window action-p95 0.7065s, largest5.819s. Median CPU0.141core,
  peak0.185core; median event-loop p95 0.001s, largest0.0148s. These do not
  indicate sustained CPU starvation, nor prove every exit met its deadline.
- Three public-stream recovery entries at03:13:54Z,04:03:29Z and04:33:25Z.
  Subsequent entry action logs resumed38.49s,59.30s and38.01s later.
  One explicit shared-recovery resume appears04:04:06Z with one owner still
  quarantined. A duplicate teardown message is not a fourth disconnect.
  Action logs do not prove venue acknowledgement or uninterrupted reducer
  coverage. The earlier
  Priority1 review separately documents deadline/replacement gaps.

## Maintenance and supporting services

The official [changelog](https://docs.polymarket.us/changelog) was reopened:
latest displayed v0.0.91 schedules September18 05:00–07:00ET maintenance, not
a matching new cause for this overnight run. The live
[status summary](https://status.polymarketexchange.com/api/v2/summary.json)
reports production operational and no scheduled maintenance, BUT retains an
investigating [DAY-order rollover incident](https://status.polymarketexchange.com/incidents/xshfct5mn9mj).
Do not say there are zero incidents. Its notice concerns DAY orders failing
to auto-cancel at5pm, not a confirmed balance/position/429 incident. Loaded
entry-expiry configuration remains enabled300s; entries use GTD, reducersGTC,
forced exitsIOC. No new API-format/rate-limit change was established.

The bot's status-feed timeout07:15:37Z recovered to HEALTHY/NORMAL07:17:37Z.
It did not establish maintenance. The public status HTML tool failed; the
bounded official JSON read succeeded. No authenticated diagnostic was used
to infer a platform-wide outage.

- Hourly reward service last completed06:08:08ET with exit0 and journal proof
  of webhook delivery. Next07:07ET. This proves delivery/freshness, not every
  financial value; value/profitability review remains Priority3.
- Changelog monitor timed out02:23ET, then succeeded03:23,04:23,05:23ET.
  Latest service exit0; next06:23ET. An isolated recovered network timeout
  does not need an emergency trading restart.
- Windows watchdog task is running; its fresh10:19:18Z status is
  `process_present`. Runner and supervisor are present. No failover test.
- Nightly backup completed00:17ET exit0,739,237,888bytes. The later parity
  online copy was integrity/hash-verified10:13:10Z,758,247,424bytes. Approximate
  growth19.0MB over5.93h; not a leak or long-term growth forecast.
- Log file approximately131MB; runner RSS approximately1.50GB; WSL available
  memory13,006MiB, swap usage0. Windows physical drive free approximately694GB;
  ample current headroom. WSL's virtual disk capacity alone was not used as
  physical-space proof. No memory-growth history was collected.
- Private WebSocket was not retested: manual REST remains selected, and that
  measurement is explicitly on demand. Public-stream success does not prove
  private balance/position delivery.

## Recommendation / remaining work

No new fatal fault or need for an immediate stop was established. Reliability
is not flawless:23 real account-read429s and long decision-age outliers remain.
First activate the reviewed Priority1 recovery repair only on separate
authorization and normal rollout gates; compare equivalent position-bearing
windows and deadline outcomes afterwards. Do not raise API limits, weaken
proof or silently change strategy/capacity to suppress errors. Investigate
the per-owner150s scheduling outlier before deciding on additional performance
work. Reward accuracy/profitability and later measurement priorities remain
separate. No broad "all fixed" or zero429 guarantee is supported.

Parity correctly reports pending deployment: Windows/GitHub contain the new
source; WSL HEAD remains`704678807` and loaded marker`1273aadd7`. This is an
intentional pending rollout, not an unexplained host fault. The offline PC
was not contacted. Review helpers/results are ignored local artifacts.
