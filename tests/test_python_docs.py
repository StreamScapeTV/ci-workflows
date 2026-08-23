from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DOC = ROOT / "docs/workflows/python.md"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
VALIDATE_PYTHON_ACTION = "StreamScapeTV/ci-workflows/actions/validate-python"
CHECKPOINT = "203aaf1efcf28ff5c99a402301718f22e20ecb58"
RELEASE = "issue #473 product-neutral Python checkpoint"


class PythonDocumentationTests(unittest.TestCase):
    def test_guidance_matches_validate_python_product_neutral_checkpoint(self) -> None:
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
        self.assertIn("consumer-owned", guide.casefold())
        self.assertIn("CIW_POSTGRES_URL", guide)
        self.assertIn("dependency_file", guide)
        self.assertIn("script_path", guide)
        self.assertIn("No Actions cache", guide)
        self.assertNotIn("command_profile", guide)
        self.assertNotIn("verify-toolchain", guide)
        self.assertNotIn("render-evidence", guide)
        for forbidden in (
            "actions/setup-python",
            "uses: actions/setup-python",
            "sudo apt",
            "apt-get",
            "arguments_json",
            "environment_json",
        ):
            self.assertNotIn(forbidden, guide)


if __name__ == "__main__":
    unittest.main()
