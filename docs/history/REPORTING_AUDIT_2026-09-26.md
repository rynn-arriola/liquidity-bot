# Reporting audit — September 26, 2026

Operator asked for an audit of the Discord summary, the Discord reward table
and their inputs. Read-only: code trace, the reward table run without
`--post-discord` (02:4x–02:51Z), ledger (`mode=ro`), the 00:4xZ authenticated
earnings download and the TV snapshot. Nothing was posted.

## Verified correct

- **Paid reward by ET day** (reward table) equals authenticated earnings for
  09-19..09-24 exactly ($201.63, $187.35, $116.37, $125.15, $127.37, $67.51).
  Liquidity program only, bot era (≥ 2026-08-14), PAID never mixed with pending.
- **Settled P&L** (closed-fill replay by fill ET day) agrees with the
  independent account-marked P&L where both exist: 09-19 −42.54 vs ~−42.67,
  09-21 −41.79 vs ~−41.83, 09-24 −36.20 vs ~−35.21, 09-20 −53.26 vs ~−56.03
  (open marks across midnight).
- **Discord summary**: headline trading is today's account-marked P&L with
  proven non-trading cash removed; lifetime rewards are bot-era PAID; pending
  never enters net; withdrawal guidance fails closed on stale balance or
  unresolved/unknown ownership. `overall_pnl_usd` and `pnl_label` are passed but
  not displayed on the card.

## Findings

1. **LOW (corrected below; originally reported HIGH) — lifetime P&L on the TV dashboard excludes settlement outcomes.**
   `tv-gain-snapshot.json` (02:07Z): overall $3,578.42 = ledger trading
   −$1,563.78 + PAID $5,142.20, flagged `overall_estimate`. Fill inventory does
   not net to zero in 291 of 3,501 traded markets (6,180.65 contracts,
   $2,957.87 cost; 60 markets/$1,270.26 with game dates ≥ 09-18) while venue
   positions are zero; only 62 have a recorded closed-without-fill
   reconciliation. Their outcomes (settlement, manual close, missed fill) are
   absent from ledger trading, so the lifetime figure can be off by up to
   thousands in either direction. The code comment recorded $67.38 across 29
   markets on 08-19; it is now ~10× larger. Needs an account-level check
   (current equity + withdrawals − deposits − 08-14 starting equity).
2. **MEDIUM — recent paid days look final while still posting.** Payouts post in
   batches over about two cycles (09-22 +15%, 09-23 +38% after first seen); the
   API returns them as PAID with no PENDING rows, so "Pending $0.00" and 09-24
   $67.51 read as final. The table's partial-day protection only triggers on
   PENDING rows.
3. **MEDIUM — the daily net is never shown.** Finalized net: 0 of 7 days;
   Account P&L unavailable 09-22, 09-23 and 09-25 (missing midnight snapshots;
   today also by a 429). The table's key output is therefore absent every day.
