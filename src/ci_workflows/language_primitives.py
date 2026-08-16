"""Product-neutral language and build primitives for shared CI functions."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence

from .runtime_primitives import ProcessResult, RuntimePrimitiveError, run_process

_SAFE_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SAFE_PACKAGE_SCRIPT = re.compile(r"^[A-Za-z0-9_.:@/-]+$")
_SAFE_GRADLE_TASK = re.compile(r"^:?[-A-Za-z0-9_.]+(?::[-A-Za-z0-9_.]+)*$")
_JAVA_VERSION = re.compile(r'(?:openjdk|java)\s+version\s+"([^"]+)"', re.IGNORECASE)


class LanguagePrimitiveError(RuntimeError):
    """Stable redacted language/build failure."""

    def __init__(
        self,
        code: str,
        operation: str,
        *,
        returncode: int | None = None,
    ) -> None:
        self.code = code
        self.operation = operation
        self.returncode = returncode
        message = f"{code}: {operation}"
        if returncode is not None:
            message += f" exited with status {returncode}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation: str
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class PythonVenv:
    root: Path
    interpreter: Path
    result: OperationResult


@dataclass(frozen=True, slots=True)
class NodeRuntime:
    node: Path
    package_manager: Path


@dataclass(frozen=True, slots=True)
class JavaRuntime:
    executable: Path
    version: str
    major: int
    result: OperationResult


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> ProcessResult: ...


class RuntimeCommandRunner:
    """Adapt the generic runtime process primitive to this module's test boundary."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> ProcessResult:
        return run_process(argv, cwd=cwd, environment=env)


def _fail(code: str, operation: str, *, returncode: int | None = None) -> None:
    raise LanguagePrimitiveError(code, operation, returncode=returncode)


