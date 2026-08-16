"""Thin typed CIW adapter for ephemeral Compose service stacks."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error
from .service_compose_primitives import (
    ComposeReadinessCheck,
    ServiceComposeError,
    capture_compose_logs,
    cleanup_compose_stack,
    compose_exec,
    compose_ps,
    compose_run,
    start_compose_stack,
    validate_compose_project,
)

_DOMAIN = "compose"
_PHASES = ("start", "exec", "run", "ps", "logs", "cleanup")


def configure_compose(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=_PHASES, required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--compose-file")
    parser.add_argument("--env-files-json")
    parser.add_argument("--services-json")
    parser.add_argument("--readiness-json")
    parser.add_argument("--service")
    parser.add_argument("--command-json")
    parser.add_argument("--options-json")
    parser.add_argument("--tail-lines", type=int)
    parser.add_argument("--max-log-bytes", type=int)


def _value(args: argparse.Namespace, context: CIWContext, name: str, default: str = "") -> str:
    value = getattr(args, name, None)
    if value is not None:
        return str(value).strip()
    return input_value(context.environment, name, default)


def _text(value: object, code: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(token in value for token in ("\x00", "\r", "\n"))
    ):
        raise CIWError(_DOMAIN, code)
    return value


def _json(raw: str, code: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise CIWError(_DOMAIN, code) from error


def _strings(raw: str, code: str, *, maximum: int = 256) -> tuple[str, ...]:
    value = _json(raw, code)
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


def _tool(context: CIWContext) -> str:
    value = context.environment.get("CI_COMPOSE_TOOL", "podman").strip()
    if value not in {"docker", "podman"}:
        raise CIWError(_DOMAIN, "compose_tool_invalid")
    return value


def _project_name(context: CIWContext) -> str:
    material = "\x00".join(
        (
            context.environment.get("GITHUB_REPOSITORY", "repository"),
            context.environment.get("GITHUB_RUN_ID", "0"),
            context.environment.get("GITHUB_RUN_ATTEMPT", "0"),
            context.environment.get("GITHUB_JOB", "job"),
        )
    ).encode("utf-8", errors="replace")
    return "ciw-" + hashlib.sha256(material).hexdigest()[:20]


def _project(args: argparse.Namespace, context: CIWContext):
    compose_file = _value(args, context, "compose_file")
    if not compose_file:
        raise CIWError(_DOMAIN, "compose_file_required")
    env_files = _strings(_value(args, context, "env_files_json", "[]"), "env_files_invalid", maximum=16)
    return validate_compose_project(
        project_root=_root(args, context),
        compose_file=compose_file,
        project_name=_project_name(context),
        tool=_tool(context),
        env_files=env_files,
    )


def _port(value: object, code: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise CIWError(_DOMAIN, code)
    return str(value)


def _duration(value: object, code: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= 600:
        raise CIWError(_DOMAIN, code)
    return float(value)


def _readiness(args: argparse.Namespace, context: CIWContext) -> tuple[ComposeReadinessCheck, ...]:
    raw = _value(args, context, "readiness_json")
    value = _json(raw, "readiness_invalid")
    if not isinstance(value, list) or not value or len(value) > 32:
        raise CIWError(_DOMAIN, "readiness_invalid")
    result: list[ComposeReadinessCheck] = []
    password = context.environment.get("PGPASSWORD", "")
    for item in value:
        if not isinstance(item, dict):
            raise CIWError(_DOMAIN, "readiness_invalid")
        service = _text(item.get("service"), "readiness_invalid", maximum=63)
        kind = item.get("kind")
        timeout = _duration(item.get("timeout_seconds"), "readiness_invalid", 60.0)
        interval = _duration(item.get("interval_seconds"), "readiness_invalid", 0.25)
        environment: dict[str, str]
        expected = (200, 204)
        if kind == "tcp":
            allowed = {"service", "kind", "host", "port", "timeout_seconds", "interval_seconds"}
            if set(item) - allowed:
                raise CIWError(_DOMAIN, "readiness_invalid")
            environment = {
                "SERVICE_HOST": _text(item.get("host"), "readiness_invalid", maximum=253),
                "SERVICE_PORT": _port(item.get("port"), "readiness_invalid"),
            }
        elif kind == "http":
            allowed = {"service", "kind", "url", "expected_statuses", "timeout_seconds", "interval_seconds"}
            if set(item) - allowed:
                raise CIWError(_DOMAIN, "readiness_invalid")
            statuses = item.get("expected_statuses", [200, 204])
            if (
                not isinstance(statuses, list)
                or not statuses
                or len(statuses) > 32
                or any(isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599 for status in statuses)
            ):
                raise CIWError(_DOMAIN, "readiness_invalid")
            expected = tuple(statuses)
            environment = {"SERVICE_HTTP_URL": _text(item.get("url"), "readiness_invalid", maximum=2048)}
        elif kind == "postgres":
            allowed = {
                "service", "kind", "host", "port", "database", "username", "sslmode",
                "timeout_seconds", "interval_seconds",
            }
            if set(item) - allowed:
                raise CIWError(_DOMAIN, "readiness_invalid")
            environment = {
                "PGHOST": _text(item.get("host"), "readiness_invalid", maximum=253),
                "PGPORT": _port(item.get("port", 5432), "readiness_invalid"),
                "PGDATABASE": _text(item.get("database"), "readiness_invalid", maximum=128),
            }
            username = item.get("username", "")
            sslmode = item.get("sslmode", "")
            if username:
                environment["PGUSER"] = _text(username, "readiness_invalid", maximum=128)
            if sslmode:
                environment["PGSSLMODE"] = _text(sslmode, "readiness_invalid", maximum=32)
            if password:
                environment["PGPASSWORD"] = password
        else:
            raise CIWError(_DOMAIN, "readiness_invalid")
        result.append(
            ComposeReadinessCheck(
                service=service,
                kind=kind,
                environment=environment,
                timeout_seconds=timeout,
                interval_seconds=interval,
                expected_statuses=expected,
            )
        )
    return tuple(result)


def _result(operation: str, **payload: Any) -> CIWResult:
    return CIWResult(
        _DOMAIN,
        "run",
        outputs={
            "result": "success",
            "compose_result_json": json.dumps(
                {"operation": operation, **payload},
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def execute_compose(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    project = _project(args, context)
    environment: Mapping[str, str] = dict(context.environment)
    services = _strings(_value(args, context, "services_json", "[]"), "services_invalid", maximum=64)
    options = _strings(_value(args, context, "options_json", "[]"), "options_invalid", maximum=256)
    try:
        if args.phase == "start":
            stack = start_compose_stack(
                project,
                environment=environment,
                readiness=_readiness(args, context),
                services=services,
                up_options=options,
            )
            return _result(
                "start",
                project_name=stack.project_name,
                readiness=[
                    {"service": item.service, "kind": item.kind, "ready": item.ready, "attempts": item.attempts, "status": item.status}
                    for item in stack.readiness
                ],
            )
        if args.phase in {"exec", "run"}:
            service = _value(args, context, "service")
            command = _strings(_value(args, context, "command_json"), "command_invalid", maximum=256)
            operation = compose_exec if args.phase == "exec" else compose_run
            result = operation(
                project,
                service=service,
                command=command,
                environment=environment,
                options=options,
            )
            return _result(args.phase, service=result.service, output_sha256=result.output_sha256, output_bytes=result.output_bytes)
        if args.phase == "ps":
            result = compose_ps(project, environment=environment, services=services)
            return _result(
                "ps",
                services=[
                    {"service": item.service, "state": item.state, "health": item.health, "exit_code": item.exit_code}
                    for item in result.services
                ],
            )
        if args.phase == "logs":
            tail = args.tail_lines if args.tail_lines is not None else 200
            maximum = args.max_log_bytes if args.max_log_bytes is not None else 64 * 1024
            capture = capture_compose_logs(
                project,
                environment=environment,
                service=_value(args, context, "service", ""),
                tail_lines=tail,
                max_bytes=maximum,
            )
            return _result(
                "logs",
                service=capture.service,
                sha256=capture.sha256,
                size_bytes=capture.size_bytes,
                truncated=capture.truncated,
            )
        if args.phase == "cleanup":
            result = cleanup_compose_stack(project, environment=environment, options=options)
            return _result("cleanup", output_sha256=result.output_sha256, output_bytes=result.output_bytes)
        raise CIWError(_DOMAIN, "phase_invalid")
    except (CIWError, ServiceComposeError) as error:
        raise project_error(error, domain=_DOMAIN) from error
