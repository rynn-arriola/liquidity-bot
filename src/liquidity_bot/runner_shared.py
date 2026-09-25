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
