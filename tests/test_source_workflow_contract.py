from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SourceWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github/workflows/reusable-resolve-source.yml").read_text(encoding="utf-8")
        cls.action = (ROOT / "actions/exact-checkout/action.yml").read_text(encoding="utf-8")
        cls.contract = json.loads((ROOT / "contracts/source-admission.json").read_text(encoding="utf-8"))

    def test_reusable_workflow_is_short_metadata_only_and_exactly_stages_helper(self) -> None:
        self.assertIn("\n  workflow_call:\n", self.workflow)
        for forbidden in (
            "\n  pull_request:\n",
            "\n  pull_request_target:\n",
            "\n  issue_comment:\n",
            "\n  workflow_run:\n",
            "\n  workflow_dispatch:\n",
            "actions/download-artifact",
            "actions/upload-artifact",
            "secrets: inherit",
            "persist-credentials: true",
        ):
            self.assertNotIn(forbidden, self.workflow)
        self.assertRegex(
            self.workflow,
            r"uses: StreamScapeTV/ci-workflows/actions/resolve-source@[0-9a-f]{40}",
        )
        self.assertIn("runs-on: [linux, amd64, general, tiny]", self.workflow)
        self.assertNotIn("runs-on: [linux, amd64, general]", self.workflow)
        self.assertNotIn("runs-on: portable", self.workflow)
        self.assertNotIn("run: |", self.workflow)

    def test_workflow_and_machine_contract_publish_the_same_typed_outputs(self) -> None:
        workflow_output_block = self.workflow.split("    outputs:\n", 1)[1].split("\npermissions:\n", 1)[0]
        actual = {
            line.strip()[:-1]
            for line in workflow_output_block.splitlines()
            if line.startswith("      ") and not line.startswith("        ") and line.strip().endswith(":")
        }
        self.assertEqual(actual, set(self.contract["outputs"]))
        self.assertIn("source_repository", actual)
        self.assertIn("requires_freshness", actual)
        self.assertIn("evidence_id", actual)

    def test_exact_checkout_action_has_no_mutable_ref_or_caller_command_surface(self) -> None:
        self.assertIn("using: composite", self.action)
        self.assertIn("--admitted-sha", self.action)
        self.assertIn("PYTHONPATH=\"${GITHUB_ACTION_PATH}/../../src\"", self.action)
        for forbidden in (
            " ref:",
            "branch:",
            "tag:",
            "remote_url",
            "command:",
            "shell_command",
            "persist-credentials: true",
            "actions/checkout@",
        ):
            self.assertNotIn(forbidden, self.action)
        self.assertNotIn("github.event.pull_request.head", self.action)

    def test_contract_declares_fail_closed_artifact_and_trust_boundaries(self) -> None:
        security = self.contract["security"]
        self.assertTrue(security["unknown_inputs_fail_closed"])
        self.assertTrue(security["consumer_trust_mode_input_forbidden"])
        self.assertTrue(security["mutable_ref_input_forbidden"])
        self.assertTrue(security["workflow_artifacts_are_untrusted"])
        self.assertFalse(security["trusted_metadata_executes_pull_request_source"])
        checkout = self.contract["exact_checkout"]
        self.assertFalse(checkout["persist_credentials"])
        self.assertTrue(checkout["detached_head_required"])
        self.assertTrue(checkout["head_equality_required"])


if __name__ == "__main__":
    unittest.main()
