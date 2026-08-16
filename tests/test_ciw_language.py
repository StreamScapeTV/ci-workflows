from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.ciw_language import configure_language, execute_language
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.language_primitives import JavaRuntime, NodeRuntime, OperationResult


class LanguageCIWAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.project = self.workspace / "source"
        self.project.mkdir(parents=True)
        (self.project / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(self.project / "gradlew", 0o755)
        (self.project / "quality.py").write_text("print('ok')\n", encoding="utf-8")
        self.context = CIWContext(
            root=self.workspace,
            environment={"GITHUB_WORKSPACE": str(self.workspace), "PATH": "/tools/bin"},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def args(**values: object) -> argparse.Namespace:
        defaults: dict[str, object] = {
            "operation": "python-tests",
            "project_root": "source",
            "working_directory": ".",
            "arguments_json": None,
            "script_path": None,
            "package_script": None,
            "install_mode": None,
            "expected_java_major": None,
            "exact_java_version": None,
            "gradle_wrapper_path": None,
            "tasks_json": None,
            "task": None,
            "test_selector": None,
            "test_module": None,
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_parser_exposes_technology_operations_not_product_profiles(self) -> None:
        parser = argparse.ArgumentParser()
        configure_language(parser)
        parsed = parser.parse_args(["--operation", "gradle-tasks", "--tasks-json", '[":app:test"]'])
        self.assertEqual(parsed.operation, "gradle-tasks")
        self.assertFalse(hasattr(parsed, "consumer"))
        self.assertFalse(hasattr(parsed, "product_id"))
        self.assertFalse(hasattr(parsed, "runner"))

    def test_python_tests_delegate_module_and_bounded_arguments(self) -> None:
        python = Path("/tools/bin/python3")
        result = OperationResult("python.tests", 0, "", "")
        with (
            patch("ci_workflows.ciw_language.resolve_python_interpreter", return_value=python) as resolve,
            patch("ci_workflows.ciw_language.run_python_tests", return_value=result) as primitive,
        ):
            response = execute_language(
                self.args(arguments_json='["tests/unit","-q"]', test_module="pytest"),
                self.context,
            )
        resolve.assert_called_once_with(search_path="/tools/bin")
        self.assertEqual(primitive.call_args.kwargs["project_directory"], self.project.resolve())
        self.assertEqual(primitive.call_args.kwargs["arguments"], ("tests/unit", "-q"))
        self.assertIn('"operation":"python.tests"', response.outputs["language_result_json"])

    def test_node_script_uses_caller_script_name_without_central_repository_lookup(self) -> None:
        runtime = NodeRuntime(Path("/tools/bin/node"), Path("/tools/bin/npm"))
        result = OperationResult("node.script", 0, "", "")
        with (
            patch("ci_workflows.ciw_language.resolve_node_runtime", return_value=runtime),
            patch("ci_workflows.ciw_language.run_node_package_script", return_value=result) as primitive,
        ):
            execute_language(
                self.args(operation="node-script", package_script="test", arguments_json='["--runInBand"]'),
                self.context,
            )
        self.assertEqual(primitive.call_args.args[1], "test")
        self.assertEqual(primitive.call_args.kwargs["arguments"], ("--runInBand",))

    def test_gradle_tasks_are_explicit_and_wrapper_is_bounded_to_project(self) -> None:
        result = OperationResult("gradle.tasks", 0, "", "")
        with patch("ci_workflows.ciw_language.run_gradle_tasks", return_value=result) as primitive:
            execute_language(
                self.args(operation="gradle-tasks", tasks_json='[":app:testDebugUnitTest",":app:lintDebug"]'),
                self.context,
            )
        self.assertEqual(primitive.call_args.args[0], (self.project / "gradlew").resolve())
        self.assertEqual(
            primitive.call_args.args[1],
            (":app:testDebugUnitTest", ":app:lintDebug"),
        )
        with self.assertRaises(CIWError):
            execute_language(
                self.args(
                    operation="gradle-tasks",
                    gradle_wrapper_path="../gradlew",
                    tasks_json='[":app:test"]',
                ),
                self.context,
            )

    def test_android_targeted_test_delegates_task_and_selector(self) -> None:
        result = OperationResult("android.targeted_test", 0, "", "")
        with patch("ci_workflows.ciw_language.android_targeted_test", return_value=result) as primitive:
            response = execute_language(
                self.args(
                    operation="android-targeted-test",
                    task=":app:testDebugUnitTest",
                    test_selector="com.example.FeatureTest",
                    arguments_json='["--stacktrace"]',
                ),
                self.context,
            )
        self.assertEqual(primitive.call_args.args[1], ":app:testDebugUnitTest")
        self.assertEqual(primitive.call_args.args[2], "com.example.FeatureTest")
        self.assertIn("android.targeted_test", response.outputs["language_result_json"])

    def test_java_verify_projects_only_version_metadata(self) -> None:
        java = Path("/tools/bin/java")
        inspected = JavaRuntime(java, "25.0.2", 25, OperationResult("java.inspect", 0, "", ""))
        with (
            patch("ci_workflows.ciw_language.resolve_java_executable", return_value=java),
            patch("ci_workflows.ciw_language.inspect_java_runtime", return_value=inspected),
            patch("ci_workflows.ciw_language.validate_java_runtime", return_value=inspected) as validate,
        ):
            response = execute_language(
                self.args(operation="java-verify", expected_java_major=25),
                self.context,
            )
        self.assertEqual(validate.call_args.kwargs["expected_major"], 25)
        self.assertIn('"major":25', response.outputs["language_result_json"])
        self.assertNotIn("/tools/bin/java", response.outputs["language_result_json"])


if __name__ == "__main__":
    unittest.main()
