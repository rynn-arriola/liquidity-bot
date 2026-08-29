#!/usr/bin/env bash
# One isolated, non-overlapping hourly reward report. The report opens the
# trading ledger read-only, makes one account-wide earnings request, and posts
# only to the dedicated rewards webhook loaded from the shared credentials
# file. It has no trading mutation path and does not share the runner process.

set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs data

STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[hourly-reward-table] at=${STAMP} starting"

flock -n data/reward-table-hourly.lock \
    timeout 90s nice -n 10 .venv/bin/python -B scripts/reward_table.py \
        --days 7 --post-discord --quiet \
        --dashboard-cache data/tv-gain-snapshot.json
CODE=$?
if [ "$CODE" -ne 0 ]; then
    echo "[hourly-reward-table] at=${STAMP} failed code=${CODE}"
    exit "$CODE"
fi

echo "[hourly-reward-table] at=${STAMP} complete"
