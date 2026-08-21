from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import ciw_android
from ci_workflows.ciw_types import CIWContext
from ci_workflows.language_primitives import LanguagePrimitiveError, OperationResult
from ci_workflows.runtime_primitives import ProcessResult


class AndroidProtectedFullFamilyProgressTests(unittest.TestCase):
    def test_bounded_runner_uses_fixed_family_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            expected = ProcessResult(0, "", "", False)
            with mock.patch.object(
                ciw_android,
                "run_process",
                return_value=expected,
            ) as run_process:
                result = ciw_android._BoundedGradleRunner().run(
                    ("./gradlew", ":app:testDebugUnitTest"),
                    cwd=root,
                    env={"CI": "true"},
                )

        self.assertIs(result, expected)
        run_process.assert_called_once_with(
            ("./gradlew", ":app:testDebugUnitTest"),
            cwd=root,
            environment={"CI": "true"},
            timeout_seconds=ciw_android._PROTECTED_FULL_FAMILY_TIMEOUT_SECONDS,
        )
        self.assertEqual(45 * 60, ciw_android._PROTECTED_FULL_FAMILY_TIMEOUT_SECONDS)
        self.assertLess(ciw_android._PROTECTED_FULL_FAMILY_TIMEOUT_SECONDS, 120 * 60)

    def test_family_markers_are_bounded_and_task_free(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        context = CIWContext(Path("/contract"), {}, stdout, stderr)
        environment = {"CI": "true"}
        gradle = mock.Mock(return_value=OperationResult("android.protected_full.unit", 0, "", ""))

        with (
            mock.patch.object(ciw_android, "run_gradle_tasks", gradle),
            mock.patch.object(ciw_android.time, "monotonic_ns", side_effect=[1_000_000, 6_000_000]),
        ):
            wall_ms = ciw_android._run_protected_full_group(
                Path("gradlew"),
                "unit",
                (":app:testDebugUnitTest",),
                project=Path("/project"),
                environment=environment,
                context=context,
            )

        self.assertEqual(5, wall_ms)
        self.assertEqual(
            "android-protected-full-family-start name=unit timeout_seconds=2700\n"
            "android-protected-full-family-complete name=unit wall_ms=5\n",
            stdout.getvalue(),
        )
        self.assertEqual("", stderr.getvalue())
        self.assertNotIn(":app:testDebugUnitTest", stdout.getvalue())
        self.assertNotIn("/project", stdout.getvalue())
        call = gradle.call_args
        self.assertEqual(Path("gradlew"), call.args[0])
        self.assertEqual((":app:testDebugUnitTest",), call.args[1])
        self.assertEqual("android.protected_full.unit", call.kwargs["operation"])
        self.assertEqual(("--no-daemon",), call.kwargs["options"])
        self.assertIs(environment, call.kwargs["environment"])
        self.assertIsInstance(call.kwargs["runner"], ciw_android._BoundedGradleRunner)

    def test_timeout_marker_preserves_stable_failure_classification(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        context = CIWContext(Path("/contract"), {}, stdout, stderr)
        failure = LanguagePrimitiveError(
            "command_timeout",
            "android.protected_full.compile",
        )

        with (
            mock.patch.object(ciw_android, "run_gradle_tasks", side_effect=failure),
            mock.patch.object(ciw_android.time, "monotonic_ns", side_effect=[2_000_000, 9_000_000]),
        ):
            with self.assertRaises(LanguagePrimitiveError) as raised:
                ciw_android._run_protected_full_group(
                    Path("gradlew"),
                    "compile",
                    (":app:compileDebugKotlin",),
                    project=Path("/project"),
                    environment={"CI": "true", "TOKEN": "must-not-appear"},
                    context=context,
                )

        self.assertIs(failure, raised.exception)
        self.assertEqual(
            "android-protected-full-family-start name=compile timeout_seconds=2700\n",
            stdout.getvalue(),
        )
        self.assertEqual(
            "android-protected-full-family-failure name=compile "
            "code=command_timeout returncode=none wall_ms=7\n",
            stderr.getvalue(),
        )
        for forbidden in (
            ":app:compileDebugKotlin",
            "/project",
            "must-not-appear",
            "TOKEN",
        ):
            self.assertNotIn(forbidden, stdout.getvalue() + stderr.getvalue())

    def test_nonzero_marker_includes_only_return_code_and_stable_code(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        context = CIWContext(Path("/contract"), {}, stdout, stderr)
        failure = LanguagePrimitiveError(
            "command_failed",
            "android.protected_full.lint",
            returncode=7,
        )

        with (
            mock.patch.object(ciw_android, "run_gradle_tasks", side_effect=failure),
            mock.patch.object(ciw_android.time, "monotonic_ns", side_effect=[4_000_000, 10_000_000]),
        ):
            with self.assertRaises(LanguagePrimitiveError):
                ciw_android._run_protected_full_group(
                    Path("gradlew"),
                    "lint",
                    (":app:lintDebug",),
                    project=Path("/project"),
                    environment={},
                    context=context,
                )

        self.assertEqual(
            "android-protected-full-family-failure name=lint "
            "code=command_failed returncode=7 wall_ms=6\n",
            stderr.getvalue(),
        )
        self.assertNotIn(":app:lintDebug", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
