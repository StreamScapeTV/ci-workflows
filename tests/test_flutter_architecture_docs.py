from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs" / "architecture" / "flutter-validation.md"
REUSABLE = ROOT / ".github" / "workflows" / "reusable-flutter.yml"


class FlutterArchitectureDocsTests(unittest.TestCase):
    def test_guide_describes_private_action_reuse_without_central_checkout(self) -> None:
        source = ARCHITECTURE.read_text(encoding="utf-8")
        lowered = source.lower()

        self.assertNotIn("central checkout", lowered)
        self.assertIn("does\nnot clone or check out central repository source", source)
        self.assertIn("central composite actions through exact full-SHA references", source)
        self.assertIn("consumer source is checked out separately through the immutable", source)
        self.assertIn("`exact-checkout` helper and reverified before execution", source)

    def test_reusable_workflow_has_no_retired_central_clone_pattern(self) -> None:
        source = REUSABLE.read_text(encoding="utf-8")

        self.assertNotIn("repository: ${{ job.workflow_repository }}", source)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", source)
        self.assertNotIn("path: .ciw", source)
        self.assertNotIn("uses: actions/checkout@", source)

        helper_refs = re.findall(
            r"uses:\s+StreamScapeTV/ci-workflows/actions/[^\s@]+@([0-9a-f]{40})",
            source,
        )
        self.assertGreaterEqual(len(helper_refs), 4)
        self.assertIn("StreamScapeTV/ci-workflows/actions/exact-checkout@", source)


if __name__ == "__main__":
    unittest.main()
