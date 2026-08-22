#!/usr/bin/env bash
# One dated snapshot of "what can this bot actually quote right now".
#
# Runs the selection funnel and the reward-period mix into a single timestamped
# file, so a question raised on one day can be answered from another day's data
# instead of being re-derived. Written 2026-08-20 for two open questions:
#
#   WATCHLIST 9  -- do the tight-book rejects cluster at 1c on a BUSY slate?
#                   Sample 1 taken 08-20T14:12Z; needs a second, another day.
#   FINDINGS     -- the venue moved every league we earn in to live-only. Is
#                   that permanent? One reading cannot tell a program change
#                   from a transient.
#   FINDINGS     -- is trading loss concentrated in FAST pairs or slow ones?
#                   Fast means a book-stability problem and points at
#                   selection; slow would point at the inventory clock. The
#                   answer moved once already when a ledger defect was
#                   mistaken for a trading one, so it wants a series.
#
# Costs ~50 venue calls through the same 4 req/s governor the runner uses, so
# it is scheduled once a day and never looped. Safe while the bot quotes: both
# probes are read-only and place no orders.
#
# TEMPORARY. Remove the crontab entry once both questions are closed -- see
# NEXT.md. Nothing here expires on its own.

set -u
cd /root/liquidity-bot || exit 1

STAMP="$(date -u +%Y-%m-%dT%H%MZ)"
OUT="logs/probe-${STAMP}.log"
mkdir -p logs

{
    echo "########## scheduled probe ${STAMP} ##########"
    echo
    echo "===== reward period mix by league ====="
    PYTHONPATH=src .venv/bin/python -B scripts/period_mix.py 2>&1
    echo
    echo "===== selection funnel and rejects ====="
    PYTHONPATH=src .venv/bin/python -B scripts/usable_markets.py --rejects 2>&1
    echo
    echo "===== pair P&L by completion time ====="
    # Ledger only, no venue calls. Tracks whether the loss sits in fast pairs
    # (a book-stability problem) or slow ones (a clock or exit-cap problem).
    # On 2026-08-22 it was 40% in the 0-300s bucket, which points at selection.
    PYTHONPATH=src .venv/bin/python -B scripts/pair_pnl.py 2>&1
} > "${OUT}" 2>&1

echo "wrote ${OUT}"
