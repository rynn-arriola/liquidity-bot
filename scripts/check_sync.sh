#!/usr/bin/env bash
# Is the droplet running the code in this repo?
#
# Not "is HEAD equal to DEPLOYED_COMMIT" -- docs and analysis scripts are
# expected to run ahead, and forcing them level would mean restarting a healthy
# bot to ship a README fix. What must hold is narrower:
#
#   1. the droplet's src/ matches this working copy byte for byte
#   2. DEPLOYED_COMMIT honestly names a commit whose src/ is that code
#
# The dangerous state is runner code written and pushed but not deployed: the
# bot is then executing something other than what you are reading.
#
#   ./scripts/check_sync.sh [user@host]        (or set DROPLET_HOST)

set -u
cd "$(dirname "$0")/.." || exit 1

HOST="${1:-${DROPLET_HOST:-}}"
KEY="${DROPLET_KEY:-$HOME/.ssh/makerlab_droplet}"
if [ -z "$HOST" ]; then
    echo "usage: $0 user@host    (or export DROPLET_HOST=user@host)"
    exit 2
fi

SSH="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10"
REMOTE=/root/liquidity-bot

deployed=$($SSH "$HOST" "head -1 $REMOTE/DEPLOYED_COMMIT 2>/dev/null")
if [ -z "$deployed" ]; then
    echo "FAIL: could not read DEPLOYED_COMMIT (is the host reachable?)"
    exit 1
fi
head=$(git rev-parse --short HEAD)
echo "repo HEAD : $head"
echo "deployed  : $deployed"
echo

status=0

# 1. Does the droplet's source match what is checked out here?
$SSH "$HOST" "cd $REMOTE && for f in src/liquidity_bot/*.py; do \
    printf '%s  %s\n' \"\$(md5sum < \"\$f\" | cut -d' ' -f1)\" \"\$(basename \$f)\"; \
    done | sort -k2" > /tmp/.sync_remote 2>/dev/null
for f in src/liquidity_bot/*.py; do
    printf '%s  %s\n' "$(md5sum < "$f" | cut -d' ' -f1)" "$(basename "$f")"
done | sort -k2 > /tmp/.sync_local

if diff -q /tmp/.sync_local /tmp/.sync_remote >/dev/null 2>&1; then
    echo "OK   droplet src/ is byte-identical to this working copy"
else
    echo "DRIFT  droplet src/ differs from this working copy:"
    diff /tmp/.sync_local /tmp/.sync_remote | sed 's/^/       /'
    status=1
fi

# 2. Is DEPLOYED_COMMIT telling the truth about that code?
if git cat-file -e "${deployed}^{commit}" 2>/dev/null; then
    if [ -z "$(git diff --name-only "$deployed" HEAD -- src/)" ]; then
        echo "OK   no runner code has changed since $deployed"
    else
        echo "DRIFT  runner code changed since $deployed but is NOT deployed:"
        git diff --name-only "$deployed" HEAD -- src/ | sed 's/^/       /'
        echo "       the bot is running something other than what you are reading"
        status=1
    fi
else
    echo "WARN  $deployed is not a commit in this repo -- fetch, or the marker is wrong"
    status=1
fi

# Docs and scripts lagging is expected and harmless; say so rather than stay silent.
lag=$(git diff --name-only "$deployed" HEAD 2>/dev/null | grep -vc '^src/' || true)
[ "${lag:-0}" -gt 0 ] && echo "note ${lag} non-runner file(s) ahead of the droplet (docs/scripts -- harmless)"

rm -f /tmp/.sync_local /tmp/.sync_remote
exit $status
