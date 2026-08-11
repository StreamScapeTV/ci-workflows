"""Workflow and execution tests for public Android validation."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from ci_workflows import android_contract, android_execution
from ci_workflows.android_types import AndroidValidationError

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github/workflows/reusable-android.yml"
SMOKE = ROOT / ".github/workflows/android-validation-smoke.yml"
ACTION = ROOT / "actions/validate-android/action.yml"
DOC = ROOT / "docs/workflows/android.md"
ARCH = ROOT / "docs/architecture/android-validation.md"
CASES = ROOT / "tests/fixtures/android-validation/cases.json"


class AndroidWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = android_contract.load_android_contract(ROOT)
        cls.cases = json.loads(CASES.read_text(encoding="utf-8"))
        cls.reusable = REUSABLE.read_text(encoding="utf-8")
        cls.smoke = SMOKE.read_text(encoding="utf-8")
        cls.action = ACTION.read_text(encoding="utf-8")
        cls.docs = DOC.read_text(encoding="utf-8")
        cls.arch = ARCH.read_text(encoding="utf-8")

    def plan(self, index: int = 0):
        request = android_contract.request_from_environment(
            dict(self.cases["positive"][index]["environment"]), self.contract
        )
        return android_contract.resolve_validation_plan(self.contract, request)

    def test_yaml_public_identity_and_bounded_inputs(self) -> None:
        reusable = yaml.safe_load(self.reusable)
        smoke = yaml.safe_load(self.smoke)
        action = yaml.safe_load(self.action)
        self.assertEqual(reusable["name"], "Reusable Android validation")
        self.assertEqual(reusable["jobs"]["validate"]["name"], "CI / Android validation")
        self.assertIn("workflow_call", reusable[True])
        inputs = set(reusable[True]["workflow_call"]["inputs"])
        self.assertEqual(inputs, {
            "admitted_sha", "validation_profile", "task_profile", "working_directory",
            "gradle_wrapper_path", "targeted_test_selector", "consumer_script_profile",
            "private_dependency_contract_id", "private_dependency_sha",
            "artifact_exception_id", "device_family", "device_request_id",
        })
        self.assertEqual(set(smoke["jobs"]), {"plan", "execute_android"})
        self.assertEqual(
            smoke["jobs"]["plan"]["runs-on"],
            ["linux", "amd64", "general"],
        )
        self.assertEqual(
            reusable["jobs"]["plan"]["runs-on"],
            ["linux", "amd64", "general"],
        )
        self.assertEqual(smoke["jobs"]["plan"]["timeout-minutes"], 10)
        self.assertEqual(smoke["jobs"]["execute_android"]["timeout-minutes"], 30)
        self.assertIn(
            "fromJSON(needs.plan.outputs.runs_on_json)",
            smoke["jobs"]["execute_android"]["runs-on"],
        )
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertNotIn("workflow_dispatch", reusable[True])

    def test_semantic_runner_and_exact_source_primitives(self) -> None:
        self.assertIn("runs-on: [linux, amd64, general]", self.reusable)
        self.assertIn("runs-on: [linux, amd64, general]", self.smoke)
        self.assertNotIn("runs-on: portable", self.reusable)
        self.assertNotIn("runs-on: portable", self.smoke)
        self.assertIn("fromJSON(needs.plan.outputs.runs_on_json)", self.reusable)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", self.reusable)
        self.assertIn("./.ciw/actions/exact-checkout", self.reusable)
        self.assertIn("./.ciw/actions/checkout-private-dependency", self.reusable)
        self.assertNotIn("self-hosted", self.reusable)
        self.assertNotIn("runs-on: mobile", self.reusable)
        for forbidden in ("macos-latest", "ubuntu-latest", "buildah", "apple-", "docker"):
            self.assertNotIn(forbidden, self.reusable.casefold())

    def test_central_source_uses_called_workflow_identity(self) -> None:
        self.assertEqual(
            self.reusable.count("repository: ${{ job.workflow_repository }}"),
            2,
        )
        self.assertEqual(self.reusable.count("ref: ${{ job.workflow_sha }}"), 2)
        self.assertEqual(self.reusable.count("EXPECTED_REPOSITORY: ${{ job.workflow_repository }}"), 2)
        self.assertEqual(self.reusable.count("EXPECTED_SHA: ${{ job.workflow_sha }}"), 2)
        self.assertNotIn("github.workflow_sha", self.reusable)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", self.reusable)

    def test_smoke_is_direct_mobile_plan_execute_not_nested_reuse(self) -> None:
        self.assertNotIn("./.github/workflows/reusable-android.yml", self.smoke)
        self.assertGreaterEqual(self.smoke.count("uses: ./.ciw/actions/validate-android"), 4)
        self.assertIn("Admit same-repository trusted pull request source", self.smoke)
        self.assertIn("Check out exact admitted smoke source", self.smoke)
        self.assertIn("Verify zero Actions artifacts", self.smoke)
        self.assertIn("Project terminal Android smoke status", self.smoke)
        self.assertNotIn("adb ", self.smoke.casefold())
        self.assertNotIn("physical-device", self.smoke.casefold())

    def test_workflow_is_orchestration_only(self) -> None:
        for forbidden in ("gradlew ", "sdkmanager ", "adb ", "jib", "keystore", "play store", "helm "):
            self.assertNotIn(forbidden, self.reusable.casefold())
        self.assertIn("android validate", self.action)
        self.assertIn("--phase", self.action)
        self.assertIn("--source-root source", self.action)
        self.assertNotIn("git clone", self.reusable)
        self.assertNotIn("git fetch", self.reusable)

    def test_unconditional_cleanup_zero_artifacts_and_terminal_projection(self) -> None:
        for name in ("Remove Android-specific", "Verify zero Android-specific residue", "Remove and verify all registered"):
            self.assertIn(name, self.reusable)
        self.assertGreaterEqual(self.reusable.count("if: always()"), 5)
        self.assertNotIn("upload-artifact", self.reusable)
        self.assertNotIn("download-artifact", self.reusable)
        self.assertIn("Project terminal Android validation status", self.reusable)
        self.assertIn("test \"${WORKSPACE_CLEANUP_OUTCOME}\" = \"success\"", self.reusable)

    def test_exact_toolchain_success_and_wrong_jdk_sdk_rejection(self) -> None:
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / "sdk"
            manager = sdk / "cmdline-tools/latest/bin/sdkmanager"
            manager.parent.mkdir(parents=True)
            manager.write_text("#!/bin/sh\n", encoding="utf-8"); manager.chmod(0o700)
            (sdk / "cmdline-tools/latest/source.properties").write_text("Pkg.Revision=22.0\n", encoding="utf-8")
            platform = sdk / "platforms/android-37"; platform.mkdir(parents=True)
            (platform / "android.jar").write_bytes(b"jar")
            build = sdk / "build-tools/37.0.0"; build.mkdir(parents=True)
            (build / "aapt2").write_bytes(b"aapt2")
            (build / "source.properties").write_text("Pkg.Revision=37.0.0\n", encoding="utf-8")
            outputs = {
                "java": subprocess.CompletedProcess([], 0, "", 'openjdk version "25"\n'),
                "javac": subprocess.CompletedProcess([], 0, "javac 25\n", ""),
                "sdkmanager": subprocess.CompletedProcess([], 0, "22.0\n", ""),
                "packages": subprocess.CompletedProcess([], 0,
                    "platform-tools | 36\nplatforms;android-37.0 | 1\nbuild-tools;37.0.0 | 37.0.0\n", ""),
            }
            def runner(argv, **_kwargs):
                name = Path(argv[0]).name
                if name == "java": return outputs["java"]
                if name == "javac": return outputs["javac"]
                if argv[-1] == "--version": return outputs["sdkmanager"]
                return outputs["packages"]
            with mock.patch.object(android_execution, "run_command", side_effect=runner):
                self.assertEqual(android_execution.verify_toolchain(root, root, plan, self.contract, {"ANDROID_SDK_ROOT": str(sdk)}), (25, 37))
                outputs["javac"] = subprocess.CompletedProcess([], 0, "javac 24\n", "")
                with self.assertRaises(AndroidValidationError) as failure:
                    android_execution.verify_toolchain(root, root, plan, self.contract, {"ANDROID_SDK_ROOT": str(sdk)})
                self.assertEqual(failure.exception.code, "toolchain_mismatch")
                outputs["javac"] = subprocess.CompletedProcess([], 0, "javac 25\n", "")
                outputs["packages"] = subprocess.CompletedProcess([], 0, "platform-tools | 36\n", "")
                with self.assertRaises(AndroidValidationError) as failure:
                    android_execution.verify_toolchain(root, root, plan, self.contract, {"ANDROID_SDK_ROOT": str(sdk)})
                self.assertEqual(failure.exception.code, "sdk_package_missing")

    def test_wrapper_distribution_no_daemon_and_drift(self) -> None:
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            seen: list[str] = []
            def runner(argv, **_kwargs):
                seen.extend(argv)
                return subprocess.CompletedProcess(argv, 0, "\nGradle 9.6.1\n", "")
            with mock.patch.object(android_execution, "run_command", side_effect=runner):
                self.assertEqual(android_execution.verify_wrapper(ROOT, state, plan, {"PATH": os.environ.get("PATH", "")}), "9.6.1")
            self.assertIn("--no-daemon", seen)
            properties = ROOT / plan.working_directory / plan.wrapper.properties_path
            original = properties.read_text(encoding="utf-8")
            try:
                properties.write_text(original.replace("9.6.1", "9.5.0"), encoding="utf-8")
                with self.assertRaises(AndroidValidationError) as failure:
                    android_execution.verify_wrapper(ROOT, state, plan, {"PATH": os.environ.get("PATH", "")})
                self.assertEqual(failure.exception.code, "wrapper_distribution_drift")
            finally:
                properties.write_text(original, encoding="utf-8")

    def test_test_selector_and_gradle_injection_rejection(self) -> None:
        valid = dict(self.cases["positive"][1]["environment"])
        plan = android_contract.resolve_validation_plan(
            self.contract, android_contract.request_from_environment(valid, self.contract)
        )
        self.assertEqual(plan.targeted_test_selector, valid["INPUT_TARGETED_TEST_SELECTOR"])
        for value in ("com.Test*", "com.Test;id", "../Test", "-Pkey=value", "com.Test --tests Other"):
            env = dict(valid); env["INPUT_TARGETED_TEST_SELECTOR"] = value
            with self.subTest(value=value), self.assertRaises(AndroidValidationError) as failure:
                android_contract.resolve_validation_plan(
                    self.contract, android_contract.request_from_environment(env, self.contract)
                )
            self.assertEqual(failure.exception.code, "test_filter_rejected")
        serialized = json.dumps(self.contract["consumers"], sort_keys=True)
        for fragment in ("--init-script", "--project-prop", "--gradle-user-home"):
            self.assertNotIn(fragment, serialized)

    def test_private_dependency_exact_identity_cleanup_and_escape(self) -> None:
        plan = self.plan(1)
        self.assertEqual(plan.private_dependency_repository, "StreamScapeTV/streamscape-media")
        self.assertEqual(plan.private_dependency_sha, "85b3c7ed9711fa6ac53059e5d3e474d791c45d26")
        self.assertEqual(plan.private_dependency_subdirectory, "android")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "workspace/tmp"; state.mkdir(parents=True)
            with self.assertRaises(AndroidValidationError) as failure:
                android_execution.verify_private_dependency(state, plan, {"CIW_ANDROID_PRIVATE_DEPENDENCY_PATH": "../escape"}, self.contract)
            self.assertEqual(failure.exception.code, "private_dependency_rejected")
        self.assertIn("remotes_erased", self.action)
        self.assertIn("credentials_erased", self.action)
        self.assertIn("private_dependency_token", self.reusable)

    def test_debug_unsigned_output_and_aab_rejection(self) -> None:
        request = dict(self.cases["positive"][1]["environment"])
        request.update({"INPUT_VALIDATION_PROFILE": "assemble-debug", "INPUT_TASK_PROFILE": "app-assemble-debug", "INPUT_TARGETED_TEST_SELECTOR": ""})
        plan = android_contract.resolve_validation_plan(
            self.contract, android_contract.request_from_environment(request, self.contract)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = root / plan.expected_debug_outputs[0]; apk.parent.mkdir(parents=True); apk.write_bytes(b"apk")
            self.assertTrue(android_execution.verify_debug_outputs(root, plan))
            aab = root / "app/build/outputs/bundle/debug/app-debug.aab"; aab.parent.mkdir(parents=True); aab.write_bytes(b"aab")
            with self.assertRaises(AndroidValidationError) as failure:
                android_execution.verify_debug_outputs(root, plan)
            self.assertEqual(failure.exception.code, "debug_output_invalid")

    def test_failure_redaction_and_cleanup_no_follow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); outside = root.parent / "android-outside"; outside.mkdir(exist_ok=True)
            link = root / "state/android-validation/link"; link.parent.mkdir(parents=True); link.symlink_to(outside, target_is_directory=True)
            android_execution.cleanup_android_state(root / "state", self.contract)
            self.assertTrue(outside.exists()); self.assertFalse((root / "state/android-validation").exists())
        redacted = android_execution.sanitize("token=abc https://user:pass@example.invalid/x /tmp/path")
        self.assertNotIn("abc", redacted); self.assertNotIn("user:pass", redacted)
        self.assertIn("<redacted>", redacted)

    def test_cleanup_waits_boundedly_for_a_single_use_gradle_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"; state.mkdir()
            (state / "android-validation").mkdir()
            active = f"GradleDaemon --gradle-user-home {state / 'android-validation'}"
            with mock.patch.object(
                android_execution,
                "run_command",
                side_effect=(
                    subprocess.CompletedProcess([], 0, active, ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                ),
            ) as command, mock.patch.object(android_execution.time, "sleep") as sleep:
                android_execution.cleanup_android_state(state, self.contract)
            self.assertEqual(command.call_count, 2)
            sleep.assert_called_once_with(
                android_execution.GRADLE_DAEMON_CLEANUP_POLL_SECONDS
            )
            self.assertFalse((state / "android-validation").exists())

    def test_cleanup_rejects_a_daemon_after_the_bounded_grace_period(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"; state.mkdir()
            active = f"GradleDaemon --gradle-user-home {state / 'android-validation'}"
            with mock.patch.object(
                android_execution,
                "run_command",
                return_value=subprocess.CompletedProcess([], 0, active, ""),
            ), mock.patch.object(
                android_execution.time,
                "monotonic",
                side_effect=(0.0, android_execution.GRADLE_DAEMON_CLEANUP_GRACE_SECONDS),
            ), self.assertRaises(AndroidValidationError) as failure:
                android_execution.cleanup_android_state(state, self.contract)
            self.assertEqual(failure.exception.code, "cleanup_failed")

    def test_room_schema_profiles_failure_projection_and_device_handoff(self) -> None:
        app = self.contract["consumers"]["StreamScapeTV/iptv-android"]["tasks"]
        self.assertEqual([row["stage"] for row in app["app-room-schema"]["commands"]], ["schema-generation", "schema-validation"])
        self.assertEqual(android_execution._failure("compile", "compile"), "compile_failed")
        self.assertEqual(android_execution._failure("tests", "performance"), "performance_failed")
        self.assertEqual(android_execution._failure("lint", "lint"), "lint_failed")
        handoff = self.plan(3)
        self.assertTrue(handoff.is_device_handoff); self.assertEqual(handoff.commands, ())
        self.assertEqual(handoff.output_mode, "handoff-only")
        self.assertNotIn("adb", json.dumps(self.contract, sort_keys=True).casefold())

    def test_docs_record_profiles_noncertification_and_deterministic_outputs(self) -> None:
        for profile in self.contract["profiles"]:
            self.assertIn(f"`{profile}`", self.docs)
        for phrase in ("not product certification", "never runs ADB", "zero GitHub Actions artifacts"):
            self.assertIn(phrase.casefold(), self.docs.casefold())
        self.assertIn("named-function architecture", self.arch)
        first = self.plan().planning_outputs(); second = self.plan().planning_outputs()
        self.assertEqual(first, second); self.assertEqual(first["workspace_profile"], "gradle")
        self.assertEqual(first["runner_profile"], "mobile")


if __name__ == "__main__":
    unittest.main()
