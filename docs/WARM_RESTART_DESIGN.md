# Warm restart — design (awaiting operator confirmation)

September 26, 2026. Operator asked to proceed with the warm restart. This
document states the approach, tradeoffs and acceptance before implementation.

## Problem and value

A restart forgets which markets the runner had already selected. Startup then
binds only what a fresh scan ranks viable *now*, and the 2-cent fresh-selection
gate refuses markets that tightened to one cent after selection — the cohort
that carried most realized net (readout: $0.245 vs $0.168 net per side-hour).
Measured cost: 216 market-hours below the pre-restart active count across 21
starts since 09-20 (0.3-37 each), on the order of $5 per restart. Fewer,
morning restarts capture part of this with no code; this design targets the
rest.

## Current retention semantics (unchanged, and what we reproduce)

While running, a bound market stays active until a **mandatory release**
(expired/kicked off, closed, reward ended or authorization lost) or an
**optional release** (economically or reward idle and flat, only when a
replacement is available). The 2-cent gate is not re-applied to bound markets;
an exact one-tick book is quoted at both touches (operator decision, DECISIONS
"New-entry spread"). Every entry still passes the runner's per-market gates
(reward eligibility dwell, entry quality, economics, loss caps, flatten
deadlines) before any order.

## Approach

1. **Remember the scope.** After each committed top-up, atomically write
   `data/warm-restart-scope.json` (ignored, local): run id, write time,
   `DEPLOYED_COMMIT`, and the active slugs with their original selection
   time. No ledger schema change, so rollback and older code are unaffected.
2. **Re-admit at startup, after the existing flat/known-state gates.** If the
   file is present, parses strictly, and is younger than 6 hours (the `day_of`
   window), re-check each remembered slug with fresh reads and keep it only if
   it passes every **mandatory-retention** condition: open/active, game not
   started and outside the flatten deadline, applicable reward period now,
   league not excluded, not retired. Reads are bounded to the existing
   revalidation shape (one bulk market read, one batched incentive read); book
   and stream setup are the normal binding reads.
3. **Bind re-admitted markets first**, capped at `max_markets`, as "already
   selected" (the 2-cent fresh gate is not re-applied, exactly as while
   running). Fill remaining slots from the normal fresh scan. Optional idle
   releases then work as usual.
4. **Binding never places an order.** Quoting decisions go through the
   unchanged per-market runner gates.
5. **Fail safe:** missing, stale, malformed, or unreadable file → today's
   behavior (fresh scan only), with a log line. Re-admission errors never block
   startup.

Log: `[warm-restart] remembered=N readmitted=M dropped={reason: count} age=…`.

## Tradeoffs and risks

- Extends "already selected on a qualifying book" across a restart. This is a
  business interpretation of the 2-cent decision and needs operator
  confirmation.
- A market kept only by retention may be lower-ranked than a fresh candidate;
  normal optional release still replaces idle ones when better supply exists.
- Startup does a small extra revalidation read before binding (bounded as
  above); no change to steady-state API usage.
- Touches startup selection (medium risk): full release verification tier,
  deliberate reversal and a restart-scenario test suite are required.

## Acceptance

Tests: tight retained market re-admitted and quoted only through normal gates;
kicked-off/closed/reward-lost/excluded/retired dropped; stale/missing/corrupt
file falls back to fresh scan; re-admission failure does not block startup;
no orders from binding alone; bounded read counts. Live acceptance after an
approved Command 1: `[warm-restart]` line, and active count after the next
restart versus the pre-stop count (compare with the 216 market-hour baseline).
