from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ci_workflows import ciw_android
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.language_primitives import JavaRuntime, OperationResult

SHA = "a" * 40


class FakeResourceSampler:
    def __init__(self) -> None:
        self.result = SimpleNamespace(
            child_cpu_ms=17,
            wall_ms=29,
            peak_memory_bytes=123456,
            peak_processes=4,
            measurement_source="test-sampler",
        )

    def __enter__(self) -> FakeResourceSampler:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class AndroidProtectedFullCompileGroupTests(unittest.TestCase):
    def test_compile_group_runs_after_pre_unit_and_before_unit(self) -> None:
        plan = ciw_android._protected_full_plan(
            {
                "pre_unit_tasks": [":app:kspDebugKotlin"],
                "compile_tasks": [":app:compileDebugKotlin"],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "gradle", "tasks": [":app:verifyRoomSchemas"]},
            },
            "validation_plan_invalid",
        )

        self.assertEqual(plan.pre_unit_tasks, (":app:kspDebugKotlin",))
        self.assertEqual(plan.compile_tasks, (":app:compileDebugKotlin",))
        self.assertEqual(
            [name for name, tasks in plan.gradle_groups if tasks],
            ["pre_unit", "compile", "unit", "lint", "assemble", "schema"],
        )
        self.assertEqual(
            plan.gradle_tasks,
            (
                ":app:kspDebugKotlin",
                ":app:compileDebugKotlin",
                ":app:testDebugUnitTest",
                ":app:lintDebug",
                ":app:assembleDebug",
                ":app:verifyRoomSchemas",
            ),
        )

    def test_execute_uses_five_fresh_gradle_groups_and_reports_them(self) -> None:
        protected = ciw_android.ProtectedFullPlan(
            unit_tasks=(":app:testDebugUnitTest",),
            lint_tasks=(":app:lintDebug",),
            assemble_tasks=(":app:assembleDebug",),
            schema_mode="none",
            pre_unit_tasks=(":app:kspDebugKotlin",),
            compile_tasks=(":app:compileDebugKotlin",),
        )
        request = ciw_android.AndroidPrimitiveRequest(
            admitted_sha=SHA,
            validation_scope="protected-full",
            working_directory=".",
            gradle_wrapper_path="gradlew",
            gradle_tasks=(),
            targeted_test_selector="",
            script=None,
            protected_full=protected,
            private_dependency_repository="",
            private_dependency_sha="",
            private_dependency_subdirectory=".",
            private_dependency_id="",
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            source = root / "source"
            state.mkdir()
            source.mkdir()
            copied = state / "tmp/android-source"
            environment = {
                "PATH": "/runner/bin",
                "HOME": str(root / "home"),
                "GRADLE_USER_HOME": str(root / "gradle"),
                "TMPDIR": str(root / "tmp"),
                "GRADLE_RO_DEP_CACHE": "/opt/gradle-ro-cache",
            }
            runtime = JavaRuntime(
                Path("/runner/java"),
                "25",
                25,
                OperationResult("java.inspect", 0, "", ""),
            )
            context = CIWContext(root, {}, io.StringIO(), io.StringIO())

            def copy_source(_source: Path, destination: Path) -> None:
                destination.mkdir(parents=True)

            gradle = mock.Mock(return_value=OperationResult("android.protected_full", 0, "", ""))
            with (
                mock.patch.object(ciw_android, "AndroidResourceSampler", return_value=FakeResourceSampler()),
                mock.patch.object(ciw_android, "_state_root", return_value=state),
                mock.patch.object(ciw_android, "_source_root", return_value=source),
                mock.patch.object(ciw_android.android_execution, "verify_exact_source"),
                mock.patch.object(ciw_android.android_execution, "copy_source", side_effect=copy_source),
                mock.patch.object(ciw_android, "_bounded_existing_directory", return_value=copied),
                mock.patch.object(ciw_android, "_dependency_path", return_value=None),
                mock.patch.object(ciw_android, "_runtime_environment", return_value=environment),
                mock.patch.object(ciw_android, "resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch.object(ciw_android, "inspect_java_runtime", return_value=runtime),
                mock.patch.object(ciw_android, "validate_java_runtime"),
                mock.patch.object(ciw_android, "run_gradle_tasks", gradle),
                mock.patch.object(
                    ciw_android.time,
                    "monotonic_ns",
                    side_effect=[
                        0,
                        1_000_000,
                        2_000_000,
                        4_000_000,
                        5_000_000,
                        8_000_000,
                        9_000_000,
                        13_000_000,
                        14_000_000,
                        19_000_000,
                    ],
                ),
            ):
                result = ciw_android._execute_request(request, argparse.Namespace(), context)

        self.assertEqual(
            [call.kwargs["operation"] for call in gradle.call_args_list],
            [
                "android.protected_full.pre_unit",
                "android.protected_full.compile",
                "android.protected_full.unit",
                "android.protected_full.lint",
                "android.protected_full.assemble",
            ],
        )
        self.assertEqual(
            [call.args[1] for call in gradle.call_args_list],
            [
                (":app:kspDebugKotlin",),
                (":app:compileDebugKotlin",),
                (":app:testDebugUnitTest",),
                (":app:lintDebug",),
                (":app:assembleDebug",),
            ],
        )
        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual(summary["gradle_invocations"], 5)
        self.assertEqual(summary["gradle_wall_ms"], 15)
        self.assertEqual(summary["task_count"], 5)
        self.assertEqual(summary["gradle_dependency_cache_mode"], "read-only-seed")

    def test_legacy_plan_omitting_compile_group_is_unchanged(self) -> None:
        plan = ciw_android._protected_full_plan(
            {
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
            },
            "validation_plan_invalid",
        )

        self.assertEqual(plan.pre_unit_tasks, ())
        self.assertEqual(plan.compile_tasks, ())
        self.assertEqual(
            [name for name, tasks in plan.gradle_groups if tasks],
            ["unit", "lint", "assemble"],
        )

    def test_empty_compile_group_fails_closed(self) -> None:
        with self.assertRaises(CIWError) as failure:
            ciw_android._protected_full_plan(
                {
                    "compile_tasks": [],
                    "unit_tasks": [":app:testDebugUnitTest"],
                    "lint_tasks": [":app:lintDebug"],
                    "assemble_tasks": [":app:assembleDebug"],
                    "schema": {"mode": "none"},
                },
                "validation_plan_invalid",
            )
        self.assertEqual(failure.exception.code, "validation_plan_invalid")

    def test_compile_task_cannot_duplicate_any_other_group(self) -> None:
        duplicate_cases = (
            {
                "pre_unit_tasks": [":app:compileDebugKotlin"],
                "compile_tasks": [":app:compileDebugKotlin"],
            },
            {
                "compile_tasks": [":app:testDebugUnitTest"],
            },
            {
                "compile_tasks": [":app:lintDebug"],
            },
            {
                "compile_tasks": [":app:assembleDebug"],
            },
        )
        for extra in duplicate_cases:
            with self.subTest(extra=extra):
                value = {
                    "unit_tasks": [":app:testDebugUnitTest"],
                    "lint_tasks": [":app:lintDebug"],
                    "assemble_tasks": [":app:assembleDebug"],
                    "schema": {"mode": "none"},
                    **extra,
                }
                with self.assertRaises(CIWError) as failure:
                    ciw_android._protected_full_plan(value, "validation_plan_invalid")
                self.assertEqual(failure.exception.code, "validation_plan_invalid")


if __name__ == "__main__":
    unittest.main()
