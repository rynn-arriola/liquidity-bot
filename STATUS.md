# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state
- **Verified:** 2026-08-29 00:00 ET / 2026-08-29 04:00 UTC
- **Liquidity bot:** STOPPED normally on the laptop at the operator's request;
  supervisor PID 778 and runner PID 785 are absent
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop proof:** the final runner record says `operator_sigterm` and
  `flat=true`; the post-stop authenticated dashboard found zero venue orders,
  zero positions, zero absent/unresolved state and no risk stop
- **Loaded source:** `04fddb1`; its trading core remains the reviewed `31f815c`
  repair and it adds only the isolated read-only TV dashboard/startup hook
- **Verification:** laptop passed 785 pre-start tests; the current desktop deployment candidate passed 806 offline tests, the focused 20-slot backward-clock regression and a size-13 authorization/propagation check
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2 is running but the runner and supervisor
  are inactive; Python 3.12, pinned SDK,
  last-used size 10, restricted credentials and exact stopped execution/scalp snapshots
  are installed; ledger integrity/hashes passed and all 785 tests passed
- **Laptop account proof:** the 00:39Z preflight was flat/order-free; the
  00:42Z authenticated live read found 39 working orders across all 20 slots
  and zero positions
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
- **Next-start quantity:** size 13 is reviewed but not active while stopped: $390 portfolio exposure, $117 market exposure, $52 market loss, $104 session/hard, $10.40 early-cross and $3.90 fee caps; slots, horizon and REST rate stay fixed
- **Display/defect:** TV remains read-only. Score-only backward-clock exceptions caused the earlier 10/20 `OBSERVED` state by retiring markets. The high-water-mark repair in `4d074ba` cannot retire any of 20 slots; it is pushed and present in both Windows checkouts but not loaded into the stopped WSL runtime. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 13 contracts on the next separately authorized start; the last stopped run used 10 |
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

The laptop remains the sole stopped owner; the desktop is an inactive manual
backup. Any handoff requires stop, venue proof and a verified ledger snapshot.

Unqualified `run the bot` targets the laptop; explicit `run on the PC` selects
the desktop with full handoff. Parity now means PC/laptop/GitHub only. Drive is
a separate manual `run weekly task`; its first encrypted laptop archive passed
local/transferred verification but was not uploaded; three-way parity passed clean on `4d73121`; local size-13 decision documentation is newer and must be reviewed/pushed before deployment.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
