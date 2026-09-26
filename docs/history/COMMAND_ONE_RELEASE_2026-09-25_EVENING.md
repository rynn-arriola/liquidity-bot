# Command 1 release — September 25 evening (`63d2917b8`)

Operator issued Command 1 after the Claude code review (items 1-5) and final
pre-deployment/end-to-end reviews ([review record](CODE_REVIEW_2026-09-25.md)).
Released commit `63d2917b88b36a11ddd0ed458f27fc26f56f142f` contains the
September 25 terminal/refill fixes (`9f0d7864a`, previously staged), periodic
account-proof failure logging, the no-behavior-change split of
`live_commissioning.py` into twelve `runner_*` mixins, the shared status-based
not-found helper, the NaN-safe unused sizing helper and a Python 3.14 test
fixture. No configuration, risk, size, slot or strategy change.

## Stop

Supervisor 198478 stopped and runner 198496 received SIGTERM at 23:48:41Z;
`stop_commissioning.sh` returned normally at 23:49:23Z (no `--force`). Final
record: `stop_reason=operator_sigterm`, `flat=true`, stopped 23:49:21Z. During
shutdown `aec-utr-adatak-urasot-2026-09-26` logged four transient
`RuntimeInvariantViolation` shutdown failures before its cancel was confirmed;
the same exception class ("working order has no owned place intent") appears in
earlier shutdowns and remains the open shutdown-ownership item. One +15
position (`aec-itfme-domrec-lukpok`) was observed flat at 23:49:16Z. The
operator reported manually closing some trades during this window. Two Discord
shutdown notifications were not delivered.

## Gates and deployment

Gate 1 (authenticated, 23:50:26Z) and gate 2 (23:54:24Z, immediately before
start): zero venue orders/positions, zero unresolved intents, unknown orders,
ledger working orders and ledger positions; no runner or supervisor present.
Stopped-runtime parity ledger copy verified at 23:50:55Z (1,292,050,432 bytes,
SHA prefix 13c2abafe161); desktop peer offline/pending. Clean WSL `main`
fast-forwarded from `f0ddc06c4` to `63d2917b8`; all 264 tracked
src/tests/scripts/config inputs match the reviewed Windows copy after line-
ending normalization and all 12 `runner_*.py` modules are present; CLI import
smoke passed; full suite in the live checkout: 2,296 tests pass.
`DEPLOYED_COMMIT` set to `63d2917b8` only after those checks.

## Start and acceptance

`start_commissioning.sh auto 15 32 hybrid` started supervisor 397960 and runner
397974 (same persisted settings). First ~3 minutes: zero tracebacks,
`[runtime-recovery]`, critical, invariant or `[periodic-reconcile]` lines;
window 18:00-24:00 ET loss $0/hard $30/cap 30, held=false; API windows 1.987
and 1.582 rps with zero failures/429s. Authenticated 23:57:57Z: 14 venue
orders on 9 markets (ledger 14), one -15 position on
`aec-setkameua-pruser-gazole-2026-09-26` covered by an exact BUY 15 reducer,
zero unresolved/unknown.

Lower breadth after restart is expected, not a regression: before the stop the
runner held 32 active markets mostly retained from earlier in the day
(`kept=31`), while evening fresh scans ranked only 10-18 viable markets
(22:22-23:31Z) and 13-14 after restart. A restart rebuilds from a fresh scan;
selector code is unchanged. Evening restarts therefore reduce breadth until
later scans find more markets.

## Breadth trace after restart (operator asked why fewer than ~30 orders)

