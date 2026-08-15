from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID_DOC = ROOT / "docs/workflows/android.md"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
VALIDATE_ANDROID_ACTION = "StreamScapeTV/ci-workflows/actions/validate-android"
EXACT_CHECKOUT_ACTION = "StreamScapeTV/ci-workflows/actions/exact-checkout"
PRIVATE_DEPENDENCY_ACTION = (
    "StreamScapeTV/ci-workflows/actions/checkout-private-dependency"
)
OBSOLETE_GUIDANCE = (
    "central composite actions directly at immutable revision "
    "`70e08d4ddf8930046632a7135950e924b82e22bf`"
)


class AndroidDocumentationTests(unittest.TestCase):
    def test_immutable_helper_guidance_matches_action_lock(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        required = {
            VALIDATE_ANDROID_ACTION,
            EXACT_CHECKOUT_ACTION,
            PRIVATE_DEPENDENCY_ACTION,
        }
        actions = {
            item["uses"]: item
            for item in lock["third_party_actions"]
            if item["uses"] in required
        }
        guide = ANDROID_DOC.read_text(encoding="utf-8")

        self.assertEqual(required, set(actions))
        for item in actions.values():
            self.assertEqual("composite", item["runtime"])
            self.assertIn(item["sha"], guide)
            self.assertIn(item["release"], guide)
        self.assertNotIn(OBSOLETE_GUIDANCE, guide)


if __name__ == "__main__":
    unittest.main()
