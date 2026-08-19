from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Sequence

from ci_workflows.language_primitives import (
    CommandOutcome,
    LanguagePrimitiveError,
    run_gradle_tasks,
)


class FixedRunner:
    def __init__(self, outcome: CommandOutcome) -> None:
        self.outcome = outcome

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        return self.outcome


class AndroidFailureDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir()
        self.wrapper = self.project / "gradlew"
        self.wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.wrapper.chmod(0o755)

    def run_failure(self, outcome: CommandOutcome, operation: str) -> tuple[str, LanguagePrimitiveError]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(LanguagePrimitiveError) as failure:
                run_gradle_tasks(
                    Path("gradlew"),
                    (":app:testDebugUnitTest",),
                    project_directory=self.project,
                    environment={"CI": "true"},
                    runner=FixedRunner(outcome),
                    operation=operation,
                )
        return stderr.getvalue(), failure.exception

    def test_failed_android_operation_emits_redacted_bounded_tail(self) -> None:
        noise = "\n".join(f"noise-{index}" for index in range(100))
        output, failure = self.run_failure(
            CommandOutcome(
                1,
                f"{noise}\n{self.project}/app/Test.kt:9 failure\npassword=hunter2\n",
                "https://alice:credential@example.invalid/repository\nauthorization=Bearer-secret\n",
            ),
            "android.protected_full",
        )

        self.assertEqual(failure.code, "command_failed")
        self.assertEqual(failure.returncode, 1)
        self.assertIn("android-command-diagnostic-begin", output)
        self.assertIn("android-command-diagnostic-end", output)
        self.assertIn("<project>/app/Test.kt:9 failure", output)
        self.assertIn("password=<redacted>", output)
        self.assertIn("authorization=<redacted>", output)
        self.assertIn("https://<redacted>@example.invalid/repository", output)
        self.assertNotIn("hunter2", output)
        self.assertNotIn("credential", output)
        self.assertNotIn(str(self.project), output)
        self.assertNotIn("noise-0", output)
        self.assertLessEqual(len(output.encode("utf-8")), 17 * 1024)

    def test_successful_android_operation_remains_silent(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = run_gradle_tasks(
                Path("gradlew"),
                (":app:assembleDebug",),
                project_directory=self.project,
                runner=FixedRunner(CommandOutcome(0, "success-output", "")),
                operation="android.assemble",
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual("", stderr.getvalue())

    def test_non_android_failure_preserves_existing_silent_contract(self) -> None:
        output, failure = self.run_failure(
            CommandOutcome(2, "password=do-not-print", "generic failure"),
            "gradle.tasks",
        )
        self.assertEqual(failure.code, "command_failed")
        self.assertEqual("", output)

    def test_android_timeout_emits_tail_and_preserves_timeout_code(self) -> None:
        output, failure = self.run_failure(
            CommandOutcome(0, "last useful timeout line", "", True),
            "android.compile",
        )
        self.assertEqual(failure.code, "command_timeout")
        self.assertIn("last useful timeout line", output)


if __name__ == "__main__":
    unittest.main()
