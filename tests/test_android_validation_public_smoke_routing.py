"""Regression coverage for public Central Android reusable smoke routing."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android-validation-smoke.yml"
OWNER_GATE = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_GATE = "github.event.pull_request.head.repo.full_name == github.repository"


class AndroidValidationPublicSmokeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_public_contract_and_terminal_jobs_are_hosted(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(["ubuntu-latest"], jobs["contracts"]["runs-on"])
        self.assertEqual(["ubuntu-latest"], jobs["terminal"]["runs-on"])
        self.assertEqual(["linux", "amd64", "mobile"], jobs["android"]["runs-on"])
        self.assertNotIn("self-hosted", self.source)

    def test_real_mobile_executor_is_exact_owner_and_same_repository_gated(self) -> None:
        android = self.workflow["jobs"]["android"]
        condition = android["if"]
        self.assertIn(OWNER_GATE, condition)
        self.assertIn(REPOSITORY_GATE, condition)
        self.assertIn("needs.contracts.result == 'success'", condition)
        self.assertNotIn("github.event.repository.private", condition)
        self.assertEqual("contracts", android["needs"])
        android_text = self.source.split("  android:\n", 1)[1].split("  terminal:\n", 1)[0]
        self.assertIn("phase: execute", android_text)
        self.assertIn("Resolve dependency graph before synthetic protected-full", android_text)
        self.assertIn("Execute protected-full in one mobile workspace", android_text)
        self.assertIn("Verify zero Android-specific residue once", android_text)
        self.assertNotIn("runs-on: [ubuntu-latest]", android_text)

    def test_every_pull_request_runner_job_has_exact_owner_admission(self) -> None:
        for job_id, job in self.workflow["jobs"].items():
            condition = job.get("if", "")
            self.assertIn(OWNER_GATE, condition, job_id)
            self.assertIn(REPOSITORY_GATE, condition, job_id)

    def test_hosted_contract_job_never_executes_gradle_or_workspace_work(self) -> None:
        contracts_text = self.source.split("  contracts:\n", 1)[1].split("  android:\n", 1)[0]
        self.assertIn("phase: plan", contracts_text)
        self.assertIn("Reject arbitrary Gradle task injection before execution", contracts_text)
        self.assertNotIn("phase: execute", contracts_text)
        self.assertNotIn("Resolve dependency graph", contracts_text)
        self.assertNotIn("Prepare one isolated Gradle workspace", contracts_text)

    def test_trusted_terminal_requires_mobile_success_and_zero_artifacts(self) -> None:
        terminal = self.workflow["jobs"]["terminal"]
        self.assertEqual(["contracts", "android"], terminal["needs"])
        run = terminal["steps"][0]["run"]
        self.assertIn('test "${CONTRACTS_RESULT}" = success', run)
        self.assertIn('test "${ANDROID_RESULT}" = success', run)
        self.assertNotIn("REPOSITORY_PRIVATE", terminal["steps"][0]["env"])
        self.assertIn("/artifacts", run)
        self.assertIn("total_count", run)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)


if __name__ == "__main__":
    unittest.main()
