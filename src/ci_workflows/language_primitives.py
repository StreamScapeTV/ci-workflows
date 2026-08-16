"""Product-neutral language and build execution primitives.

The functions in this module intentionally stop at the process/tool boundary. Product
repositories choose commands, tasks, paths, versions, and environment. Secret material
must be supplied through the environment mapping rather than dedicated string
parameters, and neither command arguments nor environment values are projected into
errors.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence


_SAFE_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SAFE_PACKAGE_SCRIPT = re.compile(r"^[A-Za-z0-9_.:@/-]+$")
_SAFE_GRADLE_TASK = re.compile(r"^:?[-A-Za-z0-9_.]+(?::[-A-Za-z0-9_.]+)*$")
_JAVA_VERSION = re.compile(r'(?:openjdk|java)\s+version\s+"([^"]+)"', re.IGNORECASE)


class LanguagePrimitiveError(RuntimeError):
    """Stable, redacted failure raised by a language/build primitive."""

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
        detail = f"{code}: {operation}"
        if returncode is not None:
            detail += f" exited with status {returncode}"
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""


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
    ) -> CommandOutcome: ...


class SubprocessCommandRunner:
    """Default process boundary used by production callers."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return CommandOutcome(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _runner(runner: CommandRunner | None) -> CommandRunner:
    return runner if runner is not None else SubprocessCommandRunner()


def _environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    result: dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise LanguagePrimitiveError("environment_invalid", "environment")
        if not key or any(character in key for character in "\x00\r\n="):
            raise LanguagePrimitiveError("environment_invalid", "environment")
        if "\x00" in value:
            raise LanguagePrimitiveError("environment_invalid", "environment")
        result[key] = value
    return result


def _safe_argument(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in "\x00\r\n")
    ):
        raise LanguagePrimitiveError("argument_invalid", field)
    return value


def _safe_arguments(values: Sequence[str], field: str) -> tuple[str, ...]:
    return tuple(_safe_argument(value, field) for value in values)


