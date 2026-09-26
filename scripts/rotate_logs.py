#!/usr/bin/env python3
"""Weekly compressed rotation of logs/commissioning.log, without root.

The runner appends with ``>>`` (O_APPEND), so ``copytruncate`` is safe: after
the truncate the next write lands at offset 0. Lines written during the copy
window (about a second) can be lost. Rotated logs are dated, kept compressed
and never removed in practice (rotate 520 = ten years of weeks), because they
are evidence for docs/history.

Run from the checkout whose log should rotate; cron runs it daily and
logrotate itself decides when a week has passed:

    .venv/bin/python -B scripts/rotate_logs.py                 # rotate if due
    .venv/bin/python -B scripts/rotate_logs.py --force         # rotate now
    .venv/bin/python -B scripts/rotate_logs.py --install-cron  # idempotent
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

LOGS = ("logs/commissioning.log",)
STATE = "data/logrotate.state"
CRON_MARK = "# liquidity-bot log rotation"


def config_text(root: Path) -> str:
    paths = " ".join(str(root / log) for log in LOGS)
    return f"""{paths} {{
    weekly
    rotate 520
    compress
    delaycompress
    copytruncate
    dateext
    dateformat -%Y%m%d
    missingok
    notifempty
}}
"""


def cron_line(root: Path) -> str:
    return (f"17 3 * * * cd {root} && nice -n 10 ionice -c3 .venv/bin/python -B "
            f"scripts/rotate_logs.py >> logs/rotate.log 2>&1 {CRON_MARK}")


def with_cron_line(existing: str, line: str) -> str:
    kept = [row for row in existing.splitlines() if CRON_MARK not in row]
    return "\n".join(kept + [line]) + "\n"


def rotate(root: Path, *, force: bool = False, logrotate: str = "logrotate") -> int:
    (root / "data").mkdir(exist_ok=True)
    fd, conf = tempfile.mkstemp(prefix="logrotate-", suffix=".conf", dir=root / "data")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(config_text(root))
        args = [logrotate, "-s", str(root / STATE)] + (["-f"] if force else []) + [conf]
        return subprocess.run(args).returncode
    finally:
        os.unlink(conf)


def install_cron(root: Path) -> int:
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = current.stdout if current.returncode == 0 else ""
    result = subprocess.run(["crontab", "-"], input=with_cron_line(existing, cron_line(root)),
                            text=True)
    return result.returncode


def main(argv: list[str]) -> int:
    root = Path.cwd().resolve()
    if not (root / "logs").is_dir():
        print(f"refused: {root} has no logs/ directory", file=sys.stderr)
        return 2
    if "--install-cron" in argv:
        return install_cron(root)
    return rotate(root, force="--force" in argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
