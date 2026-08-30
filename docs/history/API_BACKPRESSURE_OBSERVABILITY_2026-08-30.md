# API backpressure accounting and observability — 2026-08-30

## Scope and live boundary

This change corrects how the next runner interprets the already-reviewed
host-wide API admission policy. It does not change the aggregate 4/second gate,
the background/entry/safety lane rates, cooldown durations, retries, mutation
semantics, strategy selection, configured 22-slot width or risk limits.

The laptop bot remained live on its existing loaded source throughout review,
implementation and verification. No process was stopped or restarted, no
venue request was made and no order, position, ledger or configuration was
mutated. Deployment places the files on disk for a later separately authorized
restart only.

## Defects

`ApiAdmissionDeferred` correctly means that the local limiter declined a read
before any network request was sent. It inherits `AdapterError` so generic
adapter boundaries remain compatible, but two selector catches erased that
meaning:

- a full scan caught each deferred book as one market failure and continued;
  with the live 100-book cap, one shared cooldown could therefore inflate
  Discord `Lost to errors` toward the remaining candidate count;
- cached top-up revalidation swallowed each deferral as a nonviable market,
  then could immediately fall back from the bounded entry path to a much larger
  background discovery scan.

Separately, `AsyncIntervalLimiter.telemetry()` built all three cooldown fields
from its process-local monotonic map. A confirmed 429 from a scheduled report
or another cooperating process published a wall-clock deadline into the shared
state and governed the runner's next admission, but the runner could still log
that lane as `0.0`. This was an observability mismatch, not a missing admission
control.

The metadata-validation catch previously identified near the first selector
loop is not part of this defect: it contains no awaited API call. The matching
second path is cached `revalidate_viable`, where book reads do occur.

## Repair

- Full selection and cached revalidation re-raise local admission deferral at
  the first declined read. Genuine remote/schema failures remain scoped and
  counted per market.
- Startup, incremental top-up and full rotation recognize that signal before
  their generic adapter/error paths, preserve fail-closed startup or existing
  live state, log one secret-free `[api-backpressure]` record and retry after
  the reported delay clamped to 1–60 seconds. It is not a venue failure,
  maintenance event, quarantine trigger or Discord selection error.
- Cached revalidation does not substitute a full discovery scan after local
  deferral. The existing cached evidence remains merely a candidate source and
  every normal live authorization gate still reruns on the later attempt.
- `shared_telemetry()` reads bounded shared lane deadlines in a worker and
  merges them by maximum with local deadlines. Attempt, response and confirmed
  429 counters remain honestly process-local, so peer evidence can appear as a
  nonzero lane cooldown beside `rate_limited=0`.
- The evidence read uses non-blocking shared locking and falls back to local
  telemetry if the file is absent, contended or unreadable. It adds no venue
  request and cannot wait behind a peer's state transaction.

## Edge-case audit

| Case | Result |
|---|---|
| Successful full/cached selection | Existing ranking, viability and fresh-book gates are unchanged. |
| One local deferral in a large scan | The first deferral ends the pass; later candidates are neither called nor mislabeled. |
| Genuine malformed/remote market read | Only that market remains in `failures`; later healthy candidates continue. |
| Empty/no viable universe | Existing idle semantics remain unchanged. |
| Cached validation defers | No full-scan amplification and no scope/stream commit occurs. |
| Startup defers | Partial bindings are released, stop is honored and complete preflight is retried flat. |
| Invalid/NaN/large delay hint | The wait is clamped to the existing safe 1–60 second range. |
| Peer background cooldown | Only background telemetry is nonzero; entry and safety remain independent. |
| Local counters with peer cooldown | Counters stay local; the governing shared deadline is still visible. |
| Missing/corrupt/contended state file | Evidence falls back locally; admission retains its separate fail-closed path. |
| Event-loop and API load | File IO runs in a worker under a non-blocking read lock; zero API requests are added. |
| Ownership, mutations and shutdown | No mutation or ownership path changes; no absent state is inferred and existing stop-interrupt behavior remains. |
| Restart/rollback/capacity | Restart is still required to load this source; rollback restores prior reporting only, and the 20/22-slot policy is unchanged. |

## Verification

Focused selector, limiter and top-up tests passed before the full suite. The
two important mechanisms were deliberately reversed before deployment:
allowing selector deferral to fall into the generic market-error catch failed
the single-pass regression exactly, and ignoring the worker-read shared map
failed the peer-cooldown telemetry regression exactly. Both were restored.

The production-matching Windows-tree suite passed 926 tests in 89.490 seconds.
Source commit `0ff6e3c6a259c89287399bf3b0a27d5099493f99` was pushed before deployment,
fast-forwarded into the clean WSL checkout and passed the exact-checkout 926
tests in 69.712 seconds. `DEPLOYED_COMMIT` was then advanced to that exact
source commit. Supervisor PID 290240, live runner PID 290252 and dashboard PID
116618 were not signaled or restarted; the runner therefore correctly retains
its previously loaded source until a later operator-authorized restart.
