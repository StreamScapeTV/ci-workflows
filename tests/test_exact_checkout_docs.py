from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import source  # noqa: E402

ACTION = ROOT / "actions/exact-checkout/action.yml"
GUIDE = ROOT / "docs/architecture/source-and-trust.md"


class ExactCheckoutDocumentationTests(unittest.TestCase):
    def test_action_and_architecture_describe_non_empty_relative_destination(self) -> None:
        action = ACTION.read_text(encoding="utf-8")
        guide = GUIDE.read_text(encoding="utf-8")

        self.assertIn(
            "description: Non-empty safe relative destination beneath GITHUB_WORKSPACE",
            action,
        )
        self.assertIn("default: source", action)
        self.assertNotIn("Empty relative destination beneath GITHUB_WORKSPACE", action)

        self.assertIn(
            "a non-empty normalized relative path below `GITHUB_WORKSPACE`",
            guide,
        )
        self.assertNotIn("an empty normalized path below `GITHUB_WORKSPACE`", guide)
        self.assertNotIn("non-empty destination, or changed checkout fails closed", guide)

    def test_runtime_rejects_empty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaises(source.SourceAdmissionError) as caught:
                source.exact_checkout(
                    repository="StreamScapeTV/example",
                    admitted_sha="a" * 40,
                    path="",
                    fetch_depth=1,
                    token="",
                    workspace=workspace,
                )
        self.assertEqual(caught.exception.instruction, "invalid_checkout_path")


if __name__ == "__main__":
    unittest.main()
