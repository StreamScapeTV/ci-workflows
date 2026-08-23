from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import apple_execution
from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_multistage import (
    assert_zero_protected_full_residue,
    build_protected_full_plan,
    cleanup_protected_full,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
REPOSITORY = "StreamScapeTV/ci-workflows"


def stage(
    identifier: str,
    platform: str,
    *,
    operation: str = "build-for-testing",
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
        "xcodebuild_arguments": [],
        "test_selectors": [],
        "expected_outputs": [],
        "cleanup_paths": [],
    }


def raw_plan(*rows: dict[str, object]) -> str:
    return json.dumps({"stages": list(rows)}, separators=(",", ":"))


class AppleProtectedFullCleanupPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_apple_contract(ROOT)

    def build(self, *rows: dict[str, object]):
        return build_protected_full_plan(
            raw_plan(*rows),
            repository=REPOSITORY,
            admitted_sha=SHA,
            source_trust="trusted-pr",
            contract=self.contract,
        )

    def test_compile_only_platform_metadata_is_not_a_runtime_simulator_plan(self) -> None:
        plan = self.build(
            stage("ios-compile", "ios"),
            stage("tvos-compile", "tvos"),
            stage("macos-host", "macos", operation="test"),
        )

        self.assertEqual(plan.simulator_plans, ())
        self.assertFalse(any(row.needs_booted_simulator for row in plan.stages))
        self.assertIsNotNone(plan.stages[0].plan.simulator)
        self.assertIsNotNone(plan.stages[1].plan.simulator)

    def test_compile_only_cleanup_never_enters_simulator_lifecycle(self) -> None:
        plan = self.build(
            stage("ios-compile", "ios"),
            stage("tvos-compile", "tvos"),
            stage("macos-host", "macos", operation="test"),
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            state = root / "state"
            source.mkdir()
            state.mkdir()
            with (
                mock.patch.object(apple_execution, "_simulator_ownership") as ownership,
                mock.patch.object(apple_execution, "_cleanup_simulator_locked") as cleanup,
                mock.patch.object(apple_execution, "_device_inventory") as inventory,
            ):
                cleanup_protected_full(
                    plan,
                    source_root=source,
                    state_root=state,
                    environment={},
                )
                assert_zero_protected_full_residue(
                    plan,
                    source_root=source,
                    state_root=state,
                    environment={},
                )

        ownership.assert_not_called()
        cleanup.assert_not_called()
        inventory.assert_not_called()

    def test_lower_level_runtime_plan_still_retains_simulator_primitive(self) -> None:
        plan = self.build(stage("ios-runtime", "ios", operation="test"))

        self.assertTrue(plan.stages[0].needs_booted_simulator)
        self.assertEqual(len(plan.simulator_plans), 1)
        self.assertIsNotNone(plan.simulator_plans[0].simulator)


if __name__ == "__main__":
    unittest.main()
