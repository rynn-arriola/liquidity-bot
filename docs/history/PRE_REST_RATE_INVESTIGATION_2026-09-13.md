# Pre-REST comparison and paused implementation — September 13, 2026

## Authority and state

Operator first requested the narrow rate-limit repair and comparison with the
pre-WebSocket-incident bot, then said: "Can we investigate before we fix".
Further implementation is paused. Existing source/test drafts are preserved
uncommitted in the Windows review checkout; they are not deployed or approved
for live operation. This handoff is documentation only. No restart, source/key
switch, risk/cap change, order mutation or ledger repair occurred.

The one already-running bounded read-only scan completed at05:00:29Z and
verified the trading runner/supervisor absent. Production marker remains
4a166336a; REST, size10/32, $90 HARD and max30 flat pending entries (excluding
position-market orders/reducers/exits) remain unchanged. Latest complete venue
orders/positions proof is zero at04:22–04:23Z; it was not refreshed here.
Latest unresolved/unknown ledger proof is zero at04:16Z, not a fresh claim.

## Confirmed versus unknown

- Stable pre-REST v1.2.0 (`a3defc014`) and starting HEAD `28bd760d9` have the
  identical `src/liquidity_bot/market_selector.py` Git blob
  `59b679cda4ba2cb87bffdd53fa36fbc225dd2838`. Its background2/s selection
  workload was not introduced by REST. This comparison excludes uncommitted
  experimental edits and does not mean the rest of the bot is identical.
- REST adds recurring account proof that private push previously supplied;
  public market streaming is retained. Historical healthy WebSocket windows
  around1.5–2.5 requests/s and the later REST sample3.493/s are different
  periods/markets, not a controlled performance comparison.
- The existing normal-paced selector reproduced429 without the trading engine
  or account polling:18GETs, with the sixth book rejected. Therefore REST
  polling is not necessary for the current failure. This does not identify
  the original trigger, a six-book quota, or all shared-IP traffic.
- Prior inline book retries and full-slate reads were proven local demand
  amplifiers. The subsequent book-backoff/JIT repairs postdate initial onset;
  they cannot explain that earlier onset. Older pre-REST429s also exist.
- The exact venue scope/window/threshold remains unknown. Public operational
  status and a documented headline limit do not identify Cloudflare's matched
  rule. Do not conclude a ban, maintenance, or a venue policy change from this.

[Normal-paced reproduction](STARTUP_PATTERN_REPRODUCTION_2026-09-13.md) and
[initial onset/load reconstruction](PUBLIC_BOOK_BACKOFF_2026-09-12.md) contain
the exact endpoint, safe Ray IDs, historical request categories and limits.

## Already-started slower read-only comparison

The isolated diagnostic imported experimental Windows review source, not the
production runtime. It ran one real size10/32 selector pass using the same
shared governor, with optional book requests spaced4seconds. It had GET-only
hooks, no retries, stop-on-first-error,130-request/600-second bounds and stopped
process checks. It did not run account polling, trading, full startup gates,
order execution or ledger access. No cooldown reset or key/IP rotation.

Started04:53:36.844727Z; ended05:00:29.530040Z,412.69seconds:

| Workload | Result |
|---|---|
|3 event pages +9 incentive reads|12 successful responses|
|100 book reads, about4seconds apart|100 successful responses|
|Total|112GETs,0HTTP429,0mutations|
|Selector result|100 evaluated,81 viable,0 failures|

The previously rejected Marand/Solser book returned200 at04:54:12.518113Z,
Ray `a3a48d77d86922d7-EWR`, cacheHIT. Passing cached and expired-cache reads
proves this bounded scan completed, not current executable prices or sustained
live-load capacity. Different time, rolling traffic and cache conditions mean
the result supports conservative pacing but does not prove causality or the
best interval. Do not repeat a threshold hunt or restart to test a theory.

## Paused draft handoff, not accepted fixes

Uncommitted files: adapters, live_commissioning, market_selector and their
tests, including new `tests/test_optional_book_pacing.py`. Drafts add optional
book pacing, preserve recent429 streak across a single success, yield startup
on429 rather than prune the slate, and re-evaluate selection time after waits.
These have startup/refill latency tradeoffs requiring investigation/review.

An intermediate draft passed1,575 offline tests in210.645seconds and four
deliberate reversals. A later small safety-lane fairness amendment (urgent
books do not postpone the optional clock) has NOT had its final suite/reversal
run. The live diagnostic started before that amendment; it had no safety-book
traffic, so it does not exercise or validate that branch. Do not push/deploy
the code based on these intermediate results. Preserve pending files.

Next: explain old versus new request composition and current rate-limited
resource; obtain Polymarket's matched rule from the existing Ray evidence if
the operator chooses to contact support. Separate confirmed local defects from
unproven venue causes before selecting a repair. No further implementation or
restart until the operator's investigation-first decision is addressed.

Pre-work parity passed laptop/GitHub/WSL at28bd760d9, production marker4a166336a;
desktop offline/pending. Documentation sync must not deploy the paused drafts.
