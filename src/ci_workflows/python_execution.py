"Isolated Podman execution and shared helpers for product-neutral Python validation."

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
from .python_types import PythonValidationError, PythonValidationPlan, PythonValidationResult

_PODMAN_RUNTIME_SETUP_EXIT = 80
_PODMAN_DEPENDENCY_RESTORE_EXIT = 81
_PODMAN_SCRIPT_INVOCATION_EXIT = 82


def _report_failure(stage: str, code: str) -> None:
    sys.stderr.write(f"python validation stage failed: {stage}:{code}\n")


def _classify_script_failure(output: str) -> str:
    lowered = output.casefold()
    if any(marker in lowered for marker in ("temporary failure in name resolution", "name or service not known", "could not resolve host", "no such host")):
        return "script_network_failure"
    if any(marker in lowered for marker in ("certificate verify failed", "certificate_verify_failed", "x509: certificate")):
        return "script_tls_failure"
    if "command not found" in lowered or ": not found" in lowered:
        return "script_tool_missing"
    if "checksum mismatch" in lowered:
        return "script_integrity_failure"
    return "script_command_failed"


def run_command(argv: Sequence[str], *, cwd: Path, environment: Mapping[str, str], timeout_seconds: int, code: str = "command_failed", allow_failure: bool = False, failure_stage: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(list(argv), cwd=cwd, env=dict(environment), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as error:
        if allow_failure:
            return subprocess.CompletedProcess(list(argv), 124, "", "")
        if failure_stage is not None:
            _report_failure(failure_stage, code)
        raise PythonValidationError(code) from error
    if completed.returncode != 0 and not allow_failure:
        if failure_stage is not None:
            _report_failure(failure_stage, code)
        raise PythonValidationError(code)
    return completed


def git_output(root: Path, arguments: Sequence[str], code: str) -> str:
    return run_command(["git", *arguments], cwd=root, environment={**os.environ, "LC_ALL": "C", "LANG": "C"}, timeout_seconds=60, code=code).stdout.strip()


def verify_exact_source(source_root: Path, admitted_sha: str) -> None:
    require(source_root.is_dir() and (source_root / ".git").exists(), "source_mismatch")
    require(git_output(source_root, ["rev-parse", "HEAD"], "source_mismatch") == admitted_sha, "source_mismatch")
    require(not git_output(source_root, ["status", "--porcelain=v1", "--untracked-files=all"], "dirty_tree"), "dirty_tree")


def copy_source(source_root: Path, destination: Path) -> None:
    require(not destination.exists(), "isolation_unavailable")
    for current, directories, files in os.walk(source_root):
        current_path = Path(current)
        relative = current_path.relative_to(source_root)
        for name in [*directories, *files]:
            require(not (current_path / name).is_symlink(), "isolation_unavailable")
        target = destination / relative
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(current_path / name, target / name, follow_symlinks=False)


def command_environment(plan: PythonValidationPlan, base: Mapping[str, str]) -> dict[str, str]:
    del plan
    allowed = {"PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "VIRTUAL_ENV"}
    result = {name: value for name, value in base.items() if name in allowed}
    result.update({"CI":"true", "LC_ALL":"C.UTF-8", "LANG":"C.UTF-8", "PYTHONDONTWRITEBYTECODE":"1", "PIP_DISABLE_PIP_VERSION_CHECK":"1", "PIP_NO_CACHE_DIR":"1"})
    return result


def podman_command(state_root: Path) -> list[str]:
    return ["podman", "--storage-driver", "vfs", "--root", str(state_root / "python-validation" / "podman-storage")]


def container_script(plan: PythonValidationPlan) -> str:
    target = "/work/source" if plan.working_directory == "." else f"/work/source/{plan.working_directory}"
    script = f"/work/source/{plan.script_path}"
    lines = [
        "set -eu",
        f"cp -a /src/. /work/source || exit {_PODMAN_RUNTIME_SETUP_EXIT}",
        f"cd {shlex.quote(target)} || exit {_PODMAN_RUNTIME_SETUP_EXIT}",
        f"python -m venv /work/venv || exit {_PODMAN_RUNTIME_SETUP_EXIT}",
    ]
    if plan.dependency_file is not None:
        lines.append("/work/venv/bin/python -m pip install --no-input --no-cache-dir " + f"-r {shlex.quote('/work/source/' + plan.dependency_file)} || exit {_PODMAN_DEPENDENCY_RESTORE_EXIT}")
    lines.extend(["export PATH=/work/venv/bin:$PATH", f"{shlex.quote(script)} || exit {_PODMAN_SCRIPT_INVOCATION_EXIT}"])
    return "\n".join(lines)


def write_environment_file(path: Path, values: Mapping[str, str]) -> None:
    require(all(name and name.replace("_", "").isalnum() and name.upper() == name and isinstance(value, str) and "\n" not in value and "\r" not in value for name, value in values.items()), "invalid_input")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{name}={value}\n" for name, value in sorted(values.items())), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def cleanup_podman(command: Sequence[str], *, containers: Sequence[str], network: str | None, volume: str | None, images: Sequence[str], cwd: Path, environment: Mapping[str, str]) -> None:
    failed = False
    for container in containers:
        completed = run_command([*command, "rm", "-f", container], cwd=cwd, environment=environment, timeout_seconds=30, allow_failure=True)
        failed = failed or completed.returncode not in {0, 1, 125}
    if network:
        run_command([*command, "network", "rm", network], cwd=cwd, environment=environment, timeout_seconds=30, allow_failure=True)
    if volume:
        run_command([*command, "volume", "rm", "-f", volume], cwd=cwd, environment=environment, timeout_seconds=30, allow_failure=True)
    for image in images:
        run_command([*command, "image", "rm", "-f", image], cwd=cwd, environment=environment, timeout_seconds=60, allow_failure=True)
    for container in containers:
        failed = failed or run_command([*command, "container", "exists", container], cwd=cwd, environment=environment, timeout_seconds=10, allow_failure=True).returncode == 0
    if network:
        failed = failed or run_command([*command, "network", "exists", network], cwd=cwd, environment=environment, timeout_seconds=10, allow_failure=True).returncode == 0
    if volume:
        failed = failed or run_command([*command, "volume", "exists", volume], cwd=cwd, environment=environment, timeout_seconds=10, allow_failure=True).returncode == 0
    require(not failed, "cleanup_failed")


def _resource_token(plan: PythonValidationPlan, environment: Mapping[str, str]) -> str:
    return stable_identifier("py", {"run_id":environment.get("GITHUB_RUN_ID", "local"), "attempt":environment.get("GITHUB_RUN_ATTEMPT", "1"), "repository":plan.repository, "profile":plan.validation_profile}, length=16)


def _start_postgres(podman: Sequence[str], *, source_root: Path, state_root: Path, plan: PythonValidationPlan, contract: Mapping[str, Any], environment: Mapping[str, str], container: str, network: str, volume: str) -> str:
    postgres = contract["postgres"]
    password = secrets.token_urlsafe(24)
    env_file = state_root / "python-validation" / "postgres.env"
    write_environment_file(env_file, {"POSTGRES_USER":str(postgres["username"]), "POSTGRES_PASSWORD":password, "POSTGRES_DB":str(postgres["database"])})
    run_command([*podman, "network", "create", network], cwd=source_root, environment=environment, timeout_seconds=30, code="isolation_unavailable", failure_stage="runtime-container-setup")
    run_command([*podman, "volume", "create", volume], cwd=source_root, environment=environment, timeout_seconds=30, code="isolation_unavailable", failure_stage="runtime-container-setup")
    run_command([*podman, "run", "-d", "--name", container, "--network", network, "--network-alias", str(postgres["network_alias"]), "--env-file", str(env_file), "-v", f"{volume}:/var/lib/postgresql/data", str(plan.postgres_runtime_reference)], cwd=source_root, environment=environment, timeout_seconds=60, code="isolation_unavailable", failure_stage="runtime-container-setup")
    ready = False
    for _ in range(plan.readiness_attempts):
        completed = run_command([*podman, "exec", container, "pg_isready", "-U", str(postgres["username"]), "-d", str(postgres["database"])], cwd=source_root, environment=environment, timeout_seconds=10, allow_failure=True)
        if completed.returncode == 0:
            ready = True
            break
        time.sleep(plan.readiness_interval_seconds)
    if not ready:
        _report_failure("runtime-container-setup", "postgres_readiness_timeout")
    require(ready, "postgres_readiness_timeout")
    return f"postgresql://{postgres['username']}:{password}@{postgres['network_alias']}:5432/{postgres['database']}"


def _raise_podman_execution_failure(returncode: int, output: str = "") -> None:
    if returncode == _PODMAN_DEPENDENCY_RESTORE_EXIT:
        code, stage, diagnostic = "dependency_restore_failed", "dependency-restoration", "dependency_restore_failed"
    elif returncode == _PODMAN_SCRIPT_INVOCATION_EXIT:
        code, stage, diagnostic = "command_failed", "script-invocation", _classify_script_failure(output)
    else:
        code, stage, diagnostic = "isolation_unavailable", "runtime-container-setup", "isolation_unavailable"
    _report_failure(stage, diagnostic)
    raise PythonValidationError(code)


def execute_podman_plan(source_root: Path, state_root: Path, plan: PythonValidationPlan, contract: Mapping[str, Any], environment: Mapping[str, str]) -> int:
    require(plan.runtime_reference is not None, "toolchain_mismatch")
    require(shutil.which("podman") is not None, "isolation_unavailable")
    require(shutil.which("docker") is None and shutil.which("dockerd") is None, "isolation_unavailable")
    require(not Path("/var/run/docker.sock").exists() and not Path("/run/docker.sock").exists(), "isolation_unavailable")
    podman_root = state_root / "python-validation"
    for path in (podman_root / "podman-storage", podman_root / "work"):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    podman = podman_command(state_root)
    execution_environment = {**environment, "LC_ALL":"C", "LANG":"C"}
    run_command([*podman, "info"], cwd=source_root, environment=execution_environment, timeout_seconds=60, code="isolation_unavailable", failure_stage="runtime-container-setup")
    images: list[str] = []
    for image in (plan.runtime_reference, plan.postgres_runtime_reference):
        if image:
            run_command([*podman, "pull", "--platform", "linux/amd64", image], cwd=source_root, environment=execution_environment, timeout_seconds=180, code="toolchain_mismatch", failure_stage="runtime-container-setup")
            if image not in images:
                images.append(image)
    token = _resource_token(plan, environment)
    validation_container = f"{token}-validation"
    postgres_container = f"{token}-postgres"
    network = f"{token}-network" if plan.postgres_runtime_reference else None
    volume = f"{token}-pgdata" if plan.postgres_runtime_reference else None
    original_error: BaseException | None = None
    try:
        validation_environment = {"CI":"true", "PYTHONDONTWRITEBYTECODE":"1", "PIP_DISABLE_PIP_VERSION_CHECK":"1", "PIP_NO_CACHE_DIR":"1"}
        if network and volume:
            database_url = _start_postgres(podman, source_root=source_root, state_root=state_root, plan=plan, contract=contract, environment=execution_environment, container=postgres_container, network=network, volume=volume)
            validation_environment[str(contract["postgres"]["connection_environment_variable"])] = database_url
        env_file = podman_root / "validation.env"
        write_environment_file(env_file, validation_environment)
        arguments = [*podman, "run", "--rm", "--name", validation_container]
        if network:
            arguments.extend(["--network", network])
        arguments.extend(["--env-file", str(env_file), "-v", f"{source_root}:/src:ro", "-v", f"{podman_root / 'work'}:/work", plan.runtime_reference, "sh", "-ceu", container_script(plan)])
        completed = run_command(arguments, cwd=source_root, environment=execution_environment, timeout_seconds=plan.timeout_minutes * 60, allow_failure=True)
        if completed.returncode != 0:
            _raise_podman_execution_failure(completed.returncode, (completed.stdout or "") + (completed.stderr or ""))
    except BaseException as error:
        original_error = error
    try:
        cleanup_podman(podman, containers=[validation_container, postgres_container], network=network, volume=volume, images=images, cwd=source_root, environment=execution_environment)
    except BaseException as cleanup_error:
        raise PythonValidationError("cleanup_failed") from cleanup_error
    if original_error is not None:
        raise original_error
    return 1


def result_from_plan(plan: PythonValidationPlan, stage_count: int) -> PythonValidationResult:
    material = {"source_sha":plan.admitted_sha, "validation_profile":plan.validation_profile, "python_version":plan.python_version, "script_contract":"consumer-owned-executable", "stage_count":stage_count, "cleanup":"registered"}
    return PythonValidationResult(source_sha=plan.admitted_sha, resolved_python_version=plan.python_version, validation_profile=plan.validation_profile, stage_count=stage_count, cleanup_result="registered", evidence_id=stable_identifier("python", material, length=28))
