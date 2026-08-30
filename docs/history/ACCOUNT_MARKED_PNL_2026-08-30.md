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
