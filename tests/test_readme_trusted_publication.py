from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class ReadmeTrustedPublicationTests(unittest.TestCase):
    def test_trusted_workflow_examples_follow_active_main_channel(self) -> None:
        source = README.read_text(encoding="utf-8")
        main_refs = re.findall(
            r"StreamScapeTV/ci-workflows/\.github/workflows/[^\s`]+@main\b",
            source,
        )
        self.assertGreaterEqual(len(main_refs), 2)
        self.assertNotIn("@<APPROVED_CI_WORKFLOWS_SHA>", source)
        self.assertIn(
            "all consumer repositories should reference shared `ci-workflows` workflows at `@main`",
            source,
        )

    def test_public_api_guidance_matches_current_registry_model(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn("`contracts/public-workflows.json` is the machine-readable authority", source)
        self.assertIn("`deprecated-bootstrap-exception` compatibility path", source)
        self.assertNotIn("sole bootstrap public API exception", source)
        self.assertNotIn("before additional public workflows are published", source)

    def test_immutable_references_are_supported_but_not_currently_required(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertNotIn(
            "Trusted publication and release callers must pin an approved immutable 40-character",
            source,
        )
        self.assertIn(
            "not the required/default consumer channel during this rapid-development phase",
            source,
        )
        self.assertIn("illustrative only until that decision is made", source)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@v1.0.0",
            source,
        )


if __name__ == "__main__":
    unittest.main()
