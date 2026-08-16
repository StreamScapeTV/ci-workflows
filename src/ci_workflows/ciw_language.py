"""Thin typed CIW adapter for product-neutral language and mobile build primitives."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error
from .language_primitives import (
    LanguagePrimitiveError,
    android_assemble,
    android_build,
    android_lint,
    android_targeted_test,
    android_unit_test,
    inspect_java_runtime,
    install_node_dependencies,
    resolve_java_executable,
    resolve_node_runtime,
    resolve_python_interpreter,
    run_gradle_tasks,
    run_node_package_script,
    run_python_script,
    run_python_tests,
    validate_java_runtime,
)

_DOMAIN = "language"
_OPERATIONS = (
    "python-tests",
    "python-script",
    "node-install",
    "node-script",
    "java-verify",
    "gradle-tasks",
    "android-build",
    "android-assemble",
    "android-unit-test",
    "android-lint",
    "android-targeted-test",
)


def configure_language(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation", choices=_OPERATIONS, required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--working-directory")
    parser.add_argument("--arguments-json")
    parser.add_argument("--script-path")
    parser.add_argument("--package-script")
    parser.add_argument("--install-mode", choices=("ci", "install"))
    parser.add_argument("--expected-java-major", type=int)
    parser.add_argument("--exact-java-version")
    parser.add_argument("--gradle-wrapper-path")
    parser.add_argument("--tasks-json")
    parser.add_argument("--task")
    parser.add_argument("--test-selector")
    parser.add_argument("--test-module")


def _value(args: argparse.Namespace, context: CIWContext, name: str, default: str = "") -> str:
    value = getattr(args, name, None)
    if value is not None:
        return str(value).strip()
    return input_value(context.environment, name, default)


def _text(value: object, code: str, *, allow_empty: bool = False, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or any(token in value for token in ("\x00", "\r", "\n"))
        or (not allow_empty and not value)
    ):
        raise CIWError(_DOMAIN, code)
    return value


def _strings(raw: str, code: str, *, maximum: int = 256) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CIWError(_DOMAIN, code) from error
    if not isinstance(value, list) or len(value) > maximum:
        raise CIWError(_DOMAIN, code)
    return tuple(_text(item, code) for item in value)


def _root(args: argparse.Namespace, context: CIWContext) -> Path:
    raw = _value(args, context, "project_root", "source")
    candidate = Path(_text(raw, "project_root_invalid"))
    if not candidate.is_absolute():
        candidate = Path(context.environment.get("GITHUB_WORKSPACE", str(context.root))) / candidate
    if candidate.is_symlink():
        raise CIWError(_DOMAIN, "project_root_invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, "project_root_invalid") from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, "project_root_invalid")
    return resolved


def _bounded(root: Path, raw: str, code: str, *, file: bool = False) -> Path:
    text = _text(raw, code)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts or "\\" in text:
        raise CIWError(_DOMAIN, code)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise CIWError(_DOMAIN, code)
    try:
        target = (root / relative).resolve(strict=True)
        target.relative_to(root)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if file and not target.is_file():
        raise CIWError(_DOMAIN, code)
    if not file and not target.is_dir():
        raise CIWError(_DOMAIN, code)
    return target


def _project_directory(args: argparse.Namespace, context: CIWContext) -> Path:
    return _bounded(
        _root(args, context),
        _value(args, context, "working_directory", "."),
        "working_directory_invalid",
    )


def _script(args: argparse.Namespace, context: CIWContext, project: Path) -> Path:
    raw = _value(args, context, "script_path")
    if not raw:
        raise CIWError(_DOMAIN, "script_path_required")
    return _bounded(project, raw, "script_path_invalid", file=True)


def _arguments(args: argparse.Namespace, context: CIWContext) -> tuple[str, ...]:
    return _strings(_value(args, context, "arguments_json", "[]"), "arguments_invalid")


def _result(operation: str, **payload: Any) -> CIWResult:
    return CIWResult(
        _DOMAIN,
        "run",
        outputs={
            "result": "success",
            "language_result_json": json.dumps(
                {"operation": operation, **payload},
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def _android_operation(name: str) -> Callable[..., Any]:
    operations = {
        "android-build": android_build,
        "android-assemble": android_assemble,
        "android-unit-test": android_unit_test,
        "android-lint": android_lint,
    }
    return operations[name]


def execute_language(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    project = _project_directory(args, context)
    environment = dict(context.environment)
    operation = args.operation
    try:
        if operation == "python-tests":
            python = resolve_python_interpreter(search_path=environment.get("PATH"))
            result = run_python_tests(
                python,
                project_directory=project,
                arguments=_arguments(args, context),
                test_module=_text(_value(args, context, "test_module", "pytest"), "test_module_invalid"),
                environment=environment,
            )
            return _result(result.operation)
        if operation == "python-script":
            python = resolve_python_interpreter(search_path=environment.get("PATH"))
            result = run_python_script(
                python,
                _script(args, context, project),
                project_directory=project,
                arguments=_arguments(args, context),
                environment=environment,
            )
            return _result(result.operation)
        if operation in {"node-install", "node-script"}:
            runtime = resolve_node_runtime(search_path=environment.get("PATH"))
            if operation == "node-install":
                result = install_node_dependencies(
                    runtime.package_manager,
                    project_directory=project,
                    mode=_value(args, context, "install_mode", "ci"),
                    options=_arguments(args, context),
                    environment=environment,
                )
            else:
                result = run_node_package_script(
                    runtime.package_manager,
                    _text(_value(args, context, "package_script"), "package_script_required"),
                    project_directory=project,
                    arguments=_arguments(args, context),
                    environment=environment,
                )
            return _result(result.operation)
        if operation == "java-verify":
            java = resolve_java_executable(search_path=environment.get("PATH"))
            runtime = inspect_java_runtime(java, working_directory=project, environment=environment)
            expected = args.expected_java_major
            if expected is None:
                raw = _value(args, context, "expected_java_major")
                if not raw.isdigit():
                    raise CIWError(_DOMAIN, "expected_java_major_invalid")
                expected = int(raw)
            runtime = validate_java_runtime(
                runtime,
                expected_major=expected,
                exact_version=_value(args, context, "exact_java_version") or None,
            )
            return _result("java.verify", version=runtime.version, major=runtime.major)
        if operation == "gradle-tasks":
            wrapper = _script_path(args, context, project)
            result = run_gradle_tasks(
                wrapper,
                _strings(_value(args, context, "tasks_json"), "tasks_invalid"),
                project_directory=project,
                options=_arguments(args, context),
                environment=environment,
            )
            return _result(result.operation)
        if operation in {"android-build", "android-assemble", "android-unit-test", "android-lint"}:
            wrapper = _script_path(args, context, project)
            result = _android_operation(operation)(
                wrapper,
                _text(_value(args, context, "task"), "task_required"),
                project_directory=project,
                options=_arguments(args, context),
                environment=environment,
            )
            return _result(result.operation)
        if operation == "android-targeted-test":
            wrapper = _script_path(args, context, project)
            result = android_targeted_test(
                wrapper,
                _text(_value(args, context, "task"), "task_required"),
                _text(_value(args, context, "test_selector"), "test_selector_required"),
                project_directory=project,
                options=_arguments(args, context),
                environment=environment,
            )
            return _result(result.operation)
        raise CIWError(_DOMAIN, "operation_invalid")
    except (CIWError, LanguagePrimitiveError) as error:
        raise project_error(error, domain=_DOMAIN) from error


def _script_path(args: argparse.Namespace, context: CIWContext, project: Path) -> Path:
    raw = _value(args, context, "gradle_wrapper_path", "gradlew")
    return _bounded(project, raw, "gradle_wrapper_path_invalid", file=True)
