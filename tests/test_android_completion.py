from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.ciw_android_completion import (
    _live_plan,
    _release_plan,
    execute_android_live_validate,
    execute_android_release_validate,
)
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.language_primitives import JavaRuntime, OperationResult
from ci_workflows.runtime_primitives import ProcessResult

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def args(phase: str) -> argparse.Namespace:
    return argparse.Namespace(phase=phase, source_root="source", gradle_wrapper_path=None)


def dependency_inputs() -> dict[str, str]:
    return {
        "INPUT_PRIVATE_DEPENDENCY_REPOSITORY": "StreamScapeTV/shared-media",
        "INPUT_PRIVATE_DEPENDENCY_SHA": "b" * 40,
        "INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY": "android",
        "INPUT_PRIVATE_DEPENDENCY_ID": "shared-media",
    }


def live_json() -> str:
    return json.dumps(
        {
            "script": {
                "interpreter": "python3",
                "path": "ci/live_acceptance.py",
                "arguments": ["--live"],
            }
        },
        separators=(",", ":"),
    )


def release_json(*, artifact_path: str = "build/outputs/release/*.apk", retention: int = 7) -> str:
    return json.dumps(
        {
            "pre_scripts": [
                {"interpreter": "bash", "path": "ci/lint-policy.sh", "arguments": []}
            ],
            "gradle_groups": [
                {"tasks": [":app:testDebugUnitTest"]},
                {"tasks": [":app:lintDebug"]},
                {"tasks": [":app:assembleRelease"]},
                {"tasks": [":app:bundleRelease", ":app:lintRelease"]},
            ],
            "post_scripts": [
                {"interpreter": "bash", "path": "ci/release-policy.sh", "arguments": []}
            ],
            "size_budget": {
                "script_path": "ci/release-size.py",
                "apk_glob": "build/outputs/release/*.apk",
                "aab_glob": "build/outputs/release/*.aab",
                "budget_path": "ci/release-size-budgets.json",
                "baseline_path": "ci/release-size-baseline.json",
                "output_path": "build/reports/release-size-metrics.json",
            },
            "artifacts": [
                {"path": artifact_path, "kind": "apk", "required": True, "max_files": 1},
                {"path": "build/outputs/release/*.aab", "kind": "aab", "required": True, "max_files": 1},
                {"path": "build/reports/release-policy-report.json", "kind": "json", "required": True, "max_files": 1},
            ],
            "artifact_name": "android-unsigned-release",
            "retention_days": retention,
        },
        separators=(",", ":"),
    )


class AndroidCompletionPlanTests(unittest.TestCase):
    def context(self, environment: dict[str, str]) -> CIWContext:
        return CIWContext(ROOT, environment, io.StringIO(), io.StringIO())

    def test_live_plan_is_one_checked_in_script_and_product_neutral(self) -> None:
        plan = _live_plan(
            args("plan"),
            self.context(
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_WORKING_DIRECTORY": ".",
                    "INPUT_VALIDATION_PLAN_JSON": live_json(),
                    **dependency_inputs(),
                }
            ),
        )
        self.assertEqual(plan.script.path, "ci/live_acceptance.py")
        self.assertEqual(plan.script.arguments, ("--live",))
        self.assertEqual(plan.dependency.repository, "StreamScapeTV/shared-media")
        self.assertNotIn("streamscape_backend", live_json().casefold())

    def test_release_plan_preserves_historical_order_and_bounded_metadata(self) -> None:
        plan = _release_plan(
            args("plan"),
            self.context(
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_WORKING_DIRECTORY": ".",
                    "INPUT_GRADLE_WRAPPER_PATH": "gradlew",
                    "INPUT_VALIDATION_PLAN_JSON": release_json(),
                }
            ),
        )
        self.assertEqual(
            [group.tasks for group in plan.gradle_groups],
            [
                (":app:testDebugUnitTest",),
                (":app:lintDebug",),
                (":app:assembleRelease",),
                (":app:bundleRelease", ":app:lintRelease"),
            ],
        )
        self.assertEqual(plan.size_budget.script_path, "ci/release-size.py")
        self.assertEqual(plan.retention_days, 7)
        self.assertEqual(plan.artifact_name, "android-unsigned-release")

    def test_release_artifact_escape_kind_and_retention_fail_closed(self) -> None:
        cases = (
            release_json(artifact_path="../escape.apk"),
            release_json(artifact_path="build/outputs/release/*.aab"),
            release_json(retention=8),
        )
        for raw in cases:
            with self.subTest(raw=raw):
                context = self.context(
                    {
                        "INPUT_ADMITTED_SHA": SHA,
                        "INPUT_VALIDATION_PLAN_JSON": raw,
                    }
                )
                with self.assertRaises(CIWError):
                    _release_plan(args("plan"), context)

    def test_live_plan_rejects_extra_task_or_secret_mapping_channels(self) -> None:
        for extra in ("gradle_tasks", "secret_names", "environment"):
            value = json.loads(live_json())
            value[extra] = []
            context = self.context(
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_VALIDATION_PLAN_JSON": json.dumps(value),
                }
            )
            with self.subTest(extra=extra), self.assertRaises(CIWError):
                _live_plan(args("plan"), context)


