# REST position-priority repair — September 11, 2026

## Authority and live baseline

Operator: “Then lets solve faster position handling.” This authorizes the
scheduling repair. The prior restart authorization was already fulfilled;
there is no new stop, deploy or restart authorization in this task.

Start parity: Windows/GitHub/WSL documentation HEAD `d18aa2dff`, loaded marker
`64dcefdc1`; clean tracked files. Peer PC unavailable/pending. Verified private
online ledger recovery copy captured at 13:17:38Z. No executable deployment.
Supervisor 36080 / runner 36088 remained live, `auto 10 32 rest-fallback`.
Authenticated venue at 13:20:52Z showed 31 orders and positions +5, +10, +8.41.
These are a dated snapshot, not current or flat proof.

## Evidence and cause

[Earlier natural observation](REST_FALLBACK_REVIEW_2026-09-11.md): fill at
13:08:33.714740Z, building cancel log 13:10:21.611566Z (~108s), first reducer
13:11:17.506566Z (~164s). Eight API windows had no failures or rate limits.
The existing sweep retained its original reward-ranked tuple even when a
complete account poll revealed a position in a later/already-visited owner.
Cancel-terminal priority was also serviced outside that long sweep.
These timings do not prove a realized loss attributable to scheduling.
Ledger fill `received_at` records poll start, not actual detection time.

## Narrow repair

- Default WebSocket source supplies no priority hints and keeps its order.
- REST supplies exact quantity, working-order identity/status/quantity,
  unresolved-intent identity and absent-order identity tokens. Timestamps do
  not dirty identical state; positive terminal/ownership changes do.
- After the existing account refresh, interrupt flat decisions for a held
  owner, including non-selected exit-only and already-visited owners. Re-enter
  all existing stop, unresolved-intent, isolation, backoff and freshness checks.
- Changed state may interrupt again, at most four times per owner per sweep.
  This permits sequential building cancel, incorrect-size reducing cancel,
  terminal proof and exact reducing placement while bounding entry starvation.
- If that dispatched position flattens during its proof, do not turn the
  interruption into an extra flat entry; dirty it for normal later scheduling.
- No new endpoint, independent polling worker, mutation concurrency, rate-limit
  change, relaxed proof or cached mutation authority. Existing five-second
  completed-poll cadence, 30-second proof age and admission checks remain.
- No pricing, quote size, selection/rank among flat entries, hard-loss/day
  rollover, maintenance, live-game, quantity, cap or ledger-schema change.

## Verification and deliberate reversals

Sixteen focused tests cover 32/empty/short slates; preexisting and mid-poll
fills; revisiting an earlier owner; terminal cancel and intent-only resolution;
non-selected ownership; unknown intent; backoff; stop during proof; one failed
proof per sweep; three stable positions; bounded continuously changing
inventory; flattening during proof; fractional/sign changes; default mode.

The position-path integration test uses the existing REST proof, reconciler,
driver, durable intents and fake venue. It cancels the remaining building bid,
confirms it, cancels the wrong-size offer, confirms it, then creates one exact
8.41-contract passive offer before any flat decision. ACK alone leaves the
intent unresolved and creates nothing. The existing Retail BUY_SHORT offer
price-frame mapping is retained; it is not changed to SELL_LONG.

Four in-memory reversals (files untouched) produced nine expected assertion
failures: no priority hint (six), no flatten guard (one), missing unresolved
intent token (one), changed visit bound (one). Original methods restored before
the full suite. The complete review suite passed **1,298 tests in 130.126s**
using the pinned WSL Python runtime against the review source.

Read-only current-ledger microbenchmark, 20 calls each, no API/client:
owner-set scan mean/max 0.133/0.302ms; new priority tokens 0.260/0.405ms;
ownership check 0.025/0.109ms. This measures added local scheduling cost only,
not total proof CPU, end-to-end latency or sustained live improvement.

## Dedicated final edge-case audit

- Success/empty/full: unchanged flat rank and normal 30-entry minimum cap;
  reducers remain excluded. Empty sweep does nothing. Non-selected positions
  retain exit-only classification and do not enter selection.
- Timing/races: quantity/order/intention tokens, not poll timestamps, wake
  positions. Stop is checked again on interruption. Flattening is suppressed
  as an extra entry. Repeated changes have a finite per-owner budget.
- Unknown/ownership: injected owners re-enter original quarantine guards.
  No missing order becomes terminal, no unknown create is retried; complete
  REST/admission proof remains mandatory, including for reducing creates.
- Failure/load: a failed account refresh is not retried for every injected
  owner. Priority checks are local indexed reads. Additional position decisions
  may advance existing REST work, but share the existing limiter/cadence.
- Recovery/shutdown/restart/rollback: no persistent new state or schema;
  scheduler-local tokens reset each sweep. Original shutdown/recovery paths,
  stable tags and explicit source selection are unchanged. No running process
  was modified, stopped, force-killed or restarted.
- Remaining limits: no interrupt inside an in-flight API await; REST cannot
  see unpublished/stale venue fills. Unchanged inventory deadlines retain the
  existing service cadence/backoffs. Four extra visits can be exhausted under
  heavy churn, after which normal dirty-owner service remains authoritative.
  Post-deploy natural timing/load acceptance is required; do not induce fills.

