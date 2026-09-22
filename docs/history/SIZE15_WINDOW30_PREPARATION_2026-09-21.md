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

## Command 1 activation progress

Command1 stopped supervisor22198/runner22212 at00:34:08Z with final
`flat=true`. Authenticated proof at00:35:32Z found zero venue orders,
positions, unresolved intents, unknown orders, working ledger orders and ledger
positions. A stopped online backup passed integrity at954732544bytes with
SHA-256 `f0d77afe6f33781bb5b4bcce0b3a3953903d4a36f03d4ca7f96f081cbcab7a46`.

Release `ab26a2182` is pushed, deployed and marked. The exact WSL checkout
passed all1,987 tests and223 tracked runtime/test/config/script comparisons;
Windows/GitHub/WSL/marker parity passes. It remains stopped until the next
00:00ET boundary because the active18:00–00:00 row was written under the old
$25 fingerprint. At or after the boundary, repeat the authenticated flat gate,
start explicit REST size15/32, and verify the new hard30/cap30 row plus5–7
minutes of live behavior. Any failed gate leaves the bot stopped.

## Operator-authorized immediate compatibility restart

The operator then requested an immediate restart before the fresh boundary.
At00:55:07Z a new authenticated gate again proved zero venue orders,
positions, unresolved intents, unknown orders, working ledger orders and ledger
positions. The exact prior release `580dcc9f4` differs from `ab26a2182` in
executable content only by the $25/$30 risk-policy constants and one comment;
there is no schema, API, strategy or recovery rollback. WSL therefore detached
to and marked that compatible release, preserving the already fingerprinted
$25 active window while using the newly authorized size15/32.

Supervisor104653/runner104667 started at00:55:40Z in explicit REST account mode.
Startup proved public-WebSocket books, `loss=0 hard=25 cap=30`, and normal
maintenance state. Natural fills exercised reducer handling: building-order
remainders were canceled, exact reducers were placed, and earlier positions
were observed flat. At01:01:08Z authenticated proof found20 flat-entry orders
plus one exact BUY15 reducer covering the sole -15 position, zero unresolved or
unknown state, and exact ledger agreement. The latest completed API window was
clean at2.412rps; isolated earlier cash-history/balance429s were handled without
stopping the runner.

The scheduled00:01ET handoff now explicitly stops this temporary run, requires
fresh flat/clear proof, restores reviewed `ab26a2182`, and starts the $30 policy
only in the new durable window. A failed safety gate leaves production stopped.
