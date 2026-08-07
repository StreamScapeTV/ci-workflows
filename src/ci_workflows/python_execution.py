"Isolated host and Podman execution for reviewed Python validation plans."

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .foundation_types import stable_identifier
from .python_contract import bounded_path, require
from .python_types import (
    PythonValidationError,
    PythonValidationPlan,
    PythonValidationResult,
)


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    code: str = "command_failed",
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        if allow_failure:
            return subprocess.CompletedProcess(list(argv), 124, "", "")
        raise PythonValidationError(code) from error
    if completed.returncode != 0 and not allow_failure:
        raise PythonValidationError(code)
    return completed


def git_output(root: Path, arguments: Sequence[str], code: str) -> str:
    return run_command(
        ["git", *arguments],
        cwd=root,
        environment={**os.environ, "LC_ALL": "C", "LANG": "C"},
        timeout_seconds=60,
        code=code,
    ).stdout.strip()


def verify_exact_source(source_root: Path, admitted_sha: str) -> None:
    "Verify exact HEAD equality and a completely clean source tree."

    require(source_root.is_dir() and (source_root / ".git").exists(), "source_mismatch")
    require(
        git_output(source_root, ["rev-parse", "HEAD"], "source_mismatch")
        == admitted_sha,
        "source_mismatch",
    )
    require(
        not git_output(
            source_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            "dirty_tree",
        ),
        "dirty_tree",
    )


def copy_source(source_root: Path, destination: Path) -> None:
    "Copy exact source into disposable state while rejecting symlinks."

    require(not destination.exists(), "isolation_unavailable")
    for current, directories, files in os.walk(source_root):
        current_path = Path(current)
        relative = current_path.relative_to(source_root)
        for name in [*directories, *files]:
            require(not (current_path / name).is_symlink(), "isolation_unavailable")
        target = destination / relative
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(
                current_path / name,
                target / name,
                follow_symlinks=False,
            )


def host_python_version(
    executable: str,
    cwd: Path,
    environment: Mapping[str, str],
) -> str:
    completed = run_command(
        [
            executable,
            "-c",
            (
                "import platform,sys;"
                "print(f'{sys.implementation.name}|"
                "{sys.version_info.major}.{sys.version_info.minor}."
                "{sys.version_info.micro}|{platform.system()}|{platform.machine()}')"
            ),
        ],
        cwd=cwd,
        environment=environment,
        timeout_seconds=30,
        code="toolchain_mismatch",
    )
    fields = completed.stdout.strip().split("|")
    require(len(fields) == 4 and fields[0] == "cpython", "toolchain_mismatch")
    return fields[1]


def command_environment(
    plan: PythonValidationPlan,
    base: Mapping[str, str],
) -> dict[str, str]:
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "HOME",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "VIRTUAL_ENV",
    }
    result = {name: value for name, value in base.items() if name in allowed}
    result.update(
        {
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_CACHE_DIR": "1",
        }
    )
    result.update(plan.environment)
    return result


def execute_host_plan(
    source_root: Path,
    state_root: Path,
    plan: PythonValidationPlan,
    environment: Mapping[str, str],
) -> int:
    "Execute audit/host commands in copied source and isolated host state."

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
    require(
        host_python_version(sys.executable, cwd, execution_environment)
        == plan.python_version,
        "python_version_drift",
    )
    executable = sys.executable
    if plan.dependency_file is not None:
        venv = isolated / "venv"
        run_command(
            [sys.executable, "-m", "venv", str(venv)],
            cwd=cwd,
            environment=execution_environment,
            timeout_seconds=120,
            code="dependency_restore_failed",
        )
        executable = str(venv / "bin" / "python")
        execution_environment["VIRTUAL_ENV"] = str(venv)
        execution_environment["PATH"] = (
            f"{venv / 'bin'}:{execution_environment.get('PATH', '')}"
        )
        run_command(
            [
                executable,
                "-m",
                "pip",
                "install",
                "--no-input",
                "--no-cache-dir",
                "-r",
                str(bounded_path(work, plan.dependency_file)),
            ],
            cwd=cwd,
            environment=execution_environment,
            timeout_seconds=max(60, plan.timeout_minutes * 30),
            code="dependency_restore_failed",
        )
    for command in plan.commands:
        argv = tuple(
            executable if item in {"python", "python3"} else item
            for item in command.argv
        )
        run_command(
            argv,
            cwd=cwd,
            environment=execution_environment,
            timeout_seconds=plan.timeout_minutes * 60,
        )
    return len(plan.commands)


