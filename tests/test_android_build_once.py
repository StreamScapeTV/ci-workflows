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

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class FakeResourceSampler:
    def __init__(self) -> None:
        self.result = SimpleNamespace(
            child_cpu_ms=11,
            wall_ms=23,
            peak_memory_bytes=456789,
            peak_processes=5,
            measurement_source="test-sampler",
        )

    def __enter__(self) -> FakeResourceSampler:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class AndroidBuildOnceTests(unittest.TestCase):
    def test_composite_action_hardcodes_combined_protected_full_default(self) -> None:
        action = (ROOT / "actions/validate-android/action.yml").read_text(encoding="utf-8")
        self.assertIn("INPUT_PROTECTED_FULL_EXECUTION_MODE: combined", action)
        self.assertNotIn("protected_full_execution_mode:\n", action)

    def test_central_default_resolves_existing_grouped_plan_to_combined_execution(self) -> None:
        context = CIWContext(
            ROOT,
            {
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "protected-full",
                "INPUT_VALIDATION_PLAN_JSON": self._plan(),
                "INPUT_PROTECTED_FULL_EXECUTION_MODE": "combined",
            },
            io.StringIO(),
            io.StringIO(),
        )
        request = ciw_android._request(self._args("plan"), context)
        self.assertIsNotNone(request.protected_full)
        assert request.protected_full is not None
        self.assertEqual(request.protected_full.execution_mode, "combined")
        self.assertEqual(
            request.protected_full.gradle_tasks,
            (
                ":app:kspDebugKotlin",
                ":app:compileDebugKotlin",
                ":app:testDebugUnitTest",
                ":app:lintDebug",
                ":app:assembleDebug",
                ":app:verifyRoomSchemas",
            ),
        )

    def test_explicit_grouped_fallback_overrides_central_default(self) -> None:
        payload = json.loads(self._plan())
        payload["execution_mode"] = "grouped"
        context = CIWContext(
            ROOT,
            {
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "protected-full",
                "INPUT_VALIDATION_PLAN_JSON": json.dumps(payload),
                "INPUT_PROTECTED_FULL_EXECUTION_MODE": "combined",
            },
            io.StringIO(),
            io.StringIO(),
        )
        request = ciw_android._request(self._args("plan"), context)
        assert request.protected_full is not None
        self.assertEqual(request.protected_full.execution_mode, "grouped")
        self.assertEqual(
            [name for name, tasks in request.protected_full.gradle_groups if tasks],
            ["pre_unit", "compile", "unit", "lint", "assemble", "schema"],
        )

    def test_explicit_prefix_isolated_fallback_overrides_combined_default(self) -> None:
        payload = json.loads(self._plan())
        payload["execution_mode"] = "prefix-isolated"
        context = CIWContext(
            ROOT,
            {
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "protected-full",
                "INPUT_VALIDATION_PLAN_JSON": json.dumps(payload),
                "INPUT_PROTECTED_FULL_EXECUTION_MODE": "combined",
            },
            io.StringIO(),
            io.StringIO(),
        )
        request = ciw_android._request(self._args("plan"), context)
        assert request.protected_full is not None
        self.assertEqual(request.protected_full.execution_mode, "prefix-isolated")
        self.assertEqual(request.protected_full.pre_unit_tasks, (":app:kspDebugKotlin",))
        self.assertEqual(request.protected_full.compile_tasks, (":app:compileDebugKotlin",))
        self.assertEqual(
            request.protected_full.remainder_gradle_tasks,
            (
                ":app:testDebugUnitTest",
                ":app:lintDebug",
                ":app:assembleDebug",
                ":app:verifyRoomSchemas",
            ),
        )

    def test_prefix_isolated_requires_caller_pre_unit_tasks(self) -> None:
        payload = json.loads(self._plan())
        payload.pop("pre_unit_tasks")
        payload["execution_mode"] = "prefix-isolated"
        context = CIWContext(
            ROOT,
            {
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "protected-full",
                "INPUT_VALIDATION_PLAN_JSON": json.dumps(payload),
                "INPUT_PROTECTED_FULL_EXECUTION_MODE": "combined",
            },
            io.StringIO(),
            io.StringIO(),
        )
        with self.assertRaises(CIWError) as failure:
            ciw_android._request(self._args("plan"), context)
        self.assertEqual(failure.exception.code, "validation_plan_invalid")

    def test_combined_runtime_submits_one_exact_caller_task_graph(self) -> None:
        protected = ciw_android.ProtectedFullPlan(
            unit_tasks=(":app:testDebugUnitTest",),
            lint_tasks=(":app:lintDebug",),
            assemble_tasks=(":app:assembleDebug",),
            schema_mode="gradle",
            schema_tasks=(":app:verifyRoomSchemas",),
            pre_unit_tasks=(":app:kspDebugKotlin",),
            compile_tasks=(":app:compileDebugKotlin",),
            execution_mode="combined",
        )
        request = self._request(protected)

        result, gradle = self._execute(
            request,
            monotonic_ns=[1_000_000, 8_000_000],
        )

        gradle.assert_called_once()
        call = gradle.call_args
        self.assertEqual(call.kwargs["operation"], "android.protected_full.combined")
        self.assertEqual(
            call.args[1],
            (
                ":app:kspDebugKotlin",
                ":app:compileDebugKotlin",
                ":app:testDebugUnitTest",
                ":app:lintDebug",
                ":app:assembleDebug",
                ":app:verifyRoomSchemas",
            ),
        )
        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual(summary["gradle_execution_mode"], "combined")
        self.assertEqual(summary["gradle_invocations"], 1)
        self.assertEqual(summary["gradle_wall_ms"], 7)
        self.assertEqual(summary["task_count"], 6)

    def test_prefix_isolated_runtime_runs_two_prefixes_then_one_remainder(self) -> None:
        protected = ciw_android.ProtectedFullPlan(
            unit_tasks=(":app:testDebugUnitTest",),
            lint_tasks=(":app:lintDebug",),
            assemble_tasks=(":app:assembleDebug",),
            schema_mode="gradle",
            schema_tasks=(":app:verifyRoomSchemas",),
            pre_unit_tasks=(":app:kspDebugKotlin",),
            compile_tasks=(":app:compileDebugKotlin",),
            execution_mode="prefix-isolated",
        )
        request = self._request(protected)

        result, gradle = self._execute(
            request,
            monotonic_ns=[
                1_000_000,
                4_000_000,
                5_000_000,
                9_000_000,
                10_000_000,
                16_000_000,
            ],
        )

        self.assertEqual(
            [call.kwargs["operation"] for call in gradle.call_args_list],
            [
                "android.protected_full.pre_unit",
                "android.protected_full.compile",
                "android.protected_full.remainder",
            ],
        )
        self.assertEqual(
            [call.args[1] for call in gradle.call_args_list],
            [
                (":app:kspDebugKotlin",),
                (":app:compileDebugKotlin",),
                (
                    ":app:testDebugUnitTest",
                    ":app:lintDebug",
                    ":app:assembleDebug",
                    ":app:verifyRoomSchemas",
                ),
            ],
        )
        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual(summary["gradle_execution_mode"], "prefix-isolated")
        self.assertEqual(summary["gradle_invocations"], 3)
        self.assertEqual(summary["gradle_wall_ms"], 13)
        self.assertEqual(summary["task_count"], 6)

    def test_prefix_isolated_runtime_skips_absent_compile_group(self) -> None:
        protected = ciw_android.ProtectedFullPlan(
            unit_tasks=(":app:testDebugUnitTest",),
            lint_tasks=(":app:lintDebug",),
            assemble_tasks=(":app:assembleDebug",),
            schema_mode="none",
            pre_unit_tasks=(":app:kspDebugKotlin",),
            execution_mode="prefix-isolated",
        )
        request = self._request(protected)

        result, gradle = self._execute(
            request,
            monotonic_ns=[1_000_000, 3_000_000, 4_000_000, 9_000_000],
        )

        self.assertEqual(
            [call.kwargs["operation"] for call in gradle.call_args_list],
            ["android.protected_full.pre_unit", "android.protected_full.remainder"],
        )
        self.assertEqual(
            [call.args[1] for call in gradle.call_args_list],
            [
                (":app:kspDebugKotlin",),
                (":app:testDebugUnitTest", ":app:lintDebug", ":app:assembleDebug"),
            ],
        )
        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual(summary["gradle_invocations"], 2)
        self.assertEqual(summary["gradle_wall_ms"], 7)

    @staticmethod
    def _request(protected: ciw_android.ProtectedFullPlan) -> ciw_android.AndroidPrimitiveRequest:
        return ciw_android.AndroidPrimitiveRequest(
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

    @staticmethod
    def _execute(
        request: ciw_android.AndroidPrimitiveRequest,
        *,
        monotonic_ns: list[int],
    ) -> tuple[object, mock.Mock]:
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

            gradle = mock.Mock(
                return_value=OperationResult("android.protected_full", 0, "", "")
            )
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
                mock.patch.object(ciw_android.time, "monotonic_ns", side_effect=monotonic_ns),
            ):
                result = ciw_android._execute_request(request, argparse.Namespace(), context)
        return result, gradle

    @staticmethod
    def _plan() -> str:
        return json.dumps(
            {
                "pre_unit_tasks": [":app:kspDebugKotlin"],
                "compile_tasks": [":app:compileDebugKotlin"],
                "unit_tasks": [":app:testDebugUnitTest"],
                "lint_tasks": [":app:lintDebug"],
                "assemble_tasks": [":app:assembleDebug"],
                "schema": {"mode": "gradle", "tasks": [":app:verifyRoomSchemas"]},
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _args(phase: str) -> argparse.Namespace:
        return argparse.Namespace(
            phase=phase,
            source_root="source",
            admitted_sha=None,
            validation_scope=None,
            working_directory=None,
            gradle_wrapper_path=None,
            validation_plan_json=None,
            protected_full_execution_mode=None,
            private_dependency_repository=None,
            private_dependency_sha=None,
            private_dependency_subdirectory=None,
            private_dependency_id=None,
            private_dependency_verified=None,
            private_dependency_remotes_erased=None,
            private_dependency_credentials_erased=None,
            private_dependency_head_sha=None,
            private_dependency_checkout_repository=None,
            private_dependency_checkout_id=None,
            private_dependency_expected_subpath=None,
        )


if __name__ == "__main__":
    unittest.main()
