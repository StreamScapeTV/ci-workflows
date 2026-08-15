from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = ROOT / "CONTRIBUTING.md"


class ContributingLifecycleDocumentationTests(unittest.TestCase):
    def test_validation_guidance_distinguishes_head_and_disjoint_base_movement(self) -> None:
        source = CONTRIBUTING.read_text(encoding="utf-8")
        self.assertNotIn("A changed head or base invalidates older evidence.", source)
        self.assertIn(
            "A changed pull-request head invalidates exact-head evidence",
            source,
        )
        self.assertIn(
            "Integration-branch movement does not by itself invalidate a clean candidate",
            source,
        )
        self.assertIn("refresh the complete current-base self-review", source)

    def test_merge_guidance_uses_expected_head_without_rewriting_history(self) -> None:
        source = CONTRIBUTING.read_text(encoding="utf-8")
        self.assertNotIn("normally by squash", source)
        self.assertIn("expected-head protection", source)
        self.assertIn("repository-approved merge method", source)
        self.assertIn("Do not force-push published work", source)
        self.assertIn(
            "Do not rewrite published history merely to make a clean candidate match a moving base",
            source,
        )


if __name__ == "__main__":
    unittest.main()
