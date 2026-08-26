from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID_DOC = ROOT / "docs/workflows/android.md"
REQUIRED_HELPERS = (
    "validate-android",
    "warm-gradle-dependencies",
    "upload-gradle-seed",
    "exact-checkout",
    "prepare-workspace",
    "render-evidence",
    "cleanup-workspace",
    "checkout-private-dependency",
)


class AndroidDocumentationTests(unittest.TestCase):
    def test_helper_guidance_follows_main_without_per_action_checkpoint_registry(self) -> None:
        guide = ANDROID_DOC.read_text(encoding="utf-8")
        for helper in REQUIRED_HELPERS:
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@main",
                guide,
            )
        self.assertNotRegex(
            guide,
            r"StreamScapeTV/ci-workflows/actions/[^\s`]+@[0-9a-f]{40}",
        )
        self.assertNotIn("action-tool-lock.json", guide)
        self.assertNotIn("checked-in action lock", guide)
        self.assertIn("whole-library `@main` model", guide)

    def test_cache_and_credential_behavior_remains_documented(self) -> None:
        guide = ANDROID_DOC.read_text(encoding="utf-8")
        self.assertIn("CIW_MAVEN_PACKAGE_READ_TOKEN", guide)
        self.assertIn("--no-daemon --write-verification-metadata sha256", guide)
        self.assertIn("Dependency-resolution failure blocks", guide)
        self.assertIn("cache-promotion failure does not", guide)
        self.assertIn("checkout token is confined to that step", guide)
        self.assertNotRegex(guide, re.compile(r"issue #[0-9]+ immutable .* checkpoint"))


if __name__ == "__main__":
    unittest.main()
