import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_docs.py"
SPEC = importlib.util.spec_from_file_location("audit_docs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_docs)


class AuditDocsTests(unittest.TestCase):
    def test_repository_documentation_passes(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(audit_docs.audit_repository(root), [])

    def test_broken_local_link_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            source.write_text("[missing](other.md)\n", encoding="utf-8")

            problems = audit_docs.check_local_links(root, [source])

        self.assertEqual(problems, ["source.md:1: broken link other.md"])

    def test_line_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "STATUS.md").write_text("one\ntwo\n", encoding="utf-8")

            problems = audit_docs.check_budgets(root, {"STATUS.md": 1})

        self.assertEqual(
            problems,
            ["STATUS.md: 2 lines exceeds AI context budget 1"],
        )


if __name__ == "__main__":
    unittest.main()