Flow-by-flow evidence, 23:54Z-00:02Z. Scan: every evening scan ranks few
books (tonight 15-17 of ~713 rewarded, viable 13-14; same hour last night
14-17 ranked, 10-14 viable; this morning 26-28 ranked, 18-20 viable). Binding:
23:55:50Z top-up bound 14. Decisions: at 00:00:01Z (8:00 PM ET) ten markets'
reward periods became `period_is_live`; pregame-only quoting stops there and
entries are already barred inside the 300 s pre-kickoff window, which is why
23:57Z had 14 orders on 9 of 14 bound markets. Replacement: the lifecycle
top-up requested 00:00:46Z committed 00:01:51Z (released 5, kept 9, added 7,
active 16); authenticated 00:02:09Z: 15 orders on 10 markets, zero positions
and unknown orders. Correction to the note above: breadth is not capped by one
evening scan. Successive scans add newly viable markets while bound ones are
kept, so after last night's 00:09Z restart active markets rebuilt
13→15→18→19→22→26→32 by 01:32Z (~83 min). Tonight matches that trajectory.
`[api-backpressure]` lines were local 1 s deferrals with zero requests
(`LOCAL_HYBRID_OBSERVATION_PENDING`, `LOCAL_CATALOG_ACCOUNT_CHANGED`).
Finding for follow-up: `LOCAL_CATALOG_ACCOUNT_CHANGED` top-up deferrals were
0 on September 23-24 but 474 on September 25 (old runner) and 13 in the first
8 minutes of this run; they lengthen refill latency (open refill-latency item)
and are not introduced by the refactor (selector/top-up logic AST-unchanged).

## Why rewarded markets unlock slowly (measured 00:14:38Z September 26)

Bounded read-only pre-book diagnostic (catalog + incentive reads through the
shared limiter at report priority; no books, no orders) using the live policy
(size 15, daily-event lookahead 24 h). Of 709 rewarded executable markets:
606 had no reward period active now — games 6-24 h away whose programs were
only `day_of` (starts six hours before kickoff) and `live` (in-play, never
traded); by kickoff distance 214 at 6-12 h, 284+86+8+3 at 12-24 h. No
`daily_event` program was attached to any of them tonight, so the 24 h
lookahead could not bind them early. 90 were inside the `day_of` window but
their catalog spread was already below the 2-cent minimum entry spread. 13
passed to the book stage (matching the live scans' 13-17 ranked). Another 160
moneylines were beyond the 24 h window and 53 had started. Breadth therefore
rises as each game crosses T-6 h, picked up by the 15-minute scheduled scans
and kickoff-triggered top-ups; this is the venue reward window, not bot speed.

## Why `daily_event` rewards are not being used (00:17Z September 26)

Log history: `daily_event` appears only in ranked candidates from
2026-09-22T13:51Z to 2026-09-24T00:06Z, on four markets (ITF men's tennis and
WTT table tennis) under one program `politics_mid_20260921` (pool $100,
target 5,000 shares, discount 0.2; example remaining reward about $0.03 for
the bot's size). None in 169 scans on September 24 or 126 on September 25. No
code touching incentive parsing/period selection changed since September 22.
The incentive read sends `program_type=liquidityProgram` with exact symbols
and parses every liquidity period regardless of period kind. A bounded raw
read of 40 sampled executable markets **without** a program-type filter
returned only `liquidityProgram` periods `live` (33), `day_of` (28) and
`early` (1): the venue is not attaching `daily_event` (or another program
type) to the sports moneylines the bot trades. Daily per-event pools described
by the docs target events without a fixed start (for example politics); the
bot deliberately trades only pregame sports moneylines with a kickoff risk
clock. Using those pools would be a new strategy, not a selection fix.

## Fate of the markets quoted at the stop (read-only, 00:22Z September 26)

The shutdown log names the 21 markets holding orders at the 23:48Z stop (31
markets had actions in the preceding 30 minutes; the rest were bound but idle).
Checked against the current catalog and incentives with the live entry
policy: 13 were ITF/UTR/Setka games that started at 00:00Z (8 PM ET) and
could not be re-bound by any restart; 7 were still pregame inside their
`day_of` window but their catalog spread is now below the 2-cent minimum entry
spread, so a fresh scan refuses them even though the running bot had kept
quoting them after binding; 1 passed and was re-bound. The minimum entry
spread is a selection (entry) rule and is not re-applied to already-bound
markets, so a restart converts retained tight-spread markets into refused new
entries. Recovering them on restart ("warm restart" under retention rules) or
changing the spread rule are operator decisions.
