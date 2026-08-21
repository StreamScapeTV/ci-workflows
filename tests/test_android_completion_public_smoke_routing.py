from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android-completion-smoke.yml"


class AndroidCompletionPublicSmokeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_public_contract_and_terminal_jobs_are_hosted(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(["ubuntu-latest"], jobs["contracts"]["runs-on"])
        self.assertEqual(["ubuntu-latest"], jobs["terminal"]["runs-on"])
        self.assertEqual(["linux", "amd64", "mobile"], jobs["completion"]["runs-on"])
        self.assertNotIn("self-hosted", self.source)

    def test_real_mobile_executor_is_private_context_gated(self) -> None:
        completion = self.workflow["jobs"]["completion"]
        condition = completion["if"]
        self.assertIn("github.event.repository.private", condition)
        self.assertIn("needs.contracts.result == 'success'", condition)
        self.assertEqual("contracts", completion["needs"])
        completion_text = self.source.split("  completion:\n", 1)[1].split("  terminal:\n", 1)[0]
        self.assertIn("phase: execute", completion_text)
        self.assertIn("Execute synthetic live-service acceptance", completion_text)
        self.assertIn("Execute synthetic unsigned-release acceptance", completion_text)
        self.assertIn("Verify zero live-service copied-state residue", completion_text)
        self.assertIn("Verify zero unsigned-release copied-state residue", completion_text)
        self.assertNotIn("runs-on: [ubuntu-latest]", completion_text)

    def test_hosted_contract_job_never_executes_product_work(self) -> None:
        contracts_text = self.source.split("  contracts:\n", 1)[1].split("  completion:\n", 1)[0]
        self.assertIn("phase: plan", contracts_text)
        self.assertIn("Reject release artifact traversal before execution", contracts_text)
        self.assertNotIn("phase: execute", contracts_text)
        self.assertNotIn("service_username", contracts_text)
        self.assertNotIn("service_password", contracts_text)
        self.assertNotIn("Prepare one isolated Gradle workspace", contracts_text)

    def test_public_terminal_requires_skip_and_zero_artifacts(self) -> None:
        terminal = self.workflow["jobs"]["terminal"]
        self.assertEqual(["contracts", "completion"], terminal["needs"])
        run = terminal["steps"][0]["run"]
        self.assertIn('test "${CONTRACTS_RESULT}" = success', run)
        self.assertIn('test "${COMPLETION_RESULT}" = skipped', run)
        self.assertIn("REPOSITORY_PRIVATE", terminal["steps"][0]["env"])
        self.assertIn("/artifacts", run)
        self.assertIn("total_count", run)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)


if __name__ == "__main__":
    unittest.main()
