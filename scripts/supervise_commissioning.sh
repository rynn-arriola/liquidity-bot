#!/usr/bin/env bash
# Restart only after an unexpected process death. A caught trading failure,
# refusal, non-flat exit, or clean cutoff is terminal and is never restarted.

set -u
cd "$(dirname "$0")/.." || exit 1

SLUG=${1:-}
if [ -z "$SLUG" ]; then
    echo "market slug is required"
    exit 2
fi

PY=.venv/bin/python
MAX_FAST_CRASHES=3
FAST_CRASH_SECONDS=120
FAST=0
BACKOFF=10

alert() {
    "$PY" -m liquidity_bot.supervisor_alert --critical "$1" >/dev/null 2>&1 || true
}

echo "[supervisor] one-contract commissioning market=$SLUG"
alert "Liquidity Bot supervisor started for one-contract commissioning."

while true; do
    STARTED=$(date -u +%s)
    "$PY" -B -m liquidity_bot.live_commissioning \
        --market-slug "$SLUG" \
        --database data/commissioning.db \
        --lock-file data/commissioning.lock \
        --arm-live \
        --accept-one-contract-risk \
        --exclusive-api-key \
        --maker-lab-stopped >> logs/commissioning.log 2>&1
    CODE=$?
    RAN=$(( $(date -u +%s) - STARTED ))

    case "$CODE" in
        0)
            echo "[supervisor] clean exit after ${RAN}s; not restarting"
            exit 0
            ;;
        2)
            echo "[supervisor] startup refused; not restarting"
            alert "Liquidity Bot startup was refused. Nothing was launched."
            exit 2
            ;;
        3)
            echo "[supervisor] process exited without proving flat; not restarting"
            alert "Liquidity Bot exited without proving flat. Check the exchange immediately."
            exit 3
            ;;
        4)
            echo "[supervisor] caught runtime/strategy failure; not restarting"
            alert "Liquidity Bot stopped after a caught runtime failure and will not restart."
            exit 4
            ;;
    esac

    if [ "$RAN" -lt "$FAST_CRASH_SECONDS" ]; then
        FAST=$((FAST + 1))
    else
        FAST=0
        BACKOFF=10
    fi
    if [ "$FAST" -ge "$MAX_FAST_CRASHES" ]; then
        echo "[supervisor] ${FAST} fast process deaths; giving up"
        alert "Liquidity Bot supervisor gave up after ${FAST} fast process deaths. Check the exchange."
        exit 1
    fi

    echo "[supervisor] unexpected exit $CODE after ${RAN}s; restart in ${BACKOFF}s"
    alert "Liquidity Bot process died unexpectedly (exit ${CODE}); recovery restart in ${BACKOFF}s."
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
    [ "$BACKOFF" -gt 60 ] && BACKOFF=60
done
