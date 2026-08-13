#!/usr/bin/env bash
# Detached one-market launcher. It refuses to overlap maker-lab or itself.

set -u
cd "$(dirname "$0")/.." || exit 1

SLUG=${1:-}
if [ -z "$SLUG" ]; then
    echo "usage: ./scripts/start_commissioning.sh MARKET-SLUG"
    exit 2
fi

maker_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /run_live\.py/ {print $1}'
}
runner_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.live_commissioning/ {print $1}'
}
supervisor_pids() {
    ps -eo pid=,args= | awk '/[s]upervise_commissioning\.sh/ {print $1}'
}

if [ -n "$(maker_pids)" ]; then
    echo "REFUSED: maker-lab run_live.py is still running"
    exit 2
fi
if [ -n "$(runner_pids)" ] || [ -n "$(supervisor_pids)" ]; then
    echo "REFUSED: liquidity commissioning is already running"
    exit 2
fi
if [ ! -x .venv/bin/python ]; then
    echo "REFUSED: .venv/bin/python is missing"
    exit 2
fi
if [ ! -f ../maker-lab/credentials.py ] \
        && [ -z "${POLYMARKET_US_KEY_ID:-}" ]; then
    echo "REFUSED: shared credentials are unavailable"
    exit 2
fi

mkdir -p data logs
setsid nohup ./scripts/supervise_commissioning.sh "$SLUG" \
    >> logs/supervisor.log 2>&1 < /dev/null &

echo "starting one-contract commissioning for $SLUG"
for _ in $(seq 1 20); do
    sleep 1
    [ -n "$(runner_pids)" ] && break
done
if [ -z "$(runner_pids)" ]; then
    echo "runner did not remain up; last supervisor output:"
    tail -20 logs/supervisor.log 2>/dev/null || true
    tail -20 logs/commissioning.log 2>/dev/null || true
    exit 1
fi

echo "supervisor pid(s): $(supervisor_pids | tr '\n' ' ')"
echo "runner pid(s):     $(runner_pids | tr '\n' ' ')"
echo "log: $(pwd)/logs/commissioning.log"
