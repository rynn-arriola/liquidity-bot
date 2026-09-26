#!/usr/bin/env bash

set -u
cd "$(dirname "$0")/.." || exit 1

collector=$(ps -eo pid=,etimes=,comm=,args= \
    | awk '$3 ~ /^python/ && $0 ~ /liquidity_bot\.scalp_run/ {print}')
supervisor=$(ps -eo pid=,etimes=,args= \
    | awk '/[s]upervise_scalp_measurement\.sh/ {print}')
[ -n "$supervisor" ] && echo "scalp supervisor: $supervisor" \
    || echo "scalp supervisor: stopped"
[ -n "$collector" ] && echo "scalp collector:  $collector" \
    || echo "scalp collector: stopped"

if [ -f data/scalp.db ]; then
    PYTHONPATH=src .venv/bin/python -B scripts/scalp_measurement_report.py \
        --database data/scalp.db --horizons 30 --compact
else
    echo "scalp database: not created"
fi
echo "--- recent collector log ---"
tail -20 logs/scalp-measurement.log 2>/dev/null || true
