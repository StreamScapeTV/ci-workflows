"""Bounded ``ciw android validate`` adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import android as android_validation
from . import runners
from .ciw_types import CIWContext, CIWResult, write_command_file
from .workspace import resolve_state_root


def configure_android_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def _state_root(context: CIWContext) -> Path:
    try:
        root = resolve_state_root(
            runner_temp=Path(context.environment["RUNNER_TEMP"]),
            state_id=context.environment["CI_WORKFLOW_STATE_ID"],
            declared_root=context.environment["CI_WORKFLOW_ROOT"],
            contract_root=context.root,
        )
    except KeyError as error:
        raise android_validation.AndroidValidationError(
            "invalid_input"
        ) from error
    temporary = root / "tmp"
    if not temporary.is_dir() or temporary.is_symlink():
        raise android_validation.AndroidValidationError("invalid_input")
    return temporary


def _failure_outputs(
    context: CIWContext,
    error: android_validation.AndroidValidationError,
) -> None:
    diagnostics = error.diagnostic_values()
    if diagnostics:
        context.stderr.write(
            "android policy diagnostic: "
            f"rule={diagnostics['policy_rule']} "
            f"subject={diagnostics['policy_subject']}\n"
        )

    target = context.environment.get("GITHUB_OUTPUT", "")
    if not target:
        return
    summary_value = {
        "failure_code": error.code,
        "status": "failed",
        "task_profile": context.environment.get("INPUT_TASK_PROFILE", ""),
        "validation_profile": context.environment.get(
            "INPUT_VALIDATION_PROFILE",
            "",
        ),
    }
    summary_value.update(diagnostics)
    summary = json.dumps(
        summary_value,
        sort_keys=True,
        separators=(",", ":"),
    )
    write_command_file(
        Path(target),
        {
            "result": "failure",
            "source_sha": context.environment.get("INPUT_ADMITTED_SHA", ""),
            "validation_profile": context.environment.get(
                "INPUT_VALIDATION_PROFILE",
                "",
            ),
            "task_profile": context.environment.get(
                "INPUT_TASK_PROFILE",
                "",
            ),
            "test_summary": summary,
            "resolved_java_major": "",
            "resolved_android_api": "",
            "gradle_version": "",
            "private_dependency_used": "false",
            "private_dependency_contract_id": "",
            "private_dependency_repository": "",
            "private_dependency_sha": "",
            "private_dependency_subdirectory": "",
            "private_dependency_id": "",
            "artifact_exception_used": "false",
            "device_handoff_json": "",
            "debug_output_verified": "false",
            "schema_verified": "false",
            "clean_tree": "false",
            "cleanup_result": "not-run",
            "failure_code": error.code,
            "evidence_id": "",
            "runner_profile": "",
            "runs_on_json": "",
            "planner_runner_profile": "",
            "workspace_profile": "",
            "timeout_minutes": "",
            "source_trust": "",
        },
    )


def execute_android_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    """Plan, execute, clean, or inspect one Android validation request."""

    try:
        contract = android_validation.load_android_contract(context.root)
        state_root = _state_root(context) if args.phase != "plan" else None
        if args.phase == "cleanup":
            assert state_root is not None
            android_validation.cleanup_android_state(state_root, contract)
            return CIWResult(
                "android",
                "validate",
                outputs={
                    "cleanup_result": "success",
                    "failure_code": "",
                },
            )
        if args.phase == "residue":
            assert state_root is not None
            for relative in ("android-validation", "android-source"):
                path = state_root / relative
                if path.exists() or path.is_symlink():
                    raise android_validation.AndroidValidationError(
                        "cleanup_failed"
                    )
            return CIWResult(
                "android",
                "validate",
                outputs={
                    "cleanup_result": "success",
                    "failure_code": "",
                },
            )

        request = android_validation.request_from_environment(
            context.environment,
            contract,
        )
        if args.phase == "plan":
            plan = android_validation.validate(
                contract_root=context.root,
                source_root=None,
                state_root=None,
                request=request,
                phase="plan",
                environment=context.environment,
            )
            if not isinstance(
                plan,
                android_validation.AndroidValidationPlan,
            ):
                raise android_validation.AndroidValidationError(
                    "invalid_input"
                )
            resolved = runners.resolve_runner_profile(
                runners.load_runner_contract(context.root),
                workflow_api="validation.android",
                source_trust=request.source_trust,
                requested_profile=plan.runner_profile,
            )
            outputs = plan.planning_outputs()
            outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
            return CIWResult("android", "validate", outputs=outputs)

        workspace = Path(
            context.environment.get("GITHUB_WORKSPACE", ".")
        ).resolve()
        source_root = android_validation.bounded_path(
            workspace,
            android_validation.safe_relative(args.source_root),
        )
        assert state_root is not None
        result = android_validation.validate(
            contract_root=context.root,
            source_root=source_root,
            state_root=state_root,
            request=request,
            phase="execute",
            environment=context.environment,
        )
        if not isinstance(
            result,
            android_validation.AndroidValidationResult,
        ):
            raise android_validation.AndroidValidationError("invalid_input")
        return CIWResult(
            "android",
            "validate",
            outputs=result.output_values(),
        )
    except android_validation.AndroidValidationError as error:
        _failure_outputs(context, error)
        raise
