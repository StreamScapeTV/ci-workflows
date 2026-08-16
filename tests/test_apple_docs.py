from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLE_DOC = ROOT / "docs/workflows/apple.md"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
APPLE_ACTION = "StreamScapeTV/ci-workflows/actions/validate-apple"
OBSOLETE_RELEASE_SHA = "88d179740145ccea00b6986d78ceb67ea365face"


class AppleDocumentationTests(unittest.TestCase):
    def test_immutable_helper_guidance_matches_action_lock(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        apple = next(
            item
            for item in lock["third_party_actions"]
            if item["uses"] == APPLE_ACTION
        )
        guide = APPLE_DOC.read_text(encoding="utf-8")

        self.assertEqual("composite", apple["runtime"])
        self.assertIn(apple["sha"], guide)
        self.assertIn(apple["release"], guide)
        self.assertNotIn(OBSOLETE_RELEASE_SHA, guide)


if __name__ == "__main__":
    unittest.main()
