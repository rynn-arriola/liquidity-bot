#!/usr/bin/env bash

set -u
cd "$(dirname "$0")/.." || exit 1

PIDS=$(ps -eo pid=,comm=,args= \
    | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.tv_dashboard/ {print $1}')
if [ -z "$PIDS" ]; then
    echo "liquidity TV dashboard: STOPPED"
    exit 1
fi

echo "liquidity TV dashboard: RUNNING"
echo "pid(s): $(printf '%s' "$PIDS" | tr '\n' ' ')"
echo "local: http://localhost:${LIQUIDITY_TV_PORT:-8787}"
