from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs" / "architecture" / "flutter-validation.md"
REUSABLE = ROOT / ".github" / "workflows" / "reusable-flutter.yml"


class FlutterArchitectureDocsTests(unittest.TestCase):
    def test_guide_describes_main_library_reuse_without_central_checkout(self) -> None:
        source = ARCHITECTURE.read_text(encoding="utf-8")
        lowered = source.lower()

        self.assertNotIn("central checkout", lowered)
        self.assertIn("does\nnot clone or check out central repository source", source)
        self.assertIn("current Central library through `@main`", source)
        self.assertIn("consumer source is checked out separately through the", source)
        self.assertIn("`exact-checkout@main` helper and reverified before execution", source)
        self.assertNotIn("full-SHA action pins", source)
        self.assertNotIn("immutable private-action checkpoint", source)

    def test_reusable_workflow_has_no_retired_central_clone_pattern(self) -> None:
        source = REUSABLE.read_text(encoding="utf-8")

        self.assertNotIn("repository: ${{ job.workflow_repository }}", source)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", source)
        self.assertNotIn("path: .ciw", source)
        self.assertNotIn("uses: actions/checkout@", source)

        expected_helpers = (
            "validate-flutter",
            "exact-checkout",
            "prepare-workspace",
            "cleanup-workspace",
        )
        for helper in expected_helpers:
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@main",
                source,
            )


if __name__ == "__main__":
    unittest.main()