## Handoff

Final diff and dedicated edge-case audit found no business-policy, ownership,
credential, schema or unrelated-source drift. Full suite and documentation
audit passed. No deployment or restart in this task. Obtain new authorization,
stop gracefully, prove fresh
flat/known ownership, deploy pushed/reviewed source, verify native full suite
and hashes, repeat start gate and restart with unchanged persisted settings.

Final read-only runtime check at **13:33:17.166722Z / 09:33 ET** retained
supervisor 36080 / runner 36088 and marker `64dcefdc1`. Authenticated venue:
29 orders across 22 markets, three positions. Contemporaneous read-only ledger:
29 working orders, zero unknown orders and zero unresolved intents. This is
not a zero-exposure/start gate; do not restart on the strength of this snapshot.

## Authorized deployment and restart

The operator subsequently requested **“Restart the bot.”** Supervisor 36080
was stopped first; runner 36088 received SIGTERM, canceled its orders and used
the unchanged shutdown exit path. Final record at 13:36:05.528395Z reported
`flat=true`, `operator_sigterm`. No force-kill or manual venue mutation.

At **13:36:28.617670Z**, authenticated venue proved zero orders and positions;
read-only ledger proved zero working/unknown orders and unresolved intents.
Maker-lab, old runner and supervisor were absent. Pre-work parity confirmed
clean Windows/GitHub source `0f231d216`, expected pending WSL deployment and
offline peer PC; the private ledger copy was integrity/hash verified.

Reviewed the exact pushed deployment diff again: only the two scheduling source
files, focused tests and handoff/safety documentation changed. Fast-forwarded
stopped WSL from `d18aa2dff` to `0f231d216`; all **172** normalized tracked
source/test/script/web/config hashes matched. Native full-suite and fresh
second start gate were required before launch; no baseline/schema/config reset.

The stopped-runtime private recovery copy was captured and integrity/hash
verified at **13:37:58.226560Z**. The native complete suite passed **1,298 tests
in 97.952s**. After hash/test verification, `DEPLOYED_COMMIT` was set to the
exact source release `0f231d216e8102c2433bb49f13bf9187f00103f1`.

Second authenticated gate at **13:38:43.189566Z** again proved zero orders,
positions, unresolved intents, unknown orders and ledger working orders.
Started `auto 10 32 rest-fallback` as supervisor **50387** / runner **50396**.
The existing TV dashboard remained available. Startup/process creation alone
is not proof of quoting or natural position-handling latency acceptance.

Startup dropped two no-longer-pregame owners, then spent another 55.9-second
selection scan refilling those slots. Four transient event-freshness eligibility
holds subsequently recovered through the existing refresh path. No safety gate
was bypassed. Official schedule feed was HEALTHY/NORMAL at 13:40:27Z.
The first 203.4-second API window at 13:42:07Z reported **425 reads, zero
mutations, zero failures and zero rate limits**. This is startup evidence only.
First new entry was logged at **13:42:39.437122Z**.

At **13:43:33.772248Z / 09:43 ET**, authenticated venue proved **20 orders
across 20 markets and zero positions**. Contemporaneous read-only ledger showed
zero unknown orders/unresolved intents and 22 working orders; these sequential
reads overlap active order creation and are not an atomic count comparison.
The original requested settings, $90 HARD and temporary 30-entry ceiling were
preserved. No further code changes were needed during deployment/restart.

Release is loaded and ordinary entry is accepted. Sustained natural
fill/cancel/exit reaction timing and full-cap API load remain to be measured;
the flat restart cannot prove the new position-priority path has fired live.

## Natural position-priority acceptance

Read-only follow-up at **15:48:19.519897Z / 11:48 ET** proved supervisor
50387 / runner 50396 remained active on marker `0f231d216`. Authenticated venue
showed 31 orders and three exact +10 positions. Each position had one exact
10-contract SELL reducer working: M80/6666 at 0.82, Patdek/Kevedw at 0.39 and
Cilic/Modic at 0.89. Contemporaneous ledger ownership was clear.

Venue fill timestamps to first reducing-action logs:

- M80/6666: 15:39:47.475501Z to 15:39:57.849009Z = **10.374s**.
- Patdek/Kevedw: 15:41:54.745556Z to 15:42:36.231368Z = **41.486s**;
  the existing opposite entry was canceled/replaced first.
- Cilic/Modic: 15:43:13.988633Z to 15:43:27.519292Z = **13.531s**.

Median is 13.531s and range 10.374–41.486s, versus the earlier single pre-fix
163.792s observation. This is direct natural evidence that detected positions
now bypass remaining flat entries. It is only three cases and does not measure
the unpublished fill-to-REST-detection interval: ledger `received_at` can
precede venue `event_at` because it records poll start.

The latest complete window at 15:46:33Z had 737 attempts (722 reads, 15
mutations), zero failures/429s, 2.342 requests/second. One earlier window had a
single 15-second read failure and the public market socket disconnected once;
both recovered without process restart. Current action latency p95 was 4.559s,
but flat-owner decision p95 remained 325.267s. Therefore the position fix is
naturally accepted and current exposure is covered, while temporary REST still
does not equal private-WebSocket detection speed or normal flat-market latency.
