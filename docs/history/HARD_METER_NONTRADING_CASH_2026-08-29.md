# HARD meter and non-trading cash — 2026-08-29

## Finding

The emergency hard stop and the Discord HARD meter had different inputs after
a completed reward, deposit, withdrawal or other proven non-trading cash
movement. The stop correctly observed marked equity after removing that cash,
as required by safety invariants 25 and 74. The meter instead used raw account
equity change from the ET opening balance.

This could make the account headline truthfully show a profitable day while the
HARD meter incorrectly showed little or no stop consumption. For example, a
`+$200` reward combined with `-$150` adjusted trading equity leaves raw account
equity `+$50`, but consumes `$150` of the hard-stop budget. The stop behavior
was unchanged and remained fail-closed; only its operator display was wrong.

## Repair

The summary now carries two explicit values:

- `today_pnl_usd` remains raw account-wide marked-equity change for the Today
  P&L headline, including rewards and other cash movements;
- `hard_stop_pnl_usd` is the exact current-equity delta already observed by the
  live risk guard after proven non-trading cash is removed.

The HARD bar reads only the second value. Offline and legacy summary callers
that do not own a live guard retain the old raw-equity fallback. No risk limit,
cash classification, venue call, order behavior or stop behavior changed.

## Verification and deployment state

Two regressions cover the renderer and the live runner-to-summary flow. Both
hold raw Today P&L at `+$50` while the adjusted hard-stop signal is one dollar
below its cap. Deliberately restoring the old input made both tests fail with a
zero-dollar HARD reading. After restoring the repair, all 842 tests passed in
the laptop WSL Python 3.12 environment.

Commit `599fb5f` was included in the operator-authorized `859b891` deployment.
The 02:40:36Z start gate proved zero orders, positions, unresolved intents and
unknown orders; all 844 WSL tests passed in 59.440 seconds before the size-13
restart. The adjusted HARD input is now loaded; acceptance of its first summary
remains pending.
