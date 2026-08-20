from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Mapping, Sequence
from unittest import mock

from ci_workflows import ciw_apple
from ci_workflows.apple_execution import CommandOutcome
from ci_workflows.apple_types import AppleValidationError


class ScriptedRunner:
    def __init__(
        self,
        *,
        outcome: CommandOutcome | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.outcome = outcome or CommandOutcome(0, "", "")
        self.error = error

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome:
        if self.error is not None:
            raise self.error
        return self.outcome


class AppleFailureDiagnosticTests(unittest.TestCase):
    def roots(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name).resolve()
        source = root / "source"
        state = root / "workflow-state" / "tmp"
        source.mkdir(parents=True)
        state.mkdir(parents=True)
        return temporary, source, state

    def capture_run(
        self,
        runner: ciw_apple._FailureDiagnosticRunner,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int = 60,
    ) -> tuple[CommandOutcome | None, str]:
        stream = StringIO()
        with redirect_stderr(stream):
            outcome = runner.run(
                argv,
                cwd=cwd,
                env={},
                timeout_seconds=timeout_seconds,
            )
        return outcome, stream.getvalue()

    def test_nonzero_xcodebuild_emits_bounded_fail_closed_tail(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        secret = "github_pat_abcdefghijklmnopqrstuvwxyz012345"
        jwt = "eyJabcdefghijklmno.abcdefghijklmnop.qrstuvwxyz012345"
        private_url = "https://github.com/StreamScapeTV/private-media.git"
        lines = [f"old-diagnostic-{index:03d}" for index in range(120)]
        lines.extend(
            (
                f"{source}/Sources/App.swift:42:17: error: cannot convert value",
                f"Authorization: Bearer {secret}",
                f"PRIVATE_TOKEN={secret}",
                f"jwt={jwt}",
                f"dependency {private_url}",
                "::error::attempted workflow-command injection",
                "last compiler detail",
            )
        )
        delegate = ScriptedRunner(
            outcome=CommandOutcome(65, "\n".join(lines) + "\n", "")
        )
        runner = ciw_apple._FailureDiagnosticRunner(
            delegate,
            (source, state, state.parent),
        )

        outcome, diagnostic = self.capture_run(
            runner,
            ("xcodebuild", "-project", str(source / "App.xcodeproj"), "build"),
            cwd=source,
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.returncode, 65)
        self.assertIn("CIW Apple command failure: exited with status 65.", diagnostic)
        self.assertIn("last compiler detail", diagnostic)
        self.assertIn("error: cannot convert value", diagnostic)
        self.assertIn("| ::error::attempted workflow-command injection", diagnostic)
        self.assertNotIn("\n::error::", diagnostic)
        self.assertNotIn("old-diagnostic-000", diagnostic)
        self.assertNotIn(str(source), diagnostic)
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn(jwt, diagnostic)
        self.assertNotIn(private_url, diagnostic)
        self.assertIn("<redacted>", diagnostic)
        self.assertIn("<url>", diagnostic)
        self.assertLess(len(diagnostic), 13 * 1024)

    def test_timeout_emits_partial_sanitized_output_and_preserves_timeout(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        timeout = subprocess.TimeoutExpired(
            cmd=("xcodebuild", "build"),
            timeout=9,
            output=f"{source}/Sources/App.swift:7: error: partial compiler output\n",
            stderr=f"Authorization: Bearer {secret}\n",
        )
        runner = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(error=timeout),
            (source, state, state.parent),
        )
        stream = StringIO()

        with redirect_stderr(stream), self.assertRaises(subprocess.TimeoutExpired):
            runner.run(
                ("xcodebuild", "build"),
                cwd=source,
                env={},
                timeout_seconds=9,
            )

        diagnostic = stream.getvalue()
        self.assertIn("timed out after 9 seconds", diagnostic)
        self.assertIn("partial compiler output", diagnostic)
        self.assertNotIn(str(source), diagnostic)
        self.assertNotIn(secret, diagnostic)

    def test_launch_failure_emits_class_without_leaking_command_arguments(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        runner = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(error=AppleValidationError("command_failed")),
            (source, state, state.parent),
        )
        stream = StringIO()

        with redirect_stderr(stream), self.assertRaisesRegex(
            AppleValidationError,
            "command_failed",
        ):
            runner.run(
                ("xcodebuild", "-project", "/private/source/App.xcodeproj", "build"),
                cwd=source,
                env={"PRIVATE_TOKEN": "do-not-print"},
                timeout_seconds=60,
            )

        diagnostic = stream.getvalue()
        self.assertEqual(
            diagnostic,
            "CIW Apple command failure: could not be launched.\n",
        )
        self.assertNotIn("/private/source", diagnostic)
        self.assertNotIn("do-not-print", diagnostic)

    def test_success_and_non_build_housekeeping_remain_silent(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        success = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(outcome=CommandOutcome(0, "build succeeded\n", "")),
            (source, state, state.parent),
        )
        outcome, diagnostic = self.capture_run(
            success,
            ("xcodebuild", "build"),
            cwd=source,
        )
        self.assertEqual(outcome.returncode, 0)
        self.assertEqual(diagnostic, "")

        housekeeping = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(outcome=CommandOutcome(1, "", "already shutdown")),
            (source, state, state.parent),
        )
        outcome, diagnostic = self.capture_run(
            housekeeping,
            ("xcrun", "simctl", "shutdown", "00000000-0000-0000-0000-000000000000"),
            cwd=source,
        )
        self.assertEqual(outcome.returncode, 1)
        self.assertEqual(diagnostic, "")

    def test_legacy_execute_path_installs_diagnostic_runner_without_api_change(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        sentinel_result = object()
        with mock.patch.object(
            ciw_apple.apple_validation,
            "execute_apple_plan",
            return_value=sentinel_result,
        ) as execute:
            result = ciw_apple._run_plan(
                plan=mock.sentinel.plan,
                source=source,
                state=state,
                environment={},
            )
        self.assertIs(result, sentinel_result)
        runner = execute.call_args.kwargs["runner"]
        self.assertIsInstance(runner, ciw_apple._FailureDiagnosticRunner)


if __name__ == "__main__":
    unittest.main()
