from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/device-lock-contract-smoke.yml"


class DeviceLockWorkflowAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_job_level_environment_never_uses_runner_context(self) -> None:
        """Runner context is unavailable while GitHub admits and plans a job."""

        for job_name, job in self.workflow["jobs"].items():
            for environment_name, value in job.get("env", {}).items():
                self.assertNotIn(
                    "${{ runner.",
                    str(value),
                    f"{job_name} job-level env {environment_name} uses pre-runner context",
                )
        self.assertNotIn("${{ runner.temp }}", self.source)

    def test_synthetic_backend_path_is_initialized_on_the_runner(self) -> None:
        contract = self.workflow["jobs"]["contract_smoke"]
        self.assertNotIn("CIW_DEVICE_LOCK_ROOT", contract.get("env", {}))

        steps = contract["steps"]
        initialize_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Initialize synthetic private backend path"
        )
        provision_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Provision synthetic private backend root"
        )
        self.assertLess(initialize_index, provision_index)

        initialize = steps[initialize_index]
        run = initialize["run"]
        self.assertIn("RUNNER_TEMP", run)
        self.assertIn("GITHUB_ENV", run)
        self.assertIn("CIW_DEVICE_LOCK_ROOT=", run)
        self.assertIn("GITHUB_RUN_ID", run)
        self.assertIn("GITHUB_RUN_ATTEMPT", run)
        self.assertNotIn("set -x", run)

    def test_trigger_remains_pull_request_only(self) -> None:
        self.assertEqual({"pull_request"}, set(self.workflow["on"]))
        self.assertNotIn("push:", self.source)
        self.assertNotIn("workflow_dispatch", self.source)


if __name__ == "__main__":
    unittest.main()
