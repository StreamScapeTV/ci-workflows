from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-device-lock-backend-smoke.yml"
ACTION = ROOT / "actions/device-lock/action.yml"


class AppleDeviceLockBackendSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_smoke_is_pr_only_minimum_permission_and_exact_source(self) -> None:
        self.assertEqual({"pull_request"}, set(self.workflow["on"]))
        self.assertEqual({"actions": "read", "contents": "read"}, self.workflow["permissions"])
        self.assertIn("github.event.pull_request.head.sha", self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertNotIn("pull_request_target", self.source)
        self.assertNotIn("workflow_dispatch", self.source)
        self.assertNotIn("secrets.", self.source)

    def test_physical_capable_apple_selector_is_explicit(self) -> None:
        job = self.workflow["jobs"]["backend_smoke"]
        self.assertEqual(["macOS", "ARM64", "ios"], job["runs-on"])
        self.assertEqual(
            "${{ github.event.pull_request.head.repo.full_name == github.repository }}",
            job["if"],
        )

    def test_backend_is_runner_owned_and_never_caller_selected(self) -> None:
        self.assertNotIn("CIW_DEVICE_LOCK_ROOT:", self.source)
        self.assertNotIn("runner.temp", self.source)
        self.assertNotIn("mkdir", self.source)
        self.assertNotIn("rm -rf", self.source)
        self.assertIn('os.environ.get("CIW_DEVICE_LOCK_ROOT", "")', self.source)
        self.assertIn("stat.S_IMODE(info.st_mode) != 0o700", self.source)
        self.assertIn("info.st_uid != os.geteuid()", self.source)
        self.assertIn("resolved != root", self.source)
        action = yaml.load(ACTION.read_text(encoding="utf-8"), Loader=ActionsLoader)
        self.assertNotIn("backend", action["inputs"])
        self.assertNotIn("backend_root", action["inputs"])

    def test_smoke_uses_only_synthetic_hash_identity_and_lock_phases(self) -> None:
        job = self.workflow["jobs"]["backend_smoke"]
        steps = {step.get("id"): step for step in job["steps"] if step.get("id")}
        self.assertEqual({"identity", "acquire", "verify", "release", "residue"}, set(steps))
        for name, phase in (
            ("acquire", "acquire"),
            ("verify", "verify"),
            ("release", "release"),
            ("residue", "residue"),
        ):
            self.assertEqual("./.ciw/actions/device-lock", steps[name]["uses"])
            self.assertEqual(phase, steps[name]["with"]["phase"])
            self.assertEqual("tvos", steps[name]["with"]["device_family"])
            self.assertEqual("backend-smoke", steps[name]["with"]["device_capability"])
            self.assertEqual(
                "${{ steps.identity.outputs.device_identity_hash }}",
                steps[name]["with"]["device_identity_hash"],
            )
        identity = steps["identity"]["run"]
        self.assertIn("hashlib.sha256", identity)
        self.assertIn("issue-208-apple-backend-smoke", identity)
        lowered = self.source.casefold()
        for forbidden in (
            "devicectl",
            "simctl",
            "xcodebuild",
            "adb ",
            "device_identity_hash: udid",
            "device_identity_hash: serial",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_verify_release_and_residue_are_terminally_required(self) -> None:
        job = self.workflow["jobs"]["backend_smoke"]
        steps = {step.get("id"): step for step in job["steps"] if step.get("id")}
        self.assertIn("always()", steps["verify"]["if"])
        self.assertIn("always()", steps["release"]["if"])
        self.assertIn("always()", steps["residue"]["if"])
        self.assertTrue(steps["verify"]["continue-on-error"])
        self.assertTrue(steps["release"]["continue-on-error"])
        self.assertTrue(steps["residue"]["continue-on-error"])
        terminal = next(
            step for step in job["steps"]
            if step.get("name") == "Project terminal Apple backend smoke result"
        )
        self.assertEqual("always()", terminal["if"])
        for name in ("ACQUIRE_OUTCOME", "VERIFY_OUTCOME", "RELEASE_OUTCOME", "RESIDUE_OUTCOME"):
            self.assertIn(name, terminal["env"])
            self.assertIn(f'${{{name}}}', terminal["run"])
        self.assertIn("git status --porcelain=v1 --untracked-files=all", terminal["run"])

    def test_workflow_does_not_expose_backend_or_device_infrastructure(self) -> None:
        lowered = self.source.casefold()
        self.assertNotIn("echo $ciw_device_lock_root", lowered)
        self.assertNotIn("print(raw)", lowered)
        self.assertNotIn("runner.name", lowered)
        self.assertNotIn("runner_name", lowered)
        self.assertNotIn("udid", lowered)
        self.assertNotIn("serial", lowered)
        self.assertNotIn("upload-artifact", lowered)
        self.assertNotIn("download-artifact", lowered)

    def test_zero_artifact_finalizer_runs_on_general_linux(self) -> None:
        finalizer = self.workflow["jobs"]["zero_artifacts"]
        self.assertEqual(["linux", "amd64", "general"], finalizer["runs-on"])
        self.assertEqual("${{ always() && !cancelled() }}", finalizer["if"])
        run = finalizer["steps"][0]["run"]
        self.assertIn("/artifacts", run)
        self.assertIn("total_count", run)
        self.assertIn('test "${BACKEND_RESULT}" = success', run)


if __name__ == "__main__":
    unittest.main()
