#!/usr/bin/env bash
# Restart only after an unexpected process death. Safety refusals/non-flat or
# repeated crashes remain in attention mode, without relaunching trading.

set -u
cd "$(dirname "$0")/.." || exit 1

TARGET=${1:-auto}
QUOTE_SIZE=${2:-5}
SLOT_CAPACITY=${3:-20}
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "[supervisor] refused: $PY is missing"
    exit 2
fi
if ! QUOTE_SIZE=$("$PY" -B -m liquidity_bot.quote_size "$QUOTE_SIZE"); then
    echo "[supervisor] refused invalid quote size"
    exit 2
fi
if ! SLOT_CAPACITY=$("$PY" -B -m liquidity_bot.slot_capacity "$SLOT_CAPACITY"); then
    echo "[supervisor] refused invalid slot capacity"
    exit 2
fi
MAX_FAST_CRASHES=3
FAST_CRASH_SECONDS=120
FAST=0
BACKOFF=10

alert() {
    "$PY" -m liquidity_bot.supervisor_alert --critical "$1" >/dev/null 2>&1 || true
}

attention() {
    # Stay observable without repeatedly relaunching a deterministic defect or
    # touching unknown venue exposure. stop_commissioning.sh still ends this
    # supervisor normally; no child trader is alive in this loop.
    echo "[supervisor] state=ATTENTION_REQUIRED reason=$1 trading=stopped"
    alert "Liquidity Bot needs attention: $1. Trading is stopped; automatic relaunch is blocked."
    while true; do
        sleep 60
        echo "[supervisor] state=ATTENTION_REQUIRED reason=$1 trading=stopped"
    done
}

if [ "$TARGET" = "auto" ]; then
    MARKET_ARGS=(--auto-select-rewards)
else
    MARKET_ARGS=(--market-slug "$TARGET")
fi

echo "[supervisor] liquidity commissioning target=$TARGET size=$QUOTE_SIZE slots=$SLOT_CAPACITY"
alert "Liquidity Bot supervisor started at $QUOTE_SIZE contracts per quote across up to $SLOT_CAPACITY markets."

while true; do
    STARTED=$(date -u +%s)
    # -u matters: stdout to a file is block-buffered, so during the
    # 2026-08-14 incident the log was four minutes stale while the process was
    # actively failing. Only the prints that pass flush=True got through, and
    # the diagnosis had to come from the ledger instead.
    "$PY" -B -u -m liquidity_bot.live_commissioning \
        "${MARKET_ARGS[@]}" \
        --database data/commissioning.db \
        --lock-file data/commissioning.lock \
        --arm-live \
        --accept-one-contract-risk \
        --exclusive-api-key \
        --maker-lab-stopped \
        --quote-size "$QUOTE_SIZE" \
        --max-markets "$SLOT_CAPACITY" >> logs/commissioning.log 2>&1
    CODE=$?
    RAN=$(( $(date -u +%s) - STARTED ))

    case "$CODE" in
        0)
            echo "[supervisor] clean exit after ${RAN}s; not restarting"
            exit 0
            ;;
        2)
            echo "[supervisor] startup refused; not restarting"
            attention "startup refused"
            ;;
        3)
            echo "[supervisor] process exited without proving flat; not restarting"
            attention "account flatness not proven; check venue exposure"
            ;;
        4)
            echo "[supervisor] caught runtime/strategy failure; not restarting"
            attention "caught runtime/strategy integrity failure"
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
        attention "${FAST} fast process deaths"
    fi

    echo "[supervisor] unexpected exit $CODE after ${RAN}s; restart in ${BACKOFF}s"
    alert "Liquidity Bot process died unexpectedly (exit ${CODE}); recovery restart in ${BACKOFF}s."
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
    [ "$BACKOFF" -gt 60 ] && BACKOFF=60
done
