# Deferred exits and SDK transport backoff — September25

## Scope and state

Operator's “Ok fix that” authorizes the confirmed fixes following the overnight
review. Prepare/review/test/push only: no deployment, restart, strategy or risk
change. Live release remains `a4703cbe8`, supervisor37031/runner37045 since
03:33:38Z. Size15/32 hybrid and four$30 settled-loss windows remain unchanged.

At13:30:18Z a bounded authenticated report-lane read found36 venue orders:
29 flat-market entries and seven exact-side/exact-remaining-quantity reducers
for seven positions. Unresolved/unknown counts were zero; local counts agreed.
These sequential moving reads are not a stopped/atomic release gate.

## Confirmed defects and implementation

1. A locally deferred reducer changes no position/order token. The sweep's
   already-visited token then suppresses its priority even after its retry wait
   expires; it can wait behind the remaining flat slate. The03:40:10Z refusal
   in the overnight trace was followed by other owners and a03:40:29Z reducer,
   producing the measured24.250s cancel-confirm-to-replacement gap. Rearm only
   uncovered/due held owners after a delayed decision, retaining the same
   due-time, per-owner visit budget, serial executor, ownership/freshness gates,
   and service for other positions. Covered owners gain no extra priority.
   The action-driver boundary now preserves the existing bounded admission
   retry hint and logs its sanitized local cause rather than substituting the
   generic recovery delay. Never retry an uncertain network mutation.
2. Official SDK0.1.2 `APIConnectionError`/`APITimeoutError` are not builtin
   `ConnectionError`/`TimeoutError` or httpx `TransportError`. They missed the
   shared typed transport classifier, so complete account reads could fail
   without endpoint backoff. Recognize the official connection-error parent
   (which includes timeout), using the existing endpoint cooldown/probe and
   fail-closed recovery. Separate typed network failures as `@transport` in
   existing failed-read counters; retain real HTTP statuses and `@unknown`
   for unclassified failures. No diagnostic requests or additional poll loops.

The05:44–05:45Z incident explicitly contains official SDK timeout exceptions.
This classifier defect does not prove all287 overnight unknown failures were
network timeouts, nor establish a cause for every venue429.

## Review and verification

Before the source changes, the new unchanged-token retry regression failed
(held, flat0, flat1 rather than held, flat0, held) and both official-SDK error
cases failed backpressure classification. After repair they pass. Additional
tests cover covered-owner exclusion, repeated deferral's finite visit budget,
the actual action boundary's retry hint and typed transport telemetry.

Existing suite coverage must also pass for stop-during-proof, partial/final
fills, other-position service, source generations, scoped/global proof,
pending private frames, endpoint single-probe recovery, auth/schema/404
non-outages, and entry/reducer/risk behavior. No weakening of proof or admission
gates, mutation concurrency, poll cadence, position size, loss policy, pricing,
GTD, reward ranking,60s quality recovery or10s one-tick dwell.

Full production-environment isolated-stage suite passed2,274 tests from98
files in109.98s wall time. Stage: `/tmp/liquidity-periodic-checkpoint.JPnTEr`;
same installed Python3.12.3 and dependencies as production, four low-priority
test workers, input manifest and environment freeze retained there. All staged
input hashes remained unchanged. No live source edited. Final diff review
retained the existing safety/risk/strategy boundaries described above.

## Remaining work and limits

- Touch cancels are not automatically bugs. Current logic immediately cancels
  adverse/multi-tick/size/ineligible orders and retains only exactly one-tick
  less-aggressive flat entries for the approved10s dwell. No additional defect
  is proved here; do not widen this rule to manufacture fewer cancellations.
- Catalog discovery and pre/post-bind proof still run in the serial owner.
  Existing checkpoints interrupt changing/urgent inventory, but do not make
  ordinary flat quoting runnable during the entire scan/handoff. The53–63s
  refill latency remains open; these fixes do not solve it. A resumable scan
  handoff needs its own state/scope/stop review rather than skipping account
  proofs or adding another mutation executor.
- After an authorized release, use natural reducer-gap/exit timing and typed
  failure/backoff telemetry for acceptance. Do not induce fills/errors, promise
  a REST saving percentage or claim all429s eliminated.300 coverage remains
  paused. No production restart was performed for this work.
