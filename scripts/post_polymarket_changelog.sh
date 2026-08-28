#!/usr/bin/env bash
# Public RSS only: no Polymarket key, no trading ledger and no bot process.

set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs data

flock -n data/polymarket-changelog.lock \
    timeout 30s nice -n 10 .venv/bin/python -B \
        scripts/polymarket_changelog_monitor.py --post-discord
