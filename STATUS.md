# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state
- **Verified:** 2026-08-29 10:28 ET / 2026-08-29 14:28 UTC
- **Liquidity bot:** LIVE on the laptop; supervisor PID 14497 and runner PID
  14504 are active, with the isolated TV dashboard PID 1681
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Restart proof:** the old runner stopped at 14:20:23Z with
  `operator_sigterm` and `flat=true`; the post-stop authenticated snapshot
  proved zero orders and positions before the deployment started
- **Loaded source:** WSL HEAD and `DEPLOYED_COMMIT` are `2aa9fec`; the runner imports the WSL tree
- **Verification:** all 830 tests pass from the deployed WSL source. At 14:27Z
  authenticated acceptance found 34 orders on 19/20 quoting markets, zero
  positions/absent/unresolved rows and no risk stop
- **API capacity measurement:** startup made 373 attempts/146.9s clean; the next
  64s received 10 execution-history `1015`/429s at 1.813/s, then 66.2s recovered
  with 105 clean reads. The bot stayed live/flat; no slot increase is authorized
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2, Python 3.12, pinned SDK/encryption
  dependency, size 13, restricted credentials and exact execution/scalp data
  are active; the ledger stayed authoritative through the stopped deployment
- **Laptop access/power:** Windows and WSL use the same US residential Comcast
  egress with no active VPN adapter observed; Retail health returned 200/OK;
  AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum
  performance
- **Laptop schedules:** four read-only backup/report systemd timers are active;
  a stopped-safe ledger backup passed; no trading service is installed/enabled
- **Desktop cold backup:** inactive WSL copies of the Windows stopped-server
  databases, credentials and archives match byte-for-byte;
  both databases pass integrity and WSL files are owner-only. No active ledger,
  credential path, trading process, service, timer or API check exists there
- **Active quantity:** size 13 = $390 portfolio exposure, $117 market exposure, $52 market loss, $104 session/hard, $10.40 early-cross and $3.90 fee caps; slots, horizon and REST rate stay fixed
- **Natural defect acceptance:** a backward sample at 04:19:49Z emitted `[score-clock] ... action=ignored_stale_sample`; no retirement followed and the runner reached 20/20 quoting. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 13 contracts live |
| Concurrent capacity | 20 market slots |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $390 exposure / $52 loss (`30× / 4×` size); market exposure is $117 (`9×`) |
| Account threshold | $104 realized sensor and $104 marked-equity sensor |
| Risk day | `America/New_York`, DST-aware |

[DECISIONS.md](DECISIONS.md) is authoritative for policy details.

## Former server incident and current resolution

From 22:07 UTC, Retail mutations returned HTTP 403/code 7 location/VPN
denials. The old gateway mislabeled them as ambiguous no-ID PLACE intents and
retirement consumed all 20 owners. The deployed repair fails only the rejected
intent, limits later entry probes, preserves cancels/reducers and repairs only
legacy rows with exact persisted rejection proof.

The 22:56Z server acceptance repaired 20 rows and kept zero unresolved/retired
through three denied probes. The account stayed flat and no
quote could rest. The laptop's 00:40Z startup then accepted 39 quotes across
20 markets with no matching denial, so execution access works from the
selected residential runtime. The containment remains installed.
[Evidence](docs/history/MUTATION_ACCESS_DENIAL_2026-08-28.md).

## Current operating instruction

The laptop remains the sole live owner; the desktop is an inactive manual
backup. Any handoff requires stop, venue proof and a verified ledger snapshot.

Unqualified `run the bot` targets the laptop; explicit `run on the PC` selects
the desktop with full handoff. Parity covers PC/laptop/GitHub, laptop WSL and a
verified private execution-ledger recovery copy; no secret/database enters git
and no copy is activated. Drive remains the separate manual `run weekly task`.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
