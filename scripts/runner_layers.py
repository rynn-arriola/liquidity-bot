#!/usr/bin/env python3
"""Print which runner methods are overridden in which layer (read-only AST).

The live trader is CommissioningRunner (plus its runner_*.py mixins), then
RestFallbackRunner, then AccountSourceRunner. A method overridden in both
subclasses runs three layers deep; tracing it means reading every layer.

    python -B scripts/runner_layers.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "liquidity_bot"
LAYERS = (
    ("RestFallbackRunner", "rest_fallback.py"),
    ("AccountSourceRunner", "account_source_runner.py"),
)


def class_methods(path: Path, class_name: str) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {item.name: item.lineno for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
    raise LookupError(f"{class_name} not found in {path.name}")


def base_homes(package: Path) -> dict[str, str]:
    homes = {name: f"live_commissioning.py:{line}" for name, line in class_methods(
        package / "live_commissioning.py", "CommissioningRunner").items()}
    for path in sorted(package.glob("runner_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        homes.setdefault(item.name, f"{path.name}:{item.lineno}")
    return homes


def layer_rows(package: Path = PACKAGE) -> list[tuple[str, str, str, str]]:
    """(method, base home, RestFallbackRunner line, AccountSourceRunner line)."""
    homes = base_homes(package)
    rest, account = (class_methods(package / file, name) for name, file in LAYERS)
    return [(name, homes.get(name, "-"),
             f"rest_fallback.py:{rest[name]}" if name in rest else "-",
             f"account_source_runner.py:{account[name]}" if name in account else "-")
            for name in sorted(set(rest) | set(account))]


def main() -> int:
    rows = layer_rows()
    deep = [row for row in rows if row[2] != "-" and row[3] != "-"]
    print(f"{len(rows)} methods overridden below CommissioningRunner; "
          f"{len(deep)} in both subclasses (three layers):")
    for name, home, rest, account in deep:
        print(f"  {name:40} base={home:34} {rest:28} {account}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
