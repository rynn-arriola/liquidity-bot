# Summary net/pending unavailable — September 23, 2026

Operator asked whether the new market-quality tracker removed overall net and
pending rewards from Discord. Diagnosis only; no summary fix, post or restart
was authorized or performed. Tracker changes are in a separate undeployed
worktree and do not edit summary rendering/reporting/notification modules.

Live source marker remains `6deb707c6`; supervisor23308/runner23326 remain the
same processes. Current source/rendering still includes the highlighted overall
net bar and separate pending reward line. No current Discord message or screenshot
was fetched, so absence of entire labels/layout is not visually verified.

The live log records `reward_earnings_refresh` failures at September23
14:15:10.825099Z,14:30:12.671980Z and14:45:13.158087Z (10:15,10:30,10:45 ET):
`GET /v1/incentives/earnings` timed out. This is observed request failure,
not proof of a venue-wide outage, zero earnings or loss of rewards.

Code path:

- `live_commissioning.py` clears `_earnings_observed_at` after failed refresh.
- `_send_summary` passes that timestamp as `rewards_observed_at`.
- `summary_report.prepare_summary` requires a valid observation timestamp;
  without it, both paid and pending become unknown. Overall net also becomes
  unknown because it requires paid rewards plus settled trading net.
- `summary_image.py` still draws OVERALL NET and Pending. A different
  prepare-summary exception path in `notifications.py` removes the lifetime
  section entirely; no evidence of that exception was captured here.

This explains unavailable reward/net VALUES after the observed timeouts,
not a proven deletion of the visual section. A screenshot is needed if the
operator means the entire section vanished rather than values becoming blank.
Session-scoped `earnings_pending=0` log values are not account-wide pending proof.

Recommended separate reporting fix, not implemented: preserve the last successful
reward snapshot and acquisition timestamp, record refresh failure separately,
show last-known paid/pending with age/stale labels, and mark overall net as
as-of/partial where appropriate. Never present stale values as current or use
reporting cache freshness to weaken trading/ownership/risk gates. If no successful
snapshot exists, keep values unknown. Do not add polling to hide this symptom.
