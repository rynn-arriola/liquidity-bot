#!/usr/bin/env bash
# Restart the observation-only scalp collector without touching commissioning.

set -u
cd "$(dirname "$0")/.." || exit 1

mkdir -p data logs
while true; do
    printf '[%s] starting scalp measurement\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    PYTHONPATH=src .venv/bin/python -B -u -m liquidity_bot.scalp_run \
        --database data/scalp.db \
        --live-database data/commissioning.db \
        --minutes 0 \
        --pure-markets 40 \
        --horizon-minutes 1440 \
        --refresh-minutes 15 \
        --rest-rps 1 \
        --size 10 \
        --no-fill-timeout 300
    code=$?
    printf '[%s] scalp measurement exited code=%s; retrying in 15s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code"
    sleep 15
done
