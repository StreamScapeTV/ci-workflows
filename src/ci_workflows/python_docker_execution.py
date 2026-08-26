"""Docker-backed execution for reviewed GitHub-hosted Python validation.

This module is an execution implementation detail of ``validation.python``.
Callers still select only the bounded validation profile and execution backend;
they never select Docker, a socket, a runner label, or a service image.
"""
from __future__ import annotations

import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .foundation_types import stable_identifier
from .python_contract import require
from .python_execution import container_script, run_command, write_environment_file
from .python_types import PythonValidationError, PythonValidationPlan

_DEPENDENCY_RESTORE_EXIT = 81
_SCRIPT_INVOCATION_EXIT = 82


def _report_failure(stage: str, code: str) -> None:
    sys.stderr.write(f"python validation stage failed: {stage}:{code}\n")


def _raise_container_execution_failure(returncode: int) -> None:
    if returncode == _DEPENDENCY_RESTORE_EXIT:
        code, stage = "dependency_restore_failed", "dependency-restoration"
    elif returncode == _SCRIPT_INVOCATION_EXIT:
        code, stage = "command_failed", "script-invocation"
    else:
        code, stage = "isolation_unavailable", "runtime-container-setup"
    _report_failure(stage, code)
    raise PythonValidationError(code)


def _docker_command() -> list[str]:
    return ["docker"]


def _resource_token(plan: PythonValidationPlan, environment: Mapping[str, str]) -> str:
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


def _cleanup_docker(
    command: Sequence[str],
    *,
    containers: Sequence[str],
    network: str | None,
    volume: str | None,
    images: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    failed = False
    for container in containers:
        completed = run_command(
            [*command, "rm", "-f", container],
            cwd=cwd,
            environment=environment,
            timeout_seconds=30,
            allow_failure=True,
        )
        failed = failed or completed.returncode not in {0, 1}
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
            [*command, "container", "inspect", container],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    if network:
        failed = failed or run_command(
            [*command, "network", "inspect", network],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    if volume:
        failed = failed or run_command(
            [*command, "volume", "inspect", volume],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    for image in images:
        failed = failed or run_command(
            [*command, "image", "inspect", image],
            cwd=cwd,
            environment=environment,
            timeout_seconds=10,
            allow_failure=True,
        ).returncode == 0
    require(not failed, "cleanup_failed")


def _start_postgres(
    docker: Sequence[str],
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
        [*docker, "network", "create", network],
        cwd=source_root,
        environment=environment,
        timeout_seconds=30,
        code="isolation_unavailable",
        failure_stage="runtime-container-setup",
    )
    run_command(
        [*docker, "volume", "create", volume],
        cwd=source_root,
        environment=environment,
        timeout_seconds=30,
        code="isolation_unavailable",
        failure_stage="runtime-container-setup",
    )
    run_command(
        [
            *docker,
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
        failure_stage="runtime-container-setup",
    )
    ready = False
    for _ in range(plan.readiness_attempts):
        completed = run_command(
            [
                *docker,
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
        f"postgresql://{postgres['username']}:{password}"
        f"@{postgres['network_alias']}:5432/{postgres['database']}"
    )


def execute_docker_plan(
    source_root: Path,
    state_root: Path,
    plan: PythonValidationPlan,
    contract: Mapping[str, Any],
    environment: Mapping[str, str],
) -> int:
    """Execute the existing container validation plan on fixed hosted Docker.

    Docker is admitted only for the explicit ``github-hosted`` execution backend.
    The public validation profile, exact runtime digests, consumer script contract,
    PostgreSQL handoff, and cleanup semantics are otherwise unchanged.
    """

    require(environment.get("INPUT_EXECUTION_BACKEND") == "github-hosted", "isolation_unavailable")
    require(plan.runtime_reference is not None, "toolchain_mismatch")
    require(shutil.which("docker") is not None, "isolation_unavailable")

    docker_root = state_root / "python-validation"
    work_root = docker_root / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    work_root.chmod(0o700)
    docker = _docker_command()
    execution_environment = {**environment, "LC_ALL": "C", "LANG": "C"}
    run_command(
        [*docker, "info"],
        cwd=source_root,
        environment=execution_environment,
        timeout_seconds=60,
        code="isolation_unavailable",
        failure_stage="runtime-container-setup",
    )

    images: list[str] = []
    for image in (plan.runtime_reference, plan.postgres_runtime_reference):
        if image:
            run_command(
                [*docker, "pull", "--platform", "linux/amd64", image],
                cwd=source_root,
                environment=execution_environment,
                timeout_seconds=180,
                code="toolchain_mismatch",
                failure_stage="runtime-container-setup",
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
            "CI": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_CACHE_DIR": "1",
        }
        if network and volume:
            database_url = _start_postgres(
                docker,
                source_root=source_root,
                state_root=state_root,
                plan=plan,
                contract=contract,
                environment=execution_environment,
                container=postgres_container,
                network=network,
                volume=volume,
            )
            validation_environment[str(contract["postgres"]["connection_environment_variable"])] = database_url
        env_file = docker_root / "validation.env"
        write_environment_file(env_file, validation_environment)
        arguments = [*docker, "run", "--rm", "--name", validation_container]
        if network:
            arguments.extend(["--network", network])
        arguments.extend(
            [
                "--env-file",
                str(env_file),
                "-v",
                f"{source_root}:/src:ro",
                "-v",
                f"{work_root}:/work",
                plan.runtime_reference,
                "sh",
                "-ceu",
                container_script(plan),
            ]
        )
        completed = run_command(
            arguments,
            cwd=source_root,
            environment=execution_environment,
            timeout_seconds=plan.timeout_minutes * 60,
            allow_failure=True,
        )
        if completed.returncode != 0:
            _raise_container_execution_failure(completed.returncode)
    except BaseException as error:
        original_error = error
    try:
        _cleanup_docker(
            docker,
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
    return 1


__all__ = ("execute_docker_plan",)
