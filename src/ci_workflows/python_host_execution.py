"""General-runner host execution for bounded Python validation plans."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence

from .foundation_types import stable_identifier
from .language_primitives import (
    CommandRunner,
    LanguagePrimitiveError,
    PythonVenv,
    create_python_venv,
    install_python_dependencies,
    resolve_python_interpreter,
    run_python_module,
    run_python_script,
    run_python_tests,
)
from .python_contract import bounded_path, require
from .python_execution import (
    _failure_stage_for_command,
    _report_failure,
    command_environment,
    copy_source,
)
from .python_types import (
    PythonCommand,
    PythonValidationError,
    PythonValidationPlan,
    PythonValidationResult,
)
from .runtime_primitives import ProcessResult, RuntimePrimitiveError, run_process

_EXACT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_VERSION_FAMILY = re.compile(r"^[0-9]+\.[0-9]+$")


class _BoundedCommandRunner:
    """Apply the plan timeout to every merged language primitive call."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> ProcessResult:
        return run_process(
            argv,
            cwd=cwd,
            environment=env,
            timeout_seconds=self.timeout_seconds,
        )


def _raise_failure(code: str, stage: str, error: BaseException) -> None:
    _report_failure(stage, code)
    raise PythonValidationError(code) from error


def _inspect_python(
    executable: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: CommandRunner,
) -> str:
    try:
        outcome = runner.run(
            (
                str(executable),
                "-c",
                (
                    "import platform,sys;"
                    "print(f'{sys.implementation.name}|"
                    "{sys.version_info.major}.{sys.version_info.minor}."
                    "{sys.version_info.micro}|{platform.system()}|{platform.machine()}')"
                ),
            ),
            cwd=cwd,
            env=environment,
        )
    except (LanguagePrimitiveError, RuntimePrimitiveError, OSError) as error:
        _raise_failure("toolchain_mismatch", "runtime-container-setup", error)
    if outcome.timed_out or outcome.returncode != 0:
        _raise_failure(
            "toolchain_mismatch",
            "runtime-container-setup",
            PythonValidationError("toolchain_mismatch"),
        )
    fields = outcome.stdout.strip().split("|")
    if (
        len(fields) != 4
        or fields[0] != "cpython"
        or _EXACT_VERSION.fullmatch(fields[1]) is None
        or fields[2] != "Linux"
        or fields[3].casefold() not in {"x86_64", "amd64"}
    ):
        _raise_failure(
            "toolchain_mismatch",
            "runtime-container-setup",
            PythonValidationError("toolchain_mismatch"),
        )
    return fields[1]


def _matches_runtime(actual: str, expected: str) -> bool:
    if _EXACT_VERSION.fullmatch(expected):
        return actual == expected
    if _VERSION_FAMILY.fullmatch(expected):
        return actual.startswith(expected + ".")
    return False


def _create_venv(
    interpreter: Path,
    root: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: CommandRunner,
) -> PythonVenv:
    try:
        return create_python_venv(
            interpreter,
            root,
            working_directory=cwd,
            environment=environment,
            runner=runner,
        )
    except (LanguagePrimitiveError, RuntimePrimitiveError, OSError) as error:
        _raise_failure("dependency_restore_failed", "dependency-restoration", error)


def _install_dependencies(
    interpreter: Path,
    source_root: Path,
    dependency_file: str,
    *,
    environment: Mapping[str, str],
    runner: CommandRunner,
) -> None:
    try:
        install_python_dependencies(
            interpreter,
            project_directory=source_root,
            requirement_files=(Path(dependency_file),),
            options=("--no-input", "--no-cache-dir"),
            environment=environment,
            runner=runner,
        )
    except (LanguagePrimitiveError, RuntimePrimitiveError, OSError) as error:
        _raise_failure("dependency_restore_failed", "dependency-restoration", error)


