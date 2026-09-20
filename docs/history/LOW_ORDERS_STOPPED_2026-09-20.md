# Low-order check — September20

Read-only operator question; no stop/start, deployment or trading change made.

At14:33:41.505365Z authenticated report-lane reads proved zero venue orders,
zero positions, zero unknown orders/unresolved intents; ledger agreed. No live
runner/supervisor appeared in the process check. Account-source status forPID559
was REST/stopped at14:19:06Z. Loaded marker remains3fc44a674.

## Sequence visible in the current logs

- 14:10:54Z selector found19 viable markets from438 rewarded;23 public-stream
  book candidates, zero REST selection fallbacks. No blanket supply shortage.
- 14:14:52–14:18:18Z startup bound-book reads repeatedly received HTTP429 and
  Cloudflare1015 from the public gateway. Stack traces pass through preflight,
  bound-book refresh and REST `fetch_book`; this is distinct from public-WS
  selection scans. Threshold, trigger and whether access has recovered remain
  unverified. No extra public-book probe was sent.
- Account health succeeded three times14:15:35–14:17:37Z; orders fell19 to9,
  with one position. These successful account reads did not prove book recovery.
- Last pre-close window risk14:18:49Z: matched loss$0.6447/$25, cap30,
  held=false. Loss-tier reduction was not blocking entries then.
- Recovery-only process started14:18:38Z, canceled remaining orders, closed the
  +10 CS2 position with an IOC attempt, observed flat14:19:02Z and finished
  flat14:19:06Z with `startup_recovery_only`. Host recovery state is `recovered`.
  The code deliberately stops after RECOVERY_ONLY startup; do not label this
  terminal record a crash or attribute it solely to429. The cause of the
  preceding process/host interruption was not established by this bounded check.

Current low/zero orders therefore reflect a stopped recovery-complete runner,
preceded by startup REST-book throttling, not a current loss cap or proven lack
of reward markets. Recommend reviewing the interrupted-start/bound-book path
before an authorized restart; no fix/restart is authorized by this question.

Official changelog checked: September19 streaming guidance is Institutional,
not a replacement Retail endpoint. Status website fetch failed, so no current
venue-wide health assertion. Pre-documentation parity matched clean Windows/
GitHub54ac71a2; WSL0be8b85a intentionally awaits next-restart display changes.
