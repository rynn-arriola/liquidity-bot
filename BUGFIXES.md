# Production defect index

The complete defect record through 2026-08-27 is preserved at
[docs/history/BUGFIXES_THROUGH_2026-08-27.md](docs/history/BUGFIXES_THROUGH_2026-08-27.md).

Search it before designing a repair:

```powershell
rg -n -i "<symptom, component, or error>" BUGFIXES.md docs/history/BUGFIXES_*.md
```

## Latest deployed defects

- Dashboard account-read failure could render false zero/flat state. Fixed by
  publishing venue-backed fields only after a complete account snapshot.
- Closed/not-open flat owners could park nominal 20-slot capacity. Fixed with
  fresh exact metadata and complete ownership proof before transactional
  replacement.
- Alternating permanent book-error strings advanced retirement rounds. Fixed
  by stable permanent failure families.
- Maintenance repeated the same full traceback every minute. Concise attempts
  remain complete; full traces are bounded.
- An open-position paper mark could force loss-realizing hard-stop shutdown.
  Fixed with provisional breach state and newer post-close balance evidence.
- Historical/current reward-table trading rows omitted account losses. Current
  reporting uses bounded account truth when its prerequisites are proved and
  visibly marks fallbacks.

The latest trading-source repair is `dc26c96`; current deployment and runtime
state must always be read from [STATUS.md](STATUS.md) and the server.

## Required entry format

Every production defect must record:

1. operator-visible symptom;
2. measured mechanism and disproved alternatives;
3. safety constraints and affected flows;
4. focused regression plus deliberate reversal proof;
5. complete local/server verification and deployment/live-acceptance state;
6. operational cost or exposure uncertainty.

Do not turn this index into a second current-status file.
