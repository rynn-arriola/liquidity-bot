# Terminal-order and refill recovery repair — September 25

## Scope

Operator's “Ok fix it” authorizes the concrete repairs from the [prioritized log review](LOG_INVESTIGATION_BACKLOG_2026-09-25.md). Prepare, review, test and push only. No stop, deployment, restart, account mutation, configuration or strategy change is inferred. The existing `ad09519e4` runner remains live, size15/32 hybrid with four$30 settled-loss windows;300 coverage stays paused.

The full affected flow was reviewed: private notification → serial projection → account proof → position scheduling → final order admission; exact-order readers and shared recovery; complete selector → fresh revalidation → account/release/bind checks; and activities error → shared endpoint cooldown → report/logging. No second mutation owner, faster polling loop, wider market scan or new collector is introduced.

## Implemented repairs

### P1: Prompt terminal handling without duplicate proof

Routine flat-order expiry events intentionally wait for the normal REST cadence. That same classification also delayed proof after a GTD entry had become the remaining reducer of a held position. An unsolicited positive terminal transition of a still-working positioned order now wakes REST immediately. It does not infer quantity or authorize a replacement. A bot-requested cancel is explicitly excluded: it already has immediate scoped confirmation, and waking the global poll as well would waste reads before replacement.

Exact-order read workers still collect independently and join before serial projection. After joining, hybrid mode applies held/queued private events in their existing order. A typed HTTP404 can cease blocking only when that exact owned working order just acquired positive terminal evidence, its receipt is no older than the proof start, it remains in active scope, and its owner has no unresolved or absent order. The subsequent complete order/position/history snapshots remain required. An unrelated event, missing frame, authentication error, transport failure, schema failure or ownership conflict cannot take this shortcut. Positive fills remain subject to existing idempotence and identity checks.

This addresses an avoidable recovery delay, not the venue's 404 publication behavior. The observed12.1-second coverage gap must still be compared after authorized activation; no live improvement is claimed yet.

### P3: Reuse completed refill scans after a later deferral

The run loop labels a retry as a normal due scan. Previously this disabled existing cache revalidation even when the preceding full selection completed and only its later account/bind proof deferred. Preserve an in-memory identity token for that exact completed selection. Retry may use the existing120-second path: fresh metadata, rewards, full books and normal gates, with original selection age unchanged and current owners excluded. A partial/interrupted catalog never seeds the token. Stale/future selections require the full scan, failed revalidation cannot authorize a market, and successful top-up clears the token. Source/scope/ownership release and final account proof remain independent mandatory checks.

This removes repeat discovery in the completed-scan case. It does **not** solve every catalog interruption or prove all53–113-second refills will disappear. Genuine account changes and near-deadline positions still interrupt incomplete discovery. A broader resumable scan architecture is not silently introduced.

### P5: Bound error summaries

Adapter and routine runtime-recovery summaries suppress HTML response bodies and diagnostic strings containing recognized secret/header labels, flatten whitespace and cap plain error text. Status, bounded safe error code/trace ID, Retry-After and typed exception causes remain available. Ordinary NotFound/auth descriptions stay useful. This is summary hygiene, not a claim that arbitrary exception-chain dumping elsewhere is now fully sanitized. No raw response body or credential is added to tracked evidence.

## P2 and P4: Confirmed handling, external causes still open

- The two HTTP401 responses explicitly said `API key not found`. SDK signing occurs after local request admission. There is no evidence supporting key rotation, treating auth failure as a generic outage, or ignoring that failure. Shared recovery already resumed without restart. The new terminal-drain path preserves positive events during recovery, but does not suppress401. Keep the server-side cause and65-second shared recovery profile open.
- Cash and trade history already share the activities cooldown. A new regression reproduces the observed cash429 without Retry-After: one wire attempt, a positive fallback cooldown shared with trade history, and independent cancel admission. No rate ceiling, polling interval, risk arithmetic or retry timing was changed. This is not a local fix for Cloudflare's unknown threshold.
- Missing pregame state/empty books remain fail-closed. The changing-cash reporting bracket remains enforced. No made-up value or relaxed market filter is used to eliminate warnings.

## Verification and review

Before repair, regressions failed for positioned terminal wakeup, positive terminal evidence arriving during404, repeated completed catalog scans and raw HTML/oversized diagnostics. They pass after repair. Review found and corrected an initial extra-global-proof regression: the existing REST-versus-hybrid reducer comparison now again proves identical price/quantity and one account/history proof, with hybrid avoiding the exact lookup.

Additional cases cover both position signs, flat expiry batching, unrelated/missing terminal messages,401/503, full account proof still failing after terminal evidence, partial catalog failure, expired cache, failed revalidation, unchanged cache clock, completed top-up token clearing, and activities cooldown without Retry-After. Existing source, ownership, cancellation, partial IOC, stop-during-await, financial, risk and selector coverage are included in the complete production-compatible suite.

Final production-compatible suite: **2,286 tests /100 files PASS in113.52 seconds**. Isolated stage `/tmp/liquidity-log-fixes.1HuQmJ`, Python3.12.3 with the live installed environment, four low-priority isolated workers. The stage retains `inputs.sha256` (249 source/test/script/config inputs), `environment-freeze.txt` and `test-results.txt`. Post-test input hashes were unchanged and checked against the Windows candidate. The initial candidate's failed comparison is retained separately in `/tmp/liquidity-log-fixes.caEaxh`; it is not accepted release evidence.

