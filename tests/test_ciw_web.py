from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.ciw_types import CIWContext
from ci_workflows.ciw_web import StaticWebValidationError, execute_static_web_validate
from ci_workflows.runtime_primitives import ProcessResult, RuntimePrimitiveError


class StaticWebAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        scripts = self.root / "scripts"
        scripts.mkdir()
        self.build = scripts / "build.sh"
        self.build.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.build.chmod(0o755)
        self.verify = scripts / "verify.sh"
        self.verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.verify.chmod(0o755)
        self.output_file = self.root / "github-output"
        self.stderr = io.StringIO()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(self, **updates: object) -> str:
        value: dict[str, object] = {
            "build_script_path": "scripts/build.sh",
            "static_output_directory": "dist",
            "expected_files": ["index.html", "assets/app.js"],
        }
        value.update(updates)
        return json.dumps(value, separators=(",", ":"))

    def _context(self, plan: str | None = None) -> CIWContext:
        environment = {
            "GITHUB_WORKSPACE": str(self.root),
            "GITHUB_OUTPUT": str(self.output_file),
            "INPUT_ADMITTED_SHA": "a" * 40,
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_VALIDATION_PLAN_JSON": plan or self._plan(),
            "PATH": "/usr/bin:/bin",
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "UNRELATED_SECRET": "must-not-reach-build",
        }
        return CIWContext(
            root=self.root,
            environment=environment,
            stdout=io.StringIO(),
            stderr=self.stderr,
        )

    def _create_output(self) -> None:
        output = self.root / "dist"
        output.mkdir()
        (output / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
        assets = output / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('ok')\n", encoding="utf-8")

    def _outputs(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if not self.output_file.exists():
            return result
        for line in self.output_file.read_text(encoding="utf-8").splitlines():
            name, value = line.split("=", 1)
            result[name] = value
        return result

    def test_success_builds_verifies_expected_files_and_removes_output(self) -> None:
        calls: list[tuple[tuple[str, ...], Path, dict[str, str], float | None]] = []

        def fake_run(arguments, *, cwd, environment, stdin="", timeout_seconds=None):
            calls.append((tuple(arguments), cwd, dict(environment), timeout_seconds))
            self._create_output()
            return ProcessResult(0, "built", "", False)

        with patch("ci_workflows.ciw_web.run_process", side_effect=fake_run):
            result = execute_static_web_validate(argparse.Namespace(), self._context())

        self.assertEqual("success", result.outputs["result"])
        self.assertEqual("success", result.outputs["build_result"])
        self.assertEqual("true", result.outputs["output_verified"])
        self.assertEqual("2", result.outputs["output_file_count"])
        self.assertEqual(64, len(result.outputs["output_digest"]))
        self.assertEqual("success", result.outputs["cleanup_result"])
        self.assertFalse((self.root / "dist").exists())
        self.assertEqual(1, len(calls))
        argv, cwd, environment, timeout = calls[0]
        self.assertEqual(str(self.build), argv[0])
        self.assertEqual(self.root, cwd)
        self.assertEqual(1200, timeout)
        self.assertEqual(str(self.root / "dist"), environment["CIW_STATIC_OUTPUT_DIRECTORY"])
        self.assertEqual("dist", environment["CIW_STATIC_OUTPUT_RELATIVE"])
        self.assertNotIn("UNRELATED_SECRET", environment)

    def test_optional_verifier_uses_existing_immutable_output_primitive(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(arguments, *, cwd, environment, stdin="", timeout_seconds=None):
            argv = tuple(arguments)
            calls.append(argv)
            if argv[0] == str(self.build):
                self._create_output()
                return ProcessResult(0, "", "", False)
            self.assertEqual(str(self.verify), argv[0])
            self.assertEqual(self.root / "dist", cwd)
            return ProcessResult(0, "verified", "", False)

        plan = self._plan(
            verification_script_path="scripts/verify.sh",
            verification_arguments=["--strict"],
        )
        with patch("ci_workflows.ciw_web.run_process", side_effect=fake_run):
            result = execute_static_web_validate(
                argparse.Namespace(),
                self._context(plan),
            )

        self.assertEqual(2, len(calls))
        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual("success", summary["verification_result"])
        self.assertFalse((self.root / "dist").exists())

    def test_verifier_mutation_fails_closed_and_output_is_still_removed(self) -> None:
        def fake_run(arguments, *, cwd, environment, stdin="", timeout_seconds=None):
            if tuple(arguments)[0] == str(self.build):
                self._create_output()
            else:
                (cwd / "index.html").write_text("mutated\n", encoding="utf-8")
            return ProcessResult(0, "", "", False)

        plan = self._plan(verification_script_path="scripts/verify.sh")
        with patch("ci_workflows.ciw_web.run_process", side_effect=fake_run):
            with self.assertRaisesRegex(
                StaticWebValidationError,
                "verification_mutated_output",
            ):
                execute_static_web_validate(argparse.Namespace(), self._context(plan))

        outputs = self._outputs()
        self.assertEqual("verification_mutated_output", outputs["failure_code"])
        self.assertEqual("success", outputs["cleanup_result"])
        self.assertFalse((self.root / "dist").exists())

    def test_build_failure_reports_bounded_diagnostic_and_cleans_partial_output(self) -> None:
        def fake_run(arguments, *, cwd, environment, stdin="", timeout_seconds=None):
            output = self.root / "dist"
            output.mkdir()
            (output / "partial.txt").write_text("partial\n", encoding="utf-8")
            return ProcessResult(
                7,
                f"token=top-secret\nfailed under {self.root}\n",
                "normal build error\n",
                False,
            )

        with patch("ci_workflows.ciw_web.run_process", side_effect=fake_run):
            with self.assertRaisesRegex(StaticWebValidationError, "static_web_build_failed"):
                execute_static_web_validate(argparse.Namespace(), self._context())

        outputs = self._outputs()
        self.assertEqual("failure", outputs["result"])
        self.assertEqual("failure", outputs["build_result"])
        self.assertEqual("static_web_build_failed", outputs["failure_code"])
        self.assertEqual("success", outputs["cleanup_result"])
        self.assertFalse((self.root / "dist").exists())
        diagnostic = self.stderr.getvalue()
        self.assertIn("normal build error", diagnostic)
        self.assertIn("token=<redacted>", diagnostic)
        self.assertIn("<project>", diagnostic)
        self.assertNotIn("top-secret", diagnostic)

    def test_missing_expected_file_fails_after_manifest_and_cleans_output(self) -> None:
        def fake_run(arguments, *, cwd, environment, stdin="", timeout_seconds=None):
            output = self.root / "dist"
            output.mkdir()
            (output / "index.html").write_text("ok\n", encoding="utf-8")
            return ProcessResult(0, "", "", False)

        with patch("ci_workflows.ciw_web.run_process", side_effect=fake_run):
            with self.assertRaisesRegex(
                StaticWebValidationError,
                "static_web_expected_file_missing",
            ):
                execute_static_web_validate(argparse.Namespace(), self._context())

        self.assertEqual(
            "static_web_expected_file_missing",
            self._outputs()["failure_code"],
        )
        self.assertFalse((self.root / "dist").exists())

    def test_preexisting_output_is_never_claimed_or_deleted(self) -> None:
        output = self.root / "dist"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep\n", encoding="utf-8")

        with patch("ci_workflows.ciw_web.run_process") as process:
            with self.assertRaisesRegex(
                StaticWebValidationError,
                "static_web_output_preexisting",
            ):
                execute_static_web_validate(argparse.Namespace(), self._context())

        process.assert_not_called()
        self.assertTrue(marker.exists())
        self.assertEqual("static_web_output_preexisting", self._outputs()["failure_code"])

    def test_primary_build_failure_is_preserved_when_cleanup_also_fails(self) -> None:
        def fake_run(arguments, *, cwd, environment, stdin="", timeout_seconds=None):
            self._create_output()
            return ProcessResult(9, "", "build failed", False)

        with (
            patch("ci_workflows.ciw_web.run_process", side_effect=fake_run),
            patch(
                "ci_workflows.ciw_web.finalize_temporary_path",
                side_effect=RuntimePrimitiveError("cleanup_failed"),
            ),
        ):
            with self.assertRaises(StaticWebValidationError) as raised:
                execute_static_web_validate(argparse.Namespace(), self._context())

        self.assertEqual("static_web_build_failed", raised.exception.code)
        self.assertEqual("static_web_cleanup_failed", raised.exception.cleanup_code)
        outputs = self._outputs()
        self.assertEqual("static_web_build_failed", outputs["failure_code"])
        self.assertEqual("failure", outputs["cleanup_result"])
        self.assertEqual("static_web_cleanup_failed", outputs["cleanup_code"])

    def test_plan_rejects_shell_command_and_path_escape_shapes(self) -> None:
        cases = (
            self._plan(build_script_path="../build.sh"),
            self._plan(build_script_path="scripts/build.sh\nrm -rf /"),
            self._plan(static_output_directory="../dist"),
            self._plan(build_arguments="--not-an-array"),
        )
        for plan in cases:
            with self.subTest(plan=plan):
                self.output_file.unlink(missing_ok=True)
                with patch("ci_workflows.ciw_web.run_process") as process:
                    with self.assertRaises(StaticWebValidationError):
                        execute_static_web_validate(
                            argparse.Namespace(),
                            self._context(plan),
                        )
                process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
