"""Typed CIW adapters for organization maintenance and trusted Flux reconciliation."""
from __future__ import annotations

import argparse
from pathlib import Path

from .ciw_types import CIWContext, CIWResult, required_environment, write_command_file
from .flux_reconcile import plan_summary, reconcile, resolve_request
from .flux_reconcile_fs import remove_state
from .maintenance import GitHubApi, artifacts, branches, conformance, render_result, runner_retry
from .maintenance_contract import MaintenanceError, load_contract
from .maintenance_core import OperationResult

_MAINTENANCE_OUTPUTS: dict[str, tuple[str, ...]] = {
    "artifacts": ("result", "mutation_count", "request_id"),
    "branches": ("result", "mutation_count", "request_id"),
    "conformance": ("result", "mutation_count", "report_issue_url", "request_id"),
    "runner-retry": ("result", "retry_run_id", "request_id"),
}


def _bool(value: str) -> bool:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def configure_maintenance_artifacts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository-scope", default="")
    parser.add_argument("--dry-run", type=_bool, required=True)
    parser.add_argument("--request-id", required=True)


def configure_maintenance_branches(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--dry-run", type=_bool, required=True)
    parser.add_argument("--request-id", required=True)


def configure_maintenance_conformance(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository-scope", default="")
    parser.add_argument("--shared-reference-target-sha", default="")
    parser.add_argument("--dry-run", type=_bool, required=True)
    parser.add_argument("--request-id", required=True)


def configure_maintenance_runner_retry(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--dry-run", type=_bool, required=True)
    parser.add_argument("--request-id", required=True)


def configure_flux_reconcile(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--admitted-sha", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--policy-path", required=True)
    parser.add_argument("--allowlist-path", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--dry-run", type=_bool, required=True)


def _github_api(context: CIWContext) -> GitHubApi:
    token = required_environment(
        context.environment,
        "MAINTENANCE_GITHUB_TOKEN",
        domain="maintenance",
        code="maintenance_credential_required",
    )
    return GitHubApi(
        token,
        api_url=context.environment.get("GITHUB_API_URL", "https://api.github.com"),
    )


def _failure_outputs(
    context: CIWContext,
    *,
    operation: str,
    request_id: str,
    code: str,
) -> None:
    path = context.environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    if operation == "flux-reconcile":
        values = {
            "result": "failure",
            "reconciliation_state": "rejected",
            "request_id": request_id,
            "failure_code": code,
        }
    else:
        values = {
            "result": "failure",
            "request_id": request_id,
            "failure_code": code,
        }
        if operation in {"artifacts", "branches", "conformance"}:
            values["mutation_count"] = "0"
        if operation == "conformance":
            values["report_issue_url"] = ""
        if operation == "runner-retry":
            values["retry_run_id"] = ""
    write_command_file(Path(path), values)


def _maintenance_result(operation: str, value: OperationResult) -> CIWResult:
    rendered = render_result(value)
    outputs = {name: rendered[name] for name in _MAINTENANCE_OUTPUTS[operation]}
    outputs["failure_code"] = ""
    return CIWResult("maintenance", operation, outputs=outputs)


def execute_maintenance_artifacts(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    try:
        value = artifacts(
            load_contract(context.root),
            _github_api(context),
            root=context.root,
            repository_scope=args.repository_scope,
            dry_run=args.dry_run,
            request_id=args.request_id,
        )
        return _maintenance_result("artifacts", value)
    except MaintenanceError as error:
        _failure_outputs(
            context,
            operation="artifacts",
            request_id=args.request_id,
            code=error.code,
        )
        raise


def execute_maintenance_branches(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    try:
        value = branches(
            load_contract(context.root),
            _github_api(context),
            project_id=args.project_id,
            pr_number=args.pr_number,
            expected_head_sha=args.expected_head_sha,
            dry_run=args.dry_run,
            request_id=args.request_id,
        )
        return _maintenance_result("branches", value)
    except MaintenanceError as error:
        _failure_outputs(
            context,
            operation="branches",
            request_id=args.request_id,
            code=error.code,
        )
        raise


def execute_maintenance_conformance(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    try:
        value = conformance(
            load_contract(context.root),
            _github_api(context),
            root=context.root,
            repository_scope=args.repository_scope,
            shared_reference_target_sha=args.shared_reference_target_sha,
            dry_run=args.dry_run,
            request_id=args.request_id,
        )
        return _maintenance_result("conformance", value)
    except MaintenanceError as error:
        _failure_outputs(
            context,
            operation="conformance",
            request_id=args.request_id,
            code=error.code,
        )
        raise


def execute_maintenance_runner_retry(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    try:
        value = runner_retry(
            load_contract(context.root),
            _github_api(context),
            root=context.root,
            project_id=args.project_id,
            run_id=args.run_id,
            expected_head_sha=args.expected_head_sha,
            dry_run=args.dry_run,
            request_id=args.request_id,
        )
        return _maintenance_result("runner-retry", value)
    except MaintenanceError as error:
        _failure_outputs(
            context,
            operation="runner-retry",
            request_id=args.request_id,
            code=error.code,
        )
        raise


def _flux_source(context: CIWContext) -> Path:
    workspace = Path(
        required_environment(
            context.environment,
            "GITHUB_WORKSPACE",
            domain="flux",
            code="github_workspace_required",
        )
    ).resolve()
    raw_source = workspace / "source"
    if raw_source.is_symlink() or not raw_source.is_dir():
        raise MaintenanceError("flux_source_invalid")
    source = raw_source.resolve()
    if workspace not in source.parents:
        raise MaintenanceError("flux_source_invalid")
    return source


def _flux_state(context: CIWContext) -> Path:
    runner_temp = Path(
        required_environment(
            context.environment,
            "RUNNER_TEMP",
            domain="flux",
            code="runner_temp_required",
        )
    )
    run_id = required_environment(
        context.environment,
        "GITHUB_RUN_ID",
        domain="flux",
        code="github_run_id_required",
    )
    attempt = required_environment(
        context.environment,
        "GITHUB_RUN_ATTEMPT",
        domain="flux",
        code="github_run_attempt_required",
    )
    return runner_temp / f"flux-reconcile-{run_id}-{attempt}"


def execute_flux_reconcile(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    state: Path | None = None
    failure: MaintenanceError | None = None
    values: dict[str, str] | None = None
    try:
        contract = load_contract(context.root)
        contract.validate_request_id(args.request_id)
        source = _flux_source(context)
        state = _flux_state(context)
        remove_state(state, fail_on_unsafe=True)
        plan = resolve_request(
            contract,
            source_root=source,
            source_repository="StreamScapeTV/flux",
            admitted_sha=args.admitted_sha,
            target_id=args.target_id,
            product_id=args.product_id,
            operation=args.operation,
            policy_path=args.policy_path,
            allowlist_path=args.allowlist_path,
            request_id=args.request_id,
            state_root=state,
        )
        if not args.dry_run:
            reconcile(
                contract,
                plan,
                source_root=source,
                state_root=state,
                flux_kubeconfig=context.environment.get("FLUX_KUBECONFIG", ""),
                flux_sops_age_key=context.environment.get("FLUX_SOPS_AGE_KEY", ""),
            )
        values = plan_summary(plan, dry_run=args.dry_run)
        values["reconciliation_state"] = (
            "dry-run" if args.dry_run else "applied-and-verified"
        )
        values["failure_code"] = ""
    except MaintenanceError as error:
        failure = error
    finally:
        if state is not None:
            try:
                remove_state(state, fail_on_unsafe=False)
            except MaintenanceError as cleanup_error:
                if failure is None:
                    failure = cleanup_error
    if failure is not None:
        _failure_outputs(
            context,
            operation="flux-reconcile",
            request_id=args.request_id,
            code=failure.code,
        )
        raise failure
    assert values is not None
    return CIWResult("flux", "reconcile", outputs=values)