Final review: one projector/executor retained; no mutation retry, unknown-state relaxation, absent-as-terminal shortcut, price/size/risk/poll change or new API loop. Terminal404 supersession requires positive identity-checked evidence and still runs complete account proof. Scope/stop/freshness protections remain at admission. Scan reuse keeps existing validation and age limits. Logging-only formatting preserves typed causes and normal machine-readable error fields. No live acceptance can be claimed until a separately authorized deployment/restart.

At21:12:09 UTC, the unchanged live runner had23 authenticated orders:21 flat-market entries and two BUY15 reducers covering two -15 positions. Zero unresolved/unknown; the sequential local read counted22 working orders, so this moving observation is not an atomic parity/release gate. Another public socket code1001 occurred at21:08:38; the later account check confirms continued trading, while the21:09–21:12 API windows show no429 or active cooldown. This is old-release behavior, not acceptance of the prepared fixes.

Input comparison uses the project's CRLF→LF normalization: seven unchanged JSON fixtures differ only by Windows line endings; all249 normalized hashes match. Native post-test hashes also passed unchanged. No dependency update was performed.

## Remaining after preparation

- Authorized activation and natural acceptance: compare actual expiry-to-reducer gaps and completed-scan retry reuse; do not induce faults/fills.
- P2: server-side401 cause and total public-recovery downtime remain unresolved. Do not describe authentication as fixed.
- P3: interrupted **incomplete** discovery still yields and restarts; only completed-scan repetition is repaired. Broader resumable discovery needs separate design/evidence without losing fresh gates.
- P4: external429 threshold/cause remains unknown. Existing backoff verified, not eliminated.
- P5: retain monitoring of missing pregame/empty-book and report consistency recovery; current report/backup ownership should be located before interpreting legacy log silence. Raw exception-chain dumps outside routine summaries are not covered by the new formatting helper.
- Preserve earlier shutdown ownership, touch/quality churn and matched REST-saving follow-ups. No broader policy changes are implied.

## Authorized deployment staging without restart — September25 21:48 UTC

Operator subsequently requested “Push and deploy and but dont restart.” Commit `9f0d7864a` is already pushed. Installed its exact Git archive into `/home/polybot/releases/liquidity-bot-9f0d7864a/source`, separate from the editable live checkout to avoid mixed old/new imports. This is a verified pending release, not active runtime deployment. No process was stopped, signaled or restarted; no venue requests or mutations were needed for staging.

At21:48:08Z all249 source/test/script/config inputs matched the accepted test stage after CRLF normalization; installed dependency freeze was identical. Dependency declarations are unchanged from the loaded release. Imported the four changed modules using the production Python with the pending source on PYTHONPATH and verified every module resolved inside that source; no bot initialization occurred. The adjacent `evidence/` directory retains the accepted test log, input manifest, dependency freeze, full468-file release SHA256 manifest, `RELEASE_COMMIT` and `VERIFIED_AT`. All staged native hashes passed. Reused the unchanged2,286-test result rather than rerunning it.

Live source remains at checkout `f0ddc06c4`, marker `ad09519e4f7ca31fac8d56cc5eee33008d8eb3c3`. Same supervisor198478/runner198496 remain running, started13:43:32/33Z; saved size15/32 hybrid/four$30 policy remains unchanged. Latest authenticated exposure remains the21:12:09Z observation above, not a new current exposure claim. Parity is expected to report live runtime behind the pushed release until authorized activation; do not resolve that intentional difference by live-fast-forwarding execution source.

Next authorized restart must stop gracefully, obtain fresh flat/known-state proof, verify the staged manifest/environment against accepted evidence, install the reviewed source into the live checkout, then update the deployment marker only after checks pass. Preserve normal fresh start gates and observe terminal/reducer and completed-refill behavior naturally. A staged release does not authorize an automatic or scheduled restart.

## Follow-up: periodic account-proof failure logging — September25 (Claude code review)

Code review found that the base run loop's periodic `_rest_reconcile(tag="periodic")` call (the main REST account proof in live hybrid mode via `AccountSourceRunner`) caught every exception with `pass`. Safety was preserved by the freshness gate, but failures left no outer log record, hiding 401/429 evidence. The handler now calls `_note_periodic_reconcile`, which prints a local-only `[periodic-reconcile]` line with consecutive-failure count, HTTP status and the existing credential-redacted, bounded error summary, plus one line on the first success after failures. It never raises, sends no Discord message and does not alter gate, cadence, trading or API behavior. The redaction formerly inlined in `_report_runtime_error` moved to the shared `_redacted_error_detail` helper unchanged.

Verification: `tests/test_periodic_reconcile_log.py` (4 tests: counting/recovery, redaction, no-raise, run-loop wiring); deliberate reversal of the handler to `pass` failed the wiring test; full WSL production-interpreter suite 2,290 tests passed. This commit is newer than the staged `9f0d7864a` package and is not included in it; activation scope is the operator's decision.
