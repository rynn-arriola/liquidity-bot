#!/usr/bin/env bash
# Restart only after an unexpected process death. A caught terminal failure,
# refusal, non-flat exit, or operator stop is terminal and is never restarted.

set -u
cd "$(dirname "$0")/.." || exit 1

TARGET=${1:-auto}

PY=.venv/bin/python
MAX_FAST_CRASHES=3
FAST_CRASH_SECONDS=120
FAST=0
BACKOFF=10

alert() {
    "$PY" -m liquidity_bot.supervisor_alert --critical "$1" >/dev/null 2>&1 || true
}

if [ "$TARGET" = "auto" ]; then
    MARKET_ARGS=(--auto-select-rewards)
else
    MARKET_ARGS=(--market-slug "$TARGET")
fi

echo "[supervisor] one-contract commissioning target=$TARGET"
alert "Liquidity Bot supervisor started for one-contract commissioning."

while true; do
    STARTED=$(date -u +%s)
    "$PY" -B -m liquidity_bot.live_commissioning \
        "${MARKET_ARGS[@]}" \
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
        5)
            # An unexpected loop failure that still proved the account flat.
            # A fresh process re-runs the whole flat/ownership preflight before
            # any order, so restarting is safe and keeps the session alive
            # instead of losing hours to one transient defect. The fast-crash
            # counter below still stops a genuine crash loop.
            echo "[supervisor] recoverable runtime failure after ${RAN}s; restarting"
            alert "Liquidity Bot hit a recoverable runtime failure and proved flat; restarting."
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
