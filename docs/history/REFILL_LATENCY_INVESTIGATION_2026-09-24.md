# September24 refill latency investigation and activation handoff

## Scope and current state

The operator requested investigation of low orders after cash-report activation.
This investigation changed no runtime code, configuration, trading rules or
process. The broader scanner remains paused; host auto-resume was deferred.

The preceding authorized Command1 deployed `7f68c06e9` (cash-report source
`8f5f23061`). Reviewed244-input hashes, production-environment2231-test suite
and final22 dedicated cases were reused; installed focused22 tests passed.
The initial restart was23:19:15Z. After the later WSL interruption/recovery-only
flattening, the operator explicitly authorized the unchanged23:28:55Z restart.
Supervisor405/runner419 run size15/32 hybrid; REST remains proof authority.
Four$30 six-hour settled-loss windows and all business rules remain unchanged.
The old shutdown ownership invariant is a separate open issue.

Authenticated read at23:42:43Z:27 venue orders,24 on flat markets and three
exact reducers for three positions (+15 fommyk, -15 shyant, +15 naknao).
Unresolved/unknown counts were zero. Ledger working count26 versus venue27:
these serial live reads are not an atomic equality proof. Do not claim parity
of this moving snapshot or use it as a release gate.

## Confirmed flow and evidence

1. Incremental selection runs inline before the ordinary decision sweep;
   its branch continues the loop after completion or admission deferral.
   Successful scope changes perform a pre-bind account reconciliation, close
   and reopen streams, and perform another account reconciliation.
2. At23:33:56 a refill was requested. Scan ended23:34:22 (21.1s discovery),
   then commit finished23:34:41:44.8s request-to-commit, including18.5s
   plan-to-commit. Three markets were added. Another23.8s scan at23:36:12
   needed another16.0s to commit two additions. At23:37:58 a21.2s scan added
   zero markets and correctly skipped the stream rebind.
3. Dirty-input-to-completed-decision p95 was3.072/3.649s in two quiet windows,
   then53.632/64.504s in the refill windows. This is dirty-input age, not CPU
   execution time or direct exchange-fill-to-exit latency. Historical September13
   evidence already records roughly44s top-up windows versus3.3s steady state.
4. REST's override of `_position_near_its_flatten_deadline` also returns true
   for missing/inexact reducers or unresolved position orders. The caller
   requests `post_flat` whenever that boolean becomes false, without requiring
   actual flatness. At23:33:56 and23:34:56, requests accompanied position
   corrections from projected-30 to-15, not actual zero. Restoring coverage can
   therefore cause urgent discovery while an account still has inventory.
   This is a scheduling/label issue, not proof of an actual extra venue fill.
5. Urgent cached selection is allowed for120s, but only accepted if it supplies
   every wanted free slot. Partial useful supply falls back to a full scan;
   an empty cached remainder also repeats discovery. This is a code-path
   finding, not a claim that a cache-shortfall log occurred in this sample.
6. Catalog checkpoints interrupt for changed/urgent inventory, so scans are
   not wholly blind. Deferred top-ups retry after the local1s delay and skip
   that iteration's ordinary decision branch. Generic backpressure telemetry
   hides which catalog checkpoint caused each interruption. Exact allocation
   of every slow/aborted scan is therefore not proved.

AST comparison against prior loaded `dc8f62556` confirms unchanged post-flat
request, candidate collection, top-up commit, stream rebind, deadline override
and scheduler metric functions. The cash-history change did not introduce
these mechanisms; workload and timing can expose them differently.

## REST and supply: corrected claims

Cash-history reads fell to2–4 per roughly60–93s window in this run, versus
typically12–18 in earlier windows. That confirms the intended cadence effect,
not a matched whole-bot speed or percentage-saving claim. Total rate varied
1.912–3.427/s with changing markets/fills. The23:39:59 API window contained
two429s: one balance and one positions; the next23:41:16 window had zero.
No venue throttle threshold or underlying server cause is inferred.

At23:36:12 the scan had17 book candidates,14 viable markets and23 viable
sides, all day_of. This supply limit coexists with refill latency, ordinary
touch repricing and five-minute GTD expiries. The12 EXPIRED rows observed
earlier were latest stored statuses, not a complete event count or proof all
expired because of scans. Do not claim the low count was only supply, that
cash optimization made the bot24% faster, or that all429s were eliminated.

## Recommended next work (not implemented)

- Separate urgent-position service eligibility from the condition requesting
  a refill. Restoring an exact reducer must not manufacture a new scan demand.
- Keep ordinary quoting/reducers runnable between bounded discovery work;
  interrupt safely on changing account/scope and retain one serial writer.
- Use freshly revalidated partial candidates and bounded recent no-supply
  evidence instead of requiring every empty slot before accepting the cache.
  Preserve normal discovery cadence, expiry/lifecycle urgency and ranking.
- Review the measured pre/post-bind proof and socket handoff as part of the
  same path; do not simply remove either ownership proof to save time.

Tradeoff: discovery may complete later when trading is busy, while existing
orders/positions receive earlier service. Preserve filters, timers, GTD policy,
position sizing, limits and account authority. Implementation requires explicit
authorization, focused stop/fill/scope-change/starvation regressions and an
unchanged-policy review; live timing benefit requires natural acceptance.
