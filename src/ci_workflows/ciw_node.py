"""Bounded ``ciw node validate`` adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import node as node_validation
from . import runners
from .ciw_types import CIWContext, CIWResult, write_command_file
from .execution_backends import ExecutionBackendError, resolve_execution_backend
from .workspace import resolve_state_root


def configure_node_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=("plan", "execute"), default="execute")
    parser.add_argument("--source-root", default="source")


def _state_root(context: CIWContext) -> Path:
    environment = context.environment
    try:
        runner_temp = Path(environment["RUNNER_TEMP"])
        state_id = environment["CI_WORKFLOW_STATE_ID"]
        declared_root = environment["CI_WORKFLOW_ROOT"]
    except KeyError as error:
        raise node_validation.NodeValidationError("invalid_input") from error
    root = resolve_state_root(
        runner_temp=runner_temp,
        state_id=state_id,
        declared_root=declared_root,
        contract_root=context.root,
    )
    temporary = root / "tmp"
    if not temporary.is_dir() or temporary.is_symlink():
        raise node_validation.NodeValidationError("cleanup_failed")
    return temporary


def _failure_outputs(context: CIWContext, code: str) -> None:
    path = context.environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    summary = json.dumps(
        {
            "command_profile": context.environment.get("INPUT_COMMAND_PROFILE", ""),
            "status": "failed",
            "validation_profile": context.environment.get("INPUT_VALIDATION_PROFILE", ""),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    write_command_file(
        Path(path),
        {
            "result": "failure",
            "node_version": context.environment.get("INPUT_NODE_VERSION", ""),
            "npm_version": "",
            "install_result": "failure",
            "test_summary": summary,
            "build_result": "failure",
            "output_verified": "false",
            "output_digest": "",
            "clean_tree": "false",
            "cleanup_result": "not-run",
            "artifact_exception_used": "false",
            "evidence_id": "",
            "failure_code": code,
            "runner_profile": "portable",
            "runs_on_json": "",
            "workspace_profile": "minimal",
            "timeout_minutes": "",
            "source_trust": "",
        },
    )


def execute_node_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    """Plan or execute one checked-in Node validation request."""

    try:
        contract = node_validation.load_node_contract(context.root)
        request = node_validation.request_from_environment(
            context.environment,
            contract,
        )
        if args.phase == "plan":
            plan = node_validation.validate(
                contract_root=context.root,
                source_root=None,
                state_root=None,
                request=request,
                phase="plan",
                environment=context.environment,
            )
            if not isinstance(plan, node_validation.NodeValidationPlan):
                raise node_validation.NodeValidationError("invalid_input")
            organization = runners.resolve_runner_profile(
                runners.load_runner_contract(context.root),
                workflow_api="validation.node",
                source_trust=request.source_trust,
                requested_profile=plan.runner_profile,
            )
            try:
                backend = resolve_execution_backend(
                    workflow_api="validation.node",
                    execution_backend=context.environment.get(
                        "INPUT_EXECUTION_BACKEND", "organization"
                    ),
                    execution_profile=organization.execution_profile,
                    organization_runs_on=organization.runs_on,
                )
            except ExecutionBackendError as error:
                code = (
                    "unsupported_profile"
                    if error.code == "unsupported_execution_backend_profile"
                    else "invalid_input"
                )
                raise node_validation.NodeValidationError(code) from error
            outputs = plan.planning_outputs()
            outputs.update(
                {
                    "runner_profile": plan.runner_profile,
                    "runs_on_json": backend.as_dict()["runs_on_json"],
                    "workspace_profile": plan.workspace_profile,
                    "timeout_minutes": str(plan.timeout_minutes),
                    "source_trust": request.source_trust,
                }
            )
            return CIWResult("node", "validate", outputs=outputs)

        workspace = Path(
            context.environment.get("GITHUB_WORKSPACE", ".")
        ).resolve()
        relative_source = node_validation.safe_relative(args.source_root)
        source_root = node_validation.bounded_path(workspace, relative_source)
        result = node_validation.validate(
            contract_root=context.root,
            source_root=source_root,
            state_root=_state_root(context),
            request=request,
            phase="execute",
            environment=context.environment,
        )
        if not isinstance(result, node_validation.NodeValidationResult):
            raise node_validation.NodeValidationError("invalid_input")
        return CIWResult(
            "node",
            "validate",
            outputs=result.output_values(),
        )
    except node_validation.NodeValidationError as error:
        _failure_outputs(context, error.code)
        raise
