#!/usr/bin/env bash
# Stop supervisor first so it cannot restart the runner, then allow the runner
# to cancel, flatten, and reconcile. SIGKILL requires an explicit --force.

set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p data
exec 9>data/host_control.lock
flock -x 9 || exit 1
# Persist operator intent before killing anything; Windows must not undo a stop.
.venv/bin/python -B -m liquidity_bot.host_recovery --lock-held disarm || exit 1

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

runner_pids() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && $0 ~ /liquidity_bot\.live_commissioning/ {print $1}'
}
supervisor_pids() {
    ps -eo pid=,args= | awk '/[s]upervise_commissioning\.sh/ {print $1}'
}

SUPERVISORS=$(supervisor_pids)
RUNNERS=$(runner_pids)
if [ -n "$SUPERVISORS" ]; then
    # shellcheck disable=SC2086
    kill $SUPERVISORS 2>/dev/null || true
    echo "stopped supervisor: $SUPERVISORS"
fi
if [ -n "$RUNNERS" ]; then
    # shellcheck disable=SC2086
    if ! kill $RUNNERS 2>/dev/null; then
        echo "FAILED to signal runner: $RUNNERS"
        exit 1
    fi
    echo "sent SIGTERM to runner: $RUNNERS"
fi

WAITED=0
flock -u 9
exec 9>&-
GRACE=${COMMISSIONING_STOP_GRACE_SECONDS:-300}
while [ -n "$(runner_pids)" ] && [ "$WAITED" -lt "$GRACE" ]; do
    sleep 2
    WAITED=$((WAITED + 2))
    if [ $((WAITED % 30)) -eq 0 ]; then
        echo "runner is still canceling/flattening/reconciling (${WAITED}s/${GRACE}s)"
    fi
done

LEFT=$(runner_pids)
if [ -n "$LEFT" ]; then
    if [ "$FORCE" -eq 1 ]; then
        echo "WARNING: force-killing $LEFT; exchange orders may remain"
        # shellcheck disable=SC2086
        kill -9 $LEFT
    else
        echo "runner is still reconciling after ${GRACE}s; not force-killing"
        echo "inspect logs/commissioning.log and the exchange before --force"
        exit 1
    fi
fi
echo "liquidity commissioning stopped"
