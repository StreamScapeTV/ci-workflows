from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-physical-device-lock-smoke.yml"


class ApplePhysicalDeviceLockSmokeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.job = cls.workflow["jobs"]["contract"]

    def test_central_smoke_is_manual_and_github_hosted_only(self) -> None:
        self.assertEqual({"workflow_dispatch"}, set(self.workflow["on"]))
        self.assertIn(self.workflow["on"]["workflow_dispatch"], (None, {}))
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        self.assertEqual({"contract"}, set(self.workflow["jobs"]))
        self.assertEqual(["ubuntu-latest"], self.job["runs-on"])
        self.assertNotIn("fromJSON(needs", self.source)
        self.assertNotIn("runs-on: [macOS", self.source)
        self.assertNotIn("runs-on: [linux, amd64", self.source)

    def test_real_apple_fencing_is_not_executed_from_central(self) -> None:
        self.assertNotIn("uses: ./.ciw/actions/device-lock", self.source)
        self.assertNotIn("CIW_DEVICE_LOCK_ROOT", self.source)
        self.assertNotIn("authorization_receipt", self.source)
        self.assertNotIn("resource_lock_receipt", self.source)
        self.assertNotIn("acquire", self.source)
        self.assertNotIn("release the exact receipt", self.source)
        self.assertNotIn("xcodebuild", self.source.casefold())
        self.assertNotIn("xcrun", self.source.casefold())

    def test_hosted_check_preserves_consumer_runner_contract(self) -> None:
        step = next(
            item
            for item in self.job["steps"]
            if item["name"] == "Verify Apple and physical-device capacity remains consumer-only"
        )
        run = step["run"]
        self.assertIn('profiles["apple"]', run)
        self.assertIn('profiles["physical-device"]', run)
        self.assertIn('["macOS", "ARM64"]', run)
        self.assertIn('"validation.apple"', run)
        self.assertIn('"validation.device"', run)
        self.assertIn('physical["privilege"]["device_locked"] is True', run)

    def test_dispatch_source_is_exact_and_terminally_clean(self) -> None:
        self.assertIn('test "${GITHUB_REPOSITORY}" = "StreamScapeTV/ci-workflows"', self.source)
        self.assertIn('test "${GITHUB_REF}" = "refs/heads/main"', self.source)
        checkout = next(
            item for item in self.job["steps"] if item["name"] == "Check out exact protected source"
        )
        self.assertTrue(str(checkout["uses"]).startswith("actions/checkout@"))
        self.assertEqual("${{ github.sha }}", checkout["with"]["ref"])
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertFalse(checkout["with"]["set-safe-directory"])
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.source)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)


if __name__ == "__main__":
    unittest.main()
