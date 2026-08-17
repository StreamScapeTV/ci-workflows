from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ci_workflows import apple_execution, apple_plan_guard
from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_multistage import (
    build_protected_full_plan,
    execute_protected_full,
)
from ci_workflows.apple_types import AppleValidationError

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
REPOSITORY = "StreamScapeTV/ci-workflows"


def stage(
    identifier: str,
    platform: str,
    *,
    operation: str = "build",
    selectors: list[str] | None = None,
    arguments: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": identifier,
        "platform": platform,
        "operation": operation,
        "working_directory": ".",
        "container": {"kind": "project", "path": "App.xcodeproj"},
        "scheme": "App",
        "configuration": "Debug",
        "test_plan": "",
        "package_resolution_mode": "disabled",
        "resolved_files": [],
        "script": None,
        "xcodebuild_arguments": list(arguments or []),
        "test_selectors": list(selectors or []),
        "expected_outputs": [],
        "cleanup_paths": ["build"],
    }


def raw_plan(stages: list[dict[str, object]]) -> str:
    return json.dumps({"stages": stages}, separators=(",", ":"))


class AppleProtectedFullPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_apple_contract(ROOT)

    def build(self, stages: list[dict[str, object]], **dependency: str):
        return build_protected_full_plan(
            raw_plan(stages),
            repository=REPOSITORY,
            admitted_sha=SHA,
            source_trust="trusted-pr",
            contract=self.contract,
            **dependency,
        )

    def test_three_platform_compile_plan_uses_one_apple_workspace(self) -> None:
        plan = self.build(
            [
                stage("ios-build", "ios"),
                stage("tvos-build", "tvos"),
                stage("macos-build", "macos"),
            ]
        )
        self.assertEqual(
            [(row.platform, row.operation) for row in plan.stages],
            [("ios", "build"), ("tvos", "build"), ("macos", "build")],
        )
        self.assertFalse(any(row.needs_booted_simulator for row in plan.stages))
        self.assertEqual(plan.stages[0].plan.workspace_profile, "apple")
        self.assertEqual(plan.stages[1].plan.workspace_profile, "apple")
        self.assertEqual(plan.stages[2].plan.workspace_profile, "apple")
        self.assertIsNotNone(plan.stages[0].plan.simulator)
        self.assertIsNotNone(plan.stages[1].plan.simulator)
        self.assertIsNone(plan.stages[2].plan.simulator)
        outputs = plan.planning_outputs()
        self.assertEqual(outputs["runner_profile"], "apple")
        self.assertEqual(outputs["workspace_profile"], "apple")
        self.assertEqual(outputs["private_dependency_used"], "false")

    def test_test_selector_is_bounded_and_only_valid_for_test(self) -> None:
        plan = self.build(
            [stage("ios-test", "ios", operation="test", selectors=["AppTests/SmokeTests/testOne"])]
        )
        self.assertTrue(plan.stages[0].needs_booted_simulator)
        self.assertIn(
            "-only-testing:AppTests/SmokeTests/testOne",
            plan.stages[0].plan.commands[0].fixed_arguments,
        )
        with self.assertRaisesRegex(AppleValidationError, "test_selector_rejected"):
            self.build([stage("ios-build", "ios", selectors=["AppTests/SmokeTests/testOne"])])

    def test_signing_destination_and_archive_overrides_are_rejected(self) -> None:
        for value in (
            "CODE_SIGNING_ALLOWED=YES",
            "DEVELOPMENT_TEAM=ABC123",
            "-destination=platform=iOS Simulator",
            "-archivePath",
            "archive",
        ):
            with self.subTest(value=value):
                with self.assertRaises(AppleValidationError):
                    self.build([stage("ios-build", "ios", arguments=[value])])

    def test_public_guard_rejects_output_redirection_and_arbitrary_cleanup(self) -> None:
        safe = stage("ios-build", "ios", arguments=["ENABLE_TESTABILITY=YES"])
        apple_plan_guard.validate_protected_full_plan_json(raw_plan([safe]))
        for value in (
            "SYMROOT=/tmp/redirect",
            "CONFIGURATION_BUILD_DIR=/tmp/redirect",
            "OBJROOT=/tmp/redirect",
        ):
            with self.subTest(argument=value):
                with self.assertRaisesRegex(AppleValidationError, "forbidden_operation"):
                    apple_plan_guard.validate_protected_full_plan_json(
                        raw_plan([stage("ios-build", "ios", arguments=[value])])
                    )
        for value in (".git", "Sources", "App.xcodeproj"):
            guarded = stage("ios-build", "ios")
            guarded["cleanup_paths"] = [value]
            with self.subTest(cleanup=value):
                with self.assertRaisesRegex(AppleValidationError, "cleanup_failed"):
                    apple_plan_guard.validate_protected_full_plan_json(raw_plan([guarded]))

    def test_duplicate_stage_ids_and_oversized_plan_fail_closed(self) -> None:
        with self.assertRaisesRegex(AppleValidationError, "validation_plan_invalid"):
            self.build([stage("same", "ios"), stage("same", "tvos")])
        with self.assertRaisesRegex(AppleValidationError, "validation_plan_invalid"):
            self.build([stage(f"s{index}", "macos") for index in range(9)])

    def test_source_only_protected_full_is_rejected_to_preserve_general_lane(self) -> None:
        source_stage = {
            "id": "audit",
            "platform": "source",
            "operation": "script",
            "working_directory": ".",
            "container": None,
            "scheme": "",
            "configuration": "",
            "test_plan": "",
            "package_resolution_mode": "disabled",
            "resolved_files": [],
            "script": {"interpreter": "python3", "path": "scripts/check.py", "arguments": []},
            "xcodebuild_arguments": [],
            "test_selectors": [],
            "expected_outputs": [],
            "cleanup_paths": [],
        }
        with self.assertRaisesRegex(AppleValidationError, "validation_plan_invalid"):
            self.build([source_stage])

    def test_private_dependency_requires_complete_exact_identity(self) -> None:
        with self.assertRaisesRegex(AppleValidationError, "private_dependency_invalid"):
            self.build(
                [stage("macos-build", "macos")],
                private_dependency_repository="StreamScapeTV/streamscape-media",
            )
        plan = self.build(
            [stage("macos-build", "macos")],
            private_dependency_repository="StreamScapeTV/streamscape-media",
            private_dependency_sha="b" * 40,
            private_dependency_subdirectory="native/apple",
            private_dependency_id="media",
        )
        self.assertTrue(plan.private_dependency_used)
        self.assertEqual(plan.private_dependency_subdirectory, "native/apple")


class AppleProtectedFullExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_apple_contract(ROOT)

    def make_source(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        (source / "App.xcodeproj").mkdir()
        return source

    def build(self, rows: list[dict[str, object]]):
        return build_protected_full_plan(
            raw_plan(rows),
            repository=REPOSITORY,
            admitted_sha=SHA,
            source_trust="trusted-pr",
            contract=self.contract,
        )

    @staticmethod
    def toolchain_result(*_args, **_kwargs):
        return (
            "26.6",
            "17F113",
            "6.3.3",
            {
                "iphoneos": "26.5",
                "iphonesimulator": "26.5",
                "appletvos": "26.5",
                "appletvsimulator": "26.5",
                "macosx": "26.5",
            },
            None,
        )

    def test_compile_stages_share_build_state_without_booting_simulators(self) -> None:
        plan = self.build(
            [
                stage("ios-build", "ios"),
                stage("tvos-build", "tvos"),
                stage("macos-build", "macos"),
            ]
        )
        calls: list[tuple[str, str | None, Path, Path, Path]] = []

        def execute(plan, command, source, state, runner, env, directories, lease):
            calls.append(
                (
                    plan.task_profile,
                    lease.destination if lease is not None else None,
                    directories["derived-data"],
                    directories["swiftpm"],
                    directories["result-bundles"],
                )
            )
            return False

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            state = root / "state"
            state.mkdir()
            ownership = SimpleNamespace(rows=[])
            with (
                mock.patch.object(apple_execution, "verify_toolchain", side_effect=self.toolchain_result),
                mock.patch.object(apple_execution, "_execute_command", side_effect=execute),
                mock.patch.object(
                    apple_execution,
                    "_simulator_ownership",
                    return_value=nullcontext(ownership),
                ),
                mock.patch.object(apple_execution, "select_simulator") as select_simulator,
            ):
                outputs = execute_protected_full(
                    plan,
                    source_root=source,
                    state_root=state,
                    environment={},
                )
        self.assertEqual(outputs["result"], "success")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len({row[2] for row in calls}), 1, "DerivedData must be shared")
        self.assertEqual(len({row[3] for row in calls}), 1, "SwiftPM state must be shared")
        self.assertEqual(len({row[4] for row in calls}), 3, "result bundles must be stage-local")
        self.assertEqual(calls[0][1], "generic/platform=iOS Simulator")
        self.assertEqual(calls[1][1], "generic/platform=tvOS Simulator")
        self.assertIsNone(calls[2][1])
        select_simulator.assert_not_called()
        summary = json.loads(outputs["test_summary"])
        self.assertFalse(any(row["simulator_booted"] for row in summary))

    def test_only_test_stages_acquire_and_release_simulators(self) -> None:
        plan = self.build(
            [
                stage("ios-build", "ios"),
                stage("ios-test", "ios", operation="test"),
                stage("tvos-test", "tvos", operation="test"),
            ]
        )
        selected: list[str] = []
        cleaned: list[str] = []

        def select(plan, *_args, **_kwargs):
            selected.append(plan.task_profile)
            return apple_execution.SimulatorLease(
                udid="11111111-1111-1111-1111-111111111111",
                destination="platform=Simulator,id=11111111-1111-1111-1111-111111111111",
                redacted_identity="sim-test",
                created=True,
            )

        def cleanup(_source, _state, plan, **_kwargs):
            cleaned.append(plan.task_profile)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            state = root / "state"
            state.mkdir()
            ownership = SimpleNamespace(rows=[])
            with (
                mock.patch.object(apple_execution, "verify_toolchain", side_effect=self.toolchain_result),
                mock.patch.object(apple_execution, "_execute_command", return_value=False),
                mock.patch.object(
                    apple_execution,
                    "_simulator_ownership",
                    return_value=nullcontext(ownership),
                ),
                mock.patch.object(apple_execution, "select_simulator", side_effect=select),
                mock.patch.object(apple_execution, "_cleanup_simulator_locked", side_effect=cleanup),
            ):
                execute_protected_full(
                    plan,
                    source_root=source,
                    state_root=state,
                    environment={},
                )
        self.assertEqual(selected, ["protected-ios-test", "protected-tvos-test"])
        self.assertEqual(cleaned, selected)


