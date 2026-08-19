"""Product-neutral orchestration for ephemeral service/Compose validation."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .ciw_types import CIWContext, CIWResult, input_value, write_command_file
from .runtime_primitives import ProcessResult, RuntimePrimitiveError, canonical_json, run_process
from .service_compose_primitives import (
    ComposeProject,
    ComposeReadinessCheck,
    ComposeReadinessStatus,
    ServiceComposeError,
    capture_compose_logs,
    cleanup_compose_stack,
    compose_ps,
    compose_up,
    validate_compose_project,
    wait_for_compose_services,
)

_EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUN_COMPONENT = re.compile(r"^[1-9][0-9]{0,19}$")
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|authorization|secret|api[_-]?key)\s*[:=]\s*\S+"
)
_URL_CREDENTIAL = re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@")
_MAX_JSON_BYTES = 64 * 1024
_MAX_DIAGNOSTIC_BYTES = 16 * 1024
_COMPOSE_ENVIRONMENT = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "DOCKER_HOST",
    "CONTAINER_HOST",
)
_VALIDATION_ENVIRONMENT = (
    "CI",
    "GITHUB_ACTIONS",
    "GITHUB_REPOSITORY",
    "GITHUB_SHA",
    "GITHUB_REF",
    "GITHUB_EVENT_NAME",
    "RUNNER_OS",
    "RUNNER_ARCH",
)


def configure_compose_validate(_parser: argparse.ArgumentParser) -> None:
    """The reusable workflow supplies its bounded request through INPUT_* values."""


def _fail(code: str) -> None:
    raise ServiceComposeError(code)


def _required_input(environment: Mapping[str, str], name: str) -> str:
    value = input_value(environment, name)
    if not value or "\x00" in value:
        _fail("compose_input_invalid")
    return value


def _exact_sha(value: str) -> str:
    if _EXACT_SHA.fullmatch(value) is None:
        _fail("compose_source_sha_invalid")
    return value


def _json_array(raw: str, *, code: str, maximum: int) -> list[Any]:
    if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
        _fail(code)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ServiceComposeError(code) from error
    if not isinstance(value, list) or len(value) > maximum:
        _fail(code)
    return value


def _safe_relative(value: str, *, code: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        _fail(code)
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts if part != "."):
        _fail(code)
    return path


def _bounded_directory(workspace: Path, value: str) -> Path:
    root = Path(workspace)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        _fail("compose_workspace_invalid")
    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise ServiceComposeError("compose_workspace_invalid") from error
    relative = Path(".") if value == "." else _safe_relative(value, code="compose_working_directory_invalid")
    candidate = root / relative
    cursor = root
    for part in relative.parts:
        if part == ".":
            continue
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("compose_working_directory_invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ServiceComposeError("compose_working_directory_invalid") from error
    if not resolved.is_dir() or (resolved != root and root not in resolved.parents):
        _fail("compose_working_directory_invalid")
    return resolved


def _bounded_file(root: Path, value: str, *, code: str, executable: bool = False) -> Path:
    relative = _safe_relative(value, code=code)
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail(code)
    candidate = root / relative
    if candidate.is_symlink():
        _fail(code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ServiceComposeError(code) from error
    if not resolved.is_file() or root not in resolved.parents:
        _fail(code)
    if executable and not os.access(resolved, os.X_OK):
        _fail("compose_validation_script_not_executable")
    return resolved


def _project_name(environment: Mapping[str, str]) -> str:
    run_id = environment.get("GITHUB_RUN_ID", "")
    attempt = environment.get("GITHUB_RUN_ATTEMPT", "")
    if _RUN_COMPONENT.fullmatch(run_id) is None or _RUN_COMPONENT.fullmatch(attempt) is None:
        _fail("compose_run_identity_invalid")
    value = f"ciw-{run_id}-{attempt}"
    if len(value) > 63:
        _fail("compose_run_identity_invalid")
    return value


def _string_list(raw: str, *, code: str, maximum: int, service: bool = False) -> tuple[str, ...]:
    values = _json_array(raw, code=code, maximum=maximum)
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or "\x00" in value or "\r" in value or "\n" in value:
            _fail(code)
        if service and _SERVICE.fullmatch(value) is None:
            _fail(code)
        if not service:
            _safe_relative(value, code=code)
        result.append(value)
    if len(result) != len(set(result)):
        _fail(code)
    return tuple(result)


def _number(value: object, *, code: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        _fail(code)
    return float(value)


def _port(value: object, *, code: str) -> str:
    if isinstance(value, bool):
        _fail(code)
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value):
        number = int(value)
    else:
        _fail(code)
    if not 1 <= number <= 65535:
        _fail(code)
    return str(number)


def _plain(value: object, *, code: str, maximum: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        _fail(code)
    return value


def _readiness_checks(raw: str) -> tuple[ComposeReadinessCheck, ...]:
    rows = _json_array(raw, code="compose_readiness_input_invalid", maximum=32)
    if not rows:
        _fail("compose_readiness_input_invalid")
    checks: list[ComposeReadinessCheck] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            _fail("compose_readiness_input_invalid")
        service = _plain(row.get("service"), code="compose_readiness_input_invalid", maximum=63)
        if _SERVICE.fullmatch(service) is None:
            _fail("compose_readiness_input_invalid")
        kind = row.get("kind")
        if kind not in ("tcp", "http", "postgres"):
            _fail("compose_readiness_input_invalid")
        timeout = _number(
            row.get("timeout_seconds", 60),
            code="compose_readiness_input_invalid",
            minimum=0.05,
            maximum=900,
        )
        interval = _number(
            row.get("interval_seconds", 0.25),
            code="compose_readiness_input_invalid",
            minimum=0.01,
            maximum=60,
        )
        environment: dict[str, str]
        statuses = (200, 204)
        if kind == "tcp":
            if not set(row).issubset({"service", "kind", "host", "port", "timeout_seconds", "interval_seconds"}):
                _fail("compose_readiness_input_invalid")
            environment = {
                "SERVICE_HOST": _plain(row.get("host"), code="compose_readiness_input_invalid", maximum=255),
                "SERVICE_PORT": _port(row.get("port"), code="compose_readiness_input_invalid"),
            }
        elif kind == "http":
            if not set(row).issubset(
                {"service", "kind", "url", "expected_statuses", "timeout_seconds", "interval_seconds"}
            ):
                _fail("compose_readiness_input_invalid")
            url = _plain(row.get("url"), code="compose_readiness_input_invalid")
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                _fail("compose_readiness_input_invalid")
            raw_statuses = row.get("expected_statuses", [200, 204])
            if (
                not isinstance(raw_statuses, list)
                or not raw_statuses
                or len(raw_statuses) > 16
                or any(isinstance(item, bool) or not isinstance(item, int) or not 100 <= item <= 599 for item in raw_statuses)
            ):
                _fail("compose_readiness_input_invalid")
            statuses = tuple(raw_statuses)
            environment = {"SERVICE_HTTP_URL": url}
        else:
            if not set(row).issubset(
                {"service", "kind", "host", "port", "database", "user", "timeout_seconds", "interval_seconds"}
            ):
                _fail("compose_readiness_input_invalid")
            environment = {
                "PGHOST": _plain(row.get("host"), code="compose_readiness_input_invalid", maximum=255),
                "PGPORT": _port(row.get("port", 5432), code="compose_readiness_input_invalid"),
                "PGDATABASE": _plain(row.get("database"), code="compose_readiness_input_invalid", maximum=255),
            }
            if "user" in row:
                environment["PGUSER"] = _plain(row.get("user"), code="compose_readiness_input_invalid", maximum=255)
        key = (service, str(kind))
        if key in seen:
            _fail("compose_readiness_input_invalid")
        seen.add(key)
        checks.append(
            ComposeReadinessCheck(
                service=service,
                kind=kind,
                environment=environment,
                timeout_seconds=timeout,
                interval_seconds=interval,
                expected_statuses=statuses,
            )
        )
    return tuple(checks)


def _timeout(environment: Mapping[str, str]) -> float:
    raw = input_value(environment, "validation_timeout_seconds", "900")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", raw):
        _fail("compose_validation_timeout_invalid")
    value = int(raw)
    if not 1 <= value <= 3600:
        _fail("compose_validation_timeout_invalid")
    return float(value)


def _selected_environment(environment: Mapping[str, str], names: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        value = environment.get(name, "")
        if isinstance(value, str) and value and "\x00" not in value:
            result[name] = value
    return result


def _compose_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result = _selected_environment(environment, _COMPOSE_ENVIRONMENT)
    if not result.get("PATH"):
        _fail("compose_runtime_environment_invalid")
    return result


def _validation_environment(environment: Mapping[str, str], project_name: str) -> dict[str, str]:
    result = _compose_environment(environment)
    result.update(_selected_environment(environment, _VALIDATION_ENVIRONMENT))
    result["CIW_COMPOSE_PROJECT_NAME"] = project_name
    return result


def _sanitize_diagnostic(text: str, *, root: Path) -> str:
    rendered = text.replace(str(root), "<project>")
    rendered = _URL_CREDENTIAL.sub("https://<redacted>@", rendered)
    rendered = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", rendered)
    encoded = rendered.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_DIAGNOSTIC_BYTES:
        rendered = encoded[-_MAX_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace")
    return rendered


def _emit_failure_diagnostics(
    context: CIWContext,
    project: ComposeProject,
    *,
    environment: Mapping[str, str],
    services: Sequence[str],
    validation: ProcessResult | None,
) -> None:
    if validation is not None:
        raw = "\n".join(part for part in (validation.stdout, validation.stderr) if part)
        if raw:
            context.stderr.write("validation-diagnostic-begin\n")
            context.stderr.write(_sanitize_diagnostic(raw, root=project.root))
            context.stderr.write("\nvalidation-diagnostic-end\n")
    try:
        state = compose_ps(project, environment=environment, services=services)
        public_state = [
            {
                "service": item.service,
                "state": item.state,
                "health": item.health,
                "exit_code": item.exit_code,
            }
            for item in state.services
        ]
        context.stderr.write(f"compose-state={canonical_json(public_state)}\n")
    except Exception as error:
        code = getattr(error, "code", "compose_diagnostic_unavailable")
        context.stderr.write(f"compose-state-unavailable={code}\n")
    targets: tuple[str, ...] = tuple(services) or ("",)
    for service in targets:
        try:
            captured = capture_compose_logs(
                project,
                environment=environment,
                service=service,
                tail_lines=100,
                max_bytes=_MAX_DIAGNOSTIC_BYTES,
            )
        except Exception as error:
            code = getattr(error, "code", "compose_diagnostic_unavailable")
            context.stderr.write(f"compose-log-unavailable={service or 'all'}:{code}\n")
            continue
        text = _sanitize_diagnostic(captured.text, root=project.root)
        context.stderr.write(f"compose-log-diagnostic-begin service={service or 'all'}\n")
        context.stderr.write(text)
        if text and not text.endswith("\n"):
            context.stderr.write("\n")
        context.stderr.write("compose-log-diagnostic-end\n")


def _failure_outputs(context: CIWContext, error: ServiceComposeError, *, cleanup_result: str) -> None:
    path = context.environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    write_command_file(
        Path(path),
        {
            "result": "failure",
            "test_summary": "{}",
            "cleanup_result": cleanup_result,
            "failure_code": error.code,
            "cleanup_code": error.cleanup_code,
        },
    )


def _readiness_summary(statuses: Sequence[ComposeReadinessStatus]) -> list[dict[str, object]]:
    return [
        {
            "service": item.service,
            "kind": item.kind,
            "ready": item.ready,
            "attempts": item.attempts,
            "status": item.status,
        }
        for item in statuses
    ]


def execute_compose_validate(_args: argparse.Namespace, context: CIWContext) -> CIWResult:
    """Execute one exact-source, run-owned ephemeral Compose validation request."""
    environment = context.environment
    _exact_sha(_required_input(environment, "admitted_sha"))
    workspace_value = environment.get("GITHUB_WORKSPACE", "")
    if not workspace_value:
        _fail("compose_workspace_invalid")
    project_root = _bounded_directory(
        Path(workspace_value),
        input_value(environment, "working_directory", "."),
    )
    compose_file = _required_input(environment, "compose_file")
    validation_script = _bounded_file(
        project_root,
        _required_input(environment, "validation_script_path"),
        code="compose_validation_script_invalid",
        executable=True,
    )
    tool = input_value(environment, "compose_tool", "podman")
    if tool not in ("docker", "podman"):
        _fail("compose_tool_invalid")
    services = _string_list(
        input_value(environment, "services_json", "[]"),
        code="compose_services_input_invalid",
        maximum=64,
        service=True,
    )
    env_files = _string_list(
        input_value(environment, "env_files_json", "[]"),
        code="compose_env_files_input_invalid",
        maximum=16,
    )
    checks = _readiness_checks(_required_input(environment, "readiness_json"))
    project = validate_compose_project(
        project_root=project_root,
        compose_file=compose_file,
        project_name=_project_name(environment),
        tool=tool,
        env_files=env_files,
    )
    compose_environment = _compose_environment(environment)
    validation_environment = _validation_environment(environment, project.project_name)
    validation_timeout = _timeout(environment)

    attempted_up = False
    primary: ServiceComposeError | None = None
    cleanup_code = ""
    statuses: tuple[ComposeReadinessStatus, ...] = ()
    validation_result: ProcessResult | None = None
    try:
        attempted_up = True
        compose_up(
            project,
            environment=compose_environment,
            services=services,
            timeout_seconds=600,
        )
        statuses = wait_for_compose_services(
            project,
            checks,
            environment=compose_environment,
        )
        if not all(item.ready for item in statuses):
            raise ServiceComposeError("compose_readiness_failed")
        try:
            validation_result = run_process(
                (str(validation_script),),
                cwd=project_root,
                environment=validation_environment,
                timeout_seconds=validation_timeout,
            )
        except RuntimePrimitiveError as error:
            raise ServiceComposeError("compose_validation_process_failed") from error
        if validation_result.timed_out:
            raise ServiceComposeError("compose_validation_timeout")
        if validation_result.returncode != 0:
            raise ServiceComposeError("compose_validation_failed")
    except ServiceComposeError as error:
        primary = error
        _emit_failure_diagnostics(
            context,
            project,
            environment=compose_environment,
            services=services,
            validation=validation_result,
        )
    finally:
        if attempted_up:
            try:
                cleanup_compose_stack(
                    project,
                    environment=compose_environment,
                    timeout_seconds=300,
                )
            except ServiceComposeError as error:
                cleanup_code = error.code

    if primary is not None or cleanup_code:
        if primary is None:
            failure = ServiceComposeError(cleanup_code)
            _emit_failure_diagnostics(
                context,
                project,
                environment=compose_environment,
                services=services,
                validation=validation_result,
            )
        else:
            failure = ServiceComposeError(
                primary.code,
                cleanup_code=cleanup_code or primary.cleanup_code,
            )
        _failure_outputs(
            context,
            failure,
            cleanup_result="failure" if cleanup_code else "success",
        )
        raise failure from primary

    if validation_result is None:
        _fail("compose_validation_result_invalid")
    summary = canonical_json(
        {
            "readiness": _readiness_summary(statuses),
            "services": list(services),
            "validation_returncode": validation_result.returncode,
        }
    )
    return CIWResult(
        "compose",
        "validate",
        outputs={
            "result": "success",
            "test_summary": summary,
            "cleanup_result": "success",
            "failure_code": "",
            "cleanup_code": "",
            "project_name": project.project_name,
        },
    )