def _execute_python_command(
    command: PythonCommand,
    interpreter: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: CommandRunner,
) -> None:
    argv = command.argv
    stage = _failure_stage_for_command(command.stage)
    try:
        require(len(argv) >= 2, "command_failed")
        if argv[1] == "-m":
            require(len(argv) >= 3, "command_failed")
            module = argv[2]
            arguments = argv[3:]
            if module in {"pytest", "unittest"}:
                run_python_tests(
                    interpreter,
                    project_directory=cwd,
                    test_module=module,
                    arguments=arguments,
                    environment=environment,
                    runner=runner,
                )
            else:
                run_python_module(
                    interpreter,
                    module,
                    project_directory=cwd,
                    arguments=arguments,
                    environment=environment,
                    runner=runner,
                )
        else:
            run_python_script(
                interpreter,
                Path(argv[1]),
                project_directory=cwd,
                arguments=argv[2:],
                environment=environment,
                runner=runner,
            )
    except (LanguagePrimitiveError, RuntimePrimitiveError, OSError) as error:
        _raise_failure("command_failed", stage, error)


def _execute_product_command(
    command: PythonCommand,
    interpreter: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: CommandRunner,
) -> None:
    if command.argv and command.argv[0] in {"python", "python3"}:
        _execute_python_command(
            command,
            interpreter,
            cwd=cwd,
            environment=environment,
            runner=runner,
        )
        return
    stage = _failure_stage_for_command(command.stage)
    try:
        outcome = runner.run(command.argv, cwd=cwd, env=environment)
    except (LanguagePrimitiveError, RuntimePrimitiveError, OSError) as error:
        _raise_failure("command_failed", stage, error)
    if outcome.timed_out or outcome.returncode != 0:
        _raise_failure(
            "command_failed",
            stage,
            PythonValidationError("command_failed"),
        )


def execute_host_plan(
    source_root: Path,
    state_root: Path,
    plan: PythonValidationPlan,
    environment: Mapping[str, str],
) -> tuple[int, str]:
    """Execute a copied-host plan using the merged Python/runtime primitives."""

    work = state_root / "python-validation" / "work"
    copy_source(source_root, work)
    cwd = bounded_path(work, plan.working_directory)
    isolated = state_root / "python-validation" / "host"
    paths = {
        "HOME": isolated / "home",
        "TMPDIR": isolated / "tmp",
        "XDG_CACHE_HOME": isolated / "cache",
        "XDG_CONFIG_HOME": isolated / "config",
        "XDG_DATA_HOME": isolated / "data",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    execution_environment = command_environment(
        plan,
        {**environment, **{name: str(path) for name, path in paths.items()}},
    )
    runner = _BoundedCommandRunner(plan.timeout_minutes * 60)
    try:
        interpreter = resolve_python_interpreter(
            ("python3.12", "python3"),
            search_path=execution_environment.get("PATH"),
        )
    except (LanguagePrimitiveError, RuntimePrimitiveError, OSError) as error:
        _raise_failure("toolchain_mismatch", "runtime-container-setup", error)
    actual_version = _inspect_python(
        interpreter,
        cwd=cwd,
        environment=execution_environment,
        runner=runner,
    )
    require(
        _matches_runtime(actual_version, plan.python_version),
        "python_version_drift",
    )

    executable = interpreter
    if plan.dependency_file is not None:
        venv = _create_venv(
            interpreter,
            isolated / "venv",
            cwd=cwd,
            environment=execution_environment,
            runner=runner,
        )
        executable = venv.interpreter
        execution_environment["VIRTUAL_ENV"] = str(venv.root)
        execution_environment["PATH"] = (
            f"{venv.root / 'bin'}:{execution_environment.get('PATH', '')}"
        )
        _install_dependencies(
            executable,
            work,
            plan.dependency_file,
            environment=execution_environment,
            runner=runner,
        )

    for command in plan.commands:
        _execute_product_command(
            command,
            executable,
            cwd=cwd,
            environment=execution_environment,
            runner=runner,
        )
    return len(plan.commands), actual_version


def result_from_host_plan(
    plan: PythonValidationPlan,
    stage_count: int,
    resolved_python_version: str,
) -> PythonValidationResult:
    material = {
        "source_sha": plan.admitted_sha,
        "validation_profile": plan.validation_profile,
        "command_profile": plan.command_profile,
        "python_version": resolved_python_version,
        "stage_count": stage_count,
        "cleanup": "registered",
    }
    return PythonValidationResult(
        source_sha=plan.admitted_sha,
        resolved_python_version=resolved_python_version,
        validation_profile=plan.validation_profile,
        command_profile=plan.command_profile,
        stage_count=stage_count,
        cleanup_result="registered",
        evidence_id=stable_identifier("python", material, length=28),
    )
