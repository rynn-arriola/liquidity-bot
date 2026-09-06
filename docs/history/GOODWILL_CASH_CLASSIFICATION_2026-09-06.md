# Structured goodwill cash classification — 2026-09-06

## Incident

At 21:18:51Z Polymarket completed a `$100` `ACTIVITY_TYPE_TRANSFER`.
Its structured description identified version 1, `kind=remediation`,
`action=goodwill`, `scope=isv`, and the September 5 outage. The existing
classifier recognized rewards, deposits, credits, bonuses and referrals by
description, but not this new structured goodwill shape. Cash adjustment
therefore became unavailable, new-risk authority failed closed, and the bot
cancelled all flat-market entry orders with `account_new_risk_paused`.

## Repair and safety boundary

The shared cash classifier now accepts only the exact structured version-1
ISV remediation/goodwill shape as positive non-trading cash. It is subtracted
from account movement, so the credit cannot conceal trading loss or restore
the HARD budget. Unstructured goodwill text, a different action, kind, scope,
version, malformed JSON and every other unknown transfer still fail closed.

## Verification

Two regressions prove the observed `$100` credit preserves a `$100` adjusted
trading-equity reading and new-risk authority, while a nearby structured
adjustment remains unavailable. The existing unknown-transfer regression also
passes. Disabling the new classification reproduced the exact failure. The
complete POSIX review suite passes 1,126 tests. Deployment and live acceptance
are recorded after completion.
