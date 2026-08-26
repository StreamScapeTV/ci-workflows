from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLE_DOC = ROOT / "docs/workflows/apple.md"
APPLE_ACTION = "StreamScapeTV/ci-workflows/actions/validate-apple"


class AppleDocumentationTests(unittest.TestCase):
    def test_helper_guidance_uses_whole_library_main_reference(self) -> None:
        guide = APPLE_DOC.read_text(encoding="utf-8")
        self.assertIn(f"{APPLE_ACTION}@main", guide)
        self.assertIn(
            "StreamScapeTV/ci-workflows/actions/github-app-repository-token@main",
            guide,
        )
        self.assertIn(
            "StreamScapeTV/ci-workflows/actions/materialize-private-release-asset@main",
            guide,
        )
        self.assertNotIn("action-tool-lock.json", guide)
        self.assertNotIn("immutable helper checkpoint", guide)
        self.assertNotRegex(
            guide,
            r"StreamScapeTV/ci-workflows/actions/[^\s`;]+@[0-9a-f]{40}",
        )

    def test_private_source_and_diagnostic_confidentiality_remains_documented(self) -> None:
        guide = APPLE_DOC.read_text(encoding="utf-8")
        self.assertIn("transient contents-read", guide)
        self.assertIn("masks the issued token", guide)
        self.assertIn("credentials", guide)
        self.assertIn("raw build logs", guide)
        self.assertIn("not be exposed through public Actions artifacts", guide)
        self.assertIn("bounded redacted diagnostic", guide)


if __name__ == "__main__":
    unittest.main()
