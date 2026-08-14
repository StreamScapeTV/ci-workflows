from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class ReadmeTrustedPublicationTests(unittest.TestCase):
    def test_trusted_workflow_examples_do_not_follow_mutable_main(self) -> None:
        source = README.read_text(encoding="utf-8")
        mutable = re.findall(
            r"StreamScapeTV/ci-workflows/\.github/workflows/[^\s`]+@main\b",
            source,
        )
        self.assertEqual(mutable, [])
        self.assertGreaterEqual(source.count("@<APPROVED_CI_WORKFLOWS_SHA>"), 2)

    def test_public_api_guidance_matches_current_registry_model(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn("`contracts/public-workflows.json` is the machine-readable authority", source)
        self.assertIn("`deprecated-bootstrap-exception` compatibility path", source)
        self.assertNotIn("sole bootstrap public API exception", source)
        self.assertNotIn("before additional public workflows are published", source)

    def test_unpublished_stable_tag_is_explicitly_illustrative(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn("illustrative only until that tag is published", source)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@v1.0.0",
            source,
        )


if __name__ == "__main__":
    unittest.main()
