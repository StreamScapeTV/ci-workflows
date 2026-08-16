from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DOC = ROOT / "docs/workflows/python.md"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
VALIDATE_PYTHON_ACTION = "StreamScapeTV/ci-workflows/actions/validate-python"
CHECKPOINT = "aece8d01efdd5482a1c3d42db357aed87a7917e9"
RELEASE = "issue #235 general-runner Python primitives checkpoint"


class PythonDocumentationTests(unittest.TestCase):
    def test_general_runner_guidance_matches_validate_python_checkpoint(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        actions = {
            item["uses"]: item
            for item in lock["third_party_actions"]
            if item["uses"] == VALIDATE_PYTHON_ACTION
        }
        guide = PYTHON_DOC.read_text(encoding="utf-8")

        self.assertEqual({VALIDATE_PYTHON_ACTION}, set(actions))
        item = actions[VALIDATE_PYTHON_ACTION]
        self.assertEqual("composite", item["runtime"])
        self.assertEqual(CHECKPOINT, item["sha"])
        self.assertEqual(RELEASE, item["release"])
        self.assertIn(CHECKPOINT, guide)
        self.assertIn(RELEASE, guide)
        self.assertIn("runner-provided CPython 3.12", guide)
        self.assertIn("host-cpython-3.12", guide)
        self.assertIn("shared Python primitives", guide)
        self.assertIn("No Actions cache", guide)
        self.assertIn("workflow-owned persistent volume", guide)
        self.assertNotIn("verify-toolchain", guide)
        self.assertNotIn("render-evidence", guide)
        self.assertNotIn("3.12.3", guide)
        for forbidden in (
            "actions/setup-python",
            "uses: actions/setup-python",
            "sudo apt",
            "apt-get",
        ):
            self.assertNotIn(forbidden, guide)


if __name__ == "__main__":
    unittest.main()
