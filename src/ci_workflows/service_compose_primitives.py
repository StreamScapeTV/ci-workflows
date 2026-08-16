"""Product-neutral ephemeral Compose service-stack primitives for shared CI."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from .runtime_primitives import ProcessResult, RuntimePrimitiveError, finalize_temporary_paths, run_process
from .service_primitives import (
    ServicePrimitiveError,
    ServiceReadinessResult,
    normalize_postgres_connection,
    wait_for_http_service,
    wait_for_postgres,
    wait_for_tcp_service,
)

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_BYTES = 64 * 1024
_MAX_ENV_FILES = 16
_MAX_READINESS_CHECKS = 32
_MAX_LOG_BYTES = 1024 * 1024
_MAX_LOG_LINES = 10000
_MAX_TIMEOUT_SECONDS = 24 * 60 * 60
_RESERVED_OPTIONS = ("-f", "--file", "-p", "--project-name", "--project-directory", "--env-file")

ProcessRunner = Callable[..., ProcessResult]
ReadinessKind = Literal["tcp", "http", "postgres"]
ComposeTool = Literal["docker", "podman"]


class ServiceComposeError(RuntimeError):
    """Fail closed with stable non-secret primary and cleanup codes."""
    def __init__(self, code: str, *, cleanup_code: str = "") -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("service compose error code must be a safe identifier")
        if cleanup_code and _ERROR_CODE.fullmatch(cleanup_code) is None:
            raise ValueError("service compose cleanup code must be a safe identifier")
        self.code = code
        self.cleanup_code = cleanup_code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ComposeProject:
    project_name: str
    tool: ComposeTool
    compose_relative: str
    env_file_count: int
    root: Path = field(repr=False, compare=False)
    compose_file: Path = field(repr=False, compare=False)
    env_files: tuple[Path, ...] = field(repr=False, compare=False)

    def public_projection(self) -> dict[str, object]:
        return {
            "project_name": self.project_name,
            "tool": self.tool,
            "compose_file": self.compose_relative,
            "env_file_count": self.env_file_count,
        }


@dataclass(frozen=True, slots=True)
class ComposeCommandResult:
    operation: str
    returncode: int
    timed_out: bool
    output_sha256: str
    output_bytes: int
    service: str = ""


@dataclass(frozen=True, slots=True)
class ComposeServiceState:
    service: str
    container_name: str
    state: str
    health: str
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class ComposePSResult:
    command: ComposeCommandResult
    services: tuple[ComposeServiceState, ...]


@dataclass(frozen=True, slots=True)
class ComposeLogCapture:
    command: ComposeCommandResult
    service: str
    sha256: str
    size_bytes: int
    truncated: bool
    text: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ComposeReadinessCheck:
    service: str
    kind: ReadinessKind
    environment: Mapping[str, str] = field(repr=False, compare=False)
    timeout_seconds: float = 60.0
    interval_seconds: float = 0.25
    expected_statuses: tuple[int, ...] = (200, 204)

    def __post_init__(self) -> None:
        _service(self.service)
        if self.kind not in ("tcp", "http", "postgres"):
            raise ServiceComposeError("compose_readiness_kind_invalid")
        _duration(self.timeout_seconds, "compose_readiness_timeout_invalid")
        _duration(self.interval_seconds, "compose_readiness_interval_invalid")
        _environment(self.environment)
        if self.kind == "http":
            if (
                not isinstance(self.expected_statuses, tuple)
                or not self.expected_statuses
                or len(self.expected_statuses) > 32
                or any(
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or not 100 <= status <= 599
                    for status in self.expected_statuses
                )
            ):
                raise ServiceComposeError("compose_readiness_status_invalid")


@dataclass(frozen=True, slots=True)
class ComposeReadinessStatus:
    service: str
    kind: ReadinessKind
    ready: bool
    attempts: int
    status: int | None


@dataclass(frozen=True, slots=True)
class ComposeStackResult:
    project_name: str
    up: ComposeCommandResult
    readiness: tuple[ComposeReadinessStatus, ...]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ServiceComposeError(code)


def _plain(value: object, *, code: str, maximum: int = 4096) -> str:
    _require(isinstance(value, str), code)
    text = str(value)
    _require(
        bool(text)
        and len(text.encode("utf-8")) <= maximum
        and "\x00" not in text
        and "\r" not in text
        and "\n" not in text,
        code,
    )
    return text


def _service(value: object) -> str:
    text = _plain(value, code="compose_service_invalid", maximum=63)
    _require(_SERVICE_NAME.fullmatch(text) is not None, "compose_service_invalid")
    return text


def _services(values: Sequence[str]) -> tuple[str, ...]:
    _require(
        isinstance(values, Sequence) and not isinstance(values, (str, bytes)),
        "compose_services_invalid",
    )
    result = tuple(_service(value) for value in values)
    _require(len(result) <= 64, "compose_services_invalid")
    _require(len(result) == len(set(result)), "compose_services_invalid")
    return result


def _duration(value: float | int, code: str) -> float:
    _require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and 0 < float(value) <= _MAX_TIMEOUT_SECONDS,
        code,
    )
    return float(value)


def _arguments(values: Sequence[str], *, code: str) -> tuple[str, ...]:
    _require(isinstance(values, Sequence) and not isinstance(values, (str, bytes)), code)
    result = tuple(_plain(value, code=code) for value in values)
    _require(len(result) <= _MAX_ARGUMENTS, code)
    _require(sum(len(value.encode("utf-8")) for value in result) <= _MAX_ARGUMENT_BYTES, code)
    return result


def _options(values: Sequence[str]) -> tuple[str, ...]:
    result = _arguments(values, code="compose_options_invalid")
    for option in result:
        for reserved in _RESERVED_OPTIONS:
            _require(
                option != reserved and not option.startswith(reserved + "="),
                "compose_options_reserved",
            )
    return result


def _environment(environment: Mapping[str, str]) -> dict[str, str]:
    _require(isinstance(environment, Mapping), "compose_environment_invalid")
    result: dict[str, str] = {}
    for name, value in environment.items():
        _require(
            isinstance(name, str)
            and _ENVIRONMENT_NAME.fullmatch(name) is not None
            and isinstance(value, str)
            and "\x00" not in value,
            "compose_environment_invalid",
        )
        result[name] = value
    return result


def _project_root(root: Path) -> Path:
    candidate = Path(root)
    _require(
        candidate.is_absolute() and not candidate.is_symlink() and candidate.is_dir(),
        "compose_root_invalid",
    )
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        raise ServiceComposeError("compose_root_invalid") from error


def _bounded_file(
    root: Path,
    value: Path | str,
    *,
    code: str,
    suffixes: tuple[str, ...] = (),
) -> tuple[Path, str]:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ServiceComposeError(code) from error
    _require(bool(relative.parts), code)
    cursor = root
    for part in relative.parts:
        _require(part not in ("", ".", ".."), code)
        cursor = cursor / part
        _require(not cursor.is_symlink(), code)
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ServiceComposeError(code) from error
    _require(resolved.is_file() and not resolved.is_symlink(), code)
    if suffixes:
        _require(resolved.suffix.casefold() in suffixes, code)
    return resolved, relative.as_posix()


def validate_compose_project(
    *,
    project_root: Path,
    compose_file: Path | str,
    project_name: str,
    tool: str,
    env_files: Sequence[Path | str] = (),
) -> ComposeProject:
    """Validate one job-local Compose identity and its caller-owned files."""
    root = _project_root(project_root)
    name = _plain(project_name, code="compose_project_name_invalid", maximum=63)
    _require(_PROJECT_NAME.fullmatch(name) is not None, "compose_project_name_invalid")
    _require(tool in ("docker", "podman"), "compose_tool_invalid")
    compose, compose_relative = _bounded_file(
        root,
        compose_file,
        code="compose_file_invalid",
        suffixes=(".yml", ".yaml"),
    )
    _require(
        isinstance(env_files, Sequence) and not isinstance(env_files, (str, bytes)),
        "compose_env_files_invalid",
    )
    _require(len(env_files) <= _MAX_ENV_FILES, "compose_env_files_invalid")
    bounded_env_files: list[Path] = []
    for value in env_files:
        bounded, _relative = _bounded_file(root, value, code="compose_env_file_invalid")
        bounded_env_files.append(bounded)
    _require(len(bounded_env_files) == len(set(bounded_env_files)), "compose_env_files_invalid")
    return ComposeProject(
        project_name=name,
        tool=tool,
        compose_relative=compose_relative,
        env_file_count=len(bounded_env_files),
        root=root,
        compose_file=compose,
        env_files=tuple(bounded_env_files),
    )


def _base_argv(project: ComposeProject) -> tuple[str, ...]:
    prefix = ("docker", "compose") if project.tool == "docker" else ("podman", "compose")
    arguments: list[str] = [
        *prefix,
        "-f",
        str(project.compose_file),
        "--project-name",
        project.project_name,
    ]
    for env_file in project.env_files:
        arguments.extend(["--env-file", str(env_file)])
    return tuple(arguments)


def _invoke(
    project: ComposeProject,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    timeout_seconds: float | int,
    runner: ProcessRunner,
) -> ProcessResult:
    process_environment = _environment(environment)
    timeout = _duration(timeout_seconds, "compose_timeout_invalid")
    argv = _arguments(arguments, code="compose_arguments_invalid")
    try:
        result = runner(argv, cwd=project.root, environment=process_environment, timeout_seconds=timeout)
    except (RuntimePrimitiveError, ServicePrimitiveError) as error:
        raise ServiceComposeError("compose_process_boundary_failed") from error
    except ServiceComposeError:
        raise
    except Exception as error:
        raise ServiceComposeError("compose_process_boundary_failed") from error
    _require(isinstance(result, ProcessResult), "compose_process_result_invalid")
    return result


def _command_result(operation: str, result: ProcessResult, *, service: str = "") -> ComposeCommandResult:
    safe_operation = _plain(operation, code="compose_operation_invalid", maximum=64)
    if result.timed_out:
        raise ServiceComposeError(f"compose_{safe_operation}_timeout")
    if result.returncode != 0:
        raise ServiceComposeError(f"compose_{safe_operation}_failed")
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    payload = (stdout + "\0" + stderr).encode("utf-8", errors="replace")
    return ComposeCommandResult(
        operation=safe_operation,
        returncode=int(result.returncode),
        timed_out=False,
        output_sha256=hashlib.sha256(payload).hexdigest(),
        output_bytes=len(payload),
        service=service,
    )


def compose_up(
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    services: Sequence[str] = (),
    options: Sequence[str] = (),
    timeout_seconds: float | int = 600,
    runner: ProcessRunner = run_process,
) -> ComposeCommandResult:
    selected = _services(services)
    argv = (*_base_argv(project), "up", "--detach", "--remove-orphans", *_options(options), *selected)
    result = _invoke(project, argv, environment=environment, timeout_seconds=timeout_seconds, runner=runner)
    return _command_result("up", result)


def compose_down(
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    options: Sequence[str] = (),
    timeout_seconds: float | int = 300,
    runner: ProcessRunner = run_process,
) -> ComposeCommandResult:
    argv = (*_base_argv(project), "down", "--remove-orphans", *_options(options))
    result = _invoke(project, argv, environment=environment, timeout_seconds=timeout_seconds, runner=runner)
    return _command_result("down", result)


def compose_exec(
    project: ComposeProject,
    *,
    service: str,
    command: Sequence[str],
    environment: Mapping[str, str],
    options: Sequence[str] = (),
    timeout_seconds: float | int = 600,
    runner: ProcessRunner = run_process,
) -> ComposeCommandResult:
    selected = _service(service)
    command_argv = _arguments(command, code="compose_command_invalid")
    _require(bool(command_argv), "compose_command_invalid")
    argv = (*_base_argv(project), "exec", "-T", *_options(options), selected, *command_argv)
    result = _invoke(project, argv, environment=environment, timeout_seconds=timeout_seconds, runner=runner)
    return _command_result("exec", result, service=selected)


def compose_run(
    project: ComposeProject,
    *,
    service: str,
    command: Sequence[str],
    environment: Mapping[str, str],
    options: Sequence[str] = (),
    timeout_seconds: float | int = 600,
    runner: ProcessRunner = run_process,
) -> ComposeCommandResult:
    selected = _service(service)
    command_argv = _arguments(command, code="compose_command_invalid")
    _require(bool(command_argv), "compose_command_invalid")
    argv = (*_base_argv(project), "run", "--rm", *_options(options), selected, *command_argv)
    result = _invoke(project, argv, environment=environment, timeout_seconds=timeout_seconds, runner=runner)
    return _command_result("run", result, service=selected)


def _json_rows(text: str) -> list[Mapping[str, object]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows: list[Mapping[str, object]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ServiceComposeError("compose_ps_invalid") from error
            _require(isinstance(item, Mapping), "compose_ps_invalid")
            rows.append(item)
        _require(bool(rows), "compose_ps_invalid")
        return rows
    if isinstance(payload, Mapping):
        return [payload]
    _require(isinstance(payload, list) and payload, "compose_ps_invalid")
    _require(all(isinstance(item, Mapping) for item in payload), "compose_ps_invalid")
    return list(payload)


def _optional_text(value: object, *, maximum: int = 255) -> str:
    if value in (None, ""):
        return ""
    return _plain(value, code="compose_ps_invalid", maximum=maximum)


def _exit_code(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ServiceComposeError("compose_ps_invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,10}", value):
        return int(value)
    raise ServiceComposeError("compose_ps_invalid")


def compose_ps(
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    services: Sequence[str] = (),
    timeout_seconds: float | int = 60,
    runner: ProcessRunner = run_process,
) -> ComposePSResult:
    selected = _services(services)
    argv = (*_base_argv(project), "ps", "--format", "json", *selected)
    raw = _invoke(project, argv, environment=environment, timeout_seconds=timeout_seconds, runner=runner)
    command = _command_result("ps", raw)
    states: list[ComposeServiceState] = []
    for row in _json_rows(raw.stdout):
        service = _service(row.get("Service", row.get("service", "")))
        name = _optional_text(row.get("Name", row.get("name", "")))
        state = _optional_text(row.get("State", row.get("state", "")), maximum=64)
        health = _optional_text(row.get("Health", row.get("health", "")), maximum=64)
        exit_code = _exit_code(row.get("ExitCode", row.get("exit_code")))
        states.append(ComposeServiceState(service, name, state, health, exit_code))
    _require(len(states) <= 128, "compose_ps_invalid")
    return ComposePSResult(
        command=command,
        services=tuple(sorted(states, key=lambda item: (item.service, item.container_name))),
    )


def capture_compose_logs(
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    service: str = "",
    tail_lines: int = 200,
    max_bytes: int = 64 * 1024,
    timeout_seconds: float | int = 60,
    runner: ProcessRunner = run_process,
) -> ComposeLogCapture:
    _require(type(tail_lines) is int and 1 <= tail_lines <= _MAX_LOG_LINES, "compose_log_lines_invalid")
    _require(type(max_bytes) is int and 1 <= max_bytes <= _MAX_LOG_BYTES, "compose_log_bytes_invalid")
    selected = _service(service) if service else ""
    argv: tuple[str, ...] = (
        *_base_argv(project),
        "logs",
        "--no-color",
        "--tail",
        str(tail_lines),
        *((selected,) if selected else ()),
    )
    raw = _invoke(project, argv, environment=environment, timeout_seconds=timeout_seconds, runner=runner)
    command = _command_result("logs", raw, service=selected)
    encoded = raw.stdout.encode("utf-8", errors="replace")
    truncated = len(encoded) > max_bytes
    if truncated:
        encoded = encoded[-max_bytes:]
    text = encoded.decode("utf-8", errors="replace")
    return ComposeLogCapture(
        command=command,
        service=selected,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        truncated=truncated,
        text=text,
    )


def _readiness_environment(base: Mapping[str, str], overlay: Mapping[str, str]) -> dict[str, str]:
    result = _environment(base)
    result.update(_environment(overlay))
    return result


def wait_for_compose_services(
    project: ComposeProject,
    checks: Sequence[ComposeReadinessCheck],
    *,
    environment: Mapping[str, str],
) -> tuple[ComposeReadinessStatus, ...]:
    _require(
        isinstance(checks, Sequence)
        and not isinstance(checks, (str, bytes))
        and 0 < len(checks) <= _MAX_READINESS_CHECKS,
        "compose_readiness_checks_invalid",
    )
    seen: set[tuple[str, str]] = set()
    statuses: list[ComposeReadinessStatus] = []
    for check in checks:
        _require(isinstance(check, ComposeReadinessCheck), "compose_readiness_checks_invalid")
        key = (check.service, check.kind)
        _require(key not in seen, "compose_readiness_checks_invalid")
        seen.add(key)
        check_environment = _readiness_environment(environment, check.environment)
        try:
            if check.kind == "tcp":
                result = wait_for_tcp_service(
                    check_environment,
                    timeout_seconds=check.timeout_seconds,
                    interval_seconds=check.interval_seconds,
                )
            elif check.kind == "http":
                result = wait_for_http_service(
                    check_environment,
                    timeout_seconds=check.timeout_seconds,
                    interval_seconds=check.interval_seconds,
                    expected_statuses=check.expected_statuses,
                )
            else:
                connection = normalize_postgres_connection(check_environment)
                result = wait_for_postgres(
                    connection,
                    cwd=project.root,
                    environment=check_environment,
                    timeout_seconds=check.timeout_seconds,
                    interval_seconds=check.interval_seconds,
                )
        except ServicePrimitiveError as error:
            raise ServiceComposeError("compose_readiness_boundary_failed") from error
        _require(isinstance(result, ServiceReadinessResult), "compose_readiness_result_invalid")
        statuses.append(
            ComposeReadinessStatus(
                service=check.service,
                kind=check.kind,
                ready=result.ready,
                attempts=result.attempts,
                status=result.status,
            )
        )
    return tuple(statuses)


def start_compose_stack(
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    readiness: Sequence[ComposeReadinessCheck],
    services: Sequence[str] = (),
    up_options: Sequence[str] = (),
    down_options: Sequence[str] = (),
    up_timeout_seconds: float | int = 600,
    down_timeout_seconds: float | int = 300,
    runner: ProcessRunner = run_process,
) -> ComposeStackResult:
    """Bring a stack up, wait for readiness, and tear it down immediately on failure."""
    try:
        up = compose_up(
            project,
            environment=environment,
            services=services,
            options=up_options,
            timeout_seconds=up_timeout_seconds,
            runner=runner,
        )
        statuses = wait_for_compose_services(project, readiness, environment=environment)
        if not all(status.ready for status in statuses):
            raise ServiceComposeError("compose_readiness_failed")
        return ComposeStackResult(project.project_name, up, statuses)
    except ServiceComposeError as primary:
        try:
            compose_down(
                project,
                environment=environment,
                options=down_options,
                timeout_seconds=down_timeout_seconds,
                runner=runner,
            )
        except ServiceComposeError as cleanup:
            raise ServiceComposeError(primary.code, cleanup_code=cleanup.code) from primary
        raise


def cleanup_compose_stack(
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    options: Sequence[str] = (),
    timeout_seconds: float | int = 300,
    runner: ProcessRunner = run_process,
) -> ComposeCommandResult:
    """Terminal cleanup for one exact validated run-owned Compose project."""
    return compose_down(
        project,
        environment=environment,
        options=options,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def cleanup_compose_temporary_state(paths: Sequence[Path], *, root: Path) -> int:
    """Remove only caller-registered temporary Compose state beneath a reviewed root."""
    try:
        return finalize_temporary_paths(paths, root=root)
    except RuntimePrimitiveError as error:
        raise ServiceComposeError("compose_state_cleanup_failed") from error
