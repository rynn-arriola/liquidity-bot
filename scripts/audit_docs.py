#!/usr/bin/env python3
"""Audit the AI-facing Markdown architecture without changing any files."""

from __future__ import annotations

import re
from pathlib import Path
import sys
from typing import Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]

LINE_BUDGETS: Mapping[str, int] = {
    "AGENTS.md": 30,
    "CLAUDE.md": 30,
    "AI_OPERATOR.md": 220,
    "AI_ENGINEERING.md": 180,
    "STATUS.md": 80,
    "DECISIONS.md": 120,
    "NEXT.md": 120,
    "WATCHLIST.md": 120,
    "FINDINGS.md": 100,
    "BUGFIXES.md": 100,
}

REQUIRED_ROUTES: Mapping[str, tuple[str, ...]] = {
    "AGENTS.md": ("AI_OPERATOR.md",),
    "CLAUDE.md": ("AI_OPERATOR.md",),
    "AI_OPERATOR.md": (
        "STATUS.md",
        "DECISIONS.md",
        "AI_ENGINEERING.md",
        "NEXT.md",
        "WATCHLIST.md",
        "FINDINGS.md",
        "BUGFIXES.md",
        "docs/history/",
    ),
}

FORBIDDEN_CURRENT_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "AI_ENGINEERING.md": (
        "Historical status log",
        "DEPLOYED AND ACTIVE",
        "PUSHED, DEPLOYED",
    ),
    "NEXT.md": (
        "## DEPLOYED",
        "## FIXED",
        "## HISTORICAL",
        "## SUPERSEDED",
    ),
}

LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if ".git" not in path.parts
    )


def check_local_links(root: Path, files: Iterable[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1).strip().strip("<>")
            if (
                not target
                or target.startswith(("http://", "https://", "mailto:", "#"))
                or any(character in target for character in "<>*")
            ):
                continue
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            destination = (path.parent / file_part).resolve()
            if not destination.exists():
                line = text.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{path.relative_to(root)}:{line}: broken link {target}"
                )
    return problems


def check_budgets(root: Path, budgets: Mapping[str, int]) -> list[str]:
    problems: list[str] = []
    for relative, maximum in budgets.items():
        path = root / relative
        if not path.is_file():
            problems.append(f"missing canonical file: {relative}")
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > maximum:
            problems.append(
                f"{relative}: {lines} lines exceeds AI context budget {maximum}"
            )
    return problems


def check_routes(root: Path) -> list[str]:
    problems: list[str] = []
    for relative, required in REQUIRED_ROUTES.items():
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for route in required:
            if route not in text:
                problems.append(f"{relative}: missing required route {route}")
    return problems


def check_current_files(root: Path) -> list[str]:
    problems: list[str] = []
    for relative, patterns in FORBIDDEN_CURRENT_PATTERNS.items():
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern.casefold() in text.casefold():
                problems.append(
                    f"{relative}: historical status pattern leaked into current file: {pattern}"
                )
    status = root / "STATUS.md"
    if status.is_file():
        text = status.read_text(encoding="utf-8")
        if "Only current operational facts belong here" not in text:
            problems.append("STATUS.md: missing current-only ownership banner")
    return problems


def audit_repository(root: Path) -> list[str]:
    files = markdown_files(root)
    problems: list[str] = []
    problems.extend(check_local_links(root, files))
    problems.extend(check_budgets(root, LINE_BUDGETS))
    problems.extend(check_routes(root))
    problems.extend(check_current_files(root))
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    files = markdown_files(REPO_ROOT)
    mandatory_lines = sum(
        len((REPO_ROOT / path).read_text(encoding="utf-8").splitlines())
        for path in LINE_BUDGETS
        if (REPO_ROOT / path).is_file()
    )
    problems = audit_repository(REPO_ROOT)
    print("Liquidity bot AI documentation audit")
    print(f"  markdown files  : {len(files)}")
    print(f"  mandatory lines : {mandatory_lines}")
    print(f"  broken/rule issues: {len(problems)}")
    if problems:
        print("\nVERDICT: DOCUMENTATION DRIFT")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nVERDICT: DOCUMENTATION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
