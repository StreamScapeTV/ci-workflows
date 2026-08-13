from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/device-lock-contract-smoke.yml"


class DeviceLockSmokeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_smoke_is_pr_only_nonprivileged_and_source_exact(self) -> None:
        triggers = set(self.workflow["on"])
        self.assertEqual({"pull_request"}, triggers)
        self.assertEqual({"actions": "read", "contents": "read"}, self.workflow["permissions"])
        self.assertIn("github.event.pull_request.head.sha", self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertNotIn("pull_request_target", self.source)
        self.assertNotIn("workflow_dispatch", self.source)
        self.assertNotIn("secrets.", self.source)

    def test_backend_root_is_synthetic_workflow_state_not_an_action_input(self) -> None:
        self.assertIn("CIW_DEVICE_LOCK_ROOT: ${{ runner.temp }}/ciw-device-lock-", self.source)
        action = yaml.load(
            (ROOT / "actions/device-lock/action.yml").read_text(encoding="utf-8"),
            Loader=ActionsLoader,
        )
        self.assertNotIn("backend", action["inputs"])
        self.assertNotIn("backend_root", action["inputs"])
        self.assertNotIn("CIW_DEVICE_LOCK_ROOT", action["runs"]["steps"][0].get("env", {}))

    def test_release_residue_and_backend_cleanup_are_terminal(self) -> None:
        contract = self.workflow["jobs"]["contract_smoke"]
        steps = {step.get("id"): step for step in contract["steps"] if step.get("id")}
        self.assertIn("release", steps)
        self.assertIn("residue", steps)
        self.assertIn("backend_cleanup", steps)
        self.assertIn("always()", steps["release"]["if"])
        self.assertIn("always()", steps["residue"]["if"])
        self.assertEqual("always()", steps["backend_cleanup"]["if"])
        self.assertTrue(steps["release"]["continue-on-error"])
        self.assertTrue(steps["residue"]["continue-on-error"])
        self.assertTrue(steps["backend_cleanup"]["continue-on-error"])
        cleanup = steps["backend_cleanup"]["run"]
        self.assertNotIn("rm -rf", cleanup)
        self.assertIn("rmdir", cleanup)
        self.assertIn("test ! -e", cleanup)
        self.assertIn("test ! -L", cleanup)

    def test_terminal_projection_requires_all_cleanup_outcomes(self) -> None:
        terminal = next(
            step
            for step in self.workflow["jobs"]["contract_smoke"]["steps"]
            if step.get("name") == "Project terminal synthetic lock result after cleanup"
        )
        self.assertEqual("always()", terminal["if"])
        for name in (
            "FOCUSED_OUTCOME",
            "ACQUIRE_OUTCOME",
            "VERIFY_OUTCOME",
            "RELEASE_OUTCOME",
            "RESIDUE_OUTCOME",
            "BACKEND_CLEANUP_OUTCOME",
        ):
            self.assertIn(name, terminal["env"])
            self.assertIn(f'${{{name}}}', terminal["run"])

    def test_zero_artifact_finalizer_is_independent_and_non_cancelled(self) -> None:
        finalizer = self.workflow["jobs"]["zero_artifacts"]
        self.assertEqual(["linux", "amd64", "general"], finalizer["runs-on"])
        self.assertEqual("${{ always() && !cancelled() }}", finalizer["if"])
        run = finalizer["steps"][0]["run"]
        self.assertIn("/artifacts", run)
        self.assertIn("total_count", run)
        self.assertIn('test "${CONTRACT_RESULT}" = success', run)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)

    def test_smoke_never_claims_physical_device_execution(self) -> None:
        lowered = self.source.casefold()
        self.assertIn("synthetic", lowered)
        for forbidden in (
            "adb ",
            "xcrun devicectl",
            "xcrun xctrace",
            "phase: execute",
            "physical-device proof",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
