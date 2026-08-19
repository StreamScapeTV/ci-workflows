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


class AndroidProtectedFullGroupTests(unittest.TestCase):
    def _request(
        self,
        *,
        schema_mode: str,
        schema_tasks: tuple[str, ...] = (),
        schema_script: ciw_android.ScriptPlan | None = None,
        pre_unit_tasks: tuple[str, ...] = (),
    ) -> ciw_android.AndroidPrimitiveRequest:
        return ciw_android.AndroidPrimitiveRequest(
            admitted_sha=SHA,
            validation_scope="protected-full",
            working_directory=".",
            gradle_wrapper_path="gradlew",
            gradle_tasks=(),
            targeted_test_selector="",
            script=None,
            protected_full=ciw_android.ProtectedFullPlan(
                unit_tasks=(":app:testDebugUnitTest",),
                lint_tasks=(":app:lintDebug",),
                assemble_tasks=(":app:kspDebugKotlin", ":app:assembleDebug"),
                schema_mode=schema_mode,
                schema_tasks=schema_tasks,
                schema_script=schema_script,
                pre_unit_tasks=pre_unit_tasks,
            ),
            private_dependency_repository="",
            private_dependency_sha="",
            private_dependency_subdirectory=".",
            private_dependency_id="",
        )

    def _execute(
        self,
        request: ciw_android.AndroidPrimitiveRequest,
        *,
        clock: list[int],
    ) -> tuple[dict[str, object], mock.Mock, mock.Mock, dict[str, str]]:
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
            context = CIWContext(
                root,
                {},
                io.StringIO(),
                io.StringIO(),
            )

            def copy_source(_source: Path, destination: Path) -> None:
                destination.mkdir(parents=True)

            gradle = mock.Mock(return_value=OperationResult("android.protected_full", 0, "", ""))
            script = mock.Mock()
            with (
                mock.patch.object(ciw_android, "AndroidResourceSampler", return_value=FakeResourceSampler()),
                mock.patch.object(ciw_android, "_state_root", return_value=state),
                mock.patch.object(ciw_android, "_source_root", return_value=source),
                mock.patch.object(ciw_android.android_execution, "verify_exact_source") as verify_source,
                mock.patch.object(ciw_android.android_execution, "copy_source", side_effect=copy_source),
                mock.patch.object(ciw_android, "_bounded_existing_directory", return_value=copied),
                mock.patch.object(ciw_android, "_dependency_path", return_value=None),
                mock.patch.object(ciw_android, "_runtime_environment", return_value=environment),
                mock.patch.object(ciw_android, "resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch.object(ciw_android, "inspect_java_runtime", return_value=runtime),
                mock.patch.object(ciw_android, "validate_java_runtime"),
                mock.patch.object(ciw_android, "run_gradle_tasks", gradle),
                mock.patch.object(ciw_android, "_execute_script", script),
                mock.patch.object(ciw_android.time, "monotonic_ns", side_effect=clock),
            ):
                result = ciw_android._execute_request(request, argparse.Namespace(), context)

            self.assertEqual(verify_source.call_count, 2)
            summary = json.loads(result.outputs["test_summary"])
            return summary, gradle, script, environment

    def test_protected_full_reclaims_daemon_between_ordered_gradle_groups(self) -> None:
        summary, gradle, script, environment = self._execute(
            self._request(schema_mode="gradle", schema_tasks=(":app:verifySchema",)),
            clock=[
                0,
                1_000_000,
                2_000_000,
                4_000_000,
                5_000_000,
                8_000_000,
                9_000_000,
                13_000_000,
            ],
        )

        self.assertEqual(
            [call.args[1] for call in gradle.call_args_list],
            [
                (":app:testDebugUnitTest",),
                (":app:lintDebug",),
                (":app:kspDebugKotlin", ":app:assembleDebug"),
                (":app:verifySchema",),
            ],
        )
        self.assertEqual(
            [call.kwargs["operation"] for call in gradle.call_args_list],
            [
                "android.protected_full.unit",
                "android.protected_full.lint",
                "android.protected_full.assemble",
                "android.protected_full.schema",
            ],
        )
        for call in gradle.call_args_list:
            self.assertEqual(call.args[0], Path("gradlew"))
            self.assertEqual(call.kwargs["options"], ("--no-daemon",))
            self.assertIs(call.kwargs["environment"], environment)
        script.assert_not_called()
        self.assertEqual(summary["execution_model"], "single-executor")
        self.assertEqual(summary["gradle_dependency_cache_mode"], "read-only-seed")
        self.assertEqual(summary["gradle_invocations"], 4)
        self.assertEqual(summary["gradle_wall_ms"], 10)
        self.assertEqual(summary["task_count"], 5)
        self.assertEqual(summary["schema_mode"], "gradle")

    def test_optional_pre_unit_group_runs_first_and_reclaims_before_unit(self) -> None:
        summary, gradle, script, environment = self._execute(
            self._request(
                schema_mode="none",
                pre_unit_tasks=(":app:prepareKspInputs",),
            ),
            clock=[
                0,
                1_000_000,
                2_000_000,
                4_000_000,
                5_000_000,
                8_000_000,
                9_000_000,
                13_000_000,
            ],
        )

        self.assertEqual(
            [call.args[1] for call in gradle.call_args_list],
            [
                (":app:prepareKspInputs",),
                (":app:testDebugUnitTest",),
                (":app:lintDebug",),
                (":app:kspDebugKotlin", ":app:assembleDebug"),
            ],
        )
        self.assertEqual(
            [call.kwargs["operation"] for call in gradle.call_args_list],
            [
                "android.protected_full.pre_unit",
                "android.protected_full.unit",
                "android.protected_full.lint",
                "android.protected_full.assemble",
            ],
        )
        for call in gradle.call_args_list:
            self.assertEqual(call.kwargs["options"], ("--no-daemon",))
            self.assertIs(call.kwargs["environment"], environment)
        script.assert_not_called()
        self.assertEqual(summary["gradle_invocations"], 4)
        self.assertEqual(summary["gradle_wall_ms"], 10)
        self.assertEqual(summary["task_count"], 5)
        self.assertEqual(summary["schema_mode"], "none")

    def test_pre_unit_plan_is_optional_and_duplicate_safe(self) -> None:
        legacy = ciw_android._protected_full_plan(
            {
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
            },
            "validation_plan_invalid",
        )
        self.assertEqual(legacy.pre_unit_tasks, ())
        self.assertEqual(
            [name for name, tasks in legacy.gradle_groups if tasks],
            ["unit", "lint", "assemble"],
        )

        isolated = ciw_android._protected_full_plan(
            {
                "pre_unit_tasks": [":app:kspDebugKotlin"],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
            },
            "validation_plan_invalid",
        )
        self.assertEqual(isolated.pre_unit_tasks, (":app:kspDebugKotlin",))
        self.assertEqual(
            isolated.gradle_tasks,
            (
                ":app:kspDebugKotlin",
                ":app:testDebugUnitTest",
                ":app:lintDebug",
                ":app:assembleDebug",
            ),
        )

        for invalid in (
            {
                "pre_unit_tasks": [],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
            },
            {
                "pre_unit_tasks": [":app:testDebugUnitTest"],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
            },
            {
                "pre_unit_tasks": [":app:kspDebugKotlin"],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "none"},
                "pre_unit_options": ["--max-workers=1"],
            },
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CIWError) as failure:
                    ciw_android._protected_full_plan(invalid, "validation_plan_invalid")
                self.assertEqual(failure.exception.code, "validation_plan_invalid")

    def test_schema_script_does_not_create_a_phantom_gradle_group(self) -> None:
        plan = ciw_android.ScriptPlan("scripts/verify-schema.sh", ("--check",))
        summary, gradle, script, environment = self._execute(
            self._request(schema_mode="script", schema_script=plan),
            clock=[
                0,
                1_000_000,
                2_000_000,
                4_000_000,
                5_000_000,
                8_000_000,
                9_000_000,
                13_000_000,
            ],
        )

        self.assertEqual(len(gradle.call_args_list), 3)
        self.assertEqual(
            [call.kwargs["operation"] for call in gradle.call_args_list],
            [
                "android.protected_full.unit",
                "android.protected_full.lint",
                "android.protected_full.assemble",
            ],
        )
        script.assert_called_once()
        self.assertEqual(script.call_args.args[0], plan)
        self.assertIs(script.call_args.kwargs["environment"], environment)
        self.assertEqual(summary["gradle_invocations"], 3)
        self.assertEqual(summary["script_invocations"], 1)
        self.assertEqual(summary["gradle_wall_ms"], 6)
        self.assertEqual(summary["script_wall_ms"], 4)
        self.assertEqual(summary["task_count"], 4)
        self.assertEqual(summary["schema_mode"], "script")


if __name__ == "__main__":
    unittest.main()
