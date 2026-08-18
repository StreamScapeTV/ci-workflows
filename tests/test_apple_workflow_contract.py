from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from ci_workflows.apple_contract import build_plan
from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_types import AppleProfile, AppleValidationRequest

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
APPLE_HELPER_SHA = "2dacd98d19c5e136ce4803ab70b0f7ebd45414bf"


class AppleWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(
            (ROOT / "contracts/apple-validation.json").read_text(encoding="utf-8")
        )
        self.workflow = (
            ROOT / ".github/workflows/reusable-apple.yml"
        ).read_text(encoding="utf-8")
        self.smoke = (
            ROOT / ".github/workflows/apple-validation-smoke.yml"
        ).read_text(encoding="utf-8")
        self.action = (ROOT / "actions/validate-apple/action.yml").read_text(
            encoding="utf-8"
        )
        self.checkout_cleanup_adapter = (
            ROOT / "scripts/ci/apple_checkout_cleanup.py"
        ).read_text(encoding="utf-8")
        self.facade = (ROOT / "src/ci_workflows/apple.py").read_text(
            encoding="utf-8"
        )

    def test_public_api_and_stable_check(self) -> None:
        expected = {
            "admitted_sha",
            "validation_scope",
            "validation_plan_json",
            "validation_profile",
            "version_file",
            "working_directory",
            "command_profile",
            "script_path",
            "platform",
            "scheme",
            "destination_profile",
            "artifact_exception_id",
            "private_dependency_repository",
            "private_dependency_sha",
            "private_dependency_subdirectory",
            "private_dependency_id",
        }
        self.assertEqual(set(self.contract["inputs"]), expected)
        self.assertIn("name: apple-validation", self.workflow)
        self.assertIn("name: apple-validation", self.smoke)

    def test_semantic_runner_selection_uses_one_heavy_apple_executor(self) -> None:
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertEqual(self.workflow.count("runs-on:"), 2)
        self.assertEqual(self.workflow.count("id: execute"), 1)
        self.assertNotIn("matrix:", self.workflow)

    def test_smoke_exercises_all_apple_platforms_in_one_real_job(self) -> None:
        self.assertIn('"platform":"ios"', self.smoke)
        self.assertIn('"platform":"tvos"', self.smoke)
        self.assertIn('"platform":"macos"', self.smoke)
        self.assertIn("Real protected-full Apple smoke", self.smoke)

    def test_smoke_runs_when_apple_public_registration_changes(self) -> None:
        self.assertIn("reusable-apple.yml", self.smoke)
        self.assertIn("contracts/apple-validation.json", self.smoke)
        self.assertIn("actions/validate-apple/action.yml", self.smoke)

    def test_smoke_cancellation_scope_is_stable_and_skips_artifact_check(self) -> None:
        self.assertIn("cancel-in-progress: true", self.smoke)
        self.assertIn("Verify Apple routine artifacts remain zero", self.smoke)

    def test_exact_toolchain_sdk_and_package_resolution_are_checked(self) -> None:
        action = self.action.lower()
        self.assertIn("xcode", action)
        self.assertIn("swift", action)
        self.assertIn("sdk", action)
        self.assertIn("package", action)

    def test_deterministic_simulator_creation_and_owned_cleanup_are_present(self) -> None:
        action = self.action.lower()
        self.assertIn("simulator", action)
        self.assertIn("cleanup", action)
        self.assertIn("residue", action)

    def test_checkout_cleanup_is_fixed_and_no_follow(self) -> None:
        adapter = self.checkout_cleanup_adapter
        self.assertIn("os.lstat", adapter)
        self.assertIn("os.unlink", adapter)
        self.assertNotIn("shutil.rmtree", adapter)

    def test_no_follow_cleanup_and_outside_sentinel_protection_are_implemented(self) -> None:
        workflow = self.workflow
        self.assertIn("os.lstat", workflow)
        self.assertIn("os.unlink", workflow)
        self.assertNotIn("rm -rf source", workflow)

    def test_permissions_artifacts_and_private_dependency_secret_are_explicit(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("permissions:\n  actions: read\n  contents: read", self.smoke)
        text = (self.workflow + self.smoke + self.action).lower()
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("download-artifact", text)
        self.assertIn("private_dependency_token", self.workflow)
        self.assertNotIn("secrets: inherit", text)
        self.assertIn("routine apple actions artifacts verified: zero", self.smoke.lower())
        self.assertIn("total_count", self.smoke)
        self.assertEqual(self.contract["artifact_policy"], "zero-default")

    def test_private_helper_identity_and_terminal_cleanup_are_mandatory(self) -> None:
        self.assertNotIn("github.workflow_sha", self.workflow)
        self.assertNotIn("actions/checkout@", self.workflow)
        self.assertNotIn("path: .ciw", self.workflow)
        self.assertNotIn("./.ciw/actions/", self.workflow)
        self.assertEqual(
            self.workflow.count(
                f"uses: StreamScapeTV/ci-workflows/actions/validate-apple@{APPLE_HELPER_SHA}"
            ),
            4,
        )
        for action in (
            "exact-checkout",
            "prepare-workspace",
            "checkout-private-dependency",
            "cleanup-workspace",
        ):
            self.assertIn(
                f"uses: StreamScapeTV/ci-workflows/actions/{action}@{FOUNDATION_SHA}",
                self.workflow,
            )

    def test_external_actions_are_full_sha_pinned(self) -> None:
        for text in (self.workflow, self.smoke):
            for match in re.finditer(r"uses:\s+([^\s]+)", text):
                target = match.group(1)
                if target.startswith("./"):
                    continue
                self.assertRegex(target, r"@[0-9a-f]{40}(?:\s|$|#)")

    def test_fixture_is_product_neutral_and_unsigned(self) -> None:
        fixture = (ROOT / "tests/fixtures/apple-validation/smoke-project/project.yml").read_text(
            encoding="utf-8"
        ).lower()
        self.assertNotIn("streamscape", fixture)
        self.assertNotIn("signing", fixture)

    def test_no_product_name_branching_in_shared_implementation(self) -> None:
        text = (self.facade + self.action).lower()
        self.assertNotIn("iptv", text)
        self.assertNotIn("streamscape", text)

    def test_no_signing_physical_device_archive_store_or_deployment_path(self) -> None:
        text = (self.facade + self.action).lower()
        for forbidden in ("app-store", "notar", "archivepath", "development_team"):
            self.assertNotIn(forbidden, text)

    def test_media_vlc_tvos_native_contract_is_bounded_and_distinct_from_mpv(self) -> None:
        contract = load_apple_contract()
        self.assertIn("media-vlc-tvos-native", contract.command_profiles)
        self.assertIn("media-mpv-tvos", contract.command_profiles)
        self.assertNotEqual(
            contract.command_profiles["media-vlc-tvos-native"],
            contract.command_profiles["media-mpv-tvos"],
        )


if __name__ == "__main__":
    unittest.main()
