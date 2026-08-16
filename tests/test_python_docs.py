from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DOC = ROOT / "docs/workflows/python.md"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
VALIDATE_PYTHON_ACTION = "StreamScapeTV/ci-workflows/actions/validate-python"
VERIFY_TOOLCHAIN_ACTION = "StreamScapeTV/ci-workflows/actions/verify-toolchain"
OBSOLETE_GUIDANCE = "`validate-python` and `verify-toolchain` use the reviewed issue #125 checkpoint"


class PythonDocumentationTests(unittest.TestCase):
    def test_immutable_helper_guidance_matches_action_lock(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        actions = {
            item["uses"]: item
            for item in lock["third_party_actions"]
            if item["uses"] in {VALIDATE_PYTHON_ACTION, VERIFY_TOOLCHAIN_ACTION}
        }
        guide = PYTHON_DOC.read_text(encoding="utf-8")

        self.assertEqual(
            {VALIDATE_PYTHON_ACTION, VERIFY_TOOLCHAIN_ACTION}, set(actions)
        )
        for item in actions.values():
            self.assertEqual("composite", item["runtime"])
            self.assertIn(item["sha"], guide)
            self.assertIn(item["release"], guide)
        self.assertNotIn(OBSOLETE_GUIDANCE, guide)


if __name__ == "__main__":
    unittest.main()