class AndroidCompletionExecutionTests(unittest.TestCase):
    def filesystem(self, root: Path) -> tuple[Path, Path, Path]:
        workspace = root / "workspace"
        source = workspace / "source"
        source.mkdir(parents=True)
        state = root / "state"
        for path in (
            state,
            state / "home",
            state / "gradle",
            state / "tmpdir",
            state / "sdk",
        ):
            path.mkdir(exist_ok=True)
        return workspace, source, state

    def context(
        self,
        workspace: Path,
        state: Path,
        additional: dict[str, str],
    ) -> CIWContext:
        environment = {
            "GITHUB_WORKSPACE": str(workspace),
            "PATH": "/usr/bin:/bin",
            "HOME": str(state / "home"),
            "GRADLE_USER_HOME": str(state / "gradle"),
            "TMPDIR": str(state / "tmpdir"),
            "ANDROID_SDK_ROOT": str(state / "sdk"),
            **additional,
        }
        return CIWContext(ROOT, environment, io.StringIO(), io.StringIO())

    @staticmethod
    def runtime() -> JavaRuntime:
        return JavaRuntime(
            Path("/runner/java"),
            "25.0.1",
            25,
            OperationResult("java.inspect", 0, "", ""),
        )

    def test_live_execution_exposes_only_generic_credentials_to_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, source, state = self.filesystem(root)
            script = source / "ci/live_acceptance.py"
            script.parent.mkdir()
            script.write_text("print('fixture')\n", encoding="utf-8")
            context = self.context(
                workspace,
                state,
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_VALIDATION_PLAN_JSON": live_json(),
                    "CIW_SERVICE_USERNAME": "user-value",
                    "CIW_SERVICE_PASSWORD": "password-value",
                    "CIW_MAVEN_PACKAGE_READ_TOKEN": "package-read-token",
                    "STREAMSCAPE_BACKEND_USERNAME": "must-not-pass",
                    "STREAMSCAPE_BACKEND_PASSWORD": "must-not-pass",
                    "GITHUB_TOKEN": "must-not-pass",
                },
            )
            with (
                mock.patch("ci_workflows.ciw_android_completion._state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android_completion.android_execution.verify_exact_source"),
                mock.patch("ci_workflows.ciw_android_completion.resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch("ci_workflows.ciw_android_completion.inspect_java_runtime", return_value=self.runtime()),
                mock.patch(
                    "ci_workflows.ciw_android_completion.run_process",
                    return_value=ProcessResult(0, "secret-output-is-discarded", "", False),
                ) as process,
            ):
                result = execute_android_live_validate(args("execute"), context)
        environment = process.call_args.kwargs["environment"]
        self.assertEqual(environment["CIW_SERVICE_USERNAME"], "user-value")
        self.assertEqual(environment["CIW_SERVICE_PASSWORD"], "password-value")
        self.assertEqual(environment["CIW_MAVEN_PACKAGE_READ_TOKEN"], "package-read-token")
        self.assertNotIn("STREAMSCAPE_BACKEND_USERNAME", environment)
        self.assertNotIn("STREAMSCAPE_BACKEND_PASSWORD", environment)
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("user-value", json.dumps(result.outputs))
        self.assertNotIn("password-value", json.dumps(result.outputs))
        self.assertNotIn("package-read-token", json.dumps(result.outputs))
        summary = json.loads(result.outputs["test_summary"])
        self.assertTrue(summary["credentialed"])
        self.assertEqual(summary["script_invocations"], 1)

    def test_live_missing_credentials_is_explicit_and_secret_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, source, state = self.filesystem(root)
            (source / "ci").mkdir()
            (source / "ci/live_acceptance.py").write_text("pass\n", encoding="utf-8")
            context = self.context(
                workspace,
                state,
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_VALIDATION_PLAN_JSON": live_json(),
                    "CIW_SERVICE_USERNAME": "configured-user",
                },
            )
            with (
                mock.patch("ci_workflows.ciw_android_completion._state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android_completion.android_execution.verify_exact_source"),
            ):
                with self.assertRaises(CIWError) as failure:
                    execute_android_live_validate(args("execute"), context)
        self.assertEqual(failure.exception.code, "service_credentials_missing")
        self.assertNotIn("configured-user", str(failure.exception))

    def test_release_execution_reuses_project_state_and_returns_only_validated_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, source, state = self.filesystem(root)
            for relative, content in (
                ("gradlew", "#!/bin/sh\n"),
                ("ci/lint-policy.sh", "#!/bin/sh\n"),
                ("ci/release-policy.sh", "#!/bin/sh\n"),
                ("ci/release-size.py", "print('size')\n"),
                ("ci/release-size-budgets.json", "{}\n"),
                ("ci/release-size-baseline.json", "{}\n"),
                ("build/outputs/release/app-release.apk", "apk"),
                ("build/outputs/release/app-release.aab", "aab"),
                ("build/reports/release-policy-report.json", "{}\n"),
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            context = self.context(
                workspace,
                state,
                {
                    "INPUT_ADMITTED_SHA": SHA,
                    "INPUT_GRADLE_WRAPPER_PATH": "gradlew",
                    "INPUT_VALIDATION_PLAN_JSON": release_json(),
                    "GITHUB_TOKEN": "must-not-pass",
                    "CIW_MAVEN_PACKAGE_READ_TOKEN": "package-read-token",
                },
            )
            gradle_calls: list[tuple[str, ...]] = []

            def gradle(_wrapper, tasks, **kwargs):
                gradle_calls.append(tuple(tasks))
                self.assertNotIn("GITHUB_TOKEN", kwargs["environment"])
                self.assertEqual(kwargs["environment"]["CIW_MAVEN_PACKAGE_READ_TOKEN"], "package-read-token")
                return OperationResult("android.unsigned_release", 0, "", "")

            with (
                mock.patch("ci_workflows.ciw_android_completion._state_root", return_value=state),
                mock.patch("ci_workflows.ciw_android_completion.android_execution.verify_exact_source"),
                mock.patch("ci_workflows.ciw_android_completion.resolve_java_executable", return_value=Path("/runner/java")),
                mock.patch("ci_workflows.ciw_android_completion.inspect_java_runtime", return_value=self.runtime()),
                mock.patch("ci_workflows.ciw_android_completion._run_script"),
                mock.patch("ci_workflows.ciw_android_completion.run_gradle_tasks", side_effect=gradle),
                mock.patch("ci_workflows.ciw_android_completion.resolve_python_interpreter", return_value=Path("/usr/bin/python3")),
                mock.patch(
                    "ci_workflows.ciw_android_completion.run_python_script",
                    return_value=OperationResult("python.script", 0, "", ""),
                ) as size,
            ):
                result = execute_android_release_validate(args("execute"), context)

        self.assertEqual(
            gradle_calls,
            [
                (":app:testDebugUnitTest",),
                (":app:lintDebug",),
                (":app:assembleRelease",),
                (":app:bundleRelease", ":app:lintRelease"),
            ],
        )
        self.assertEqual(size.call_count, 1)
        paths = json.loads(result.outputs["artifact_paths_json"])
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(str(state / "tmp/android-release-source") in path for path in paths))
        self.assertNotIn("GITHUB_TOKEN", result.outputs["test_summary"])
        self.assertNotIn("package-read-token", json.dumps(result.outputs))
        self.assertEqual(result.outputs["retention_days"], "7")

    def test_cleanup_and_residue_remove_only_completion_copy(self) -> None:
        for executor, relative in (
            (execute_android_live_validate, "tmp/android-live-source"),
            (execute_android_release_validate, "tmp/android-release-source"),
        ):
            with self.subTest(executor=executor.__name__), tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "state"
                target = state / relative
                target.mkdir(parents=True)
                outside = state / "outside.txt"
                outside.write_text("keep", encoding="utf-8")
                context = CIWContext(ROOT, {}, io.StringIO(), io.StringIO())
                with mock.patch("ci_workflows.ciw_android_completion._state_root", return_value=state):
                    result = executor(args("cleanup"), context)
                    self.assertEqual(result.outputs["cleanup_result"], "success")
                    result = executor(args("residue"), context)
                    self.assertEqual(result.outputs["cleanup_result"], "success")
                self.assertTrue(outside.is_file())


class AndroidCompletionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routine = (ROOT / ".github/workflows/reusable-android.yml").read_text(encoding="utf-8")
        self.live = (ROOT / ".github/workflows/reusable-android-live-service.yml").read_text(encoding="utf-8")
        self.release = (ROOT / ".github/workflows/reusable-android-release.yml").read_text(encoding="utf-8")

    def test_each_new_gate_has_one_heavy_mobile_executor(self) -> None:
        selector = "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
        self.assertEqual(self.live.count(selector), 1)
        self.assertEqual(self.release.count(selector), 1)
        self.assertEqual(self.live.count("actions/exact-checkout@"), 1)
        self.assertEqual(self.live.count("actions/prepare-workspace@"), 1)
        self.assertEqual(self.release.count("actions/exact-checkout@"), 1)
        self.assertEqual(self.release.count("actions/prepare-workspace@"), 1)

    def test_routine_package_read_token_is_execution_only_and_artifact_light(self) -> None:
        text = self.routine.casefold()
        self.assertNotIn("service_username", text)
        self.assertNotIn("service_password", text)
        self.assertEqual(
            1,
            self.routine.count("maven_package_read_token: ${{ secrets.maven_package_read_token }}"),
        )
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("actions/cache", text)

    def test_live_secrets_are_generic_execute_only_and_no_artifact_is_uploaded(self) -> None:
        self.assertIn("service_username:", self.live)
        self.assertIn("service_password:", self.live)
        plan = self.live.split("  live:", 1)[0]
        self.assertNotIn("secrets.service_username", plan)
        self.assertNotIn("secrets.service_password", plan)
        self.assertIn("service_username: ${{ secrets.service_username }}", self.live)
        self.assertIn("service_password: ${{ secrets.service_password }}", self.live)
        self.assertEqual(
            1,
            self.live.count("maven_package_read_token: ${{ secrets.maven_package_read_token }}"),
        )
        self.assertNotIn("STREAMSCAPE_", self.live)
        self.assertNotIn("upload-artifact", self.live.lower())
        self.assertNotIn("actions/cache", self.live.lower())

    def test_release_has_one_pinned_upload_and_no_service_or_signing_authority(self) -> None:
        self.assertEqual(self.release.count("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"), 1)
        lower = self.release.casefold()
        self.assertNotIn("service_username", lower)
        self.assertNotIn("service_password", lower)
        self.assertNotIn("signing", lower)
        self.assertNotIn("play_store", lower)
        self.assertNotIn("registry", lower)
        self.assertEqual(
            1,
            self.release.count("maven_package_read_token: ${{ secrets.maven_package_read_token }}"),
        )
        self.assertNotIn("actions/cache", lower)
        self.assertIn("artifact_paths_json", self.release)
        self.assertIn("retention-days: ${{ steps.execute.outputs.retention_days }}", self.release)

    def test_shared_implementation_has_no_product_identity_or_legacy_secret_names(self) -> None:
        implementation = (ROOT / "src/ci_workflows/ciw_android_completion.py").read_text(encoding="utf-8").casefold()
        for forbidden in (
            "streamscapetv/iptv-android",
            "streamscapetv/streamscape-media",
            "streamscape_backend_username",
            "streamscape_backend_password",
            "streamscape_email",
            "streamscape_password",
        ):
            self.assertNotIn(forbidden, implementation)


if __name__ == "__main__":
    unittest.main()
