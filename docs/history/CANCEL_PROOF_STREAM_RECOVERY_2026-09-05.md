# Cancel-proof stream recovery — 2026-09-05

## Incident

During a Retail API outage, cancellation requests returned both 5xx ambiguity
and later acknowledgements, while authoritative order reads continued to show
the same orders `OPEN`. Market-stream recovery correctly kept entry disabled
but treated its wait for terminal cancel proof as another failed reconnect.
After 12 attempts it exited non-flat. Authenticated stopped-state evidence found
40 bot-owned open orders across 22 markets, zero positions, and no unowned or
`UNKNOWN_ABSENT` order; the ledger retained 44 uncertain cancel intents.

## Repair

`_quarantine_working_orders` now distinguishes pending terminal CANCEL proof
from other reconciliation failures. Market-stream recovery handles that state
like no-ID PLACE evidence: it resets the reconnect budget, remains no-entry,
continues the existing positive-read/retry protocol, and stays interruptible by
operator and risk stops. A later authoritative `OPEN` read may reissue either
an `UNCERTAIN` or `ACKNOWLEDGED` cancel. Unknown or absent state still cannot.

## Verification and recovery gate

Eight focused tests cover acknowledged retry, unknown refusal, quarantine
classification, indefinite wait, ordinary bounded reconnect, no-ID recovery
and risk-stop behavior. Removing the dedicated wait handler made its regression
fail; restoration passed. The final review tree passed all 1,124 POSIX tests.
A stopped-safe SQLite backup was created before any recovery mutation. Push,
deployment, startup recovery, authenticated flat proof and live acceptance are
recorded after completion.
