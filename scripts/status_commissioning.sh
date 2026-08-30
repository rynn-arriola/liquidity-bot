#!/usr/bin/env bash

set -u
cd "$(dirname "$0")/.." || exit 1

runner=$(ps -eo pid=,comm=,args= \
    | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.live_commissioning/ {print $1}')
supervisor=$(ps -eo pid=,args= | awk '/[s]upervise_commissioning\.sh/ {print $1}')
maker=$(ps -eo pid=,comm=,args= \
    | awk '$2 ~ /^python/ && $0 ~ /run_live\.py/ {print $1}')

[ -n "$maker" ] && echo "WARNING: maker-lab runner active: $maker" \
    || echo "maker-lab: stopped"
[ -n "$supervisor" ] && echo "liquidity supervisor: $supervisor" \
    || echo "liquidity supervisor: stopped"
[ -n "$runner" ] && echo "liquidity runner: $runner" \
    || echo "liquidity runner: stopped"
if [ -f data/commissioning_quote_size ]; then
    echo "configured quote size: $(tr -d '[:space:]' < data/commissioning_quote_size)"
else
    echo "configured quote size: 5 (default)"
fi
if [ -f data/commissioning_slot_capacity ]; then
    SLOT_CAPACITY=$(tr -d '[:space:]' < data/commissioning_slot_capacity)
    if NORMALIZED_CAPACITY=$(.venv/bin/python -B -m liquidity_bot.slot_capacity "$SLOT_CAPACITY" 2>/dev/null); then
        echo "configured slot capacity: $NORMALIZED_CAPACITY"
    else
        echo "configured slot capacity: INVALID"
    fi
else
    echo "configured slot capacity: 20 (default)"
fi

echo
echo "--- commissioning log ---"
tail -20 logs/commissioning.log 2>/dev/null || echo "no commissioning log"
