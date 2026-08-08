from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FlutterWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads((ROOT / "contracts/flutter-validation.json").read_text(encoding="utf-8"))
        self.workflow = (ROOT / ".github/workflows/reusable-flutter.yml").read_text(encoding="utf-8")
        self.smoke = (ROOT / ".github/workflows/flutter-validation-smoke.yml").read_text(encoding="utf-8")
        self.action = (ROOT / "actions/validate-flutter/action.yml").read_text(encoding="utf-8")

    def test_public_api_and_stable_check(self) -> None:
        self.assertEqual("validation.flutter", self.contract["workflow_api"])
        self.assertEqual("1.0.0", self.contract["contract_version"])
        self.assertIn("name: CI / Flutter validation", self.workflow)
        self.assertIn("workflow_call:", self.workflow)
        self.assertIn("validation_profile:", self.workflow)
        self.assertIn("command_profile:", self.workflow)
        self.assertNotIn("consumer_contract:\n", self.workflow)
        expected_inputs = {
            "admitted_sha", "artifact_exception_id", "command_profile",
            "platform", "script_path", "validation_profile",
            "version_file", "working_directory",
        }
        block = self.workflow.split("inputs:", 1)[1].split("outputs:", 1)[0]
        actual_inputs = set(re.findall(r"^      ([a-z_]+):$", block, re.M))
        self.assertEqual(expected_inputs, actual_inputs)
        output_block = self.workflow.split("outputs:", 1)[1].split("permissions:", 1)[0]
        actual_outputs = set(re.findall(r"^      ([a-z_]+):$", output_block, re.M))
        self.assertEqual({"result", "test_summary", "artifact_exception_used"}, actual_outputs)

    def test_semantic_runner_separation(self) -> None:
        for runner in ("portable", "mobile", "apple"):
            self.assertRegex(self.workflow, rf"runs-on:\s*{runner}\b")
        for forbidden in ("self-hosted", "macos-latest", "ubuntu-latest", "windows-latest"):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("needs.plan.outputs.runner_profile == 'mobile'", self.workflow)
        self.assertIn("needs.plan.outputs.runner_profile == 'apple'", self.workflow)
        self.assertIn("needs.plan.outputs.runner_profile == 'portable'", self.workflow)

    def test_action_and_tool_setup_are_full_sha_pinned(self) -> None:
        uses = re.findall(r"uses:\s*([^\s#]+)", self.workflow + "\n" + self.smoke)
        for value in uses:
            if value.startswith("./"):
                continue
            self.assertRegex(value, r"@[0-9a-f]{40}$", value)
        self.assertRegex(self.contract["setup"]["action"], r"@[0-9a-f]{40}$")
        self.assertIn(self.contract["setup"]["action"], self.workflow)

    def test_no_artifact_secret_signing_or_device_paths(self) -> None:
        text = (self.workflow + "\n" + self.smoke + "\n" + self.action).lower()
        for forbidden in (
            "upload-artifact", "download-artifact", "secrets.", "workflow_dispatch",
            "keychain", "testflight", "app store", "notarization",
            "runner_labels", "registry", "deployment",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("--no-codesign", json.dumps(self.contract))
        self.assertIn("artifact_exception_used", self.action)

    def test_plan_precedes_execution_and_cleanup_is_always(self) -> None:
        self.assertLess(self.workflow.index("jobs:\n  plan:"), self.workflow.index("  mobile:"))
        self.assertIn("if: always()", self.workflow)
        self.assertIn("phase: cleanup", self.workflow)
        self.assertIn("phase: residue", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn("github.workflow_sha", self.workflow)

    def test_smoke_has_portable_mobile_and_apple_real_paths(self) -> None:
        self.assertIn("source-audit", self.smoke)
        self.assertIn("android-debug", self.smoke)
        self.assertIn("ios-simulator", self.smoke)
        self.assertIn("flutter create", self.smoke)
        self.assertIn("routine flutter actions artifacts verified: zero", self.smoke.lower())
        self.assertIn("jobs:\n  source_audit:", self.smoke)
        self.assertIn("  zero_artifacts:", self.smoke)
        self.assertNotIn("  source-audit:", self.smoke)
        self.assertNotIn("  zero-artifacts:", self.smoke)


if __name__ == "__main__":
    unittest.main()
