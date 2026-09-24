#!/usr/bin/env python3
"""Run unittest files in isolated parallel workers with compact diagnostics."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
RAN_RE = re.compile(r"Ran (\d+) tests? in ([0-9.]+)s")


@dataclass(frozen=True)
class Result:
    file: str
    returncode: int
    count: int
    test_seconds: float | None
    wall_seconds: float
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.count > 0


def discover(patterns: list[str]) -> list[str]:
    files = sorted(path.name for path in TESTS.glob("test_*.py"))
    if not patterns:
        return files
    return [
        file for file in files
        if any(fnmatch.fnmatch(file, pattern) for pattern in patterns)
    ]


def parse_summary(output: str) -> tuple[int, float | None]:
    matches = list(RAN_RE.finditer(output))
    if not matches:
        return 0, None
    match = matches[-1]
    return int(match.group(1)), float(match.group(2))


def run_file(file: str) -> Result:
    env = os.environ.copy()
    paths = [str(ROOT / "src"), str(TESTS)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    command = [
        sys.executable,
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        file,
        "-v",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    wall = time.perf_counter() - started
    count, test_seconds = parse_summary(completed.stdout)
    return Result(
        file=file,
        returncode=completed.returncode,
        count=count,
        test_seconds=test_seconds,
        wall_seconds=wall,
        output=completed.stdout,
    )


def default_workers() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run unittest files in parallel isolated processes."
    )
    parser.add_argument(
        "patterns",
        nargs="*",
        help="optional filename globs such as test_rest_*.py",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=default_workers(),
        help="parallel processes (default: up to 4; use 1 for serial isolation)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list selected test files without running them",
    )
    parser.add_argument(
        "--show-passing-output",
        action="store_true",
        help="print verbose unittest output for passing files too",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be at least 1")

    files = discover(args.patterns)
    if not files:
        parser.error("no test files matched")
    if args.list:
        print("\n".join(files))
        return 0

    started = time.perf_counter()
    results: list[Result] = []
    failures: list[Result] = []
    print(f"Running {len(files)} test files with {args.workers} worker(s)")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(run_file, file): file for file in files}
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            label = "PASS" if result.ok else "FAIL"
            print(
                f"[{label}] {result.file}: {result.count} tests, "
                f"{result.wall_seconds:.2f}s",
                flush=True,
            )
            if not result.ok:
                failures.append(result)
                if result.returncode == 0:
                    print("No tests were discovered; treating this file as failed.")
                print(result.output, end="" if result.output.endswith("\n") else "\n")
            elif args.show_passing_output:
                print(result.output, end="" if result.output.endswith("\n") else "\n")

    elapsed = time.perf_counter() - started
    total = sum(result.count for result in results)
    slowest = sorted(results, key=lambda result: result.wall_seconds, reverse=True)[:10]
    print(f"\nRan {total} tests from {len(results)} files in {elapsed:.2f}s wall time")
    print("Slowest files:")
    for result in slowest:
        print(f"  {result.wall_seconds:8.2f}s  {result.file}")
    if failures:
        print(f"FAILED: {len(failures)} file(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
