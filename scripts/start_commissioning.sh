#!/usr/bin/env bash
# Detached one-market-at-a-time launcher. It refuses overlapping executors.

set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p data logs
exec 9>data/host_control.lock
flock -x 9 || exit 1

TARGET=${1:-auto}
QUOTE_SIZE=${2:-}
SLOT_CAPACITY=${3:-20}
ACCOUNT_STATE_MODE=${4:-saved}
if [ "$#" -gt 4 ]; then
    echo "usage: ./scripts/start_commissioning.sh [MARKET-SLUG|auto] [QUOTE-SIZE] [SLOT-CAPACITY] [websocket|rest|rest-fallback|hybrid]"
    exit 2
fi
case "$ACCOUNT_STATE_MODE" in
    websocket|rest|rest-fallback|hybrid|saved) ;;
    *) echo "REFUSED: invalid account-state mode"; exit 2 ;;
esac

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

mkdir -p data logs
if [ -z "$QUOTE_SIZE" ]; then
    if [ -f data/commissioning_quote_size ]; then
        QUOTE_SIZE=$(tr -d '[:space:]' < data/commissioning_quote_size)
    else
        QUOTE_SIZE=5
    fi
fi
if [ ! -x .venv/bin/python ]; then
    echo "REFUSED: .venv/bin/python is missing"
    exit 2
fi
if ! QUOTE_SIZE=$(.venv/bin/python -B -m liquidity_bot.quote_size "$QUOTE_SIZE"); then
    exit 2
fi
if ! SLOT_CAPACITY=$(.venv/bin/python -B -m liquidity_bot.slot_capacity "$SLOT_CAPACITY"); then
    exit 2
fi
if [ ! -f ../maker-lab/credentials.py ] \
        && [ -z "${POLYMARKET_US_KEY_ID:-}" ]; then
    echo "REFUSED: shared credentials are unavailable"
    exit 2
fi

printf '%s\n' "$QUOTE_SIZE" > data/commissioning_quote_size
printf '%s\n' "$SLOT_CAPACITY" > data/commissioning_slot_capacity
HOST_TOKEN=$(.venv/bin/python -B -m liquidity_bot.host_recovery --lock-held \
    arm "$TARGET" "$QUOTE_SIZE" "$SLOT_CAPACITY" "$ACCOUNT_STATE_MODE") || exit 2
setsid nohup ./scripts/supervise_commissioning.sh "$TARGET" "$QUOTE_SIZE" "$SLOT_CAPACITY" "$ACCOUNT_STATE_MODE" \
    normal "$HOST_TOKEN" >> logs/supervisor.log 2>&1 < /dev/null 9>&- &
flock -u 9
exec 9>&-

echo "starting liquidity commissioning target=$TARGET size=$QUOTE_SIZE slots=$SLOT_CAPACITY account_state=$ACCOUNT_STATE_MODE"
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

# The TV view is an independent read-only observer.  A display/browser failure
# must never convert a successful trading startup into a failed startup.
if ! ./scripts/start_tv_dashboard.sh --open; then
    echo "WARNING: bot is running, but the optional TV dashboard did not start"
fi
