# Host outage protection — September 15, 2026 ET

## Incident evidence and authority

The operator approved the proposed exchange-expiring entry orders and an
outside-WSL recovery watchdog. They subsequently reported manually closing some
positions. This is not evidence that remaining orders were cancelled, nor
authorization to liquidate the rest or resume ordinary trading.

The last runner log ended at September15 22:30:14Z without a graceful terminal
record. Both supervisor and trader were absent. A subsequent WSL boot timestamp
was19:38:53 ET, showing that the runtime was recreated; it does not establish
the exact time or reason the earlier runtime died. Windows events reviewed did
not establish an operating-system shutdown. Do not call this an API504 cause.

Authenticated23:44:31Z state held20 orders across15 markets and six positions.
Three positions had no resting order; the other three had orders, which alone
does not prove exact reducing coverage. Position-resolution activities after
the log stopped showed three deltas: -$3.90, -$5.90 and +$4.00 (net -$5.80).
That bounded history read did not reach full history EOF and is not daily P&L.

After the operator's manual closes, authenticated23:59:05Z and September16
00:11:11Z both proved zero positions and18 resting orders across14 markets,
zero unresolved intents and zero unknown orders. The stopped ledger still had
32 working orders/two positions and was not repaired by these read-only checks.
The later process check again found no trader/supervisor. Resting orders can
still fill; neither the manual statement nor old ledger establishes flatness.
Final September16 00:21:36Z process/account check again proved no trader or
supervisor,18 resting orders/14 markets, zero positions/unresolved/unknown orders.

## Deployed design

- `config/entry_expiry_policy.json` enables a300-second lifetime on new passive
  entries, shortened to the existing effective pregame flatten boundary. The
  strict configuration permits60–300 seconds or explicit disable; missing or
  malformed configuration refuses startup before credentials/venue access.
