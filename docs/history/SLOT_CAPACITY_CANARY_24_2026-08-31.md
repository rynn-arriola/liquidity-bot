# 24-slot capacity canary and dynamic breadth reserve — 2026-08-31

## Operator decision and live boundary

The operator authorized preparing one 24-slot canary and explicitly
said not to restart the bot during implementation. The laptop remains the sole
live owner and its unchanged size-13/22-slot process remains the runtime truth.
The selected next-run quantity is 10, so the prepared target is size 10 / 24.
Twenty stays the bare-launcher default. The source validator now permits only
whole values 20 through 24 and still refuses 25 before credentials or network.

This is a canary authorization, not evidence that 24 is a durable maximum.
Starting it still requires reviewed source, exact deployment, graceful stop,
positive authenticated flat/order-free/ambiguity-free proof and a separate
restart decision.

## What scales with breadth

Worst-case one-quote slot footprint is `quote_size × configured slots`. The
old portfolio cap was always `30 × quote_size`, which preserved the original
ten-unit cushion only at 20 slots and silently reduced it as breadth increased.
The operator chose to make breadth-dependent values scale.

Portfolio exposure is now:

`(configured slots + 10 cushion units) × quote_size`

Account session and hard stops now preserve the original size-10/20-slot $80
allowance per slot:

`8 × quote_size × configured_slots / 20`

At the selected size 10 this produces:

| Slots | Slot footprint | Portfolio cap | Cushion | Weekly reserve | Comfortable capital |
|---:|---:|---:|---:|---:|---:|
| 20 | $200 | $300 | $100 | $560 | $860 |
| 22 | $220 | $320 | $100 | $616 | $936 |
| 24 | $240 | $340 | $100 | $672 | $1,012 |

Capital and withdrawal guidance already consume the runtime slot count and the
configured portfolio cap and seven hard-stop days, so their displayed footprint,
cushion and comfortable balance follow both properties without another venue call.

The following remain quantity- or policy-linked rather than breadth-linked: at
size 10, $40 per-market loss, $90 per-market exposure, $8 early-cross cap, 40
taker-exit count and $7.50 ET-day fee pause; the four-request/second governor,
six-hour horizon and 900-second inventory clock remain fixed. The account stops
are the deliberate exception and become $96 at 24 slots.

That fee sentence records the prepared design before its first activation. The
22:06Z start reconstructed `$9.5300` against `$7.50` and paused safely; the
operator then superseded only the fee policy with the quantity-and-breadth rule
recorded in [daily fee breadth scaling](DAILY_EXECUTION_FEE_BREADTH_SCALING_2026-08-31.md).

Changing quantity and breadth in the same restart means the acceptance proves
the combined size-10/24 operating envelope, not an isolated reward or fill-rate
effect from either variable. API topology still isolates cleanly because 24
uses three shards and three batches. A capacity rollback keeps size 10 and
returns only breadth to 22.

## Stream, API and CPU review

Twenty-four uses the existing three private connections as `8/8/8`; no fourth
connection is added. Bounded contract and incentive refresh also remains three
eight-market batches. Slot 25 would create a fourth private shard and remains
outside the source authority.

The executable offline model had drifted from the canonical documentation: it
counted one balance request at the five-second marked-risk cadence even though
the runner performs balance/positions/balance. The model now counts all three,
adding the missing 0.40 requests/second. Its current projections are:

| Scenario | 22 slots | 24 slots | Meaning |
|---|---:|---:|---|
| Normal | 2.10/s | 2.16/s | Below the 4/s gate and below 75% utilization |
| Busy | 3.61/s | 3.78/s | Below 4/s but without the chosen 25% headroom |
| Whole public feed silent | 8.57/s | 9.23/s | Not viable; recovery must fail closed |

The deterministic replay reports low-priority quiet-book deadline misses even
in the normal scenario at the current width because it releases periodic jobs
at the same instant. This is not hidden or reclassified as a pass. Actual 22-slot
acceptance remains the stronger evidence: the original 30-minute canary had
zero failures/429s, safety deadlines or sustained queue. The twelve latest live
windows checked before this change ran at 1.314-1.511/s, with zero failures,
zero 429s and maximum queue depth one.

