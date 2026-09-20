# Summary overall-net presentation restoration — September 20, 2026

## Scope

The September 20 compact summary release changed more than the requested image
size and hard-stop meter. It compressed the lifetime result into two columns
and made overall net an ordinary row. That removed the strongest visual cue in
the previously accepted design. The operator asked to keep the old overall
profit presentation and add the hard-stop meter, not redesign that section.

## Correction

The summary image again presents:

- five full-width lifetime rows;
- one large full-width green or red `OVERALL NET` bar;
- pending reward directly below and visibly excluded from overall net; and
- the existing partial-ledger and paid-attribution notes below the money rows.

The 1200 by 1382 image size, independently aligned label/value columns, and
settled-window `HARD` meter remain. The lower market and capital diagnostics
were compacted to preserve the accepted money hierarchy without changing or
dropping their values.

## Safety and verification

This is presentation-only. It changes no API request, report arithmetic,
settled-loss calculation, trading rule, slot, order, position, risk, scheduler,
or runner lifecycle behavior.

- The focused summary suite passed all 32 tests.
- An in-memory reversal that replaced the restored overall-net heading failed
  the new visual-hierarchy regression as expected.
- The complete installed suite passed all 1,985 tests in 300.733 seconds.
- A 1200 by 1382 preview was rendered and visually checked against the
  operator's reference image.
- The final diff and empty/full/boundary review found no value-path or API-load
  change. Positive and negative net values retain distinct bar colors; pending
  remains outside the bar; unknown and overflow guards remain intact.

## Operational state

Commit `46ea761ad` was pushed and fast-forwarded into the laptop WSL checkout
under the report lock. The installed focused suite passed all 32 tests and the
Windows/WSL source and test hashes match. The runner was not stopped or
restarted: PID 1063 continues using the previously loaded release `2f607b679`.
The restored presentation therefore loads on the next authorized restart.

The no-restart deployment did not disturb live inventory handling. At
15:36:16Z the authenticated account had three open positions (`-10`, `-10`,
and `-3`), each with exactly one full-quantity BUY reducer. During observation,
a separate `+10` position partially reduced to `+2.06`; the runner cancelled
the stale 10-contract reducer, acknowledged an exact SELL `2.06` replacement
7.82 seconds after the changed-position observation, and then executed the
normal 15-minute IOC exit. Authenticated state recorded that position flat at
15:35:59Z. No diagnostic placed, cancelled, or modified an order.
