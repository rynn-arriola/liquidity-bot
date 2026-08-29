#!/usr/bin/env bash

set -u
cd "$(dirname "$0")/.." || exit 1

dashboard_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.tv_dashboard/ {print $1}'
}

PIDS=$(dashboard_pids)
if [ -z "$PIDS" ]; then
    echo "liquidity TV dashboard already stopped"
    rm -f data/tv-dashboard.pid
    exit 0
fi

for pid in $PIDS; do
    kill "$pid" 2>/dev/null || true
done
for _ in $(seq 1 30); do
    [ -z "$(dashboard_pids)" ] && break
    sleep 0.1
done
LEFT=$(dashboard_pids)
if [ -n "$LEFT" ]; then
    echo "dashboard did not stop cleanly: $LEFT"
    exit 1
fi
rm -f data/tv-dashboard.pid
echo "liquidity TV dashboard stopped"
