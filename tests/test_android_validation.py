"""Focused contract tests for reusable Android validation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows import android_contract
from ci_workflows.android_types import AndroidValidationError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/android-validation/cases.json"


class AndroidValidationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = android_contract.load_android_contract(ROOT)
        cls.cases = json.loads(FIXTURES.read_text(encoding="utf-8"))

    def test_contract_identity_toolchain_profiles_and_prohibitions(self) -> None:
        contract = self.contract
        self.assertEqual(contract["workflow_api"], "validation.android")
        self.assertEqual(contract["contract_version"], "1.0.0")
        self.assertEqual(contract["stable_check_name"], "CI / Android validation")
        self.assertEqual(contract["planner_runner_profile"], "portable")
        self.assertEqual(contract["execution_runner_profile"], "mobile")
        self.assertEqual(
            contract["toolchain"]["packages"],
            ["platform-tools", "platforms;android-37.0", "build-tools;37.0.0"],
        )
        self.assertEqual(contract["toolchain"]["java_major"], 25)
        self.assertEqual(contract["toolchain"]["javac_major"], 25)
        self.assertEqual(contract["toolchain"]["android_api"], 37)
        self.assertEqual(contract["toolchain"]["command_line_tools_version"], "14742923")
        self.assertEqual(contract["toolchain"]["build_tools_version"], "37.0.0")
        self.assertEqual(
            set(contract["profiles"]),
            {
                "toolchain-smoke", "compile", "unit-targeted", "unit-full",
                "performance", "lint", "assemble-debug", "room-schema",
                "consumer-script", "device-handoff",
            },
        )
        serialized = json.dumps(contract, sort_keys=True).casefold()
        for forbidden in (
            '"runner"', '"container_engine"', '"signing_identity"',
            '"play_store"', '"database_url"', '"backend_url"',
        ):
            self.assertIn(forbidden, serialized)
        self.assertNotIn("self-hosted", serialized)
        self.assertNotIn("google jib", serialized)

    def test_positive_fixture_requests_resolve_to_mobile(self) -> None:
        for case in self.cases["positive"]:
            with self.subTest(case=case["name"]):
                request = android_contract.request_from_environment(
                    case["environment"], self.contract
                )
                plan = android_contract.resolve_validation_plan(self.contract, request)
                self.assertEqual(plan.runner_profile, case["expected_runner"])
                self.assertEqual(plan.planner_runner_profile, "portable")
                self.assertEqual(plan.admitted_sha, case["environment"]["INPUT_ADMITTED_SHA"])
                self.assertIn("--no-daemon", plan.fixed_gradle_arguments)

    def test_negative_fixture_requests_fail_with_stable_code(self) -> None:
        for case in self.cases["negative"]:
            with self.subTest(case=case["name"]), self.assertRaises(AndroidValidationError) as failure:
                request = android_contract.request_from_environment(
                    case["environment"], self.contract
                )
                android_contract.resolve_validation_plan(self.contract, request)
            self.assertEqual(failure.exception.code, case["code"])

    def test_target_selector_is_exact_and_property_injection_is_absent(self) -> None:
        valid = "com.streamscapetv.app.feature.PlayerViewModelTest.exactCase"
        self.assertRegex(valid, self.contract["test_selector_regex"])
        for invalid in (
            "com.Example*", "com.ExampleTest --tests Other", "com.ExampleTest;id",
            "../ExampleTest", "-Psecret=value", "com.ExampleTest#method",
        ):
            self.assertNotRegex(invalid, self.contract["test_selector_regex"])
        for consumer in self.contract["consumers"].values():
            for task in consumer["tasks"].values():
                serialized = "\0".join(
                    item for command in task["commands"] for item in command["argv"]
                )
                self.assertNotIn("--init-script", serialized)
                self.assertNotIn("--project-prop", serialized)
                self.assertNotIn("--gradle-user-home", serialized)

    def test_private_dependency_is_exact_detached_primitive_contract(self) -> None:
        dependency = self.contract["private_dependencies"]["streamscape-media-android-v1"]
        self.assertEqual(dependency["repository"], "StreamScapeTV/streamscape-media")
        self.assertEqual(dependency["expected_subdirectory"], "android")
        self.assertEqual(
            dependency["allowed_shas"],
            ["85b3c7ed9711fa6ac53059e5d3e474d791c45d26"],
        )
        self.assertIn("android/settings.gradle.kts", dependency["required_paths"])
        self.assertIn("android/gradlew", dependency["required_paths"])
        request = android_contract.request_from_environment(
            dict(self.cases["positive"][1]["environment"]), self.contract
        )
        plan = android_contract.resolve_validation_plan(self.contract, request)
        self.assertTrue(plan.requires_private_dependency)
        self.assertEqual(plan.private_dependency_id, "streamscape-media-android")
        self.assertEqual(plan.private_dependency_subdirectory, "android")

    def test_device_handoff_emits_no_device_execution_command(self) -> None:
        plan = android_contract.resolve_validation_plan(
            self.contract,
            android_contract.request_from_environment(
                dict(self.cases["positive"][3]["environment"]), self.contract
            ),
        )
        self.assertTrue(plan.is_device_handoff)
        self.assertEqual(plan.commands, ())
        self.assertEqual(plan.output_mode, "handoff-only")
        self.assertEqual(plan.device_family, "android-tv")
        self.assertNotIn("adb", json.dumps(self.contract["consumers"], sort_keys=True).casefold())

    def test_artifact_exception_is_named_bounded_and_never_allows_packages(self) -> None:
        exception = self.contract["artifact_exceptions"]["android-redacted-diagnostics-v1"]
        self.assertLessEqual(exception["maximum_bytes"], 16 * 1024 * 1024)
        self.assertLessEqual(exception["maximum_files"], 200)
        self.assertLessEqual(exception["retention_days"], 3)
        self.assertIn(".apk", exception["forbidden_extensions"])
        self.assertIn(".aab", exception["forbidden_extensions"])
        environment = dict(self.cases["positive"][1]["environment"])
        environment["INPUT_ARTIFACT_EXCEPTION_ID"] = "unknown-exception"
        with self.assertRaises(AndroidValidationError) as failure:
            android_contract.resolve_validation_plan(
                self.contract,
                android_contract.request_from_environment(environment, self.contract),
            )
        self.assertEqual(failure.exception.code, "artifact_policy_failed")

    def test_paths_reject_traversal_and_symlink_escape(self) -> None:
        for value in ("../gradlew", "/tmp/gradlew", "a/../../gradlew", "a\\gradlew"):
            with self.subTest(value=value), self.assertRaises(AndroidValidationError):
                android_contract.safe_relative(value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "safe").mkdir()
            outside = root.parent / "outside-android-validation"
            outside.mkdir(exist_ok=True)
            link = root / "safe/link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink unavailable")
            with self.assertRaises(AndroidValidationError):
                android_contract.bounded_path(root, "safe/link/file")

    def test_fork_source_is_rejected_without_private_dependency_or_cache(self) -> None:
        event = {"pull_request": {"head": {"repo": {"full_name": "fork/iptv-android"}}}}
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            environment = dict(self.cases["positive"][1]["environment"])
            environment.update(
                {
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_EVENT_PATH": str(event_path),
                    "GITHUB_REPOSITORY": "StreamScapeTV/iptv-android",
                }
            )
            with self.assertRaises(AndroidValidationError) as failure:
                android_contract.resolve_validation_plan(
                    self.contract,
                    android_contract.request_from_environment(environment, self.contract),
                )
            self.assertEqual(failure.exception.code, "source_trust_rejected")

    def test_contract_hash_is_deterministic(self) -> None:
        path = ROOT / android_contract.CONTRACT_PATH
        first = android_contract.file_sha256(path)
        second = android_contract.file_sha256(path)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
