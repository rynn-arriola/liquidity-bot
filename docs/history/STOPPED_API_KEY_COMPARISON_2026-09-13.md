# Stopped API and saved-key comparison — September 13, 2026

## Scope and result

Operator requested other endpoints and saved keys be checked. Bot stayed stopped;
no order, ledger, production credential, risk, configuration or source mutation.
Original trading credentials and the distinct saved diagnostic credentials were
loaded without printing values; diagnostic secret passed in memory/stdin only.
Ignored helpers: `.local-tools/diagnostic_key_bridge.py` (Windows credential
bridge) and `.local-tools/stopped_api_key_comparison.py` (WSL read-only test).

Test ran **04:22:27.750744–04:25:20.104957 UTC / 00:22–00:25 ET**.
Ten HTTP GETs used the shared report lane at0.25/s, retry_attempts=1,
request-method/process guards, bounded pagination and whole-test abort on first429.
All10 returned200, no429. No private raw payloads/secrets were retained here.

| Endpoint | Original key | Saved diagnostic key |
|---|---|---|
| `/v1/account/balances` | 200, object | 200, object |
| `/v1/orders/open` | 200, complete, 0 orders | 200, complete, 0 orders |
| `/v1/portfolio/positions` | 200, complete, 0 nonzero positions | 200, complete, 0 nonzero positions |
| `/v1/portfolio/activities`, trades, limit20 | 200, 20 rows, EOF false | 200, 20 rows, EOF false |
| `/v1/ws/private`,45s each | Connected, initial/final Pong;0 frames | Connected, initial/final Pong;0 frames |

Original orders/positions proof04:22:31.956726–04:22:35.989967Z;
diagnostic proof04:22:55.963697–04:23:00.068691Z.
Activity checks cover one page, not the entire account history.
Balance check confirms response availability/shape, not audited accounting.
Private subscriptions used installed-SDK-shaped camelCase JSON: ACCOUNT_BALANCE,
ORDER and POSITION; position subscription scoped to the single test market below.
Each45-second observation followed subscriptions and an initial awaited Pong.
Both had zero decode failures because no application frames arrived.

Public test market: `aec-itfme-huawei-junfu-2026-09-13`.
Metadata `/v1/market/slug/...` and `/v1/markets/.../book` on public gateway
returned200/open at04:22:44Z and04:22:47Z. The book request Ray was
`a3a45f753b271906-EWR`. These REST reads are unauthenticated, not key-specific.
The market WebSocket `/v1/ws/markets` then returned7 JSON `marketData` frames
over45 seconds with handshake and initial/final Pong, zero decode failures.
No order/cancel/exit operation or natural fill/update was induced.

## Interpretation and limits

- Both credentials authenticate REST, making an original-key-only failure less
  likely. This does not rule out all account-specific restrictions or prove
  trading permissions; mutation endpoints were deliberately not tested.
- Public streaming works in this single-market sample. Private transport works,
  but no order/balance/position application message arrived on either key.
  Because the account was flat/inactive, this does not establish whether a new
  natural trade would produce an update or guarantee initial snapshots.
- A public-book200 at low rate is not normal startup or steady-state acceptance.
  The previous normal restart hit429 despite a successful slow test. Do not
  restart or rotate credentials based on these results.
- A different key cannot resolve the public-book limit simply by being substituted
  into an unauthenticated request. Exact Cloudflare scope/threshold remains unknown.
- Current official changelog was re-opened (latest visible Sept10v0.0.88);
  live status reported All Systems Operational. Neither establishes private-feed
  health for this account or contradicts measured endpoint-specific symptoms.

## Handoff

Runner/supervisor absent before and through the final stopped-process guard.
Latest venue exposure proof is above; unresolved/unknown ledger counts were not
re-read (last proven zero04:16Z). Source marker remains `4a166336a`; $90 HARD,
size10/32 and REST maximum30 flat pending entries, excluding position orders,
remain unchanged. Follow-up is representative request-demand diagnosis and a
support escalation using this comparison, not a new key/restart loop.

Pre-documentation parity passed on the laptop; offline desktop peer pending.
WSL/private ledger recovery copy verified04:27:10Z,388657152bytes,
SHA256 prefix63c773d6be11. Helpers are ignored; only this handoff is published.
