from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Sequence
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import flutter
from ci_workflows import flutter_execution
from ci_workflows.flutter_contract import (
    EXPECTED_COMMAND_FIELDS,
    EXPECTED_COMMAND_IDS,
    EXPECTED_CONSUMER_FIELDS,
    EXPECTED_CONSUMER_IDS,
    EXPECTED_CONSUMER_PROFILE_FIELDS,
    EXPECTED_FAILURE_CODES,
    EXPECTED_FORBIDDEN_INPUTS,
    EXPECTED_GENERATION_FIELDS,
    EXPECTED_PROFILE_FIELDS,
    EXPECTED_SETUP_FIELDS,
    EXPECTED_TOOLCHAIN_FIELDS,
    EXPECTED_TOOLCHAIN_IDS,
    FlutterValidationError,
    build_plan,
    generated_flutter_files,
    parse_jdk_identity,
    validate_contract,
)
from ci_workflows.flutter_execution import (
    CommandOutcome,
    CommandRunner,
    _verify_expected_outputs,
    assert_zero_flutter_residue,
    bind_pub_cache,
    snapshot_persistent_pub_cache,
    terminal_cleanup_flutter_state,
    verify_persistent_pub_cache,
)
from ci_workflows.flutter_types import FlutterProfile, FlutterRequest, RunnerCapability

SHA = "a" * 40


def runtime(version: str) -> dict[str, object]:
    return json.loads(
        (ROOT / f"tests/fixtures/flutter-validation/runtime-{version}.json").read_text(
            encoding="utf-8"
        )
    )


def request(profile: str, consumer: str = "synthetic-smoke") -> FlutterRequest:
    repositories = {
        "directus-canonical": "StreamScapeTV/directus-front",
        "finance-embedded-web": "StreamScapeTV/finance-hub",
        "synthetic-smoke": "StreamScapeTV/ci-workflows",
    }
    return FlutterRequest(
        repositories[consumer], SHA, consumer, FlutterProfile(profile), "trusted-pr"
    )


class FakeRunner(CommandRunner):
    def __init__(self, identity: Mapping[str, object], *, fail: str = "") -> None:
        self.identity = identity
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []
        self.pub_caches: list[str] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        command = tuple(argv)
        self.calls.append(command)
        if command and command[0] in {"flutter", "dart"}:
            self.pub_caches.append(env.get("PUB_CACHE", ""))
        if self.fail and self.fail in " ".join(command):
            return CommandOutcome(17, "", "synthetic failure")
        if command == ("java", "-XshowSettings:properties", "-version"):
            return CommandOutcome(
                0,
                "",
                "\n".join(
                    (
                        f"    java.version = {self.identity['javaVersion']}",
                        f"    java.runtime.version = {self.identity['javaRuntimeVersion']}",
                        f"    java.vendor = {self.identity['javaVendor']}",
                    )
                ),
            )
        if command == ("javac", "-version"):
            return CommandOutcome(0, f"javac {self.identity['javacVersion']}\n", "")
        if command == ("flutter", "--version", "--machine"):
            return CommandOutcome(0, json.dumps(self.identity), "")
        if command[:3] == ("flutter", "build", "apk"):
            target = cwd / "build/app/outputs/flutter-apk/app-debug.apk"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"apk")
        return CommandOutcome(0, "", "")


class FlutterValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = flutter.load_flutter_contract(ROOT)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name)

    def assert_code(self, code: str, operation) -> FlutterValidationError:
        with self.assertRaises(FlutterValidationError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def source(self, *, version: str = "3.41.4", gradle: str = "8.14") -> Path:
        source = self.temp / f"source-{len(list(self.temp.glob('source-*')))}"
        source.mkdir()
        (source / "pubspec.yaml").write_text("name: repair\n", encoding="utf-8")
        (source / "pubspec.lock").write_text("packages: {}\n", encoding="utf-8")
        wrapper = source / "android/gradle/wrapper/gradle-wrapper.properties"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text(
            f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{gradle}-all.zip\n",
            encoding="utf-8",
        )
        if version == "3.44.6":
            (source / ".fvmrc").write_text(
                json.dumps({"flutter": version}) + "\n", encoding="utf-8"
            )
        return source

    def environment(self, state: Path, persistent: Path | None = None) -> dict[str, str]:
        persistent = persistent or self.temp / "persistent-pub"
        persistent.mkdir(parents=True, exist_ok=True)
        (persistent / "sentinel").write_text("unchanged", encoding="utf-8")
        snapshot_persistent_pub_cache(state, {"PUB_CACHE": str(persistent)})
        github_env = self.temp / "github-env"
        github_env.write_text("", encoding="utf-8")
        bound = bind_pub_cache(state, {"GITHUB_ENV": str(github_env)})
        return {
            "PUB_CACHE": bound["pub_cache_path"],
            "JAVA_HOME": "/opt/contract-jdk",
            "GITHUB_RUN_NUMBER": "12",
        }

    def test_generated_bytes_and_complete_inventory(self) -> None:
        expected_paths = {
            ROOT / "contracts/flutter-validation.json",
            ROOT / "tests/fixtures/flutter-validation/runtime-3.41.4.json",
            ROOT / "tests/fixtures/flutter-validation/runtime-3.44.6.json",
        }
        self.assertEqual(expected_paths, set(generated_flutter_files(ROOT)))
        self.assertEqual((), flutter.generate_flutter_contract_files(ROOT, check=True))
        self.assertEqual(EXPECTED_TOOLCHAIN_IDS, set(self.contract["toolchains"]))
        self.assertEqual(EXPECTED_COMMAND_IDS, set(self.contract["commands"]))
        self.assertEqual(EXPECTED_CONSUMER_IDS, set(self.contract["consumer_contracts"]))
        self.assertEqual(EXPECTED_FAILURE_CODES, set(self.contract["failure_codes"]))
        self.assertEqual(EXPECTED_FORBIDDEN_INPUTS, set(self.contract["forbidden_inputs"]))
        self.assertEqual(EXPECTED_GENERATION_FIELDS, set(self.contract["generation"]))
        self.assertEqual(EXPECTED_SETUP_FIELDS, set(self.contract["setup"]))
        for row in self.contract["toolchains"].values():
            self.assertEqual(EXPECTED_TOOLCHAIN_FIELDS, set(row))
        for row in self.contract["profiles"].values():
            self.assertEqual(EXPECTED_PROFILE_FIELDS, set(row))
        for row in self.contract["commands"].values():
            self.assertEqual(EXPECTED_COMMAND_FIELDS, set(row))
        for consumer in self.contract["consumer_contracts"].values():
            self.assertEqual(EXPECTED_CONSUMER_FIELDS, set(consumer))
            for profile in consumer["profiles"].values():
                self.assertEqual(EXPECTED_CONSUMER_PROFILE_FIELDS, set(profile))

    def test_check_mode_reports_byte_drift(self) -> None:
        fixture = ROOT / "tests/fixtures/flutter-validation/runtime-3.41.4.json"
        original = fixture.read_bytes()
        try:
            fixture.write_bytes(original.rstrip(b"\n"))
            self.assert_code(
                "generated_contract_drift",
                lambda: flutter.generate_flutter_contract_files(ROOT, check=True),
            )
        finally:
            fixture.write_bytes(original)
        self.assertEqual((), flutter.generate_flutter_contract_files(ROOT, check=True))

    def test_contract_rejects_missing_fields_ids_and_mutable_jdk(self) -> None:
        mutations: list[dict[str, object]] = []
        missing_toolchain = copy.deepcopy(self.contract)
        missing_toolchain["toolchains"].pop("3.41.4")
        mutations.append(missing_toolchain)
        missing_command = copy.deepcopy(self.contract)
        missing_command["commands"].pop("pub-restore")
        mutations.append(missing_command)
        missing_consumer = copy.deepcopy(self.contract)
        missing_consumer["consumer_contracts"].pop("synthetic-smoke")
        mutations.append(missing_consumer)
        mutable_jdk = copy.deepcopy(self.contract)
        mutable_jdk["setup"]["jdk_action"] = "actions/setup-java@v5"
        mutations.append(mutable_jdk)
        mutable_jdk_selector = copy.deepcopy(self.contract)
        mutable_jdk_selector["toolchains"]["3.41.4"]["jdk_version"] = "21"
        mutations.append(mutable_jdk_selector)
        caller_jdk = copy.deepcopy(self.contract)
        caller_jdk["setup"]["caller_jdk"] = True
        mutations.append(caller_jdk)
        for mutation in mutations:
            with self.subTest(mutation=list(mutation)):
                self.assert_code("contract_invalid", lambda m=mutation: validate_contract(m))

    def test_mobile_profiles_have_one_exact_flutter_dart_gradle_jdk_tuple(self) -> None:
        for profile in ("quality", "canonical-gate", "android-debug", "compatibility-smoke"):
            plan = build_plan(self.contract, request(profile), None)
            self.assertIs(plan.runner_profile, RunnerCapability.MOBILE)
            self.assertIn("jdk-verify", [stage.value for stage in plan.stages])
            self.assertEqual("3.41.4", plan.toolchain.flutter_version)
            self.assertEqual("3.11.1", plan.toolchain.dart_version)
            self.assertEqual("8.14", plan.toolchain.gradle_version)
            self.assertEqual("temurin", plan.toolchain.jdk_distribution)
            self.assertEqual("21.0.8+9.0.LTS", plan.toolchain.jdk_version)
            self.assertEqual("21.0.8+9-LTS", plan.toolchain.java_runtime_version)
        self.assertEqual(
            "21.0.8",
            parse_jdk_identity(
                "java.version = 21.0.8\njava.runtime.version = 21.0.8+9-LTS\njava.vendor = Eclipse Adoptium",
                "javac 21.0.8",
                build_plan(self.contract, request("quality"), None).toolchain,
            )["java_version"],
        )
        self.assert_code(
            "jdk_mismatch",
            lambda: parse_jdk_identity(
                "java.version = 25\njava.runtime.version = 25+36\njava.vendor = Eclipse Adoptium",
                "javac 25",
                build_plan(self.contract, request("quality"), None).toolchain,
            ),
        )

    def test_pub_cache_is_bound_under_registered_state_and_persistent_cache_is_unchanged(self) -> None:
        state = self.temp / "state"
        state.mkdir()
        persistent = self.temp / "persistent"
        environment = self.environment(state, persistent)
        expected = state / "flutter-validation/pub-cache"
        self.assertEqual(expected, Path(environment["PUB_CACHE"]))
        self.assertEqual("true", verify_persistent_pub_cache(state)["persistent_pub_cache_unchanged"])
        (persistent / "sentinel").write_text("changed", encoding="utf-8")
        self.assert_code(
            "persistent_pub_cache_changed", lambda: verify_persistent_pub_cache(state)
        )

    def test_wrong_or_symlinked_pub_cache_fails_before_flutter(self) -> None:
        source = self.source()
        state = self.temp / "state"
        state.mkdir()
        plan = build_plan(self.contract, request("quality"), source)
        runner = FakeRunner(runtime("3.41.4"))
        self.assert_code(
            "pub_cache_rejected",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=state,
                request=plan.request,
                phase="execute",
                environment={"PUB_CACHE": "/tmp/wrong", "JAVA_HOME": "/opt/jdk"},
                runner=runner,
            ),
        )
        self.assertEqual([], runner.calls)
        environment = self.environment(state)
        cache = Path(environment["PUB_CACHE"])
        cache.rmdir()
        cache.symlink_to(self.temp)
        self.assert_code(
            "pub_cache_rejected",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=state,
                request=plan.request,
                phase="execute",
                environment=environment,
                runner=runner,
            ),
        )

    def test_every_flutter_command_uses_isolated_pub_cache_and_exact_gradle(self) -> None:
        source = self.source()
        state = self.temp / "state"
        state.mkdir()
        environment = self.environment(state)
        runner = FakeRunner(runtime("3.41.4"))
        result = flutter.validate(
            contract_root=ROOT,
            source_root=source,
            state_root=state,
            request=request("android-debug"),
            phase="execute",
            environment=environment,
            runner=runner,
        )
        self.assertEqual("success", result.status)
        self.assertEqual("8.14", result.gradle_version)
        self.assertTrue(runner.pub_caches)
        self.assertEqual({environment["PUB_CACHE"]}, set(runner.pub_caches))
        wrong = self.source(gradle="8.13")
        wrong_state = self.temp / "wrong-state"
        wrong_state.mkdir()
        wrong_env = self.environment(wrong_state)
        self.assert_code(
            "gradle_mismatch",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=wrong,
                state_root=wrong_state,
                request=request("android-debug"),
                phase="execute",
                environment=wrong_env,
                runner=FakeRunner(runtime("3.41.4")),
            ),
        )

    def test_safe_missing_output_is_output_missing_and_unsafe_types_are_path_rejected(self) -> None:
        source = self.source()
        self.assert_code(
            "output_missing",
            lambda: _verify_expected_outputs(source, ["build/missing.apk"]),
        )
        outside = self.temp / "outside"
        outside.write_text("outside", encoding="utf-8")
        target = source / "build/link.apk"
        target.parent.mkdir()
        target.symlink_to(outside)
        self.assert_code(
            "path_rejected", lambda: _verify_expected_outputs(source, ["build/link.apk"])
        )
        fifo = source / "build/output.fifo"
        os.mkfifo(fifo)
        self.assert_code(
            "path_rejected", lambda: _verify_expected_outputs(source, ["build/output.fifo"])
        )
        self.assert_code(
            "invalid_input", lambda: _verify_expected_outputs(source, ["../escape"])
        )

    def test_primary_failure_is_preserved_when_cleanup_fails(self) -> None:
        source = self.source()
        state = self.temp / "state"
        state.mkdir()
        residue = state / "flutter-validation"
        residue.mkdir()
        os.mkfifo(residue / "blocked")
        error = self.assert_code(
            "command_failed",
            lambda: terminal_cleanup_flutter_state(
                source, state, primary_failure_code="command_failed"
            ),
        )
        self.assertEqual("command_failed", error.primary_code)
        self.assertEqual("cleanup_failed", error.cleanup_code)
        self.assertEqual(
            {
                "result": "failure",
                "failure_code": "command_failed",
                "primary_failure_code": "command_failed",
                "cleanup_failure_code": "cleanup_failed",
                "cleanup_result": "failure",
            },
            error.output_values(),
        )

    def test_early_setup_failure_cleanup_preserves_primary_without_generated_source(self) -> None:
        source = self.temp / "source-not-created"
        state = self.temp / "state"
        state.mkdir()
        (state / "flutter-validation").mkdir()
        self.assertEqual(
            {
                "result": "failure",
                "failure_code": "command_failed",
                "primary_failure_code": "command_failed",
                "cleanup_failure_code": "",
                "cleanup_result": "success",
            },
            terminal_cleanup_flutter_state(
                source, state, primary_failure_code="command_failed"
            ),
        )
        assert_zero_flutter_residue(source, state)

    def test_cleanup_removes_nested_read_only_flutter_and_pub_state(self) -> None:
        source = self.source()
        state = self.temp / "state"
        state.mkdir()
        for root in (
            source / "build" / "read-only" / "nested",
            state / "flutter-validation" / "pub-cache" / "read-only" / "nested",
        ):
            root.mkdir(parents=True)
            generated = root / "generated"
            generated.write_text("generated\n", encoding="utf-8")
            generated.chmod(0o400)
            root.chmod(0o500)
            root.parent.chmod(0o500)
        self.assertEqual(
            {
                "result": "success",
                "failure_code": "",
                "primary_failure_code": "",
                "cleanup_failure_code": "",
                "cleanup_result": "success",
            },
            terminal_cleanup_flutter_state(source, state),
        )
        assert_zero_flutter_residue(source, state)

    def test_cleanup_stops_and_waits_for_the_state_scoped_gradle_daemon(self) -> None:
        source = self.source()
        state = self.temp / "state"
        flutter_state = state / "flutter-validation"
        flutter_state.mkdir(parents=True)
        active = f"123 GradleDaemon --gradle-user-home {flutter_state / 'gradle-home'}"
        with mock.patch.object(
            flutter_execution.subprocess,
            "run",
            side_effect=(
                subprocess.CompletedProcess([], 0, active, ""),
                subprocess.CompletedProcess([], 0, active, ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ),
        ) as command, mock.patch.object(
            flutter_execution.os, "kill"
        ) as kill, mock.patch.object(flutter_execution.time, "sleep") as sleep:
            terminal_cleanup_flutter_state(source, state)
        self.assertEqual(3, command.call_count)
        kill.assert_called_once_with(123, flutter_execution.signal.SIGTERM)
        sleep.assert_called_once_with(
            flutter_execution.GRADLE_DAEMON_CLEANUP_POLL_SECONDS
        )
        self.assertFalse(flutter_state.exists())

    def test_cleanup_rejects_a_state_scoped_gradle_daemon_after_grace_period(self) -> None:
        source = self.source()
        state = self.temp / "state"
        flutter_state = state / "flutter-validation"
        flutter_state.mkdir(parents=True)
        active = f"123 GradleDaemon --gradle-user-home {flutter_state / 'gradle-home'}"
        with mock.patch.object(
            flutter_execution.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, active, ""),
        ), mock.patch.object(flutter_execution.os, "kill") as kill, mock.patch.object(
            flutter_execution.time,
            "monotonic",
            side_effect=(0.0, flutter_execution.GRADLE_DAEMON_CLEANUP_GRACE_SECONDS),
        ), self.assertRaises(FlutterValidationError) as failure:
            terminal_cleanup_flutter_state(source, state)
        self.assertEqual("cleanup_failed", failure.exception.code)
        kill.assert_called_once_with(123, flutter_execution.signal.SIGTERM)
        self.assertTrue(flutter_state.exists())

    def test_cleanup_does_not_wait_for_a_gradle_daemon_outside_its_state(self) -> None:
        source = self.source()
        state = self.temp / "state"
        flutter_state = state / "flutter-validation"
        flutter_state.mkdir(parents=True)
        other_state = self.temp / "other-state" / "flutter-validation" / "gradle-home"
        active = f"123 GradleDaemon --gradle-user-home {other_state}"
        with mock.patch.object(
            flutter_execution.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, active, ""),
        ) as command, mock.patch.object(flutter_execution.os, "kill") as kill:
            terminal_cleanup_flutter_state(source, state)
        command.assert_called_once()
        kill.assert_not_called()
        self.assertFalse(flutter_state.exists())

    def test_source_audit_and_device_handoff_install_nothing(self) -> None:
        for profile in ("source-audit", "device-handoff"):
            plan = build_plan(self.contract, request(profile), None)
            self.assertFalse(plan.install_required)
            self.assertIs(plan.runner_profile, RunnerCapability.PORTABLE)
            self.assertEqual("21.0.8+9.0.LTS", plan.toolchain.jdk_version)


if __name__ == "__main__":
    unittest.main()
