from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import flutter

PRIVATE_HELPERS = (
    "exact-checkout",
    "prepare-workspace",
    "cleanup-workspace",
)
OWNER_GATE = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_GATE = "github.event.pull_request.head.repo.full_name == github.repository"


class FlutterWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = flutter.load_flutter_contract(ROOT)
        self.reusable = (ROOT / ".github/workflows/reusable-flutter.yml").read_text(
            encoding="utf-8"
        )
        self.mobile_smoke = (
            ROOT / ".github/workflows/flutter-validation-smoke.yml"
        ).read_text(encoding="utf-8")
        self.apple_smoke = (
            ROOT / ".github/workflows/flutter-apple-validation-smoke.yml"
        ).read_text(encoding="utf-8")
        self.action = (ROOT / "actions/validate-flutter/action.yml").read_text(
            encoding="utf-8"
        )

    def test_setup_actions_match_contract_without_global_pin_policy(self) -> None:
        setup = self.contract["setup"]
        self.assertIn(f"uses: {setup['action']}", self.reusable)
        self.assertIn(f"uses: {setup['jdk_action']}", self.reusable)
        self.assertIn(f"uses: {setup['jdk_action']}", self.mobile_smoke)
        self.assertEqual("actions/setup-java@v5.6.0", setup["jdk_action"])
        self.assertNotIn("immutable", setup)
        for value in (setup["action"], setup["jdk_action"]):
            owner, reference = value.rsplit("@", 1)
            self.assertTrue(owner)
            self.assertTrue(reference)

    def test_caller_has_no_jdk_gradle_pub_cache_or_runner_input(self) -> None:
        public_inputs = self.reusable.split("outputs:", 1)[0]
        for forbidden in (
            "jdk_version:",
            "java_version:",
            "gradle_version:",
            "pub_cache:",
            "runner:",
            "runs_on:",
            "download_url:",
        ):
            self.assertNotIn(forbidden, public_inputs)
        self.assertIn("java-version: ${{ needs.plan.outputs.jdk_version }}", self.reusable)
        self.assertIn("distribution: ${{ needs.plan.outputs.jdk_distribution }}", self.reusable)

    def test_private_central_helpers_follow_main_without_central_clone(self) -> None:
        self.assertNotIn("actions/checkout@", self.reusable)
        self.assertNotIn("repository: ${{ job.workflow_repository }}", self.reusable)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", self.reusable)
        self.assertNotIn("path: .ciw", self.reusable)
        self.assertNotIn("./.ciw/actions/", self.reusable)
        self.assertNotIn("secrets: inherit", self.reusable)
        self.assertNotIn("private_dependency_token", self.reusable)
        for helper in PRIVATE_HELPERS:
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@main",
                self.reusable,
            )
        self.assertIn(
            "StreamScapeTV/ci-workflows/actions/validate-flutter@main",
            self.reusable,
        )
        self.assertEqual(
            3,
            self.reusable.count(
                "uses: StreamScapeTV/ci-workflows/actions/exact-checkout@main"
            ),
        )
        self.assertEqual(
            3,
            self.reusable.count(
                "uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@main"
            ),
        )
        self.assertEqual(
            3,
            self.reusable.count(
                "uses: StreamScapeTV/ci-workflows/actions/cleanup-workspace@main"
            ),
        )
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.reusable)
        self.assertEqual(
            3,
            self.reusable.count(
                '(cd source && test "$(git rev-parse HEAD)" = "${EXPECTED_SHA}")'
            ),
        )

    def test_plan_exports_exact_flutter_dart_gradle_jdk_tuple(self) -> None:
        for output in (
            "flutter_version",
            "dart_version",
            "gradle_version",
            "jdk_distribution",
            "jdk_version",
        ):
            self.assertIn(f"{output}: ${{{{ steps.plan.outputs.{output} }}}}", self.reusable)
        self.assertIn("Resolve contract-owned Flutter Dart Gradle and JDK tuple", self.reusable)

    def test_reusable_semantic_selectors_and_hosted_self_ci_are_separated(self) -> None:
        def job_block(source: str, job: str) -> str:
            match = re.search(
                rf"(?ms)^  {re.escape(job)}:\n(.*?)(?=^  [a-z_]+:\n|\Z)",
                source,
            )
            self.assertIsNotNone(match, job)
            return match.group(0)

        for source in (self.reusable, self.mobile_smoke, self.apple_smoke):
            self.assertNotIn("runs-on: portable", source)
            self.assertNotIn("runs-on: [linux, amd64, general]", source)

        for job in ("plan", "validate"):
            self.assertIn(
                "runs-on: [linux, amd64, general, small]",
                job_block(self.reusable, job),
            )
        for job in ("portable", "mobile", "apple"):
            self.assertIn(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
                job_block(self.reusable, job),
            )
        for job in ("source_audit", "focused_tests", "plan", "android"):
            self.assertIn(
                "runs-on: [ubuntu-latest]",
                job_block(self.mobile_smoke, job),
            )
        self.assertIn(
            "runs-on: [ubuntu-latest]",
            job_block(self.apple_smoke, "plan"),
        )
        self.assertIn("runs-on: [macos-latest]", job_block(self.apple_smoke, "ios"))
        for source, job in ((self.mobile_smoke, "android"), (self.apple_smoke, "ios")):
            block = job_block(source, job)
            self.assertNotIn(
                "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
                block,
            )
            self.assertIn(OWNER_GATE, block)
            self.assertIn(REPOSITORY_GATE, block)
            self.assertNotIn("github.event.repository.private", block)
            self.assertIn("needs.plan.result == 'success'", block)
        self.assertIn(
            '== ["linux", "amd64", "mobile"]',
            self.mobile_smoke,
        )
        self.assertIn(
            '== ["macOS", "ARM64"]',
            self.apple_smoke,
        )

    def test_smokes_do_not_add_actions_artifact_api_finalizers(self) -> None:
        for source in (self.mobile_smoke, self.apple_smoke):
            self.assertNotIn("zero_artifacts:", source)
            self.assertNotIn("actions: read", source)
            self.assertNotIn("/artifacts", source)
            self.assertNotIn("total_count", source)

    def test_pub_cache_is_only_registered_workflow_state(self) -> None:
        expected = "{0}/tmp/flutter-validation/pub-cache"
        for source in (self.reusable, self.mobile_smoke, self.apple_smoke):
            self.assertIn(expected, source)
            self.assertNotIn("$HOME/.pub-cache", source)
            self.assertNotIn("{0}/.pub-cache", source)
            self.assertNotIn("/opt/runner-cache/pub", source)
        self.assertIn("phase: persistent-cache-snapshot", self.reusable)
        self.assertIn("phase: pub-cache-bind", self.reusable)
        self.assertIn("phase: persistent-cache-verify", self.reusable)
        self.assertIn("Prove persistent host pub cache is unchanged", self.reusable)

    def test_mobile_and_apple_order_snapshot_setup_bind_verify_execute_verify_cleanup(self) -> None:
        mobile = self.reusable.split("  mobile:", 1)[1].split("  apple:", 1)[0]
        apple = self.reusable.split("  apple:", 1)[1].split("  validate:", 1)[0]
        for block in (mobile, apple):
            snapshot = block.index("phase: persistent-cache-snapshot")
            flutter_setup = block.index("uses: subosito/flutter-action@")
            bind = block.index("phase: pub-cache-bind")
            toolchain = block.index("phase: verify-toolchain")
            execute = block.index("phase: execute")
            persistent_verify = block.index("phase: persistent-cache-verify")
            cleanup = block.index("phase: cleanup")
            residue = block.index("phase: residue")
            self.assertLess(snapshot, flutter_setup)
            self.assertLess(flutter_setup, bind)
            self.assertLess(bind, toolchain)
            self.assertLess(toolchain, execute)
            self.assertLess(execute, persistent_verify)
            self.assertLess(persistent_verify, cleanup)
            self.assertLess(cleanup, residue)
            terminal_steps = {
                "persistent_cache_verify": "phase: persistent-cache-verify",
                "flutter_cleanup": "phase: cleanup",
                "flutter_residue": "phase: residue",
            }
            for step_id, phase in terminal_steps.items():
                start = block.index(f"- id: {step_id}")
                end = block.find("\n      - ", start + 1)
                step_block = block[start : end if end >= 0 else None]
                self.assertIn("if: always()", step_block, step_id)
                self.assertIn(phase, step_block, step_id)
        self.assertLess(mobile.index("uses: actions/setup-java@"), mobile.index("uses: subosito/flutter-action@"))
        self.assertNotIn("uses: actions/setup-java@", apple)

    def test_mobile_and_apple_flutter_setup_retry_is_bounded_and_cleans_state(self) -> None:
        mobile = self.reusable.split("  mobile:", 1)[1].split("  apple:", 1)[0]
        apple = self.reusable.split("  apple:", 1)[1].split("  validate:", 1)[0]
        for block in (mobile, apple):
            self.assertEqual(2, block.count("uses: subosito/flutter-action@"))
            self.assertEqual(1, block.count("- id: flutter_setup_primary"))
            self.assertEqual(1, block.count("- id: flutter_setup_reset"))
            self.assertEqual(1, block.count("- id: flutter_setup_retry"))
            primary = block.split("- id: flutter_setup_primary", 1)[1].split(
                "- id: flutter_setup_reset", 1
            )[0]
            reset = block.split("- id: flutter_setup_reset", 1)[1].split(
                "- id: flutter_setup_retry", 1
            )[0]
            retry = block.split("- id: flutter_setup_retry", 1)[1].split(
                "- id: pub_cache_bind", 1
            )[0]
            self.assertIn("continue-on-error: true", primary)
            expected_condition = "if: ${{ steps.flutter_setup_primary.outcome == 'failure' }}"
            self.assertIn(expected_condition, reset)
            self.assertIn(expected_condition, retry)
            self.assertIn('case "${FLUTTER_CACHE_PATH}" in "${CI_TOOL_ROOT}/flutter-sdk-"*', reset)
            self.assertIn('test ! -L "${FLUTTER_CACHE_PATH}" && test ! -L "${PUB_CACHE_PATH}"', reset)
            self.assertIn('rm -rf -- "${FLUTTER_CACHE_PATH}" "${PUB_CACHE_PATH}"', reset)
            self.assertIn('mkdir -p -- "${PUB_CACHE_PATH}"', reset)
            self.assertIn("sleep 5", reset)
            run_block = reset.split("run: |", 1)[1]
            self.assertLess(
                len([line for line in run_block.splitlines() if line.strip()]),
                8,
            )
            self.assertLess(
                block.index("- id: flutter_setup_retry"),
                block.index("phase: pub-cache-bind"),
            )

    def test_smoke_verifies_jdk_before_flutter_project_generation(self) -> None:
        android = self.mobile_smoke.split("  android:", 1)[1]
        self.assertLess(android.index("uses: actions/setup-java@"), android.index("phase: verify-toolchain"))
        self.assertLess(android.index("phase: verify-toolchain"), android.index("flutter create --no-pub"))
        self.assertIn("java-version: ${{ needs.plan.outputs.jdk_version }}", android)
        self.assertIn("grep -F \"gradle-${EXPECTED_GRADLE}-all.zip\"", android)

    def test_manual_flutter_commands_assert_exact_pub_cache_each_time(self) -> None:
        for source in (self.mobile_smoke, self.apple_smoke):
            command_block = source.split("Create contract-owned", 1)[1].split("- id: flutter", 1)[0]
            self.assertNotIn("require_pub_cache", command_block)
            lines = [line.strip() for line in command_block.splitlines() if line.strip()]
            flutter_indexes = [
                index
                for index, line in enumerate(lines)
                if line.startswith("flutter ") or "&& flutter " in line
            ]
            self.assertEqual(2, len(flutter_indexes))
            expected_guard = [
                'test "${PUB_CACHE}" = "${EXPECTED_PUB_CACHE}"',
                'test -d "${PUB_CACHE}"',
                'test ! -L "${PUB_CACHE}"',
            ]
            for index in flutter_indexes:
                self.assertEqual(expected_guard, lines[index - 3:index], lines[index])

    def test_runtime_enforces_pub_cache_before_every_flutter_or_dart_command(self) -> None:
        execution = (ROOT / "src/ci_workflows/flutter_execution.py").read_text(
            encoding="utf-8"
        )
        run_checked = execution.split("def _run_checked", 1)[1].split("def _verify_authority", 1)[0]
        self.assertIn('argv[0] in {"flutter", "dart"}', run_checked)
        self.assertIn("_assert_exact_pub_cache", run_checked)
        self.assertIn("require_exists=True", run_checked)

    def test_cleanup_projects_primary_and_cleanup_failures(self) -> None:
        self.assertIn("primary_failure_code:", self.action)
        self.assertIn("cleanup_failure_code:", self.action)
        for source in (self.reusable, self.mobile_smoke, self.apple_smoke):
            self.assertIn("primary_failure_code: ${{ steps.", source)
        execution = (ROOT / "src/ci_workflows/flutter_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("raise FlutterValidationError(primary_failure_code, cleanup.code)", execution)

    def test_output_missing_and_path_rejected_are_separate(self) -> None:
        execution = (ROOT / "src/ci_workflows/flutter_execution.py").read_text(
            encoding="utf-8"
        )
        function = execution.split("def _verify_expected_outputs", 1)[1].split("def _verify_gradle_wrapper", 1)[0]
        self.assertIn('fail("output_missing")', function)
        self.assertIn('fail("path_rejected")', function)
        self.assertLess(function.index('fail("output_missing")'), function.index('fail("path_rejected")'))

    def test_contract_and_runtime_json_are_readable_exact_generated_bytes(self) -> None:
        self.assertEqual((), flutter.generate_flutter_contract_files(ROOT, check=True))
        for relative in (
            "contracts/flutter-validation.json",
            "tests/fixtures/flutter-validation/runtime-3.41.4.json",
            "tests/fixtures/flutter-validation/runtime-3.44.6.json",
        ):
            payload = (ROOT / relative).read_bytes()
            self.assertTrue(payload.endswith(b"\n"))
            self.assertIn(b"\n  \"", payload)
            self.assertEqual(payload, (json.dumps(json.loads(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
        self.assertIn("python3 scripts/ci/flutter.py generate --check", self.mobile_smoke)

    def test_action_is_thin_and_exposes_all_terminal_outputs(self) -> None:
        self.assertIn('python3 "${GITHUB_ACTION_PATH}/../../scripts/ci/ciw.py"', self.action)
        self.assertIn("flutter validate", self.action)
        self.assertNotIn("curl ", self.action)
        self.assertNotIn("sudo ", self.action)
        for output in (
            "gradle_version",
            "jdk_distribution",
            "jdk_version",
            "java_version",
            "java_runtime_version",
            "java_vendor",
            "javac_version",
            "pub_cache_path",
            "persistent_pub_cache_unchanged",
            "primary_failure_code",
            "cleanup_failure_code",
        ):
            self.assertRegex(self.action, rf"(?m)^  {re.escape(output)}:\n")

    def test_no_signing_store_or_deployment_path(self) -> None:
        combined = "\n".join((self.reusable, self.mobile_smoke, self.apple_smoke)).lower()
        for forbidden in (
            "secrets: inherit",
            "packages: write",
            "id-token: write",
            "testflight",
            "app store",
            "notarization",
            "kubeconfig",
            "registry_token",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()