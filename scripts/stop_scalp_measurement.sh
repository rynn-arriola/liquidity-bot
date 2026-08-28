#!/usr/bin/env bash
# Stop only the observation process; never signal the trading bot.

set -u
cd "$(dirname "$0")/.." || exit 1

collector_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.scalp_run/ {print $1}'
}
supervisor_pids() {
    ps -eo pid=,args= | awk '/[s]upervise_scalp_measurement\.sh/ {print $1}'
}

supervisors=$(supervisor_pids)
collectors=$(collector_pids)
if [ -n "$supervisors" ]; then
    # shellcheck disable=SC2086
    kill $supervisors 2>/dev/null || true
fi
if [ -n "$collectors" ]; then
    # SIGINT lets asyncio flush and censor the final open measurement window.
    # shellcheck disable=SC2086
    kill -INT $collectors 2>/dev/null || true
fi
for _ in $(seq 1 20); do
    [ -z "$(collector_pids)" ] && break
    sleep 1
done
if [ -n "$(collector_pids)" ]; then
    echo "collector is still flushing; not force-killing"
    exit 1
fi
echo "scalp measurement stopped; liquidity bot was not signaled"
