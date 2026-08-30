from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ApiArchitectureTests(unittest.TestCase):
    def test_executable_gateways_cannot_omit_a_limiter(self):
        """Prevent a new tool from silently creating an independent budget."""

        violations: list[str] = []
        for folder in (ROOT / "scripts", ROOT / "src" / "liquidity_bot"):
            for path in sorted(folder.glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), path.as_posix())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = (
                        node.func.id
                        if isinstance(node.func, ast.Name)
                        else node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else ""
                    )
                    if name != "AsyncReadOnlyGateway":
                        continue
                    if not any(item.arg == "limiter" for item in node.keywords):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            violations, [],
            "production AsyncReadOnlyGateway must receive an explicit limiter")


if __name__ == "__main__":
    unittest.main()
