# Findings index

Measured facts are indexed here; detailed history through 2026-08-27 is in
[docs/history/FINDINGS_THROUGH_2026-08-27.md](docs/history/FINDINGS_THROUGH_2026-08-27.md).

Before theorizing, search both files:

```powershell
rg -n -i "<market, error, metric, or theory>" FINDINGS.md docs/history/FINDINGS_*.md
```

## Current high-value findings

- **Twenty-slot health is not raw order count.** Use queue `markets=20`, active
  owners, eligibility, resting-time coverage, unresolved ownership and
  positions. A slot can be healthy while temporarily holding no order.
- **Closed owners parked capacity.** The deployed repair releases only after
  fresh exact closed/not-open metadata plus complete flat/order-free ownership
  proof. It awaits natural live acceptance after the current stop.
- **Maintenance recovery works when the venue recovers.** It requires three
  consecutive complete authenticated checks, normal reconciliation and fresh
  streams. The later forced stop did not obtain venue proof, so exposure is
  currently unknown.
- **Open marked loss is provisional.** New entries block immediately, but an
  open-position mark does not become a terminal hard stop until the documented
  close/newer-balance evidence arrives.
- **Resting side-hours are the reward-facing time metric.** Reward-score seconds
  and process-session hours answer different questions.
- **Current-day account-boundary trading is more complete than matched pairs,**
  but per-market P&L remains incomplete when settlements/manual closes are not
  attributable.
- **Queue rank is only bounded.** The venue does not publish exact position;
  automatic cancel/repost remains unauthorized.
- **GitHub `main` is the tracked-file source of truth.** Production source/tests
  are verified against `DEPLOYED_COMMIT`; venue state remains a separate live
  authority.
- **The mandatory AI startup route is now bounded.** On 2026-08-27 the tracked
  entry/status/decision/engineering set fell from 1,030 lines / 8,990 words to
  442 lines / 3,170 words while the former long files were preserved in dated
  archives. `scripts/audit_docs.py` enforces budgets, routes and local links.
- **Polymarket's trader guides are not all Retail wire contracts.** The bot
  uses `api.polymarket.us` and the pinned Retail SDK; Institutional examples
  using participant accounts, JWT/Auth0, FIX, gRPC, Drop Copy or
  `api.prod.polymarketexchange.com` are conceptual unless the matching Retail
  endpoint documents them. The audited source map and implementation gaps are
  in [docs/POLYMARKET_API_REFERENCE.md](docs/POLYMARKET_API_REFERENCE.md).
- **The 2026-07-01 fee schedule matches the bot's signed-fee accounting.**
  Retail execution commission is already a USD `MoneyAmount`: positive taker
  fees are costs and negative maker rebates are credits. Partner fixed-point
  decoding is a different interface and would overstate some Retail fees if
  applied here. No fee-handling defect was found in this documentation review.

## How to add a finding

Add a concise, dated fact here while it is current. Move detailed incident
evidence into a dated archive during periodic compaction. Record what was
measured, the scope, the source, what interpretation was disproved and what
decision remains.
