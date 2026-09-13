# REST execution-history load optimization — September 13, 2026

## Authority and operating state

The operator asked to optimize the elevated REST request count after the bot
was stopped for the shared public-stream recovery repair. The operator later
authorized deployment and restart after review. Preserve manual REST account
mode, size 10 / 32, the $90 HARD limit, and the maximum 30 flat pending entries
excluding every position order, reducer and exit.

The stopped authenticated proof at 21:07:34Z showed zero venue orders and
positions and zero unresolved/unknown ledger state. Subsequent release evidence
is recorded below.

## Bigger-picture review and rejected alternatives

The measured comparison found 122 trade-history requests in 289.9 seconds,
approximately two pages per normal proof. Portfolio history is sorted newest
first and durable execution IDs provide the existing overlap anchor. A stopped,
read-only probe asked for 200 activities but the venue still returned 20 rows,
`eof=false` and a continuation cursor, so requesting a larger page does not
reduce calls.

The first implementation attempt reused one complete open-order list instead
of retaining the two-list bracket. Five existing race/freshness regressions
failed, including disappearance, partial fill between reads and slow-proof
cases. That attempt was fully reverted. The two matching complete lists remain
mandatory for optimized flat-order proof; missing or changed rows still need
exact terminal evidence. The balance/position/balance equity bracket also
remains because it protects deposit/withdrawal classification and the marked
$90 HARD calculation. Slowing active polling would directly worsen position
and exit latency and was rejected.

The chosen optimization affects only pagination of mandatory trade history.
Startup, source bind/switch, recovery, shutdown, disabled optimization,
positioned accounts, unresolved/absent orders, an empty durable history, and
each 30-second audit retain the deeper two-page overlap. Between audits, a
flat, current and unambiguous proof may stop after the first nonempty page when
every execution ID on that newest page is already durable. Any new execution,
an empty/no-overlap page, malformed content, repeated cursor or missing EOF
continues or fails closed exactly as before. A burst that fills the first page
with new executions therefore continues until known overlap or proven EOF.

The account snapshot reader and compatibility REST runner share the same rule;
the WebSocket account path keeps the default two-page behavior. The 30-second
deep audit bounds discovery of an unusually late-published older fill while
current position and order changes still force immediate deep proof. No new
endpoint, cache authority, background writer, automatic source switch, API
ceiling or retry was added.

## Expected load effect

If the measured 289.9-second sample's 61 apparent two-page proofs had all been
flat, stable and evenly spread, the change would replace roughly 51 of 122
history page calls with local durable-overlap proof: about 71 history calls
instead of 122. That is a best-case estimated saving of about 0.176
request/second and would move the measured total from 3.129/s toward 2.953/s if
all other traffic were identical. Actual savings are smaller whenever a
position, unresolved/absent order, recovery or 30-second audit keeps the second
page. This is not a controlled live measurement and cannot guarantee no HTTP
429. Position, mutation, selection and exact-order traffic also vary.

The official Retail rate-limit page currently states 20 authenticated requests
per second per API key, but prior endpoint/account 429s occurred below that
average. The internal shared 4/s governor is unchanged. Live API rate, queue
depth, 429s and natural fill-to-reducer/exit timing therefore still need a
separately authorized deployment and restart acceptance.

## Verification and edge-case audit

- Ninety-three focused tests pass across the snapshot collector, compatibility
  REST runner and permanent account-source runner.
- Two deliberate in-memory reversals failed by assertion: forcing every stable
  flat proof back to two pages was caught by the load regression, and forcing
  every proof to one page was caught by the position/uncertainty/deep-audit
  safeguards. No import/setup error was accepted as a reversal.
- The final complete native WSL suite passed all 1,647 tests in 227.633 seconds.
- New coverage proves first-page durable overlap, full first-page new bursts,
  default page-two late-fill discovery, invalid policy values, startup/no-anchor,
  open positions, unresolved and absent orders, the 29.999/30-second boundary,
  backward clocks through the policy condition, and failure without falsely
  advancing the last successful deep audit.
- Existing coverage retains malformed/truncated pages, repeated cursors,
  BUSTED trades without execution legs, immutable fill conflicts, cancellation,
  source generation, partial fills, full 32-owner behavior, hard exits,
  shutdown and REST/WebSocket source separation.
- The change adds no mutation, order-price/size/ranking change, schema migration
  or credential. Rollback is additive and simple, but restores the measured
  extra history page per stable-flat proof.

## Deployment and seven-minute acceptance

- Commits `70a273743` and `87ad2df28` were pushed before deployment. Windows,
  GitHub, WSL `HEAD` and `DEPLOYED_COMMIT` matched `87ad2df28`; all 193 reviewed
  source/test/script/web/config hashes matched.
- The exact runtime checkout passed all 1,647 tests in 156.445 seconds.
- Authenticated gates at 21:54:14Z and 21:58:30Z each proved zero venue orders,
  zero positions and zero unresolved/unknown ledger state before startup.
- The standard manual-REST size-10/32 launcher started supervisor `346475` and
  runner `346508` at 21:58Z. The first selection used public-stream books for
  all 45 candidates and no REST-book fallback.
- Five complete API windows reported 3.336, 3.233, 3.214, 2.952 and 2.581
  requests/second, followed by 1.708/s in the sixth window. Every window had
  zero HTTP 429 and zero cooldown. The latest three had zero failures. Early
  `order_retrieve` confirmation work dominated the startup windows and fell
  from 112 to zero by the 1.708/s window.
- The optimized stable-flat path is directly visible in the last window:
  execution history used 10 requests in 60.3 seconds. Active/uncertain windows
  correctly remained deeper, reaching 29–34 history requests while positions
  were open.
- Two natural positions were detected and managed. One reducer was acknowledged
  1.36 seconds after observation. A later position first encountered
  `execution_state_unknown`, recovered and received its reducer after 26.09
  seconds; it was continuously managed afterward. This is safe recovery evidence
  but not WebSocket-equivalent latency acceptance.
- At 22:06:01Z both positions had naturally returned flat. Authenticated venue
  and ledger proof agreed on exactly 30 flat entry orders across 28 markets,
  zero position-market orders, zero positions and zero unresolved/unknown state.
  This proves the 30-entry ceiling excludes reducers/position orders.

## Remaining acceptance

Continue sustained natural observation for reconnects, hard exits and endpoint
rate behavior. Do not claim that the optimization guarantees no future 429 or
that every reducer matches former WebSocket latency; the 26.09-second recovered
episode remains explicit evidence. No further tuning is justified from the
short acceptance window alone.
