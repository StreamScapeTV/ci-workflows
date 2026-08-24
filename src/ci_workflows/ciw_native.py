"""Bounded product-neutral native/CMake validation adapter."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from . import native_primitives
from .ciw_types import write_command_file
from .workspace import resolve_state_root

_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_JSON_BYTES = 16 * 1024
_MAX_LIST_ITEMS = 128
_PLAN_KEYS = {
    "definitions",
    "configure_options",
    "generator",
    "build_target",
    "build_configuration",
    "build_options",
    "test_target",
    "test_options",
    "jobs",
}


class NativeValidationError(RuntimeError):
    """Fail closed with one stable native-validation error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise NativeValidationError(code)


def safe_relative(value: str, *, code: str = "invalid_working_directory") -> Path:
    _require(isinstance(value, str) and bool(value), code)
    _require("\\" not in value and "\x00" not in value, code)
    path = PurePosixPath(value)
    _require(not path.is_absolute() and ".." not in path.parts, code)
    normalized = Path(*path.parts)
    return Path(".") if str(normalized) in {"", "."} else normalized


def bounded_path(root: Path, relative: Path, *, code: str) -> Path:
    try:
        base = root.resolve(strict=True)
    except OSError as error:
        raise NativeValidationError(code) from error
    candidate = base / relative
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise NativeValidationError(code) from error
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise NativeValidationError(code) from error
    _require(resolved.is_dir() and not candidate.is_symlink(), code)
    return resolved


def _bounded_text(value: object, *, code: str, allow_empty: bool = True) -> str:
    _require(isinstance(value, str), code)
    _require(
        (allow_empty or bool(value))
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value,
        code,
    )
    _require(len(value.encode("utf-8")) <= 4096, code)
    return value


def _string_map(value: object) -> dict[str, str]:
    _require(isinstance(value, dict) and len(value) <= _MAX_LIST_ITEMS, "invalid_cmake_definitions")
    _require(
        all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()),
        "invalid_cmake_definitions",
    )
    return dict(value)


def _string_list(value: object, *, code: str) -> tuple[str, ...]:
    _require(isinstance(value, list) and len(value) <= _MAX_LIST_ITEMS, code)
    _require(all(isinstance(item, str) for item in value), code)
    return tuple(value)


def _jobs(value: object) -> int:
    _require(type(value) is int and 1 <= value <= 64, "invalid_jobs")
    return value


def _plan(raw: str) -> dict[str, object]:
    _require(isinstance(raw, str) and bool(raw), "invalid_validation_plan")
    _require(len(raw.encode("utf-8")) <= _MAX_JSON_BYTES, "invalid_validation_plan")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise NativeValidationError("invalid_validation_plan") from error
    _require(isinstance(value, dict), "invalid_validation_plan")
    _require(set(value) <= _PLAN_KEYS, "invalid_validation_plan")
    return {
        "definitions": _string_map(value.get("definitions", {})),
        "configure_options": _string_list(
            value.get("configure_options", []), code="invalid_configure_options"
        ),
        "generator": _bounded_text(value.get("generator", ""), code="invalid_generator"),
        "build_target": _bounded_text(
            value.get("build_target", ""), code="invalid_build_target"
        ),
        "build_configuration": _bounded_text(
            value.get("build_configuration", ""), code="invalid_build_configuration"
        ),
        "build_options": _string_list(
            value.get("build_options", []), code="invalid_build_options"
        ),
        "test_target": _bounded_text(
            value.get("test_target", "test"), code="invalid_test_target", allow_empty=False
        ),
        "test_options": _string_list(
            value.get("test_options", []), code="invalid_test_options"
        ),
        "jobs": _jobs(value.get("jobs", 2)),
    }


def _state_temp(environment: Mapping[str, str], contract_root: Path) -> Path:
    try:
        runner_temp = Path(environment["RUNNER_TEMP"])
        state_id = environment["CI_WORKFLOW_STATE_ID"]
        declared_root = environment["CI_WORKFLOW_ROOT"]
    except KeyError as error:
        raise NativeValidationError("isolation_unavailable") from error
    root = resolve_state_root(
        runner_temp=runner_temp,
        state_id=state_id,
        declared_root=declared_root,
        contract_root=contract_root,
    )
    temporary = root / "tmp"
    _require(temporary.is_dir() and not temporary.is_symlink(), "isolation_unavailable")
    return temporary


def request_from_environment(environment: Mapping[str, str]) -> dict[str, object]:
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    _require(_SHA.fullmatch(admitted_sha) is not None, "invalid_admitted_sha")
    return {
        "admitted_sha": admitted_sha,
        "working_directory": safe_relative(environment.get("INPUT_WORKING_DIRECTORY", ".")),
        **_plan(environment.get("INPUT_VALIDATION_PLAN_JSON", "")),
    }


def _emit_outputs(environment: Mapping[str, str], values: Mapping[str, str]) -> None:
    output = environment.get("GITHUB_OUTPUT", "")
    if output:
        write_command_file(Path(output), values)


def execute_native_validate(
    *,
    contract_root: Path,
    workspace: Path,
    environment: Mapping[str, str],
    configure: Callable[..., native_primitives.NativeCommandResult] = native_primitives.cmake_configure,
    build: Callable[..., native_primitives.NativeCommandResult] = native_primitives.cmake_build,
) -> dict[str, str]:
    """Run CMake configure, build and test-target work in one isolated build tree."""

    request = request_from_environment(environment)
    source = bounded_path(
        workspace / "source",
        request["working_directory"],
        code="invalid_working_directory",
    )
    state_temp = _state_temp(environment, contract_root)
    build_dir = state_temp / "native-cmake-build"

    configure(
        source_dir=source,
        build_dir=build_dir,
        definitions=request["definitions"],
        generator=request["generator"],
        options=request["configure_options"],
        environment=environment,
    )
    build(
        build_dir=build_dir,
        jobs=request["jobs"],
        target=request["build_target"],
        configuration=request["build_configuration"],
        options=request["build_options"],
        environment=environment,
    )
    build(
        build_dir=build_dir,
        jobs=request["jobs"],
        target=request["test_target"],
        configuration=request["build_configuration"],
        options=request["test_options"],
        environment=environment,
    )

    summary = json.dumps(
        {"build": "success", "configure": "success", "status": "success", "test": "success"},
        sort_keys=True,
        separators=(",", ":"),
    )
    outputs = {
        "result": "success",
        "source_sha": str(request["admitted_sha"]),
        "test_summary": summary,
        "failure_code": "",
    }
    _emit_outputs(environment, outputs)
    return outputs


def _failure_outputs(environment: Mapping[str, str], code: str) -> None:
    candidate_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    source_sha = candidate_sha if _SHA.fullmatch(candidate_sha) is not None else ""
    _emit_outputs(
        environment,
        {
            "result": "failure",
            "source_sha": source_sha,
            "test_summary": json.dumps({"status": "failed"}, sort_keys=True, separators=(",", ":")),
            "failure_code": code,
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ciw-native")
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    args = parser().parse_args(argv)
    env = dict(os.environ if environment is None else environment)
    try:
        execute_native_validate(
            contract_root=args.root.resolve(),
            workspace=args.workspace.resolve(),
            environment=env,
        )
    except NativeValidationError as error:
        _failure_outputs(env, error.code)
        sys.stderr.write(f"native validation failed: {error.code}\n")
        return 2
    except native_primitives.NativePrimitiveError as error:
        _failure_outputs(env, error.code)
        sys.stderr.write(f"native validation failed: {error.code}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
