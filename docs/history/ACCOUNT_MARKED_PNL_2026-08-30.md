# Account marked P&L repair — 2026-08-30

## Symptom and proof

Discord reported positive current-day trading while the account was losing.
A cash-stable authenticated read at 05:26:29Z reconciled:

- ET opening cash/equity: `$968.2870`;
- current settled cash: `$937.6602`;
- open position basis/value: `$9.5900 / $9.4900`;
- closed cash-basis trading: `-$21.0368`;
- open unrealized mark: `-$0.1000`; and
- account-wide marked trading: `-$21.1368`.

The Retail balance row simultaneously reported `assetNotional=0`. This
disproved both prior shortcuts: raw cash/equity change (`-$30.6268`) and closed
realized alone (`-$21.0368`) are not the current marked trading result.

## Mechanism and repair

Every live account mark now uses a complete Retail positions page bracketed by
equal settled-cash reads. Equity is current fiat cash plus each nonzero
position's documented `cashValue`. The current ET trading total subtracts the
durable opening equity and positively classified rewards, deposits,
withdrawals and transfers. Current `cost` permits the identity:

`closed trading + (cashValue - cost) = marked trading`.

If cost is missing or the day began with carried inventory, the marked total
remains valid but the closed/open split is unavailable. Missing `cashValue`,
moving cash, pagination failure or ET rollover makes the mark unavailable.

Discord, the hourly reward report, the terminal dashboard, the TV dashboard
and the emergency HARD sensor use this shared account mark. The runner writes
an atomic local, owner-only cache for the TV service; it contains no credential,
expires at ET rollover and a write failure cannot affect trading.

## Safety and capacity cost

The accurate marked-risk read is balance/positions/balance rather than one
balance request. At the five-second decision margin it adds about `0.40 REST
requests/second` over the previous model. A due non-trading scan still adds its
activity page(s) and final equal-cash proof. All calls remain under the shared
4/s governor; the 22-slot restart must verify request rate, limiter queue,
deadlines and venue failures. Updated scenarios are recorded in the canonical
API reference.

## Verification

Regressions cover the exact incident arithmetic, `assetNotional=0` with
nonzero `cashValue`, cash movement during a snapshot, absent basis, absent
position value, reward exclusion, ET cache rollover, corrupt decomposition and
atomic cache publication. The deliberate reversal restored the invalid
balance-row shortcut and failed both incident tests (`944.4802` versus
`950.9802` equity and `-$30.6268` versus `-$21.1368` marked trading). After
restoring the repair, all 884 WSL tests passed in 81.354 seconds. Deployment,
commit and live acceptance evidence are added after the flat-gated restart.

## Deployment and live acceptance

Business commit `49b56b2` was pushed before deployment. Graceful shutdown
parked one size-13 reducing order and truthfully ended `flat=false`; the order
then filled, after which a fresh authenticated stopped read proved zero open
orders and zero nonzero positions. Ledger ownership checks showed zero
unresolved intents, absent orders and foreign working orders before startup.

The exact stopped WSL checkout passed 884 tests in 59.374 seconds. That run
also exposed two test-isolation defects: a dashboard fixture inherited the
machine's persisted 22-slot setting, and runner tests wrote their fake 2026-08-13
P&L cache to the checkout. Both were isolated to temporary files, deliberately
reversed to reproduce their failures, and the resulting 885-test suite passed
in the WSL production virtual environment in 96.464 seconds. No trading logic
changed in those follow-ups.

The size-13/22-slot runner and read-only dashboard then started from the
reviewed source. The dashboard accepted the runner's current ET cache and
displayed `-$24.2568` marked trading = `-$24.2568` closed + `$0` open, with
non-trading cash `$0`. It showed 41 working orders across 21 markets, zero
positions, unresolved intents and unknown orders. The startup/API-selection
burst ran at `2.806/s`; the next two normal windows ran at `1.440-1.482/s`.
All three reported zero failures and rate limits, wait p99 no higher than
`0.223s`, and queue depth no higher than one.
