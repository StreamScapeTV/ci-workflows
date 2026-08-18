"""Wiring coverage for Android execution timing/resource evidence."""
from __future__ import annotations

import argparse
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.android_resource_metrics import AndroidResourceMetrics
from ci_workflows.ciw_android import (
    AndroidPrimitiveRequest,
    ProtectedFullPlan,
    ScriptPlan,
    _execute_request,
)
from ci_workflows.ciw_types import CIWContext
from ci_workflows.language_primitives import JavaRuntime, OperationResult

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class _FakeSampler:
    def __init__(self, metrics: AndroidResourceMetrics) -> None:
        self.result = metrics

    def __enter__(self) -> "_FakeSampler":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False


class AndroidTelemetryContractTests(unittest.TestCase):
    def _request(self) -> AndroidPrimitiveRequest:
        return AndroidPrimitiveRequest(
            admitted_sha=SHA,
            validation_scope="protected-full",
            working_directory=".",
            gradle_wrapper_path="gradlew",
            gradle_tasks=(),
            targeted_test_selector="",
            script=None,
            protected_full=ProtectedFullPlan(
                unit_tasks=("testDebugUnitTest",),
                lint_tasks=("lintDebug",),
                assemble_tasks=("assembleDebug",),
                schema_mode="script",
                schema_script=ScriptPlan("ci/verify-schema.sh", ("--check",)),
            ),
            private_dependency_repository="",
            private_dependency_sha="",
            private_dependency_subdirectory=".",
            private_dependency_id="",
        )

    def test_protected_full_summary_exposes_bounded_wall_and_resource_facts(self) -> None:
        metrics = AndroidResourceMetrics(
            wall_ms=1350,
            child_cpu_ms=2410,
            peak_memory_bytes=4_200_000_000,
            peak_processes=23,
            measurement_source="cgroup-v2-sampled",
        )
        runtime = JavaRuntime(
            executable=Path("/runner/java"),
            version="25",
            major=25,
            result=OperationResult("java.inspect", 0, "", ""),
        )
        context = CIWContext(ROOT, {"PATH": "/bin"}, io.StringIO(), io.StringIO())
        with (
            mock.patch("ci_workflows.ciw_android.AndroidResourceSampler", return_value=_FakeSampler(metrics)),
            mock.patch("ci_workflows.ciw_android._state_root", return_value=Path("/state")),
            mock.patch("ci_workflows.ciw_android._source_root", return_value=Path("/source")),
            mock.patch("ci_workflows.ciw_android.android_execution.verify_exact_source"),
            mock.patch("ci_workflows.ciw_android.android_execution.copy_source"),
            mock.patch("ci_workflows.ciw_android._bounded_existing_directory", return_value=Path("/project")),
            mock.patch("ci_workflows.ciw_android._dependency_path", return_value=None),
            mock.patch("ci_workflows.ciw_android._runtime_environment", return_value={"PATH": "/bin"}),
            mock.patch("ci_workflows.ciw_android.resolve_java_executable", return_value=Path("/runner/java")),
            mock.patch("ci_workflows.ciw_android.inspect_java_runtime", return_value=runtime),
            mock.patch("ci_workflows.ciw_android.validate_java_runtime"),
            mock.patch("ci_workflows.ciw_android.run_gradle_tasks"),
            mock.patch("ci_workflows.ciw_android._execute_script"),
            mock.patch(
                "ci_workflows.ciw_android.time.monotonic_ns",
                side_effect=(1_000_000_000, 1_275_000_000, 1_300_000_000, 1_345_000_000),
            ),
        ):
            result = _execute_request(self._request(), argparse.Namespace(), context)

        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual(summary["execute_wall_ms"], 1350)
        self.assertEqual(summary["gradle_wall_ms"], 275)
        self.assertEqual(summary["script_wall_ms"], 45)
        self.assertEqual(summary["child_cpu_ms"], 2410)
        self.assertEqual(summary["peak_memory_bytes"], 4_200_000_000)
        self.assertEqual(summary["peak_processes"], 23)
        self.assertEqual(summary["resource_measurement"], "cgroup-v2-sampled")
        self.assertEqual(summary["gradle_invocations"], 1)
        self.assertEqual(summary["script_invocations"], 1)
        self.assertEqual(summary["schema_mode"], "script")
        self.assertNotIn("/state", result.outputs["test_summary"])
        self.assertNotIn("/source", result.outputs["test_summary"])
        self.assertNotIn("/project", result.outputs["test_summary"])

    def test_unsupported_resource_metrics_are_json_null_not_zero(self) -> None:
        metrics = AndroidResourceMetrics(
            wall_ms=10,
            child_cpu_ms=None,
            peak_memory_bytes=None,
            peak_processes=None,
            measurement_source="unavailable",
        )
        runtime = JavaRuntime(
            executable=Path("/runner/java"),
            version="25",
            major=25,
            result=OperationResult("java.inspect", 0, "", ""),
        )
        request = self._request()
        request = AndroidPrimitiveRequest(
            admitted_sha=request.admitted_sha,
            validation_scope="compile",
            working_directory=request.working_directory,
            gradle_wrapper_path=request.gradle_wrapper_path,
            gradle_tasks=("compileDebugKotlin",),
            targeted_test_selector="",
            script=None,
            protected_full=None,
            private_dependency_repository="",
            private_dependency_sha="",
            private_dependency_subdirectory=".",
            private_dependency_id="",
        )
        context = CIWContext(ROOT, {"PATH": "/bin"}, io.StringIO(), io.StringIO())
        with (
            mock.patch("ci_workflows.ciw_android.AndroidResourceSampler", return_value=_FakeSampler(metrics)),
            mock.patch("ci_workflows.ciw_android._state_root", return_value=Path("/state")),
            mock.patch("ci_workflows.ciw_android._source_root", return_value=Path("/source")),
            mock.patch("ci_workflows.ciw_android.android_execution.verify_exact_source"),
            mock.patch("ci_workflows.ciw_android.android_execution.copy_source"),
            mock.patch("ci_workflows.ciw_android._bounded_existing_directory", return_value=Path("/project")),
            mock.patch("ci_workflows.ciw_android._dependency_path", return_value=None),
            mock.patch("ci_workflows.ciw_android._runtime_environment", return_value={"PATH": "/bin"}),
            mock.patch("ci_workflows.ciw_android.resolve_java_executable", return_value=Path("/runner/java")),
            mock.patch("ci_workflows.ciw_android.inspect_java_runtime", return_value=runtime),
            mock.patch("ci_workflows.ciw_android.validate_java_runtime"),
            mock.patch("ci_workflows.ciw_android.run_gradle_tasks"),
            mock.patch("ci_workflows.ciw_android.time.monotonic_ns", side_effect=(10, 20)),
        ):
            result = _execute_request(request, argparse.Namespace(), context)
        summary = json.loads(result.outputs["test_summary"])
        self.assertIsNone(summary["child_cpu_ms"])
        self.assertIsNone(summary["peak_memory_bytes"])
        self.assertIsNone(summary["peak_processes"])
        self.assertEqual(summary["resource_measurement"], "unavailable")


if __name__ == "__main__":
    unittest.main()
