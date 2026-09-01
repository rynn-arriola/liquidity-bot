# Documentation history index

These files preserve evidence and superseded handoffs. They are searchable
history, not current instructions or runtime truth.

| Archive | Purpose |
|---|---|
| [AI engineering through 2026-08-27](AI_ENGINEERING_THROUGH_2026-08-27.md) | Former combined engineering and historical status reference |
| [Status pre-compaction](STATUS_2026-08-27_PRE_COMPACTION.md) | Last long-form operational snapshot before compact status |
| [NEXT through 2026-08-27](NEXT_THROUGH_2026-08-27.md) | Completed and unfinished implementation narratives retained verbatim |
| [Watchlist through 2026-08-27](WATCHLIST_THROUGH_2026-08-27.md) | Prior measurements, decisions and closed hypotheses |
| [Findings through 2026-08-27](FINDINGS_THROUGH_2026-08-27.md) | Full measured evidence log |
| [Bug fixes through 2026-08-27](BUGFIXES_THROUGH_2026-08-27.md) | Full production defect history |
| [Mutation access denial — 2026-08-28](MUTATION_ACCESS_DENIAL_2026-08-28.md) | Evidence, repair and activation state for the 20-slot 403 cascade |
| [Desktop cold-backup setup — 2026-08-28](DESKTOP_COLD_BACKUP_SETUP_2026-08-28.md) | Inactive WSL runtime, recovery validation and manual-handoff boundary |
| [TV Gain dashboard addition — 2026-08-28](TV_GAIN_DASHBOARD_2026-08-28.md) | P&L cards, seven-day Gain table, accounting semantics and zero-API activation boundary |
| [Score-clock slot retirement — 2026-08-28](SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md) | Why score telemetry produced `OBSERVED` slots, containment and 20-slot reversal proof |
| [Size-scaled taker-exit count — 2026-08-30](TAKER_EXIT_SCALING_2026-08-30.md) | Trigger evidence, `4 × size` decision, slot-control audit, edge cases and deployment proof |
| [API backpressure accounting and observability — 2026-08-30](API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md) | Selection deferral semantics, shared cooldown telemetry and review evidence |
| [TV dashboard active scope — 2026-08-30](TV_DASHBOARD_ACTIVE_SCOPE_2026-08-30.md) | Stopped collector-scope defect, live-PID runner handoff, ledger fallback and edge-case proof |
| [Hard-exit sequential cancel repair — 2026-08-31](HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md) | Two-order planner/driver mismatch, exposure-growth incident, per-order confirmation repair and acceptance boundary |
| [Trading-loss price-mix verification — 2026-08-31](TRADING_LOSS_PRICE_MIX_VERIFICATION_2026-08-31.md) | Reproduction of price/league tables, role-classification flaw and 0.15 entry-floor decision boundary |
| [Tiny residual cashout — 2026-08-31](TINY_RESIDUAL_CASHOUT_2026-08-31.md) | Exact fractional-quantity evidence, five-minute decision, cancel-confirm-IOC boundaries and deployment state |
| [Daily execution-fee breadth scaling — 2026-08-31](DAILY_EXECUTION_FEE_BREADTH_SCALING_2026-08-31.md) | First 10/24 pause evidence, operator calibration, scaling rule and restart acceptance contract |
| [Dirty-market decision scheduler — 2026-08-31](DIRTY_MARKET_SCHEDULER_2026-08-31.md) | Latency-first 32-slot preparation, unconditional position safety, exact dirty/timer triggers, telemetry, reversal and activation gates |
| [Exact settlement-wait recovery — 2026-09-01](SETTLEMENT_WAIT_RECOVERY_2026-09-01.md) | Market-specific empty-book evidence, exact-owner isolation policy, risk/capacity boundaries, verification and activation gate |
| [Inactive-market lifecycle recovery — 2026-09-01](INACTIVE_MARKET_LIFECYCLE_RECOVERY_2026-09-01.md) | Exact non-open owner evidence, safe refill, mutation-free positioned wait, unknown-data backoff and activation gate |
| [Combined release review — 2026-09-01](RELEASE_REVIEW_2026-09-01.md) | Cumulative business-output audit, cross-change interactions, exact WSL verification and deployment gates |
| [Direct 32-slot canary preparation — 2026-09-01](SLOT_CAPACITY_CANARY_32_PREPARATION_2026-09-01.md) | Complete API-consumer inventory, empirical 24→32 projection, opportunistic reporting lane, derived risk and rollback gates |

Search archives with `rg`; do not load all of them into an AI context. New
current facts go to the compact root files and are periodically archived by a
reviewed compaction commit.
