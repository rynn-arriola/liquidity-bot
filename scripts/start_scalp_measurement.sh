#!/usr/bin/env bash
# Detached observation-only launcher; refuses duplicate collectors.

set -u
cd "$(dirname "$0")/.." || exit 1

collector_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.scalp_run/ {print $1}'
}
supervisor_pids() {
    ps -eo pid=,args= | awk '/[s]upervise_scalp_measurement\.sh/ {print $1}'
}

if [ -n "$(collector_pids)" ] || [ -n "$(supervisor_pids)" ]; then
    echo "REFUSED: scalp measurement is already running"
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
setsid nohup ./scripts/supervise_scalp_measurement.sh \
    >> logs/scalp-measurement.log 2>&1 < /dev/null &

for _ in $(seq 1 20); do
    sleep 1
    [ -n "$(collector_pids)" ] && break
done
if [ -z "$(collector_pids)" ]; then
    echo "collector did not remain up"
    tail -30 logs/scalp-measurement.log 2>/dev/null || true
    exit 1
fi
echo "scalp supervisor pid(s): $(supervisor_pids | tr '\n' ' ')"
echo "scalp collector pid(s):  $(collector_pids | tr '\n' ' ')"
echo "orders: impossible by collector design; database: data/scalp.db"
