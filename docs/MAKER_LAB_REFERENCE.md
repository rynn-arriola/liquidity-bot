# maker-lab reference

Reference commit observed during this implementation review:
`58eae25b8a689f148d093d7a113fc6d73b104c0b`

The old project is evidence. It is not imported at runtime and is not copied
wholesale. This prevents its configuration history and accounting state from
silently becoming the new bot's behavior.

## Concepts worth retaining

- Full-book parsing and observed tick sizes: `../maker-lab/book.py`
- Desired-state reconciliation and uncertain-order quarantine: `../maker-lab/live_runner.py`
- Durable, idempotent fill booking: `../maker-lab/live_runner.py` and `../maker-lab/simulator.py`
- Exchange-truth startup reconciliation: `../maker-lab/run_live.py`
- Adversarial execution tests: `../maker-lab/test_live_orders.py` and `../maker-lab/test_live_safety.py`
- Incentive and earnings endpoint discoveries: `../maker-lab/universe.py`, `../maker-lab/reward_markets.py`, and `../maker-lab/reward_yield.py`
- One-glance Discord layout, explicit mode labeling, separate error channel,
  nonblocking delivery, and pending-reward separation. The new payload is
  rebuilt around liquidity score, inventory age, exits, readiness, and exact
  exchange state rather than copying maker-lab's summary calculations.

## Mistakes that must not cross the boundary

- Selecting soonest-starting markets instead of reward-positive markets
- Treating every `active` lifecycle as applicable now
- Treating `targetSize` as the amount our bot should post
- Allowing a force-exit guard to disable the independent pair-completion clock
- Using realized P&L as the only loss protection
- Double-pacing synchronous REST calls inside an async event loop
- Allowing unmatched inventory to reach settlement by default
- Attributing shared-account activity to this strategy without order ownership
- Using raw contract-hours as if they were eligible reward score
- Assuming a published reward pool is our payout

## Evidence that shapes the new defaults

- The August 12 loss was driven primarily by unmatched settlement inventory.
- Sixty-second fill markouts were positive; entry price was not the main failure.
- Setka reward earnings were real but small relative to a 10-contract settlement loss.
- Some 5,000-contract target sides qualified by only a few contracts, so eligibility must be continuous rather than a startup filter.
