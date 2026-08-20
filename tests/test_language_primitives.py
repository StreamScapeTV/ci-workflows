from __future__ import annotations

import inspect
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast
from unittest import mock

from ci_workflows import language_primitives as primitives
from ci_workflows.language_primitives import (
    CommandOutcome,
    JavaRuntime,
    LanguagePrimitiveError,
    OperationResult,
    android_assemble,
    android_build,
    android_lint,
    android_targeted_test,
    android_unit_test,
    create_python_venv,
    dart_restore,
    flutter_restore,
    inspect_java_runtime,
    install_node_dependencies,
    install_python_dependencies,
    resolve_java_executable,
    resolve_node_runtime,
    resolve_python_interpreter,
    run_dart_operation,
    run_flutter_operation,
    run_gradle_tasks,
    run_node_package_script,
    run_python_module,
    run_python_script,
    run_python_tests,
    validate_java_runtime,
)


@dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


class RecordingRunner:
    def __init__(self, *outcomes: CommandOutcome, error: OSError | None = None) -> None:
        self.outcomes = list(outcomes) or [CommandOutcome(0, "ok\n", "")]
        self.error = error
        self.calls: list[Call] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        self.calls.append(Call(tuple(argv), cwd, dict(env)))
        if self.error is not None:
            raise self.error
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


class LanguagePrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def tool(self, name: str) -> Path:
        path = self.bin / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path.resolve()

    def gradle_wrapper(self) -> Path:
        path = self.project / "gradlew"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_python_resolution_prefers_first_available_executable(self) -> None:
        python = self.tool("python3.12")

        def which(name: str, *, path: str | None = None) -> str | None:
            self.assertEqual(path, "/reviewed/path")
            return str(python) if name == "python3.12" else None

        with mock.patch.object(primitives.shutil, "which", side_effect=which):
            resolved = resolve_python_interpreter(
                ("missing-python", "python3.12"),
                search_path="/reviewed/path",
            )
        self.assertEqual(python, resolved)

    def test_runtime_resolution_fails_cleanly_when_tool_is_missing(self) -> None:
        with mock.patch.object(primitives.shutil, "which", return_value=None):
            with self.assertRaises(LanguagePrimitiveError) as context:
                resolve_python_interpreter(("missing",))
        self.assertEqual("tool_unavailable", context.exception.code)
        self.assertEqual("python.resolve", context.exception.operation)

    def test_create_python_venv_projects_structured_result_and_flags(self) -> None:
        python = self.tool("python")
        runner = RecordingRunner(CommandOutcome(0, "created", ""))
        result = create_python_venv(
            python,
            Path(".venv"),
            working_directory=self.project,
            clear=True,
            system_site_packages=True,
            upgrade_deps=True,
            environment={"CI": "true"},
            runner=runner,
        )
        self.assertEqual(self.project / ".venv", result.root)
        self.assertEqual(result.root / "bin/python", result.interpreter)
        self.assertEqual("python.venv", result.result.operation)
        self.assertEqual(
            (
                str(python),
                "-m",
                "venv",
                "--clear",
                "--system-site-packages",
                "--upgrade-deps",
                str(self.project / ".venv"),
            ),
            runner.calls[0].argv,
        )
        self.assertEqual({"CI": "true"}, runner.calls[0].env)

    def test_python_dependency_install_uses_only_local_declared_sources(self) -> None:
        python = self.tool("python")
        requirements = self.project / "requirements-ci.txt"
        requirements.write_text("example==1.0\n", encoding="utf-8")
        runner = RecordingRunner()
        result = install_python_dependencies(
            python,
            project_directory=self.project,
            requirement_files=(Path("requirements-ci.txt"),),
            editable_project=True,
            options=("--disable-pip-version-check",),
            runner=runner,
        )
        self.assertIsInstance(result, OperationResult)
        self.assertEqual(
            (
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(requirements),
                "-e",
                str(self.project),
            ),
            runner.calls[0].argv,
        )

    def test_python_dependency_install_requires_a_declared_target(self) -> None:
        python = self.tool("python")
        with self.assertRaises(LanguagePrimitiveError) as context:
            install_python_dependencies(python, project_directory=self.project)
        self.assertEqual("request_invalid", context.exception.code)

    def test_python_module_script_and_tests_keep_product_arguments_caller_owned(self) -> None:
        python = self.tool("python")
        script = self.project / "scripts" / "verify.py"
        script.parent.mkdir()
        script.write_text("print('ok')\n", encoding="utf-8")
        runner = RecordingRunner()
        module_result = run_python_module(
            python,
            "tools.verify",
            project_directory=self.project,
            arguments=("--mode", "strict"),
            runner=runner,
        )
        script_result = run_python_script(
            python,
            Path("scripts/verify.py"),
            project_directory=self.project,
            arguments=("fixture.json",),
            runner=runner,
        )
        tests_result = run_python_tests(
            python,
            project_directory=self.project,
            test_module="unittest",
            arguments=("discover", "-s", "tests"),
            runner=runner,
        )
        self.assertEqual("python.module", module_result.operation)
        self.assertEqual("python.script", script_result.operation)
        self.assertEqual("python.tests", tests_result.operation)
        self.assertEqual(
            (str(python), "-m", "tools.verify", "--mode", "strict"),
            runner.calls[0].argv,
        )
        self.assertEqual(
            (str(python), str(script), "fixture.json"),
            runner.calls[1].argv,
        )
        self.assertEqual(
            (str(python), "-m", "unittest", "discover", "-s", "tests"),
            runner.calls[2].argv,
        )

    def test_python_script_and_module_reject_unsafe_paths_and_names(self) -> None:
        python = self.tool("python")
        outside = self.root / "outside.py"
        outside.write_text("print('outside')\n", encoding="utf-8")
        link = self.project / "linked.py"
        link.symlink_to(outside)
        with self.assertRaises(LanguagePrimitiveError):
            run_python_script(
                python,
                link,
                project_directory=self.project,
                runner=RecordingRunner(),
            )
        with self.assertRaises(LanguagePrimitiveError):
            run_python_module(
                python,
                "bad-module;echo",
                project_directory=self.project,
                runner=RecordingRunner(),
            )

    def test_node_runtime_install_and_package_script_are_process_bounded(self) -> None:
        node = self.tool("node")
        npm = self.tool("npm")

        def which(name: str, *, path: str | None = None) -> str | None:
            return {"node": str(node), "npm": str(npm)}.get(name)

        with mock.patch.object(primitives.shutil, "which", side_effect=which):
            runtime = resolve_node_runtime()
        self.assertEqual(node, runtime.node)
        self.assertEqual(npm, runtime.package_manager)

        runner = RecordingRunner()
        install = install_node_dependencies(
            runtime.package_manager,
            project_directory=self.project,
            mode="ci",
            options=("--ignore-scripts",),
            runner=runner,
        )
        script = run_node_package_script(
            runtime.package_manager,
            "test:unit",
            project_directory=self.project,
            arguments=("tests/smoke.test.ts",),
            runner=runner,
        )
        self.assertEqual("node.install", install.operation)
        self.assertEqual("node.script", script.operation)
        self.assertEqual((str(npm), "ci", "--ignore-scripts"), runner.calls[0].argv)
        self.assertEqual(
            (str(npm), "run", "test:unit", "--", "tests/smoke.test.ts"),
            runner.calls[1].argv,
        )

    def test_node_package_script_rejects_control_or_shell_shaped_names(self) -> None:
        npm = self.tool("npm")
        for value in ("", "test\nnext", "test;echo"):
            with self.subTest(value=value), self.assertRaises(LanguagePrimitiveError):
                run_node_package_script(
                    npm,
                    value,
                    project_directory=self.project,
                    runner=RecordingRunner(),
                )

    def test_java_inspection_validates_modern_and_legacy_runtime_versions(self) -> None:
        java = self.tool("java")
        modern = RecordingRunner(
            CommandOutcome(0, "", 'openjdk version "21.0.8" 2025-07-15\n')
        )
        runtime = inspect_java_runtime(
            java,
            working_directory=self.project,
            runner=modern,
        )
        self.assertEqual("21.0.8", runtime.version)
        self.assertEqual(21, runtime.major)
        self.assertIs(
            runtime,
            validate_java_runtime(runtime, expected_major=21, exact_version="21.0.8"),
        )

        legacy = RecordingRunner(CommandOutcome(0, "", 'java version "1.8.0_452"\n'))
        old_runtime = inspect_java_runtime(
            java,
            working_directory=self.project,
            runner=legacy,
        )
        self.assertEqual(8, old_runtime.major)

    def test_java_resolution_and_validation_fail_with_stable_codes(self) -> None:
        java = self.tool("java")
        with mock.patch.object(primitives.shutil, "which", return_value=str(java)):
            self.assertEqual(java, resolve_java_executable())
        malformed = RecordingRunner(CommandOutcome(0, "", "not a java version"))
        with self.assertRaises(LanguagePrimitiveError) as context:
            inspect_java_runtime(java, working_directory=self.project, runner=malformed)
        self.assertEqual("java_version_invalid", context.exception.code)

        runtime = JavaRuntime(
            executable=java,
            version="17.0.12",
            major=17,
            result=OperationResult("java.inspect", 0, "", ""),
        )
        with self.assertRaises(LanguagePrimitiveError) as mismatch:
            validate_java_runtime(runtime, expected_major=21)
        self.assertEqual("java_version_mismatch", mismatch.exception.code)

    def test_gradle_tasks_use_exact_wrapper_project_tasks_and_options(self) -> None:
        wrapper = self.gradle_wrapper()
        runner = RecordingRunner()
        result = run_gradle_tasks(
            Path("gradlew"),
            (":app:assembleDebug", ":lib:test"),
            project_directory=self.project,
            options=("--stacktrace", "--no-daemon"),
            environment={"GRADLE_USER_HOME": "/tmp/gradle"},
            runner=runner,
        )
        self.assertEqual("gradle.tasks", result.operation)
        self.assertEqual(
            (
                str(wrapper.resolve()),
                ":app:assembleDebug",
                ":lib:test",
                "--stacktrace",
                "--no-daemon",
            ),
            runner.calls[0].argv,
        )
        self.assertEqual({"GRADLE_USER_HOME": "/tmp/gradle"}, runner.calls[0].env)

    def test_gradle_rejects_empty_or_option_shaped_tasks_and_bad_wrapper(self) -> None:
        wrapper = self.gradle_wrapper()
        with self.assertRaises(LanguagePrimitiveError):
            run_gradle_tasks(wrapper, (), project_directory=self.project, runner=RecordingRunner())
        with self.assertRaises(LanguagePrimitiveError):
            run_gradle_tasks(
                wrapper,
                ("--project-dir",),
                project_directory=self.project,
                runner=RecordingRunner(),
            )
        wrapper.chmod(0o644)
        with self.assertRaises(LanguagePrimitiveError) as context:
            run_gradle_tasks(
                wrapper,
                ("assemble",),
                project_directory=self.project,
                runner=RecordingRunner(),
            )
        self.assertEqual("path_not_executable", context.exception.code)

    def test_android_helpers_keep_tasks_caller_supplied_and_operations_structured(self) -> None:
        wrapper = self.gradle_wrapper()
        runner = RecordingRunner()
        results = (
            android_build(wrapper, "buildFlavor", project_directory=self.project, runner=runner),
            android_assemble(
                wrapper, ":app:assembleQa", project_directory=self.project, runner=runner
            ),
            android_unit_test(
                wrapper,
                ":app:testQaUnitTest",
                project_directory=self.project,
                runner=runner,
            ),
            android_lint(wrapper, ":app:lintQa", project_directory=self.project, runner=runner),
        )
        self.assertEqual(
            ("android.build", "android.assemble", "android.unit_test", "android.lint"),
            tuple(result.operation for result in results),
        )
        self.assertEqual("buildFlavor", runner.calls[0].argv[1])
        self.assertEqual(":app:assembleQa", runner.calls[1].argv[1])
        self.assertEqual(":app:testQaUnitTest", runner.calls[2].argv[1])
        self.assertEqual(":app:lintQa", runner.calls[3].argv[1])

    def test_android_targeted_test_adds_only_the_explicit_selector(self) -> None:
        wrapper = self.gradle_wrapper()
        runner = RecordingRunner()
        result = android_targeted_test(
            wrapper,
            ":app:testQaUnitTest",
            "com.example.PlayerTest.oneCase",
            project_directory=self.project,
            options=("--no-daemon",),
            runner=runner,
        )
        self.assertEqual("android.targeted_test", result.operation)
        self.assertEqual(
            (
                str(wrapper.resolve()),
                ":app:testQaUnitTest",
                "--no-daemon",
                "--tests",
                "com.example.PlayerTest.oneCase",
            ),
            runner.calls[0].argv,
        )

    def test_flutter_and_dart_restore_and_operations_use_caller_arguments(self) -> None:
        flutter = self.tool("flutter")
        dart = self.tool("dart")
        runner = RecordingRunner()
        results = (
            flutter_restore(flutter, project_directory=self.project, runner=runner),
            dart_restore(dart, project_directory=self.project, runner=runner),
            run_flutter_operation(
                flutter,
                "build",
                project_directory=self.project,
                arguments=("apk", "--debug"),
                runner=runner,
            ),
            run_flutter_operation(
                flutter,
                "test",
                project_directory=self.project,
                arguments=("test/player_test.dart",),
                runner=runner,
            ),
            run_flutter_operation(
                flutter,
                "analyze",
                project_directory=self.project,
                arguments=("lib",),
                runner=runner,
            ),
            run_dart_operation(
                dart,
                "test",
                project_directory=self.project,
                arguments=("test/unit_test.dart",),
                runner=runner,
            ),
            run_dart_operation(
                dart,
                "analyze",
                project_directory=self.project,
                arguments=("lib",),
                runner=runner,
            ),
        )
        self.assertEqual(
            (
                "flutter.restore",
                "dart.restore",
                "flutter.build",
                "flutter.test",
                "flutter.analyze",
                "dart.test",
                "dart.analyze",
            ),
            tuple(item.operation for item in results),
        )
        self.assertEqual((str(flutter), "pub", "get"), runner.calls[0].argv)
        self.assertEqual((str(dart), "pub", "get"), runner.calls[1].argv)
        self.assertEqual((str(flutter), "build", "apk", "--debug"), runner.calls[2].argv)

    def test_flutter_and_dart_reject_unreviewed_operation_names(self) -> None:
        flutter = self.tool("flutter")
        dart = self.tool("dart")
        with self.assertRaises(LanguagePrimitiveError):
            run_flutter_operation(
                flutter,
                cast(object, "doctor"),
                project_directory=self.project,
                runner=RecordingRunner(),
            )
        with self.assertRaises(LanguagePrimitiveError):
            run_dart_operation(
                dart,
                cast(object, "compile"),
                project_directory=self.project,
                runner=RecordingRunner(),
            )

    def test_command_failure_is_structured_and_does_not_project_output_or_environment(self) -> None:
        npm = self.tool("npm")
        secret = "credential-material-that-must-not-project"
        runner = RecordingRunner(CommandOutcome(23, secret, secret))
        with self.assertRaises(LanguagePrimitiveError) as context:
            install_node_dependencies(
                npm,
                project_directory=self.project,
                environment={"PRIVATE_TOKEN": secret},
                runner=runner,
            )
        error = context.exception
        self.assertEqual("command_failed", error.code)
        self.assertEqual("node.install", error.operation)
        self.assertEqual(23, error.returncode)
        self.assertNotIn(secret, str(error))
        self.assertNotIn("PRIVATE_TOKEN", str(error))

    def test_process_launch_error_maps_to_stable_command_unavailable(self) -> None:
        npm = self.tool("npm")
        with self.assertRaises(LanguagePrimitiveError) as context:
            install_node_dependencies(
                npm,
                project_directory=self.project,
                runner=RecordingRunner(error=OSError("host detail")),
            )
        self.assertEqual("command_unavailable", context.exception.code)
        self.assertNotIn("host detail", str(context.exception))

    def test_environment_and_arguments_reject_controls_without_shell_interpretation(self) -> None:
        npm = self.tool("npm")
        with self.assertRaises(LanguagePrimitiveError):
            install_node_dependencies(
                npm,
                project_directory=self.project,
                environment={"BAD\nKEY": "value"},
                runner=RecordingRunner(),
            )
        with self.assertRaises(LanguagePrimitiveError):
            run_node_package_script(
                npm,
                "test",
                project_directory=self.project,
                arguments=("bad\nargument",),
                runner=RecordingRunner(),
            )

    def test_public_api_has_no_product_or_secret_specific_parameters(self) -> None:
        public_functions = (
            resolve_python_interpreter,
            create_python_venv,
            install_python_dependencies,
            run_python_module,
            run_python_script,
            run_python_tests,
            resolve_node_runtime,
            install_node_dependencies,
            run_node_package_script,
            resolve_java_executable,
            inspect_java_runtime,
            validate_java_runtime,
            run_gradle_tasks,
            android_build,
            android_assemble,
            android_unit_test,
            android_lint,
            android_targeted_test,
            flutter_restore,
            dart_restore,
            run_flutter_operation,
            run_dart_operation,
        )
        forbidden_names = {"secret", "token", "password", "product", "registry", "cache"}
        for function in public_functions:
            with self.subTest(function=function.__name__):
                names = set(inspect.signature(function).parameters)
                self.assertTrue(names.isdisjoint(forbidden_names))

        source = Path(primitives.__file__).read_text(encoding="utf-8").casefold()
        for forbidden in ("streamscape", "iptv-", "actions/cache", "github actions cache"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