- Retail wire format uses `TIME_IN_FORCE_GOOD_TILL_DATE` and UTC `goodTillTime`.
  This was rechecked against the official [Retail orders overview](https://docs.polymarket.us/api-reference/orders/overview)
  and [Retail create-order contract](https://docs.polymarket.us/api-reference/orders/create-order).
  Institutional GOOD_TILL_TIME/FIX cancel-on-disconnect is not substituted.
- Passive reducers remain GTC and hard exits IOC. No proactive cancel/renewal
  timer was added. Existing reconciliation must positively prove terminal
  expiry before capacity is reusable; absence or local time is never proof.
  A fill racing expiry retains its inventory. Expired queued entry requests
  fail locally before network submission, closing their intent as unsent.
- Windows Task Scheduler runs `watch_host.ps1` hidden in the logged-in user's
  session. It checks the same WSL host every15 seconds, without venue calls or
  writable-ledger access. A bounded local health file reports unknown on check
  failure. It does not wake the laptop, switch hosts or kill a live trader.
- Missing/disarmed recovery state cannot launch anything. Normal launch arms
  a generation; manual stop durably disarms before signaling processes, even
  if the previous state file was malformed. Start, stop, supervisor spawn and
  watchdog share one control lock. Busy watchdog checks do not queue.
- Only disappearance of both trader and supervisor can trigger recovery.
  Existing execution-lease, complete account/ownership and configured-capacity
  gates remain. Three persisted attempts maximum, with120-second spacing after
  unexplained recovery-process disappearance. Refusal/nonflat exits latch
  attention rather than relaunching; normal completion remains paused.
- Recovery-only binds identified exposure, never scans for replacements or
  permits new entries. It uses the current verified account source, preserves
  durable position clocks and risk state, and follows the existing bounded
  sequential cancel-confirm/price-capped IOC shutdown. Unlike ordinary manual
  shutdown, it cannot walk away with a parked exit assuming a normal trader
  will follow. An already-flat account finishes without discovery.

## Tradeoffs and limits

Exchange expiry cannot retroactively protect existing GTC orders or close a
filled position. It limits future unattended entry lifetime, not total loss.
At30 constantly resting entries and300seconds, simple arithmetic suggests
up to six replacements/minute (0.10 creates/s) attributable to expiry; this is
not measured total REST overhead or a worst-case bound near shortened kickoff
deadlines. Terminal confirmation also needs existing proof traffic. Queue
priority resets and brief reward gaps are possible. No claim of guaranteed
zero429s, preserved reward yield or automatic five-minute position closure.

Windows/WSL/network/exchange availability and executable depth are still
required for recovery. A hung-but-present trader is not killed automatically.
After a Windows reboot, the logged-in-user task requires login. A failed or
unexecutable recovery stays in attention with unknown/nonflat truth, never
invented cancellation, flatness or permission to trade.

## Verification and dedicated review

First complete isolated native suite passed1,731 tests. Expanded focused tests
passed30, including REST and WebSocket recovery,30-owned-order cancellation,
manual-close reconciliation, managed-position no-entry, partial fill at expiry,
expired admission, malformed/disarmed state, persisted crash bounds and
recovery-only supervisor exits. Three deliberate reversals failed as expected:
removing entry expiry, ignoring manual disarm and allowing recovery parking.
All changes were restored. Shell syntax and PowerShell parsing passed. A real
Windows-to-WSL `-Once` probe against an isolated disarmed test checkout returned
`disarmed`, making no venue request and launching no trader. Final complete
native release suite passed all1,739 tests in171.251seconds after the final
directory-fsync durability change; the preceding1,739-test run also passed.
Final source, configuration, shell/Windows scripts and test diff review passed.
Documentation audit has zero issues. No live failure, order or fill was induced.

Dedicated review covered success/failure, empty/full32-slot scope, partial and
unknown evidence, cancel/fill races, stop/spawn serialization, old-generation
completion, source-switch persistence, crash retry limits, load effects,
shutdown, rollback and secrets. $25 settled-fill windows,30 pending-entry cap
excluding positions, quote size10/32, reward ranking, normal900-second timer,
pregame cutoff and manual REST selection remain unchanged.

## Activation and live acceptance

Commit `63c2c42f9` was pushed, deployed to the laptop WSL checkout and recorded
in `DEPLOYED_COMMIT`. Because the WSL HTTPS fetch hung, deployment used a
locally created Git bundle and a normal fast-forward, preserving ancestry.
The exact production checkout passed all1,739 tests in168.801seconds. Parity
then verified Windows/GitHub/WSL/marker identity and a private recovery-ledger
backup (`486,076,416` bytes; abbreviated hash `9c274839043b`). Authenticated
September16 00:30:49Z proof was fully flat/clear before launch.

The Windows task was installed and verified, then normal manual-REST size10/32
trading started at00:31:06Z as supervisor1009/runner1023. The watchdog reported
`process_present`. Inspected entry intents used
`TIME_IN_FORCE_GOOD_TILL_DATE` with expiry exactly300seconds after creation.
At00:32:36Z the public book socket reported server shutdown; the runner
quarantined, proved cancellations and resumed placing entries without a process
restart. Initial API windows ran3.108–3.736rps. One90.5-second window recorded
two `balance@429` responses; the following window recorded zero429s, so the
platform's low-rate account-read behavior remains a live limitation rather
than an outage-protection regression.

Authenticated00:40:09Z state showed29 flat entry orders and one +2 position
inside the unchanged900-second inventory window. The runner detected the fill,
cancelled its building side and maintained/repriced a passive exact reducer;
the sampled read landed between cancel and replacement, with one intent in
flight. Unknown orders were zero. This proves initial live operation and
recovery, not the uninduced900-second hard-exit or actual host-disappearance
watchdog path.

Use the normal bot stop script to disarm; killing processes directly is
indistinguishable from a crash and can trigger armed recovery.

For rollback, disarm recovery with the reviewed stop path and disable/remove
the Windows task before reverting launcher/runtime files. Account and ledger
proof remain required; preserve ledger, risk and source-control files. Setting
entry expiry `enabled:false` affects only newly submitted orders after a safe
restart; existing GTD orders keep their exchange deadline. Do not alter live
orders or induce an outage/fill merely to test acceptance.
