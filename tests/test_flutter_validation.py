from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import flutter
from ci_workflows.flutter_contract import (
    FlutterValidationError,
    build_plan,
    discover_flutter_pin,
    parse_runtime_identity,
    validate_contract,
)
from ci_workflows.flutter_execution import CommandOutcome, CommandRunner
from ci_workflows.flutter_types import (
    FlutterProfile,
    FlutterRequest,
    RunnerCapability,
)

FIXTURES = ROOT / "tests" / "fixtures" / "flutter-validation"
SHA = "a" * 40
REPOSITORIES = {
    "directus-canonical": "StreamScapeTV/directus-front",
    "finance-embedded-web": "StreamScapeTV/finance-hub",
    "synthetic-smoke": "StreamScapeTV/ci-workflows",
}


class FakeRunner(CommandRunner):
    def __init__(
        self,
        runtime: Mapping[str, object],
        *,
        fail_at: str = "",
        mutate_lock: bool = False,
    ) -> None:
        self.runtime = runtime
        self.fail_at = fail_at
        self.mutate_lock = mutate_lock
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        command = tuple(argv)
        self.calls.append(command)
        joined = " ".join(command)
        if self.fail_at and self.fail_at in joined:
            return CommandOutcome(17, "", "synthetic failure")
        if command == ("flutter", "--version", "--machine"):
            return CommandOutcome(0, json.dumps(self.runtime), "")
        if self.mutate_lock and command[:3] == ("flutter", "pub", "get"):
            (cwd / "pubspec.lock").write_text("changed\n", encoding="utf-8")
        if command[:3] == ("flutter", "build", "apk"):
            filename = (
                "app-arm64-v8a-debug.apk"
                if "--split-per-abi" in command
                else "app-debug.apk"
            )
            target = cwd / "build/app/outputs/flutter-apk" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"apk")
        if command[:3] == ("flutter", "build", "appbundle"):
            target = cwd / "build/app/outputs/bundle/debug/app-debug.aab"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"aab")
        if command[:4] == ("flutter", "build", "ios", "--simulator"):
            target = cwd / "build/ios/iphonesimulator/Runner.app"
            target.mkdir(parents=True, exist_ok=True)
        if (
            command[:3] == ("flutter", "build", "ios")
            and "--no-codesign" in command
        ):
            target = cwd / "build/ios/iphoneos/Runner.app"
            target.mkdir(parents=True, exist_ok=True)
        return CommandOutcome(0, "", "")


