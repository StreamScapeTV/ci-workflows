from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import apple  # noqa: E402
from ci_workflows.apple_contract import build_plan  # noqa: E402
from ci_workflows.apple_execution import (  # noqa: E402
    CommandOutcome,
    _simulator_device_name,
    assert_zero_apple_residue,
    cleanup_apple_state,
    execute_apple_plan,
    select_simulator,
)
from ci_workflows.apple_types import AppleProfile, AppleStage  # noqa: E402

GOOD_UDID = "11111111-2222-3333-4444-555555555555"
SECOND_UDID = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"


class FakeRunner:
    def __init__(
        self,
        *,
        xcode_version: str = "26.6",
        xcode_build: str = "17F113",
        swift_version: str = "6.3.3",
        sdk_versions: Mapping[str, str] | None = None,
        runtime_available: bool = True,
        device_types_available: bool = True,
        devices: Sequence[Mapping[str, object]] = (),
        fail_token: str | None = None,
        mutate_path: Path | None = None,
        retain_deleted_device: bool = False,
    ) -> None:
        self.xcode_version = xcode_version
        self.xcode_build = xcode_build
        self.swift_version = swift_version
        self.sdk_versions = dict(
            sdk_versions
            or {
                "iphoneos": "26.5",
                "iphonesimulator": "26.5",
                "appletvos": "26.5",
                "appletvsimulator": "26.5",
                "macosx": "26.5",
            }
        )
        self.runtime_available = runtime_available
        self.device_types_available = device_types_available
        self.devices = list(devices)
        self.fail_token = fail_token
        self.mutate_path = mutate_path
        self.retain_deleted_device = retain_deleted_device
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome:
        command = tuple(argv)
        self.calls.append(command)
        joined = " ".join(command)
        if self.fail_token and self.fail_token in joined:
            return CommandOutcome(9, "", "failed")
        if command == ("xcodebuild", "-version"):
            return CommandOutcome(
                0,
                f"Xcode {self.xcode_version}\nBuild version {self.xcode_build}\n",
                "",
            )
        if command == ("swift", "--version"):
            return CommandOutcome(
                0,
                f"Apple Swift version {self.swift_version} (swiftlang-test)\n",
                "",
            )
        if command[:2] == ("xcrun", "--sdk"):
            sdk = command[2]
            value = self.sdk_versions.get(sdk)
            if value is None:
                return CommandOutcome(1, "", "missing sdk")
            return CommandOutcome(0, f"{value}\n", "")
        if command == ("xcrun", "simctl", "list", "runtimes", "-j"):
            rows = []
            if self.runtime_available:
                rows = [
                    {
                        "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
                        "version": "26.5",
                        "isAvailable": True,
                    },
                    {
                        "identifier": "com.apple.CoreSimulator.SimRuntime.tvOS-26-5",
                        "version": "26.5",
                        "isAvailable": True,
                    },
                ]
            return CommandOutcome(0, json.dumps({"runtimes": rows}), "")
        if command == ("xcrun", "simctl", "list", "devicetypes", "-j"):
            rows = []
            if self.device_types_available:
                rows = [
                    {
                        "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
                        "name": "iPhone 17 Pro",
                        "productFamily": "iPhone",
                    },
                    {
                        "identifier": (
                            "com.apple.CoreSimulator.SimDeviceType."
                            "Apple-TV-4K-3rd-generation-4K"
                        ),
                        "name": "Apple TV 4K (3rd generation)",
                        "productFamily": "Apple TV",
                    },
                ]
            return CommandOutcome(0, json.dumps({"devicetypes": rows}), "")
        if command in {
            (
                "xcrun",
                "simctl",
                "list",
                "devices",
                "available",
                "-j",
            ),
            ("xcrun", "simctl", "list", "devices", "-j"),
        }:
            return CommandOutcome(
                0,
                json.dumps(
                    {
                        "devices": {
                            "com.apple.CoreSimulator.SimRuntime.iOS-26-5": self.devices,
                            "com.apple.CoreSimulator.SimRuntime.tvOS-26-5": self.devices,
                        }
                    }
                ),
                "",
            )
        if command[:3] == ("xcrun", "simctl", "create"):
            self.devices.append(
                {
                    "name": command[3],
                    "udid": GOOD_UDID,
                    "state": "Shutdown",
                    "isAvailable": True,
                    "deviceTypeIdentifier": command[4],
                }
            )
            return CommandOutcome(0, f"{GOOD_UDID}\n", "")
        if command[:3] == ("xcrun", "simctl", "delete"):
            if not self.retain_deleted_device:
                self.devices = [
                    row for row in self.devices if row.get("udid") != command[3]
                ]
            return CommandOutcome(0, "", "")
        if self.mutate_path is not None and command[0] in {"swift", "xcodebuild"}:
            self.mutate_path.write_text("mutated\n", encoding="utf-8")
        return CommandOutcome(0, "ok\n", "")


class AppleValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = apple.load_apple_contract(ROOT)

    def request(
        self,
        profile: AppleProfile,
        *,
        repository: str = "StreamScapeTV/ci-workflows",
        consumer: str = "ciw-apple-smoke",
        sha: str = "a" * 40,
        trust: str = "trusted-pr",
        exception: str | None = None,
    ) -> apple.AppleValidationRequest:
        return apple.AppleValidationRequest(
            repository=repository,
            admitted_sha=sha,
            consumer_contract=consumer,
            validation_profile=profile,
            source_trust=trust,
            artifact_exception_id=exception,
        )

    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        shutil.copytree(ROOT / "contracts", root / "contracts")
        shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
        (root / "AGENTS.md").write_text("fixture rules\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "CIW"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "ciw@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        return temporary, root, sha

    def mutated_contract(self, mutate) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "contracts").mkdir()
        raw = json.loads((ROOT / "contracts" / "apple-validation.json").read_text())
        mutate(raw)
        (root / "contracts" / "apple-validation.json").write_text(
            json.dumps(raw),
            encoding="utf-8",
        )
        temporary.root = root  # type: ignore[attr-defined]
        return temporary

    def test_contract_has_exact_public_api_and_profiles(self) -> None:
        self.assertEqual(self.contract["workflow_api"], "validation.apple")
        self.assertEqual(self.contract["contract_version"], "1.0.0")
        self.assertEqual(self.contract["stable_check_name"], "CI / Apple validation")
        self.assertEqual(self.contract["execution_runner_profile"], "apple")
        self.assertEqual(
            set(self.contract["profiles"]),
            {profile.value for profile in AppleProfile},
        )

    def test_plan_is_deterministic_and_uses_semantic_apple(self) -> None:
        request = self.request(AppleProfile.IOS_SIMULATOR)
        first = build_plan(self.contract, request)
        second = build_plan(self.contract, request)
        self.assertEqual(first, second)
        self.assertEqual(first.runner_profile.value, "apple")
        self.assertEqual(first.planner_runner_profile.value, "portable")
        self.assertEqual(first.planning_outputs(), second.planning_outputs())

    def test_untrusted_fork_is_rejected_before_execution(self) -> None:
        with self.assertRaisesRegex(apple.AppleValidationError, "source_trust_rejected"):
            build_plan(
                self.contract,
                self.request(AppleProfile.IOS_SIMULATOR, trust="untrusted-fork"),
            )

    def test_forbidden_public_inputs_are_rejected(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "INPUT_ADMITTED_SHA": "a" * 40,
            "INPUT_VALIDATION_PROFILE": "macos",
            "INPUT_COMMAND_PROFILE": "ciw-apple-smoke",
        }
        for name in (
            "INPUT_RUNNER",
            "INPUT_ARBITRARY_COMMAND",
            "INPUT_SECRET_NAME",
            "INPUT_DEPLOYMENT",
            "INPUT_PHYSICAL_DEVICE",
            "INPUT_SIGNING_IDENTITY",
        ):
            with self.subTest(name=name):
                env = {**base, name: "attacker-controlled"}
                with self.assertRaisesRegex(apple.AppleValidationError, "forbidden_input"):
                    apple.request_from_environment(env, self.contract)

    def test_contract_matching_project_scheme_configuration_and_test_plan(self) -> None:
        base = self.request(AppleProfile.IOS_SIMULATOR)
        for field, value, code in (
            ("working_directory", "elsewhere", "working_directory_rejected"),
            ("project_path", "Other.xcodeproj", "container_invalid"),
            ("scheme", "Other", "scheme_rejected"),
            ("configuration", "Release", "configuration_rejected"),
            ("test_plan", "Other.xctestplan", "test_plan_rejected"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(apple.AppleValidationError, code):
                    build_plan(self.contract, replace(base, **{field: value}))

    def test_path_traversal_and_symlink_escape_are_rejected(self) -> None:
        with self.assertRaisesRegex(apple.AppleValidationError, "invalid_input"):
            apple.safe_relative("../escape")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"outside-{root.name}"
            outside.mkdir()
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(apple.AppleValidationError, "path_rejected"):
                    apple.bounded_path(root, "link/value")
            finally:
                shutil.rmtree(outside)

    def test_xcode_and_swift_identity_parsing(self) -> None:
        self.assertEqual(
            apple.parse_xcode_identity("Xcode 26.6\nBuild version 17F113\n"),
            ("26.6", "17F113"),
        )
        self.assertEqual(
            apple.parse_swift_identity("Apple Swift version 6.3.3 (swiftlang)"),
            "6.3.3",
        )
        for output in ("Xcode unknown", "Build version 17F113"):
            with self.assertRaisesRegex(
                apple.AppleValidationError,
                "toolchain_identity_invalid",
            ):
                apple.parse_xcode_identity(output)

    def test_exact_xcode_and_swift_mismatch_fail_closed(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(
            self.contract,
            self.request(AppleProfile.MACOS, sha=sha),
        )
        for runner in (
            FakeRunner(xcode_version="26.5"),
            FakeRunner(xcode_build="17F112"),
            FakeRunner(swift_version="6.3.2"),
        ):
            with self.subTest(runner=runner):
                with self.assertRaisesRegex(apple.AppleValidationError, "toolchain_mismatch"):
                    execute_apple_plan(
                        plan=plan,
                        source_root=source,
                        state_root=source.parent / "state",
                        runner=runner,
                        environment={},
                    )

    def test_required_sdk_present_and_missing(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.MACOS, sha=sha))
        result = execute_apple_plan(
            plan=plan,
            source_root=source,
            state_root=source.parent / "state-good",
            runner=FakeRunner(),
            environment={},
        )
        self.assertEqual(result.sdk_versions["macosx"], "26.5")
        with self.assertRaisesRegex(apple.AppleValidationError, "sdk_missing"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state-missing",
                runner=FakeRunner(sdk_versions={"macosx": "26.5"}),
                environment={},
            )

    def test_deterministic_simulator_creation_and_redacted_identity(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.IOS_SIMULATOR, sha=sha))
        runner = FakeRunner()
        result = execute_apple_plan(
            plan=plan,
            source_root=source,
            state_root=source.parent / "state",
            runner=runner,
            environment={},
        )
        self.assertTrue(result.simulator_identity.startswith("sim-"))
        self.assertNotIn(GOOD_UDID, result.output_values()["simulator_identity"])
        self.assertTrue(any(call[:3] == ("xcrun", "simctl", "create") for call in runner.calls))
        self.assertIn(("xcrun", "simctl", "delete", GOOD_UDID), runner.calls)
        create = next(
            call for call in runner.calls if call[:3] == ("xcrun", "simctl", "create")
        )
        self.assertEqual(
            create[4],
            "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
        )
        self.assertTrue(create[3].startswith("CIW Apple Validation iPhone "))

    def test_booted_or_shutdown_unowned_simulator_is_rejected(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.IOS_SIMULATOR, sha=sha))
        for state in ("Booted", "Shutdown"):
            state_root = source.parent / f"state-{state}"
            device_name = _simulator_device_name(plan, state_root)
            runner = FakeRunner(
                devices=[
                    {
                        "name": device_name,
                        "udid": GOOD_UDID,
                        "state": state,
                        "isAvailable": True,
                        "deviceTypeIdentifier": (
                            "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
                        ),
                    }
                ]
            )
            with self.subTest(state=state):
                with self.assertRaisesRegex(apple.AppleValidationError, "simulator_unowned"):
                    execute_apple_plan(
                        plan=plan,
                        source_root=source,
                        state_root=state_root,
                        runner=runner,
                        environment={},
                    )

    def test_multiple_or_malformed_simulators_are_rejected(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.IOS_SIMULATOR, sha=sha))
        multiple_state = source.parent / "state-multiple"
        device_name = _simulator_device_name(plan, multiple_state)
        multiple = [
            {
                "name": device_name,
                "udid": GOOD_UDID,
                "state": "Shutdown",
                "isAvailable": True,
                "deviceTypeIdentifier": (
                    "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
                ),
            },
            {
                "name": device_name,
                "udid": SECOND_UDID,
                "state": "Shutdown",
                "isAvailable": True,
                "deviceTypeIdentifier": (
                    "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
                ),
            },
        ]
        with self.assertRaisesRegex(apple.AppleValidationError, "simulator_ambiguous"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=multiple_state,
                runner=FakeRunner(devices=multiple),
                environment={},
            )
        malformed_state = source.parent / "state-malformed"
        malformed_name = _simulator_device_name(plan, malformed_state)
        malformed = [
            {
                "name": malformed_name,
                "udid": "not-a-udid",
                "state": "Shutdown",
                "isAvailable": True,
                "deviceTypeIdentifier": (
                    "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
                ),
            }
        ]
        with self.assertRaisesRegex(apple.AppleValidationError, "simulator_malformed"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=malformed_state,
                runner=FakeRunner(devices=malformed),
                environment={},
            )

    def test_missing_runtime_is_rejected(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.IOS_SIMULATOR, sha=sha))
        with self.assertRaisesRegex(apple.AppleValidationError, "runtime_missing"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state",
                runner=FakeRunner(runtime_available=False),
                environment={},
            )

    def test_missing_or_wrong_simulator_device_family_is_rejected(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(
            self.contract,
            self.request(AppleProfile.IOS_SIMULATOR, sha=sha),
        )
        with self.assertRaisesRegex(
            apple.AppleValidationError,
            "simulator_contract_invalid",
        ):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state-no-device-type",
                runner=FakeRunner(device_types_available=False),
                environment={},
            )
        temporary_contract = self.mutated_contract(
            lambda raw: raw["simulators"]["ciw-ios"].__setitem__(
                "device_family",
                "Apple TV",
            )
        )
        self.addCleanup(temporary_contract.cleanup)
        with self.assertRaisesRegex(
            apple.AppleValidationError,
            "simulator_contract_invalid",
        ):
            apple.load_apple_contract(temporary_contract.root)  # type: ignore[attr-defined]

    def test_physical_or_generic_destination_contract_is_rejected(self) -> None:
        for platform in ("iOS", "generic/platform=iOS", "physical-device"):
            with self.subTest(platform=platform):
                temporary = self.mutated_contract(
                    lambda raw, platform=platform: raw["simulators"]["ciw-ios"].__setitem__(
                        "platform",
                        platform,
                    )
                )
                self.addCleanup(temporary.cleanup)
                with self.assertRaisesRegex(apple.AppleValidationError, "unsafe_destination"):
                    apple.load_apple_contract(temporary.root)  # type: ignore[attr-defined]

    def test_signing_archive_store_notarization_and_keychain_are_rejected(self) -> None:
        tokens = (
            "CODE_SIGNING_ALLOWED=YES",
            "archive",
            "exportArchive",
            "testflight",
            "notarytool",
            "security import",
        )
        for token in tokens:
            with self.subTest(token=token):
                temporary = self.mutated_contract(
                    lambda raw, token=token: raw["tasks"]["ciw-macos-smoke"]["commands"][0][
                        "fixed_arguments"
                    ].append(token)
                )
                self.addCleanup(temporary.cleanup)
                with self.assertRaisesRegex(apple.AppleValidationError, "forbidden_operation"):
                    apple.load_apple_contract(temporary.root)  # type: ignore[attr-defined]

    def test_invalid_scheme_configuration_and_test_plan_contracts(self) -> None:
        mutations = (
            ("scheme", "../../bad", "scheme_rejected"),
            ("configuration", "\nDebug", "configuration_rejected"),
            ("test_plan", "Tests/Plan.json", "test_plan_rejected"),
        )
        for field, value, code in mutations:
            with self.subTest(field=field):
                temporary = self.mutated_contract(
                    lambda raw, field=field, value=value: raw["tasks"]["ciw-macos-smoke"][
                        "container"
                    ].__setitem__(field, value)
                )
                self.addCleanup(temporary.cleanup)
                with self.assertRaisesRegex(apple.AppleValidationError, code):
                    apple.load_apple_contract(temporary.root)  # type: ignore[attr-defined]

    def test_project_symlink_escape_is_rejected(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        project = source / "tests/fixtures/apple-validation/smoke-project/AppleValidationSmoke.xcodeproj"
        shutil.rmtree(project)
        outside = source.parent / f"outside-project-{source.name}"
        outside.mkdir()
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        project.symlink_to(outside, target_is_directory=True)
        subprocess.run(["git", "add", "-A"], cwd=source, check=True)
        subprocess.run(["git", "commit", "-qm", "symlink fixture"], cwd=source, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        plan = build_plan(self.contract, self.request(AppleProfile.MACOS, sha=sha))
        with self.assertRaisesRegex(apple.AppleValidationError, "path_rejected|container_invalid"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state",
                runner=FakeRunner(),
                environment={},
            )

    def test_package_resolution_lock_mutation_is_rejected(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name)
        (source / "apple").mkdir()
        (source / "apple/Package.swift").write_text("// package\n", encoding="utf-8")
        resolved = source / "apple/Package.resolved"
        resolved.write_text("locked\n", encoding="utf-8")
        plan = build_plan(
            self.contract,
            self.request(
                AppleProfile.SWIFT_PACKAGE,
                repository="StreamScapeTV/streamscape-media",
                consumer="streamscape-media-apple",
            ),
        )
        plan = replace(
            plan,
            container=replace(
                plan.container,
                package_resolution_mode="locked",
                resolved_files=("apple/Package.resolved",),
            ),
            protected_paths=("apple/Package.swift",),
        )
        with self.assertRaisesRegex(
            apple.AppleValidationError,
            "package_resolution_mutation",
        ):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state-lock",
                runner=FakeRunner(mutate_path=resolved),
                environment={},
            )

    def test_checked_in_dependency_preparation_script_rejects_symlink(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name)
        (source / "native").mkdir()
        (source / "native/versions.lock.json").write_text("{}\n", encoding="utf-8")
        (source / "scripts/ci").mkdir(parents=True)
        outside = source.parent / f"outside-{source.name}.sh"
        outside.write_text("#!/bin/sh\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        (source / "scripts/ci/run-validation-scope.sh").symlink_to(outside)
        (source / "scripts/ci/prepare-apple-mpv.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        plan = build_plan(
            self.contract,
            self.request(
                AppleProfile.NATIVE_DEPENDENCY_PREPARATION,
                repository="StreamScapeTV/streamscape-media",
                consumer="streamscape-media-apple",
            ),
        )
        with self.assertRaisesRegex(apple.AppleValidationError, "path_rejected|script_rejected"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state-native",
                runner=FakeRunner(),
                environment={},
            )

    def test_command_failure_propagates_without_shell_masking(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.MACOS, sha=sha))
        with self.assertRaisesRegex(apple.AppleValidationError, "xcodebuild_failed"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state",
                runner=FakeRunner(fail_token="AppleValidationSmoke.xcodeproj"),
                environment={},
            )

    def test_cleanup_failure_preserves_primary_execution_failure(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(
            self.contract,
            self.request(AppleProfile.IOS_SIMULATOR, sha=sha),
        )
        with self.assertRaises(apple.AppleValidationError) as captured:
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state-primary-and-cleanup",
                runner=FakeRunner(
                    fail_token="AppleValidationSmoke.xcodeproj",
                    retain_deleted_device=True,
                ),
                environment={},
            )
        self.assertEqual(captured.exception.code, "xcodebuild_failed")
        self.assertTrue(captured.exception.cleanup_failed)

    def test_dirty_tree_is_rejected(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        (source / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        plan = build_plan(self.contract, self.request(AppleProfile.MACOS, sha=sha))
        with self.assertRaisesRegex(apple.AppleValidationError, "dirty_source"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state",
                runner=FakeRunner(),
                environment={},
            )

    def test_artifact_exception_allow_and_deny(self) -> None:
        allowed = build_plan(
            self.contract,
            self.request(
                AppleProfile.MACOS,
                exception="redacted-xcresult-diagnostics",
            ),
        )
        self.assertEqual(allowed.artifact_exception_id, "redacted-xcresult-diagnostics")
        with self.assertRaisesRegex(apple.AppleValidationError, "artifact_exception_rejected"):
            build_plan(
                self.contract,
                self.request(AppleProfile.MACOS, exception="not-registered"),
            )

    def test_cleanup_unlinks_symlink_without_following_outside_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as state_dir:
            source = Path(source_dir)
            state = Path(state_dir)
            outside = source.parent / f"sentinel-{source.name}"
            outside.mkdir()
            sentinel = outside / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            try:
                (source / "build").symlink_to(outside, target_is_directory=True)
                plan = build_plan(self.contract, self.request(AppleProfile.MACOS))
                plan = replace(plan, cleanup_paths=("build",))
                cleanup_apple_state(source, state, plan, runner=FakeRunner(), environment={})
                self.assertFalse((source / "build").exists())
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
                assert_zero_apple_residue(source, state, plan)
            finally:
                shutil.rmtree(outside)

    def test_cleanup_failure_does_not_accept_unregistered_path(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as state_dir:
            source = Path(source_dir)
            state = Path(state_dir)
            plan = build_plan(self.contract, self.request(AppleProfile.MACOS))
            plan = replace(plan, cleanup_paths=("../outside",))
            with self.assertRaisesRegex(apple.AppleValidationError, "cleanup_failed"):
                cleanup_apple_state(source, state, plan, runner=FakeRunner(), environment={})

    def test_cleanup_fails_when_created_simulator_residue_remains(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(
            self.contract,
            self.request(AppleProfile.IOS_SIMULATOR, sha=sha),
        )
        with self.assertRaisesRegex(apple.AppleValidationError, "cleanup_failed"):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=source.parent / "state-simulator-residue",
                runner=FakeRunner(retain_deleted_device=True),
                environment={},
            )

    def test_output_projection_is_redacted_and_deterministic(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        plan = build_plan(self.contract, self.request(AppleProfile.IOS_SIMULATOR, sha=sha))
        result = execute_apple_plan(
            plan=plan,
            source_root=source,
            state_root=source.parent / "state",
            runner=FakeRunner(),
            environment={},
        )
        outputs = result.output_values()
        self.assertEqual(outputs["result"], "success")
        self.assertEqual(outputs["cleanup_result"], "success")
        self.assertEqual(outputs["clean_tree"], "true")
        self.assertNotIn(GOOD_UDID, json.dumps(outputs))
        self.assertEqual(len(outputs["evidence_id"]), 64)
        self.assertIn(AppleStage.CLEANUP, result.completed_stages)


if __name__ == "__main__":
    unittest.main()
