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

echo
echo "--- commissioning log ---"
tail -20 logs/commissioning.log 2>/dev/null || echo "no commissioning log"
