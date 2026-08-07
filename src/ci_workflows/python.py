"""Public facade and bounded orchestration for reusable Python validation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .foundation_types import FoundationError
from .policy import verify_repository_policy
from .python_contract import (
    CONTRACT_PATH,
    bounded_path,
    load_python_contract,
    request_from_environment,
    resolve_python_version,
    resolve_validation_plan,
    runtime_reference,
    safe_relative,
    source_trust_from_environment,
    validate_dependency_lock,
)
from .python_execution import (
    cleanup_podman,
    container_script,
    execute_host_plan,
    execute_podman_plan,
    result_from_plan,
    run_command,
    verify_exact_source,
)
from .python_types import (
    PythonCommand,
    PythonValidationError,
    PythonValidationPlan,
    PythonValidationRequest,
    PythonValidationResult,
)

# Private compatibility aliases retained for focused mutation tests. They do not
# accept callbacks or caller-selected functions and remain inside this package.
_container_script = container_script
_cleanup_podman = cleanup_podman
_run = run_command


def _verify_policy(
    source_root: Path,
    request: PythonValidationRequest,
    contract_root: Path,
    phase: str,
) -> None:
    try:
        verify_repository_policy(
            source_root,
            repository=request.repository,
            phase=phase,
            artifact_manifest_json="[]",
            artifact_exception_id=request.artifact_exception_id,
            trust_mode=request.source_trust,
            contract_root=contract_root,
        )
    except FoundationError as error:
        raise PythonValidationError("policy_failed") from error


def validate(
    *,
    contract_root: Path,
    source_root: Path | None,
    state_root: Path | None,
    request: PythonValidationRequest,
    phase: str,
    environment: Mapping[str, str],
) -> PythonValidationPlan | PythonValidationResult:
    """Plan or execute one contract-bounded Python validation request."""

    if phase not in {"plan", "execute"}:
        raise PythonValidationError("invalid_input")
    contract = load_python_contract(contract_root)
    plan = resolve_validation_plan(contract, request)
    if phase == "plan":
        return plan
    if source_root is None or state_root is None:
        raise PythonValidationError("invalid_input")

    exact_source = source_root.resolve()
    registered_state = state_root.resolve()
    verify_exact_source(exact_source, plan.admitted_sha)
    resolve_python_version(exact_source, plan)
    validate_dependency_lock(exact_source, plan)
    _verify_policy(exact_source, request, contract_root, "before")

    original_error: BaseException | None = None
    stage_count = 0
    try:
        if plan.isolation == "copied-host-source":
            stage_count = execute_host_plan(
                exact_source,
                registered_state,
                plan,
                environment,
            )
        elif plan.isolation in {"podman-vfs", "podman-vfs-postgres"}:
            stage_count = execute_podman_plan(
                exact_source,
                registered_state,
                plan,
                contract,
                environment,
            )
        else:
            raise PythonValidationError("isolation_unavailable")
    except BaseException as error:
        original_error = error

    verify_exact_source(exact_source, plan.admitted_sha)
    _verify_policy(exact_source, request, contract_root, "after")
    if original_error is not None:
        raise original_error
    return result_from_plan(plan, stage_count)


__all__ = (
    "CONTRACT_PATH",
    "PythonCommand",
    "PythonValidationError",
    "PythonValidationPlan",
    "PythonValidationRequest",
    "PythonValidationResult",
    "bounded_path",
    "load_python_contract",
    "request_from_environment",
    "resolve_python_version",
    "resolve_validation_plan",
    "runtime_reference",
    "safe_relative",
    "source_trust_from_environment",
    "validate",
    "validate_dependency_lock",
)
