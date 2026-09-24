# Hybrid live log review — September 24, 2026

Read-only operator request. No source/config changes, trading mutations or restart.
The unchanged supervisor21730/runner21744 continues on deployed ab3f7f352,
auto size15/32 hybrid. REST remains complete account authority.

## Measured problems

The 27 completed API-budget windows ending15:46:15–16:14:09Z span1762.8s:
5830 attempts, weighted3.307 requests/s. These are aggregated window boundaries,
not an exact wall-clock30-minute event enumeration.

| Failure | Count |
|---|---:|
| Balance HTTP429 | 19 |
| Positions HTTP429 | 18 |
| Execution history HTTP429 | 2 |
| Cash history HTTP429 | 1 |
| Order retrieval HTTP404 | 15 |
| Earnings timeout / unknown HTTP status | 1 |

Risk logs repeatedly show equity_unknown/window_not_ready entry holds. Selection
at16:03:15Z reports balance evidence678s old; at16:08:16Z capital proof is valid
and mutations resumed. This explains interrupted entry availability, not a
proven rate-limit threshold, ban, Cloudflare attribution or hybrid-caused fault.

Four timed IOC acknowledgments were late by5.046,5.294,34.634 and24.071s.
The two largest were leovik-sydvit at16:07:19.606Z and cs2-lph-wbta at
16:07:26.638Z. Deadline offset measures command acknowledgment, not confirmed
fill latency or a continuously uncovered position. Cancel-confirmation ages
were2.008/1.581s; the full delay cannot be assigned to cancellation alone.
Both positions are absent in the fresh16:15:30Z authenticated snapshot.
Trace deadline scheduling/account-proof recovery before selecting a fix.

An earnings refresh timed out15:48:38Z; an earnings call later completed in
the window ending16:04:09Z. This review did not authenticate an earnings total.
The official-status feed timed out16:10:40Z and recovered HEALTHY/NORMAL at
16:12:54Z. Direct web status retrieval also failed; no platform-wide outage
claim follows. Current changelog review supplies no demonstrated explanation
for these account429s.

## Current account and feed evidence

Authenticated snapshot16:15:30.680Z contains34orders:30 flat entries and four
exact reducing orders, with positions -15 gruvoj-zelmat, -7 ungmax-iasvic,
+10 uzuale-midmih and -15 fasiva-tebyar. Local16:15:31Z has34 working orders,
zero unresolved/unknown/absent. Snapshot calls are sequential, not atomic.
Current position ages were34–345s. Existing16:00:58Z selection clocks put the
earliest current game at16:25Z; this is stored schedule evidence, not a fresh
venue live-state assertion.

Private telemetry continues receiving: orders15187, pending0, cumulative gaps5
unchanged from the earlier check, scoped admissions101. Counts are processing
telemetry, not fills or requests saved; private balance recovery remains unproved.
One reducer was canceled/replaced11 times over its current short episode. The
logged replacements completed, but queue/resting-time efficiency remains an
open measurement, not proof of an additional defect.

## Recommendation and scope

Prioritize exit-deadline/account-throttle interaction over new optimization.
Keep ordinary order404 reconciliation and reporting timeouts separate from
unresolved ownership. Do not weaken current proofs or alter trading rules.
This review establishes a material latency issue despite recovered current
coverage; it does not authorize implementation, stop, deployment or restart.

Sources: runtime commissioning.log; authenticated read-only account snapshot;
read-only execution ledger and stored quality discovery clocks;
[official changelog](https://docs.polymarket.us/changelog).