class AppleProtectedFullWorkflowShapeTests(unittest.TestCase):
    def test_reusable_and_smoke_use_one_heavy_executor(self) -> None:
        reusable = (ROOT / ".github/workflows/reusable-apple.yml").read_text(encoding="utf-8")
        smoke = (ROOT / ".github/workflows/apple-validation-smoke.yml").read_text(encoding="utf-8")
        self.assertEqual(
            reusable.count("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"),
            1,
        )
        self.assertEqual(
            smoke.count("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"),
            1,
        )
        self.assertNotIn("Real iOS simulator smoke", smoke)
        self.assertNotIn("Real tvOS simulator smoke", smoke)
        self.assertNotIn("Real unsigned macOS smoke", smoke)
        self.assertIn("Real protected-full Apple smoke", smoke)
        self.assertIn("validation_scope: protected-full", smoke)
        self.assertNotIn('"operation":"test"', smoke)

    def test_reusable_has_one_checkout_workspace_dependency_and_terminal_cleanup(self) -> None:
        reusable = (ROOT / ".github/workflows/reusable-apple.yml").read_text(encoding="utf-8")
        adapter = (ROOT / "src/ci_workflows/ciw_apple.py").read_text(encoding="utf-8")
        self.assertEqual(reusable.count("actions/exact-checkout@"), 1)
        self.assertEqual(reusable.count("actions/prepare-workspace@"), 1)
        self.assertEqual(reusable.count("actions/checkout-private-dependency@"), 1)
        self.assertEqual(reusable.count("actions/cleanup-workspace@"), 1)
        self.assertEqual(reusable.count("phase: execute"), 1)
        self.assertEqual(reusable.count("phase: cleanup"), 1)
        self.assertEqual(reusable.count("phase: residue"), 1)
        self.assertIn("apple_plan_guard.validate_protected_full_plan_json", adapter)
        self.assertNotIn("actions/cache", reusable.lower())
        self.assertNotIn("upload-artifact", reusable.lower())


if __name__ == "__main__":
    unittest.main()
