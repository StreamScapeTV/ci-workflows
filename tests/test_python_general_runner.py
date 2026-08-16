from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import python as python_validation
from ci_workflows import python_host_execution
from ci_workflows.language_primitives import OperationResult, PythonVenv
from ci_workflows.python_types import PythonCommand, PythonValidationPlan
from ci_workflows.runtime_primitives import ProcessResult

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class PythonGeneralRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = python_validation.load_python_contract(ROOT)

    def backend_plan(self) -> PythonValidationPlan:
        request = python_validation.PythonValidationRequest(
            repository="StreamScapeTV/iptv-backend",
            admitted_sha=SHA,
            validation_profile="host",
            command_profile="locked-test",
            working_directory=".",
            version_file=None,
            script_path="scripts/run_release_gates.sh",
            artifact_exception_id=None,
            source_trust="trusted-pr",
        )
        return python_validation.resolve_validation_plan(self.contract, request)

    def test_backend_host_plan_uses_general_python_family_and_product_commands(self) -> None:
        plan = self.backend_plan()
        self.assertEqual("portable", plan.runner_profile)
        self.assertEqual("host-cpython-3.12", plan.runtime_id)
        self.assertEqual("3.12", plan.python_version)
        self.assertEqual("requirements.txt", plan.dependency_file)
        self.assertEqual(
            [command.argv for command in plan.commands],
            [
                ("./scripts/run_release_gates.sh",),
                ("python3", "-m", "pytest", "-q"),
            ],
        )

    def test_runtime_match_accepts_any_312_patch_and_rejects_other_families(self) -> None:
        for version in ("3.12.0", "3.12.14", "3.12.99"):
            with self.subTest(version=version):
                self.assertTrue(
                    python_host_execution._matches_runtime(version, "3.12")
                )
        for version in ("3.11.9", "3.13.0", "3.12"):
            with self.subTest(version=version):
                self.assertFalse(
                    python_host_execution._matches_runtime(version, "3.12")
                )

    def test_host_execution_uses_language_primitives_for_restore_and_pytest(self) -> None:
        plan = self.backend_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            state = root / "state"
            source.mkdir()
            state.mkdir()
            requirements = source / "requirements.txt"
            requirements.write_text("example==1.0\n", encoding="utf-8")
            script = source / "scripts/run_release_gates.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            interpreter = root / "python3.12"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            venv_root = state / "python-validation/host/venv"
            venv_interpreter = venv_root / "bin/python"
            result = OperationResult("python.venv", 0, "", "")
            venv = PythonVenv(venv_root, venv_interpreter, result)

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
                    "run_python_tests",
                    return_value=OperationResult("python.tests", 0, "", ""),
                ) as tests,
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
                    {"PATH": os.environ.get("PATH", "")},
                )

        self.assertEqual(2, stage_count)
        self.assertEqual("3.12.14", version)
        resolve.assert_called_once_with(
            ("python3.12", "python3"),
            search_path=mock.ANY,
        )
        self.assertEqual(interpreter, create_venv.call_args.args[0])
        self.assertEqual((Path("requirements.txt"),), install.call_args.kwargs["requirement_files"])
        self.assertEqual(("--no-input", "--no-cache-dir"), install.call_args.kwargs["options"])
        self.assertEqual("pytest", tests.call_args.kwargs["test_module"])
        self.assertEqual(("-q",), tests.call_args.kwargs["arguments"])
        self.assertEqual(
            ("./scripts/run_release_gates.sh",),
            tuple(process.call_args.args[0]),
        )

    def test_host_execution_rejects_non_312_runner_before_dependency_restore(self) -> None:
        plan = self.backend_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            state = root / "state"
            source.mkdir()
            state.mkdir()
            (source / "requirements.txt").write_text(
                "example==1.0\n",
                encoding="utf-8",
            )
            script = source / "scripts/run_release_gates.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            interpreter = root / "python3.13"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            with (
                mock.patch.object(
                    python_host_execution,
                    "resolve_python_interpreter",
                    return_value=interpreter,
                ),
                mock.patch.object(
                    python_host_execution,
                    "_inspect_python",
                    return_value="3.13.0",
                ),
                mock.patch.object(
                    python_host_execution,
                    "create_python_venv",
                ) as create_venv,
            ):
                with self.assertRaisesRegex(
                    python_validation.PythonValidationError,
                    "python_version_drift",
                ):
                    python_host_execution.execute_host_plan(
                        source,
                        state,
                        plan,
                        {"PATH": os.environ.get("PATH", "")},
                    )
        create_venv.assert_not_called()

    def test_host_result_reports_the_resolved_runner_patch(self) -> None:
        result = python_host_execution.result_from_host_plan(
            self.backend_plan(),
            2,
            "3.12.14",
        )
        values = result.output_values()
        self.assertEqual("3.12.14", values["resolved_python_version"])
        self.assertEqual("success", values["result"])
        self.assertRegex(values["evidence_id"], r"^python-[a-z0-9]{28}$")


if __name__ == "__main__":
    unittest.main()