def runtime(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def request(
    profile: str,
    consumer: str = "directus-canonical",
) -> FlutterRequest:
    return FlutterRequest(
        REPOSITORIES[consumer],
        SHA,
        consumer,
        FlutterProfile(profile),
        "trusted-pr",
    )


class FlutterValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = flutter.load_flutter_contract(ROOT)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name)
        self.copy_index = 0

    def fixture_copy(self, name: str) -> Path:
        self.copy_index += 1
        target = self.temp / f"{name}-{self.copy_index}"
        shutil.copytree(FIXTURES / name, target)
        return target

    def assert_code(self, code: str, operation) -> None:
        with self.assertRaises(FlutterValidationError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)

    def test_contract_is_deterministic_and_immutable_setup_is_pinned(self) -> None:
        first = json.dumps(
            self.contract,
            sort_keys=True,
            separators=(",", ":"),
        )
        second = json.dumps(
            flutter.load_flutter_contract(ROOT),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(first, second)
        action = self.contract["setup"]["action"]
        self.assertRegex(action, r"@[0-9a-f]{40}$")
        self.assertTrue(self.contract["setup"]["immutable"])
        self.assertFalse(self.contract["setup"]["caller_download_url"])
        self.assertFalse(self.contract["setup"]["caller_runtime"])

    def test_directus_exact_fvm_json_shape(self) -> None:
        pin = discover_flutter_pin(
            self.fixture_copy("directus"),
            self.contract["consumer_contracts"]["directus-canonical"],
        )
        self.assertEqual("3.44.6", pin.version)
        self.assertEqual((".fvmrc",), pin.sources)

    def test_finance_exact_fvm_json_shape(self) -> None:
        pin = discover_flutter_pin(
            self.fixture_copy("finance"),
            self.contract["consumer_contracts"]["finance-embedded-web"],
        )
        self.assertEqual("3.41.4", pin.version)
        self.assertEqual((".fvmrc",), pin.sources)

    def test_exact_contract_pin_is_bounded_to_reviewed_smoke_consumer(self) -> None:
        pin = discover_flutter_pin(
            self.fixture_copy("smoke"),
            self.contract["consumer_contracts"]["synthetic-smoke"],
        )
        self.assertEqual("3.41.4", pin.version)
        self.assertEqual(("contract",), pin.sources)

    def test_pin_malformed_range_alias_channel_and_multiple_values_fail(self) -> None:
        consumer = self.contract["consumer_contracts"]["finance-embedded-web"]
        values = (
            "not-json",
            '{"flutter":"^3.41.4"}',
            '{"flutter":"stable"}',
            '{"flutter":"3.41.4","channel":"stable"}',
            '{"flutter":["3.41.4","3.44.6"]}',
        )
        for value in values:
            with self.subTest(value=value):
                source = self.fixture_copy("finance")
                (source / ".fvmrc").write_text(value, encoding="utf-8")
                self.assert_code(
                    "invalid_runtime_source",
                    lambda: discover_flutter_pin(source, consumer),
                )
                shutil.rmtree(source)

    def test_pin_agreement_and_mismatch(self) -> None:
        source = self.fixture_copy("directus")
        (source / ".flutter-version").write_text(
            "3.44.6\n",
            encoding="utf-8",
        )
        pin = discover_flutter_pin(
            source,
            self.contract["consumer_contracts"]["directus-canonical"],
        )
        self.assertEqual((".flutter-version", ".fvmrc"), pin.sources)
        (source / ".flutter-version").write_text(
            "3.41.4\n",
            encoding="utf-8",
        )
        self.assert_code(
            "runtime_pin_mismatch",
            lambda: discover_flutter_pin(
                source,
                self.contract["consumer_contracts"]["directus-canonical"],
            ),
        )

    def test_symlinked_pin_and_escaped_gate_fail(self) -> None:
        source = self.fixture_copy("directus")
        pin = source / ".fvmrc"
        pin.unlink()
        pin.symlink_to(self.temp / "outside-version")
        (self.temp / "outside-version").write_text(
            '{"flutter":"3.44.6"}\n',
            encoding="utf-8",
        )
        self.assert_code(
            "path_rejected",
            lambda: discover_flutter_pin(
                source,
                self.contract["consumer_contracts"]["directus-canonical"],
            ),
        )
        modified = copy.deepcopy(self.contract)
        modified["consumer_contracts"]["directus-canonical"]["profiles"][
            "canonical-gate"
        ]["gate_path"] = "../outside.sh"
        self.assert_code(
            "path_rejected",
            lambda: build_plan(
                modified,
                request("canonical-gate"),
                source,
            ),
        )

    def test_repository_contract_binding_fails_closed(self) -> None:
        mismatched = FlutterRequest(
            "StreamScapeTV/finance-hub",
            SHA,
            "directus-canonical",
            FlutterProfile.QUALITY,
            "trusted-pr",
        )
        self.assert_code(
            "consumer_contract_rejected",
            lambda: build_plan(self.contract, mismatched, None),
        )

    def test_runtime_and_dart_identity_are_exact(self) -> None:
        toolchain = build_plan(
            self.contract,
            request("quality"),
            None,
        ).toolchain
        identity = parse_runtime_identity(
            json.dumps(runtime("runtime-3.44.6.json")),
            toolchain,
        )
        self.assertEqual("3.12.2", identity["dart_version"])
        bad = runtime("runtime-3.44.6.json")
        bad["dartSdkVersion"] = "3.12.1"
        self.assert_code(
            "dart_mismatch",
            lambda: parse_runtime_identity(json.dumps(bad), toolchain),
        )
        bad = runtime("runtime-3.44.6.json")
        bad["frameworkVersion"] = "3.44.5"
        self.assert_code(
            "runtime_mismatch",
            lambda: parse_runtime_identity(json.dumps(bad), toolchain),
        )

    def test_runner_separation_and_source_audit_no_install(self) -> None:
        cases = {
            "source-audit": RunnerCapability.PORTABLE,
            "device-handoff": RunnerCapability.PORTABLE,
            "quality": RunnerCapability.MOBILE,
            "canonical-gate": RunnerCapability.MOBILE,
            "android-debug": RunnerCapability.MOBILE,
            "compatibility-smoke": RunnerCapability.MOBILE,
            "ios-simulator": RunnerCapability.APPLE,
        }
        for profile, expected in cases.items():
            with self.subTest(profile=profile):
                plan = build_plan(self.contract, request(profile), None)
                self.assertEqual(expected, plan.runner_profile)
                if profile in {"source-audit", "device-handoff"}:
                    self.assertFalse(plan.install_required)
        self.assertNotEqual(
            build_plan(
                self.contract,
                request("android-debug"),
                None,
            ).runner_profile,
            build_plan(
                self.contract,
                request("ios-simulator"),
                None,
            ).runner_profile,
        )

    def test_quality_stage_order_and_optional_node_composition(self) -> None:
        plan = build_plan(self.contract, request("quality"), None)
        self.assertEqual(
            (
                "runtime-verify",
                "dependency-restore",
                "quality",
                "tests",
                "cleanup",
            ),
            tuple(stage.value for stage in plan.stages),
        )
        finance = build_plan(
            self.contract,
            request("quality", "finance-embedded-web"),
            None,
        )
        self.assertEqual(
            "source-audit",
            finance.node_composition["command_profile"],
        )
        self.assertEqual("22.16.0", finance.node_composition["node_version"])

    def test_android_and_ios_are_debug_unsigned_only(self) -> None:
        android = build_plan(self.contract, request("android-debug"), None)
        ios = build_plan(self.contract, request("ios-simulator"), None)
        android_text = " ".join(
            " ".join(command.argv) for command in android.commands
        )
        ios_text = " ".join(" ".join(command.argv) for command in ios.commands)
        self.assertIn("--debug", android_text)
        self.assertIn("--split-per-abi", android_text)
        self.assertIn("android-arm64", android_text)
        self.assertNotIn("--release", android_text)
        self.assertIn("pod install --deployment", ios_text)
        self.assertIn("--simulator", ios_text)
        self.assertIn("--debug", ios_text)
        self.assertNotIn("--release", ios_text)
        modified = copy.deepcopy(self.contract)
        modified["commands"]["ios-simulator-debug"]["argv"].append(
            "--release"
        )
        self.assert_code(
            "command_boundary_rejected",
            lambda: validate_contract(modified),
        )
        for token in ("TestFlight", "keychain", "provision", "deploy"):
            with self.subTest(token=token):
                changed = copy.deepcopy(self.contract)
                changed["commands"]["ios-simulator-debug"]["argv"].append(
                    token
                )
                self.assert_code(
                    "command_boundary_rejected",
                    lambda changed=changed: validate_contract(changed),
                )

    def test_checked_in_gate_and_device_handoff_are_bounded(self) -> None:
        source = self.fixture_copy("directus")
        gate = build_plan(
            self.contract,
            request("canonical-gate"),
            source,
        )
        self.assertEqual("tool/ci_gate.sh", gate.gate_path)
        handoff = build_plan(
            self.contract,
            request("device-handoff"),
            source,
        )
        self.assertEqual("deferred", handoff.device_handoff["execution"])
        self.assertFalse(handoff.install_required)
        self.assertEqual((), handoff.commands)

    def test_execution_success_lockfile_integrity_outputs_and_cleanup(self) -> None:
        source = self.fixture_copy("directus")
        result = flutter.validate(
            contract_root=ROOT,
            source_root=source,
            state_root=self.temp / "state",
            request=request("android-debug"),
            phase="execute",
            runner=FakeRunner(runtime("runtime-3.44.6.json")),
            environment={"GITHUB_RUN_NUMBER": "12"},
        )
        self.assertEqual("success", result.status)
        self.assertTrue(result.output_verified)
        self.assertTrue(result.clean_tree)
        self.assertEqual("success", result.cleanup_result)
        self.assertFalse((source / "build").exists())
        self.assertFalse((self.temp / "state/flutter-validation").exists())

    def test_lockfile_mutation_command_failure_dirty_source_and_cleanup_failure(
        self,
    ) -> None:
        source = self.fixture_copy("directus")
        self.assert_code(
            "lockfile_drift",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=self.temp / "state-lock",
                request=request("quality"),
                phase="execute",
                runner=FakeRunner(
                    runtime("runtime-3.44.6.json"),
                    mutate_lock=True,
                ),
            ),
        )
        source = self.fixture_copy("directus")
        self.assert_code(
            "command_failed",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=self.temp / "state-command",
                request=request("quality"),
                phase="execute",
                runner=FakeRunner(
                    runtime("runtime-3.44.6.json"),
                    fail_at="analyze",
                ),
            ),
        )
        source = self.fixture_copy("directus")
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=source,
            check=True,
        )
        (source / "untracked.txt").write_text("dirty", encoding="utf-8")
        self.assert_code(
            "dirty_source",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=self.temp / "state-dirty",
                request=request("source-audit"),
                phase="execute",
                runner=FakeRunner(runtime("runtime-3.44.6.json")),
            ),
        )
        residue = self.temp / "residue/flutter-validation"
        residue.mkdir(parents=True)
        self.assert_code(
            "cleanup_failed",
            lambda: flutter.assert_zero_flutter_residue(
                self.fixture_copy("directus"),
                self.temp / "residue",
            ),
        )

    def test_public_workflow_input_mapping_and_exact_optional_matches(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/directus-front",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "canonical-gate",
            "INPUT_COMMAND_PROFILE": "directus-canonical",
            "INPUT_VERSION_FILE": ".fvmrc",
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_SCRIPT_PATH": "tool/ci_gate.sh",
            "INPUT_PLATFORM": "flutter",
            "INPUT_ARTIFACT_EXCEPTION_ID": "",
            "INPUT_SOURCE_TRUST": "trusted-pr",
        }
        value = flutter.request_from_environment(base, self.contract)
        self.assertEqual("directus-canonical", value.consumer_contract)
        self.assertEqual("StreamScapeTV/directus-front", value.repository)
        for key, bad in (
            ("INPUT_VERSION_FILE", ".flutter-version"),
            ("INPUT_WORKING_DIRECTORY", "subdir"),
            ("INPUT_SCRIPT_PATH", "tool/other.sh"),
            ("INPUT_PLATFORM", "android"),
            ("INPUT_ARTIFACT_EXCEPTION_ID", "retain-output"),
        ):
            with self.subTest(key=key):
                changed = dict(base)
                changed[key] = bad
                self.assert_code(
                    "forbidden_input",
                    lambda changed=changed: flutter.request_from_environment(
                        changed,
                        self.contract,
                    ),
                )
        changed = dict(base)
        changed["INPUT_COMMAND_PROFILE"] = "finance-embedded-web"
        self.assert_code(
            "consumer_contract_rejected",
            lambda: flutter.request_from_environment(changed, self.contract),
        )

    def test_source_trust_is_derived_from_same_repository_event(self) -> None:
        event = self.temp / "event.json"
        event.write_text(
            json.dumps(
                {
                    "pull_request": {
                        "head": {
                            "repo": {
                                "full_name": "StreamScapeTV/ci-workflows"
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        environment = {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        }
        self.assertEqual(
            "trusted-pr",
            flutter.source_trust_from_environment(environment),
        )
        event.write_text(
            json.dumps(
                {
                    "pull_request": {
                        "head": {"repo": {"full_name": "external/fork"}}
                    }
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            "untrusted-fork",
            flutter.source_trust_from_environment(environment),
        )

    def test_output_missing_and_evidence_are_deterministic(self) -> None:
        class NoOutputRunner(FakeRunner):
            def run(self, argv, *, cwd, env):
                outcome = super().run(argv, cwd=cwd, env=env)
                if argv[:3] == ("flutter", "build", "apk"):
                    shutil.rmtree(cwd / "build", ignore_errors=True)
                return outcome

        source = self.fixture_copy("directus")
        self.assert_code(
            "output_missing",
            lambda: flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=self.temp / "state-output",
                request=request("android-debug"),
                phase="execute",
                runner=NoOutputRunner(runtime("runtime-3.44.6.json")),
            ),
        )
        evidence = []
        for index in range(2):
            source = self.fixture_copy("directus")
            result = flutter.validate(
                contract_root=ROOT,
                source_root=source,
                state_root=self.temp / f"state-evidence-{index}",
                request=request("quality"),
                phase="execute",
                runner=FakeRunner(runtime("runtime-3.44.6.json")),
            )
            evidence.append(result.evidence_id)
        self.assertEqual(evidence[0], evidence[1])

    def test_caller_selected_runner_device_engine_registry_and_deployment_fail(
        self,
    ) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/directus-front",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_CONSUMER_CONTRACT": "directus-canonical",
            "INPUT_VALIDATION_PROFILE": "quality",
            "INPUT_SOURCE_TRUST": "trusted-pr",
        }
        for key in (
            "RUNNER",
            "DEVICE",
            "ENGINE",
            "REGISTRY",
            "DEPLOYMENT",
            "DOWNLOAD_URL",
            "RUNTIME",
            "PACKAGE_MANAGER",
            "SHELL",
            "ARBITRARY_COMMAND",
        ):
            with self.subTest(key=key):
                environment = dict(base)
                environment[f"INPUT_{key}"] = "caller-value"
                self.assert_code(
                    "forbidden_input",
                    lambda environment=environment: (
                        flutter.request_from_environment(
                            environment,
                            self.contract,
                        )
                    ),
                )


if __name__ == "__main__":
    unittest.main()
