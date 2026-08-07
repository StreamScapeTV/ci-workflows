"""Bounded ``ciw python validate`` adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import python as python_validation
from . import runners
from .ciw_types import CIWContext, CIWResult, write_command_file
from .workspace import resolve_state_root


def configure_python_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=("plan", "execute"), default="execute")
    parser.add_argument("--source-root", default="source")


def _state_root(context: CIWContext) -> Path:
    environment = context.environment
    try:
        runner_temp = Path(environment["RUNNER_TEMP"])
        state_id = environment["CI_WORKFLOW_STATE_ID"]
        declared_root = environment["CI_WORKFLOW_ROOT"]
    except KeyError as error:
        raise python_validation.PythonValidationError("invalid_input") from error
    root = resolve_state_root(
        runner_temp=runner_temp,
        state_id=state_id,
        declared_root=declared_root,
        contract_root=context.root,
    )
    temporary = root / "tmp"
    if not temporary.is_dir() or temporary.is_symlink():
        raise python_validation.PythonValidationError("isolation_unavailable")
    return temporary


def _failure_outputs(context: CIWContext, code: str) -> None:
    path = context.environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    summary = json.dumps(
        {
            "status": "failed",
            "validation_profile": context.environment.get(
                "INPUT_VALIDATION_PROFILE", ""
            ),
            "command_profile": context.environment.get(
                "INPUT_COMMAND_PROFILE", ""
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    write_command_file(
        Path(path),
        {
            "result": "failure",
            "test_summary": summary,
            "source_sha": context.environment.get("INPUT_ADMITTED_SHA", ""),
            "resolved_python_version": "",
            "validation_profile": context.environment.get(
                "INPUT_VALIDATION_PROFILE", ""
            ),
            "command_profile": context.environment.get("INPUT_COMMAND_PROFILE", ""),
            "cleanup_result": "not-run",
            "failure_code": code,
            "artifact_exception_used": "false",
            "evidence_id": "",
            "runner_profile": "",
            "runs_on_json": "",
            "workspace_profile": "",
            "timeout_minutes": "",
            "source_trust": "",
        },
    )


def execute_python_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    """Plan or execute one checked-in Python validation request."""

    try:
        request = python_validation.request_from_environment(context.environment)
        if args.phase == "plan":
            plan = python_validation.validate(
                contract_root=context.root,
                source_root=None,
                state_root=None,
                request=request,
                phase="plan",
                environment=context.environment,
            )
            if not isinstance(plan, python_validation.PythonValidationPlan):
                raise python_validation.PythonValidationError("invalid_input")
            resolved = runners.resolve_runner_profile(
                runners.load_runner_contract(context.root),
                workflow_api="validation.python",
                source_trust=request.source_trust,
                requested_profile=plan.runner_profile,
            )
            outputs = plan.planning_outputs()
            outputs.update(
                {
                    "runner_profile": plan.runner_profile,
                    "runs_on_json": resolved.as_dict()["runs_on_json"],
                    "workspace_profile": plan.workspace_profile,
                    "timeout_minutes": str(plan.timeout_minutes),
                    "source_trust": request.source_trust,
                    "evidence_id": "",
                }
            )
            return CIWResult("python", "validate", outputs=outputs)

        workspace = Path(
            context.environment.get("GITHUB_WORKSPACE", ".")
        ).resolve()
        relative_source = python_validation.safe_relative(args.source_root)
        source_root = python_validation.bounded_path(workspace, relative_source)
        result = python_validation.validate(
            contract_root=context.root,
            source_root=source_root,
            state_root=_state_root(context),
            request=request,
            phase="execute",
            environment=context.environment,
        )
        if not isinstance(result, python_validation.PythonValidationResult):
            raise python_validation.PythonValidationError("invalid_input")
        return CIWResult(
            "python",
            "validate",
            outputs=result.output_values(),
        )
    except python_validation.PythonValidationError as error:
        _failure_outputs(context, error.code)
        raise
