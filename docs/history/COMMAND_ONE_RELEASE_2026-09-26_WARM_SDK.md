# Command 1 release — September 26 (`9f000d404`)

Operator reviewed the undeployed set and issued "Command 1 now" (02:3xZ). The
optional seeding of the warm-restart scope from the dashboard active-scope file
was not answered, so it was not performed; this first restart used a fresh scan.

Deployed: warm restart (`7ddaa273b`), league selection exclusion mechanism with
an EMPTY list (`5e1fa327c`, `e14e9bbab`) plus the tracker `plain()` set fix,
polymarket-us 1.0.2 with all SDK clients built by `sdk_client.new_async_client`
(`max_retries=0`) (`9f000d404`). Review: 951 baseline definitions, none removed;
only the expected 9 changed; tests none removed, 24 added, 1 retargeted with no
assertion loss; 2,320 pass on SDK 0.1.2 and 1.0.2.

Stop: `stop_commissioning.sh` 02:31:19Z → 02:31:43Z (no force); final record
`flat=true`, `operator_sigterm`, stopped 02:31:40Z; three transient shutdown
retries (known shutdown-ownership item). Gate 1 02:31:56Z: zero venue
orders/positions, unresolved, unknown, ledger working/positions. Parity
recovery copy verified 02:32:18Z (hash prefix beabb5016435).

Deploy: clean WSL `main` fast-forwarded `e2d68088e` → `9f000d404`. Wheel
`polymarket_us-1.0.2-py3-none-any.whl` sha256 re-verified
`c7f61e31…12b8e` and installed `--no-deps --no-index`; live venv 0.1.2 → 1.0.2;
live client `max_retries=0`. 272 tracked inputs match; CLI smoke OK;
`config/selection_exclusions.json` empty; live-checkout suite 2,320 PASS.
`DEPLOYED_COMMIT` set only after checks. Gate 2 02:35:46Z all zero; started
`auto 15 32 hybrid`, supervisor 413082 / runner 413096.

Acceptance 02:38:36Z: 0 tracebacks/recoveries/critical/invariant lines;
`[selection-exclusions] excluded_leagues=none`; `[warm-restart]
outcome=no_scope` (old runner never wrote a scope) and
`data/warm-restart-scope.json` now written; SDK 1.0.2; API 2.273/s, 0
failures, 0 rate limited; hybrid receiving, 0 gaps; authenticated 30 orders on
24 markets, 0 positions/unresolved/unknown; window loss $1.61/$30 cap 30.

Next natural checks: `[warm-restart] outcome=readmitted` at the next restart
and its breadth recovery versus the 216 market-hour baseline; faster disconnect
detection from graceful closes; API rate unchanged (no SDK retries).
Rollback: reinstall 0.1.2 wheel the same way and revert commits; the helper
works on both SDKs.