def _argument(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        _fail("argument_invalid", field)
    return value


def _arguments(values: Sequence[str], field: str) -> tuple[str, ...]:
    return tuple(_argument(value, field) for value in values)


def _environment(value: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if value is None else value
    result: dict[str, str] = {}
    for name, item in source.items():
        if (
            not isinstance(name, str)
            or not isinstance(item, str)
            or not name
            or any(c in name for c in "\x00\r\n=")
            or "\x00" in item
        ):
            _fail("environment_invalid", "environment")
        result[name] = item
    return result


def _directory(path: Path, operation: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        _fail("path_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LanguagePrimitiveError("path_invalid", operation) from error
    if not resolved.is_dir():
        _fail("path_invalid", operation)
    return resolved


def _project_file(
    project_directory: Path,
    path: Path,
    operation: str,
    *,
    executable: bool = False,
) -> Path:
    root = _directory(project_directory, operation)
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    if candidate.is_symlink():
        _fail("path_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LanguagePrimitiveError("path_invalid", operation) from error
    if (resolved != root and root not in resolved.parents) or not resolved.is_file():
        _fail("path_invalid", operation)
    if executable and not os.access(resolved, os.X_OK):
        _fail("path_not_executable", operation)
    return resolved


def _tool(path: Path, operation: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as error:
        raise LanguagePrimitiveError("tool_unavailable", operation) from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        _fail("tool_unavailable", operation)
    return resolved


def _resolve(
    candidates: Sequence[str],
    *,
    operation: str,
    search_path: str | None,
) -> Path:
    if not candidates:
        _fail("tool_unavailable", operation)
    for candidate in candidates:
        found = shutil.which(_argument(candidate, operation), path=search_path)
        if found:
            return _tool(Path(found), operation)
    _fail("tool_unavailable", operation)


def _execute(
    operation: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None,
    runner: CommandRunner | None,
) -> OperationResult:
    try:
        outcome = (runner or RuntimeCommandRunner()).run(
            _arguments(argv, operation),
            cwd=_directory(cwd, operation),
            env=_environment(environment),
        )
    except RuntimePrimitiveError as error:
        code = "command_unavailable" if error.code == "process_start_failed" else "runtime_failed"
        raise LanguagePrimitiveError(code, operation) from error
    except OSError as error:
        raise LanguagePrimitiveError("command_unavailable", operation) from error
    if not isinstance(outcome, ProcessResult):
        _fail("runner_result_invalid", operation)
    if outcome.timed_out:
        _fail("command_timeout", operation)
    if outcome.returncode != 0:
        _fail("command_failed", operation, returncode=outcome.returncode)
    return OperationResult(operation, 0, outcome.stdout, outcome.stderr)


def resolve_python_interpreter(
    candidates: Sequence[str] = ("python3.12", "python3"),
    *,
    search_path: str | None = None,
) -> Path:
    return _resolve(candidates, operation="python.resolve", search_path=search_path)


def create_python_venv(
    interpreter: Path,
    venv_directory: Path,
    *,
    working_directory: Path,
    clear: bool = False,
    system_site_packages: bool = False,
    upgrade_deps: bool = False,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> PythonVenv:
    operation = "python.venv"
    python = _tool(interpreter, operation)
    working = _directory(working_directory, operation)
    requested = Path(venv_directory)
    root = requested if requested.is_absolute() else working / requested
    if root.is_symlink():
        _fail("path_invalid", operation)
    argv = [str(python), "-m", "venv"]
    argv += [
        flag
        for enabled, flag in (
            (clear, "--clear"),
            (system_site_packages, "--system-site-packages"),
            (upgrade_deps, "--upgrade-deps"),
        )
        if enabled
    ]
    argv.append(str(root))
    result = _execute(operation, argv, cwd=working, environment=environment, runner=runner)
    suffix = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return PythonVenv(root, root / suffix, result)


def install_python_dependencies(
    interpreter: Path,
    *,
    project_directory: Path,
    requirement_files: Sequence[Path] = (),
    install_project: bool = False,
    editable_project: bool = False,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    operation = "python.install"
    python = _tool(interpreter, operation)
    root = _directory(project_directory, operation)
    if install_project and editable_project:
        _fail("request_invalid", operation)
    if not requirement_files and not install_project and not editable_project:
        _fail("request_invalid", operation)
    argv = [
        str(python),
        "-m",
        "pip",
        "install",
        *_arguments(options, f"{operation}.options"),
    ]
    for requirement in requirement_files:
        argv += ["-r", str(_project_file(root, requirement, operation))]
    if editable_project:
        argv += ["-e", str(root)]
    elif install_project:
        argv.append(str(root))
    return _execute(operation, argv, cwd=root, environment=environment, runner=runner)


def run_python_module(
    interpreter: Path,
    module: str,
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
    operation: str = "python.module",
) -> OperationResult:
    python = _tool(interpreter, operation)
    if _SAFE_MODULE.fullmatch(module) is None:
        _fail("module_invalid", operation)
    argv = [str(python), "-m", module, *_arguments(arguments, f"{operation}.arguments")]
    return _execute(operation, argv, cwd=project_directory, environment=environment, runner=runner)


def run_python_script(
    interpreter: Path,
    script: Path,
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    operation = "python.script"
    python = _tool(interpreter, operation)
    root = _directory(project_directory, operation)
    script_path = _project_file(root, script, operation)
    argv = [
        str(python),
        str(script_path),
        *_arguments(arguments, f"{operation}.arguments"),
    ]
    return _execute(operation, argv, cwd=root, environment=environment, runner=runner)


def run_python_tests(
    interpreter: Path,
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    test_module: str = "pytest",
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return run_python_module(
        interpreter,
        test_module,
        project_directory=project_directory,
        arguments=arguments,
        environment=environment,
        runner=runner,
        operation="python.tests",
    )


def resolve_node_runtime(
    node_candidates: Sequence[str] = ("node",),
    package_manager_candidates: Sequence[str] = ("npm",),
    *,
    search_path: str | None = None,
) -> NodeRuntime:
    return NodeRuntime(
        _resolve(node_candidates, operation="node.resolve", search_path=search_path),
        _resolve(
            package_manager_candidates,
            operation="node.package_manager.resolve",
            search_path=search_path,
        ),
    )


def install_node_dependencies(
    package_manager: Path,
    *,
    project_directory: Path,
    mode: Literal["ci", "install"] = "ci",
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    operation = "node.install"
    manager = _tool(package_manager, operation)
    if mode not in ("ci", "install"):
        _fail("request_invalid", operation)
    argv = [str(manager), mode, *_arguments(options, f"{operation}.options")]
    return _execute(operation, argv, cwd=project_directory, environment=environment, runner=runner)


def run_node_package_script(
    package_manager: Path,
    script: str,
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    operation = "node.script"
    manager = _tool(package_manager, operation)
    if _SAFE_PACKAGE_SCRIPT.fullmatch(script) is None:
        _fail("package_script_invalid", operation)
    extra = _arguments(arguments, f"{operation}.arguments")
    argv = [str(manager), "run", script, *(("--", *extra) if extra else ())]
    return _execute(operation, argv, cwd=project_directory, environment=environment, runner=runner)


def resolve_java_executable(
    candidates: Sequence[str] = ("java",),
    *,
    search_path: str | None = None,
) -> Path:
    return _resolve(candidates, operation="java.resolve", search_path=search_path)


def _java_major(version: str) -> int:
    parts = version.split(".")
    head = parts[1] if parts[0] == "1" and len(parts) > 1 else parts[0]
    match = re.match(r"^(\d+)", head)
    if match is None:
        _fail("java_version_invalid", "java.inspect")
    return int(match.group(1))


def inspect_java_runtime(
    executable: Path,
    *,
    working_directory: Path,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> JavaRuntime:
    operation = "java.inspect"
    java = _tool(executable, operation)
    result = _execute(
        operation,
        [str(java), "-version"],
        cwd=working_directory,
        environment=environment,
        runner=runner,
    )
    output = "\n".join(part for part in (result.stderr, result.stdout) if part)
    match = _JAVA_VERSION.search(output)
    if match is None:
        _fail("java_version_invalid", operation)
    version = match.group(1)
    return JavaRuntime(java, version, _java_major(version), result)


def validate_java_runtime(
    runtime: JavaRuntime,
    *,
    expected_major: int,
    exact_version: str | None = None,
) -> JavaRuntime:
    operation = "java.validate"
    if isinstance(expected_major, bool) or expected_major <= 0:
        _fail("request_invalid", operation)
    if runtime.major != expected_major:
        _fail("java_version_mismatch", operation)
    if exact_version is not None and runtime.version != _argument(exact_version, operation):
        _fail("java_version_mismatch", operation)
    return runtime


def _gradle_task(value: str, operation: str) -> str:
    if _SAFE_GRADLE_TASK.fullmatch(value) is None or value.startswith("-"):
        _fail("gradle_task_invalid", operation)
    return value


def run_gradle_tasks(
    wrapper_path: Path,
    tasks: Sequence[str],
    *,
    project_directory: Path,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
    operation: str = "gradle.tasks",
) -> OperationResult:
    if not tasks:
        _fail("request_invalid", operation)
    root = _directory(project_directory, operation)
    wrapper = _project_file(root, wrapper_path, operation, executable=True)
    argv = [
        str(wrapper),
        *(_gradle_task(task, operation) for task in tasks),
        *_arguments(options, f"{operation}.options"),
    ]
    return _execute(operation, argv, cwd=root, environment=environment, runner=runner)


def _android(
    operation: str,
    wrapper_path: Path,
    task: str,
    *,
    project_directory: Path,
    options: Sequence[str],
    environment: Mapping[str, str] | None,
    runner: CommandRunner | None,
) -> OperationResult:
    return run_gradle_tasks(
        wrapper_path,
        (task,),
        project_directory=project_directory,
        options=options,
        environment=environment,
        runner=runner,
        operation=operation,
    )


def android_build(
    wrapper_path: Path,
    task: str,
    *,
    project_directory: Path,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return _android(
        "android.build",
        wrapper_path,
        task,
        project_directory=project_directory,
        options=options,
        environment=environment,
        runner=runner,
    )


def android_assemble(
    wrapper_path: Path,
    task: str,
    *,
    project_directory: Path,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return _android(
        "android.assemble",
        wrapper_path,
        task,
        project_directory=project_directory,
        options=options,
        environment=environment,
        runner=runner,
    )


def android_unit_test(
    wrapper_path: Path,
    task: str,
    *,
    project_directory: Path,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return _android(
        "android.unit_test",
        wrapper_path,
        task,
        project_directory=project_directory,
        options=options,
        environment=environment,
        runner=runner,
    )


def android_lint(
    wrapper_path: Path,
    task: str,
    *,
    project_directory: Path,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return _android(
        "android.lint",
        wrapper_path,
        task,
        project_directory=project_directory,
        options=options,
        environment=environment,
        runner=runner,
    )


def android_targeted_test(
    wrapper_path: Path,
    task: str,
    test_selector: str,
    *,
    project_directory: Path,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    selector = _argument(test_selector, "android.targeted_test.selector")
    return _android(
        "android.targeted_test",
        wrapper_path,
        task,
        project_directory=project_directory,
        options=(*options, "--tests", selector),
        environment=environment,
        runner=runner,
    )


def _pub_restore(
    operation: str,
    executable: Path,
    *,
    project_directory: Path,
    environment: Mapping[str, str] | None,
    runner: CommandRunner | None,
) -> OperationResult:
    tool = _tool(executable, operation)
    return _execute(
        operation,
        [str(tool), "pub", "get"],
        cwd=project_directory,
        environment=environment,
        runner=runner,
    )


def flutter_restore(
    executable: Path,
    *,
    project_directory: Path,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return _pub_restore(
        "flutter.restore",
        executable,
        project_directory=project_directory,
        environment=environment,
        runner=runner,
    )


def dart_restore(
    executable: Path,
    *,
    project_directory: Path,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    return _pub_restore(
        "dart.restore",
        executable,
        project_directory=project_directory,
        environment=environment,
        runner=runner,
    )


def run_flutter_operation(
    executable: Path,
    operation: Literal["build", "test", "analyze"],
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    if operation not in ("build", "test", "analyze"):
        _fail("request_invalid", "flutter.operation")
    name = f"flutter.{operation}"
    tool = _tool(executable, name)
    argv = [str(tool), operation, *_arguments(arguments, f"{name}.arguments")]
    return _execute(name, argv, cwd=project_directory, environment=environment, runner=runner)


def run_dart_operation(
    executable: Path,
    operation: Literal["test", "analyze"],
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    if operation not in ("test", "analyze"):
        _fail("request_invalid", "dart.operation")
    name = f"dart.{operation}"
    tool = _tool(executable, name)
    argv = [str(tool), operation, *_arguments(arguments, f"{name}.arguments")]
    return _execute(name, argv, cwd=project_directory, environment=environment, runner=runner)
