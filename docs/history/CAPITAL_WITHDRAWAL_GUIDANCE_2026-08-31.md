# Capital withdrawal guidance — 2026-08-31

## Operator need

The operator needs a plain answer to two different questions: how much cash is
comfortable for the selected quantity and slot count, and how much is safely
free to withdraw. Reward cash can arrive on later days, so pending reward is
not a substitute for operating cash.

This feature is reporting only. It does not submit a withdrawal, alter a risk
limit, gate an order or add a venue request.

## Calculation

The existing strategy envelope already proves the worst-case flat-market
footprint: complementary bid and offer prices total one quote-size notional per
slot. The configured portfolio cap remains the exposure reserve and may be
larger than that footprint. Seven daily hard-stop budgets are added as weekly
operating runway so delayed rewards are not treated as immediately spendable.

```text
slot footprint      = quote size × configured slots
exposure reserve    = max(portfolio exposure cap, slot footprint)
exposure cushion    = exposure reserve - slot footprint
weekly reserve      = 7 × daily hard-stop budget
comfortable balance = exposure reserve + weekly reserve
free to withdraw    = max(0, min(buying power,
                                  settled cash - comfortable balance))
```

The final amount is rounded down to cents. Working-order reservations cannot
be called free because venue buying power caps the result. Settled cash cannot
fall below the comfortable balance even when buying power is higher. Pending
rewards never enter either input.

At the selected size 13 and 22 slots:

| Component | Amount |
|---|---:|
| Slot footprint (`13 × 22`) | $286 |
| Existing exposure reserve (`13 × 30`) | $390 |
| Exposure cushion | $104 |
| Daily hard-stop budget (`13 × 8`) | $104 |
| Seven-day reserve | $728 |
| Comfortable balance | $1,118 |

## Failure behavior and operational isolation

The summary publishes `UNAVAILABLE`, never a guessed zero or stale dollar
amount, when balance evidence is missing/stale, an intent is unresolved, an
order is unknown or the summary carries an integrity problem. Negative or
non-finite dynamic account values also fail closed. A configuration drift that
makes slot footprint exceed the portfolio cap automatically raises the
exposure reserve to the footprint rather than understating capital.

The calculation consumes only the runner's existing cash and buying-power
snapshot. It performs no I/O and therefore adds zero REST, WebSocket or Discord
requests beyond the already scheduled summary post. The current runner was
left running unchanged during build, review and deployment; the card becomes
active only on the next operator-authorized restart.

## Verification

Focused tests cover exact size-13/22-slot arithmetic, buying-power reservation,
balance below target, cent round-down, stale/unresolved and invalid evidence,
configuration drift and invalid static configuration. Discord rendering tests
prove both the complete card and the `UNAVAILABLE` state. The live runner test
exercises the existing summary path. Final suite and deliberate-reversal
results are recorded in the deployment commit and current operator status.
The deliberate reversal removed the weekly reserve and the exact arithmetic
test failed at `$390 != $1,118`; after restoration, all 947 tests passed in
Ubuntu/WSL in 66.722 seconds. The native-Windows run passed 943 and correctly
refused four retired measurement constructors that require POSIX host locking.
