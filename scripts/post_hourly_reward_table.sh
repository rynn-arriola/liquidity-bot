#!/usr/bin/env bash
# One isolated, non-overlapping hourly reward report. The report opens the
# trading ledger read-only and makes at most twelve bounded account reads. Its
# opportunistic API lane waits for unused host allowance and is capped at one
# request per four seconds; the wider timeout lets it finish within the hourly
# interval without competing with trading. It posts only to the dedicated
# rewards webhook and has no trading mutation path.

set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs data

STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[hourly-reward-table] at=${STAMP} starting"

flock -n data/reward-table-hourly.lock \
    timeout 45m nice -n 10 .venv/bin/python -B scripts/reward_table.py \
        --days 7 --post-discord --quiet \
        --dashboard-cache data/tv-gain-snapshot.json
CODE=$?
if [ "$CODE" -ne 0 ]; then
    echo "[hourly-reward-table] at=${STAMP} failed code=${CODE}"
    exit "$CODE"
fi

echo "[hourly-reward-table] at=${STAMP} complete"
