"""Thin, bounded Helm command adapter registered through shared CIW."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from .ciw_types import CIWContext, CIWResult, write_command_file
from .helm_types import HelmRequest, HelmValidationError


def _state_root(root: Path, environment: Mapping[str, str]) -> Path:
    from .workspace import resolve_state_root

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
    if not (temporary.is_dir() and not temporary.is_symlink()):
        raise HelmValidationError("cleanup_failed")
    return temporary


def _source_root(
    root: Path,
    environment: Mapping[str, str],
    relative: str,
) -> Path:
    from .helm_contract import bounded_path

    workspace = Path(environment.get("GITHUB_WORKSPACE", ".")).resolve()
    return bounded_path(workspace, relative, "source_mismatch")


def _require_operation_trust(request: HelmRequest, operation: str) -> None:
    if operation == "publish" and request.source_trust != "trusted-exact":
        raise HelmValidationError("source_trust_rejected")


def _runner_profile(operation: str) -> str:
    return "buildah-tiny" if operation == "publish" else "portable"


def _failure_outputs(
    environment: Mapping[str, str],
    code: str,
    operation: str,
) -> None:
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
        "runner_profile": _runner_profile(operation),
        "runs_on_json": "",
        "workspace_profile": "minimal",
        "timeout_minutes": "90" if operation == "publish" else "60",
        "source_trust": environment.get("INPUT_SOURCE_TRUST", ""),
    }
    write_command_file(Path(path), common)


def _plan(
    root: Path,
    environment: Mapping[str, str],
    operation: str,
) -> dict[str, str]:
    """Resolve only shared runner mechanics; product metadata stays in caller source."""

    from . import runners
    from .helm_contract import request_from_environment

    request = request_from_environment(environment)
    _require_operation_trust(request, operation)
    api = "helm.publish" if operation == "publish" else "helm.validate"
    runner_profile = _runner_profile(operation)
    resolved = runners.resolve_runner_profile(
        runners.load_runner_contract(root),
        workflow_api=api,
        source_trust=request.source_trust,
        requested_profile=runner_profile,
    )
    return {
        "result": "planned",
        "chart_digest": "",
        "artifact_exception_used": "false",
        "immutable_references_json": "",
        "chart_package_sha256": "",
        "published": "false",
        "failure_code": "",
        "runner_profile": resolved.profile,
        "runs_on_json": resolved.as_dict()["runs_on_json"],
        "workspace_profile": "minimal",
        "timeout_minutes": "90" if operation == "publish" else "60",
        "source_trust": request.source_trust,
        "chart_name": "",
    }


def _caller_manifest_contract(request: HelmRequest) -> dict[str, object]:
    """Compatibility shape for the existing manifest parser without a central allowlist."""

    return {
        "products": {
            request.product_id: {
                "repository": request.repository,
            }
        }
    }


def execute(
    root: Path,
    environment: Mapping[str, str],
    *,
    operation: str,
    phase: str,
    source_relative: str,
) -> dict[str, str]:
    from .helm_archive import finalize_validation_archive
    from .helm_contract import request_from_environment, require
    from .helm_execution import cleanup_helm_state, verify_no_helm_residue
    from .helm_policy import run_policy_hook
    from .helm_simple import publish as publish_chart
    from .helm_simple import resolve_plan, validate_and_package

    require(operation in {"validate", "publish"}, "invalid_operation")
    if operation == "publish" and phase in {"measure-start", "measure-stop"}:
        from .helm_measurement import start, stop

        return start(root, environment) if phase == "measure-start" else stop(root, environment)
    if phase == "plan":
        return _plan(root, environment, operation)
    state_root = _state_root(root, environment)
    if phase == "cleanup":
        cleanup_helm_state(state_root)
        return {
            "result": "success",
            "cleanup_result": "success",
            "failure_code": "",
        }
    if phase == "residue":
        verify_no_helm_residue(state_root)
        return {
            "result": "success",
            "cleanup_result": "success",
            "failure_code": "",
        }

    request = request_from_environment(environment)
    _require_operation_trust(request, operation)
    source_root = _source_root(root, environment, source_relative)
    plan = resolve_plan(source_root, _caller_manifest_contract(request), request)
    validation = validate_and_package(
        source_root,
        state_root,
        plan,
        request.admitted_sha,
        environment,
    )
    run_policy_hook(
        source_root,
        state_root,
        plan,
        request.admitted_sha,
        environment,
    )
    validation = finalize_validation_archive(
        validation,
        plan.product.chart_name,
    )

    if operation == "publish":
        publication = publish_chart(
            source_root,
            state_root,
            plan,
            validation,
            environment,
        )
        values = publication.output_values()
        values.update(
            {
                "artifact_exception_used": "false",
                "failure_code": "",
                "runner_profile": _runner_profile(operation),
                "workspace_profile": "minimal",
                "timeout_minutes": "90",
                "source_trust": request.source_trust,
            }
        )
        return values

    values = validation.output_values()
    values.update(
        {
            "failure_code": "",
            "runner_profile": "portable",
            "workspace_profile": "minimal",
            "timeout_minutes": "60",
            "source_trust": request.source_trust,
        }
    )
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
    parser.add_argument(
        "--phase",
        choices=(
            "plan",
            "execute",
            "cleanup",
            "residue",
            "measure-start",
            "measure-stop",
        ),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


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
