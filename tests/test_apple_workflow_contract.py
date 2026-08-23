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
APPLE_HELPER_SHA = "c82cd9fba134ff736621b8bbd636594c2a6fe923"
OWNER_GATE = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_GATE = "github.event.pull_request.head.repo.full_name == github.repository"


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
        self.multistage = (
            ROOT / "src/ci_workflows/apple_multistage.py"
        ).read_text(encoding="utf-8")
        self.guard = (
            ROOT / "src/ci_workflows/apple_plan_guard.py"
        ).read_text(encoding="utf-8")
        self.types = (
            ROOT / "src/ci_workflows/apple_types.py"
        ).read_text(encoding="utf-8")

    def test_public_api_and_stable_check(self) -> None:
        self.assertEqual(self.contract["workflow_api"], "validation.apple")
        self.assertEqual(self.contract["contract_version"], "1.0.0")
        self.assertEqual(self.contract["stable_check_name"], "CI / Apple validation")
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
            "validation_scope",
            "validation_plan_json",
            "private_dependency_repository",
            "private_dependency_sha",
            "private_dependency_subdirectory",
            "private_dependency_id",
        }
        block = self.workflow.split("inputs:", 1)[1].split("secrets:", 1)[0]
        actual_inputs = set(re.findall(r"^      ([a-z_]+):$", block, re.M))
        self.assertEqual(expected_inputs, actual_inputs)
        output_block = self.workflow.split("outputs:", 1)[1].split(
            "permissions:", 1
        )[0]
        actual_outputs = set(re.findall(r"^      ([a-z_]+):$", output_block, re.M))
        self.assertEqual(
            {"artifact_exception_used", "cleanup_result", "result", "test_summary"},
            actual_outputs,
        )
        self.assertIn("private_dependency_token:", self.workflow)
        self.assertNotIn("secrets: inherit", self.workflow)

    def test_reusable_semantic_selection_and_hosted_self_ci_are_separated(self) -> None:
        direct_general_selector = "runs-on: [linux, amd64, general, small]"
        dynamic_apple_selector = "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
        hosted_control_selector = "runs-on: [ubuntu-latest]"
        hosted_apple_selector = "runs-on: [macos-latest]"
        self.assertIn(direct_general_selector, self.workflow)
        self.assertNotIn(direct_general_selector, self.smoke)
        self.assertEqual(self.smoke.count(hosted_control_selector), 2)
        self.assertEqual(self.smoke.count(hosted_apple_selector), 1)
        self.assertNotIn("runs-on: ubuntu-latest", self.smoke)
        self.assertEqual(self.workflow.count(dynamic_apple_selector), 1)
        self.assertNotIn(dynamic_apple_selector, self.smoke)
        self.assertIn('["macOS","ARM64"]', self.smoke)
        self.assertGreaterEqual(self.smoke.count(OWNER_GATE), 3)
        self.assertGreaterEqual(self.smoke.count(REPOSITORY_GATE), 3)
        self.assertNotIn("github.event.repository.private", self.smoke)
        self.assertIn('APPLE_RESULT: ${{ needs.apple.result }}', self.smoke)
        self.assertIn('test "${APPLE_RESULT}" = success', self.smoke)
        self.assertNotIn("runs-on: [linux, amd64, general]", self.workflow + self.smoke)
        self.assertNotIn("runs-on: portable", self.workflow + self.smoke)
        self.assertNotIn("runs-on: macOS", self.workflow + self.smoke)
        self.assertNotIn("runs-on: self-hosted", self.workflow + self.smoke)
        self.assertNotIn("macos-latest", self.workflow)
        self.assertIn("macos-latest", self.smoke)
        self.assertNotIn("ubuntu-latest", self.workflow)
        self.assertEqual(self.contract["planner_runner_profile"], "portable")
        self.assertEqual(self.contract["execution_runner_profile"], "apple")
        self.assertIn('requested_profile="apple"', (
            ROOT / "src/ci_workflows/ciw_apple.py"
        ).read_text(encoding="utf-8"))

    def test_smoke_exercises_all_apple_platforms_in_one_real_job(self) -> None:
        self.assertNotIn("uses: ./.github/workflows/reusable-apple.yml", self.smoke)
        self.assertEqual(self.smoke.count("uses: ./.ciw/actions/validate-apple"), 4)
        self.assertIn('"platform":"ios"', self.smoke)
        self.assertIn('"platform":"tvos"', self.smoke)
        self.assertIn('"platform":"macos"', self.smoke)
        self.assertIn("Real protected-full Apple smoke", self.smoke)
        self.assertNotIn("Real iOS simulator smoke", self.smoke)
        self.assertNotIn("Real tvOS simulator smoke", self.smoke)
        self.assertNotIn("Real unsigned macOS smoke", self.smoke)
        self.assertIn("github.event.pull_request.head.sha", self.smoke)
        self.assertIn("head.repo.full_name == github.repository", self.smoke)
        self.assertIn("timeout-minutes: 120", self.smoke)
        self.assertIn("validation_scope: protected-full", self.smoke)
        self.assertNotIn('"operation":"test"', self.smoke)
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
        self.assertIn(OWNER_GATE, zero_artifacts)
        self.assertIn(REPOSITORY_GATE, zero_artifacts)
        self.assertIn("always() && !cancelled()", zero_artifacts)

    def test_smoke_runs_when_apple_public_registration_changes(self) -> None:
        for path in (
            "contracts/bootstrap-public-workflows.json",
            "contracts/ciw-commands.json",
            "contracts/public-workflows.json",
            "contracts/public-workflows/validation.json",
            "docs/reference/ciw.md",
            "docs/workflows/public-api-reference.md",
            "src/ci_workflows/ciw.py",
            "tests/test_bootstrap.py",
            "tests/test_ciw_cli.py",
            "tests/test_ciw_contracts.py",
            "tests/test_public_api_contract.py",
        ):
            with self.subTest(path=path):
                self.assertIn(f"      - {path}", self.smoke)
        self.assertIn("      - src/ci_workflows/apple_multistage.py", self.smoke)
        self.assertIn("      - src/ci_workflows/apple_plan_guard.py", self.smoke)
        self.assertIn("      - tests/test_apple_*.py", self.smoke)

    def test_external_actions_are_full_sha_pinned(self) -> None:
        text = "\n".join((self.workflow, self.smoke))
        for value in re.findall(r"uses:\s*([^\s#]+)", text):
            if value.startswith("./"):
                continue
            self.assertRegex(value, r"@[0-9a-f]{40}$", value)

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
        self.assertEqual(self.workflow.count("phase: execute"), 1)
        self.assertEqual(self.workflow.count("phase: cleanup"), 1)
        self.assertEqual(self.workflow.count("phase: residue"), 1)
        self.assertGreaterEqual(self.workflow.count("if: always()"), 4)
        self.assertIn("APPLE_CLEANUP_OUTCOME", self.workflow)
        self.assertIn("WORKSPACE_CLEANUP_OUTCOME", self.workflow)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.workflow)
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${EXPECTED_SHA}"', self.workflow)
        self.assertIn("os.lstat", self.workflow)
        self.assertIn("stat.S_ISLNK", self.workflow)
        self.assertIn("os.path.lexists", self.workflow)

    def test_media_vlc_tvos_native_contract_is_bounded_and_distinct_from_mpv(self) -> None:
        merged = load_apple_contract(ROOT)
        consumers = merged["consumer_contracts"]
        self.assertEqual(
            consumers["streamscape-media-apple"]["profiles"][
                "native-dependency-preparation"
            ],
            "media-native-dependency",
        )
        self.assertEqual(
            consumers["streamscape-media-vlc-tvos-apple"]["repository"],
            "StreamScapeTV/streamscape-media",
        )
        self.assertEqual(
            consumers["streamscape-media-vlc-tvos-apple"]["profiles"],
            {"native-dependency-preparation": "media-vlc-tvos-native-dependency"},
        )
        request = AppleValidationRequest(
            repository="StreamScapeTV/streamscape-media",
            admitted_sha="a" * 40,
            consumer_contract="streamscape-media-vlc-tvos-apple",
            validation_profile=AppleProfile.NATIVE_DEPENDENCY_PREPARATION,
            source_trust="trusted-exact",
            platform="apple-native",
        )
        plan = build_plan(merged, request)
        self.assertEqual(plan.task_profile, "media-vlc-tvos-native-dependency")
        self.assertEqual(plan.runner_profile.value, "apple")
        self.assertEqual(plan.planner_runner_profile.value, "portable")
        self.assertEqual(len(plan.commands), 1)
        self.assertEqual(
            plan.commands[0].script_path,
            "scripts/ci/build-private-tvos-vlc-candidate.sh",
        )
        self.assertEqual(plan.commands[0].fixed_arguments, ())
        self.assertIsNone(plan.simulator)
        self.assertEqual(plan.artifact_exception_id, None)
        self.assertIn("native/vlc/root-intake.lock.json", plan.protected_paths)
        self.assertIn(
            "native/vlc/apple-tvos-build-support-inventory.json",
            plan.protected_paths,
        )
        self.assertEqual(
            plan.environment_bindings,
            (("STREAMSCAPE_ARTIFACT_DIR", "native-output"),),
        )

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
        self.assertGreaterEqual(self.workflow.count("os.lstat"), 2)
        self.assertGreaterEqual(self.workflow.count("stat.S_ISLNK"), 2)
        self.assertGreaterEqual(self.workflow.count("os.path.lexists"), 2)
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
        self.assertNotIn("secrets: inherit", workflow_text)
        self.assertNotIn("keychain", self.workflow.lower())
        source = self.execution.lower()
        self.assertIn("code_signing_allowed=no", source)
        self.assertIn("code_signing_required=no", source)
        self.assertIn("code_sign_identity=", source)
        self.assertNotIn("archive_path", source)
        self.assertIn("_SAFE_BOOLEAN_BUILD_SETTINGS", self.guard)
        self.assertIn("_SAFE_CLEANUP_LEAVES", self.guard)

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
        self.assertIn("External Display", self.execution)
        self.assertIn("_recorded_owned_companion_candidates", self.execution)
        self.assertIn("_delete_recorded_owned_objects", self.execution)
        self.assertNotIn("state_root.resolve()", self.execution)
        self.assertNotIn('destination = "generic/', self.execution.lower())
        self.assertIn("generic/platform=", self.multistage)
        self.assertIn("needs_booted_simulator", self.multistage)

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
        self.assertIn("shared_directories", self.multistage)
        self.assertIn('shared["result-bundles"] / stage.identifier', self.multistage)

    def test_no_product_name_branching_in_shared_implementation(self) -> None:
        shared = "\n".join(
            (
                self.facade,
                self.planner,
                self.execution,
                self.multistage,
                self.guard,
                self.types,
                (ROOT / "src/ci_workflows/ciw_apple.py").read_text(encoding="utf-8"),
                (ROOT / "src/ci_workflows/apple_contract_fragments.py").read_text(
                    encoding="utf-8"
                ),
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
        self.assertIn("_remove_no_follow", self.multistage)
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