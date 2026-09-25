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
