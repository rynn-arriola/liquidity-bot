"use strict";

const byId = (id) => document.getElementById(id);
const formatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

let lastSnapshot = null;
let lastReceived = null;

function readableAge(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "unknown age";
  const value = Math.max(0, Math.floor(Number(seconds)));
  if (value < 2) return "just now";
  if (value < 60) return `${value}s ago`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s ago`;
  if (value < 86400) return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m ago`;
  return `${Math.floor(value / 86400)}d ago`;
}

function metric(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function numericValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function money(value, { signed = false, missing = "—" } = {}) {
  const number = numericValue(value);
  if (number === null) return missing;
  const sign = signed && number >= 0 ? "+" : "";
  return `$${sign}${number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function applyMoneyTone(element, value) {
  const number = numericValue(value);
  element.classList.remove("positive", "negative", "neutral");
  element.classList.add(number === null || number === 0 ? "neutral" : (number > 0 ? "positive" : "negative"));
}

function shortMarket(slug) {
  if (!slug) return "Available capacity";
  const clean = slug.replace(/^aec-/, "");
  if (clean.length <= 28) return clean;
  return `${clean.slice(0, 13)}…${clean.slice(-12)}`;
}

function setIndicator(id, state) {
  const element = byId(id);
  element.className = state;
}

function renderSlots(slots, capacity) {
  const grid = byId("slot-grid");
  grid.replaceChildren();
  const safeSlots = Array.isArray(slots) ? slots : [];
  const fallbackCapacity = Math.min(32, Math.max(20, safeSlots.length || 20));
  const safeCapacity = Number.isInteger(capacity) && capacity >= 20 && capacity <= 32
    ? capacity
    : fallbackCapacity;
  const columns = safeCapacity > 24 ? 16 : (safeCapacity > 22 ? 12 : (safeCapacity > 20 ? 11 : 10));
  grid.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  for (let index = 0; index < safeCapacity; index += 1) {
    const source = safeSlots[index] || { number: index + 1, market: null, status: "empty" };
    const slot = document.createElement("article");
    slot.className = `slot ${source.status || "empty"}`;
    slot.title = source.market || "No market in the latest observed scope";

    const number = document.createElement("span");
    number.className = "slot-number";
    number.textContent = `SLOT ${String(source.number || index + 1).padStart(2, "0")}`;
    const market = document.createElement("span");
    market.className = "slot-market";
    market.textContent = shortMarket(source.market);
    const status = document.createElement("span");
    status.className = "slot-status";
    status.textContent = source.status === "quoting" ? "resting order" : (source.status || "empty");
    slot.append(number, market, status);
    grid.append(slot);
  }
}

function activityTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : formatter.format(parsed);
}

function renderActivity(activity) {
  const list = byId("activity-list");
  list.replaceChildren();
  if (!Array.isArray(activity) || activity.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-activity";
    empty.textContent = "No matching local events in the recent log tail.";
    list.append(empty);
    return;
  }
  for (const item of activity.slice(0, 9)) {
    const row = document.createElement("li");
    row.className = "activity-item";
    const dot = document.createElement("i");
    dot.className = `activity-dot ${item.kind || "system"}`;
    const text = document.createElement("span");
    text.className = "activity-text";
    text.textContent = item.message || "Local event";
    const time = document.createElement("time");
    time.className = "activity-time";
    time.textContent = activityTime(item.at);
    row.append(dot, text, time);
    list.append(row);
  }
  // The dashboard is a one-screen instrument panel. Keep the newest events
  // that physically fit instead of making the whole page scroll or clipping a
  // partial final row when the browser/TV viewport changes.
  while (list.scrollHeight > list.clientHeight + 1 && list.children.length > 1) {
    list.lastElementChild.remove();
  }
}

function appendMoneyCell(row, value, { signed = false, missing = "—", estimate = false } = {}) {
  const cell = document.createElement("td");
  cell.className = "gain-money";
  cell.textContent = `${estimate && numericValue(value) !== null ? "~" : ""}${money(value, { signed, missing })}`;
  applyMoneyTone(cell, value);
  row.append(cell);
}

function renderGainReport(report, livePnl) {
  const todayPnl = byId("today-pnl");
  const overallPnl = byId("overall-pnl");
  const body = byId("gain-table-body");
  body.replaceChildren();

  if (!report || !report.available || !report.headline) {
    const hasLive = livePnl && livePnl.available;
    todayPnl.textContent = hasLive
      ? money(livePnl.marked_pnl, { signed: true }) : "—";
    overallPnl.textContent = "—";
    applyMoneyTone(todayPnl, hasLive ? livePnl.marked_pnl : null);
    applyMoneyTone(overallPnl, null);
    byId("today-pnl-detail").textContent = hasLive
      ? (livePnl.realized_pnl == null || livePnl.unrealized_pnl == null
        ? `Marked account trading · ${readableAge(livePnl.age_seconds)}`
        : `Closed ${money(livePnl.realized_pnl, { signed: true })} · open ${money(livePnl.unrealized_pnl, { signed: true })} · ${readableAge(livePnl.age_seconds)}`)
      : "Waiting for a proven mark";
    byId("overall-pnl-detail").textContent = "Incomplete ledger + paid gain";
    byId("gain-report-age").textContent = "Unavailable";
    byId("gain-report-state").textContent = "LOCAL SNAPSHOT NOT YET AVAILABLE";
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "gain-empty";
    cell.textContent = "Waiting for the first hourly snapshot…";
    row.append(cell);
    body.append(row);
    return;
  }

  const headline = report.headline;
  const currentMark = livePnl && livePnl.available
    ? livePnl.marked_pnl : headline.today_pnl;
  todayPnl.textContent = money(currentMark, { signed: true });
  overallPnl.textContent = money(headline.overall_pnl, { signed: true });
  applyMoneyTone(todayPnl, currentMark);
  applyMoneyTone(overallPnl, headline.overall_pnl);
  const closedValue = livePnl && livePnl.available
    ? livePnl.realized_pnl : headline.today_realized;
  const openValue = livePnl && livePnl.available
    ? livePnl.unrealized_pnl : headline.today_unrealized;
  const closed = money(closedValue, { signed: true });
  const open = money(openValue, { signed: true });
  const markAge = readableAge(livePnl && livePnl.available
    ? livePnl.age_seconds : headline.today_age_seconds);
  byId("today-pnl-detail").textContent =
    closedValue == null || openValue == null
      ? `Marked account trading · ${markAge}`
      : `Closed ${closed} · open ${open} · ${markAge}`;
  byId("overall-pnl-detail").textContent = headline.overall_includes_settlements
    ? `Fills + settlements + paid gain · estimate · Since ${headline.overall_since_et} ET`
    : `Incomplete ledger + paid gain · Since ${headline.overall_since_et} ET`;
  byId("gain-report-age").textContent = readableAge(report.age_seconds);
  const stale = Number(report.age_seconds) > 7200;
  byId("gain-report-state").textContent = stale ? "SNAPSHOT STALE" : "LOCAL SNAPSHOT READY";

  const rows = Array.isArray(report.rows) ? report.rows : [];
  for (const item of rows) {
    const row = document.createElement("tr");
    if (item.current_day) row.className = "current-day";
    const dateCell = document.createElement("th");
    dateCell.scope = "row";
    dateCell.textContent = `${item.date_et}${item.current_day ? " · TODAY" : ""}`;
    row.append(dateCell);
    const rowTrading = item.current_day && livePnl && livePnl.available
      ? livePnl.marked_pnl : item.trading;
    appendMoneyCell(row, rowTrading, { signed: true, missing: "Unavailable", estimate: item.trading_estimate });
    appendMoneyCell(row, item.gain, { missing: "Not posted" });
    if (item.gain_posting && (item.pending == null || Number(item.pending) === 0)) {
      const posting = document.createElement("td");
      posting.className = "gain-money pending-net";
      posting.textContent = "Still posting";
      row.append(posting);
    } else {
      appendMoneyCell(row, item.pending, { missing: "Not posted" });
    }
    const hours = document.createElement("td");
    hours.className = "side-hours";
    hours.textContent = Number.isFinite(Number(item.side_hours)) ? Number(item.side_hours).toFixed(2) : "—";
    row.append(hours);
    if (item.net_status === "finalized") {
      appendMoneyCell(row, item.net, { signed: true });
    } else {
      const net = document.createElement("td");
      net.className = "gain-money pending-net";
      net.textContent = "Pending";
      row.append(net);
    }
    body.append(row);
  }
}

function render(data) {
  lastSnapshot = data;
  lastReceived = Date.now();
  const ledger = data.ledger || {};
  const process = data.process || {};
  const log = data.log || {};
  const scope = ledger.scope;
  const mode = ["live", "stale", "degraded", "offline"].includes(data.mode) ? data.mode : "degraded";
  byId("mode-dot").className = `mode-dot ${mode}`;
  byId("mode-label").textContent = mode.toUpperCase();

  let detail = "Trading runner is not detected on this machine.";
  if (mode === "live") detail = "Runner detected · local telemetry is fresh";
  if (mode === "stale") detail = "Runner detected · local activity has gone quiet";
  if (mode === "degraded") detail = "Runner detected · one or more local sources are unavailable";
  byId("mode-detail").textContent = detail;

  byId("working-orders").textContent = metric(ledger.working_orders);
  byId("working-markets").textContent = ledger.working_markets === null || ledger.working_markets === undefined ? "unknown markets" : `${ledger.working_markets} market${ledger.working_markets === 1 ? "" : "s"}`;
  byId("scope-size").textContent = scope ? metric(scope.size) : "—";
  byId("scope-age").textContent = scope ? `${scope.status || "observed"} · ${readableAge(scope.age_seconds)}` : "No scope snapshot";
  byId("positions").textContent = metric(ledger.positions);
  const attentionKnown = ledger.unresolved_intents !== null && ledger.unresolved_intents !== undefined && ledger.unknown_orders !== null && ledger.unknown_orders !== undefined;
  byId("attention-count").textContent = attentionKnown ? String(ledger.unresolved_intents + ledger.unknown_orders) : "—";
  byId("attention-detail").textContent = attentionKnown ? `${ledger.unresolved_intents} unresolved · ${ledger.unknown_orders} unknown` : "intent + unknown unavailable";

  byId("runner-state").textContent = process.runner ? `UP · ${readableAge(process.runner_uptime_seconds).replace(" ago", "")}` : "STOPPED";
  byId("supervisor-state").textContent = process.supervisor ? "UP" : "STOPPED";
  byId("ledger-state").textContent = ledger.available ? readableAge(ledger.age_seconds) : "UNAVAILABLE";
  byId("log-state").textContent = log.available ? readableAge(log.age_seconds) : "UNAVAILABLE";
  setIndicator("runner-indicator", process.runner ? "good" : "bad");
  setIndicator("supervisor-indicator", process.supervisor ? "good" : "bad");
  setIndicator("ledger-indicator", ledger.available ? (ledger.age_seconds > 120 ? "warn" : "good") : "bad");
  setIndicator("log-indicator", log.available ? (log.age_seconds > 120 ? "warn" : "good") : "bad");

  const capacity = Number(data.capacity);
  byId("scope-capacity").textContent = Number.isInteger(capacity) ? String(capacity) : "—";
  byId("slot-capacity").textContent = Number.isInteger(capacity) ? String(capacity) : "—";
  renderSlots(ledger.slots, capacity);
  renderActivity(data.activity);
  renderGainReport(data.gain_report, data.live_pnl);
  byId("footer-source").textContent = data.source || "LOCAL READ-ONLY";
  updateObservationAge();
}

function updateObservationAge() {
  if (!lastReceived) return;
  const elapsed = (Date.now() - lastReceived) / 1000;
  byId("observed-age").textContent = readableAge(elapsed);
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    byId("mode-dot").className = "mode-dot degraded";
    byId("mode-label").textContent = "DISPLAY DISCONNECTED";
    byId("mode-detail").textContent = "The local dashboard service is not responding.";
  }
}

function updateClock() {
  byId("clock").innerHTML = `${formatter.format(new Date())} <small>ET</small>`;
  updateObservationAge();
}

updateClock();
refresh();
setInterval(updateClock, 1000);
setInterval(refresh, 1000);
window.addEventListener("resize", () => {
  if (lastSnapshot) renderActivity(lastSnapshot.activity);
});
