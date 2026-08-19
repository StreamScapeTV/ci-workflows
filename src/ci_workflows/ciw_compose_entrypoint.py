"""Executable boundary for the product-neutral service/Compose adapter."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from .ciw_compose import execute_compose_validate
from .ciw_types import CIWContext, input_value, project_error
from .service_compose_primitives import ServiceComposeError

_MAX_PLAN_BYTES = 16 * 1024
_ALLOWED_PLAN_KEYS = {
    "compose_file",
    "services",
    "env_files",
    "readiness",
    "validation_script_path",
    "validation_timeout_seconds",
}
_REQUIRED_PLAN_KEYS = {
    "compose_file",
    "readiness",
    "validation_script_path",
}


def _fail(code: str) -> None:
    raise ServiceComposeError(code)


def _text(value: object, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        _fail(code)
    return value


def _array(value: object, *, code: str) -> list[object]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _parse_plan(environment: Mapping[str, str]) -> dict[str, object]:
    raw = input_value(environment, "validation_plan_json")
    if not raw or len(raw.encode("utf-8")) > _MAX_PLAN_BYTES:
        _fail("compose_plan_invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ServiceComposeError("compose_plan_invalid") from error
    if not isinstance(value, dict):
        _fail("compose_plan_invalid")
    if not _REQUIRED_PLAN_KEYS <= set(value) or not set(value) <= _ALLOWED_PLAN_KEYS:
        _fail("compose_plan_invalid")

    compose_file = _text(value.get("compose_file"), code="compose_file_invalid")
    validation_script = _text(
        value.get("validation_script_path"),
        code="compose_validation_script_invalid",
    )
    services = _array(value.get("services", []), code="compose_services_input_invalid")
    env_files = _array(value.get("env_files", []), code="compose_env_files_input_invalid")
    readiness = _array(value.get("readiness"), code="compose_readiness_input_invalid")
    timeout = value.get("validation_timeout_seconds", 900)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        _fail("compose_validation_timeout_invalid")

    return {
        "compose_file": compose_file,
        "services": services,
        "env_files": env_files,
        "readiness": readiness,
        "validation_script_path": validation_script,
        "validation_timeout_seconds": timeout,
    }


def _adapter_environment(environment: Mapping[str, str]) -> dict[str, str]:
    plan = _parse_plan(environment)
    result = dict(environment)
    result["INPUT_ADMITTED_SHA"] = input_value(environment, "admitted_sha")
    result["INPUT_WORKING_DIRECTORY"] = input_value(environment, "working_directory", ".")
    result["INPUT_COMPOSE_FILE"] = str(plan["compose_file"])
    result["INPUT_COMPOSE_TOOL"] = "podman"
    result["INPUT_SERVICES_JSON"] = json.dumps(
        plan["services"], separators=(",", ":"), ensure_ascii=True
    )
    result["INPUT_ENV_FILES_JSON"] = json.dumps(
        plan["env_files"], separators=(",", ":"), ensure_ascii=True
    )
    result["INPUT_READINESS_JSON"] = json.dumps(
        plan["readiness"], separators=(",", ":"), ensure_ascii=True
    )
    result["INPUT_VALIDATION_SCRIPT_PATH"] = str(plan["validation_script_path"])
    result["INPUT_VALIDATION_TIMEOUT_SECONDS"] = str(
        plan["validation_timeout_seconds"]
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ciw-compose")
    parser.add_argument("--root", type=Path, required=True)
    parsed = parser.parse_args(argv)
    environment = dict(os.environ)
    try:
        projected_environment = _adapter_environment(environment)
        context = CIWContext(
            root=parsed.root.resolve(),
            environment=projected_environment,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        result = execute_compose_validate(argparse.Namespace(), context)
        result.emit(context)
    except Exception as error:
        projected = project_error(error, domain="compose")
        print(f"service/Compose validation failed: {projected.code}", file=sys.stderr)
        return projected.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
