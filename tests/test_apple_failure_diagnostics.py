from __future__ import annotations

import json
import shutil
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


class BundleProducingRunner:
    def __init__(self, bundle: Path, outcome: CommandOutcome) -> None:
        self.bundle = bundle
        self.outcome = outcome

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome:
        self.bundle.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def result_bundle(state: Path, stage: str = "compile") -> Path:
        bundle = (
            state
            / "apple-validation"
            / "result-bundles"
            / stage
            / "validation.xcresult"
        )
        bundle.mkdir(parents=True, exist_ok=True)
        return bundle

    @staticmethod
    def xcode_argv(source: Path, bundle: Path) -> tuple[str, ...]:
        return (
            "xcodebuild",
            "-project",
            str(source / "App.xcodeproj"),
            "-resultBundlePath",
            str(bundle),
            "build",
        )

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
            state_root=state,
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

    def test_xcodebuild_uses_owned_xcresult_when_stdout_lacks_concrete_error(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        bundle = self.result_bundle(state)
        secret = "github_pat_abcdefghijklmnopqrstuvwxyz012345"
        private_url = "https://github.com/StreamScapeTV/private-media.git"
        result = json.dumps(
            {
                "status": "failed",
                "errorCount": 1,
                "errors": [
                    {
                        "issueType": "Swift Compiler Error",
                        "message": (
                            "cannot convert value; "
                            f"token={secret}; dependency {private_url}; "
                            "::error::not-a-workflow-command"
                        ),
                        "sourceURL": (
                            f"file://{source}/Sources/App.swift"
                            "#StartingLineNumber=42&StartingColumnNumber=17"
                        ),
                    }
                ],
            }
        )
        runner = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(
                outcome=CommandOutcome(
                    65,
                    "SwiftCompile normal arm64 Compiling App.swift\n"
                    "Command SwiftCompile failed with a nonzero exit code\n",
                    "",
                )
            ),
            (source, state, state.parent),
            state_root=state,
        )

        with mock.patch.object(
            ciw_apple,
            "_read_xcresult_build_results",
            return_value=result,
        ) as read_result:
            outcome, diagnostic = self.capture_run(
                runner,
                self.xcode_argv(source, bundle),
                cwd=source,
            )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.returncode, 65)
        read_result.assert_called_once_with(bundle.resolve())
        self.assertIn(
            "Swift Compiler Error: App.swift:42:17: cannot convert value",
            diagnostic,
        )
        self.assertNotIn("SwiftCompile normal arm64", diagnostic)
        self.assertNotIn(str(source), diagnostic)
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn(private_url, diagnostic)
        self.assertIn("<redacted>", diagnostic)
        self.assertIn("<url>", diagnostic)
        self.assertIn("| Swift Compiler Error:", diagnostic)
        self.assertNotIn("\n::error::", diagnostic)

    def test_useful_stdout_does_not_read_xcresult(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        bundle = self.result_bundle(state)
        runner = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(
                outcome=CommandOutcome(
                    65,
                    f"{source}/Sources/App.swift:9:3: error: missing return\n",
                    "",
                )
            ),
            (source, state, state.parent),
            state_root=state,
        )

        with mock.patch.object(ciw_apple, "_read_xcresult_build_results") as read_result:
            _, diagnostic = self.capture_run(
                runner,
                self.xcode_argv(source, bundle),
                cwd=source,
            )

        read_result.assert_not_called()
        self.assertIn("App.swift:9:3: error: missing return", diagnostic)
        self.assertNotIn(str(source), diagnostic)

    def test_corrupt_xcresult_falls_back_to_existing_output(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        bundle = self.result_bundle(state)
        ordinary = (
            "SwiftCompile normal arm64 Compiling App.swift\n"
            "Command SwiftCompile failed with a nonzero exit code\n"
        )
        runner = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(outcome=CommandOutcome(65, ordinary, "")),
            (source, state, state.parent),
            state_root=state,
        )

        with mock.patch.object(
            ciw_apple,
            "_read_xcresult_build_results",
            return_value="{not-json",
        ):
            outcome, diagnostic = self.capture_run(
                runner,
                self.xcode_argv(source, bundle),
                cwd=source,
            )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.returncode, 65)
        self.assertIn("SwiftCompile normal arm64", diagnostic)
        self.assertIn("failed with a nonzero exit code", diagnostic)

    def test_out_of_state_result_bundle_is_never_read(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        outside = Path(temporary.name) / "outside" / "validation.xcresult"
        outside.mkdir(parents=True)
        ordinary = "Command SwiftCompile failed with a nonzero exit code\n"
        runner = ciw_apple._FailureDiagnosticRunner(
            ScriptedRunner(outcome=CommandOutcome(65, ordinary, "")),
            (source, state, state.parent),
            state_root=state,
        )

        with mock.patch.object(ciw_apple, "_read_xcresult_build_results") as read_result:
            _, diagnostic = self.capture_run(
                runner,
                self.xcode_argv(source, outside),
                cwd=source,
            )

        read_result.assert_not_called()
        self.assertIn("failed with a nonzero exit code", diagnostic)

    def test_xcresult_fallback_is_emitted_before_caller_cleanup(self) -> None:
        temporary, source, state = self.roots()
        self.addCleanup(temporary.cleanup)
        bundle = (
            state
            / "apple-validation"
            / "result-bundles"
            / "compile"
            / "validation.xcresult"
        )
        runner = ciw_apple._FailureDiagnosticRunner(
            BundleProducingRunner(
                bundle,
                CommandOutcome(
                    65,
                    "Command SwiftCompile failed with a nonzero exit code\n",
                    "",
                ),
            ),
            (source, state, state.parent),
            state_root=state,
        )
        result = json.dumps(
            {
                "errors": [
                    {
                        "issueType": "Swift Compiler Error",
                        "message": "cannot find symbol in scope",
                        "sourceURL": "file:///private/work/App.swift#StartingLineNumber=5",
                    }
                ]
            }
        )

        def read_before_cleanup(candidate: Path) -> str:
            self.assertEqual(candidate, bundle.resolve())
            self.assertTrue(candidate.is_dir())
            return result

        with mock.patch.object(
            ciw_apple,
            "_read_xcresult_build_results",
            side_effect=read_before_cleanup,
        ):
            _, diagnostic = self.capture_run(
                runner,
                self.xcode_argv(source, bundle),
                cwd=source,
            )

        shutil.rmtree(state / "apple-validation")
        self.assertFalse(bundle.exists())
        self.assertIn("Swift Compiler Error: App.swift:5: cannot find symbol in scope", diagnostic)

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
            state_root=state,
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
            state_root=state,
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
            state_root=state,
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
            state_root=state,
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
        self.assertEqual(runner._state_root, state)


if __name__ == "__main__":
    unittest.main()
