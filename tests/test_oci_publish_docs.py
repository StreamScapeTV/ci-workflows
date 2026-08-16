from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/workflows/oci-publish.md"


class OciPublishDocumentationTests(unittest.TestCase):
    def test_publication_guide_describes_merged_architecture(self) -> None:
        text = DOC.read_text(encoding="utf-8")

        for stale in (
            "issue-#17-exclusive",
            "After #16 integrates",
            "Until that handoff",
            "issue-#16 runtime are available",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, text)

        self.assertIn("The merged publication layer has no caller destination input.", text)
        self.assertIn("the merged `oci.build` layer", text)
        self.assertIn("issue #154", text)
        self.assertIn("does not by itself activate any producer", text)


if __name__ == "__main__":
    unittest.main()
