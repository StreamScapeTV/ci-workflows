from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class ReadmeTrustedPublicationTests(unittest.TestCase):
    def test_consumer_examples_follow_active_main_library_channel(self) -> None:
        source = README.read_text(encoding="utf-8")
        main_refs = re.findall(
            r"StreamScapeTV/ci-workflows/\.github/workflows/[^\s`]+@main\b",
            source,
        )
        self.assertGreaterEqual(len(main_refs), 3)
        self.assertNotIn("@<APPROVED_CI_WORKFLOWS_SHA>", source)
        self.assertIn(
            "consumer repositories call shared `ci-workflows` workflows at `@main`",
            source,
        )
        self.assertIn("ordinary shared-library consumption", source)
        self.assertNotIn("active-development/bootstrap phase", source)

    def test_public_api_guidance_matches_current_registry_model(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn("`contracts/public-workflows.json` is the machine-readable authority", source)
        self.assertIn("`deprecated-bootstrap-exception` compatibility path", source)
        self.assertNotIn("sole bootstrap public API exception", source)
        self.assertNotIn("before additional public workflows are published", source)

    def test_full_sha_is_optional_and_future_v1_is_a_drop_in_channel(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn(
            "full SHAs are optional for ordinary consumers unless a later reviewed policy explicitly requires them",
            source,
        )
        self.assertIn("future human-readable compatibility tag such as `@v1`", source)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-node.yml@v1",
            source,
        )

    def test_normal_release_example_is_tag_push_without_recovery_ceremony(self) -> None:
        source = README.read_text(encoding="utf-8")
        normal_release = source.split(
            "The normal image + Helm release caller is the product tag-push path.", 1
        )[1].split(
            "The older exact-tag compatibility workflow retains separately reviewed recovery capabilities",
            1,
        )[0]
        self.assertIn('tags:\n      - "*.*.*"', normal_release)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-native-image-chart.yml@main",
            normal_release,
        )
        self.assertIn("The product tag is release-version authority", normal_release)
        for forbidden in (
            "workflow_dispatch:",
            "release_mode: existing-tag",
            "release_source_sha:",
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@",
        ):
            self.assertNotIn(forbidden, normal_release)

    def test_legacy_existing_tag_reference_is_explicitly_not_normal_adoption(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@<compatibility-ref>",
            source,
        )
        self.assertIn("release_mode: existing-tag", source)
        self.assertIn("This is **not** the normal release skeleton", source)


if __name__ == "__main__":
    unittest.main()
