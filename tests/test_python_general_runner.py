from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import python as python_validation
from ci_workflows import python_host_execution
from ci_workflows.language_primitives import OperationResult, PythonVenv
from ci_workflows.runtime_primitives import ProcessResult

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class PythonGeneralRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = python_validation.load_python_contract(ROOT)

    def plan(self, *, dependency_file: str | None = "requirements.lock"):
        request = python_validation.PythonValidationRequest(
            repository="ExampleOrg/example-service",
            admitted_sha=SHA,
            validation_profile="host",
            python_version="3.12",
            working_directory=".",
            version_file=None,
            dependency_file=dependency_file,
            script_path="ci/validate.sh",
            artifact_exception_id=None,
            source_trust="trusted-pr",
        )
        return python_validation.resolve_validation_plan(self.contract, request)

    def test_host_plan_uses_general_python_family_without_product_commands(self) -> None:
        plan = self.plan()
        self.assertEqual("portable", plan.runner_profile)
        self.assertEqual("host-cpython-3.12", plan.runtime_id)
        self.assertEqual("3.12", plan.python_version)
        self.assertEqual("requirements.lock", plan.dependency_file)
        self.assertEqual("ci/validate.sh", plan.script_path)
        self.assertFalse(hasattr(plan, "commands"))
        self.assertFalse(hasattr(plan, "command_profile"))
        self.assertFalse(hasattr(plan, "environment"))

    def test_runtime_match_accepts_any_312_patch_and_rejects_other_families(self) -> None:
        for version in ("3.12.0", "3.12.14", "3.12.99"):
            with self.subTest(version=version):
                self.assertTrue(python_host_execution._matches_runtime(version, "3.12"))
        for version in ("3.11.9", "3.13.0", "3.12"):
            with self.subTest(version=version):
                self.assertFalse(python_host_execution._matches_runtime(version, "3.12"))

    def test_host_execution_restores_optional_lock_then_runs_one_script(self) -> None:
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            state = root / "state"
            source.mkdir()
            state.mkdir()
            (source / "requirements.lock").write_text("example==1.0\n", encoding="utf-8")
            script = source / "ci/validate.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            interpreter = root / "python3.12"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            venv_root = state / "python-validation/host/venv"
            venv_interpreter = venv_root / "bin/python"
            venv = PythonVenv(
                venv_root,
                venv_interpreter,
                OperationResult("python.venv", 0, "", ""),
            )

            with (
                mock.patch.object(
                    python_host_execution,
                    "resolve_python_interpreter",
                    return_value=interpreter,
                ) as resolve,
                mock.patch.object(
                    python_host_execution,
                    "_inspect_python",
                    return_value="3.12.14",
                ),
                mock.patch.object(
                    python_host_execution,
                    "create_python_venv",
                    return_value=venv,
                ) as create_venv,
                mock.patch.object(
                    python_host_execution,
                    "install_python_dependencies",
                    return_value=OperationResult("python.install", 0, "", ""),
                ) as install,
                mock.patch.object(
                    python_host_execution,
                    "run_process",
                    return_value=ProcessResult(0, "", "", False),
                ) as process,
            ):
                stage_count, version = python_host_execution.execute_host_plan(
                    source,
                    state,
                    plan,
                    {
                        "PATH": os.environ.get("PATH", ""),
                        "PRODUCT_SECRET": "must-not-cross-boundary",
                    },
                )

        self.assertEqual(1, stage_count)
        self.assertEqual("3.12.14", version)
        resolve.assert_called_once_with(("python3.12", "python3"), search_path=mock.ANY)
        self.assertEqual(interpreter, create_venv.call_args.args[0])
        self.assertEqual((Path("requirements.lock"),), install.call_args.kwargs["requirement_files"])
        self.assertEqual(("--no-input", "--no-cache-dir"), install.call_args.kwargs["options"])
        argv = tuple(process.call_args.args[0])
        self.assertTrue(argv[0].endswith("/work/ci/validate.sh"), argv)
        environment = process.call_args.kwargs["environment"]
        self.assertNotIn("PRODUCT_SECRET", environment)
        self.assertEqual("true", environment["CI"])

    def test_host_execution_rejects_non_312_runner_before_dependency_restore(self) -> None:
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            state = root / "state"
            source.mkdir()
            state.mkdir()
            (source / "requirements.lock").write_text("example==1.0\n", encoding="utf-8")
            script = source / "ci/validate.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            interpreter = root / "python3.13"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            with (
                mock.patch.object(python_host_execution, "resolve_python_interpreter", return_value=interpreter),
                mock.patch.object(python_host_execution, "_inspect_python", return_value="3.13.0"),
                mock.patch.object(python_host_execution, "create_python_venv") as create_venv,
            ):
                with self.assertRaises(PythonValidationError) as caught:
                    python_host_execution.execute_host_plan(
                        source,
                        state,
                        plan,
                        {"PATH": os.environ.get("PATH", "")},
                    )
        self.assertEqual("python_version_drift", caught.exception.code)
        create_venv.assert_not_called()

    def test_host_result_reports_resolved_patch_and_script_contract(self) -> None:
        result = python_host_execution.result_from_host_plan(self.plan(), 1, "3.12.14")
        values = result.output_values()
        self.assertEqual("3.12.14", values["resolved_python_version"])
        self.assertEqual("success", values["result"])
        self.assertIn("consumer-owned-executable", values["test_summary"])
        self.assertRegex(values["evidence_id"], r"^python-[a-z0-9]{28}$")


PythonValidationError = python_validation.PythonValidationError


if __name__ == "__main__":
    unittest.main()
