# Optional book load control — September 13, 2026

## Authority and scope

After the [daytime comparison](DAYTIME_RELEASE_LOAD_COMPARISON_2026-09-13.md),
the operator said “Ok lets build from that.” This authorizes the targeted
repair and review, not deployment/restart. Production stays stopped on
`4a166336a`; retain $90 HARD, size 10 / 32, explicitly selected REST and its
maximum 30 flat pending entries. Positioned-market orders and reducers/exits
remain excluded. No ledger, policy, credential or supervisor change.

The earlier REST run averaged 1.767 requests/s versus 3.550/s after the daytime
release. This is measured load change, not proof of the venue's specific quota
or that one commit caused its first rejection. Keep the CPU index, corrected
trade-history query and safety fixes. Do not restore faults to slow execution.

## Repair

- The production shared governor admits an optional public book read at most
  once every four seconds across cooperating processes. Aggregate admission
  stays 4/s; metadata, mandatory account proof and mutation policy are unchanged.
- Discovery and startup candidate books use that allowance. A long selection
  wait continues the existing stop/position/deadline checkpoint outside every
  lock; no future aggregate/safety slot is reserved while waiting.
- Routine flat-owner book service and its immediate stale-book recovery use
  the same allowance but defer instead of sleeping through four seconds per
  owner. Local deferral neither cancels resting orders nor increments broken-
  market failure counts, renews a receipt, or authorizes a stale-price entry.
- A bounded process-local FIFO of live demand prevents the first quiet owner
  from repeatedly taking every available slot. At most 128 names are held;
  inactive demand expires after max(5 seconds, optional interval + 1). This
  list is scheduling only, not persisted ownership or a host-slot reservation.
- Held positions, ambiguity, recovery/retirement, economic replacement dwell,
  shutdown and actual non-stale-book recovery retain the safety path. Safety
  book reads do not move the optional clock and cannot starve it by extending
  that clock. A confirmed public-book 429 still applies its existing endpoint
  circuit to all book readers; a safety label cannot bypass unavailable depth.
- One successful recovery probe clears the active book circuit but no longer
  erases recent rejection history. Alternating success/429 retains bounded
  adaptive backoff; the existing 60-second quiet interval resets the streak.
- Selection recalculates time-dependent reward/pregame eligibility at completion
  of paced reads. These observations remain selection hints; execution still
  requires independently fresh book, lifecycle and complete account evidence.

## Performance limits and tradeoff

Fresh public WebSocket books remain immediately usable under their original
receipt times. Optional REST-only refill and quote refresh can be slower: 32
continuously quiet owners require about 124 seconds between first and last
optional read, plus other latency. This is not a four-second position polling
interval or a relaxation of the five-second execution-book limit. Account
polling stays completion-based 5/1 seconds with unchanged complete proof.

The earlier stopped [bounded paced scan](PRE_REST_RATE_INVESTIGATION_2026-09-13.md)
completed 100 books and 12 catalog requests without a 429 in 412.69 seconds.
It predates the final runtime/nonwaiting/fairness code; it is not acceptance of
this release, a controlled A/B quota test, or proof of live 30-entry capacity.
No new venue requests or trading mutations were made for this implementation.
Reduced optional pressure cannot guarantee no future 429 or WebSocket-equivalent
entry/fill/exit latency; mandatory safety/account traffic remains essential.

## Verification and dedicated final edge audit

Focused runner/selector/endpoint suites: 507 tests passed in 70.528 seconds.
Six deliberate repair reversals each produced the expected assertion failure
and zero errors, then restored automatically, including removal of full-width
fairness. The final complete suite passed **1,580 tests in 212.769 seconds**
using the production Python 3.12 environment against the reviewed Windows-mount
source. Temporary-directory ResourceWarnings were nonfatal; no test failed.
After completion, the final diff/edge review confirmed no additional defect
requiring a code change. Documentation audit: 134 Markdown files, 961 mandatory
lines, zero issues; whitespace diff check passed.

- Success/empty/full: real gateway plus offline fake clients; local and POSIX
  shared admission; configured factory; full 32-owner fairness simulation with
  safety position/account reads on every tick; 128-name bound/expiry.
- Timing/boundaries: exact four-second admission, no claim while waiting,
  corrupt/future timestamps bounded, checkpoint interruption, clock crossing
  game start/reward end, independent streamed receipts and unchanged 3/5-second
  quiet/fresh-book guards. Source selection remains explicitly manual.
- Failure/unknown/ownership: actual 429 remains endpoint-scoped; local refusal
  sends zero requests and preserves order/failure/receipt evidence; flat stale
  recovery cannot evade the allowance. Nonzero/absent/unresolved/recovery owners
  bypass optional pacing. Complete account proofs and no-absence inference
  remain unchanged; no mutation retry or ownership adoption was added.
- Shutdown/restart: operator stop interrupts waiting discovery; later shutdown
  safety remains available. The optional shared timestamp is bounded and
  additive; old code ignores it, process-local demand is disposable. Rollback
  must still follow normal stopped/known-state deployment gates. No database
  migration, durable clock reset, source promotion or risk/cap adjustment.
- Load/priority: optional reads cannot reserve later aggregate slots; checkpoint
  reads stay in safety and do not recursively call the checkpoint. Stale queued
  demand expires. Safety bypass affects optional pacing only, never venue 429,
  account freshness, IOC attempt/deadline, price/depth or exact quantity guards.
- Final diff reviewed for policy drift, secret exposure and scope: only the
  governor, read classification and selector clock repair plus tests/docs.

## Remaining release gates

No restart/deploy is authorized by this work. Obtain explicit release authority,
fresh authenticated flat/known ownership proof, reviewed deployment hashes and
the exact deployed suite before restart. Then monitor natural request-category
rates, 429s, admission waits, entry fill-up and reducer/IOC timing without induced
fills or load tests. Do not claim sustained acceptance from a stopped probe.
Desktop parity remains pending while offline; production intentionally stays
behind the newly prepared source.

The final process check around 05:45Z found no commissioning runner/supervisor;
the deployed marker was still `4a166336a340f99003c6e4d5810283f02e363eda`.
Last authenticated account proof remains zero orders/positions at 04:22–04:23Z,
and last ambiguity proof zero at 04:16Z. Neither is represented as a fresh
exposure query. Pre-work parity found laptop/GitHub/WSL documentation HEAD
`b4b3211b9`, intentional local source drafts and offline desktop; its verified
private ledger recovery copy was captured 05:33:19Z, 388,657,152 bytes,
SHA-256 prefix `63c773d6be11`. No copy became a writable ledger.
