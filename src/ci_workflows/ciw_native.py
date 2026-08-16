"""Thin typed CIW adapter for product-neutral native build primitives."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error
from .native_primitives import (
    ConfigureStep,
    NativePrimitiveError,
    cleanup_native_state,
    cmake_build,
    cmake_configure,
    cmake_install,
    create_deterministic_archive,
    inspect_native_outputs,
    run_configure_steps,
    run_make,
    run_ninja,
)

_DOMAIN = "native"
_PHASES = (
    "configure",
    "cmake-configure",
    "cmake-build",
    "cmake-install",
    "make",
    "ninja",
    "archive",
    "inspect",
    "cleanup",
)


def configure_native(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=_PHASES, required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--source-directory")
    parser.add_argument("--state-directory")
    parser.add_argument("--install-directory")
    parser.add_argument("--configure-steps-json")
    parser.add_argument("--definitions-json")
    parser.add_argument("--options-json")
    parser.add_argument("--targets-json")
    parser.add_argument("--members-json")
    parser.add_argument("--outputs-json")
    parser.add_argument("--cleanup-paths-json")
    parser.add_argument("--generator")
    parser.add_argument("--target")
    parser.add_argument("--configuration")
    parser.add_argument("--component")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--archive-format")
    parser.add_argument("--archive-output")
    parser.add_argument("--cwd-scope", choices=("source", "state"))


def _value(args: argparse.Namespace, context: CIWContext, name: str, default: str = "") -> str:
    value = getattr(args, name, None)
    if value is not None:
        return str(value).strip()
    return input_value(context.environment, name, default)


def _text(value: object, code: str, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value.encode("utf-8")) > 4096
        or (not allow_empty and not value)
    ):
        raise CIWError(_DOMAIN, code)
    return value


def _json(raw: str, code: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise CIWError(_DOMAIN, code) from error


def _string_list(raw: str, code: str, *, maximum: int = 4096) -> tuple[str, ...]:
    value = _json(raw, code)
    if not isinstance(value, list) or len(value) > maximum:
        raise CIWError(_DOMAIN, code)
    return tuple(_text(item, code) for item in value)


def _string_map(raw: str, code: str) -> Mapping[str, str]:
    value = _json(raw, code)
    if not isinstance(value, dict) or len(value) > 128:
        raise CIWError(_DOMAIN, code)
    result: dict[str, str] = {}
    for name, item in value.items():
        result[_text(name, code)] = _text(item, code, allow_empty=True)
    return result


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


def _state_root(context: CIWContext) -> Path:
    raw = context.environment.get("CI_WORKFLOW_ROOT", "")
    candidate = Path(raw)
    if not raw or not candidate.is_absolute() or candidate.is_symlink():
        raise CIWError(_DOMAIN, "workspace_state_required")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, "workspace_state_invalid") from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, "workspace_state_invalid")
    return resolved


def _bounded(root: Path, raw: str, code: str, *, must_exist: bool) -> Path:
    text = _text(raw, code)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts or "\\" in text:
        raise CIWError(_DOMAIN, code)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise CIWError(_DOMAIN, code)
        if not cursor.exists():
            break
    try:
        resolved = (root / relative).resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if must_exist and not resolved.exists():
        raise CIWError(_DOMAIN, code)
    return resolved


def _source_path(args: argparse.Namespace, context: CIWContext, name: str, default: str = ".") -> Path:
    return _bounded(_root(args, context), _value(args, context, name, default), f"{name}_invalid", must_exist=True)


def _state_path(args: argparse.Namespace, context: CIWContext, name: str, default: str, *, must_exist: bool) -> Path:
    return _bounded(_state_root(context), _value(args, context, name, default), f"{name}_invalid", must_exist=must_exist)


def _result(operation: str, **payload: Any) -> CIWResult:
    projection = {"operation": operation, **payload}
    return CIWResult(
        _DOMAIN,
        "run",
        outputs={
            "result": "success",
            "native_result_json": json.dumps(projection, sort_keys=True, separators=(",", ":")),
        },
    )


def execute_native(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    environment = dict(context.environment)
    phase = args.phase
    try:
        if phase == "configure":
            raw = _value(args, context, "configure_steps_json")
            value = _json(raw, "configure_steps_invalid")
            if not isinstance(value, list) or not value or len(value) > 32:
                raise CIWError(_DOMAIN, "configure_steps_invalid")
            steps: list[ConfigureStep] = []
            root = _root(args, context)
            for item in value:
                if not isinstance(item, dict) or set(item) != {"tool", "arguments", "cwd"}:
                    raise CIWError(_DOMAIN, "configure_steps_invalid")
                arguments = item["arguments"]
                if not isinstance(arguments, list) or len(arguments) > 256:
                    raise CIWError(_DOMAIN, "configure_steps_invalid")
                steps.append(
                    ConfigureStep(
                        _text(item["tool"], "configure_steps_invalid"),
                        tuple(_text(argument, "configure_steps_invalid") for argument in arguments),
                        _bounded(root, _text(item["cwd"], "configure_steps_invalid"), "configure_steps_invalid", must_exist=True),
                    )
                )
            results = run_configure_steps(tuple(steps), environment=environment)
            return _result("configure", command_count=len(results))

        if phase == "cmake-configure":
            result = cmake_configure(
                source_dir=_source_path(args, context, "source_directory"),
                build_dir=_state_path(args, context, "state_directory", "native/build", must_exist=False),
                definitions=_string_map(_value(args, context, "definitions_json", "{}"), "definitions_invalid"),
                generator=_text(_value(args, context, "generator", ""), "generator_invalid", allow_empty=True),
                options=_string_list(_value(args, context, "options_json", "[]"), "options_invalid", maximum=256),
                environment=environment,
            )
            return _result(result.operation)

        if phase == "cmake-build":
            jobs = args.jobs if args.jobs is not None else int(_value(args, context, "jobs", "1"))
            result = cmake_build(
                build_dir=_state_path(args, context, "state_directory", "native/build", must_exist=True),
                jobs=jobs,
                target=_text(_value(args, context, "target", ""), "target_invalid", allow_empty=True),
                configuration=_text(_value(args, context, "configuration", ""), "configuration_invalid", allow_empty=True),
                options=_string_list(_value(args, context, "options_json", "[]"), "options_invalid", maximum=256),
                environment=environment,
            )
            return _result(result.operation)

        if phase == "cmake-install":
            result = cmake_install(
                build_dir=_state_path(args, context, "state_directory", "native/build", must_exist=True),
                install_dir=_state_path(args, context, "install_directory", "native/install", must_exist=False),
                configuration=_text(_value(args, context, "configuration", ""), "configuration_invalid", allow_empty=True),
                component=_text(_value(args, context, "component", ""), "component_invalid", allow_empty=True),
                options=_string_list(_value(args, context, "options_json", "[]"), "options_invalid", maximum=256),
                environment=environment,
            )
            return _result(result.operation)

        if phase in {"make", "ninja"}:
            scope = _value(args, context, "cwd_scope", "source")
            if scope not in {"source", "state"}:
                raise CIWError(_DOMAIN, "cwd_scope_invalid")
            base = _root(args, context) if scope == "source" else _state_root(context)
            cwd = _bounded(base, _value(args, context, "source_directory", "."), "cwd_invalid", must_exist=True)
            jobs = args.jobs if args.jobs is not None else int(_value(args, context, "jobs", "1"))
            operation = run_make if phase == "make" else run_ninja
            result = operation(
                cwd=cwd,
                targets=_string_list(_value(args, context, "targets_json"), "targets_invalid", maximum=256),
                jobs=jobs,
                options=_string_list(_value(args, context, "options_json", "[]"), "options_invalid", maximum=256),
                environment=environment,
            )
            return _result(result.operation)

        if phase == "archive":
            result = create_deterministic_archive(
                root=_state_path(args, context, "install_directory", "native/install", must_exist=True),
                members=_string_list(_value(args, context, "members_json"), "members_invalid"),
                output_path=_state_path(args, context, "archive_output", "native-output.tar.gz", must_exist=False),
                format=_text(_value(args, context, "archive_format", "tar.gz"), "archive_format_invalid"),
            )
            return _result("archive", sha256=result.sha256, size_bytes=result.size_bytes)

        if phase == "inspect":
            outputs = inspect_native_outputs(
                root=_state_path(args, context, "install_directory", "native/install", must_exist=True),
                outputs=_string_list(_value(args, context, "outputs_json"), "outputs_invalid", maximum=256),
            )
            return _result(
                "inspect",
                outputs=[
                    {"path": item.path, "kind": item.kind, "size_bytes": item.size_bytes, "sha256": item.sha256, "executable": item.executable}
                    for item in outputs
                ],
            )

        if phase == "cleanup":
            root = _state_root(context)
            paths = tuple(
                _bounded(root, value, "cleanup_paths_invalid", must_exist=False)
                for value in _string_list(_value(args, context, "cleanup_paths_json", "[]"), "cleanup_paths_invalid", maximum=64)
            )
            removed = cleanup_native_state(root=root, paths=paths)
            return _result("cleanup", removed_paths=removed)

        raise CIWError(_DOMAIN, "phase_invalid")
    except (CIWError, NativePrimitiveError, ValueError) as error:
        raise project_error(error, domain=_DOMAIN) from error
