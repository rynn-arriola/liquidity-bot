#!/usr/bin/env bash
# Start the isolated read-only TV view. Browser opening is WSL-desktop only.

set -u
cd "$(dirname "$0")/.." || exit 1

OPEN_BROWSER=0
if [ "${1:-}" = "--open" ]; then
    OPEN_BROWSER=1
elif [ "$#" -gt 0 ]; then
    echo "usage: ./scripts/start_tv_dashboard.sh [--open]"
    exit 2
fi

HOST=${LIQUIDITY_TV_HOST:-0.0.0.0}
PORT=${LIQUIDITY_TV_PORT:-8787}
case "$PORT" in
    ''|*[!0-9]*) echo "dashboard not started: LIQUIDITY_TV_PORT must be numeric"; exit 2 ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    echo "dashboard not started: LIQUIDITY_TV_PORT must be from 1 to 65535"
    exit 2
fi
URL="http://localhost:$PORT"

dashboard_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.tv_dashboard/ {print $1}'
}

dashboard_ready() {
    .venv/bin/python -B - "$PORT" <<'PY' >/dev/null 2>&1
import json
import sys
from urllib.request import urlopen

with urlopen(f"http://127.0.0.1:{int(sys.argv[1])}/api/state", timeout=0.3) as response:
    payload = json.load(response)
if payload.get("schema_version") != 1 or "ZERO VENUE API CALLS" not in payload.get("source", ""):
    raise SystemExit(1)
PY
}

open_wsl_browser() {
    [ "$OPEN_BROWSER" -eq 1 ] || return 0
    grep -qi microsoft /proc/version 2>/dev/null || return 0
    if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoProfile -NonInteractive -Command \
            "Start-Process '$URL'" >/dev/null 2>&1 || true
    elif command -v explorer.exe >/dev/null 2>&1; then
        explorer.exe "$URL" >/dev/null 2>&1 || true
    fi
}

if [ -n "$(dashboard_pids)" ]; then
    if ! dashboard_ready; then
        echo "dashboard process exists but its local endpoint is unhealthy"
        exit 1
    fi
    echo "liquidity TV dashboard already running at $URL"
    open_wsl_browser
    exit 0
fi

if [ ! -x .venv/bin/python ]; then
    echo "dashboard not started: .venv/bin/python is missing"
    exit 1
fi

mkdir -p data logs
setsid nohup .venv/bin/python -B -m liquidity_bot.tv_dashboard \
    --host "$HOST" --port "$PORT" \
    >> logs/tv-dashboard.log 2>&1 < /dev/null &
DASHBOARD_PID=$!
printf '%s\n' "$DASHBOARD_PID" > data/tv-dashboard.pid

READY=0
for _ in $(seq 1 30); do
    if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        break
    fi
    if dashboard_ready && kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        READY=1
        break
    fi
    sleep 0.1
done

if [ "$READY" -ne 1 ]; then
    echo "dashboard did not remain up; last output:"
    tail -20 logs/tv-dashboard.log 2>/dev/null || true
    exit 1
fi

echo "liquidity TV dashboard pid=$DASHBOARD_PID url=$URL"
if [ "$HOST" = "0.0.0.0" ]; then
    echo "LAN display: http://<this-machine-LAN-IP>:$PORT (local/private clients only)"
fi
open_wsl_browser
