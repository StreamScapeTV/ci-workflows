from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
APPLE_HELPER_SHA = "88d179740145ccea00b6986d78ceb67ea365face"


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
        self.planner = (
            ROOT / "src/ci_workflows/apple_contract.py"
        ).read_text(encoding="utf-8")
        self.execution = (
            ROOT / "src/ci_workflows/apple_execution.py"
        ).read_text(encoding="utf-8")
        self.types = (
            ROOT / "src/ci_workflows/apple_types.py"
        ).read_text(encoding="utf-8")

    def test_public_api_and_stable_check(self) -> None:
        self.assertEqual(self.contract["workflow_api"], "validation.apple")
        self.assertEqual(self.contract["contract_version"], "1.0.0")
        self.assertEqual(
            self.contract["stable_check_name"],
            "CI / Apple validation",
        )
        self.assertIn("name: CI / Apple validation", self.workflow)
        self.assertIn("workflow_call:", self.workflow)
        expected_inputs = {
            "admitted_sha",
            "artifact_exception_id",
            "command_profile",
            "destination_profile",
            "platform",
            "scheme",
            "script_path",
            "validation_profile",
            "version_file",
            "working_directory",
        }
        block = self.workflow.split("inputs:", 1)[1].split("outputs:", 1)[0]
        actual_inputs = set(re.findall(r"^      ([a-z_]+):$", block, re.M))
        self.assertEqual(expected_inputs, actual_inputs)
        output_block = self.workflow.split("outputs:", 1)[1].split(
            "permissions:",
            1,
        )[0]
        actual_outputs = set(
            re.findall(r"^      ([a-z_]+):$", output_block, re.M)
        )
        self.assertEqual(
            {"artifact_exception_used", "result", "test_summary"},
            actual_outputs,
        )

    def test_semantic_runner_selection_uses_protected_planner(self) -> None:
        direct_general_selector = "runs-on: [linux, amd64, general]"
        self.assertIn(direct_general_selector, self.workflow)
        self.assertEqual(self.smoke.count(direct_general_selector), 2)
        self.assertNotIn("runs-on: portable", self.workflow + self.smoke)
        self.assertEqual(
            1,
            self.workflow.count(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
            ),
        )
        self.assertEqual(
            3,
            self.smoke.count(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
            ),
        )
        self.assertNotIn("runs-on: macOS", self.workflow)
        self.assertNotIn("runs-on: self-hosted", self.workflow + self.smoke)
        self.assertNotIn("macos-latest", self.workflow + self.smoke)
        self.assertNotIn("ubuntu-latest", self.workflow + self.smoke)
        self.assertEqual(self.contract["planner_runner_profile"], "portable")
        self.assertEqual(self.contract["execution_runner_profile"], "apple")
        self.assertIn(
            'requested_profile=plan.runner_profile.value',
            (ROOT / "src/ci_workflows/ciw_apple.py").read_text(encoding="utf-8"),
        )

    def test_smoke_exercises_exact_implementation_on_all_apple_platforms(self) -> None:
        self.assertNotIn(
            "uses: ./.github/workflows/reusable-apple.yml",
            self.smoke,
        )
        self.assertGreaterEqual(
            self.smoke.count("uses: ./.ciw/actions/validate-apple"),
            12,
        )
        self.assertIn("validation_profile: ios-simulator", self.smoke)
        self.assertIn("validation_profile: tvos-simulator", self.smoke)
        self.assertIn("validation_profile: macos", self.smoke)
        self.assertIn("Real iOS simulator smoke", self.smoke)
        self.assertIn("Real tvOS simulator smoke", self.smoke)
        self.assertIn("Real unsigned macOS smoke", self.smoke)
        self.assertIn("github.event.pull_request.head.sha", self.smoke)
        self.assertIn("head.repo.full_name == github.repository", self.smoke)
        self.assertIn("timeout-minutes: 120", self.smoke)
        self.assertNotIn("workflow_dispatch:", self.smoke)

    def test_smoke_cancellation_scope_is_stable_and_skips_artifact_check(self) -> None:
        concurrency = self.smoke.split("concurrency:", 1)[1].split("jobs:", 1)[0]
        self.assertIn(
            "group: apple-validation-smoke-pr-${{ github.event.pull_request.number }}",
            concurrency,
        )
        self.assertNotIn("github.event.pull_request.head.sha", concurrency)
        self.assertIn("cancel-in-progress: true", concurrency)
        zero_artifacts = self.smoke.split("  zero_artifacts:", 1)[1]
        self.assertIn("if: ${{ always() && !cancelled() }}", zero_artifacts)

    def test_smoke_runs_when_apple_public_registration_changes(self) -> None:
        for path in (
            "contracts/bootstrap-public-workflows.json",
            "contracts/ciw-commands.json",
            "contracts/public-workflows.json",
            "contracts/public-workflows/validation.json",
            "docs/reference/ciw.md",
            "docs/workflows/public-api-reference.md",
            "src/ci_workflows/ciw.py",
            "tests/test_apple_ciw_dispatch.py",
            "tests/test_bootstrap.py",
            "tests/test_ciw_cli.py",
            "tests/test_ciw_contracts.py",
            "tests/test_public_api_contract.py",
        ):
            with self.subTest(path=path):
                self.assertIn(f"      - {path}", self.smoke)

    def test_external_actions_are_full_sha_pinned(self) -> None:
        text = "\n".join((self.workflow, self.smoke))
        for value in re.findall(r"uses:\s*([^\s#]+)", text):
            if value.startswith("./"):
                continue
            self.assertRegex(value, r"@[0-9a-f]{40}$", value)

    def test_permissions_and_zero_artifact_policy_are_explicit(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("permissions:\n  actions: read\n  contents: read", self.smoke)
        text = (self.workflow + self.smoke + self.action).lower()
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("download-artifact", text)
        self.assertNotIn("secrets:", text)
        self.assertIn("routine apple actions artifacts verified: zero", self.smoke.lower())
        self.assertIn("total_count", self.smoke)
        self.assertEqual(self.contract["artifact_policy"], "zero-default")

    def test_private_helper_identity_and_terminal_cleanup_are_mandatory(self) -> None:
        self.assertNotIn("github.workflow_sha", self.workflow)
        self.assertNotIn("actions/checkout@", self.workflow)
        self.assertNotIn("repository: ${{ job.workflow_repository }}", self.workflow)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", self.workflow)
        self.assertNotIn("path: .ciw", self.workflow)
        self.assertNotIn("./.ciw/actions/", self.workflow)
        self.assertNotIn(
            "python3 .ciw/scripts/ci/apple_checkout_cleanup.py",
            self.workflow,
        )
        self.assertEqual(
            4,
            self.workflow.count(
                f"uses: StreamScapeTV/ci-workflows/actions/validate-apple@{APPLE_HELPER_SHA}"
            ),
        )
        self.assertIn(
            f"uses: StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
            self.workflow,
        )
        self.assertIn(
            f"uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}",
            self.workflow,
        )
        self.assertIn(
            f"uses: StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}",
            self.workflow,
        )
        self.assertIn("phase: cleanup", self.workflow)
        self.assertIn("phase: residue", self.workflow)
        self.assertGreaterEqual(self.workflow.count("if: always()"), 3)
        self.assertIn("test ! -e .ciw", self.workflow)
        self.assertIn("test ! -L .ciw", self.workflow)
        self.assertIn("CIW_CLEANUP_OUTCOME", self.workflow)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "${EXPECTED_SHA}"', self.workflow)

    def test_checkout_cleanup_is_fixed_and_no_follow(self) -> None:
        workflows = self.workflow + self.smoke
        self.assertNotIn(
            "python3 .ciw/scripts/ci/apple_checkout_cleanup.py",
            self.workflow,
        )
        self.assertIn(
            "python3 .ciw/scripts/ci/apple_checkout_cleanup.py source",
            self.smoke,
        )
        self.assertNotIn("rm -rf -- source", workflows)
        self.assertGreaterEqual(self.workflow.count("os.lstat"), 3)
        self.assertGreaterEqual(self.workflow.count("stat.S_ISLNK"), 3)
        self.assertGreaterEqual(self.workflow.count("os.path.lexists"), 3)
        self.assertIn(
            '_TARGETS = {"central": ".ciw", "source": "source"}',
            self.checkout_cleanup_adapter,
        )
        self.assertIn("choices=sorted(_TARGETS)", self.checkout_cleanup_adapter)
        self.assertIn("_remove_no_follow(path)", self.checkout_cleanup_adapter)
        self.assertIn("os.path.lexists(path)", self.checkout_cleanup_adapter)

    def test_no_signing_physical_device_archive_store_or_deployment_path(self) -> None:
        workflow_text = (self.workflow + self.smoke + self.action).lower()
        for forbidden in (
            "secrets.",
            "keychain import",
            "provisioning_profile",
            "development_team",
            "archivepath",
            "exportarchive",
            "testflight",
            "notarytool",
            "kubectl",
            "helm",
            "docker",
            "buildah",
            "device_udid",
        ):
            self.assertNotIn(forbidden, workflow_text)
        source = self.execution.lower()
        self.assertIn("code_signing_allowed=no", source)
        self.assertIn("code_signing_required=no", source)
        self.assertIn("code_sign_identity=", source)
        self.assertNotIn("archive_path", source)

    def test_deterministic_simulator_creation_and_owned_cleanup_are_present(self) -> None:
        self.assertIn(
            '("xcrun", "simctl", "list", "devices", "available", "-j")',
            self.execution,
        )
        self.assertIn('"simctl",\n            "create"', self.execution)
        self.assertIn('"simctl", "bootstatus"', self.execution)
        self.assertIn('"simctl", "shutdown"', self.execution)
        self.assertIn('"simctl", "delete"', self.execution)
        self.assertIn("RUNNER_WORKSPACE", self.execution)
        self.assertIn(".ciw-apple-simulator-ownership-v1", self.execution)
        self.assertIn("registry.json", self.execution)
        self.assertIn("fcntl.flock", self.execution)
        self.assertIn("pending-create", self.execution)
        self.assertIn("simulator_ownership_locked", self.execution)
        self.assertIn("simulator_ownership_corrupt", self.execution)
        self.assertIn("simulator_ownership_identity_mismatch", self.execution)
        self.assertIn("simulator_unowned", self.execution)
        self.assertIn("simulator_ambiguous", self.execution)
        self.assertNotIn("state_root.resolve()", self.execution)
        self.assertNotIn('destination = "generic/', self.execution.lower())

    def test_exact_toolchain_sdk_and_package_resolution_are_checked(self) -> None:
        toolchain = self.contract["toolchain"]
        self.assertEqual(toolchain["xcode_version"], "26.6")
        self.assertEqual(toolchain["xcode_build"], "17F113")
        self.assertEqual(toolchain["swift_version"], "6.3.3")
        self.assertEqual(
            set(toolchain["sdk_versions"]),
            {
                "iphoneos",
                "iphonesimulator",
                "appletvos",
                "appletvsimulator",
                "macosx",
            },
        )
        self.assertIn("xcrun", self.execution)
        self.assertIn("--show-sdk-version", self.execution)
        self.assertIn("-disableAutomaticPackageResolution", self.execution)
        self.assertIn("-onlyUsePackageVersionsFromResolvedFile", self.execution)
        self.assertIn("package_resolution_mutation", self.execution)

    def test_no_product_name_branching_in_shared_implementation(self) -> None:
        shared = "\n".join(
            (
                self.facade,
                self.planner,
                self.execution,
                self.types,
                (ROOT / "src/ci_workflows/ciw_apple.py").read_text(encoding="utf-8"),
            )
        ).lower()
        for product in ("streamscapetv/iptv-apple", "streamscapetv/streamscape-media"):
            self.assertNotIn(product, shared)
        self.assertNotIn("streamscape_", shared)
        self.assertIn("StreamScapeTV/iptv-apple", json.dumps(self.contract))
        self.assertIn("StreamScapeTV/streamscape-media", json.dumps(self.contract))

    def test_no_follow_cleanup_and_outside_sentinel_protection_are_implemented(self) -> None:
        self.assertIn("os.lstat", self.execution)
        self.assertIn("stat.S_ISLNK", self.execution)
        self.assertIn("_remove_no_follow", self.execution)
        self.assertIn("_lexical_target", self.execution)
        self.assertIn("outside_sentinels", self.contract["cleanup"])
        self.assertTrue(self.contract["cleanup"]["outside_sentinels"])

    def test_fixture_is_product_neutral_and_unsigned(self) -> None:
        project = (
            ROOT
            / "tests/fixtures/apple-validation/smoke-project/AppleValidationSmoke.xcodeproj/project.pbxproj"
        ).read_text(encoding="utf-8")
        source = (
            ROOT / "tests/fixtures/apple-validation/smoke-project/Sources/SmokeApp.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("SUPPORTED_PLATFORMS", project)
        self.assertIn("iphoneos iphonesimulator appletvos appletvsimulator macosx", project)
        self.assertIn("CODE_SIGNING_ALLOWED = NO", project)
        self.assertIn("CODE_SIGNING_REQUIRED = NO", project)
        self.assertNotIn("tv.streamscape", project)
        self.assertIn("@main", source)
        self.assertNotIn("StreamScapeTV", source)


if __name__ == "__main__":
    unittest.main()