CPU projects to 0.677 of one core at 24 from the measured 0.564-core/20-slot
baseline, below the 0.75-core comfort budget. This projection is not acceptance;
the live canary must measure it.

## Operator-display propagation audit

The live Discord summary already builds its slot count, quantity, session/hard
limits, portfolio cap and weekly reserve from the runner's one loaded
`CommissioningConfig`. An end-to-end runner regression now proves that size
10 / 24 renders `0/24`, both $96 stop meters, $240 footprint, $100 cushion,
$672 weekly reserve and $1,012 comfortable capital. The terminal dashboard's
read-only diagnostic configuration also reads both restart-scoped files; its
regression now proves size 10 / 24 produces $96/$40/$96 session, market and
hard caps.

One real display defect was found. The TV backend accepted the new 24-slot
runtime file and returned 24 slot records, but browser JavaScript still treated
anything above 22 as invalid. It would label the scope as 24 while silently
rendering only the fallback count. The browser now accepts only 20-24, bounds a
malformed fallback to that range and lays 24 slots out as two rows of 12. This
is display-only: it has no venue client, does not alter selection or risk and
adds no API traffic.

## Edge-case and rollback contract

- 20 remains the default; explicit 20-24 normalize; 19, 24.5, 25 and non-finite
  values refuse before network access.
- 24 owners must appear exactly once across three `8/8/8` private shards and
  three bounded contract/incentive batches.
- Empty supply remains visible as unused capacity; no market or order is
  fabricated to fill a slot.
- Existing order, position, unresolved-intent and unknown-ownership gates are
  capacity-independent and remain fail-closed.
- The slot-derived portfolio cap is restart-scoped with the exact slot count;
  it cannot change under live orders.
- Any canary failure/429, sustained limiter queue, safety deadline,
  stream-coverage loss or persistent account ambiguity requires ordinary
  graceful stop, positive flat proof and rollback to size 10 / 22 slots.
- The running laptop process was not stopped or restarted for this work.

## Acceptance still required

After an explicitly authorized flat-gated restart at `auto 10 24`, observe at
least 30 continuous minutes spanning startup and a real selection burst. Record
API attempt rates/categories, failures/429s, limiter wait/queue tails, exact
three-shard coverage, CPU use, 24-owner scope, unresolved/unknown state and
normal exit progress. Until that passes, 22 is the accepted live width.

## Local verification

Focused capacity, configuration, capital-guidance, Discord and private-shard
coverage passed 93 tests. The display propagation group passed 59 tests.
Deliberately restoring the browser's 22-slot ceiling failed the new TV
boundary regression exactly. Deliberately restoring the runner's 22-slot ceiling, fixed
`30 × size` cap and one-request risk mark made their regressions fail at the
exact boundaries. Deliberately restoring quantity-only account stops then
failed all four 22/24 breadth assertions. After restoration, the complete
Ubuntu project suite passed 968/968 tests in 99.125 seconds before the display
regressions. After the display repair and its tests, the final complete Ubuntu
suite passed 969/969 in 100.069 seconds. The hourly gain table contains no
quantity, breadth, stop or capital constants and is therefore unaffected.
Documentation audit limits and `git diff --check` are clean.

Source commit `59baf67c654642a0fbb4b4d20cbe6cc4c5ca71d3` was pushed to
GitHub and fast-forwarded into the clean laptop WSL checkout. The deployed
marker and tracked source/tests/scripts/web files matched that commit, and the
exact laptop checkout passed all 969 tests in 69.890 seconds at low CPU
priority. The required parity run also produced a verified private recovery
copy before deployment. Supervisor 937731 and runner 937743 were not signaled,
stopped or restarted; the running command remains size 13 / 22, so the new
runner configuration still activates only after a separately authorized
flat-gated restart.
