# September 25 log investigation and prioritized backlog

## Scope and authority

Operator requested investigation of the latest issues, review of the available logs, and a prioritized `NEXT.md` handoff for later work. This is documentation only: no strategy, config, source, ledger, deployment marker, stop or restart changes. Preserve size 15 / 32 hybrid, four $30 settled-loss windows, all entry/exit and ownership rules, and paused 300 coverage.

Reviewed the current commissioning run from 13:43:32 through 20:02:00 UTC (09:43–16:02 ET), with exit outcomes read again around 20:06 UTC and one authenticated exposure check at 20:08 UTC. Inspected available supervisor, dashboard, reward, backup and private-collector operational logs. Dashboard output ends September 24; the separate reward-table and backup logs end August 28, so they cannot establish today's reporting/backup health. Historical release-test files are not today's runtime errors. No permanent collector or additional market scan was started.

## Latest verified state

- Same supervisor 198478 / runner 198496, loaded `ad09519e4`, started 13:43:33 UTC.
- At 20:08:01 UTC: 28 venue orders = 24 flat-market entries plus four matching reducers. Positions were +15, +3, +11.73 and +15; each had one SELL order for the exact remaining quantity. Ledger working orders/positions agreed; unresolved intents and unknown orders were zero. Sequential live reads are an observation, not an atomic release gate.
- Through 20:02 UTC, 348 completed API windows: 60,003 attempts / 22,669.5 seconds = **2.647 requests/second**; 56,161 reads and 3,842 mutations. Failure categories: 165 order-retrieve 404s, two earnings transport failures, two order-retrieve 401s and **one cash-history 429**. Averages do not prove compliance with an unknown endpoint/IP/burst window.
- The previous zero-429 report was correct only through its 19:02 UTC cutoff. The later 19:40 UTC incident must not be omitted.
- 39 timed-exit episodes had a later same-market flat observation; none remained unmatched in that extraction. This proves observed outcomes, not every instant of coverage or venue execution quality. No reducer-decision error records appeared; the newly repaired local-deferred reducer branch was not naturally exercised.
- [Official status](https://status.polymarketexchange.com/) returned All Systems Operational, zero incidents and zero scheduled maintenance during this review. The [changelog](https://docs.polymarket.us/changelog) showed no newly published explanation for these errors. Current green status does not rule out an earlier or account/endpoint-specific incident.

## P1 — Exit coverage gaps and exact-order terminal proof

**Confirmed:** The WTT Juaper/Alialk hard exit was acknowledged at 15:15:27.466 UTC, 5.880 seconds after its deadline, and flat was observed at 15:15:32.937. Immediately afterward `position_transition_proof` failed because exact lookup of that IOC returned 404. Later reconciliation continued; the bot did not crash. Across the earlier 32 episodes, initial IOC ACK offsets were 1.8–9.0 seconds. Do not call every 404 harmless: absence is not terminal or flat proof.

An apparently slow first reducer on `aec-itfme-seyandluimig-johlitnovnov-2026-09-25` is **not** evidence of four minutes uncovered. The ledger confirms the existing BUY-15 at 0.48 remained until expiry at 15:15:31.633, covering the SELL-15 fill at 15:11:37.105. A replacement BUY-15 was acknowledged at 15:15:43.758: approximately **12.1 seconds after expiry**, worth tracing as the actual coverage gap. Do not equate time since position observation with time without a reducer.

The 19:56 hard-exit retry also worked: +4.24 became +2.23 after the first IOC, retry ACK at 19:56:38.269, flat at 19:56:41.579. The second ACK was 13.388 seconds past the original deadline; first ACK to flat was 11.461 seconds. This is a partial-fill retry, not a stuck position.

**Next work:** Reconstruct expiry/cancel → terminal proof → urgent decision → reducer/IOC ACK for the 12.1-second case and slowest deadlines using existing ledger and timing logs. Inspect `rest_fallback.py` position-transition reconciliation, `account_source_runner.py::_read_exact_order_states` and `live_commissioning.py::_rest_reconcile`. Determine whether already completed, scope-compatible terminal evidence is needlessly discarded; do not infer terminal status from 404 or broaden authority.

**Acceptance:** Replays cover GTD expiry, delayed exact lookup, stale bulk snapshots, partial IOC fills and newly arriving fills. No duplicate/non-reducing order, no unresolved state bypass, unchanged exit deadline/price/quantity rules, and bounded urgent retry. Keep shutdown's older unowned-order invariant as a separate open case; this live check does not resolve it.

## P2 — Shared public-stream recovery and transient order 401s

**Confirmed timeline (UTC):**

- 14:48:00.151 public-book socket closed normally with code 1001, `server shutting down`; shared recovery started.
- 14:48:16.301 quarantine began; bulk terminal cancellation observations appeared at 14:48:56.310.
- 14:48:57.317 exact reads of two working orders returned **AuthenticationError: API key not found**, HTTP 401. Trace IDs: `a40ad497685480d4-EWR` and `a40ad4a40d774350-EWR`.
- 14:49:00.749 affected markets had terminal cancellation evidence; private listener resumed 14:49:05.482 and an entry was placed at 14:49:05.514. About 65.4 seconds elapsed from public close to first fresh entry.

**Source check:** `adapters.py::_call` waits for admission before invoking the SDK; `live_orders.py` supplies a deferred retrieve callback. Installed SDK `async_client.py::_request` creates auth headers immediately before its HTTP call. No evidence of a signature aging in our admission queue or credentials changing. This does not establish the server-side cause of `API key not found`, or prove the public close caused the 401s.

**Next work:** Trace the shared recovery cancellation/proof fan-out and narrow auth-error handling. Preserve redacted endpoint/time/trace evidence for support if repeated. Do not blindly retry all authentication failures, rotate keys, or remove ownership checks. Separate public market-stream renewal from private account-stream delivery.

**Acceptance:** Recoverable public closure remains bounded, existing positions retain verified exit handling, true authentication failure cannot authorize new entries, and later valid evidence clears quarantine without a restart.

## P3 — Refill latency and repeated catalog invalidation

**Confirmed:** Committed lifecycle waits included 113.1 seconds at 15:19:12 UTC, 88.2 at 16:57:15, 69.4 at 17:02:28, 65.3 at 17:07:41 and 53.7 at 19:01:39. The 113.1-second pass replaced one expired owner while keeping 31. Immediately beforehand, catalog work deferred for account changes and positioned-owner priority. Current-run local deferrals: 946 hybrid-observation-pending, 172 catalog-account-changed, one catalog-position-priority and three event-entry-blocked. These explicitly sent zero requests; they are not venue rate-limit responses.

Worst scheduler dirty-input latency p95 was 81.065 seconds during shared recovery; another window reached 74.196 seconds near 20:01. These metrics are not CPU execution time or direct fill-to-exit latency.

**Next work:** Trace `AccountSourceRunner._replacement_candidates_for_top_up`, its read checkpoints, and `_top_up_active_markets` pre/post-bind proofs. Quantify discarded discovery work versus necessary account invalidation. Assess resumable discovery with a fresh final account proof and urgent-owner service, retaining a single projector/executor. Use existing recordings before adding measurements. Compare matching active-scope/churn periods; no claim of hybrid savings from unlike workloads.

**Acceptance:** Faster vacancy refill under fills/account changes without stale admission, scan-bound/filter changes, more REST load, or delayed exits. Preserve touch/quality dwell rules; their reward-versus-churn comparison remains open.

## P4 — Cash-history Cloudflare 429, not eliminated

At **19:40:10.558 UTC / 15:40:10 ET**, `GET /v1/portfolio/activities` returned HTTP 429 with a Cloudflare 1015 body. Structured telemetry:

- category `cash_history`, lane `safety`;
- workflow `financial_refresh → cash_history_refresh`;
- response duration 0.024 seconds; parsed retry-after 0.0 (not evidence of immediate safe recovery);
- CF-Ray `a40c7fc5baa21914-EWR`.

The call came from `_refresh_window_safety → fetch_nontrading_cash_changes`, not a separate Discord polling process. The containing 60.9-second window had 186 attempts (3.055/s), including only two cash-history requests, 36 execution-history, 27 positions, 33 open-order and 42 exact-order reads. Thus this does not prove cash cadence alone triggered the limit. Cash and execution history share the activities endpoint. The next completed window had zero failures, and subsequent sampled windows showed no active cooldown. No retry storm or process exit was observed.

**Next work:** Correlate existing per-request workflow/burst evidence across both activities uses and all local callers, then examine endpoint cooldown and optional-report yielding. Preserve current balances, settled-loss computation, risk-transition checks, positions and exit priority. Do not raise request ceilings, disable fallback, or change polling solely from a low aggregate average. Venue counting scope/window remains unconfirmed.

**Acceptance:** Transport failures, local deferrals and HTTP 429 remain distinct; valid Retry-After/backoff is honored, history pages remain complete, cash/balance reporting stays consistently bracketed, and safety work is not starved.

## P5 — Diagnostic hygiene and lower-priority recovery review

Raw Cloudflare HTML currently propagates into exception logs, adding noise and operational identifiers. Future fix: retain bounded sanitized error class/status/code/trace/workflow fields, omit raw HTML and sensitive headers, and test non-JSON responses. Do not lose request attribution.

There were 58 market-incentive recovery records for unavailable pregame state, nine empty-book recovery records across two components, two earnings transport failures, one cancellation-evidence-pending recovery, and a 19:58:25 cash-report consistency rejection. These are **recovery records, not proven distinct defects**. Pregame/empty-book guards must stay fail-closed. The cash report correctly refused to combine changing snapshots; do not manufacture a value to suppress that warning. Follow repeated owner starvation or reports that fail to recover using subsequent success evidence.

Legacy reward/backup log destinations are stale; locate their current owners before diagnosing reporting failure from them. Current parity performed a verified backup independently. Preserve prior measurement/watchlist work; this review is not proof that all reporting or all venue failure modes are tested.

## Verification and handoff

Existing logs and read-only local ledger queries provided the investigation. One bounded report-lane authenticated exposure read was used; no intentional fills, cancellations, faults or stress probes. Run documentation audit/diff review, commit/push this handoff and final `check_parity.py --sync --no-peer`. Full strategy tests and restart are unnecessary for these documentation-only changes. Implement the numbered items only in a later authorized task.
