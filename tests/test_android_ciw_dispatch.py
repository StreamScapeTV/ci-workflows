"""Focused primitive-backed runtime coverage for ``ciw android validate``."""
from __future__ import annotations

import argparse
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import ciw
from ci_workflows.ciw_android import execute_android_validate
from ci_workflows.ciw_docs import load_command_contract
from ci_workflows.ciw_types import CIWContext, CIWError, CIWResult
from ci_workflows.language_primitives import JavaRuntime, OperationResult
from ci_workflows.runtime_primitives import ProcessResult

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class AndroidCIWDispatchTests(unittest.TestCase):
    def test_runtime_registry_keeps_one_bounded_android_command(self) -> None:
        runtime = ciw.runtime_command_index()
        self.assertIn("android validate", runtime)
        spec = runtime["android validate"]
        self.assertEqual(spec.domain, "android")
        self.assertEqual(spec.operation, "validate")
        self.assertEqual(spec.qualified_handler, "ci_workflows.ciw.handle_android_validate")
        contract = load_command_contract(ROOT)
        row = next(
            item
            for item in contract["commands"]
            if item["domain"] == "android" and item["operation"] == "validate"
        )
        self.assertEqual(row["handler"], spec.qualified_handler)
        ciw.validate_runtime_contract(ROOT)

    def test_parser_exposes_typed_android_fields_without_shell_or_runner(self) -> None:
        arguments = ciw.parser().parse_args(
            [
                "android",
                "validate",
                "--phase",
                "plan",
                "--admitted-sha",
                SHA,
                "--validation-scope",
                "gradle",
                "--gradle-tasks-json",
                '["assembleDebug","testDebugUnitTest"]',
            ]
        )
        self.assertEqual(arguments.phase, "plan")
        self.assertEqual(arguments.validation_scope, "gradle")
        self.assertEqual(arguments.gradle_tasks_json, '["assembleDebug","testDebugUnitTest"]')
        for forbidden in ("shell", "runner", "runs_on", "product_id", "command"):
            self.assertFalse(hasattr(arguments, forbidden))
        with self.assertRaises(SystemExit):
            ciw.parser().parse_args(["android", "arbitrary"])

    def test_plan_is_product_neutral_and_derives_mobile_and_dependency_metadata(self) -> None:
        context = CIWContext(
            root=ROOT,
            environment={
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "unit",
                "INPUT_WORKING_DIRECTORY": "android",
                "INPUT_GRADLE_WRAPPER_PATH": "gradlew",
                "INPUT_GRADLE_TASKS_JSON": '["testDebugUnitTest"]',
                "INPUT_PRIVATE_DEPENDENCY_REPOSITORY": "StreamScapeTV/shared-lib",
                "INPUT_PRIVATE_DEPENDENCY_SHA": "b" * 40,
                "INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY": "android",
                "INPUT_PRIVATE_DEPENDENCY_ID": "shared-lib",
            },
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        result = execute_android_validate(self._args("plan"), context)
        self.assertEqual(result.outputs["runner_profile"], "mobile")
        self.assertEqual(result.outputs["runs_on_json"], '["linux","amd64","mobile"]')
        self.assertEqual(result.outputs["workspace_profile"], "gradle")
        self.assertEqual(result.outputs["private_dependency_used"], "true")
        self.assertEqual(
            result.outputs["private_dependency_repository"],
            "StreamScapeTV/shared-lib",
        )
        self.assertNotIn("product", " ".join(result.outputs).casefold())

    def test_plan_rejects_partial_dependency_and_unbounded_scope_shapes(self) -> None:
        context = CIWContext(
            ROOT,
            {
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "script",
                "INPUT_SCRIPT_PATH": "ci/verify-schema.sh",
                "INPUT_SCRIPT_ARGUMENTS_JSON": "[]",
                "INPUT_GRADLE_TASKS_JSON": "[]",
                "INPUT_PRIVATE_DEPENDENCY_SHA": "b" * 40,
            },
            io.StringIO(),
            io.StringIO(),
        )
        with self.assertRaises(CIWError) as failure:
            execute_android_validate(self._args("plan"), context)
        self.assertEqual(failure.exception.code, "private_dependency_invalid")

        context = CIWContext(
            ROOT,
            {
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_VALIDATION_SCOPE": "compile",
                "INPUT_GRADLE_TASKS_JSON": '["assembleDebug","lintDebug"]',
            },
            io.StringIO(),
            io.StringIO(),
        )
        with self.assertRaises(CIWError) as failure:
            execute_android_validate(self._args("plan"), context)
        self.assertEqual(failure.exception.code, "validation_plan_invalid")

    def test_compile_executes_copy_with_no_secret_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, _source, state = self._filesystem(root)
            context = self._context(
                workspace,
                state,
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_VALIDATION_SCOPE": "compile",
                    "INPUT_WORKING_DIRECTORY": ".",
                    "INPUT_GRADLE_WRAPPER_PATH": "gradlew",
                    "INPUT_GRADLE_TASKS_JSON": '["assembleDebug"]',
                    "GITHUB_TOKEN": "must-not-reach-product",
                    "PRIVATE_DEPENDENCY_TOKEN": "also-secret",
                },
            )
            runtime = JavaRuntime(
                Path("/runner/java"),
                "25.0.1",
                25,
                OperationResult("java.inspect", 0, "", ""),
            )
            operation = OperationResult("android.build", 0, "", "")
            with (
                mock.patch("ci_workflows.ciw_android.resolve_state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android.android_execution.verify_exact_source") as exact,
                mock.patch("ci_workflows.ciw_android.resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch("ci_workflows.ciw_android.inspect_java_runtime", return_value=runtime),
                mock.patch("ci_workflows.ciw_android.android_build", return_value=operation) as build,
            ):
                result = execute_android_validate(self._args("execute"), context)
            self.assertEqual(exact.call_count, 2)
            kwargs = build.call_args.kwargs
            self.assertEqual(build.call_args.args[:2], (Path("gradlew"), "assembleDebug"))
            self.assertEqual(kwargs["project_directory"], state / "tmp/android-source")
            self.assertEqual(kwargs["options"], ("--no-daemon",))
            self.assertNotIn("GITHUB_TOKEN", kwargs["environment"])
            self.assertNotIn("PRIVATE_DEPENDENCY_TOKEN", kwargs["environment"])
            self.assertEqual(result.outputs["result"], "success")
            self.assertEqual(result.outputs["clean_tree"], "true")
            self.assertIn('"java_major":25', result.outputs["test_summary"])

    def test_dependency_execution_requires_exact_checkout_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, _source, state = self._filesystem(root)
            dependency = state / "dependencies/shared-lib"
            expected = dependency / "android"
            expected.mkdir(parents=True)
            context = self._context(
                workspace,
                state,
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_VALIDATION_SCOPE": "gradle",
                    "INPUT_GRADLE_TASKS_JSON": '["check"]',
                    "INPUT_PRIVATE_DEPENDENCY_REPOSITORY": "StreamScapeTV/shared-lib",
                    "INPUT_PRIVATE_DEPENDENCY_SHA": "b" * 40,
                    "INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY": "android",
                    "INPUT_PRIVATE_DEPENDENCY_ID": "shared-lib",
                    "INPUT_PRIVATE_DEPENDENCY_VERIFIED": "true",
                    "INPUT_PRIVATE_DEPENDENCY_REMOTES_ERASED": "true",
                    "INPUT_PRIVATE_DEPENDENCY_CREDENTIALS_ERASED": "true",
                    "INPUT_PRIVATE_DEPENDENCY_HEAD_SHA": "b" * 40,
                    "INPUT_PRIVATE_DEPENDENCY_CHECKOUT_REPOSITORY": "StreamScapeTV/shared-lib",
                    "INPUT_PRIVATE_DEPENDENCY_CHECKOUT_ID": "shared-lib",
                    "INPUT_PRIVATE_DEPENDENCY_EXPECTED_SUBPATH": "android",
                    "CI_PRIVATE_DEPENDENCY_PATH": str(dependency),
                },
            )
            runtime = JavaRuntime(
                Path("/runner/java"),
                "25",
                25,
                OperationResult("java.inspect", 0, "", ""),
            )
            with (
                mock.patch("ci_workflows.ciw_android.resolve_state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android.android_execution.verify_exact_source"),
                mock.patch("ci_workflows.ciw_android.resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch("ci_workflows.ciw_android.inspect_java_runtime", return_value=runtime),
                mock.patch(
                    "ci_workflows.ciw_android.run_gradle_tasks",
                    return_value=OperationResult("android.gradle", 0, "", ""),
                ) as gradle,
            ):
                execute_android_validate(self._args("execute"), context)
            self.assertEqual(
                gradle.call_args.kwargs["environment"]["CI_PRIVATE_DEPENDENCY_PATH"],
                str(expected.resolve()),
            )
            shutil.rmtree(state / "tmp/android-source")
            context.environment["INPUT_PRIVATE_DEPENDENCY_HEAD_SHA"] = "c" * 40
            with (
                mock.patch("ci_workflows.ciw_android.resolve_state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android.android_execution.verify_exact_source"),
            ):
                with self.assertRaises(CIWError) as failure:
                    execute_android_validate(self._args("execute"), context)
            self.assertEqual(failure.exception.code, "private_dependency_unverified")

    def test_script_scope_executes_checked_in_argv_not_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, source, state = self._filesystem(root)
            script = source / "ci/verify-schema.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o700)
            context = self._context(
                workspace,
                state,
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_VALIDATION_SCOPE": "script",
                    "INPUT_GRADLE_TASKS_JSON": "[]",
                    "INPUT_SCRIPT_PATH": "ci/verify-schema.sh",
                    "INPUT_SCRIPT_ARGUMENTS_JSON": '["--check","room"]',
                },
            )
            runtime = JavaRuntime(
                Path("/runner/java"),
                "25",
                25,
                OperationResult("java.inspect", 0, "", ""),
            )
            with (
                mock.patch("ci_workflows.ciw_android.resolve_state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android.android_execution.verify_exact_source"),
                mock.patch("ci_workflows.ciw_android.resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch("ci_workflows.ciw_android.inspect_java_runtime", return_value=runtime),
                mock.patch(
                    "ci_workflows.ciw_android.run_process",
                    return_value=ProcessResult(0, "", "", False),
                ) as process,
            ):
                result = execute_android_validate(self._args("execute"), context)
            argv = process.call_args.args[0]
            self.assertTrue(str(argv[0]).endswith("/tmp/android-source/ci/verify-schema.sh"))
            self.assertEqual(argv[1:], ("--check", "room"))
            self.assertNotIn("shell", process.call_args.kwargs)
            self.assertEqual(result.outputs["validation_scope"], "script")

    def test_cleanup_and_residue_are_independent_of_invalid_request_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, _source, state = self._filesystem(root)
            copy = state / "tmp/android-source"
            copy.mkdir(parents=True)
            (copy / "generated.txt").write_text("state", encoding="utf-8")
            context = self._context(workspace, state, {"INPUT_VALIDATION_SCOPE": "invalid"})
            with mock.patch("ci_workflows.ciw_android.resolve_state_root", return_value=state):
                cleanup = execute_android_validate(self._args("cleanup"), context)
                residue = execute_android_validate(self._args("residue"), context)
            self.assertEqual(cleanup.outputs["cleanup_result"], "success")
            self.assertEqual(residue.outputs["cleanup_result"], "success")
            self.assertFalse(copy.exists())

    def test_handler_delegates_to_typed_android_adapter(self) -> None:
        arguments = argparse.Namespace(phase="plan", source_root="source")
        context = CIWContext(ROOT, {}, io.StringIO(), io.StringIO())
        expected = CIWResult("android", "validate", outputs={"result": "planned"})
        with mock.patch.object(ciw, "execute_android_validate", return_value=expected) as execute:
            actual = ciw.handle_android_validate(arguments, context)
        self.assertIs(actual, expected)
        execute.assert_called_once_with(arguments, context)

    @staticmethod
    def _args(phase: str) -> argparse.Namespace:
        return argparse.Namespace(
            phase=phase,
            source_root="source",
            admitted_sha=None,
            validation_scope=None,
            working_directory=None,
            gradle_wrapper_path=None,
            gradle_tasks_json=None,
            targeted_test_selector=None,
            script_path=None,
            script_arguments_json=None,
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

    @staticmethod
    def _filesystem(root: Path) -> tuple[Path, Path, Path]:
        workspace = root / "workspace"
        source = workspace / "source"
        state = root / "state"
        source.mkdir(parents=True)
        (source / "gradlew").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (source / "gradlew").chmod(0o700)
        for relative in ("tmp", "home", "gradle", "run-tmp", "dependencies"):
            (state / relative).mkdir(parents=True, exist_ok=True)
        (root / "sdk").mkdir()
        return workspace, source, state

    @staticmethod
    def _context(
        workspace: Path,
        state: Path,
        values: dict[str, str],
    ) -> CIWContext:
        root = workspace.parent
        environment = {
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(root),
            "CI_WORKFLOW_STATE_ID": "state",
            "CI_WORKFLOW_ROOT": str(state),
            "HOME": str(state / "home"),
            "GRADLE_USER_HOME": str(state / "gradle"),
            "TMPDIR": str(state / "run-tmp"),
            "ANDROID_SDK_ROOT": str(root / "sdk"),
            "PATH": os.environ.get("PATH", "/usr/bin"),
            **values,
        }
        return CIWContext(ROOT, environment, io.StringIO(), io.StringIO())


if __name__ == "__main__":
    unittest.main()
