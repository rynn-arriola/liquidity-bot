"""Module-level constants shared by the commissioning runner and its mixins.

Moved verbatim from ``live_commissioning`` (values unchanged) so both the
runner and its mixins read the identical objects without a circular import.
"""

from __future__ import annotations

# A recent full-universe selection can seed an urgent post-flat refill, but it
# is never trusted directly. Within this small window the runner bulk-refreshes
# metadata/incentives and re-reads every candidate book; older evidence falls
# back to the normal complete scan. The value is operational latency, not a
# strategy horizon or reward authorization lifetime.
POST_FLAT_SELECTION_CACHE_SECONDS = 120.0

# Bound-market metadata and incentive rows are both bulk-readable. Refresh one
# private-shard-sized scope at a time: this replaces sixteen per-market calls
# with two while ensuring one malformed/failed response can affect at most the
# same eight owners as one private shard. A short look-ahead coalesces timers
# that were previously staggered by serial REST completion.
CONTRACT_REFRESH_SECONDS = 60.0
CONTRACT_REFRESH_MAX_BACKOFF_SECONDS = 900.0
SETTLEMENT_WAIT_BOOK_REFRESH_SECONDS = 60.0
CONTRACT_REFRESH_BATCH_SIZE = 8
CONTRACT_REFRESH_LOOKAHEAD_SECONDS = 5.0

# The runtime loss sensor needs the same positive cash classification as the
# read-only reward report. One cash-only page currently covers the account;
# four is the proven bounded ceiling. The marked-equity snapshot itself is a
# balance/positions/balance bracket. A due cash-activity scan adds its page(s)
# and one final equal-cash proof; all requests still share the 4/s governor.
NONTRADING_CASH_MAX_PAGES = 4

# Refresh before the risk guard's hard freshness edge. A twenty-market sweep
# can itself take several seconds when individual books need REST recovery.
DECISION_EQUITY_REFRESH_MARGIN_SECONDS = 5.0
DECISION_STATE_REFRESH_MARGIN_SECONDS = 5.0

# A PLACE whose response carried no exchange order id must never be resent: the
# venue may have accepted it even though the acknowledgement was lost.  It can,
# however, stop being live risk once several complete account snapshots agree
# that its market has neither an open order nor a position.  Requiring both
# repetition and elapsed time protects against an eventually-consistent first
# snapshot after a venue outage while keeping recovery in-process.
ORPHAN_PLACE_CLEAR_PROOFS = 3
ORPHAN_PLACE_CLEAR_MIN_SECONDS = 10.0
