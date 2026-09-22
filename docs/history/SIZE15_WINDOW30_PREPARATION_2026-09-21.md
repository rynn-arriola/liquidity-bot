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

## September 22 scheduled activation blocked

The scheduled task began at 04:01:30Z (00:01:30 ET). The supervisor was
stopped and the runner received SIGTERM through the normal stop script.
The runner ended at 04:02:12.566135Z with `operator_sigterm` and `flat=false`.
Its shutdown path canceled flat entries but parked two pregame positions on
their existing reducing orders: +15 in `aec-setkameua-marole-sydvik-2026-09-22`
and -1 in `aec-setkameua-nebdmy-shcden-2026-09-22`. This was the existing
passive-exit shutdown behavior, not proof that the account was flat.

Authenticated release proof at 04:02:31Z found two orders, two positions,
two ledger working orders, two ledger positions and zero unresolved/unknown
state. The required flat gate failed. A follow-up at 04:03:14Z found one
venue position (+15, Marole/Sydvik) and one matching OPEN SELL15. The other
position cleared at the venue after shutdown; the stopped ledger still held
both rows and must be reconciled before a future start. These are timestamped
observations, not an assertion that an unattended resting order will fill.

The old process also crossed the midnight boundary before the stop. Read-only
inspection of `execution_settled_risk_windows` at 04:03:09Z found the new
04:00–10:00Z row already persisted with the $25 policy, cap 30, settled P&L
0.0000 and no trip. `WindowRiskMeter.observe` refuses a policy fingerprint
change within an existing window. Therefore the $30 release cannot start in
this window even if exposure subsequently clears. Do not delete the row,
rewrite its policy or reset loss. The original 00:01 schedule was too late;
a future boundary handoff must stop the old runner before the boundary.

No deployment, marker update, restart, manual venue mutation or ledger repair
was performed. WSL HEAD/marker remain `580dcc9f4`; Windows/GitHub have reviewed
`5bbfa5550`. Its executable/test difference from `ab26a2182` is only
`rest_fallback.py` and `test_rest_bulk_polling.py`, the reviewed create-proof
optimization. Earlier 1,999-test/reversal evidence remains valid, but exact
deployed tests and live acceptance were not reached. The previous description
of deployment as "staged" meant a scheduled handoff; source was not installed.

Parity confirms the expected deployment gap and created an integrity/hash
verified private recovery copy at 04:02:55.911267Z, 966,148,096 bytes,
SHA-256 prefix `6709238d3a27`. The offline PC was skipped. The activation
heartbeat was paused after the failed attempt, preventing automatic retries.
Both bot processes are stopped and the normal stop disarmed host recovery.
Remaining work is account/ledger clearance plus a reviewed policy transition,
then deployment/hash/native-suite/start gates and 5–7 minute live acceptance.

## September 22 Command 1 follow-up

The operator renewed Command 1 after the failed scheduled attempt. The normal
stop command confirmed production stopped. At 04:06:57Z authenticated state
was zero venue orders/positions; only the stopped ledger retained two owners.
At 04:07:37Z exact retrieval positively proved their orders CANCELED and
FILLED, with no ambiguity. The existing exclusive-lease stopped prestart
reconciler then imported normal order/fill/position evidence with all venue
mutation entry points disabled. It did not rewrite risk-window state.
Authenticated 04:08:27Z proof confirmed all six counts zero: venue orders,
venue positions, unresolved intents, unknown orders, working ledger orders
and ledger positions. The verified pre-reconciliation private backup remains
available (04:07:37Z, 966,148,096 bytes, hash prefix `6709238d3a27`).

The clean WSL checkout switched back to main and fast-forwarded to `4ebf85913`.
Its executable/test/config/script content exactly matches reviewed `5bbfa5550`;
223 normalized artifact comparisons passed against Git and Windows.
The exact installed full suite was launched; completion follows below.

The account blocker is cleared, but the persisted $25 current-window policy
still blocks $30 startup. Command 1 does not authorize deleting that window,
resetting loss or silently extending the temporary $25 policy. No start or
venue mutation was issued. The previously scheduled heartbeat remains paused.

### Explicit current-window $30 authorization and restart

The operator then explicitly instructed applying $30 immediately, replacing
the existing current-window setting. This supersedes the earlier requirement
to wait for an unused window, but does not authorize forgiving any trading
loss, clearing a HARD hold or changing quote/exit rules.

The exact installed suite passed all 1,999 tests in 192.978 seconds; all 223
artifact hashes matched reviewed `5bbfa5550`. A stopped SQLite online backup
at 04:14:10Z passed integrity `ok`: 966,152,192 bytes, SHA-256
`31723caf8eb398efbc9db486ad987458bd0b11073b56fb90dfe34410727ffc68`.

The one-off migration held the exclusive bot lease and checked that both
executors were stopped, authenticated orders/positions were zero, the ledger
was flat/clear, and the exact 04:00–10:00Z window had the old $25 policy and
no HARD trip/release. It conditionally changed only that row's `policy` to
the configured $30 fingerprint. Row count, all other columns and all other
windows were compared unchanged. A transactional dry run rolled back and
proved the original row identical; isolated normal risk replay accepted $30
and preserved the actual matched-fill P&L of -$1.45000000, selecting cap 30.
The stored 0.0000 pre-shutdown mark was not a P&L reset: startup recomputes
the total from the durable fills, including the stopped reconciliation.

The migration committed at 04:14:39Z under explicit authorization. A fresh
04:14:44Z authenticated gate again found all six account/ledger counts zero.
The marker was set to `5bbfa55505a0ca55e0a9aea18b4cc0eeb2d1c543`, and
explicit REST size15/32 launched supervisor121616 and runner121634.
Natural five-to-seven-minute acceptance follows below; no synthetic fill or
failure is induced. The obsolete midnight activation heartbeat stays paused.
