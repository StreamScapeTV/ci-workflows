"""Thin CLI adapters for the shared non-language foundation primitives."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Mapping, Sequence

from .dependencies import checkout_private_dependency
from .evidence import build_evidence, parse_toolchain_json, write_evidence
from .foundation_types import FoundationError, bounded_int, require
from .policy import verify_repository_policy
from .tooling import install_locked_asset, verify_runtime_capability, verify_tool_set
from .workspace import (
    WorkspaceContext,
    cleanup_workspace,
    prepare_workspace,
    register_state_path,
    resolve_state_root,
)

_COMMAND_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _environment(name: str, *, required: bool = True, default: str = "") -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise FoundationError("required_environment_missing")
    return value


def _input(name: str, default: str = "") -> str:
    key = "INPUT_" + name.upper().replace("-", "_")
    return os.environ.get(key, default).strip()


def _append_commands(path_name: str, values: Mapping[str, str]) -> None:
    path_value = os.environ.get(path_name, "")
    if not path_value:
        return
    path = Path(path_value)
    lines: list[str] = []
    for name, value in values.items():
        require(_COMMAND_NAME.fullmatch(name) is not None, "invalid_github_command_name")
        require(isinstance(value, str) and "\n" not in value and "\r" not in value, "invalid_github_command_value")
        lines.append(f"{name}={value}\n")
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
    except OSError as error:
        raise FoundationError("github_command_file_unavailable") from error


def _write_outputs(values: Mapping[str, str]) -> None:
    _append_commands("GITHUB_OUTPUT", values)


def _write_environment(values: Mapping[str, str]) -> None:
    _append_commands("GITHUB_ENV", values)


def _state_root(contract_root: Path) -> Path:
    return resolve_state_root(
        runner_temp=Path(_environment("RUNNER_TEMP")),
        state_id=_environment("CI_WORKFLOW_STATE_ID"),
        declared_root=_environment("CI_WORKFLOW_ROOT"),
        contract_root=contract_root,
    )


def _prepare(contract_root: Path) -> None:
    context = WorkspaceContext(
        workspace=Path(_environment("GITHUB_WORKSPACE")),
        runner_temp=Path(_environment("RUNNER_TEMP")),
        repository=_environment("GITHUB_REPOSITORY"),
        run_id=_environment("GITHUB_RUN_ID"),
        run_attempt=bounded_int(
            _environment("GITHUB_RUN_ATTEMPT", default="1"),
            minimum=1,
            maximum=1_000_000,
            instruction="invalid_run_attempt",
        ),
        job=_environment("GITHUB_JOB"),
        runner_os=_environment("RUNNER_OS"),
    )
    state = prepare_workspace(
        context,
        profile=_input("profile", "minimal"),
        cache_mode=_input("cache_mode", "disabled"),
        source_sha=_input("source_sha") or None,
        lock_digest=_input("lock_digest") or None,
        trust_mode=_input("trust_mode") or None,
        contract_root=contract_root,
    )
    _write_environment(state.environment)
    _write_outputs(state.output_values())


def _verify_tools(contract_root: Path) -> None:
    operation = _input("operation", "verify-set")
    require(operation in {"verify-set", "install-asset"}, "unsupported_tool_operation")
    if operation == "verify-set":
        evidence = verify_tool_set(_input("tool_set", "baseline"), contract_root=contract_root)
        capability = verify_runtime_capability(
            _input("capability_profile", "baseline"),
            declared_os=os.environ.get("RUNNER_OS") or None,
            declared_architecture=os.environ.get("RUNNER_ARCH") or None,
            contract_root=contract_root,
        )
        _write_outputs({**evidence.output_values(), **capability.output_values()})
        return
    asset_id = _input("asset_id")
    require(bool(asset_id), "locked_asset_id_required")
    relative = f"tools/{asset_id}"
    destination = register_state_path(
        _state_root(contract_root),
        name=f"tool-{asset_id}",
        relative=relative,
        kind="tool",
        contract_root=contract_root,
        create=True,
    )
    installed = install_locked_asset(
        asset_id,
        destination_root=destination,
        contract_root=contract_root,
    )
    outputs = installed.output_values()
    outputs["asset_relative_path"] = f"{relative}/{installed.filename}"
    _write_environment({"CI_LOCKED_TOOL_PATH": str(destination / installed.filename)})
    _write_outputs(outputs)


def _checkout_dependency(contract_root: Path) -> None:
    result = checkout_private_dependency(
        state_root=_state_root(contract_root),
        repository=_input("repository"),
        admitted_sha=_input("admitted_sha"),
        dependency_id=_input("dependency_id"),
        expected_subpath=_input("expected_subpath", "."),
        fetch_depth=bounded_int(
            _input("fetch_depth", "1"),
            minimum=1,
            maximum=1000,
            instruction="invalid_dependency_fetch_depth",
        ),
        token=os.environ.get("PRIVATE_DEPENDENCY_TOKEN", ""),
        contract_root=contract_root,
    )
    target = _state_root(contract_root) / result.relative_path
    _write_environment({"CI_PRIVATE_DEPENDENCY_PATH": str(target)})
    _write_outputs(result.output_values())


def _verify_policy(contract_root: Path) -> None:
    report = verify_repository_policy(
        Path(_environment("GITHUB_WORKSPACE")),
        repository=_environment("GITHUB_REPOSITORY"),
        phase=_input("phase", "after"),
        artifact_manifest_json=_input("artifacts_json", "[]"),
        artifact_exception_id=_input("artifact_exception_id") or None,
        trust_mode=_input("trust_mode") or None,
        contract_root=contract_root,
    )
    _write_outputs(report.output_values())


def _render_evidence(contract_root: Path) -> None:
    toolchain = parse_toolchain_json(_input("toolchain_json", "{}"))
    result = build_evidence(
        source_sha=_input("source_sha"),
        workflow_release=_input("workflow_release"),
        runner_profile=_input("runner_profile"),
        toolchain=toolchain,
        command_profile=_input("command_profile"),
        result=_input("result"),
        cleanup_state=_input("cleanup_state", "not-run"),
        cleanup_removed_paths=bounded_int(
            _input("cleanup_removed_paths", "0"),
            minimum=0,
            maximum=10000,
            instruction="invalid_cleanup_count",
        ),
        contract_root=contract_root,
    )
    path = write_evidence(_state_root(contract_root), result)
    _write_environment({"CI_EVIDENCE_FILE": str(path)})
    _write_outputs(result.output_values())


def _cleanup(contract_root: Path) -> None:
    report = cleanup_workspace(
        _state_root(contract_root),
        expected_state_id=os.environ.get("CI_WORKFLOW_STATE_ID") or None,
        contract_root=contract_root,
    )
    _write_outputs(report.output_values())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, required=True)
    result.add_argument(
        "operation",
        choices=(
            "prepare-workspace",
            "verify-toolchain",
            "checkout-private-dependency",
            "verify-repository-policy",
            "render-evidence",
            "cleanup-workspace",
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.operation == "prepare-workspace":
            _prepare(arguments.root)
        elif arguments.operation == "verify-toolchain":
            _verify_tools(arguments.root)
        elif arguments.operation == "checkout-private-dependency":
            _checkout_dependency(arguments.root)
        elif arguments.operation == "verify-repository-policy":
            _verify_policy(arguments.root)
        elif arguments.operation == "render-evidence":
            _render_evidence(arguments.root)
        else:
            _cleanup(arguments.root)
    except BaseException as error:
        instruction = getattr(error, "instruction", "foundation_unexpected_failure")
        print(str(instruction), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