def _real_directory(path: Path, operation: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        raise LanguagePrimitiveError("path_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LanguagePrimitiveError("path_invalid", operation) from error
    if not resolved.is_dir():
        raise LanguagePrimitiveError("path_invalid", operation)
    return resolved


def _project_file(
    project_directory: Path,
    path: Path,
    operation: str,
    *,
    executable: bool = False,
) -> Path:
    root = _real_directory(project_directory, operation)
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    if candidate.is_symlink():
        raise LanguagePrimitiveError("path_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LanguagePrimitiveError("path_invalid", operation) from error
    if resolved != root and root not in resolved.parents:
        raise LanguagePrimitiveError("path_invalid", operation)
    if not resolved.is_file():
        raise LanguagePrimitiveError("path_invalid", operation)
    if executable and not os.access(resolved, os.X_OK):
        raise LanguagePrimitiveError("path_not_executable", operation)
    return resolved


def _tool_path(path: Path, operation: str) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LanguagePrimitiveError("tool_unavailable", operation) from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise LanguagePrimitiveError("tool_unavailable", operation)
    return resolved


def _resolve_executable(
    candidates: Sequence[str],
    *,
    operation: str,
    search_path: str | None = None,
) -> Path:
    if not candidates:
        raise LanguagePrimitiveError("tool_unavailable", operation)
    for candidate in candidates:
        name = _safe_argument(candidate, operation)
        resolved = shutil.which(name, path=search_path)
        if resolved:
            return _tool_path(Path(resolved), operation)
    raise LanguagePrimitiveError("tool_unavailable", operation)


def _execute(
    operation: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None,
    runner: CommandRunner | None,
) -> OperationResult:
    directory = _real_directory(cwd, operation)
    arguments = _safe_arguments(tuple(argv), operation)
    try:
        outcome = _runner(runner).run(
            arguments,
            cwd=directory,
            env=_environment(environment),
        )
    except OSError as error:
        raise LanguagePrimitiveError("command_unavailable", operation) from error
    if not isinstance(outcome, CommandOutcome):
        raise LanguagePrimitiveError("runner_result_invalid", operation)
    if outcome.returncode != 0:
        raise LanguagePrimitiveError(
            "command_failed",
            operation,
            returncode=outcome.returncode,
        )
    return OperationResult(
        operation=operation,
        returncode=outcome.returncode,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )


def resolve_python_interpreter(
    candidates: Sequence[str] = ("python3.12", "python3"),
    *,
    search_path: str | None = None,
) -> Path:
    """Resolve the first executable Python candidate without installing anything."""

    return _resolve_executable(
        candidates,
        operation="python.resolve",
        search_path=search_path,
    )


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
    """Create a virtual environment using an already resolved interpreter."""

    python = _tool_path(interpreter, "python.venv")
    working = _real_directory(working_directory, "python.venv")
    requested_root = Path(venv_directory)
    root = requested_root if requested_root.is_absolute() else working / requested_root
    if root.is_symlink():
        raise LanguagePrimitiveError("path_invalid", "python.venv")
    argv = [str(python), "-m", "venv"]
    if clear:
        argv.append("--clear")
    if system_site_packages:
        argv.append("--system-site-packages")
    if upgrade_deps:
        argv.append("--upgrade-deps")
    argv.append(str(root))
    result = _execute(
        "python.venv",
        argv,
        cwd=working,
        environment=environment,
        runner=runner,
    )
    interpreter_path = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return PythonVenv(root=root, interpreter=interpreter_path, result=result)


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
    """Install dependencies from checked-in requirement files and/or the local project."""

    python = _tool_path(interpreter, "python.install")
    root = _real_directory(project_directory, "python.install")
    if install_project and editable_project:
        raise LanguagePrimitiveError("request_invalid", "python.install")
    if not requirement_files and not install_project and not editable_project:
        raise LanguagePrimitiveError("request_invalid", "python.install")

    argv = [str(python), "-m", "pip", "install"]
    argv.extend(_safe_arguments(options, "python.install.options"))
    for requirement in requirement_files:
        argv.extend(
            (
                "-r",
                str(_project_file(root, Path(requirement), "python.install")),
            )
        )
    if editable_project:
        argv.extend(("-e", str(root)))
    elif install_project:
        argv.append(str(root))
    return _execute(
        "python.install",
        argv,
        cwd=root,
        environment=environment,
        runner=runner,
    )


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
    python = _tool_path(interpreter, operation)
    if _SAFE_MODULE.fullmatch(module) is None:
        raise LanguagePrimitiveError("module_invalid", operation)
    argv = [
        str(python),
        "-m",
        module,
        *_safe_arguments(arguments, f"{operation}.arguments"),
    ]
    return _execute(
        operation,
        argv,
        cwd=project_directory,
        environment=environment,
        runner=runner,
    )


def run_python_script(
    interpreter: Path,
    script: Path,
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    python = _tool_path(interpreter, "python.script")
    root = _real_directory(project_directory, "python.script")
    script_path = _project_file(root, script, "python.script")
    return _execute(
        "python.script",
        [
            str(python),
            str(script_path),
            *_safe_arguments(arguments, "python.script.arguments"),
        ],
        cwd=root,
        environment=environment,
        runner=runner,
    )


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
    """Resolve Node and its caller-selected package-manager executable."""

    return NodeRuntime(
        node=_resolve_executable(
            node_candidates,
            operation="node.resolve",
            search_path=search_path,
        ),
        package_manager=_resolve_executable(
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
    manager = _tool_path(package_manager, "node.install")
    if mode not in ("ci", "install"):
        raise LanguagePrimitiveError("request_invalid", "node.install")
    return _execute(
        "node.install",
        [str(manager), mode, *_safe_arguments(options, "node.install.options")],
        cwd=project_directory,
        environment=environment,
        runner=runner,
    )


def run_node_package_script(
    package_manager: Path,
    script: str,
    *,
    project_directory: Path,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> OperationResult:
    manager = _tool_path(package_manager, "node.script")
    if _SAFE_PACKAGE_SCRIPT.fullmatch(script) is None:
        raise LanguagePrimitiveError("package_script_invalid", "node.script")
    argv = [str(manager), "run", script]
    extra = _safe_arguments(arguments, "node.script.arguments")
    if extra:
        argv.extend(("--", *extra))
    return _execute(
        "node.script",
        argv,
        cwd=project_directory,
        environment=environment,
        runner=runner,
    )


def resolve_java_executable(
    candidates: Sequence[str] = ("java",),
    *,
    search_path: str | None = None,
) -> Path:
    return _resolve_executable(
        candidates,
        operation="java.resolve",
        search_path=search_path,
    )


def _java_major(version: str) -> int:
    head = version.split(".", 1)[0]
    if head == "1":
        pieces = version.split(".")
        if len(pieces) < 2 or not pieces[1].isdigit():
            raise LanguagePrimitiveError("java_version_invalid", "java.inspect")
        return int(pieces[1])
    match = re.match(r"^(\d+)", head)
    if match is None:
        raise LanguagePrimitiveError("java_version_invalid", "java.inspect")
    return int(match.group(1))


def inspect_java_runtime(
    executable: Path,
    *,
    working_directory: Path,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> JavaRuntime:
    java = _tool_path(executable, "java.inspect")
    result = _execute(
        "java.inspect",
        [str(java), "-version"],
        cwd=working_directory,
        environment=environment,
        runner=runner,
    )
    output = "\n".join(part for part in (result.stderr, result.stdout) if part)
    match = _JAVA_VERSION.search(output)
    if match is None:
        raise LanguagePrimitiveError("java_version_invalid", "java.inspect")
    version = match.group(1)
    return JavaRuntime(
        executable=java,
        version=version,
        major=_java_major(version),
        result=result,
    )


def validate_java_runtime(
    runtime: JavaRuntime,
    *,
    expected_major: int,
    exact_version: str | None = None,
) -> JavaRuntime:
    if isinstance(expected_major, bool) or expected_major <= 0:
        raise LanguagePrimitiveError("request_invalid", "java.validate")
    if runtime.major != expected_major:
        raise LanguagePrimitiveError("java_version_mismatch", "java.validate")
    if exact_version is not None:
        _safe_argument(exact_version, "java.validate")
        if runtime.version != exact_version:
            raise LanguagePrimitiveError("java_version_mismatch", "java.validate")
    return runtime


def _gradle_task(task: str, operation: str) -> str:
    if _SAFE_GRADLE_TASK.fullmatch(task) is None or task.startswith("-"):
        raise LanguagePrimitiveError("gradle_task_invalid", operation)
    return task


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
        raise LanguagePrimitiveError("request_invalid", operation)
    root = _real_directory(project_directory, operation)
    wrapper = _project_file(root, wrapper_path, operation, executable=True)
    selected_tasks = tuple(_gradle_task(task, operation) for task in tasks)
    return _execute(
        operation,
        [
            str(wrapper),
            *selected_tasks,
            *_safe_arguments(options, f"{operation}.options"),
        ],
        cwd=root,
        environment=environment,
        runner=runner,
    )


def _run_android_task(
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
    return _run_android_task(
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
    return _run_android_task(
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
    return _run_android_task(
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
    return _run_android_task(
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
    selector = _safe_argument(test_selector, "android.targeted_test.selector")
    return _run_android_task(
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
    tool = _tool_path(executable, operation)
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
        raise LanguagePrimitiveError("request_invalid", "flutter.operation")
    tool = _tool_path(executable, f"flutter.{operation}")
    return _execute(
        f"flutter.{operation}",
        [
            str(tool),
            operation,
            *_safe_arguments(arguments, f"flutter.{operation}.arguments"),
        ],
        cwd=project_directory,
        environment=environment,
        runner=runner,
    )


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
        raise LanguagePrimitiveError("request_invalid", "dart.operation")
    tool = _tool_path(executable, f"dart.{operation}")
    return _execute(
        f"dart.{operation}",
        [
            str(tool),
            operation,
            *_safe_arguments(arguments, f"dart.{operation}.arguments"),
        ],
        cwd=project_directory,
        environment=environment,
        runner=runner,
    )
