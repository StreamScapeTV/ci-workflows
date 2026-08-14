from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-physical-device-lock-smoke.yml"
FIXED_SYNTHETIC_DEVICE_HASH = (
    "7d054eda67532b301dded6c800d129646403f37995c4a96c723f2016b7b4e6c3"
)


class ApplePhysicalDeviceLockSmokeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)
        self.job = self.workflow["jobs"]["fence_smoke"]
        self.steps = self.job["steps"]

    def test_smoke_is_manual_input_free_and_uses_only_physical_capable_apple_selector(self) -> None:
        self.assertEqual({"workflow_dispatch"}, set(self.workflow["on"]))
        dispatch = self.workflow["on"]["workflow_dispatch"]
        self.assertIn(dispatch, (None, {}))
        self.assertEqual({"actions": "read", "contents": "read"}, self.workflow["permissions"])
        self.assertEqual(["macOS", "ARM64", "ios"], self.job["runs-on"])
        self.assertNotIn("secrets.", self.source)
        self.assertNotIn("pull_request_target", self.source)
        self.assertNotIn("repository_dispatch", self.source)

    def test_dispatch_is_protected_main_and_checkout_is_exact(self) -> None:
        guard = next(step for step in self.steps if step.get("id") == "dispatch_guard")
        self.assertIn("StreamScapeTV/ci-workflows", guard["run"])
        self.assertIn("refs/heads/main", guard["run"])

        checkout = next(
            step for step in self.steps if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertEqual("${{ github.sha }}", checkout["with"]["ref"])
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertFalse(checkout["with"]["set-safe-directory"])
        self.assertIn("git rev-parse HEAD", self.source)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.source)

    def test_backend_root_is_runner_owned_required_and_never_self_provisioned(self) -> None:
        guard = next(step for step in self.steps if step.get("id") == "backend_guard")
        self.assertIn("CIW_DEVICE_LOCK_ROOT", guard["run"])
        self.assertIn("Runner-owned device-lock backend is unavailable.", guard["run"])
        self.assertNotIn("CIW_DEVICE_LOCK_ROOT:", self.source)
        self.assertNotIn("runner.temp", self.source)
        self.assertNotIn("mktemp", self.source)
        self.assertNotIn("tempfile", self.source)
        self.assertNotRegex(self.source, r"(?m)^\s*mkdir\b")
        self.assertNotRegex(self.source, r"(?m)^\s*rmdir\b")

        action = yaml.load(
            (ROOT / "actions/device-lock/action.yml").read_text(encoding="utf-8"),
            Loader=ActionsLoader,
        )
        self.assertNotIn("backend", action["inputs"])
        self.assertNotIn("backend_root", action["inputs"])

    def test_existing_device_lock_action_runs_exact_bounded_phases_in_order(self) -> None:
        lock_steps = [
            step for step in self.steps if str(step.get("uses", "")).endswith("/actions/device-lock")
        ]
        self.assertEqual(
            ["acquire", "verify", "release", "residue"],
            [step["with"]["phase"] for step in lock_steps],
        )
        for step in lock_steps:
            with_values = step["with"]
            self.assertEqual("ios", with_values["device_family"])
            self.assertEqual("infrastructure-smoke", with_values["device_capability"])
            self.assertEqual(FIXED_SYNTHETIC_DEVICE_HASH, with_values["device_identity_hash"])
            self.assertRegex(with_values["device_identity_hash"], r"^[0-9a-f]{64}$")
            self.assertEqual("${{ github.sha }}", with_values["tested_source_sha"])
            self.assertEqual(
                "issue-216-synthetic-infrastructure-authorization",
                with_values["authorization_receipt"],
            )
            self.assertEqual(
                "issue-216-${{ github.run_id }}-${{ github.run_attempt }}",
                with_values["request_id"],
            )
            self.assertEqual("300", str(with_values["lease_seconds"]))

        verify = next(step for step in lock_steps if step["with"]["phase"] == "verify")
        release = next(step for step in lock_steps if step["with"]["phase"] == "release")
        residue = next(step for step in lock_steps if step["with"]["phase"] == "residue")
        self.assertIn("always()", verify["if"])
        self.assertIn("always()", release["if"])
        self.assertIn("always()", residue["if"])
        self.assertTrue(verify["continue-on-error"])
        self.assertTrue(release["continue-on-error"])
        self.assertTrue(residue["continue-on-error"])

    def test_cleanup_removes_only_synthetic_release_marker_after_residue(self) -> None:
        cleanup = next(step for step in self.steps if step.get("id") == "cleanup")
        self.assertEqual("${{ always() && steps.residue.outcome == 'success' }}", cleanup["if"])
        self.assertTrue(cleanup["continue-on-error"])
        run = cleanup["run"]
        self.assertIn('Path(root) / "released" / f"{resource_key_hash}.json"', run)
        self.assertIn("RESOURCE_KEY_HASH", cleanup["env"])
        self.assertNotIn("/leases/", run)
        self.assertNotIn(".transaction.lock", run)
        self.assertNotIn("rm -rf", run)
        self.assertNotIn("rmdir", run)
        self.assertNotIn("mkdir", run)

    def test_smoke_has_no_device_product_or_signing_execution_surface(self) -> None:
        lowered = self.source.casefold()
        for forbidden in (
            "xcrun devicectl",
            "xcrun simctl",
            "xcodebuild",
            "adb ",
            "codesign",
            "security find-identity",
            "provisioning",
            "install-app",
            "launch-app",
            "upload-artifact",
            "download-artifact",
            "runner.name",
            "runner.os",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertNotIn("outputs", self.job)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", FIXED_SYNTHETIC_DEVICE_HASH))

    def test_zero_artifact_and_terminal_projection_fail_closed(self) -> None:
        zero = next(step for step in self.steps if step.get("id") == "zero_artifacts")
        self.assertEqual("always()", zero["if"])
        self.assertTrue(zero["continue-on-error"])
        self.assertIn("/artifacts", zero["run"])
        self.assertIn("total_count", zero["run"])

        terminal = next(
            step
            for step in self.steps
            if step.get("name") == "Project terminal synthetic Apple fencing result"
        )
        self.assertEqual("always()", terminal["if"])
        for name in (
            "DISPATCH_GUARD_OUTCOME",
            "BACKEND_GUARD_OUTCOME",
            "ACQUIRE_OUTCOME",
            "VERIFY_OUTCOME",
            "RELEASE_OUTCOME",
            "RESIDUE_OUTCOME",
            "CLEANUP_OUTCOME",
            "ZERO_ARTIFACTS_OUTCOME",
        ):
            self.assertIn(name, terminal["env"])
            self.assertIn(f'${{{name}}}', terminal["run"])


if __name__ == "__main__":
    unittest.main()