4. **LOW — competing/unclear figures.** Settled 7-day −$266.82 versus
   "balanced-pair estimate" −$300.26; resting side-hours include reducers
   (5–9% above the tracker's entry-only hours). Both correct by definition, but
   unlabeled differences invite misreading.
5. **LOW — report lane 429.** The report's own positions read received a
   Cloudflare 429 (02:51:29Z, report lane) while the live bot polled positions
   ~25/min; the live bot's windows before and after showed 0 rate-limited, 0
   cooldown. Relevant to the open 429 investigation.

## Recommendations (operator decisions)

- Verify lifetime P&L against account truth before relying on it; relabel or
  hide the TV lifetime figure until reconciled; longer term, book settlement /
  closed-without-fill outcomes from venue activity.
- Mark the latest ~2 ET days' rewards "still posting".
- Make midnight account snapshots robust, or show a clearly labeled provisional
  net (paid − settled) when account P&L is missing.
- Label the pair estimate and resting-hours definitions or drop the pair estimate.

## CORRECTION to finding 1 (03:1xZ)

The 291-market / $2,957.87 figure was an analysis error: fill direction was
inferred from whether the intent contained "LONG", which inverts the 234
`ORDER_INTENT_SELL_LONG` and 115 `ORDER_INTENT_SELL_SHORT` fills from the early
bot. Using the ledger's own `intent_direction`, **60** markets have fills that
do not net to zero. Read-only `ACTIVITY_TYPE_POSITION_RESOLUTION` history (408
rows since 2026-06-21, 406 with a held position) explains **45** of them as
market settlements (settlement P&L `afterPosition.realized −
beforePosition.realized` ≈ −$50.27 on those markets); **15** remain unexplained,
all last filled 2026-08-15/16 (85 contracts). The TV lifetime figure is
therefore off by roughly $50–$90, not thousands. Finding 1 is downgraded to
LOW. Also measured: `ACTIVITY_TYPE_LIQUIDITY_PROGRAM` and
`ACTIVITY_TYPE_TAKER_FEE_REBATE` activity returned 0 rows, so reward payouts
are not appearing as account cash activity and are not leaking into
account-marked trading P&L.

## Fixes (03:3xZ, operator: "fix all the problems") — pushed, not deployed

Reporting only. Nothing in trading, risk, selection or timing changed.

1. **Settlements booked (finding 1).** New `liquidity_bot/settlement_activity.py`
   reads `ACTIVITY_TYPE_POSITION_RESOLUTION` (report lane, bounded pages,
   fails closed on a page bound or a bad row) and caches them in
   `data/settlement-resolutions.json` (git-ignored). The first run reads the
   whole history (408 rows, 5 pages); later runs read one page with a 2-day
   overlap. `settled_replay` keeps the ledger's fill order exactly and, at each
   resolution, closes whatever inventory the ledger still carries in that
   market at the venue's long-side resolution price using the ledger's own
   cost basis (fees were already charged at fill time). Settled P&L by day and
   the TV lifetime figure now include these. If the venue read fails, days
   the cached history does not cover show unavailable, never zero; with no
   history at all, settled P&L is unavailable and the lifetime figure stays
   ledger-only and says so. Verified on real data: 45 settlements, −$50.65
   (venue's own before/after delta −$50.27); venue position size equals the
   ledger residual in all 45; 09-20 settled −$56.03 now equals account-marked
   −$56.03. With no resolutions the replay is identical to the old one.
   The 15 unexplained 08-15/16 markets remain; the lifetime figure keeps its
   "estimate" flag.
2. **Still-posting rewards (finding 2).** The newest 2 closed ET days with
   paid rows show "Still posting" (Discord: `posting`, with a footer note) and
   never finalize. A real PENDING amount is still shown when present.
3. **Daily net (finding 3).** A day whose recorded boundaries are both within
   60 seconds of midnight is the calendar day (they land 0–9 s after
   midnight). Live dry run: finalized net 09-19 +$158.96, 09-20 +$131.32,
   09-21 +$74.54 (was none). 09-22/09-23 stay unavailable because the 09-23
   opening was recorded 16m47s after midnight (restart), outside the
   15-minute window — correct, not a bug. Making midnight snapshots robust
   across restarts would touch the live runner and is not done here.
4. **Labels (finding 4).** "Old balanced-pair estimate (Nd; diagnostic only,
   misses settlements and unpaired fills)"; its 265 count is described as
   markets it cannot pair (buy fills only), not unbalanced positions. Settled
   P&L is "closed fills + settlements". Resting side-hours note that reducers
   count and the tracker (entry-only) reads lower. TV lifetime detail reads
   "Fills + settlements + paid gain · estimate".
5. **Report 429 (finding 5).** Today's account mark reuses the running bot's
   `data/tv-live-pnl.json` when it is for today, ≤5 minutes old, reconciles
   and the ET opening baseline is valid; otherwise the old bounded reads run.
   This removes two balance reads, the positions read and the cash-activity
   read from each hourly run. Dry run took 21 s.

Tests: `tests/test_reporting_audit_fixes.py` (19) plus updated
`test_reward_table.py` expectations. Deliberate reversals (no tolerance, no
posting days, dashboard/table ignoring posting, zero settlement, wrong short
payout, no staleness limit, no coverage check) each fail. Full WSL suite
2,339 PASS. Two read-only dry runs against production data (no Discord post,
no dashboard write) and a local image render. The dry run created the
settlement cache in the production `data/` directory; it is read by nothing
until these files are deployed.

Deployment: reporting scripts, the TV dashboard server and its page. The
runner itself does not need these changes, but production files change only
with operator approval (Command 1 or an explicit reporting-only sync).
