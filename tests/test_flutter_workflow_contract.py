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

    def test_exact_pinned_setup_actions_match_contract(self) -> None:
        setup = self.contract["setup"]
        self.assertIn(f"uses: {setup['action']}", self.reusable)
        self.assertIn(f"uses: {setup['jdk_action']}", self.reusable)
        self.assertIn(f"uses: {setup['jdk_action']}", self.mobile_smoke)
        self.assertRegex(setup["action"], r"@[0-9a-f]{40}$")
        self.assertRegex(setup["jdk_action"], r"@[0-9a-f]{40}$")
        self.assertNotIn("actions/setup-java@v", self.reusable)
        self.assertNotIn("actions/setup-java@v", self.mobile_smoke)

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

    def test_plan_exports_immutable_flutter_dart_gradle_jdk_tuple(self) -> None:
        for output in (
            "flutter_version",
            "dart_version",
            "gradle_version",
            "jdk_distribution",
            "jdk_version",
        ):
            self.assertIn(f"{output}: ${{{{ steps.plan.outputs.{output} }}}}", self.reusable)
        self.assertIn("Resolve contract-owned Flutter Dart Gradle and JDK tuple", self.reusable)

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
            self.assertIn("if: always()", block[persistent_verify - 160:persistent_verify])
            self.assertIn("if: always()", block[cleanup - 200:cleanup])
            self.assertIn("if: always()", block[residue - 180:residue])
        self.assertLess(mobile.index("uses: actions/setup-java@"), mobile.index("uses: subosito/flutter-action@"))
        self.assertNotIn("uses: actions/setup-java@", apple)

    def test_smoke_verifies_jdk_before_flutter_project_generation(self) -> None:
        android = self.mobile_smoke.split("  android:", 1)[1].split("  zero_artifacts:", 1)[0]
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

    def test_no_artifact_upload_signing_store_or_deployment_path(self) -> None:
        combined = "\n".join((self.reusable, self.mobile_smoke, self.apple_smoke)).lower()
        for forbidden in (
            "upload-artifact",
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
