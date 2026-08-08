from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FlutterWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(
            (ROOT / "contracts/flutter-validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.workflow = (
            ROOT / ".github/workflows/reusable-flutter.yml"
        ).read_text(encoding="utf-8")
        self.smoke = (
            ROOT / ".github/workflows/flutter-validation-smoke.yml"
        ).read_text(encoding="utf-8")
        self.apple_smoke = (
            ROOT / ".github/workflows/flutter-apple-validation-smoke.yml"
        ).read_text(encoding="utf-8")
        self.action = (ROOT / "actions/validate-flutter/action.yml").read_text(
            encoding="utf-8"
        )

    def test_public_api_and_stable_check(self) -> None:
        self.assertEqual("validation.flutter", self.contract["workflow_api"])
        self.assertEqual("1.0.0", self.contract["contract_version"])
        self.assertIn("name: CI / Flutter validation", self.workflow)
        self.assertIn("workflow_call:", self.workflow)
        self.assertIn("validation_profile:", self.workflow)
        self.assertIn("command_profile:", self.workflow)
        self.assertNotIn("consumer_contract:\n", self.workflow)
        expected_inputs = {
            "admitted_sha",
            "artifact_exception_id",
            "command_profile",
            "platform",
            "script_path",
            "validation_profile",
            "version_file",
            "working_directory",
        }
        block = self.workflow.split("inputs:", 1)[1].split("outputs:", 1)[0]
        actual_inputs = set(re.findall(r"^      ([a-z_]+):$", block, re.M))
        self.assertEqual(expected_inputs, actual_inputs)
        output_block = self.workflow.split("outputs:", 1)[1].split(
            "permissions:", 1
        )[0]
        actual_outputs = set(
            re.findall(r"^      ([a-z_]+):$", output_block, re.M)
        )
        self.assertEqual(
            {"result", "test_summary", "artifact_exception_used"},
            actual_outputs,
        )

    def test_semantic_runner_separation_uses_trusted_resolver(self) -> None:
        self.assertIn("runs-on: portable", self.workflow)
        self.assertEqual(
            3,
            self.workflow.count(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
            ),
        )
        self.assertEqual(
            1,
            self.smoke.count(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
            ),
        )
        self.assertEqual(
            1,
            self.apple_smoke.count(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
            ),
        )
        for text in (self.workflow, self.smoke, self.apple_smoke):
            self.assertNotIn("needs.android_plan.outputs.runs_on_json", text)
            self.assertNotIn("needs.ios_plan.outputs.runs_on_json", text)
            self.assertNotIn(
                "uses: ./.github/workflows/reusable-flutter.yml", text
            )
        combined = self.workflow + self.smoke + self.apple_smoke
        for forbidden in (
            "runs-on: self-hosted",
            "runs-on: macos-latest",
            "runs-on: ubuntu-latest",
            "runs-on: windows-latest",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertIn(
            "needs.plan.outputs.runner_profile == 'mobile'", self.workflow
        )
        self.assertIn(
            "needs.plan.outputs.runner_profile == 'apple'", self.workflow
        )
        self.assertIn(
            "needs.plan.outputs.runner_profile == 'portable'", self.workflow
        )

    def test_action_and_tool_setup_are_full_sha_pinned(self) -> None:
        text = "\n".join((self.workflow, self.smoke, self.apple_smoke))
        uses = re.findall(r"uses:\s*([^\s#]+)", text)
        for value in uses:
            if value.startswith("./"):
                continue
            self.assertRegex(value, r"@[0-9a-f]{40}$", value)
        self.assertRegex(
            self.contract["setup"]["action"], r"@[0-9a-f]{40}$"
        )
        self.assertIn(self.contract["setup"]["action"], self.workflow)
        self.assertIn(self.contract["setup"]["action"], self.smoke)
        self.assertIn(self.contract["setup"]["action"], self.apple_smoke)

    def test_setup_and_pub_caches_are_marker_bound(self) -> None:
        text = self.workflow + self.smoke + self.apple_smoke
        self.assertIn("cache-path:", text)
        self.assertIn("pub-cache-path:", text)
        self.assertIn("env.CI_TOOL_ROOT", text)
        self.assertIn("env.HOME", text)
        self.assertNotIn("RUNNER_TOOL_CACHE", text)

    def test_no_artifact_secret_signing_or_device_paths(self) -> None:
        text = (
            self.workflow
            + "\n"
            + self.smoke
            + "\n"
            + self.apple_smoke
            + "\n"
            + self.action
        ).lower()
        for forbidden in (
            "upload-artifact",
            "download-artifact",
            "secrets.",
            "workflow_dispatch",
            "keychain",
            "testflight",
            "app store",
            "notarization",
            "runner_labels",
            "registry",
            "deployment",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("--no-codesign", json.dumps(self.contract))
        self.assertIn("artifact_exception_used", self.action)

    def test_plan_precedes_execution_and_cleanup_is_always(self) -> None:
        self.assertLess(
            self.workflow.index("jobs:\n  plan:"),
            self.workflow.index("  mobile:"),
        )
        for text in (self.workflow, self.smoke, self.apple_smoke):
            self.assertIn("if: always()", text)
            self.assertIn("phase: cleanup", text)
            self.assertIn("phase: residue", text)
            self.assertIn("persist-credentials: false", text)
        self.assertIn("github.workflow_sha", self.workflow)

    def test_synthetic_smoke_lock_bootstrap_precedes_enforcement(self) -> None:
        self.assertIn(
            "flutter create --no-pub --platforms=android", self.smoke
        )
        self.assertIn(
            "flutter create --no-pub --platforms=ios", self.apple_smoke
        )
        self.assertIn("(cd source && flutter pub get)", self.smoke)
        self.assertIn("(cd source && flutter pub get)", self.apple_smoke)
        self.assertIn(
            '["flutter", "pub", "get", "--enforce-lockfile"]',
            json.dumps(self.contract),
        )

    def test_smokes_cover_portable_mobile_apple_and_zero_artifacts(self) -> None:
        self.assertIn("source-audit", self.smoke)
        self.assertIn("focused_tests:", self.smoke)
        self.assertIn("validation_profile: android-debug", self.smoke)
        self.assertIn("validation_profile: ios-simulator", self.apple_smoke)
        self.assertIn(
            "routine flutter mobile actions artifacts verified: zero",
            self.smoke.lower(),
        )
        self.assertIn(
            "routine flutter apple actions artifacts verified: zero",
            self.apple_smoke.lower(),
        )
        self.assertIn("jobs:\n  source_audit:", self.smoke)
        self.assertIn("jobs:\n  plan:", self.apple_smoke)
        self.assertIn("  zero_artifacts:", self.smoke)
        self.assertIn("  zero_artifacts:", self.apple_smoke)
        self.assertNotIn("  source-audit:", self.smoke)
        self.assertNotIn("  zero-artifacts:", self.smoke + self.apple_smoke)


if __name__ == "__main__":
    unittest.main()
