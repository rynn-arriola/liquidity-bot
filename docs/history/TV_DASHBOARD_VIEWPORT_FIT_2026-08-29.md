# TV dashboard viewport fit — 2026-08-29

## Symptom and cause

At the browser's 1280×720 laptop viewport, the dashboard document measured
1,542 pixels tall. The gain table and lower slot/runtime information therefore
required more than one full screen of vertical scrolling. The page used
content-sized cards, a five-column/four-row slot map and stacked health/activity
panels; its width breakpoints did not account for available screen height.

## Repair

The dashboard is now a `100dvh` grid with viewport-relative gaps, padding,
type and card sizes. Landscape screens use a ten-column/two-row slot map and
place capacity, health and recent activity in one row. Height breakpoints
compact the seven-day table and secondary labels before space runs out. Recent
activity retains the newest rows that physically fit instead of adding page
height or clipping a partial final row.

No state, accounting, API, ledger or trading behavior changed. The dashboard
continues to read only its local ledger/log/process/snapshot inputs and makes
zero venue calls.

## Verification and deployment

Browser measurements proved document width/height equal the viewport and zero
panel overflow at 800×600, 1024×768, 1280×720, 1366×768 and 1920×1080. The
original 1280×720 case fell from 1,542 pixels to exactly 720 with every major
section visible. A deliberate reversal from fixed viewport height to minimum
height failed the frontend regression exactly; all 842 tests passed after the
repair was restored.

Commit `c8a7a45` is active in the laptop WSL checkout. Static assets are read
per request, so the existing dashboard PID loaded them on browser refresh; no
dashboard or bot restart occurred. The bot PID and loaded trading-source marker
remained unchanged.