def podman_command(state_root: Path) -> list[str]:
    return [
        "podman",
        "--storage-driver",
        "vfs",
        "--root",
        str(state_root / "python-validation" / "podman-storage"),
        "--runroot",
        str(state_root / "python-validation" / "podman-runroot"),
    ]


def container_script(plan: PythonValidationPlan) -> str:
    "Render only contract-owned argv into the immutable execution container."

    target = (
        "/work/source"
        if plan.working_directory == "."
        else f"/work/source/{plan.working_directory}"
    )
    lines = [
        "set -eu",
        "cp -a /src/. /work/source",
        f"cd {shlex.quote(target)}",
        "python -m venv /work/venv",
    ]
    if plan.dependency_file is not None:
        lines.append(
            "/work/venv/bin/python -m pip install --no-input --no-cache-dir "
            f"-r {shlex.quote('/work/source/' + plan.dependency_file)}"
        )
    lines.append("export PATH=/work/venv/bin:$PATH")
    lines.extend(shlex.join(command.argv) for command in plan.commands)
    return "\n".join(lines)


def write_environment_file(path: Path, values: Mapping[str, str]) -> None:
    "Write one mode-0600 Podman env file without multiline values."

    require(
        all(
            name
            and name.replace("_", "").isalnum()
            and name.upper() == name
            and isinstance(value, str)
            and "\n" not in value
            and "\r" not in value
            for name, value in values.items()
        ),
        "invalid_input",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def cleanup_podman(
    command: Sequence[str],
    *,
    containers: Sequence[str],
    network: str | None,
    volume: str | None,
    images: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    "Remove and verify all process, container, network, volume, and image state."

    failed = False
    for container in containers:
        completed = run_command(
            [*command, "rm", "-f", container],
            cwd=cwd,
            environment=environment,
            timeout_seconds=30,
            allow_failure=True,
        )
        failed = failed or completed.returncode not in {0, 1, 125}
    if network:
        run_command(
            [*command, "network", "rm", network],
            cwd=cwd,
            environment=environment,
            timeout_seconds=30,
            allow_failure=True,
        )
    if volume:
        run_command(
            [*command, "volume", "rm", "-f", volume],
            cwd=cwd,
            environment=environment,
            timeout_seconds=30,
            allow_failure=True,
        )
    for image in images:
        run_command(
            [*command, "image", "rm", "-f", image],
            cwd=cwd,
            environment=environment,
            timeout_seconds=60,
            allow_failure=True,
        )
    for container in containers:
        failed = failed or run_command(
            [*command, "container", "exists", container],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    if network:
        failed = failed or run_command(
            [*command, "network", "exists", network],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    if volume:
        failed = failed or run_command(
            [*command, "volume", "exists", volume],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    require(not failed, "cleanup_failed")


def _resource_token(
    plan: PythonValidationPlan,
    environment: Mapping[str, str],
) -> str:
    return stable_identifier(
        "py",
        {
            "run_id": environment.get("GITHUB_RUN_ID", "local"),
            "attempt": environment.get("GITHUB_RUN_ATTEMPT", "1"),
            "repository": plan.repository,
            "profile": plan.validation_profile,
        },
        length=16,
    )


def _start_postgres(
    podman: Sequence[str],
    *,
    source_root: Path,
    state_root: Path,
    plan: PythonValidationPlan,
    contract: Mapping[str, Any],
    environment: Mapping[str, str],
    container: str,
    network: str,
    volume: str,
) -> str:
    postgres = contract["postgres"]
    password = secrets.token_urlsafe(24)
    env_file = state_root / "python-validation" / "postgres.env"
    write_environment_file(
        env_file,
        {
            "POSTGRES_USER": str(postgres["username"]),
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": str(postgres["database"]),
        },
    )
    run_command(
        [*podman, "network", "create", network],
        cwd=source_root,
        environment=environment,
        timeout_seconds=30,
        code="isolation_unavailable",
    )
    run_command(
        [*podman, "volume", "create", volume],
        cwd=source_root,
        environment=environment,
        timeout_seconds=30,
        code="isolation_unavailable",
    )
    run_command(
        [
            *podman,
            "run",
            "-d",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            str(postgres["network_alias"]),
            "--env-file",
            str(env_file),
            "-v",
            f"{volume}:/var/lib/postgresql/data",
            str(plan.postgres_runtime_reference),
        ],
        cwd=source_root,
        environment=environment,
        timeout_seconds=60,
        code="isolation_unavailable",
    )
    ready = False
    for _ in range(plan.readiness_attempts):
        completed = run_command(
            [
                *podman,
                "exec",
                container,
                "pg_isready",
                "-U",
                str(postgres["username"]),
                "-d",
                str(postgres["database"]),
            ],
            cwd=source_root,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        )
        if completed.returncode == 0:
            ready = True
            break
        time.sleep(plan.readiness_interval_seconds)
    require(ready, "postgres_readiness_timeout")
    return (
        f"postgresql://{postgres['username']}:{password}@"
        f"{postgres['network_alias']}:5432/{postgres['database']}"
    )


def execute_podman_plan(
    source_root: Path,
    state_root: Path,
    plan: PythonValidationPlan,
    contract: Mapping[str, Any],
    environment: Mapping[str, str],
) -> int:
    "Execute a reviewed Podman plan with optional ephemeral PostgreSQL."

    require(plan.runtime_reference is not None, "toolchain_mismatch")
    require(shutil.which("podman") is not None, "isolation_unavailable")
    require(
        shutil.which("docker") is None and shutil.which("dockerd") is None,
        "isolation_unavailable",
    )
    require(
        not Path("/var/run/docker.sock").exists()
        and not Path("/run/docker.sock").exists(),
        "isolation_unavailable",
    )
    podman_root = state_root / "python-validation"
    for path in (
        podman_root / "podman-storage",
        podman_root / "podman-runroot",
        podman_root / "work",
    ):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    podman = podman_command(state_root)
    execution_environment = {**environment, "LC_ALL": "C", "LANG": "C"}
    run_command(
        [*podman, "info"],
        cwd=source_root,
        environment=execution_environment,
        timeout_seconds=60,
        code="isolation_unavailable",
    )
    images = [plan.runtime_reference]
    for image in (
        plan.runtime_reference,
        plan.postgres_runtime_reference,
    ):
        if image:
            run_command(
                [*podman, "pull", "--platform", "linux/amd64", image],
                cwd=source_root,
                environment=execution_environment,
                timeout_seconds=180,
                code="toolchain_mismatch",
            )
            if image not in images:
                images.append(image)

    token = _resource_token(plan, environment)
    validation_container = f"{token}-validation"
    postgres_container = f"{token}-postgres"
    network = f"{token}-network" if plan.postgres_runtime_reference else None
    volume = f"{token}-pgdata" if plan.postgres_runtime_reference else None
    original_error: BaseException | None = None
    try:
        validation_environment = {
            **plan.environment,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_CACHE_DIR": "1",
        }
        if network and volume:
            database_url = _start_postgres(
                podman,
                source_root=source_root,
                state_root=state_root,
                plan=plan,
                contract=contract,
                environment=execution_environment,
                container=postgres_container,
                network=network,
                volume=volume,
            )
            require(plan.database_environment_variable is not None, "invalid_input")
            validation_environment[plan.database_environment_variable] = database_url

        env_file = podman_root / "validation.env"
        write_environment_file(env_file, validation_environment)
        arguments = [
            *podman,
            "run",
            "--rm",
            "--name",
            validation_container,
        ]
        if network:
            arguments.extend(["--network", network])
        arguments.extend(
            [
                "--env-file",
                str(env_file),
                "-v",
                f"{source_root}:/src:ro",
                "-v",
                f"{podman_root / 'work'}:/work",
                plan.runtime_reference,
                "sh",
                "-ceu",
                container_script(plan),
            ]
        )
        run_command(
            arguments,
            cwd=source_root,
            environment=execution_environment,
            timeout_seconds=plan.timeout_minutes * 60,
        )
    except BaseException as error:
        original_error = error
    try:
        cleanup_podman(
            podman,
            containers=[validation_container, postgres_container],
            network=network,
            volume=volume,
            images=images,
            cwd=source_root,
            environment=execution_environment,
        )
    except BaseException as cleanup_error:
        raise PythonValidationError("cleanup_failed") from cleanup_error
    if original_error is not None:
        raise original_error
    return len(plan.commands)


def result_from_plan(
    plan: PythonValidationPlan,
    stage_count: int,
) -> PythonValidationResult:
    material = {
        "source_sha": plan.admitted_sha,
        "validation_profile": plan.validation_profile,
        "command_profile": plan.command_profile,
        "python_version": plan.python_version,
        "stage_count": stage_count,
        "cleanup": "registered",
    }
    return PythonValidationResult(
        source_sha=plan.admitted_sha,
        resolved_python_version=plan.python_version,
        validation_profile=plan.validation_profile,
        command_profile=plan.command_profile,
        stage_count=stage_count,
        cleanup_result="registered",
        evidence_id=stable_identifier("python", material, length=28),
    )
