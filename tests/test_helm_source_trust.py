from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-helm-validate.yml"


class HelmSourceTrustWorkflowTests(unittest.TestCase):
    def test_pull_request_and_pull_request_target_share_pr_trust_classification(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event_name == 'pull_request'", text)
        self.assertIn("github.event_name == 'pull_request_target'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name != github.repository", text)
        self.assertIn("'untrusted-fork'", text)
        self.assertIn("'trusted-pr'", text)

    def test_both_pr_event_families_checkout_exact_head_repository(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        expected = (
            "repository: ${{ (github.event_name == 'pull_request' || "
            "github.event_name == 'pull_request_target') && "
            "github.event.pull_request.head.repo.full_name || github.repository }}"
        )
        self.assertIn(expected, text)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", text)

    def test_validation_remains_tokenless_portable_intent_on_general_small(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("registry_username", text)
        public_inputs = text.split("secrets:", 1)[0] if "secrets:" in text else text
        self.assertNotIn("source_trust:\n        description:", public_inputs)
        self.assertIn("runs-on: [linux, amd64, general, small]", text)
        self.assertNotIn("runs-on: [linux, amd64, general]", text)
        self.assertNotIn("runs-on: portable", text)


if __name__ == "__main__":
    unittest.main()
