"""Thin, bounded Helm command adapter pending shared CIW registration."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from . import runners
from .ciw_types import CIWContext, CIWResult, write_command_file
from .helm_contract import (
    bounded_path,
    load_helm_contract,
    load_helm_publication_contract,
    request_from_environment,
    require,
    resolve_validation_plan,
)
from .helm_execution import (
    cleanup_helm_state,
    publish_and_read_back,
    validate_and_package,
    verify_no_helm_residue,
)
from .helm_types import HelmValidationError
from .workspace import resolve_state_root


def _state_root(root: Path, environment: Mapping[str, str]) -> Path:
    try:
        state = resolve_state_root(
            runner_temp=Path(environment["RUNNER_TEMP"]),
            state_id=environment["CI_WORKFLOW_STATE_ID"],
            declared_root=environment["CI_WORKFLOW_ROOT"],
            contract_root=root,
        )
    except KeyError as error:
        raise HelmValidationError("invalid_input") from error
    temporary = state / "tmp"
    require(temporary.is_dir() and not temporary.is_symlink(), "cleanup_failed")
    return temporary


def _source_root(root: Path, environment: Mapping[str, str], relative: str) -> Path:
    workspace = Path(environment.get("GITHUB_WORKSPACE", ".")).resolve()
    return bounded_path(workspace, relative, "source_mismatch")


def _failure_outputs(environment: Mapping[str, str], code: str, operation: str) -> None:
    path = environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    common = {
        "result": "failure",
        "chart_digest": "",
        "artifact_exception_used": "false",
        "immutable_references_json": "",
        "chart_package_sha256": "",
        "published": "false",
        "failure_code": code,
        "runner_profile": "portable",
        "runs_on_json": "",
        "workspace_profile": "minimal",
        "timeout_minutes": "90" if operation == "publish" else "60",
        "source_trust": environment.get("INPUT_SOURCE_TRUST", ""),
    }
    write_command_file(Path(path), common)


def _plan(root: Path, environment: Mapping[str, str], operation: str) -> dict[str, str]:
    contract = load_helm_contract(root)
    if operation == "publish":
        load_helm_publication_contract(root)
    request = request_from_environment(environment)
    template = contract["products"].get(request.product_id)
    require(isinstance(template, Mapping) and template.get("repository") == request.repository, "repository_rejected")
    api = "helm.publish" if operation == "publish" else "helm.validate"
    resolved = runners.resolve_runner_profile(
        runners.load_runner_contract(root),
        workflow_api=api,
        source_trust=request.source_trust,
        requested_profile="portable",
    )
    return {
        "result": "planned",
        "chart_digest": "",
        "artifact_exception_used": "false",
        "immutable_references_json": "",
        "chart_package_sha256": "",
        "published": "false",
        "failure_code": "",
        "runner_profile": "portable",
        "runs_on_json": resolved.as_dict()["runs_on_json"],
        "workspace_profile": "minimal",
        "timeout_minutes": "90" if operation == "publish" else "60",
        "source_trust": request.source_trust,
        "chart_name": str(template["chart_name"]),
    }


def execute(
    root: Path,
    environment: Mapping[str, str],
    *,
    operation: str,
    phase: str,
    source_relative: str,
) -> dict[str, str]:
    if phase == "plan":
        return _plan(root, environment, operation)
    state_root = _state_root(root, environment)
    if phase == "cleanup":
        cleanup_helm_state(state_root)
        return {"result": "success", "cleanup_result": "success", "failure_code": ""}
    if phase == "residue":
        verify_no_helm_residue(state_root)
        return {"result": "success", "cleanup_result": "success", "failure_code": ""}
    contract = load_helm_contract(root)
    if operation == "publish":
        load_helm_publication_contract(root)
    request = request_from_environment(environment)
    source_root = _source_root(root, environment, source_relative)
    plan = resolve_validation_plan(source_root, contract, request)
    validation = validate_and_package(
        source_root, state_root, plan, request.admitted_sha, environment
    )
    if operation == "validate":
        values = validation.output_values()
        values.update({
            "failure_code": "",
            "runner_profile": "portable",
            "workspace_profile": "minimal",
            "timeout_minutes": "60",
            "source_trust": request.source_trust,
        })
        return values
    published = publish_and_read_back(
        source_root, state_root, plan, validation, environment
    )
    values = published.output_values()
    values.update({
        "artifact_exception_used": "false",
        "failure_code": "",
        "runner_profile": "portable",
        "workspace_profile": "minimal",
        "timeout_minutes": "90",
        "source_trust": request.source_trust,
    })
    return values


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def configure_helm_validate(parser: argparse.ArgumentParser) -> None:
    _configure(parser)


def configure_helm_publish(parser: argparse.ArgumentParser) -> None:
    _configure(parser)


def execute_helm_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return CIWResult(
        "helm",
        "validate",
        outputs=execute(
            context.root,
            context.environment,
            operation="validate",
            phase=args.phase,
            source_relative=args.source_root,
        ),
    )


def execute_helm_publish(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    return CIWResult(
        "helm",
        "publish",
        outputs=execute(
            context.root,
            context.environment,
            operation="publish",
            phase=args.phase,
            source_relative=args.source_root,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Helm workflow command")
    parser.add_argument("--root", required=True)
    parser.add_argument("operation", choices=("validate", "publish"))
    _configure(parser)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        values = execute(
            root,
            os.environ,
            operation=args.operation,
            phase=args.phase,
            source_relative=args.source_root,
        )
        target = os.environ.get("GITHUB_OUTPUT", "")
        if target:
            write_command_file(Path(target), values)
        else:
            sys.stdout.write(json.dumps(values, sort_keys=True) + "\n")
        return 0
    except HelmValidationError as error:
        _failure_outputs(os.environ, error.code, args.operation)
        sys.stderr.write(f"helm validation failed: {error.code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
