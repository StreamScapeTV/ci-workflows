from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/architecture/source-and-trust.md"


class SourceTrustDocumentationTests(unittest.TestCase):
    def test_public_reusable_workflow_example_follows_active_main_channel(self) -> None:
        source = GUIDE.read_text(encoding="utf-8")
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-resolve-source.yml@main",
            source,
        )
        self.assertNotIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-resolve-source.yml@<immutable-reference>",
            source,
        )
        self.assertIn(
            "they are not preferred or required over `@main` for privileged consumers",
            source,
        )

    def test_private_helper_distribution_remains_immutable(self) -> None:
        source = GUIDE.read_text(encoding="utf-8")
        helper_section = source.split(
            "## Immutable private reusable-helper distribution", 1
        )[1].split("## Consumer patterns", 1)[0]
        self.assertIn("exact full-SHA references", helper_section)
        self.assertIn("immutable private action archive", helper_section)
        self.assertNotIn("@main", helper_section)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/actions/exact-checkout@<immutable-helper-sha>",
            source,
        )


if __name__ == "__main__":
    unittest.main()
