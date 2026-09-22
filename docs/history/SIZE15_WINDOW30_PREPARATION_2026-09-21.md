# Size 15 and $30 window preparation — September 21, 2026

## Operator decision

The operator selected 15 contracts per quote and a fixed $30 settled-trade
loss limit for each 00–06/06–12/12–18/18–00 ET window. The pending flat-entry
schedule keeps the same percentages and counts:

| Confirmed settled loss | Budget remaining | Pending flat entries |
|---:|---:|---:|
| $0.00–$5.99 | 100%–above 80% | 30 |
| $6.00–$11.99 | 80%–above 60% | 25 |
| $12.00–$17.99 | 60%–above 40% | 20 |
| $18.00–$23.99 | 40%–above 20% | 15 |
| $24.00–$29.99 | 20%–above 0% | 10 |
| $30.00 or more | 0% | 0 / HARD |

Positions, reducers, IOC exits, recovery and shutdown remain excluded from the
pending-entry count. Only durable matched closing P&L and actual fill fees
affect the window. Open orders, open positions, marks and non-trading cash do
not.

## Automatically scaled size-15 limits

At 32 configured slots, existing formulas produce $12 early-cross loss, $60
stressed-exit entry loss, $135 per-market exposure, $630 portfolio exposure,
$20 fee observation and $840 seven-day window reserve. The maximum pending
flat-entry count remains 30; increasing quantity does not increase breadth.

## Activation boundary

The running process remains size 10 with the loaded $25 policy. Risk policy is
fingerprinted in each durable six-hour row and refuses a different policy in
the same window. Therefore the prepared change must be deployed through the
normal flat/order-free start gate into a fresh six-hour window that has no row
written under the old policy. Do not hot-reload it, rewrite an active window,
or restart it over an old-policy row. Deployment/restart requires a separate
operator instruction.

No process, order, position or venue state was changed by this preparation.

## Verification

- Focused risk, dashboard and source-handoff checks pass.
- Deliberately restoring the old $25 configuration makes the size-15/$30
  contract test fail; restoring $30 makes it pass again.
- The complete WSL production-environment suite passes all 1,987 tests.
- Documentation audit and diff whitespace checks pass.
